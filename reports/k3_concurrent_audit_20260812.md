# Codex LOOP 当前部署：并发 K3 审计与机械复验

日期：2026-08-12（Asia/Shanghai）

## 结论

当前 LOOP 的核心 v2 代码、Windows/WSL 同步、L2、plan pipeline、meter、provider health、worktree、lifecycle 和 layered gate 均已通过机械复验；但不能表述为“所有目标已经达成”。本轮确认并修复了两个真实生产接线缺口：8765 会把已恢复父 rollout 的历史 `task_complete` 误判为当前终态；真正工作区根 `E:\codex-LOOP\.codex\hooks.json` 仍指向旧 v1 Sol gate，而不是 layered router。

最近 5 小时 Sol effective share 仍约 50%–56%，高于 20%–25% 目标。代码治理链已经可用，但当前这条超长安装/审计根任务本身仍在污染窗口；新 hook 需要新任务或 Desktop 重启后才能保证加载。

## 16 路 K3 并发审计事实

- 启动 16 个显式 `verifier/reviewer` 子 agent，全部 `fork_context=false`。
- 16/16 rollout 的实际 `turn_context.model` 为 `weiwu-k3/kimi-k3`，抽样及角色 pin 的 effort 为 `max`；没有 inherited Sol。
- 修复 8765 后，页面实时显示 K3=16，并显示全部中文语义任务名。
- 16 个 agent 均执行了多轮只读 shell 检查，rollout 从约 65 KB 增长到约 87–198 KB。
- 等待约 12 分钟无终态；向全部 agent 发送立即收口中断后，新 turn 均进入 `task_started`，但 provider 未及时返回首个收口响应。
- 降到 4 路后继续等待仍无终态，最终全部显式关闭，previous_status 均为 `running`，终态为 `shutdown`。
- 因此本轮不能把任何子 agent 标记为 PASS；它验证了 K3 宽并发启动和实际执行，也暴露了宽审计及时收口不足。最终结论来自机械测试、运行时状态和本根证据复核。

## 本轮修复

### 1. 8765 Desktop rollout 可见性

旧 `rollout_terminal()` 对尾部做原始子串搜索：父 rollout 只要曾出现历史 `task_complete`，即使后面有更新的 `task_started`，仍会被判 terminal，所有当前孩子被隐藏。

现改为解析尾部 JSONL，并按最后一个 lifecycle event 判定：`task_started` 清除 terminal；`task_complete/turn_aborted/turn_failed/thread_closed` 设 terminal。回归测试覆盖：恢复父任务、真实完成子任务、恢复父任务下的 active K3 child 可见。

### 2. 全 K3 meter 测试合同

临时路由下 `models.v4_model == models.k3_model`。实际模型族 meter 必须把两者合并为 K3，不能制造不存在的 V4 token share；角色级 worker/verifier 区分继续由 role meter/refill 保留。测试现覆盖同模型与不同模型两种拓扑。

### 3. Windows 测试可移植性

POSIX bash/symlink 测试在 Windows 明确 skip；UTF-8 产物显式按 UTF-8 读取。没有修改生产代码来迎合错误的 Windows bash 路径。

### 4. 真正工作区根的 Sol gate

`E:\codex-LOOP\.codex\hooks.json` 的 PreToolUse 从旧 `hooks/sol_tool_gate.py` 改为 `hooks/sol_tool_gate_router.py`。router 可依据 `routing.mode` 选择 cold_start/shadow/layered，并在 layered 授权过期或 v2 gate 失败时 fail closed。

收口后 Windows 与 WSL 的 trusted root state 均已通过 governor CLI 切到 `execution`。直接实例化生产 `SolToolGateV2` 的合成 PreToolUse 验证结果：Windows `allow=false, rule=budget_high, share=0.5624`；WSL `allow=false, rule=budget_high, share=0.4999`。普通根 shell 工具现在应被拒绝并要求派发。

## 最终机械验证

- Windows unit：332 passed，21 个 POSIX-only skip。
- Windows orchestration_v2：407 passed。
- WSL unit：346 passed，4 skipped。
- WSL orchestration_v2：407 passed。
- 8765 regression：3 passed。
- Windows/WSL managed files：50，manifest SHA256 相同，0 mismatch。
- Windows layered gate：13/13 PASS。
- WSL layered gate：13/13 PASS。
- Windows meter：22,289 records，v1/v2 comparison PASS。
- WSL 合并 Windows+WSL meter：23,413 records，comparison PASS。
- K3 provider：真实 plan pipeline probe 72.5 秒成功，1 个 schema-valid packet，health fresh。
- OpenCodex：Proxy healthy，port 10100。
- ipybox：orphan_gateways=0，descendants=0。
- 8765 清理后：IDLE，running=0，estimated=0。

## 尚未达成/需观察

1. Sol 5h effective share 当前仍约 56.24%（Windows）/49.99%（合并口径），没有达到 20%–25%。
2. 16 路 K3 宽审计能执行但未在合理时间收口；复杂宽波应使用 bounded packet + lifecycle timeout，或降低同时进入生成阶段的 K3 数量，不能把“已启动”当“已完成”。
3. 当前已打开的 Desktop 根任务可能不会热加载新的 workspace hook；需要新开任务或重启 Desktop 后验证真实 PreToolUse 记录。
4. 当前已打开的长任务仍可能持有启动时加载的旧 hook 配置；新任务/重启后必须观察 `data/governor/gate_decisions.ndjsonl` 新增真实 PreToolUse `budget_high` 记录，才能确认 Desktop host 层已经加载新 router。

所以准确结论是：生产编排接线已达到可用并通过门禁；两个新发现的接线缺口已修；实际 Sol KPI 和 K3 宽审计收口仍需在新任务中观察，不能宣称已经完全达标。
