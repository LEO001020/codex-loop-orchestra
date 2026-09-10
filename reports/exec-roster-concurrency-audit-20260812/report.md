# exec_roster schema v2 消费者审计与并发真值修复（只读审计报告）

日期：2026-08-12 · 仓库：E:\codex-LOOP\codex-loop-s-f2 · 模式：只读审计（未做任何编辑）

## 1. 结论（TL;DR）

根因不是多写者竞争写锁，而是 **Windows 上 `os.replace` 与"未加锁、未开 FILE_SHARE_DELETE 的读者"冲突**：
写者被 `.exec_roster.lock`（msvcrt 字节锁）正确串行化，但所有读者（8765 监控 1Hz、headless_wave 轮询 20Hz、
refill/watchdog/gate 等）用普通 `open()` 读 roster；CPython 默认共享模式不含 FILE_SHARE_DELETE，
任一读者打开窗口与写者 `os.replace` 瞬间重叠即抛 `PermissionError: [WinError 5]`（已双进程复现）。
supervisor 的 `except BaseException → state=spawn_failed` + `finally: boundary.close()` 把一次瞬时写失败
放大为"运行中 worker 被标记 spawn_failed 并被 Job 关闭杀掉"。

审计期间（18:14:18/18:14:39）仓库内已出现与本结论一致的**最小修复**（`harness/lifecycle_supervisor.py` 的
`atomic_json` 增加 5s 有界重试 + tmp 清理、`locked` 改 LK_NBLCK 退避），配套单测 16/16 通过；
修复后 18:15 起的新一轮 8 路 wave（`adhoc-1bea1cc97a-roster-stability-*`）8/8 running、0 新增 PermissionError。
以下各节给出可复核证据。

## 2. Verified evidence（可复核事实）

### 2.1 生产故障现场（pre-fix，2026-08-12 17:37–17:44）

- `data/events.ndjson`：同一 8 路 wave 内 `exec_spawned=8`、`exec_failed=7`，`why` 全部为
  `supervisor_error`，错误串为 `PermissionError: [WinError 5] 拒绝访问。: '<pid>.tmp' -> 'exec_roster.json'`
  （事件行 50/51/54/67/87/88/90 附近）。
- `data/lifecycle/exec_roster.json`：7 个 job 的 history 均为
  `generation_started → starting → running → spawn_failed`，且均带 `os_pid` 与 `worker_proc_start_ticks`；
  `heartbeat_at → spawn_failed` 间隔约 1.02s（如 `policy-env-override`：heartbeat 1786527806.55、
  spawn_failed 1786527807.58），证明失败发生在 **1s 心跳写入**而非出生阶段；任务实际运行了 57–373s。
- 残留 tmp 文件：`exec_roster.json.15248.tmp / .22616.tmp / .38772.tmp`（17:40/04:59）、
  `native_roster.json.49424.tmp`（17:43）——旧 `atomic_json` 失败后不清理 tmp 的直接物证。

### 2.2 机制复现（本机双进程实验）

进程 A 用普通 `open()` 打开 `exec_roster.json` 并保持句柄；进程 B 执行与 `atomic_json` 相同的
`os.replace(tmp, dst)`。结果精确复现：

```
reader: reader opened, bytes=37
writer: replace FAILED: PermissionError: [WinError 5] 拒绝访问。: '...exec_roster.json.999.tmp' -> '...exec_roster.json'
```

即：目标文件被无 FILE_SHARE_DELETE 的并发句柄持有时，Windows `MoveFileExW(REPLACE_EXISTING)` 返回
ERROR_ACCESS_DENIED。CPython `open()`/`read_text`/`json.load(open(...))` 默认共享模式
（FILE_SHARE_READ|FILE_SHARE_WRITE，不含 FILE_SHARE_DELETE），本仓库全部读者均为该形态。

### 2.3 写者/读者矩阵（当前代码，均已核对行号）

写者（全部经 `.exec_roster.lock`，同一 msvcrt/fcntl 协议）：

| 写者 | 位置 | 说明 |
|---|---|---|
| supervisor `Store.update` | harness/lifecycle_supervisor.py:130-175 | 出生/心跳/终态；锁内读改写 |
| hooks `cold_reconcile_exec` | hooks/subagent_lifecycle.py:604-634 | 锁内读，`atomic_json` 写 |
| headless_wave 恢复路径 | harness/headless_wave.py:105-125 → Store.update | 锁内 |

读者（**无锁**，普通 open，会阻塞 os.replace）：

| 读者 | 位置 | 频率/触发 |
|---|---|---|
| headless_wave `roster_item` | harness/headless_wave.py:86-92 | wait_observed 轮询 20Hz（每 50ms） |
| 8765 monitor `snapshot` | launchers/loop_monitor_server.py:410-420（仓库外 E:\codex-LOOP\launchers） | 页面 setInterval 1s（:647），同时读 WSL+Windows 两份 roster |
| refill_controller v1/v2 | harness/refill_controller.py:250-259、refill_controller_v2.py:192-201 | recompute 周期 |
| statemachine v1/v2 watchdog | harness/statemachine.py:296-319、statemachine_v2.py:516-543 | 周期 watchdog |
| layered_gate | harness/layered_gate.py:348-357 | 门禁检查 |
| dispatch | harness/dispatch.py:182-189 | observed_birth_counts |

锁只串行化写者；任何读者打开窗口与 replace 重叠即失败。8 写者 × 1s 心跳 ≈ 8 次写/s，
叠加 monitor 1Hz + launcher 20Hz（wave 头 ~60s）+ watchdog/refill 周期读，碰撞概率与观察到的
7/8 失败率吻合。

## 3. Root cause（根因链）

1. 写者串行正确（`.exec_roster.lock`，msvcrt LK_LOCK/LK_NBLCK），排除"多写者丢更新"作为主因。
2. Windows 原子替换语义：`os.replace(tmp, roster)` 要求对目标持有 DELETE 访问权；任何未开
   FILE_SHARE_DELETE 的并发句柄（本仓库所有读者 + 监控进程）→ WinError 5。
3. 旧 `atomic_json`（18:14 前）对 PermissionError 无重试、失败不清理 tmp。
4. supervisor `run()` 的 `except BaseException`（lifecycle_supervisor.py:612-617）把任意异常
   （含心跳期 roster 写失败）统一写 `state=spawn_failed`；`finally: boundary.close()`（:618-620）
   关闭 Job → 杀掉仍在运行的 worker。瞬时 IO 抖动被放大为"健康任务被误杀 + 假 spawn_failed 真值"。
5. 8765/refill 等按 `state` 计数：被误标的 job 从 running 掉出，并发真值同时失真（少记 7 个 running）。

## 4. schema v2 消费者兼容约束（新增字段 / retry marker）

现状：exec_roster 无 JSON Schema 文件（schemas/ 仅 decision_skeleton/plan_expander/short_result），
所有消费者均为 `dict.get` 容错读取，测试无整 dict 相等断言 → **新增顶层/作业级字段是加性安全**。
当前 job 字段全集（数据实证）：packet_id, run_id, attempt, state, task_name, role, model, plane, cwd,
supervisor_pid, supervisor_proc_start_ticks, parent_session_id, started_at, deadline_at, command,
stdout_path, stderr_path, os_pid, worker_proc_start_ticks, heartbeat_at, updated_at, history, failure,
（completed 时）published_report，（恢复时）recovered_by。

retry marker / 新字段的硬约束：

1. **不要引入新 state 字符串**：`headless_wave.observed/wait_observed` 只认 `state=="running"`，
   终态集合固定（headless_wave.py:136-171）；未知状态会让 handoff 一直轮询到超时 → 返回
   `unobserved`，出生债务不消除。8765 monitor 只识别 starting/running/completed/failed/
   timed_out/cancelled/spawn_failed（loop_monitor_server.py:486-506），新状态在计数中静默消失。
2. **必须走 Store.update 的 generation guard**（lifecycle_supervisor.py:139-165）：run_id 变更仅允许
   `state=="starting"` 且 `started_at >= 旧值`；retry marker 不能绕过该守卫（有测试
   `test_late_old_generation_cannot_clobber_newer_run` 锁定）。
3. **attempt 必须保持 int 可转**：`recover_live_supervisor` 与 `cold_reconcile_exec` 均
   `int(row.get("attempt", 0) or 0)`。
4. **heartbeat_at 仍是唯一活跃信号**：statemachine v1/v2 watchdog 用 5s 新鲜度判定 supervisor 归属
   （statemachine.py:34,312-318；statemachine_v2.py:72,537-543），retry marker 不能替代心跳，
   重试代必须刷新 heartbeat_at/updated_at，否则被 watchdog 判死。
5. **refill 控制器按行计 running**：旧代残留 `running` 行会重复占位（refill_controller_v2.py:192-201），
   重试必须终态化（或移除）被取代的一代。
6. **跨平面合并键不含 attempt**：monitor `merge_exec_rosters`（loop_monitor_server.py:170-200）按
   `max(updated_at, heartbeat_at, started_at)` 选代、平手时后者（Windows）胜出。若未来 WSL/Windows
   对同一 packet_id 各自重试，时钟偏差/时间戳回退可能选错代——需要协调修改仓库外监控（unresolved）。
7. **hooks cold_reconcile_exec**（subagent_lifecycle.py:604-634）依赖 state∈{starting,running} +
   supervisor_pid/ticks 判定 lost；新增字段无影响，但若改 state 语义需同步。
8. layered_gate（:348-357）与 dispatch（:182-189）只读 jobs map/state，加性安全。

## 5. Minimal fix（当前盘上已应用，18:14:18 + 18:14:39）

仅改生命周期 roster 并发真值，未动并发目标/policy/ipybox/UI（近 3h 变更清单中与本次相关的只有
`harness/lifecycle_supervisor.py` 与其单测）。

1. `atomic_json(path, value, *, replace_timeout_s=5.0)`（lifecycle_supervisor.py:83-120）：
   - `os.replace` 对 PermissionError 有界重试，退避 5ms→100ms，5s 上限；
   - 超时抛 `LifecycleError("atomic roster replace remained blocked ...")`；
   - `finally` 清理本进程 tmp（成功/失败都不留 `.tmp`）。
2. `locked(path, *, timeout_s=10.0)`（lifecycle_supervisor.py:44-80）：Windows 端由阻塞式
   `LK_LOCK`（10×1s 后直接抛 OSError）改为 `LK_NBLCK` + 指数退避 + 10s 上限的显式
   `LifecycleError("lifecycle lock remained busy ...")`，锁获取失败可区分、可见。
3. 语义保持：不丢锁、不原地改写目标、读改写仍在锁内；`Store.update` 的 generation guard、
   history、cancel 语义未变。

该实现与 `tests/unit/test_lifecycle_supervisor.py` 中新增用例完全对应（18:14:39 更新）。

## 6. Tests（已在本机复核）

`python -m pytest tests/unit/test_lifecycle_supervisor.py -q` → **16 passed in 6.70s**。
关键用例：
- `test_atomic_json_retries_transient_windows_permission_error`：前 3 次 replace 抛 WinError 5，
  第 4 次成功；断言无残留 tmp。
- `test_atomic_json_fails_visible_after_bounded_replace_timeout`：持续阻塞 → LifecycleError，
  目标不存在、无残留 tmp。
- `test_store_multiprocess_updates_preserve_all_jobs`：16 进程 × 20 次 update 全成功，
  16 个 job 全在、state=running、无残留 tmp。
- `test_late_old_generation_cannot_clobber_newer_run` / `test_retry_generation_emits_terminal_event_for_each_attempt`：
  run_id/attempt 代际语义锁定（retry marker 兼容基线）。

## 7. Unresolved（残留风险，按重要性排序）

1. **hooks 写者未同步修复**：`hooks/subagent_lifecycle.py` 自有 `lock()`（:74）与 `atomic_json`（:99-105）
   仍是旧实现（阻塞 LK_LOCK + 无重试）；`cold_reconcile_exec` 写 exec_roster.json 及 NativeRoster
   写 native_roster.json 仍可能命中同一 WinError 5（`native_roster.json.49424.tmp` 即其现场）。
   建议把重试/退避提升为 `orchestration_common.py` 共享实现（:279-288 也仍是旧形态）。
2. **supervisor 异常分类仍一刀切**：`run()` 的 `except BaseException`（lifecycle_supervisor.py:612-617）
   对"出生前失败"与"运行期 roster 写失败"都记 `spawn_failed` 且 `finally` 杀 worker。
   修复后触发概率大幅下降（需 replace 阻塞 >5s 或锁忙 >10s），但语义上仍建议：
   运行期写失败（已成功 spawn、state==running）应保留 running、记录 failure 明细并重试，
   而非杀进程翻 spawn_failed。
3. **读者侧未改共享模式**：8765 monitor（仓库外）与 headless_wave 等仍以无 FILE_SHARE_DELETE
   方式读；写者重试吸收了毫秒级窗口，但 monitor 多标签页/更高轮询频率会拉长写者重试延迟。
4. **残留旧 tmp**：`exec_roster.json.15248/22616/38772.tmp`、`native_roster.json.49424.tmp`
   为修复前遗留，新代码只清理本进程 tmp；可加启动期 best-effort 清扫。
5. **跨平面锁不互通**：Windows msvcrt 字节锁与 WSL fcntl.flock 无互斥语义。当前 WSL/Windows
   各写各的 roster（monitor 分别读 `\\wsl.localhost\...` 与 `E:\...`）无共享文件；若未来两平面
   指向同一物理文件（如 WSL 经 /mnt/e 跑同仓库），并发写保护失效——需在架构层排除。
6. **monitor 合并键**（见 §4.6）：引入 retry marker 前需同步改
   `E:\codex-LOOP\launchers\loop_monitor_server.py::merge_exec_rosters` 的代际选择逻辑。

## 8. 方法学备注

- 审计开始时（本会话首次读取）`lifecycle_supervisor.py` 为旧实现（无重试）；18:14:18/18:14:39
  该文件与 `tests/unit/test_lifecycle_supervisor.py` 被并发更新为修复版。本报告全部结论基于
  当前盘上状态复核（文件 mtime 与 SHA256 已记录），修复非本审计所写。
- 生产数据（events.ndjson / exec_roster.json / lifecycle 目录）在 18:15-18:17 有新 wave 写入；
  修复后 8/8 running、0 新增 PermissionError，与单测结论一致。
