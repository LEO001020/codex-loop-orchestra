# 补位控制器边界测试审查报告（只读）

审查对象：`harness/refill_controller_v2.py`、`harness/refill_consumer_v2.py` 及
`tests/orchestration_v2/test_refill_controller_v2.py`、`test_refill_consumer_v2.py`，
并核对 `statemachine_v2.py`、`dispatch.py`、`headless_wave.py`、
`config/refill_policy.toml` 中的相关机制。未修改任何源文件/测试。

## 一、已验证事实

1. 测试基线：`python -m pytest tests\orchestration_v2\test_refill_controller_v2.py
   tests\orchestration_v2\test_refill_consumer_v2.py -q -p no:cacheprovider`
   → **34 passed**（controller 22 + consumer 12）。
2. 政策基线（config/refill_policy.toml）：target_total=60，v4=45/k3=15，
   low_water=34/11，reservations_borrowable=true，spawn_interval_ms=1000。
3. 现有覆盖（已逐条核对）：
   - controller：pool 派生（role/hint）、queue_sync 双池总替换、policy 缺失
     fail-closed、双池 demand 匹配、零需求零补位、low-water→target 持续补位、
     可借用保留、watermark 回收、initializing 不清债、native TTL 失效、
     exec 双进程代校验、deficit≤pending、refill_required 事件、policy 版本
     强制重算、finalize/resume。
   - consumer：空 ledger 无 manifest、manifest 仅真实 packet、缺文件/缺 role/
     pool-role 冲突 fail-visible、release_review 排除、parent 溯源与失效 parent
     排除、dry-run 保留队列、run_once 消费 dispatched 事件、schedule_run
     启动/合并。
4. 三类回归对应的机制事实（行号以当前工作树为准）：
   - 补位判定：`queue_empty = pending_total <= 0`（refill_controller_v2.py:298）；
     `required` = 非空队列 ∧ 非 finalize ∧ pending[p]>0 ∧ running[p]<targets[p]
     （:304）；`raw_debt = min(pending, target-running)`（:310）；
     `raw_spawn = raw_debt - initializing`（:312）；deficit 受
     `capacity = target_total - running - initializing` 上限（:313-314）。
     active 跌落的观察入口：native running 超 1800s TTL 不计（:70,:236）；
     exec running 需 supervisor+worker 两个进程代同时存活（:262-268）。
     单池有需求时可借用另一池保留至 target_total（:285-289）。
   - 不造任务链：queue_sync_ledger 只把 ledger 的 DISPATCHABLE 与
     K3_WORK_STATES 计入 pending（:195-211）→ deficit≤pending →
     select_tasks 只选「state==DISPATCHABLE ∧ 非 release_review ∧ parent 激活
     ∧ packet 文件存在且 id 匹配 ∧ role 显式且与 pool 一致」的 packet
     （refill_consumer_v2.py:135-193）→ tasks 为空则 build_manifest 返回 None
     → run_once 直接 idle 返回、不调用 headless_wave（:195-246）。
   - 债务保留链：headless_wave.run 仅当全部结果 ∈
     {running, already_active, already_completed, dry_run} 才返回 0，否则 3
     （headless_wave.py:471-473）。dispatch 在 launch_supervisor 之前先写
     `dispatched` 事件（dispatch.py:566-568）。statemachine_v2：
     `dispatched`→RUNNING（t3，statemachine_v2.py:102）；`exec_failed` 且
     detail.phase=="pre_spawn" 时从 DISPATCHABLE/RUNNING 回到 DISPATCHABLE
     并累计 pre_spawn_failures，超过 PRE_SPAWN_FAILURE_MAX=3 转 DUTY_REVIEW
     （:451-466,:76）；`dispatch_refused` 是 INFO_EVENT，不改状态（:145,:401）。
     因此 pre-spawn/refusal 类失败后 ledger 保持 DISPATCHABLE，下一次
     queue_sync 重新计入 pending，deficit 保留；before/after 两次 recompute
     都会在 events.ndjson 追加 refill_required。
5. 现有测试缺口（三类回归均无直接覆盖）：
   - 没有任何测试从「运行中→跌落」的 roster 变化出发断言 deficit 上升与
     manifest 自动生成（test_demand_backed_refill_continues_from_low_water_to_target
     直接以 low-water 起步；test_dry_run_preserves_queue_and_uses_dispatcher
     以 active=0 起步）。
   - queue_empty 只在 controller 层断言；无 run_once idle 分支
     「不调用 headless_wave」的断言；无「队列非空但无 DISPATCHABLE 可选」
     （如仅 K3 work state）→ 零 manifest/零 spawn 的 actuator 级断言。
   - 无 provider 失败（wave 返回 3 / wave 抛异常 / StateMachine.step 抛异常）
     后 deficit 保留、packet 可重选、refill_required 事件复现的断言；
     无 pre_spawn_failures 计数与 DUTY_REVIEW 上限语义断言。

## 二、风险 / 反例

1. **债务保留有内置上限**：pre_spawn 失败 3 次后转 DUTY_REVIEW
   （statemachine_v2.py:458-466），packet 离开 refill 队列（DUTY_REVIEW 不计
   pending）。若验收语义是「provider 失败后债务保留」，3 次上限是反例；
   若是有意设计（转 duty 通道），当前无测试锁定该分界。
2. **保留路径依赖 INFO_EVENT/phase 约定**：`dispatch_refused` 靠 INFO_EVENT
   身份保持 DISPATCHABLE；任何把它改为普通 transition 的改动都会走 off-table
   默认 → DEAD_LETTER，债务被静默清除。现有测试未覆盖该约定。
3. **native running 的 TTL 排除不看进程存活**：超过 1800s 未刷新 updated_at
   的活 agent 被当作已退出 → active 虚跌、capacity 虚增。deficit≤pending 与
   capacity 双重上限可防止凭空多生，但会抬高 deficit 与 refill_required 事件
   频率。无测试锁定「TTL 跌落」这一 active-drop 路径。
4. **exec roster 的「running 但 worker 死」窗口**：supervisor 活、worker 死
   → 不计 running（active 跌落），但 ledger 仍是 RUNNING → 无 pending 不补位
   （正确）；只有该 packet 事后回到 DISPATCHABLE 才会触发补位。
   dispatched+exec_failed 的事件顺序与 phase 是保留与否的分水岭，建议固化。
5. **queue_empty 守卫覆盖不到 phantom demand**：K3_WORK_STATES 被计入
   pending，故仅含 L2_VERIFY/EXPAND_K3 的 ledger 使 queue_empty 永为 False；
   此时真正挡住造任务的是 select_tasks 的 DISPATCHABLE 过滤，不是
   queue_empty 守卫。该边界无 actuator 级测试。
6. **失败时状态字段可能误导**：run_once 在 wave 返回 3 时仍返回
   `status="dispatched"`（refill_consumer_v2.py:243-244），provider 失败只体现
   在 rc 与 after.deficit。测试应断言 rc 与 deficit，不能只看 status。
7. **wave 异常逃逸面**：run_once 中 headless_wave.run 若抛
   (OSError, ValueError, RefillConsumerError) 之外的类型（如 RuntimeError），
   异常逃逸 main 的捕获（:247-256 只捕三类），无结构化 failed JSON、post-failure
   sync 不执行；债务仅靠 before 状态落盘保留，失败可见性不完整。
8. **补位选择顺序是 sorted(packet_id)**（refill_consumer_v2.py:151），无优先级/
   affinity；跌落后的「补谁」不确定，测试不要断言具体 packet 顺序。
9. **陈旧 dispatched 事件反例**：DISPATCHABLE 状态下任何带 run_id 的
   dispatched 事件都走 t3 到 RUNNING（statemachine_v2.py:102），run_id 去重守卫
   只保护 RUNNING 及之后的 state（:437-447）。若事件流残留同 attempt 的旧
   dispatched 事件（且 run_id 与当前不符），包可能被误标 RUNNING 而清债。
   真实 dispatcher 总是带 run_id，风险低，但值得用反例测试钉住
   （provider 失败 + 陈旧事件 → 债务必须保留）。

## 三、建议测试（三类回归，可直接并入 tests/orchestration_v2/，复用
make_root/write_packet/现有 headless_wave.run monkeypatch 手法）

### A. active 跌落 + DISPATCHABLE 存在 → 自动补

1. `test_active_drop_recomputes_deficit`（controller 层）：
   roster 45 条 v4 running + pending 0 → required False；把其中 20 条状态改为
   completed（模拟 stop）→ queue_set(20,"v4") → recompute：required True、
   deficit v4==20、reason=="below_low_water"（25<34）。
2. `test_active_drop_end_to_end_dry_run_auto_refills`（actuator 层）：
   25 running + 3 个 DISPATCHABLE worker packet → run_once(dry_run=True)：
   headless_wave.run 恰好被调一次、manifest tasks==3、before.deficit v4==3、
   after.pending 不变（dry-run 不消费债务）。
3. `test_active_drop_via_native_ttl_and_exec_worker_death`：
   (a) 同一批 running 条目 updated_at 老化 >1800s → running 0 → 有 pending 时
   deficit==min(pending, target)；(b) exec job 标 running 但 worker 进程代
   死亡（monkeypatch _process_generation_alive）→ 不计 running → deficit 恢复。
4. `test_drop_with_borrowed_reservation_uses_full_target`：
   v4 单池 pending 60、running 40 → target=={v4:60,k3:0}（借用 k3 保留）、
   deficit v4==20、k3==0；随后 k3 出现 pending → 回收（与既有 watermark 测试
   互补，覆盖「跌落+借用」组合）。

### B. queue_empty / 无 DISPATCHABLE → 不凭空造任务

5. `test_run_once_idle_never_invokes_headless_wave`：空 ledger → run_once：
   status=="idle"、manifest None、headless_wave.run 计数 0（monkeypatch 使任何
   调用即失败）、无 refill_required 事件。
6. `test_phantom_k3_demand_never_fabricates_tasks`：ledger 仅 L2_VERIFY/
   EXPAND_K3（无 DISPATCHABLE）→ queue 非空、deficit k3>0、refill_required
   True，但 run_once → idle、零 spawn、零 manifest。注释写明：本场景的保护者
   是 select_tasks 的 DISPATCHABLE 过滤，而非 queue_empty 守卫。
7. `test_deficit_never_selects_more_than_dispatchable`：构造 deficit v4=5 但
   实际仅 2 个 DISPATCHABLE → select_tasks 恰好 2 个、manifest tasks==2
   （补 deficit_capped_by_pending 的 consumer 侧）。

### C. provider 失败后债务保留

8. `test_provider_failure_keeps_debt_and_reselectable`：1 个 DISPATCHABLE
   worker；headless_wave.run 返回 3 且不写任何 dispatched 事件（模拟
   health_blocked/backoff）→ run_once rc==3、after.deficit v4==1、ledger 仍
   DISPATCHABLE、events.ndjson 含两次 refill_required（before+after）、
   select_tasks 再次选出同一 packet。
9. `test_pre_spawn_failure_returns_to_dispatchable_and_counts`：预写
   dispatched(run_id=R1)+exec_failed(phase=pre_spawn, run_id=R1) 事件 →
   StateMachine.step 后 ledger 回 DISPATCHABLE、pre_spawn_failures==1；再叠加
   至 4 次 → DUTY_REVIEW（显式锁定 PRE_SPAWN_FAILURE_MAX=3 的债务保留上限
   语义；若验收要求无限期保留，此断言会暴露设计分歧）。
10. `test_state_machine_step_failure_keeps_debt`：monkeypatch
    statemachine_v2.StateMachine.step 抛异常，wave 返回 0 → run_once rc==3
    （rc or 3 路径）、after.deficit>0、ledger 仍 DISPATCHABLE、
    refill_state.json 中 deficit 保留。
11. `test_provider_exception_keeps_persisted_debt`：headless_wave.run 抛
    RuntimeError → pytest.raises 捕获；随后断言 refill_state.json
    deficit v4==1（before 已落盘）且 ledger 仍 DISPATCHABLE。若要求结构化
    失败输出，需扩展 main 的异常捕获集（风险 #7）。
12. `test_stale_dispatched_event_does_not_clear_debt`（反例钉桩）：provider
    失败返回 3，但事件流残留同 attempt 的旧 dispatched 事件 → 断言 ledger
    仍 DISPATCHABLE、after.deficit>0（钉住风险 #9 的去重边界）。

## 四、验证命令

`$env:PYTHONDONTWRITEBYTECODE='1'; python -m pytest tests\orchestration_v2\test_refill_controller_v2.py tests\orchestration_v2\test_refill_consumer_v2.py -q -p no:cacheprovider`
