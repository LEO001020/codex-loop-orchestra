# 第二轮 B K3 复审证据包

范围：根 governor、meter、真实 demand refill、DispatcherV2、headless lifecycle。

不可变约束：48 总目标；V4 36 / K3 12；Desktop 8 仅承载偏好；空 ledger 零出生；普通执行显式 worker/V4；审计显式 K3；Desktop ipybox off、headless worker on、K3 default off。

本轮关键实现：

- `hooks/subagent_lifecycle.py` 只调用 `RefillControllerV2`，先 `queue_sync_ledger()` 再 `recompute()`；Stop/SessionStart/SubagentStop 复用 meter bridge 的锁与 60 秒 debounce。
- `harness/refill_consumer_v2.py` 是薄 actuator：跨进程 `.consumer.lock` 覆盖 sync→select→dispatch→observe；只消费有真实 packet 文件的 `DISPATCHABLE + explicit role`；拒绝 pool/role 冲突与 release_review；不生成 prompt。
- `harness/headless_wave.py` packet 模式再次校验 ledger 状态、实际 role/model、进程创建令牌；只在相同新 run_id 的 `running` 稳定 0.25 秒后计入。
- `DispatcherV2` 继续复用 v1 worktree/throttle/lifecycle；显式 role 对应语义 route；预算先 reserve 后 spawn；Popen 后的记账异常不再 reclaim 活子；重试 prompt 保留 previous_attempt；K3 只读 capture_report 保留。
- `BudgetController.register_agent` 幂等，已打开预算且额度耗尽时拒绝；lifecycle supervisor terminal finally 做 best-effort reclaim。
- router 是唯一 PreToolUse 入口；非 LOOP cwd 旁路；project-parent 映射 canonical F2 root；子 gate 固定注入同一 `LOOP_ROOT`；layered v2 目标是 `root_turn_governor.py`，cold_start rollback 保留。
- meter policy 增加 ordinary execution profile aliases（V4 Flash / GLM 5.2）以避免临时切换把在窗流量归为 unknown；已强制刷新，当前 5h Sol share 约 35.43%，meter status OK。
- sustained refill 在有真实 demand 时补到 target；low-water 只标记紧急度，不成为 36/48 的稳定上限。

测试证据：

- `python -m pytest tests/orchestration_v2 -q` → `423 passed`。
- 生命周期、hook、headless、8765、dispatch、meter 定向 → `102 passed, 1 skipped`。
- 第二轮细分定向曾连续得到 `140 passed`、`103 passed`。
- Windows 当前 Python 无 `mcp`，两个真实 ipybox MCP smoke 在收集阶段 `ModuleNotFoundError: mcp`；必须在后续 WSL/安装轮验证，不能算 PASS。
- 真实 headless V4 审计波：8/8 stable running，随后全部正常完成；8765 recent_tasks 可见 Windows CLI 语义任务名。
- 当前真实 LOOP ledger packets={}，refill v3：pending=0、deficit=0、target=48、preferred=36/12；正确行为是不填充。

请只读复核源码与上述证据，输出 `PASS` 或 `REDO`，最多 500 字。REDO 必须给出可复现的 P0/P1 边界和文件/行号；不得把后续第三/四轮项目当成本轮 B 失败。
