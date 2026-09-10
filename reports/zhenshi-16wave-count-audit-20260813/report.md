# 真实16波计数复核（L2 只读验证）

复核时间：2026-08-13 02:46–03:03（Asia/Shanghai）
复核方式：只读。查询 `http://127.0.0.1:8765/api/status`（2 次快照），并在本机只读解析 `C:\Users\hzq00\.codex\sessions\2026\08\13\rollout-*.jsonl` 的 `session_meta` / `turn_context` / 生命周期尾部事件；未修改任何文件、未触碰监控服务、未派生子 agent、未访问 VPS。

## 结论（1 行）

截至最后快照 03:09:09，8765 当前显示 **0 个**运行中的 weiwu-k3/kimi-k3 max 子 agent（freshness=**STALE**，非 ESTIMATED）；此前 02:46:48 快照显示的 16 个为 rollout-fallback **ESTIMATED**（非权威罗斯特计数），全部 16 个 rollout 自 02:45–02:46 起持续停更（≥17 分钟），本周波次的真实实时计数无法确认存活。

## 快照对照

| 项 | 快照 1（02:46:48） | 快照 2（03:09:09） |
|---|---|---|
| freshness | ESTIMATED | STALE |
| counts.running | 16 | 0 |
| counts.estimated | 16（= 全部为估算） | 0 |
| counts.recent_activity | 16（存疑，见下） | 0 |
| pools.k3 / v4 | 16 / 0 | 0 / 0 |
| planes.desktop / headless | 16 / 0 | 0 / 0 |
| liveness.desktop_rollout_evidence | 16 | 0 |
| stale_native / stale_headless | 154 / 1 | 154 / 1 |
| target / deficit | 60 / 44 | 60 / 60 |

注：快照 2 突变根因——app-server 于 03:02:23 重启（进程 epoch=1786561643），服务端按 `mtime < desktop_started_at` 栅栏将重启前全部 rollout 判为 crash 证据剔除（[loop_monitor_server.py 扫描栅栏](E:/codex-LOOP/launchers/loop_monitor_server.py:399)）。

## 逐项核对：16 个 rollout 的 turn_context

对父会话 `019feacd-184e-7472-9f11-ea364c22e595` 在 02:45:38–02:46:14 出生的全部 16 个 rollout 文件逐项读取 `turn_context`（每个文件第 5 行）：

- **model：16/16 = `weiwu-k3/kimi-k3`**（`session_meta.model` 为空，实际模型取自 turn_context，与服务端 `read_rollout_model` 同法）
- **effort：16/16 = `max`**
- **agent_role：16/16 = `verifier`**（与 AGENTS 路由要求一致）
- **任务名与 /api/tasks 16 行一一对应**：60策略动态边界终审、8765实模实时复核、8765新页面终审、Epilogue代际标记终审、Epilogue锁实现复核、Provider出生门终审、Roster编码完整性终审、V1V2游标共存终审、发布门测试覆盖复核、安装回滚兼容终审、并发不降级架构终审、持续补位回归终审、显式角色绕行终审、校验和最终清单复核、正常终态漏调度复核、真实16波计数复核（本任务）。

## stale 与 ESTIMATED 区分

- 16/16 均为 **ESTIMATED**：它们全部来自 rollout-fallback 分支（计数+estimated），没有任何一个进入权威 native/exec roster（否则 estimated<16）。
- **存活可疑（stale 语义）**：15/16 个 rollout 自出生起 **0 次写入**（mtime == 创建时间 02:45:38–02:46:14，其中 6 个尾部已有 task_complete）；唯一活跃的为 02:46:14 出生的「真实16波计数复核」（本 agent 自身），其 action log 无写入权限、符合只读约束。文件级远端非终端但 mtime 停更 ≥17 分钟。
- 快照 1 的 `recent_activity=16` 与全部文件 mtime 停更 10+ 分钟相矛盾；按服务端 `recent_activity = now - mtime <= 120s` 的定义本应 ≈1。此为仪表盘活性信号与底层文件证据的不一致。
- headless 平面：0 运行，且 headless_age_seconds ≈ 22317s（~6.2h），1 个 stale_headless 登记残留。
- 首脑 stale_native=154：为历史 Desktop 事件驱动登记残留（无数当前 epoch rollout 证据），不计入有效并发。

## 判定

前后两个快照均不满足「当前运行 16 个实际 weiwu-k3/kimi-k3 max 子 agent」的强表述：02:46 时是 16 个 ESTIMATED 估算（模型/effort 属实但存活未证实），03:09 时监控归零（STALE）。目标 60，当前缺口 60。
