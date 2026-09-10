# Codex LOOP F2 · ipybox、并发与上下文最终审计

日期：2026-08-10（Asia/Shanghai）

本报告取代同名文件此前的 24 路/禁用 ipybox 阶段性结论。当前权威状态是：

- LOOP 单任务常态目标 48 路，V4/K3 偏好 36/12，可互借。
- 1 秒出生间隔，最多 8 个 initializing，每 8 个出生做一次轻量 OpenCodex 健康检查。
- 所有 LOOP headless worker 启用 ipybox；MCP 懒启动，第一次真正执行 cell 时才启动 Jupyter。
- ipybox 整个运行时位于外层 SRT 沙箱；supervisor 管理独立进程组并清理孤儿。
- V4/K3 catalog 物理上下文 1,000,000，Codex 有效窗口 950,000，800,000 自动压缩。
- GPT/Sol 主 agent 未设置 context override，仍由现有 catalog 保持 372,000 物理/353,400 有效窗口。
- 全局监视器显示跨项目观察数与 LOOP 单任务目标两个独立指标，不再显示无意义的 `48 / 24`。

## 1. 崩溃归因

证据不能证明 ipybox 是唯一首因，但能确认它曾是严重的连锁故障放大器：

- OpenCodex 先退出并由现有 wrapper 重启；Codex Desktop 随后发生整进程级重启。
- WSL uptime 连续，无同期 WSL OOM、kernel panic、shutdown 或 reboot 证据。
- 修复前曾发现 62 个 gateway、61 个 kernel、59 个 PPID=1 孤儿 gateway 和 58 个孤儿后代 kernel。
- 因此更准确的结论是：传输/Desktop/OpenCodex 会话断裂使 worker 消失；旧 ipybox/Jupyter 生命周期管理未跟随回收，放大了 CPU、进程数和后续故障压力。

## 2. ipybox 最终架构

### 2.1 全 worker 可用，按需启动

`harness/dispatch.py` 对每个 headless worker 显式传入：

```text
-c mcp_servers.ipybox.enabled=true
```

不再使用全局禁用。`harness/ipybox_lazy.py` 的行为：

- MCP initialize 与 `tools/list` 不启动 Jupyter。
- 第一次 `execute_ipython_cell` 才创建 ToolServer、KernelGateway 和 IPython kernel。
- `asyncio.Lock` 防止同一会话首次并发调用重复启动。
- `AsyncExitStack` 统一关闭资源。
- 尚未启动 kernel 时调用 reset，直接返回干净状态。
- 临时目录固定到 workspace 内 `.ipybox-tmp`，避免沙箱内 `/tmp` 不可写。

实测曾观察到 33 个 lazy MCP 会话、18 个 gateway、18 个 kernel、孤儿 0；这说明“所有会话可用 ipybox”和“只有实际调用者付 Jupyter 成本”同时成立。

### 2.2 外层 SRT 沙箱

ipybox 0.9.2 自带的 gateway-only sandbox 会把 KernelGateway 放进网络命名空间，但 MCP 父进程留在外面，父进程无法连接沙箱里的 localhost gateway。因此最终拓扑是：

```text
supervisor（沙箱外，掌握进程组）
  └─ sandbox-runtime 0.0.71
       └─ ipybox_lazy MCP
            ├─ ToolServer
            ├─ KernelGateway
            └─ IPython kernel
```

自定义 `config/ipybox_sandbox.json`：

- 允许写 workspace data、IPython/Jupyter 状态目录。
- 外网 allowlist 为空。
- 拒读 `.env`、SSH、AWS、Azure、Codex、gcloud 凭证路径和 Docker socket。
- `allowAllUnixSockets=true`，因为 Jupyter ZeroMQ 需要 Unix socket，而 Linux SRT 不能按 socket 路径细分；敏感宿主 socket 与凭证路径另行显式拒绝。
- `allowPty=false`，`enableWeakerNestedSandbox=false`。

真实 MCP smoke 已验证：workspace 外写入被拒、外网连接被拒。

### 2.3 supervisor 与孤儿回收

`/home/codexloop/bin/ipybox-supervised`：

- 子运行时 `setsid()`，单独进程组。
- Linux parent-death signal。
- 监听 TERM/INT/HUP。
- 每 250 ms 检测父进程变成 PID 1。
- TERM 后最多等待 5 秒，必要时整组 SIGKILL。
- 启动前与退出后调用 fail-closed orphan reaper。

`harness/ipybox_cleanup.py` 的最终收敛策略：

- 只处理 `PPID == 1`、命令属于 `/home/codexloop/.venvs/codex-loop-f2/` 且含 `jupyter-kernelgateway` 的根。
- reaper 自己持有 `/tmp/codex-loop-ipybox-reaper-v2.lock`，所有调用方统一串行。
- 终止前复核 PID 与 `create_time`，降低 PID reuse 误杀风险。
- 最多三轮重新 discover/terminate，吸收后代 reparent 竞态。
- 子代先终止，5 秒后仍存活才 SIGKILL。

审计期间发生一次热部署兼容问题：存量旧 supervisor 先持有 v1 外层锁，再调用新 reaper 获取同一路径，形成自锁直到超时，留下 13 个孤儿 gateway。锁路径升级为 v2 后，旧 supervisor 持 v1、新 reaper 持 v2，不再互锁；一次 apply 清理了 26 个进程（13 gateway + 13 后代），随后稳定为孤儿 0。

8 个并发 reaper 实测：8/8 exit 0，总耗时约 0.46 秒，最终孤儿 0。

### 2.4 MCP 审批

Windows 与 WSL config 对 ipybox 使用 per-tool approval：

```toml
[mcp_servers.ipybox]
default_tools_approval_mode = "prompt"

[mcp_servers.ipybox.tools.execute_ipython_cell]
approval_mode = "approve"

[mcp_servers.ipybox.tools.reset]
approval_mode = "approve"
```

因此 headless 可非交互执行 cell/reset；`install_package` 与 `register_mcp_server` 仍需 prompt，没有把所有工具无条件放行。

## 3. ipybox 验证结果

### 3.1 正常路径

`tests/ipybox_supervisor_smoke.py`：

```text
IPYBOX_LAZY_OK tools=4 gateway_on_first_cell=1
```

覆盖：initialize、4 工具列表、列表阶段不启动 Jupyter、第一次 cell 只启动 1 个 gateway、真实 cell、沙箱写/网络边界、断开后恢复 baseline。

### 3.2 父客户端强杀

`tests/ipybox_parent_death_smoke.py` 强制 SIGKILL MCP client，结果：

```text
IPYBOX_PARENT_DEATH_OK orphan_delta=0 gateway_delta=0
```

证明 SRT/bwrap/Jupyter 后代没有逃离 supervisor 回收链。

### 3.3 真实模型工具调用

V4 rollout：

```text
mcp__ipybox.execute_ipython_cell
sum((i % 97) * (i % 89) for i in range(12345))
Ok: 26016864
tool duration: 2.202 s
```

任务总时长约 132 秒，ipybox 工具本身约 2.2 秒；慢点主要来自模型/OpenCodex 流程，不是 cell 执行。

### 3.4 非致命 warning

- Pydantic unresolved forward reference warning。
- IPKernel TCP without encryption warning。
- websocket ping timeout 被调整到 interval。

KernelGateway/IPython 通信位于隔离的 SRT 网络命名空间内，TCP warning 不代表对宿主或外网开放。

最终全套测试之后的第一次 smoke 曾出现一次空消息 `execute_ipython_cell isError=True`；当时 gateway 已启动，约 10 秒后工具返回错误，没有留下孤儿。清理后 INFO 重跑通过，随后再连续 2 次正常 smoke 和 1 次父死亡 smoke 全部通过，最终孤儿 0。该单次现象按瞬态记录，未伪装成首次即绿；若今后重复，应保留 gateway INFO 日志继续定位 websocket/kernel readiness。

## 4. 并发策略

`config/refill_policy.toml` 当前值：

```toml
[concurrency]
target_total = 48
v4_target = 36
k3_target = 12
v4_low_water = 27
k3_low_water = 9

[spawn_throttle]
spawn_interval_ms = 1000
max_initializing = 8
health_gate_every = 8
failure_backoff_seconds = 30
health_timeout_ms = 2000
```

语义：

- 48 是单个 LOOP 执行池常态目标，不是跨所有 Desktop 根任务的硬上限。
- 36/12 是偏好与低水位，不是硬分区；V4/K3 可互借空槽。
- initializing 8 只限制出生握手，不限制已经稳定运行的 worker。
- 每 8 个出生做一次 OpenCodex 轻量 health check；健康时没有固定额外暂停。
- 失败只暂停新出生 30 秒，不取消已运行任务、不清空需求。

历史上 24 路真实 wave 已完成 24/24、0 failed。用户随后实测跨任务全局 48 路时机器流畅，因此目标提升到 48。当前没有在 VPS 约 31 路活动时再叠加一个 48 路 LOOP wave，因为这会未经验证地把全局推到约 79 路；48 单池完整压力 wave 应在其他大任务下降后运行。

## 5. 全局并发监视器

URL：`http://127.0.0.1:8765/`

最终 UI：

- “全局观察到并发”：Windows Desktop、WSL/headless 和旧任务 rollout 估算的跨项目总数。
- “LOOP 单任务目标”：48，独立显示，不再组成 `观察数 / 目标` 分数。
- 项目分组，例如 `VPS 31`。
- V4/K3 色条按实际池占比绘制，不用全局观察数除以单池目标。
- 任务列表最多返回 64 条。
- rollout 中优先提取派发消息首行 `任务名：...`；用户侧不显示 Aristotle/Pauli 等随机 transport nickname。
- 没有明确任务名的旧任务显示“项目子任务 · ID短码”。

性能：

- rollout 状态扫描缓存 5 秒。
- 任务名每个 rollout 最多读取前 17 行/512 KiB，进程生命周期内永久缓存。
- tail terminal 检查最多读 128 KiB。
- 前端 1 秒刷新只请求本地 JSON，不调用模型。
- 20 次 API 实测平均约 40.9 ms，最大约 89.8 ms。

旧任务没有 lifecycle hook 事件，因此 rollout 只能标为 `ESTIMATED/open~`；这不是强运行真值。新用户级 hook 需要在 Codex `/hooks` 中完成一次精确哈希信任，不能手工伪造 trust hash。

## 6. 非 GPT 子 agent 1M 上下文

官方依据：

- https://learn.chatgpt.com/docs/config-file/config-reference
- https://learn.chatgpt.com/docs/agent-configuration/subagents

OpenAI 官方文档确认 `model_context_window`、`model_auto_compact_token_limit` 是有效配置键，自定义 agent TOML 是 spawned session 的独立 config layer。

最终配置：

- V4 `weiwu/deepseek-v4-flash`：物理 1,000,000；有效 950,000；800,000 自动压缩。
- K3 `weiwu-k3/kimi-k3`：物理 1,000,000；有效 950,000；800,000 自动压缩。
- worker、duty_officer、verifier、reviewer role TOML 均携带同样配置。
- headless dispatch 将 context/compact 与 model/effort 一样显式传给 `codex exec`。
- WSL `opencodex-catalog.json` 的 V4/K3 上限为 1M，且补入原先缺失的 K3 条目。后续 Fable 审计发现 Windows catalog 在 23:03 被 OpenCodex/Codex 同步重新写回 272k/244.8k；因此“双端已同步”不再成立，详见 `reports/fable-audit/FABLE_AUDIT_ENTRY.md`。
- GPT/Sol 主配置没有 context override；`gpt-5.6-sol` 保持 372,000 物理/353,400 有效、334,800 自动压缩。

真实新进程验证：

```text
V4 task_started.model_context_window = 950000
K3 task_started.model_context_window = 950000
```

Desktop 主进程会缓存启动时的 model catalog。当前 Desktop 内原生 worker 二次探针显示 258,400；Fable 审计进一步确认 Windows catalog 本身已漂移回 272k/244.8k，因此仅重启 Desktop 不足以恢复 950k。必须先修复 OpenCodex catalog 的持久生成源并验证 Windows/WSL 双端值，再重启并探针；WSL/headless 因 CLI 显式 override 仍按 1M/800k 生效。

## 7. 回归与运行证据

最终验证：

- WSL 全套：375 passed, 1 skipped。
- dispatch/context/refill 定向：30 passed；新增 context 定向后 15 passed。
- 正常 ipybox smoke：通过。
- 父死亡 fault injection：通过。
- 8 路并发 reaper：8/8 exit 0。
- OpenCodex：PID 23124，健康，backoff=false（审计快照）。
- monitor：目标 48，偏好 36/12，随机昵称 0。
- 最终 orphan dry-run：0。

Windows 本地全套曾得到 356 passed、1 skipped，其余失败来自 Windows Bash/symlink 权限环境；本轮记录称同一树在 WSL 环境得到 375 passed、1 skipped，但独立审计发现原始完整 stdout 未随本包保留，因此该数字标为“已报告、待独立重放”，不作为单独的发布证据。

## 8. 独立审计 agent

启动了 16 个只读审计角色。5 个提交了可用结论，11 个在收到“立即收口”中断后仍未返回，最终按 runtime 状态 shutdown 并全部 close，未继续占用并发。

吸收的高价值发现：

- supervisor/SRT 进程组逃逸风险：已用父客户端 SIGKILL fault injection 证伪当前链路的逃逸，gateway/kernel 回到 baseline。
- cleanup 并发与 PID reuse 风险：已将锁移入 reaper，加入 create-time 复核和多轮收敛。
- 用户级 hook 是纯生命周期记账、fail-open，无全局 Sol gate。
- 用户级 hook 的精确信任仍需用户通过 `/hooks` 完成；不手工写 trust hash。

没有采纳的过度结论：只审计仓库内 `harness/ipybox_cleanup.py` 的 agent 曾认为 cleanup 无调用方；实际调用方位于仓库外的 `E:\codex-LOOP\.bootstrap\ipybox-supervised`，已部署到 `/home/codexloop/bin/ipybox-supervised`。

## 9. 交付边界与后续动作

已完成：ipybox 全 worker 可用、懒启动、沙箱、审批、父死亡回收、孤儿回收、全局 UI、真实任务名、48 路目标、V4/K3 1M catalog 与 800k compaction。

仍需用户侧一次动作：

1. 先修复 OpenCodex 对 Windows catalog 的持久重写源，确认 V4/K3 为 1M/800k；再重启 Codex Desktop，并用新原生子 agent 验证 effective context 为 950k。
2. 在 `/hooks` 中审核并信任新的用户级 lifecycle hook 精确哈希。

建议但不阻塞当前交付：当 VPS 大并发下降后，运行一个独立 48/48 LOOP wave，记录 `48 completed / 0 failed / orphan 0 / OpenCodex PID unchanged`，作为单池 48 路的最终容量证据。
