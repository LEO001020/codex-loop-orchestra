# 超时自动重试接线 — 只读追踪报告

日期：2026-08-13 ｜ 范围：`E:\codex-LOOP\codex-loop-s-f2`（只读，未改任何源文件）
基线注意：仓库正被活跃进程写入（`data/lifecycle/.exec_roster.lock` 曾被占用）；`config/statemachine_v2_transitions.json`（05:18:39）、`harness/statemachine_v2.py`（05:20:02）、`harness/retry.py`（05:18:39）时间戳相差分钟级，当前树处于“接线中”状态。

## 一、已验证事实：timeout → retry_dispatch 的生产调用链

### 1. timeout 事件的生产者（3 个，全部写入同一 events.ndjson）

| 生产者 | 位置 | 说明 |
|---|---|---|
| 生命周期监督器（worker 超限被杀） | `harness/lifecycle_supervisor.py:654-655`（判定）→ `:670-674` append `timeout` | 带 `run_id`/`attempt`；`Store.events_path = data/events.ndjson`（`:136`）；去重 `event_exists`（`:201-223`） |
| v2 watchdog（step 内审计 + 就地置 TIMED_OUT） | `harness/statemachine_v2.py:598-641`，追加在 `:635-640` | `_append_event`（`:291-292`）持 `data/lifecycle/.events.lock`；下一轮被幂等吸收（`:494-502`） |
| v1 watchdog（仅 cold_start/shadow 直连） | `harness/statemachine.py:290-334`（`:327` 追加） | v1 无 t37；`statemachine.py:391-394` 仅 layered 委派 v2 |

非状态机事件 `spawn_initializing_timeout`（`harness/dispatch.py:269-270`）是节流诊断，非 t5 生产者（见风险 7）。

### 2. timeout → TIMED_OUT 的消费与“重试申请”桥

- `terminal_packet_epilogue.run()`（`harness/terminal_packet_epilogue.py:110-160`）：
  - `:130` `StateMachine(paths).step()` 应用 timeout → t5 → TIMED_OUT（`statemachine_v2.py:104`）；
  - `:132` `route_terminal_retries(paths, states)` → 对 `{"FAILED","TIMED_OUT"}`（`:51-53`）子进程调 `harness/retry.py --packet <pid> --error <failure_text>`（`:54-60`，30s 超时）；
  - `:134-135` 有路由时立刻再 `step()` 一次——即 t37/t9 的应用点；
  - `:137-140` 之后 `run_refill_once`（只选 DISPATCHABLE，见下）。
- 触发源：`lifecycle_supervisor.py:780-789`（任意终态边缘，含 timeout）→ `schedule_epilogue`（`:468-524`）→ 拉起 `terminal_packet_epilogue.py`（`:516-518`）。

### 3. retry_dispatch 的生产者（唯一）与幂等闸

- `harness/retry.py:202-211`：`new_attempt = attempts+1`（`:202`）→ `with locked(data/lifecycle/.events.lock)`（`:203`）→ `has_retry_dispatch(pid, attempt)` 全量扫描去重（`:48-69`）→ `append_event(pid, "retry_dispatch", decision)`（`:211`，带 `attempt`）。
- 状态闸 `retry.py:152-161`（未提交改动：`not in {"FAILED","TIMED_OUT"}` → no-op）。git diff 证实相对基线 `!= "FAILED"` 为新增。
- 分类表 `config/retry_classes.yaml:33-37`（timeout 类，action retry，max_retries 2）；运行级预算 6（`:26`）；检查在 `retry.py:145,198`。
- 生产调用方：唯一是 `terminal_packet_epilogue.route_terminal_retries`；harness/hooks/config 全量 rg 无其他 retry.py 调用点。

### 4. retry_dispatch 的消费（t37）与预算

- `harness/statemachine_v2.py:137` `("TIMED_OUT","retry_dispatch") → ("RUNNING",37)`；`:108` t9 FAILED→RUNNING。
- 预算 `:545-551`：`bump_timeout_retry_counter`（`data/timeout_retry_counters.json`，cap=TIMEOUT_RETRY_MAX=1，`:74`）→ 第二次 t37 直接 `timeout_retry_exhausted` DEAD_LETTER；`:570-571` t9/t37 应用时 `attempts+1`；`:566-567` 事件带 run_id 时设 `current_run_id`。
- 表校验只查 t37 存在（`statemachine_v2.py:236-239`），不查目标状态。

## 二、关键矛盾：retry_dispatch 目标状态漂移（最小插入点所在）

同一棵树存在两套互相矛盾的声明：

**意图 = DISPATCHABLE**（refill 执行器负责物理出生）：
- `config/statemachine_v2_transitions.json:18`（顶层，05:18 修改）t37 → DISPATCHABLE
- `harness/retry.py:5-6` 头注释（未提交）FAILED/TIMED_OUT->DISPATCHABLE；The refill actuator owns physical birth
- `tests/orchestration_v2/test_statemachine_v2.py:66, 87-98`（t37 → DISPATCHABLE；第二次 → DEAD_LETTER）
- `tests/statemachine_paths/test_all_transitions.py:27`（t9 → DISPATCHABLE）
- `tests/unit/test_retry.py:56-57` 注释（applied retry_dispatch → packet DISPATCHABLE）
- `harness/refill_consumer_v2.py:150-155`：select_tasks 只选 `state == "DISPATCHABLE"`——v2 自动物理出生的唯一通道

**实现 = RUNNING**（顶层代码与旧测试）：
- `harness/statemachine_v2.py:108, 137`；`tests/test_statemachine_v2.py:65, 91`
- 嵌套部署副本 `codex-loop-s-f2/codex-loop-s-f2/harness/statemachine_v2.py:137` 及 `codex-loop-s-f2/codex-loop-s-f2/config/statemachine_v2_transitions.json:18` 均为 RUNNING

`layered_gate` 只检查必需转换“存在”（`harness/layered_gate.py:57-59, 198-218`），不比对目标，漂移不会被门禁拦截。

### 最小可插入位置（推荐）

把 `harness/statemachine_v2.py:108`（t9）与 `:137`（t37）的目标从 RUNNING 改为 DISPATCHABLE，并同步顶层旧测试（`tests/test_statemachine_v2.py:65, 91`）与嵌套副本。单点改动即可闭环：
1. t37/t9 应用后进入 DISPATCHABLE，`refill_consumer_v2.select_tasks`（`:150-155`）→ `headless_wave` → `DispatcherV2`（`dispatch_v2.py:508-509` 新 run_id、`:536-549` 出生）→ `dispatched`(t3) → RUNNING；
2. watchdog 只扫 RUNNING（`statemachine_v2.py:604-605`），消除“重试申请后、物理出生前被旧 spawn_times 再次超时”窗口；
3. `current_run_id` 由新 `dispatched` 建立（`:566-567`），无需 retry_dispatch 携带 run_id。

若坚持 RUNNING 为目标，最小插入点退化为三件套（严格更多代码与竞态面）：(a) retry_dispatch 携带新 run_id，或在 t5/t6/t9/t37 应用时清 `current_run_id`（`statemachine_v2.py:563-572`）；(b) 重试应用时刷新 `spawn_times.json`（现仅 `dispatch.py:409-425` 物理出生时刷新）；(c) `refill_consumer_v2.py:150-155` 增选“已重试未出生”的 RUNNING。

## 三、锁与幂等要求

1. **events.ndjson 单一写锁域**：`data/lifecycle/.events.lock`（`orchestration_common.py:164-165`）。现写者已共用：`statemachine_v2._append_event`（`:291-292`）、`lifecycle_supervisor.append_event_once`（`:202-223`）、`retry.py:203`、`dispatch.py:144`。新增发射点必须挂同一把锁；`retry.py:43-46` 裸 append 仅在调用方持锁时安全。
2. **同 attempt 重复 retry_dispatch 是致命的**：RUNNING/DISPATCHABLE 下再收 retry_dispatch 属 off-table → DEAD_LETTER（`statemachine_v2.py:513-517`）。三重闸（状态闸 `retry.py:152-161`、`has_retry_dispatch` `:48-69/203-206`、supervisor 式 event_exists `:208-215`）必须保留。
3. **retry.py 的 ledger 读不在 progress_ledger.lock 内**：`retry.py:142-145` 读 attempts 时，`step()`（`statemachine_v2.py:653`）可能并发应用 t9/t37。重复追加仍会被 has_retry_dispatch 拦截（同 attempt），但状态闸读旧快照；最小加固是把状态闸 + attempt 计算移进 `retry.py:203` 的 events 锁内（或让 retry.py 在 step 临界区内被调用）。
4. **timeout_retry_counters.json 只能有一个写者域**：现由 apply_event 在 `progress_ledger.lock` 内写（`statemachine_v2.py:344-362, 545-551, 653`）。retry.py 不得再碰该计数器，否则 read-modify-write 竞态。
5. **事件必须带 attempt**：`retry.py:210-211` 已带；生产事件不得像测试那样裸发（`tests/test_layered_e2e.py:172-173` 仅测试用），否则 `has_retry_dispatch` 与 `stale_generation_ignored`（`statemachine_v2.py:419-424`）失效。
6. **run_id 交接不变量**：一个 RUNNING 代只能有一个 `current_run_id`。DISPATCHABLE 目标下由新 `dispatched`(t3) 建立；RUNNING 目标下交接断裂（见风险 2）。

## 四、风险 / 反例

1. **RUNNING 目标下的死循环死信**（当前实现）：t37 → RUNNING 后 refill 不选 RUNNING（`refill_consumer_v2.py:150-155`）；spawn_times 仍是旧代戳 → 下一步 watchdog 再超时（`statemachine_v2.py:606-640`；current_run_id 仍为旧 run_id，stale 闸 `:431-439` 不拦截）→ 第二次 retry_dispatch → 计数器超限 DEAD_LETTER（`:545-551`）。自动流里 t37 重试在物理出生前必被绞杀。
2. **run_id 交接陷阱**：retry_dispatch 不带 run_id（`retry.py:210-211` 无 uuid），current_run_id 保留旧代；新代 dispatched(新 run_id) 被 `duplicate_generation_ignored` 吞掉（`statemachine_v2.py:440-449`），新代终态事件被 `stale_run_id_ignored` 丢弃（`:431-439`）→ 卡 RUNNING。语义已由 `tests/orchestration_v2/test_statemachine_v2.py:401-425` 固化，但无测试覆盖“重试后再出生”的交接。
3. **watchdog 再超时窗口**：spawn_times 只在物理出生时刷新（`dispatch.py:409-425`；`dispatch_v2.py:508-509`）；重试申请与出生之间 watchdog 用旧戳（`statemachine_v2.py:606-616`），P1-9 合成回退（`:583-592`）因旧条目存在而不生效。
4. **双份部署副本漂移**：嵌套 `codex-loop-s-f2/codex-loop-s-f2/` 的 t37/t9 与 config JSON 仍为 RUNNING；按副本部署会回滚修复。layered_gate 不比对目标（`:198-218`），门禁放过。
5. **分类劫持**：TIMED_OUT 的失败文本来自 roster/stderr（`terminal_packet_epilogue.py:14-44`）；若尾部文本先命中 permission_denied/compilation_error 等非 retry 类（`retry_classes.yaml:98-109, 76-91`），超时包走 DUTY_REVIEW/DLQ 而非 t37。连续两次同类先被 2_consecutive_same_class 分到 duty（`retry.py:174-181`），与 t37 计数器形成两套重叠预算（另：run_level_retry_budget=6 全运行共享，`retry.py:145,198`）。
6. **cold_start/shadow 模式回归**：`lifecycle_supervisor.py:780-789` 不分模式触发 epilogue，v2 step 总会应用 t37；但 `statemachine.py reconcile` CLI 在非 layered 走 v1 表（`statemachine.py:391-394`），v1 无 (TIMED_OUT, retry_dispatch)（`statemachine.py:66`）→ 同一事件流被 v1 判 off-table → DEAD_LETTER + Sol wake。混合工具期超时重试会被 v1 reconcile 绞杀。
7. **非状态机 timeout 事件**：`dispatch.py:269-270` 的 spawn_initializing_timeout 不在 INFO_EVENTS（`statemachine_v2.py:151-165`），且带在册 pid → 若被 step 读到会 off-table 死信（仅 throttle 失败时发生）。
8. **epilogue 双轨**：`orchestration_epilogue._run_epilogue_locked`（`orchestration_epilogue.py:96-129`）只 step+refill，不路由重试；`terminal_packet_epilogue` 才路由。两者持不同锁（`.epilogue.lock` vs `.terminal_packet.lock`），step 均落 `progress_ledger.lock`，重试发射本身串行安全；唯一未上锁的读是 retry.py 的 ledger 读（要求 3）。

## 五、建议测试

1. **表/清单一致性**（当前为红）：断言 `T[(TIMED_OUT,"retry_dispatch")] == T[(FAILED,"retry_dispatch")] == ("DISPATCHABLE",37/9)`，且与 `config/statemachine_v2_transitions.json:18` 及嵌套副本（`codex-loop-s-f2/codex-loop-s-f2/harness/statemachine_v2.py`、其 config JSON）三者一致。
2. **E2E 超时重试**（仿 `tests/orchestration_v2/test_statemachine_v2.py:226-247` + `tests/orchestration_v2/test_terminal_packet_epilogue.py:113-126`）：RUNNING + 旧 spawn_times → step=TIMED_OUT → route_terminal_retries → 恰好 1 条 retry_dispatch → step=DISPATCHABLE 且 attempts=1 → refill_consumer_v2.select_tasks 能选中 → 模拟出生 dispatched(run_id=new, attempt=1) → RUNNING 且 current_run_id=new → 旧代 timeout(run_id=old) 被 stale_run_id_ignored 吸收、状态不变。
3. **幂等重入**：同一 TIMED_OUT 连续两次 route_terminal_retries → 只有 1 条 retry_dispatch、0 条 duty_review、0 死信（现有 `tests/unit/test_retry.py:37-66` 只覆盖 FAILED，补 TIMED_OUT 变体）。
4. **预算封顶**：attempts=1 的 TIMED_OUT 再路由 → retry_dispatch → timeout_retry_exhausted DEAD_LETTER + dead_letters/<pid>.json（apply 级已覆盖于 `tests/orchestration_v2/test_statemachine_v2.py:87-98`，补 route 级）。
5. **watchdog 不再误伤**：DISPATCHABLE（重试后）状态下带旧 spawn_times 连续 step 两次 → 无 timeout、无死信；重新 RUNNING + 新戳后旧代事件不触发。
6. **并发**：两个并发 terminal_packet_epilogue（或并发 step + retry.py）处理同一 TIMED_OUT → 恰好 1 条 retry_dispatch、无 DEAD_LETTER（验证 events 锁 + has_retry_dispatch 原子性）。
7. **run_id 交接**（若保留 RUNNING 目标）：retry_dispatch 携带新 run_id → 后续同 run_id dispatched 走 generation_dispatch_confirmed（`statemachine_v2.py:486-493`）——当前该路径断裂，测试应钉死所选语义。
8. **分类劫持**：TIMED_OUT + stderr 含 permission denied → duty_review、绝不 retry_dispatch（记录“超时重试依赖分类表”这一事实）。
9. **副本漂移守卫**：断言嵌套包 t9/t37 与顶层一致（部署回归防线，当前为红）。
