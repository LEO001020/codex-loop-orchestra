# 有限重试发布门审计（FAILED/TIMED_OUT/pre_spawn 生产链）

- 审计时间：2026-08-13 06:05–06:11（Asia/Shanghai）
- 范围：`E:\codex-LOOP\codex-loop-s-f2`，只读。未改文件、未跑服务、未派子 agent、未触 VPS。
- 关联前置报告：`reports/retry-wiring-audit-20260813/report.md`（05:10–05:25，确认接线刚补、风险点 1–9）。本报告不重复其结论，聚焦「有限重试预算与生产者缺口」。

---

## 结论摘要（先行）

1. **`FAILED`/`TIMED_OUT` 的 `retry_dispatch` 只有一个生产者调用点**：`terminal_packet_epilogue.run()` 里 step → `route_terminal_retries` → step（`terminal_packet_epilogue.py:130-139`）。另一条主循环 `orchestration_epilogue` 与 v2 supervisor 分支都**没有**接线（`orchestration_epilogue.py:73` step 后直接进 plan/l2，无 retry；`harness/lifecycle_supervisor_v2.py` 全文无 `schedule_epilogue`）。watchdog / 主循环使包进入 `TIMED_OUT` 后，若没有后续 v1 终止事件触发 epilogue，包一直卡在 `TIMED_OUT`，无生产者。
2. **`pre_spawn` 无限重试**：`statemachine_v2.py:454-477` 对 `exec_failed(phase=pre_spawn)` 直接回 `DISPATCHABLE`（+1 `pre_spawn_failures`），由 refill 重派；**不经过 `retry.py`**，所以分类表、`max_attempts/max_retries`、run_level_retry_budget、session_circuit_breaker 全部绕开。账本 `attempts` 恒为 0（只在 t9/t37 才 +1，`statemachine_v2.py:574-575`），严格满足「无限 pre_spawn attempts=0」。上限只有 `PRE_SPAWN_FAILURE_MAX=3`（`statemachine_v2.py:76`），超限直接 `DUTY_REVIEW`——没有 backoff，且从未把错误文本交给分类。
3. **`TIMED_OUT` 双预算 + 账本漂移**：`TIMEOUT_RETRY_MAX=1`（`statemachine_v2.py:74, 545-551`）与 `retry_classes.yaml:33-38 timeout.max_retries=2` 是两套预算；`retry.py:145-216` 从不写 `attempts`（账本只在 t9/t37 时 +1），timeout 计数器又另存 `timeout_retry_counters.json`：`retry.py` 可能已 append `retry_dispatch`，应用到 t37 时却因计数器超限判 DLQ——事件与账本不一致。
4. **历史上已发生的反例**（前值报告证据）：93 条 `exec_failed` / 55 条 `timeout` 全部 0 条 `retry_dispatch`，401 token 失效应走 auth_failure→DLQ 却被父会话 5 连派，熔断从未累计。新的接线只覆盖 v1 终止 epilogue 这一路。

--- 
## 关键代码引用（证据）

### 生产者/消费者全景
| 位置 | 角色 | 事实 |
| --- | --- | --- |
| `harness/terminal_packet_epilogue.py:47-65` | retry 唯一生产调用点 | `route_terminal_retries` 对 states 中 FAILED/TIMED_OUT 调用 `retry.py` |
| `harness/terminal_packet_epilogue.py:130-139` | 调用序列 | step → route_terminal_retries → （若有路由）再 step → refill |
| `harness/orchestration_epilogue.py:73` | 主循环**不**路由 retry | step() 之后无 retry，TIMED_OUT 包无生产者 |
| `harness/lifecycle_supervisor_v2.py` | 无 epilogue 调度 | 文件无 `schedule_epilogue`，v2 失败不进这条链 |
| `harness/lifecycle_supervisor.py:752-789` | v1 发射端点 | `phase = "pre_spawn" if boundary is None else "post_spawn"`；`terminal = boundary is not None`，**pre_spawn 失败不调度 epilogue**（:775, :780-789） |
| `harness/statemachine_v2.py:454-477` | pre_spawn 无上限回 DISPATCHABLE | `pre_spawn_failures+1`，超限 → DUTY_REVIEW；从不分类、不 backoff、不进预算 |
| `harness/statemachine_v2.py:573` | attempts=0 保持 | 只有 `dispatched` 清 0 且 attempts 只在 t9/t37（:574-575）+1，pre_spawn 重试不计入 |
| `harness/statemachine_v2.py:545-551` | t37 独立预算 | `timeout_retry_counters.json`，cap=1（:74），与分类表 max_retries=2（`retry_classes.yaml:33-38`）冲突 |
| `harness/retry.py:153-162` | 状态守卫 | 非 FAILED/TIMED_OUT → `retry_already_scheduled` no-op |
| `harness/retry.py:164-172` | 熔断 / off-table | breaker=10/60s（`retry_classes.yaml` 顶部 `session_circuit_breaker: 10`）；OFF-table → DUTY_REVIEW |
| `harness/retry.py:201-216` | 预算与原子 append | max_att；run_budget=6；同 attempt 通过 `has_retry_dispatch` + lock 去重；append `retry_dispatch` |
| `harness/dispatch_v2.py:559-571` | 出生失败发 exec_failed(phase=pre_spawn) | reclaim budget 后 raise DispatchBlocked，外层 `dispatch()` catch 到后 append `dispatch_refused`（INFO 事件） |
| `harness/dispatch.py:560-575` | v1 dispatcher 发射 | `append_event("dispatched")` 先于 supervisor 启动；supervisor 启动失败 → `exec_failed(phase=pre_spawn)` |
| `triggers_v2.yaml:84-86` | 声明 face | `retry_dispatch_timeout emitted_by: harness/retry.py, budget=timeout_retry_max=1`——声明 vs 现实：retry.py 只有一处调用点 |

## 目标缺陷路径（三条，逐条给证据 + 状态转移）

### 路径 A — pre_spawn 出生阶段失败：无生产者的「无限 DISPATCHABLE 回路」

事件时序（代码证据逐行）：

```
DispatcherV2._spawn
  ├─ dispatch_v2.py:533-534   budget.register_agent(reservation_id)  → reserve-before-birth
  ├─ dispatch_v2.py:536-555   dispatch_v1.dispatch_single(...)        抛异常
  └─ dispatch_v2.py:559-571   except → budget.reclaim(reservation)
                               → self._event(pid, "exec_failed",
                                             {phase: "pre_spawn", ...})
                               → raise DispatchBlocked("spawn failed")
DispatcherV2.dispatch (外层)
  └─ dispatch_v2.py:470-477   catch DispatchBlocked → _event("dispatch_refused", {error})
                              （statemachine_v2.py:149 列于 INFO_EVENTS，= 状态机不变）

dispatch.py:560-575 的等价 v1 路径（supervisor 未出生同事件形态）：
  append_event("dispatched") ──必须先于 supervisor 启动写，避免 off-table race
  try:
      supervisor_pid = launch_supervisor(...)
  except OSError:
      append_event("exec_failed", {phase:"pre_spawn", why:"supervisor_launch_failed"})
      raise
```

状态机处理（`statemachine_v2.py:454-477`）：

```
DISPATCHABLE ──exec_failed(phase=pre_spawn)──► DISPATCHABLE (pre_spawn_failures += 1)
RUNNING      ──exec_failed(phase=pre_spawn)──► DISPATCHABLE (同上，附 run_id)
DISPATCHABLE (pre_spawn_failures > PRE_SPAWN_FAILURE_MAX=3)
              ──► DUTY_REVIEW (via pre_spawn_failure_exhausted)
```

缺陷 4 层（每一层都独立成缺）——

1. **无 retry_dispatch 生产者**：exec_failed(phase=pre_spawn) 直接被守卫规则回 DISPATCHABLE（`statemachine_v2.py:454-477`），永远不会触碰 `FAILED`/`TIMED_OUT`，而 `route_terminal_retries`（`terminal_packet_epilogue.py:51-53`）只路由 FAILED/TIMED_OUT。
2. **attempts=0**：账本 attempts 只在 t9/t37 时 +1（`statemachine_v2.py:574-575`），pre_spawn 回路不计数；同时 v1/v2 dispatcher 的 `attempt = self._packet_attempt(pid)` 用同值生成的 run_id `f"{pid}-a{attempt}-..."`（`dispatch_v2.py:508-509`）→ 同一包反复出生失败会产生**同 attempt 号不同 run_id** 的轨迹，无法在账本里区分次数。
3. **绕过 retry_classes.yaml**：错误文本从未交给 `retry.py`（routes are `spawn_failed`/`supervisor_launch_failed` — `dispatch_v2.py:563` / `dispatch.py:571`），表里的 max_attempts、run_level budget、session_circuit_breaker 全部失效。
4. **没有 backoff**：回路内 refill 一旦启动就立即重派（`refill_controller_v2.py:198-208` 将 DISPATCHABLE 计入补位池），burst 等生在瞬时（CreateProcess OSError / `agent thread limit` / WSL adapter 抖动）会以全速重复，反而不是一个连续类失败序列——`retry.py` 的熔断永远不会看到它（因为 retry.py 还没被调用过）。

**无穷也不会真正无穷**：有 PRE_SPAWN_FAILURE_MAX=3（`statemachine_v2.py:76`）。但这是「粗暴截流」，不是预算——越界直接送出 `DUTY_REVIEW`，中间完全没有走重的第三次机会，且任何一次 retry 都没有类别信息。

### 路径 B — TIMED_OUT：有 t37 却没生产者；双预算 + 账本漂移

事件时序：

```
RUNNING ──timeout (watchdog / supervisor limit hit)──► TIMED_OUT  (t5, statemachine_v2.py:105)
生命来源:
  1. watchdog (statemachine_v2.py:598-645)       ← RUNNING + spawn_times/job binder + elapsed > limit
  2. supervisor timeout  path                    ← lifecycle_supervisor 的等等
TIMED_OUT 有 t37 边:
  TIMED_OUT ──retry_dispatch──► DISPATCHABLE   (statemachine_v2.py:140, config/statemachine_v2_transitions.json:18)
  TIMEOUT_RETRY_MAX = 1        (statemachine_v2.py:74)
  retry_classes.yaml timeout.max_retries = 2   (retry_classes.yaml:33-38)
```

缺陷 3 层——

1. **TIMED_OUT 在主循环无生产者**：唯一调用点在 `terminal_packet_epilogue.run()`（:130-139），而该入口只由 v1 supervisor `terminal` 时调度（`lifecycle_supervisor.py:780-789`）。主循环 `orchestration_epilogue.py:73` step() 之后**没有** route_terminal_retries，v2 supervisor（`lifecycle_supervisor_v2.py`）又**无** schedule_epilogue。任何被主路的 watchdog 判 TIMED_OUT 的包，**只有在同 Run 内又有另一个 v1 包终止时**才会被挨近路由；否则就卡在 TIMED_OUT，下一会话同样不复现。
2. **双预算**：`TIMEOUT_RETRY_MAX=1`（t37 在状态机边 + `timeout_retry_counters.json`）vs `timeout.max_retries=2`（retry.py 决定）。`retry.py` 可能已 append retry_dispatch（override allowance=2），落到状态机 apply 时计数器超限 → t37 判 DLQ（statemachine_v2.py:545-551）→ **事件已出口但不成行**。
3. **账本 attempts 不增**：`retry.py` 从不写 `attempts`（只在 t9/t37 apply 时 +1，`statemachine_v2.py:574-575`）；因此 `TOTAL_RETRIES`（retry.py:146）跟 retry.py 本身发出的 `attempt` 号（retry.py:207, 215）会出现账目错位（账本 0，attempt 1），重复调用时の加重亦受影响（见 D 节）。
4. **分类劫持**：`_failure_text` 会给 timeout 包拼 `TIMED_OUT ...`（前值报告 §3），可命中 timeout class；但若 stderr 尾部同时包含 permission_denied/auth_failure 之类将先命中后者（patterns 自上而下首个命中，`retry.py:149-151`），超时包会被走 DLQ/duty_review 而非 t37，且这条路径与 timeout_retry_counters 完全解耦。

### 路径 C — FAILED：生产者唯一且偏差风险

类似 B。FAILED 的 t9（retry_dispatch→DISPATCHABLE）依赖 `route_terminal_retries` 准确路由：

- **重复熔断计数**：`retry.py:164` 的 `breaker(record_failure=True)` 在 `has_retry_dispatch` 原子去重（:208-216）**之前**。同一个 FAILED 包若被重复调用（epilogue 重叠 exec、竞事件）但 retry_dispatch 已经出口（同 attempt，所以 route_terminal_retries 重复路由是安全的），每次调用都会在 `.breaker.json` 多记一次 fail，虚增熔断。
- **dispatch_refused 路径**：DispatcherV2.dispatch (dispatch_v2.py:470-477) catch 任何 DispatchBlocked——含 k3_unavailable、sol_budget_blocked、parent_sandbox_invalid——都 append `dispatch_refused`。它是 INFO（statemachine_v2.py:149），状态机不动。包仍然 DISPATCHABLE，同 attempts=0。这些 route-level拒绝是指块发的（`route_ledger.ndjsonl` 已报复债），但 terminal epilogue 不知道它们，永远也不会退成 FAILED 让分类表处理。

---

## 最小表驱动有限重试方案（设计）

原则：
1. **单一生成处理器**：所有失败统一汇聚到 `retry.py`（表驱动分类 + full-jitter backoff + 预算 + 熔断）；不再让 statemachine 另行回 DISPATCHABLE。
2. **单一权威账本**：账本 `attempts` 是唯一权威重试计数；废除 `timeout_retry_counters.json` 双预算。retry.py 决策时与账本一起原子写下；状态机 apply 只做「账本 vs 事件 generation 校验」。
3. **表 > 常量**：`PRE_SPAWN_FAILURE_MAX`、`TIMEOUT_RETRY_MAX` 这两个硬编码常量迁移到 `retry_classes.yaml`（phase-aware class / class.max_retries），删除状态机里的硬编码；表加 `phase` 字段或新增 `pre_spawn` class。
4. **普通任务绝不回退 Sol**：DUTY_REVIEW 走 duty_driver 机械化处置（enforce=true），仅高险信号会拉 Sol；retry 路径永不 spawn sol 角色。`retry.py` 仅以零代码 process 分类，零 LLM。
5. **生产者统一在 step 内消费失败**：让 `route_terminal_retries`（或内联 retry 逻辑）在每个 step 中都运行，取代只在 v1 terminal epilogue 钩子的现状，使 watchdog/v2 supervisor/exec_failed 所有路径都有生产者。

### 最小 schema 修改（`retry_classes.yaml`，向后兼容）

在 class 级增加可选 `phases: [pre_spawn|post_spawn|any]`（默认 any），并加一个专用 class：

```yaml
classes:
  - name: spawn_hang_or_birth_failure
    pattern: '(?i)(spawn_failed|supervisor_launch_failed|supervisor_error|createprocess|agent thread limit|wait.*(not-a-cell|dummy)|no report_agent_job_result)'
    phases: [pre_spawn, any]    # 新增 phase-aware 匹配；默认表 class 用 [any]
    action: retry
    max_retries: 3              # = 现行 PRE_SPAWN_FAILURE_MAX；从表读出
    backoff_base: 5             # 新增 backoff，断路前 burst
    backoff_cap: 30
  # 现有 timeout class 保持；但 TIMEOUT_RETRY_MAX 不再单独常量，由 max_retries 决定
```

同步：

- `retry.py:111-124` `breaker()`：key 从“表内 class 一张表”扩展为“表 class + phase”；默认 all-phase 不变。
- `retry.py:136-140` 新 CLI `--phase`（必填，防护性默认 any），`terminal_packet_epilogue.route_terminal_retries` 按证据传 phase。
- `statemachine_v2.py:454-477` exec_failed(phase=pre_spawn) 守卫删除，让它跟其它 exec_failed 一同走 `FAILED`（t6）；`statemachine_v2.py:76` `PRE_SPAWN_FAILURE_MAX` 删除；`statemachine_v2.py:458-477` 彻底合并至 t10（FAILED→DUTY_REVIEW）处理。
- `terminal_packet_epilogue.py:47-65` `route_terminal_retries`：把 ledger state 为 FAILED/TIMED_OUT 的包全部纳入，不依赖 states 参数过滤（当前已正确），补一条 **再调用同一函数覆盖 `DISPATCHABLE+pre_spawn_failures>0` 的包**（如果短期不移除守卫）— 但推荐直接移除守卫。
- `orchestration_epilogue.py:73` step() 后调用同一 `route_terminal_retries(paths, states)`（move 到 statemachine_2.step() 返回后），并向 supervisor 传 phase=post_spawn；watchdog 分支传 phase=post_spawn（超时不是出生阶段）。

### 状态转移（目标终态，事件都走同一张表）

```
RUNNING ──timeout──► TIMED_OUT ──retry_dispatch──► DISPATCHABLE   （表 timeout.max_retries，与 t37 对账）
                                 └─budget_exhausted──► DEAD_LETTER（t14）
                                 └─duty_review     ──► DUTY_REVIEW（off-table / 2-consecutive / circuit）
FAILED  ──retry_dispatch──► DISPATCHABLE   （表 max_retries，同一个 retry.py 决策）
        ├─budget_exhausted──► DEAD_LETTER（t15）
        └─duty_review     ──► DUTY_REVIEW
DISPATCHABLE / RUNNING ──exec_failed(phase=pre_spawn)──► 表-driven 决策(见上)
                                ├─same class & budget left ──► FAILED ──► t9
                                └─off-table / budget out   ──► DUTY_REVIEW / DEAD_LETTER
```

「FAILED」成为唯一的中转枢纽：所有可重试失败先 FAILED（t6），由 retry.py 分类决策；TIMED_OUT 仅作为 t5 的专用入口，在入口后立即走表。状态机不再保留 PRE_SPAWN_FAILURE_MAX/TIMEOUT_RETRY_MAX 两常量。
---

## 测试矩阵（每项：构造 → 触达路径 → 断言；不依赖 LLM）

| # | 场景 | 构造 / 触达 | 期望 |
|---|---|---|---|
| T1 | pre_spawn 无限重试阻断 | 洋装 `dispatch_single` 抛 OSError×3 | a) 每发出生 retry_dispatch 1 条；b) `attempts` 递增为 1,2,3；c) 第 4 发 budget_exhausted → DEAD_LETTER/DUTY_REVIEW；d) breaker 非 class 不断；e) `attempts=0` 反例消失 |
| T2 | pre_spawn 重启分类生效 | 错误文本含 `spawn_failed`，phase=pre_spawn | 走 `spawn_hang_or_birth_failure` class；延迟 5–30s full-jitter；off-table中文错误文(e.g. `拒绝访问`)→ DUTY_REVIEW |
| T3 | TIMED_OUT 主循环可路由 | 设 RUNNING + spawn_times 过期 + roster 空缺 → orchestration_epilogue.step | a) TIMED_OUT 立即被路由 → retry_dispatch 恰 1 条；b) 进入 DISPATCHABLE；c) 再 timeout → budget_exhausted → DEAD_LETTER；d) retry_dispatch 重出击事件确认 (`has_retry_dispatch` 原子去重) |
| T4 | TIMED_OUT 双预算一致性 | TIMEOUT_RETRY_MAX 删除后 | 无 timeout_retry_counters.json 文件；timeout 另路导致 `FAILED` 也只可从表限 1 次；无“事件已出口但 t37 判 DLQ”反例 |
| T5 | 分类劫持修复回归 | TIMED_OUT stderr 尾部含 `401 unauthorized` | timeout 类优先（phase-aware：timeout 的 phase=post_spawn+limit_evidence）；不先命中 auth_failure；消解前值报告 §6 |
| T6 | breaker 不虚计 | 连续重复调用 retry.py 同一 FAILED 包×5（状态已走 retry_dispatch） | `.breaker.json` 只多 1 条 fail；出口仍为 `retry_already_scheduled`；10/60s 后才真开 |
| T7 | dispatch_refused → FAILED | DispatchBlocked(k3_unavailable) | a) append FAILED 不能绕过；b) 表 class 为 `k3_unavailable`→ duty_fixable；c) dispatch_refused 改为非 INFO |
| T8 | 总台账一致 | 单 run 中混入 T1×2 + T5×1 | `total_retries`（retry.py:146）跟 ledger attempts 总和相等；`timeout_retry_counters.json` 不存在 |
| T9 | 中共文错误（前值报告 §7） | stderr = `CreateProcessW failed (5): 拒绝访问。` | 当前行为固定为 DUTY_REVIEW；新表补中文 pattern 则在 spawn_hang_or_birth_failure→retry |
| T10 | 序列号 generation 校验 | 同一 attempt 重复 route_terminal_retries 并发调用×2 | 恰 1 条 retry_dispatch；1 个二进制 generation；refill._packet_attempt 返回后 +1 |
| T11 | circuit breaker | 10 包 FAILED in 60s；11th 包 FAILED | rc=6, action=circuit_open；非 FAILED/TIMED_OUT 包不受影响 |
| T12 | acceptance_fail | REPORTED 包 rc≠0 进 L0 判定 | （生产者另包） — 本报告范围之外；与 retry 表集成后 FAILED → 表分类 |
| T13 | 全链路 (golden G2 扩展 ) | deployed tree：dispatched→exec_failed(ETIMEDOUT)→retry_dispatch→dispatched→subagent_stop | G2 原断言 PASS；且补 attempts 递增、breaker 计数不跟 attempt 重复变动 |
| T14 | 账簿漂移报警 | ledger 写 attempts 与 retry.py 数据不同步 | fail-closed：StateMachine.apply_event 检测 event_attempt != ledger attempts+1 → DUTY_REVIEW，不静默 |

---

## 最小改动清单（像 diff 一样表述；禁行文件不被列）

1. `harness/retry.py`
   - CLI 增 `--phase {pre_spawn,post_spawn,any}`（默认 any，安全）；`_class_from(e, phase)` phase-aware。
   - `retry.py:111-124` `breaker()`：计数 key 从 (pid,) → (class_name or none, phase)；读表超出就动者入空段。
   - `retry.py:145-216`：事件 append 内联加上 `attempts` 递增（与状态机重构后保持一致记录）；`has_retry_dispatch` 在 `breaker` 记录 fail **之前** 查（防重复调用虚计）。
2. `config/retry_classes.yaml`：加 phase 字段；新增 `spawn_hang_or_birth_failure` class（见上）；`timeout.max_retries` 维持 2 并废除 TIMEOUT_RETRY_MAX；重申 `default_action: duty_review`。
3. `harness/statemachine_v2.py`
   - 删 `PRE_SPAWN_FAILURE_MAX`、`TIMEOUT_RETRY_MAX` 常量；删 :74/:76；删 :545-551 t37 独立 budget 判断；删 :573 reset `pre_spawn_failures`（本就不存在）。
   - :454-477 守卫删除；exec_failed(phase=pre_spawn) 走正常 FAILED（t6）。
   - `apply_event`；校验 event_attempt vs ledger attempts+1；验证失败 → DUTY_REVIEW。
4. `harness/terminal_packet_epilogue.py:47-65`：`route_terminal_retries` phase 参数按来路传入；对 ledger state 是 DISPATCHABLE + pre_spawn_failures>0 短期兼容路由。
5. `harness/orchestration_epilogue.py:73`：step 后调用 `route_terminal_retries`；watchdog/v2 supervisor 事件源都从这过。
6. `harness/lifecycle_supervisor.py:775-796`：pre_spawn 也调度 epilogue（boundary=None 的 terminal 尝试仍应调度到低带宽家庭式）。
7. `harness/dispatch_v2.py:470-477`（含 :454/:476 dispatch_refused）：DispatchBlocked 进入 FAILED (t6) 而 not-INFO；`sol_budget_blocked`（:504）保持负债但不影响 lifecycle。
8. `harness/lifecycle_supervisor_v2.py`：终态落账后统一调度 epilogue（与 v1 对齐）。

---

## 状态与责任边界（不改现况）

- 本报告**没有**修改任何 `harness/`、`config/`、`data/` 文件；仅创建/更新此 `reports/limited-retry-release-gate/report.md`。
- 改动清单（上节）严格只读列出，供后续 modif packet 复用。
- 防御机制不变：`PACKET_ID_RE`、`safe_pid_filename`、事件 append 原子锁、run-level budget、session_circuit_breaker 继续启用。

## 结论（机器可读）

所有三条缺陷实存，根因同一：**仅 terminal_packet_epilogue 一处调用 retry.py**。

- pre_spawn 无限 attempts=0：**结构缺**（状态机守卫绕过表）。
- TIMED_OUT 无法主路 retry + 双预算 + 账本漂移：**生产者缺 + 两并账**。
- FAILED 重复调用致 breaker 虚计 + dispatch_refused 非 FAILED：**顺序缺 + INFO 分类**。

推荐修复采取本报告§最小表驱动方案：

- 表增加 phase-aware class 并将 `spawn_hang_or_birth_failure` 设表化；
- 删除状态机里 `PRE_SPAWN_FAILURE_MAX` / `TIMEOUT_RETRY_MAX`；
- 将 `route_terminal_retries` 调到 orchestration_epilogue.step 后；
- 保守判优：dispatch_refused/DispatchBlocked 走 FAILED（t6）再分类，而不是静默继续 dispatch 等待。

