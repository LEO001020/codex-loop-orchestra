# 冷启动回滚契约审计（只读）

- 审计时间：2026-08-12 17:39 (Asia/Shanghai)
- 范围：cold_start/shadow/layered 状态转换、回滚键、LayeredGate.enable、routing_mode.py
- 约束遵守：未创建子 agent；未编辑任何文件；未运行任何测试；仅收敛 orchestration policy 与安全模式。
- 仓库：E:\codex-LOOP\codex-loop-s-f2（git 基线仅 f1d6eac "Fable F2 delivery baseline"；v1/v2 策略与 data/governor 均为未跟踪文件）

## 1. Verified findings

### F1 双权威交叉接线：v1 与 v2 各持一份 `[routing].mode`，当前值分裂

- `config/orchestration_policy.toml`（下称 v1）：`mode = "cold_start"`（:19）、`rollback_key = "routing.mode=cold_start"`（:27）。
- `config/orchestration_policy_v2.toml`（下称 v2）：`mode = "layered"`（:56）、`rollback_mode = "cold_start"`（:58）。
- v1 的消费方全部经由 `OrchestrationPolicy.load`（orchestration_common.py:331-333，默认路径 v1）：agent_router.py:353、dispatch_v2.py:309-311、trigger_eval_v2.py:386、root_turn_governor.py:291、budget_controller.py:131。
- v2 的消费方：sol_tool_gate_router.py:37、layered_gate.py:111、statemachine.py:380、trigger_eval.py:232、dispatch.py:129（仅 ipybox）、l2_consumer.py:152、metering/*（model_token_share_v2.py:608、usage_reconcile.py:43、bridge.py:123）、plan_pipeline.py:46、dual_plane_hash.py:45、routing_mode.py:77、sol_tool_gate_v2.py:137。
- 交叉接线结果：AgentRouter / dispatch_v2 / trigger_eval_v2 实际按 **cold_start** 运行（一切非 direct_l3/l4 升级 direct_l3→Sol）；PreToolUse 钩子（.codex/hooks.json:73-87，matcher `.*`，10s 超时，已安装）按 **layered** 运行并要求 ≤1h 授权。
- layered_gate.check_default_adapter 的断言 "stable v1 entries route to v2 by one mode key" 与实际不符：v1 稳定入口 statemachine.py / trigger_eval.py 读的是 v2 文件，而 v2 编排模块读的是 v1 文件——接线是交叉的，不是同键。

### F2 回滚键两套语义，所谓"单键回滚"实为双文件双键

- v1 侧（agent_router.py:250-253）：`rollback_key` 只做 truthy 检查，任何非空字符串都通过。
- v2 侧（layered_gate.py:219-229）：`rollback_mode` 必须精确等于 `"cold_start"`；`""` 与 `"shadow"` 均拒绝（test_layered_gate.py:155-169）。
- test_cold_start_rollback.py:77-84 把"两份策略都必须声明回滚键"固化为测试契约。
- conftest.set_routing_mode 默认只改 **v1**（conftest.py:86-96）；routing_mode.py CLI 只写 **v2**（routing_mode.py:77）。测试证明的"一键回滚"与 CLI 实现的"一键回滚"操作的是不同文件；两者各自都不能完成端到端单键回滚。幸运的是当前 v1 恰为 cold_start，AgentRouter 侧无意中处于安全态。

### F3 routing_mode.py 绕过门禁、无审计，且只写 v2

- `set` 不做任何 gate 检查、不写授权、不写审计（routing_mode.py:64-70、83-90）。v2:57 注释"flip to layered ONLY via layered_gate"在 CLI 上不成立：`python harness/routing_mode.py set layered` 会直接成功。
- 兜底是下游 fail-closed：agent_router 降级 shadow（agent_router.py:350-367），钩子层 deny（sol_tool_gate_router.py:126-128）。因此"set layered 成功"≠"layered 生效"。
- `rehearse` 只演练"恢复 cold_start 的字节"并绑定 restored_sha256（routing_mode.py:52-64）；任何后续策略字节变更（含 model_profile.py 应用 profile）都会使其失效。

### F4 LayeredGate.enable：名义六条件、实际 13 条件；授权过期且不绑定策略字节

- 文档与代码漂移：docstring 称六条件，check_all 实际跑 13 项（layered_gate.py:397-433；test_layered_gate.py:20-26 EXPECTED_CONDITIONS=13）。
- enable 只写授权、不写 mode（layered_gate.py:440-467）；授权文件（data/governor/layered_authorization.json）仅含 status/ts/conditions，**不记录 policy_sha256 或当前 mode**——1h 窗口内的授权可以事后授权一次 mode 翻转。
- 执行端新鲜度：sol_tool_gate_router.layered_authorized 要求 ≤3600s 且全部条件 ok（sol_tool_gate_router.py:76-88）。
- 当前授权 ts=1786471862（2026-08-12 02:11:03 +08），03:11 已过期；layered_gate.ndjsonl 之后唯一事件是 03:14:08 `layered_mode_refused`（provider_health 失败），无新授权 → 钩子层现处"layered 但未授权 → 所有 gated 工具 deny"。
- 六项（agent_router）与十三项（layered_gate）不是同一契约的两个视图，而是两套独立实现：agent_router 检查磁盘 marker（心跳文件、canary status==PASS 无时效、validators marker、ledger schema、rollback_key truthy）；layered_gate 检查 policy 键 + 实时 canary + transitions manifest 文件 + rollback_mode 精确相等。同一 guard 失败在路由层降级 shadow、在钩子层直接 deny。

### F5 三态转换与状态机契约

- cold_start：非 direct_l3/l4 一律升级 direct_l3→SOL_ADJUDICATE（agent_router.py:416-427；trigger_eval_v2.py:274-279）；回滚永不需门禁（test_cold_start_rollback.py:44-54 "burn the world" 可达）。
- shadow：按 cold_start 执行 + 写 data/router/shadow_log.ndjsonl（agent_router.py:429-435）；钩子层另写 data/governor/hook_shadow.ndjsonl（sol_tool_gate_router.py:52-73）→ 双影子语料。
- layered：门禁全过才生效；回滚后再前进需重跑全门禁（test_cold_start_rollback.py:117-127）。
- 状态机：statemachine.py 仅当 **v2** mode=="layered" 时委派 statemachine_v2（statemachine.py:378-391）；trigger_eval.py 在 shadow/layered 时委派 trigger_eval_v2（trigger_eval.py:230-241）。v2 清单要求 t27-t38 齐备、终态 = MERGED/DEAD_LETTER/DONE、SOL_ADJUDICATE 非终态（config/statemachine_v2_transitions.json；layered_gate.py:190-217 条件 5）。

### F6 当前部署姿态（实测，2026-08-12 17:39 +08）

- v1=cold_start → 路由层全量冷启动。
- v2=layered 但：授权过期（>1h）；rollback_rehearsal 哈希失配（演练 restored_sha256=16e5747a… vs 当前 v2 sha256=af9319bf…，与 dual_plane_hash.json policy_sha256=af9319bf… 一致）；meter age=56005s（>7200）；K3 health age=55839s（>3600）；heartbeat age=52003s（>300）→ LayeredGate.enable 现在至少 4 项必拒（consumer_heartbeat / meter_v2_fresh / provider_health / rollback_rehearsal）。
- 即当前既不是干净的 layered（未授权、门禁必拒），也不是干净的回滚（v2 仍 layered，钩子 deny 所有 gated 工具）。
- model_profile.py:110-128 应用 profile v4f（data/governor/model_profile.json ts=1786513392，13:43:13，updated_files=19）同时改写 v1/v2 两份 `[models]`——这是已证实会改写策略字节的写入者，也是演练失效的最可能来源；它不碰 `[routing].mode`。
- 时间线（+08）：00:18 演练 PASS → 02:11 授权 PASS（演练仍匹配）→ 03:14 门禁拒绝（provider_health）→ 13:43 profile v4f 应用 → 14:01 dual_plane_hash（新哈希）→ 现在：演练失效、授权过期。

### F7 双平面哈希盲区

- dual_plane_hash.py:45 只哈希 v2；v1 的 mode 漂移对它不可见。当前 v1=cold_start / v2=layered 正是该盲区的实例。

## 2. Exact evidence

| 证据 | 值 |
|---|---|
| v1 `[routing].mode` | `"cold_start"`（config/orchestration_policy.toml:19） |
| v1 `rollback_key` | `"routing.mode=cold_start"`（:27），agent_router.py:250-253 仅 truthy |
| v2 `[routing].mode` | `"layered"`（config/orchestration_policy_v2.toml:56） |
| v2 `rollback_mode` | `"cold_start"`（:58），layered_gate.py:219-229 精确相等 |
| v1 读方 | orchestration_common.py:331-333 → agent_router/dispatch_v2/trigger_eval_v2/root_turn_governor/budget_controller |
| v2 读方 | sol_tool_gate_router.py:37、layered_gate.py:111、statemachine.py:380、trigger_eval.py:232、l2_consumer、metering、plan_pipeline.py:46、dual_plane_hash.py:45、routing_mode.py:77、sol_tool_gate_v2.py:137 |
| CLI 翻转面 | routing_mode.py:77 仅 v2；测试翻转面 conftest.py:86-96 默认仅 v1 |
| LayeredGate 条件数 | check_all 13 项（layered_gate.py:397-433）；docstring 称 6；test_layered_gate.py:20-26 |
| 授权时效 | sol_tool_gate_router.py:76-88 ≤3600s；当前授权 02:11:03（ts 1786471862）已过期 |
| 演练绑定 | layered_gate.py check_rollback_rehearsal：restored_sha256 必须等于当前 v2 sha256 |
| 哈希失配 | 演练 16e5747a41c3bfb00e146a3f98eb4e6d9838995c49e43c095c216784aa8024eb vs 当前 af9319bf5df3519146904b1b21d67dd4f87f5d0584d17b2d43ab79a9c016f26d |
| 实时传感器 | meter age 56005s（>7200）、K3 health age 55839s（>3600）、heartbeat age 52003s（>300）、canary status=PASS ts 03:14 |
| 钩子安装 | .codex/hooks.json:73-87 PreToolUse sol_tool_gate_router.py，matcher `.*`，timeout 10 |
| 门禁失败语义 | 路由层降级 shadow（agent_router.py:350-367）；钩子层 deny（sol_tool_gate_router.py:126-128） |
| 回滚测试契约 | test_cold_start_rollback.py:44-54 免门禁、:77-84 双文件键、:117-127 重进需全门禁 |
| 状态机 | statemachine.py:378-391；statemachine_v2_transitions.json（t27-t38，终态 MERGED/DEAD_LETTER/DONE） |
| git 基线 | 仅 f1d6eac；v1/v2 策略、loop_config_v2.toml、data/governor 均为未跟踪 |
| 双平面盲区 | dual_plane_hash.py:45 仅哈希 v2 |

## 3. Minimal recommendation（不新增第三份配置）

目标态：全仓恰好**一个文件**携带 `[routing].mode`，所有消费方读同一文件。选 v2 为唯一权威（v2 已是 10+ 消费方的文件，且是 [gate_guard]/[l2_queue]/[validator] 的家；v1 消费方全部收敛在 OrchestrationPolicy.load 一个入口）。

1. orchestration_common.py:331-333 默认路径改 v2（保留 LOOP_ORCH_POLICY 覆盖语义）——一处改动接管全部 v1 消费方。
2. 把 v1 独有且仍被读的键迁入 v2：`[model_context]`（dispatch_v2 RolePin 依赖，dispatch_v2.py:193-198）；`l2_heartbeat_max_age_s` 与 `[l2_queue].consumer_heartbeat_max_age_s` 合一；`statemachine_schema_required` 与 `[gate_guard]` manifest 检查合一。
3. 回滚键唯一化：只保留 `rollback_mode="cold_start"`（精确相等），agent_router.py:250-253 改读同键同语义；删除 v1 `rollback_key`；test_cold_start_rollback.py:77-84 的"双文件"断言改为"单文件"断言。
4. 门禁实现合一：agent_router.effective_mode 复用 LayeredGate.check_all（或共享同一 guard 函数），消除 6 vs 13 双实现漂移；统一失败语义（建议与现有测试契约一致的"降级 shadow"，钩子层同步降级而非 deny；或反向统一为拒绝——需产品裁决，见 unresolved）。
5. routing_mode.py：`set layered` 必须先通过同一授权检查（复用 layered_authorized()：≤3600s 且全 ok），否则拒绝并指向 `layered_gate.py enable`；`set cold_start`/`set shadow` 永不过门禁；set 落审计行（event/mode/ts/policy_sha256，写入 layered_gate.ndjsonl）。
6. model_profile.py 只写唯一权威文件；应用 profile 后演练自动失效的机制保留，并在 set/enable 流程内强制重演 rollback rehearsal。
7. 删除或冻结 orchestration_policy.toml 与 loop_config_v2.toml（后者若保留仅文档用途、零读取者）；dual_plane_hash 只哈希唯一文件。

立即收敛操作（审计建议，未执行）：把 v2 mode 置回 cold_start 对齐 v1（消除"layered 未授权 deny"姿态），下次前进前依次重跑 `routing_mode.py rehearse` → `layered_gate.py enable` → 再 set layered。

## 必须保留的不变量

- I1 唯一模式权威：恰好一个文件含 `[routing].mode`，全消费方读同一文件，无第二翻转点。
- I2 回滚免门禁：cold_start 在任何传感器/授权失败下可达（burn-the-world 可达性，test_cold_start_rollback.py:44-54）。
- I3 前进需门禁：layered 仅在新鲜（≤3600s）全条件授权下生效；授权绑定唯一文件字节（policy_sha256）。
- I4 字节级演练绑定：rollback_rehearsal.restored_sha256 == 当前策略 sha256；任何字节变更（含 model_profile）使演练失效并阻断 enable。
- I5 单一回滚键语义：rollback_mode 必须精确等于 "cold_start"，禁止 truthy 别名。
- I6 状态机契约：layered 使用含 t27–t38 的 v2 清单且 SOL_ADJUDICATE 非终态；cold_start/shadow 保留 v1 表；回滚不丢弃 in-flight L2/ledger 状态（可继续 drain）。
- I7 门禁失败语义一致：同一 guard 失败在所有执行面产生同一行为并留 fail-visible 日志（当前路由层降级/钩子层 deny 不一致，必须收敛）。
- I8 模型针唯一来源：单一文件 `[models]`；profile 切换不得产生第二模型针来源。
- I9 哈希同源：dual_plane_hash / rollback_rehearsal / 授权均指向唯一文件，任一漂移即阻断 layered。

## 4. Unresolved

- 当前 v1=cold_start / v2=layered 的分裂是半回滚还是半前进，无法从只读证据确定（策略均未入 git 基线，无翻转历史）。
- 门禁失败的统一语义（降级 shadow vs 拒绝）需要产品裁决；本审计仅给出与现有测试契约一致的最小默认。
- 授权是否必须绑定 policy_sha256（enforcement 端校验）——本审计列为不变量 I3 的实现建议，但当前代码无此检查。
- `[model_context]` 迁入 v2 后 RolePin 的 context/compaction 引脚行为未经验证（禁止运行测试）。
- 双影子语料（data/router/shadow_log.ndjsonl 与 data/governor/hook_shadow.ndjsonl）是否合并，属于校准语料聚合问题，不影响模式语义。
- 03:14 门禁拒绝后无人再跑 enable：是有意停用 layered 还是遗漏，无法判定。
