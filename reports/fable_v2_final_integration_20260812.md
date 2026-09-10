# Fable orchestration-v2 最终生产集成报告

日期：2026-08-12（Asia/Shanghai）

## 结论

> 2026-08-12 后续审计更正：核心生产接线与 layered 门禁已完成，但实际 5h Sol effective share 仍约 50%–56%，尚未达到 20%–25%。同时修复了 8765 恢复父 rollout 误判和真正工作区根仍指向旧 v1 Sol gate 两项接线缺口。详见 `reports/k3_concurrent_audit_20260812.md`。

Fable v2 的生产功能接线已经完成，Windows 控制面与 WSL/headless 执行面均处于 `layered` 模式，最终 13 项机械门禁全部通过。旧 F2 的 worktree、出生节流、lifecycle、roster、报告发布和 ipybox 治理仍是物理执行层；v2 负责路由、预算、L2、计划扩展、短结果和状态机决策，没有建立第二套无治理的执行通道。

今晚的临时模型路由为：根 agent 继续 `gpt-5.6-sol`；所有子 agent 角色实际模型统一为 `weiwu-k3/kimi-k3`、`max`、1,000,000 context、800,000 auto-compact。执行角色与 verifier/reviewer 角色仍保留 36/12 的语义调度配额，8765 则按实际模型显示 K3 总目标 48。

## 已完成的 Fable 生产接线

1. 三模式路由已接入稳定入口：`cold_start`、`shadow`、`layered`；当前为 `layered`，单键回滚目标为 `cold_start`。
2. `send_l2` 已接真实 exactly-once consumer、claim/heartbeat/stale reaper、K3 lifecycle 和严格 ShortResult validator；真实 L2 verifier 返回 `pass`，重复 drain 为零新增派发。
3. v2 dispatcher 已复用旧 F2 的物理执行原语，不再直接绕过出生间隔、健康门、lifecycle、roster 和报告发布。
4. K3 plan-expander 已配置化，并接 lifecycle、provider health/backoff、schema 校验、DAG materialization 和 bounded Sol adjudication。
5. token meter 已接真实 rollout 单扫描桥，同时生成 v1/v2、ledger 和 shadow comparison；maintenance 不进入生产分母，Windows/WSL 多源 sessions 合并，comparison 为 PASS。
6. Sol hook router 已实现三模式行为；layered 要求新鲜授权，手工改 mode 无授权时 fail closed。
7. orchestration epilogue 已在状态机与 reconcile 边界刷新 L2、consumer heartbeat 和 meter；错误进入 FAIL_VISIBLE。
8. ipybox 由角色和执行面控制：Desktop native 关闭；WSL ordinary worker 开启；K3 verifier/reviewer/plan-expander 默认关闭，代码执行例外显式开启；孤儿清理 dry-run 为 0。
9. Windows/WSL 双平面受管清单已扩至 50 个关键文件，包含 refill 单一权威、refill controller、worktree 物理执行脚本和本轮关键回归测试；最终 manifest SHA256 一致，mismatch 为 0。
10. WSL 普通 worker worktree 接线已修复：若 `$LOOP_ROOT` 本身是 Git 仓库则直接作为 `LOOP_REPO`，否则才回退 `$LOOP_ROOT/repo`；隔离要求未放宽。
11. 8765 显示真实运行模型、执行面、语义任务名和模型池；临时全 K3 路由下目标显示为 K3 48，而不是旧 GLM 36/K3 12 的模型口径。

## 真实运行证据

- K3 ordinary worker：`k3-worker-probe-20260812-001`，`role=worker`，实际模型 `weiwu-k3/kimi-k3`，`model_reasoning_effort=max`，ipybox enabled，21.1 秒完成，返回 `K3_WORKER_OK`。
- K3 L2 verifier：`l2v-7a54bedb198f3fb140fa9b60`，30.7 秒完成，verdict `pass`，validator `OK`。
- K3 plan-expander：`v2job-plan-faa12d5206f363b561a9`，91.1 秒完成，产出 schema-valid packet。
- 8765 在 worker 探针运行时显示：任务名 `K3执行池真实探针`、角色 `worker`、池 `k3`、执行面 `WSL CLI`、状态 `running`；完成后 active 自动归零。
- OpenCodex：`Proxy healthy`，端口 10100；8765 health 与 `/api/status` 均可用。
- ipybox orphan audit：`orphan_gateways=0`、`descendants=0`。

## 最终验证

- Windows layered gate：13/13 PASS。
- WSL layered gate：13/13 PASS。
- 双平面哈希：42 个受管文件，Windows/WSL manifest SHA256 相同，0 mismatch。
- 回滚演练：策略文件切到 cold_start 后按精确字节恢复，恢复前后 SHA256 相同。
- v2 全套基线：Windows 407 passed；WSL 407 passed。
- 当前路由变更后的模型/lifecycle 定向测试：Windows 26 passed；WSL 23 passed。
- smoke gate：Windows 4 passed、3 个 POSIX-only skip；WSL 7 passed。
- refill controller：Windows 20 passed；WSL 20 passed。
- worktree 生产入口：WSL `worktree_pool.sh status` 成功识别 `/home/codexloop/codex-loop-s-f2` 的 `main` 工作树。

## Fable 原残余项处理结果

- R4（v2 直接 Popen 绕过旧 admission）：已修复，v2 物理派发复用旧 dispatch/lifecycle。
- R5（ledger append O(n²)）：真实 meter bridge 已改为单扫描、批量追加和幂等去重。
- R8（缺 plan pipeline）：已实现并完成真实 K3 provider probe。
- 生产 layered 不可达：已修复并通过 13 项门禁正式启用。
- L2 producer/consumer completion 缺口：已完成真实模型 E2E。
- 普通 worker 的 `$LOOP_ROOT/repo` 假设：已修复为当前 Git 根自动识别。

## 仍保留的非阻塞维护债

以下项目不影响当前生产链运行，但没有伪装成“已经删除”：

1. `orchestration_policy.toml` 与 `orchestration_policy_v2.toml` 仍是两种 schema 的策略声明面；当前值同步并纳入双平面哈希，但后续可统一 loader/schema。
2. `loop_config_v2.toml` 仍作为并发文档镜像存在；唯一执行权威是 `refill_policy.toml`。两者已一起纳入 42 文件哈希。
3. root governor 与 v2 tool gate 仍有部分重叠实现；当前实际 hook 经 router 选择，合同测试与 layered 授权防止漂移。
4. gate guard 的 policy skip 仍保留为显式运维能力；默认 13 项全部 required，当前没有 skip。
5. catalog generator 不在本集成包内重建；当前 Windows/WSL runtime 依靠显式模型、context 和 compaction CLI/TOML pin，已真实调用验证。

这些是维护性债务，不是 Fable v2 当前生产功能的缺失，也没有阻塞 layered。

## 回滚与备份

- 编排模式回滚：`python harness/routing_mode.py set cold_start`
- WSL 本轮变更前备份：`/home/codexloop/codex-loop-s-f2/backup-20260812-k3-switch`
- 早期集成备份：
  - `/home/codexloop/codex-loop-s-f2/backup-integration-20260811-233347`
  - `/home/codexloop/codex-loop-s-f2/backup-incremental-20260811-234655`
  - `/home/codexloop/codex-loop-s-f2/backup-managed-20260811-235305`
- WSL 用户配置备份：`/home/codexloop/.codex/backup-pins-20260811-233710`

当前不要求重启 Codex Desktop 才能使用 headless LOOP；只有 Desktop app-server 自身缓存的模型目录显示需要刷新时，才需要之后重启 Desktop。当前对话可继续使用。
