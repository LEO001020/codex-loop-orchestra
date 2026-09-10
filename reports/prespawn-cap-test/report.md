# 出生前失败上限测试 —— 只读设计报告

任务：为 `E:\codex-LOOP\codex-loop-s-f2` 设计 pre_spawn 持续失败的有限计数/上限测试矩阵。
约束：只读（不改文件/不派生/不碰 VPS）；上限后进入 duty/dead-letter；不回退 Sol。
本报告只读源代码与既有测试，未运行任何会写缓存的命令，未修改任何文件。

---

## 一、已验证事实（file:line）

1. **pre_spawn 失败当前是无上限的无限重试**：
   `harness/statemachine_v2.py:451-476`，`apply_event` 对
   `exec_failed + detail.phase == "pre_spawn"` 且包状态在 `{DISPATCHABLE, RUNNING}`
   时，无条件把状态重置回 `DISPATCHABLE`（history via `pre_spawn_failure_retryable`）。
   该分支不递增任何计数器、不写 dead letter、不进入 duty、不触碰 Sol。
2. **attempt 永不增长，stale 过滤形同虚设**：
   `attempts` 只在 t9/t37（`statemachine_v2.py:570-572`）递增，pre_spawn 分支不递增，
   因此每轮重试 event_attempt 仍是 0，`stale_generation_ignored`
   （`statemachine_v2.py:427-436`）永远不会拦截后续 pre_spawn 失败。
3. **refill 层把 DISPATCHABLE 无限视为债务**：
   `refill_controller_v2.py:195-217` `queue_sync_ledger` 把每个 DISPATCHABLE 计为
   pending；`recompute` 的 `raw_debt`（`refill_controller_v2.py:310-315`）按
   target-running 产生 deficit；`refill_consumer_v2.py:135` `select_tasks`
   每轮重新选中同一 packet → 反复 spawn → 反复 pre_spawn 失败 → 无限循环。
4. **pre_spawn 失败事件的三个来源**：
   - `dispatch_v2.py:562-567`：v2 桥失败时发 `exec_failed`，detail
     `{"why":"spawn_failed","phase":"pre_spawn","error":...,"run_id":...,"attempt":...}`，
     并先 reclaim 预算 reservation；
   - `dispatch.py:572-574`：v1 物理层 supervisor 启动失败，同 phase；
   - `lifecycle_supervisor.py:755-756`：boundary is None → phase=`pre_spawn`、
     state=`spawn_failed`。
5. **已有同构上限模式可直接复用**：`_bump_counter`
   （`statemachine_v2.py:344-357`）+ 磁盘计数器文件（replan/timeout_retry），
   常量 `REPLAN_CAP=2`、`TIMEOUT_RETRY_MAX=1`、`L2_ATTEMPTS_MAX=1`
   （`statemachine_v2.py:49-51`）。写失败 fail-closed（返回 cap+1, exceeded=True）。
6. **dead-letter/duty 落点已存在**：`to_dead_letter`
   （`statemachine_v2.py:328-342`）写 `data/dead_letters/<pid>.json` +
   `sol_wake` + escalation_log；t38 `duty_triage` DEAD_LETTER→DUTY_REVIEW
   （事件由 `harness/duty_driver.py` 发射，`triggers_v2.yaml:120`）；
   t13 `duty_terminal` DUTY_REVIEW→DEAD_LETTER；`dead_this_step` 使 CLI 退出码 2。
7. **不回退 Sol 的现有顺序保证**：pre_spawn 分支（:451）先于 t26 release-review
   规则（:506-514）执行，故 release_review 包在 pre_spawn 失败时回 DISPATCHABLE
   而不是 SOL_ADJUDICATE；`SOL_ADJUDICATE` 非 terminal，仅剩 t22/t23 两个出口
   （`validate_transition_table` :166-230，:214 断言出口集）。
8. **实测现场**：`data/orchestration/terminal_epilogue.log` 中 16 目标 wave 有
   8-11 个任务 `spawn_failed`（`LifecycleError: CreateProcessW failed (5) 拒绝访问`），
   同一批 packet 在下一轮 manifest 中被再次选中 —— 正是本上限要终结的场景。
9. **现有测试缺口**：`tests/orchestration_v2/test_statemachine_v2.py:413-427`
   `test_pre_spawn_failure_preserves_refill_debt` 只断言“失败→DISPATCHABLE、
   current_run_id 清除、债务保留”，无上限/无 duty/无 dead-letter 断言。
   测试基建（`tests/orchestration_v2/conftest.py` 的 `make_root`/`emit_event`/
   `green_guards`）可直接支撑新矩阵，全部走 tmp_path 隔离根，零真实 spawn。
10. **触发表已有零-Sol duty 前哨模式**：`triggers_v2.yaml` `timeout_second` →
    `spawn_duty_officer`（“zero-Sol triage before DLQ”），pre_spawn 上限后的
    duty 落点可复用同一 action 与 `duty_triage` 事件。

---

## 二、风险 / 反例

R1. **计数器文件损坏 = 重置（fail-open）**：`_bump_counter` 用 `read_json(path, {})`
    读取，损坏/非 dict 时回退 `{}` → 计数从 0 重来，上限形同虚设；只有“写失败”才
    fail-closed。设计应把“不可读”也按超限/告警处理，并加测试固定。
R2. **只做 refill 层跳过、不做 statemachine 上限**：包永远停在 DISPATCHABLE，
    ledger 权威状态与“已死”语义不符，`wave_check`/观测/报告都会失真。
    上限必须落在 statemachine 权威侧，refill 跳过仅是纵深防御。
R3. **t26 误路由反例**：若把 pre_spawn 并入普通 `exec_failed` 或把上限逻辑放在
    t26 之后，release_review 包会经 t26 进 SOL_ADJUDICATE —— 违反“不回退Sol”。
    当前“pre_spawn 先于 t26”的顺序是正确性前提，测试必须固定该顺序。
R4. **stale run_id / attempt 误计**：pre_spawn 分支在 `stale_run_id_ignored`
    （:435-445）之后；若新实现直接计数而不校验 run_id/attempt，会把并发波次或
    旧代失败误计入上限，提前误杀可重试包。测试必须覆盖“不匹配不计”。
R5. **非 DISPATCHABLE/RUNNING 状态下的 pre_spawn 事件**：会走 off-table →
    `to_dead_letter`（含 sol_wake）。上限逻辑必须只作用于 DISPATCHABLE/RUNNING，
    其余状态保持现有 off-table 语义（行为回归点）。
R6. **“不回退Sol”的语义分叉**：`to_dead_letter` 固定写 `sol_wake/*.md` 与
    escalation_log `SOL_WAKE`。若验收把“不回退Sol”解读为“零 Sol 痕迹”，需为
    传输类死信增加抑制参数；若仅指路由（state ≠ SOL_ADJUDICATE），现行为即可。
    验收里必须二选一并断言。
R7. **“持续失败”口径**：总量 vs 连续量。总量与 replan/timeout 上限一致、单点
    实现；连续量需在成功（dispatched/subagent_stop）时清零，多一条写路径。
    建议 v1 用“每包 pre_spawn 事件总量”（run_id 匹配才计），并显式测试
    “成功打断后仍累计”的文档化行为。
R8. **预算/出生副作用**：cap 后不得再有 register_agent/reclaim 与新 run_id；
    测试需 stub 预算与物理 dispatcher 断言零额外出生尝试。
R9. **现有唯一 pre_spawn 测试是“债务保留”单测**：新上限不得破坏它——
    cap-1 次失败仍必须保留 refill 债务（DISPATCHABLE），只在 cap 次才转出。

---

## 三、建议测试（最小矩阵）

设计基线（供测试对齐）：`PRE_SPAWN_FAILURE_MAX = 3`（经
`config/orchestration_policy_v2.toml` 新增 `[spawn].pre_spawn_failure_max` 读取，
遵循 fail-closed 政策读取）；`bump_pre_spawn_counter(pid)` 复用 `_bump_counter`
写 `data/pre_spawn_failure_counters.json`；超限 → `to_dead_letter(..., reason=
"pre_spawn_failure_exhausted", detail={count,cap})`（reason 与 off_table_event
区分），随后走既有 t38 `duty_triage` → DUTY_REVIEW（duty_driver，零 Sol）；
`refill_consumer_v2.select_tasks` 加纵深防御：计数≥cap 的 DISPATCHABLE 不入选。

| ID | 场景 | 构造 | 期望 |
|----|------|------|------|
| S1 | 上限边界 | 同 run_id、attempt=0 连续 3 个 `exec_failed(pre_spawn)` | 第 1、2 次 → DISPATCHABLE（via `pre_spawn_failure_retryable`，计数 1、2，无死信）；第 3 次（==cap）→ DEAD_LETTER（via `pre_spawn_failure_exhausted`，计数 3）；`data/dead_letters/p1.json` reason 正确；state 全程 ≠ SOL_ADJUDICATE |
| S2 | 超限幂等 | 第 4 个 pre_spawn 失败 | 仍 DEAD_LETTER；不重复写死信、`dead_this_step` 不重复累加；history 无新增致命项 |
| S3 | 不回退 Sol 不变式 | cap 后对 DEAD_LETTER 施加 `sol_replan`/其他 Sol 事件；另做 release_review 包 3 连败 | 前者 off-table → 仍 DEAD_LETTER（reason off_table_event），绝不进 SOL_ADJUDICATE；后者 cap 前始终 DISPATCHABLE（pre_spawn 先于 t26），cap 后 DEAD_LETTER，history 无 t26/SOL_ADJUDICATE |
| S4 | duty 链 | cap 后发 `duty_triage`（t38）→ DUTY_REVIEW；再 `duty_retryable` / `duty_terminal` | DUTY_REVIEW 后按 enforce=true 语义：重试回 RUNNING 或 `duty_terminal` 回 DEAD_LETTER；全程零 SOL_ADJUDICATE；`enforce_duty=False` 时按既有 record-only 死信（回归） |
| S5 | refill 排除 | cap 后 `queue_sync_ledger` + `recompute` + `select_tasks` | pending/deficit/unfulfilled_demand 不再含该包；`select_tasks` 返回 []（cap 前仍入选） |
| S6 | 包隔离 | p1 超限、p2 新包 | p2 的 pre_spawn 失败仍回 DISPATCHABLE 且计数独立；p1 计数不变 |
| S7 | stale 不计 | attempt < 当前 attempts；run_id ≠ current_run_id 的 pre_spawn 失败 | 分别 `stale_generation_ignored` / `stale_run_id_ignored`；计数不变、状态不变（RUNNING/DISPATCHABLE 保持） |
| S8 | fail-closed 计数 | monkeypatch `atomic_write_json` 抛 OSError；另构造损坏计数器 JSON | 前者立即按超限 DEAD_LETTER（绝无无限重试）；后者按设计决策断言：不可读=超限（推荐）或至少不无限循环 |
| S9 | 预算/出生副作用 | cap 后 stub DispatcherV2/budget | 不再调用 register_agent/reclaim；无新 run_id；`dispatch` 调用次数=cap |
| S10 | post_spawn 判别 | RUNNING + `exec_failed` phase=post_spawn/缺省 | 走 t6 → FAILED（再经 t10 duty），不是 DISPATCHABLE；pre_spawn 计数不变 |
| S11 | 政策/清单同步 | 新 key 缺失 / manifest 未声明 | `OrchestrationPolicy` 读缺失 key 抛 PolicyError（fail-closed）；`validate_transition_table` 通过；`statemachine_v2_transitions.json` 与代码一致（若采用直接 DUTY_REVIEW 边则新增 t39 并同步 manifest；DLQ-first 设计则无需新 t，只改分支） |
| S12 | 全环 e2e（mock 传输） | `emit_event` 灌 planned→dag_assert_pass→dispatched→3×pre_spawn，`sm.step()` 逐步驱动；`run_once` + mock headless_wave 返回 spawn_failed | 终态 DEAD_LETTER、`dead_this_step==1`、step 退出码 2；refill manifest 在 cap 后不再含该包 |

落点：S1-S4、S6-S8、S10-S11 进 `tests/orchestration_v2/test_statemachine_v2.py`
（复用 `sm`/`_led` fixture 与 conftest `make_root`/`emit_event`）；
S5、S9、S12 进 `tests/orchestration_v2/test_refill_consumer_v2.py` 与
`tests/orchestration_v2/test_dispatch_v2.py`（stub 传输，不真实 spawn）。
全部为 tmp_path 隔离根单测，符合“只读、不派生、不碰 VPS”。
