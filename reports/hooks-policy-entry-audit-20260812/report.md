# Hooks策略入口审计报告（只读）

任务名：Hooks策略入口
时间：2026-08-12 17:43 CST（Asia/Shanghai）
范围：`hooks/sol_tool_gate_router.py`、`hooks/sol_tool_gate_v2.py`、`hooks/sol_tool_gate.py`、宿主/项目 hooks 配置、`config/orchestration_policy*.toml` 及其消费者。
约束：全程只读；未修改任何被审文件。未创建子 agent。

## 1. Verified findings

### 1.1 两个 gate 的 policy 路径依赖（hooks 侧已收敛到 v2）
- `sol_tool_gate_router.py:31` `find_root` 用 `config/orchestration_policy_v2.toml` 存在性作根标记；`:37` `read_mode` 读同一文件的 `[routing].mode`（默认 `cold_start`，非法值抛错）；policy 不可读时回退 cold_start（`:45-49`）。
- 路由分流：cold_start → 调 v1 `sol_tool_gate.py`（`:90, 98-107`）；shadow → v1+v2 双跑并追加 `data/governor/hook_shadow.ndjsonl`（`:53-66, 108-118`）；layered → 校验 `data/governor/layered_authorization.json`（PASS + ts≤3600s + 全部 condition ok，`:77-85`）后调 v2（`:120-138`）。
- `sol_tool_gate_v2.py:132-139` 默认读 `config/orchestration_policy_v2.toml`；`:155-179` 消费 `[models].sol_model/v4_model/k3_model`、`[governor].*`、`[tokens].stale_after_s`、`[budget].planning_*`、`[hysteresis].critical_1h_share`。
- `sol_tool_gate.py`（v1）零 policy 依赖：`ALLOWED_STATES`/`GATED_TOOLS`/`MODEL_FAMILY` 全部硬编码（`:25, 29-43`），只读 `data/progress_ledger.json`（`:58, 79`）。

### 1.2 宿主 hooks 配置存在四层接线，两层已挂 router、一层挂 v1、一层无 policy
- 活跃层1：`E:\codex-LOOP\codex-loop-s-f2\.codex\hooks.json` PreToolUse[0] → `sol_tool_gate_router.py`（timeout 10），[1] → subagent_lifecycle。
- 活跃层2：`E:\codex-LOOP\.codex\hooks.json` PreToolUse → 同一 router；宿主 `C:\Users\hzq00\.codex\config.toml` `[hooks.state]` 中两层 `pre_tool_use:0:0` 的 trusted_hash 相同（`sha256:615ede49…`），f2 另有 `pre_tool_use:0:1`（`2e3d7772…`）。改 hooks.json 命令串即触发重新信任；改脚本内容不触发。
- 用户级 `C:\Users\hzq00\.codex\hooks.json`：仅生命周期计数，无 policy hooks。
- 安装层：`install.sh:229` 只拷贝 `subagent_start_meter.sh / subagent_lifecycle.py / sol_tool_gate.py`（不含 router/v2）；`:313-327` 生成的新仓库 hooks.json 直接挂 v1 `sol_tool_gate.py`。`hooks/hooks.json.example` PreToolUse 则挂 router（与 install.sh 不一致）。

### 1.3 真正的未统一消费者在 harness：OrchestrationPolicy 默认仍读 v1
- `harness/orchestration_common.py:329-334` `OrchestrationPolicy.load` 默认 `paths.config / "orchestration_policy.toml"`（仅 `LOOP_ORCH_POLICY` 环境变量可覆盖；当前会话未设置）。
- 消费者：`budget_controller.py:131`、`agent_router.py:345`（effective_mode 用 `policy.routing_mode()`，`:353, :400`）、`dispatch_v2.py:309`、`headless_wave.py:261`、`root_turn_governor.py:291`、`trigger_eval_v2.py:386`。
- v1 文件 17:42 新增头部自称 “DEPRECATED HISTORICAL SNAPSHOT – NOT READ BY PRODUCTION CODE”，与上述实际读取关系不符。

### 1.4 v1/v2 schema 分歧，直接切 v2 会破坏 accessor
- `model_pin(role_family)` 取 `[models].{sol,k3,v4}`（v1 键）；v2 键为 `sol_model/k3_model/v4_model` → 切 v2 后 `model_pin("v4")` 抛 `ModelPinError`；`dispatch_v2.py:160-181` `resolve_role_pin` 直接依赖它。
- `model_context(family,key)` 取 `[model_context]` 表（仅 v1 有）；v2 为平铺 `v4_context_tokens/v4_compaction_tokens`。
- `l2_max_age_s` 读 `[routing].l2_max_age_s`（v1）vs v2 的 `[l2_queue].l2_max_age_s`；`rollback_key`、`l2_heartbeat_max_age_s`、`meter_stale_after_s`、`lease_renew_on_packet_creation` 仅 v1 有。

### 1.5 根目录标记分歧：父工作区 skeleton ledger 会劫持 root 解析
- router 根标记 = policy 文件（回退 `Path(__file__).parents[1]`）；v2 gate `main()` 根标记 = `data/progress_ledger.json`（`v2.py:418-427`）；v1 gate 同用 ledger 标记。
- 当前会话 `LOOP_ROOT` 未设置、cwd=`E:\codex-LOOP`；`E:\codex-LOOP\data\progress_ledger.json` 存在（29B 空骨架，packets=0 → 推导 planning）→ v1/v2 gate 会把 root 解析到父工作区而非 f2：v1 读到 planning 直接放行；v2 读 `E:\codex-LOOP\config\orchestration_policy_v2.toml`（不存在）→ policy unreadable → Sol gated 工具全拒（layered/shadow 下）。
- f2 自身 ledger：`loop_state="execution"`（无 attestation；v1 直接采信，v2 会 flag `state_key_unattested`）。

### 1.6 审计期间外部并发收敛（17:42:11，非本 agent 所为）
- mtime 证据：`orchestration_policy.toml`（3716→3837B）、`orchestration_policy_v2.toml`（8023→8161B）、`managed_files_v2.txt` 均在 2026-08-12 17:42:11 被改写。
- 结果：v2 `[routing].mode` 由 layered 改为 cold_start（此前 v1=cold_start vs v2=layered，hook 层与 harness 层语义分裂）；v1 头部加 DEPRECATED 声明；v2 补入 v1 的 budget 字段（state_cooldown_s/reclaim_on_completion/allocation_headroom）与 `gate_guard.statemachine_schema_required`；`managed_files_v2.txt` 移除 `config/orchestration_policy.toml`（双平面托管清单只剩 v2）。

### 1.7 运行时 attestation 现状：layered 已不可用，re-enable 会被拒
- `layered_authorization.json`：PASS 但 ts=1786471862.67（02:11:02），年龄 15.5h > 3600s → router `layered_authorized()` 判 False → 若 mode=layered，router 会 deny 所有 gated 根工具。
- `gate_decisions.ndjsonl` 仅 1 条（budget_high deny，02:11:40）。
- `layered_gate.ndjsonl`：05:14 re-enable 被拒（provider_health 失败）。
- `rollback_rehearsal.json`：stored `16e5747a…` ≠ 当前 v2 哈希 `4c89de92…` → `check_rollback_rehearsal` 必失败。
- `dual_plane_hash.json`：ts=1786514478（14:01:18）年龄 3.7h > 3600s，且 stored policy sha `af9319bf…` ≠ 当前 → `check_dual_plane_hash` 必失败。
- 当前双文件 mode 均已为 cold_start（17:42 收敛后），router 走 v1 fail-open 路径，attestation 过期不影响 cold_start。

## 2. Exact evidence（关键行号）
| 事实 | 证据 |
|---|---|
| router 只读 v2 policy | `hooks/sol_tool_gate_router.py:31, 37` |
| router 分层授权标记 | `hooks/sol_tool_gate_router.py:77-85`（PASS、ts≤3600s、conditions 全 ok） |
| v2 gate 读 v2 policy + 字段 | `hooks/sol_tool_gate_v2.py:137, 155-179` |
| v2 gate 硬编码 ledger | `hooks/sol_tool_gate_v2.py:213, 423` |
| v1 gate 零 policy 依赖 | `hooks/sol_tool_gate.py:25, 34-43, 58, 79` |
| OrchestrationPolicy 默认读 v1 | `harness/orchestration_common.py:331-333` |
| v1 schema accessor 契约 | `harness/orchestration_common.py:356-368`（model_pin）、model_context、l2_max_age_s |
| harness 消费者 | `budget_controller.py:131`、`agent_router.py:345/353/400`、`dispatch_v2.py:309`、`headless_wave.py:261`、`root_turn_governor.py:291`、`trigger_eval_v2.py:386` |
| 活跃 hooks 接线 | `E:\codex-LOOP\codex-loop-s-f2\.codex\hooks.json` PreToolUse[0]=router；`E:\codex-LOOP\.codex\hooks.json` PreToolUse=router；宿主 `C:\Users\hzq00\.codex\config.toml` `[hooks.state]`（615ede49… / 2e3d7772…） |
| install.sh 挂 v1、不拷 router | `install.sh:229, 313-327`；对照 `hooks/hooks.json.example` PreToolUse=router |
| v1 移出双平面托管 | `config/managed_files_v2.txt`（17:42:11 起仅剩 v2，行 10） |
| attestation 过期/失配 | `data/governor/layered_authorization.json`（15.5h）、`rollback_rehearsal.json`（16e5747a vs 4c89de92）、`dual_plane_hash.json`（af9319bf vs 4c89de92，3.7h）、`layered_gate.ndjsonl`（05:14 provider_health 拒绝） |
| 父工作区 ledger 劫持 | `E:\codex-LOOP\data\progress_ledger.json`（29B skeleton）vs `f2/data/progress_ledger.json`（loop_state=execution） |
| 当前双文件 mode | `config/orchestration_policy.toml:17`、`config/orchestration_policy_v2.toml:56` 均为 cold_start |

## 3. Minimal recommendation（本轮仅统一 policy）

### 必须改（本轮闭环最小集）
- M1 `harness/orchestration_common.py`：`OrchestrationPolicy.load` 默认路径切到 `config/orchestration_policy_v2.toml`，并把 `model_pin`/`model_context`/`l2_max_age_s` accessor 适配 v2 键（`*_model`、平铺 context 键、`[l2_queue]`）；保留 `LOOP_ORCH_POLICY` 逃生阀。
- M2 统一 root 解析标记：router/v1/v2 gate 与 OrchestrationPolicy 使用同一根标记（建议 policy 文件或强制 `LOOP_ROOT`），否则父工作区 skeleton ledger 劫持 root（当前会话即会命中）。
- M3 改完 v2 后刷新 attestation 链（顺序）：`dual_plane_hash.py` 重跑 → `routing_mode.py rehearse` → `layered_gate.py enable`（需先恢复 provider_health；05:14 曾失败）。否则一旦切回 layered，router 对 gated 工具全拒。
- M4 v1 头部 DEPRECATED 声明与真实读取关系对齐：M1 落地前不得宣称 “NOT READ BY PRODUCTION CODE”；`managed_files_v2.txt` 已把 v1 移出托管，若 v1 仍被读，WSL 平面一致性将分叉。

### 应暂缓改（本轮不做）
- D1 删除 `config/orchestration_policy.toml`：M1 未落地前删除 = harness `PolicyError` fail-closed（P0-8.3）；且是迁移证据、backup/双平面仍引用。
- D2 `install.sh` / `hooks.json.example` 统一到 router：属新仓库安装路径，改动会引入新的 trusted-hash 重新认证流程，与 f2 活跃配置解耦。
- D3 v1 gate 硬编码模型表与 fail-open 语义：cold_start 回滚面，无 policy 依赖，不在“统一 policy”范围内。
- D4 并发/ipybox/状态机（`refill_policy.toml`、`[ipybox]`、`statemachine_v2_transitions.json`）：明确不在本轮。

## 4. Unresolved
- U1 17:42:11 的并发写入来源未确认（本审计只读）；v2 哈希在 14:01（dual-plane）与 17:42 之间变化，且 policy 文件 mtime 与 marker 哈希互相矛盾，疑似 Windows/WSL 双平面同步与 model_profile 运行的竞态。
- U2 当前会话（cwd=E:\codex-LOOP）gated 工具未被拒绝，与“stale marker + layered 应 deny”的静态推断矛盾；PreToolUse hooks 是否在本会话实际触发未知（未做带 payload 冒烟，因会写 audit 日志，违反只读）。
- U3 OrchestrationPolicy 切 v2 后，v1 独有键（`rollback_key`、`l2_heartbeat_max_age_s`、`meter_stale_after_s`、`lease_renew_on_packet_creation`）在 v2 中的归属未定义。
- U4 05:14 re-enable 失败根因（provider_health）未查；K3 provider 状态在数据层，需另开只读包。

## 5. 附注
- 全程只读；被审文件零改动。
- 审计期间观察到外部进程对 `config/orchestration_policy*.toml` 与 `config/managed_files_v2.txt` 的并发写入（17:42:11），本报告快照以 17:43 为准。
