# 失败自动重试接线审计（exec_failed/acceptance_fail → retry.py）

审计时间：2026-08-13 05:10–05:25；范围：E:\codex-LOOP\codex-loop-s-f2（只读）。
**注意：审计期间该仓库正被活体会话并发修改**（多个文件 mtime 落在 05:18–05:20），
本报告严格区分“历史事实（events 快照 05:12:18 之前）”与“当前树现状（05:20 之后）”。
未改动任何源码/配置/数据文件；仅新增本报告。

## 结论摘要

**历史根因（对应 events 中 retry_dispatch=0）：生产运行代码里不存在任何调用
`harness/retry.py` 的路径。** 证据：① data/events.ndjson（1023 行，冻结于 05:12:18）
有 93 条 `exec_failed`、55 条 `timeout`，但 `retry_dispatch/duty_review/budget_exhausted/
acceptance_fail/acceptance_pass` 全部为 0；② 同一时段生产运行的
`terminal_packet_epilogue` 每次输出均无 `retry_routes` 键（日志覆盖到 05:12:18），
即当时的 epilogue 只跑 state_machine+refill；③ 全库检索当时的 harness/hooks/config/
metering/install/.codex，`retry.py` 只被测试直接调用（`tests/golden/test_g2_retry_success.py`
等），无任何生产调用点。实际失败被父会话（LLM）当作新包直接 `dispatched` 消化：
同一包 parent-073b8e05… 5 次 `exec_failed` + 7 次 `dispatched`，ledger attempts 恒为 0，
分类、重试预算（run=6）、熔断（10 次/60s）从未参与——包括本应 dead_letter 的 401 token 失效。

**现状（05:18–05:20）：活体会话刚把接线补上（全部未提交）**——`terminal_packet_epilogue.py`
新增 `route_terminal_retries()`，对 ledger 中 FAILED/TIMED_OUT 包调用
`retry.py --packet <pid> --error <文本>`，随后再跑一次状态机消费事件；同时
t9/t37 目标改为 `DISPATCHABLE`（重试只入机械补位队列，物理出生由 refill 执行），
retry.py 守卫扩展为 `{"FAILED","TIMED_OUT"}` 并加了同 attempt 原子去重与 duty ticket。
新接线尚未在任何生产事件中产生 `retry_dispatch`（events 仍为 0，因为 05:12 后无新
terminal 运行），且仍存在覆盖缺口（见“风险”3–5）。

## 已验证事实

### 1. 事件历史（data/events.ndjson，1023 行，05:12:18 后未再增长）

| 事件 | 计数 | 说明 |
|---|---|---|
| exec_failed | 93 | 47 个包至少失败一次；单包最多 5 次（parent-073b8e05…） |
| timeout | 55 | 54 个包 |
| retry_dispatch | **0** | 任务所指 |
| duty_review / budget_exhausted / dead_letter 事件 | **0** | retry.py 三条失败路径从未发生 |
| acceptance_pass / acceptance_fail | **0** | t7/t8 边无生产者 |
| l2_requested / verdict_* | 0 | L1/L2 分层管线未在本历史中启动 |

### 2. 历史运行代码无 retry 路由（根因，已交叉验证）

- `data/orchestration/terminal_epilogue.log`（344 KB）与 `terminal_packet_status.json`：
  多次生产运行的 epilogue 输出 schema 均只有 `state_machine`/`refill`/`legacy_reports_wrapped`，
  **没有任何一次出现 `retry_routes` 键**；`route_terminal_retries` 是 05:20 才加入的。
- 05:12 时点的全库检索（harness/hooks/config/metering/install/.codex）：
  `retry.py` 仅出现在 README/AGENTS/triggers_v2.yaml 文档声明与测试调用中。
- `triggers_v2.yaml:84-85` 声明 `retry_dispatch_timeout: emitted_by: harness/retry.py`——
  是声明，不是调用。
- 父会话手动重派证据：parent-073b8e05… 事件序列
  `exec_failed(spawn_failed) → dispatch_refused → dispatched×4(pre_spawn supervisor_error,
  CreateProcessW 拒绝访问) → dispatched(成功) → subagent_stop → … → exec_failed(nonzero_exit,
  stderr_tail=token_invalidated 401) → dispatched → subagent_stop`；
  ledger 全 32 包 attempts 均为 0（24 REPORTED / 4 RUNNING / 4 TIMED_OUT，无 FAILED 存留）。

### 3. 当前树接线现状（05:18–05:20，未提交、进行中）

- `harness/terminal_packet_epilogue.py`（mtime 05:20:02，untracked）：
  `run()` 内顺序 = `StateMachine.step()` → `route_terminal_retries()`（对 FAILED/TIMED_OUT
  调用 retry.py，错误文本由 `_failure_text()` 从 exec_roster 的
  `job.failure(why/error/stderr_tail) + stop_reason + exit_code + stderr_path 尾部(≤8192)`
  组装，总长 ≤12000）→ `StateMachine.step()` 消费 retry_dispatch/duty_review/budget_exhausted
  → `run_refill_once()`（DISPATCHABLE 包由机械补位重派）。
- `statemachine_v2.py:111/140` 与 `statemachine.py:61`、`config/statemachine_v2_transitions.json`：
  t9/t37 目标改为 **DISPATCHABLE**（注释：重试准入不是物理出生，物理出生归 actuator）。
- `harness/retry.py`（mtime 05:18:39）：守卫 `state not in {"FAILED","TIMED_OUT"}` 即 no-op；
  同 attempt 通过 `has_retry_dispatch()` + `data/lifecycle/.events.lock` 原子去重；
  duty_review 分支写 `data/duty_review/<pid>.json` ticket；pid 文件名安全校验。
- 测试同步更新：`tests/golden/test_g2_retry_success.py` 断言 retry 后
  `state == "DISPATCHABLE"` 且 attempts==1（“retry admitted for physical refill”）；
  `tests/unit/test_retry.py`、`test_statemachine.py`、`tests/statemachine_paths/
  test_all_transitions.py` 均已改动（git status 05:20 新增这些 M）。
- 版本基线：HEAD=f1d6eac（2026-08-10 06:31 “Fable F2 delivery baseline”，v1 时代）；
  整个 v2 层（statemachine_v2.py、terminal_packet_epilogue.py、orchestration_epilogue.py、
  triggers_v2.yaml 等）都是 untracked 文件，未提交。

### 4. 输入源与配套状态（当前接线可用的事实）

- roster 失败信息（exec_roster.json，166 jobs）：
  - v1 nonzero_exit：`job.failure` 含 `why/stderr_tail/stderr_path`（log 中可见 401 全文）；
  - timeout：`job.failure=None`，但有 `stop_reason=timeout`、`exit_code=3221225786`、
    `stderr_path`——`_failure_text` 合成的 `"TIMED_OUT\ntimeout\n3221225786\n<stderr尾>"`
    可命中 retry_classes.yaml 的 `timeout` 类（`timed?[ _-]?out` 匹配 "TIMED_OUT"）。
- `retry_classes.yaml`：run_level_retry_budget=6、session_circuit_breaker=10、14 个类；
  `auth_failure`（401/token_invalidated）→ dead_letter；`resource_contention`
  （lock busy）→ retry。
- `data/.breaker.json` 不存在、`data/duty_review/` 不存在（熔断与 duty ticket 从未启用）。
- `refill_controller_v2.py:208` 只把 DISPATCHABLE 计入机械补位池——新设计
  （retry_dispatch→DISPATCHABLE）与补位机制正好衔接。
- `acceptance_pass/acceptance_fail`：全库只有两张状态表引用（statemachine.py:59-60、
  statemachine_v2.py:107-108），**零发射器**（测试中由测试手工 append）。

## 风险 / 反例

1. **历史反例（已发生）**：401 token_invalidated 属于 auth_failure（应 dead_letter），
   实际被父会话 5 连发重派，烧 token 且熔断计数从未累积。新接线生效后该类会被吸收，
   但历史上已造成的浪费不可回补。
2. **触发面缺口（最重要）**：`route_terminal_retries` 只在 `terminal_packet_epilogue.run()`
   内执行；该 run 仅由 `schedule_epilogue`（lifecycle_supervisor.py:789 的 finally，
   仅 v1 supervisor 且仅 canonical packet）与 orchestration_epilogue 的 `__main__`
   兼容分支触发。**statemachine 主循环/watchdog 路径（orchestration_epilogue.run_epilogue）
   不路由重试** ⇒ watchdog 判 TIMED_OUT 的包（ledger 现有 4 个）只有等后续某个包
   v1 终止触发 epilogue 才会被路由；若没有新终止事件，就一直卡在 TIMED_OUT。
3. **v2 supervisor 缺口**：`lifecycle_supervisor_v2.py` 无任何 schedule_epilogue 调用，
   v2 模式的 exec_failed 不会触发 retry 路由（当前 05:12 前事件即主要来自 v2/单波模式）。
4. **acceptance 缺口**：`acceptance_fail` 无发射器，t8（REPORTED→FAILED）在生产是死边；
   补 retry 路由不会自动让验收失败进入重试，需同时加 emitter（建议在
   `signals_collect.py` 聚合处：replay 的 exit_codes 含非 0 时 append
   `acceptance_fail` + replay 输出文本）。
5. **时序/幂等**：接线已做“先 step 后 retry 再 step”（顺序正确）；但 retry.py 本身不改
   ledger 的 state/attempts（依赖状态机消费 t9/t37 时 +1），若未来有人绕过该顺序
   （如在 ledger 仍 RUNNING 时调用），守卫会静默 no-op（exit 0）。另外 breaker 计数
   发生在 `has_retry_dispatch` 检查之前：同一 FAILED 包若被重复调用 retry.py
   （状态机尚未消费已 append 的 retry_dispatch），每次都会记录一次熔断失败，可能虚计
   熔断；当前 epilogue 同锁串行下风险低，但外部重复调用路径需留意。
6. **双预算**：t37 有独立 timeout_retry 计数器（上限 1），retry.py 又有 per-class
   max_retries 与 run 级 budget=6；timeout 场景可能出现 retry.py 已 append
   retry_dispatch 而状态机按 t37 超限判 DLQ 的账实错位（事件已发但未成行）。
7. **分类缺口**：`CreateProcessW failed (5): 拒绝访问。`（WinError 5，中文）不命中
   permission_denied（仅英文 pattern）→ off-table → duty_review（cold_start 下
   enforce=false → DLQ）。历史 93 条 exec_failed 中大量属于此类，接线后不会重试而是
   进 DLQ——属预期，但若想重试需补中文 pattern 或显式接受。
8. **并发写风险**：仓库正被活体会话修改（本次审计期间 statemachine_v2.py、retry.py、
   terminal_packet_epilogue.py 均被改写），任何基于快照的结论都可能被下一分钟覆盖；
   且全部未提交，无版本锚点。
9. **文档漂移**：`statemachine.py:229` 注释仍写 “retry_dispatch already moves
   FAILED -> RUNNING”，与当前表（DISPATCHABLE）不一致。

## 最小非 LLM 接线建议（与当前进行中的实现对齐）

当前实现已覆盖主路径（terminal epilogue 内 step→retry→step→refill，t9/t37→DISPATCHABLE，
同 attempt 原子去重）。补齐建议（按优先级）：

1. **把 retry 路由提升到 `orchestration_epilogue.run_epilogue`**：在 `StateMachine.step()`
   之后调用同一 `route_terminal_retries()`，使 statemachine 主循环/watchdog 产生的
   TIMED_OUT 与 v2 supervisor 失败都能被路由，而不是只依赖 v1 终止事件。
2. **v2 supervisor 失败分支补 `schedule_epilogue`**（或在 v2 内直接调用 retry 路由），
   保证 single_v2 模式的 exec_failed 也进闭环。
3. **补 `acceptance_fail` 发射器**：在 L0 结论处（`signals_collect.py` 已持有 replay 输出
   与 exit_codes）append `acceptance_fail` 事件（含 replay 输出文本作为分类输入），
   激活 t8→FAILED→retry 路径。
4. **错误文本下沉到发射点**：v2 `finalize_child` 的 nonzero_exit 事件补
   `stderr_tail/stderr_path`（v1 已有），避免 `_failure_text` 只能靠 roster 兜底。
5. **修复 statemachine.py:229 过期注释**；评估是否把 retry.py 的
   `has_retry_dispatch` 检查提前到 breaker 计数之前，消除重复调用的虚计熔断。

失败分类输入来源（现状可用清单）：
- exec_failed 事件 detail：why / exit_code / stderr_path / stderr_tail（v1）、
  validation / quarantined（v2）；timeout 事件：why / limit_s / grace_s。
- exec_roster job：failure（why/error/stderr_tail）、stop_reason、exit_code、
  stderr_path（`_failure_text` 已全部使用，含 ≤8192 尾部、总 ≤12000）。
- stderr.log 落盘：`data/reports/<pid>/stderr.log`（已验证存在，含 401 全文）。
- L0 验收：acceptance_replay.sh 的 replay JSON（commands/rc/test_count）→
  signals_collect.py 的 signals JSON（含 retry_count=ledger attempts、run_level_budget=6）。

## 建议测试

1. **全链路回归（不直调 retry.py）**：构造 dispatched→exec_failed（文本含 ETIMEDOUT），
   经 `terminal_packet_epilogue.run()`（或新提升后的 epilogue 路径）断言
   retry_dispatch 恰好 1 条、step 后 state=DISPATCHABLE、attempts=1、refill 重派后 RUNNING；
   镜像 golden G2 但走 actuator 而非手工调用。
2. **watchdog TIMED_OUT 触发**：RUNNING 包超时（watchdog 事件）→ 经
   orchestration_epilogue 路由 → retry_dispatch → DISPATCHABLE；第二次超时 → DLQ
   （t37 双预算，断言无重复 retry_dispatch）。
3. **v2 supervisor 失败**：finalize_child nonzero_exit → 触发 epilogue → 分类发生。
4. **auth 死信**：401/token_invalidated → dead_letter_permanent + budget_exhausted，
   无重派（封堵历史反例）。
5. **off-table**：未知文本 → duty_review + `data/duty_review/<pid>.json` ticket、exit 4。
6. **预算/熔断**：run 级 6 次耗尽 → dead_letter；60s 内 10 次同类失败 → circuit_open +
   duty_review；重复调用不虚计熔断（若采纳建议 5）。
7. **acceptance_fail emitter**：replay rc≠0 → acceptance_fail → t8 → FAILED → 分类。
8. **WinError5 中文**：`CreateProcessW failed (5): 拒绝访问。` 固化当前 off-table→duty_review
   行为（或补中文 pattern 后断言重试/死信）。
9. **幂等**：同一 FAILED 包连续两次 epilogue → 仅 1 条 retry_dispatch、attempts 只 +1。
