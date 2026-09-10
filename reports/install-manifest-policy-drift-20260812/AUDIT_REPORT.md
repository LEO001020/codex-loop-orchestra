# 安装清单策略漂移 — 只读审计报告

* 任务名：安装清单策略漂移
* 范围：`install.sh`、`config/managed_files_v2.txt`、`SHA256SUMS`、`harness/dual_plane_hash.py` 对双 policy 的部署/校验依赖。
* 约束：只读，未编辑任何受管文件；仅收敛唯一 orchestration policy 与安全模式；未创建子 agent。
* 审计日期：2026-08-12。当前 active_profile=v4f，v4 pin 实测一致：`weiwu/deepseek-v4-flash` + `ultra`。

## 1. Verified findings

### F1 — 双 policy 都在生产读取链上，且存在真实值分歧，不是文档镜像

* v1 `orchestration_policy.toml`：`status="phase2-implementation"`，`[routing].mode="cold_start"`，`[models]` 为 `sol/k3/v4` + `[model_context]`；无 `[validator]`/`[l2_queue]`/`[gate_guard]`/`[capabilities]`，`[governor]` 无 `fail_mode`/`break_glass_env`。
* v2 `orchestration_policy_v2.toml`：`policy_version=2`，`status="fable-audit-implementation"`，`[routing].mode="layered"`，`[models]` 为 `sol_model/k3_model/v4_model` + `legacy_aliases`；含 `[gate_guard].require_dual_plane_hash=true` 与 `[governor].fail_mode="closed"`（安全模式唯一声明处）。
* 读取链实测：
  * v1 执行面：`harness/agent_router.py` 经 `OrchestrationPolicy.load`（`orchestration_common.py:316-333` 默认读 v1）→ `harness/dispatch_v2.py:61,70` import → `harness/headless_wave.py:32,37` import。
  * v2 执行面：`harness/dispatch.py:129`、`l2_consumer.py:152,431`、`layered_gate.py:111,362,385`、`routing_mode.py:77`、`trigger_eval.py:232`、`statemachine.py:380`、`usage_reconcile.py:43`、`model_token_share_v2.py:608`、`model_token_share_bridge.py:123`、`hooks/sol_tool_gate_router.py:31,37`、`hooks/sol_tool_gate_v2.py:137`、`orchestration_epilogue.py:41`、`plan_pipeline.py:46`。
* 结论：`routing.mode` v1=cold_start vs v2=layered 是两套执行面同时生效的真分歧；唯一 policy 收敛尚未发生。

### F2 — install.sh 不安装、不校验任何 policy 文件，也不消费任一清单

* `install.sh` 共 356 行，5 步：prereq、`agents/*.toml` → `$CODEX_HOME/agents/`、`config.toml.example` 合并、data 骨架 + hooks 挂载、`smoke_gate.sh`。全文无 `SHA256SUMS`、`managed_files_v2.txt`、`orchestration_policy` 引用。
* `install.sh` 自身只在 SHA256SUMS 中（哈希当前匹配 `14c2565…`），不在 `managed_files_v2.txt`；`harness/smoke_gate.sh` 同理。
* policy 部署/同步实际依赖 `harness/model_profile.py:110-121`（唯一 v1/v2 同步机制），无静态断言校验 v1↔v2 语义等价；`roles_v2.yaml` 不在其更新集。

### F3 — SHA256SUMS 过期且与双平面清单覆盖域脱节

* 实测 100 行：66 匹配、34 失配、0 缺失。失配覆盖 AGENTS.md、README.md、4 个 agent TOML、config.toml.example、roles.yaml、dispatch.py、statemachine.py、trigger_eval.py、worktree_pool.sh、hooks/sol_tool_gate.py、subagent_lifecycle.py、metering/*、tests/* 等。
* 30 个 v2 时代文件只在 v2 清单、SHA256SUMS 不覆盖：`orchestration_policy.toml`、`orchestration_policy_v2.toml`、`managed_files_v2.txt`、`refill_policy.toml`、`model_profiles.toml`、`roles_v2.yaml`、`dual_plane_hash.py`、`dispatch_v2.py`、`layered_gate.py`、`l2_consumer.py`、`sol_tool_gate_router.py`、`model_token_share_v2.py`、`headless_wave.py` 等。
* `README.md:10` 仍把 `sha256sum -c SHA256SUMS` 列为唯一 Integrity 手段；README 全文无 managed_files_v2/dual-plane 提及 → 文档校验面与实际受管面是两套系统。

### F4 — v2 清单固化双 policy 而非收敛；dual_plane_hash 只证平面相等、不证唯一

* `managed_files_v2.txt` 共 56 行全部存在于树中；第 10-11 行同时托管 v1 与 v2。
* `dual_plane_hash.py:45` 的 `policy_sha256` 只对 v2 计算；v1 仅参与 Windows/WSL 平面相等比较（第 26-44 行）。v1/v2 值分歧零检测。
* `layered_gate.py:370-385` 仅校验：marker status=PASS、双平面 manifest sha 相等、age≤3600、policy_sha256==当前 v2 哈希。即 `require_dual_plane_hash=true` 只证明两平面一致，不证明只有唯一 policy。
* `data/governor/dual_plane_hash.json` 最近一次 `status=PASS`（ts=1786514478），56 文件、mismatches=[]、双平面 sha 相同、policy_sha256=af9319bf…（仅 v2）→ 校验链对当前双 policy 状态判 PASS。

### F5 — install.sh 挂载面、实际部署面、清单三方不一致

* install.sh 挂载 `subagent_start_meter.sh`、`subagent_lifecycle.py`、`sol_tool_gate.py`；其生成的 hooks.json `PreToolUse` 只注册 `sol_tool_gate.py`（subagent_start_meter.sh 挂载但未写入 hooks.json）；hooks.json 已存在且不完整时仅 NOTICE，不自动合并。
* 实际部署的 `.codex/hooks.json`（git 未跟踪）：`PreToolUse` → `sol_tool_gate_router.py`（按 v2 routing.mode 分派 v1/v2 门）；`SubagentStart` → `subagent_start_meter.ps1`；`Stop` → `reconcile_subagent_metering.ps1`。与 install.sh 模板不同源。
* 清单覆盖差：`hooks/sol_tool_gate_v2.py`（router 分派的 v2 安全门，19366B）与 `hooks/subagent_start_meter.sh`（install.sh 挂载）都不在 `managed_files_v2.txt`；`.codex/hooks/reconcile_subagent_metering.py`、`subagent_start_meter.ps1` 只在 SHA256SUMS；`.codex/hooks.json` 只在 v2 清单。

### F6 — 整个 v2 面在 git 中未跟踪，部署基线=工作区快照

* 唯一提交 `f1d6eac "Fable F2 delivery baseline"`；`git status` 显示 orchestration_policy_v2.toml、managed_files_v2.txt、dual_plane_hash.py、.codex/、hooks/、harness/ v2 文件、data/governor/ 等全部 `??` 未跟踪；SHA256SUMS、install.sh、agents/*.toml 等为 `M`。
* 安装/审计无法基于提交校验 v2 面；SHA256SUMS 的 34 处失配中相当部分即未提交的后续修改。

### F7 — 14 个现存活跃文件完全无清单覆盖（两清单都不含）

`agents/plan_expander.toml`、`config/ipybox_sandbox.json`、`config/statemachine_v2_transitions.json`、`config/triggers_v2.yaml`、`harness/agent_router.py`、`harness/budget_controller.py`、`harness/ipybox_cleanup.py`、`harness/ipybox_lazy.py`、`harness/lifecycle_supervisor_v2.py`、`harness/result_reducer.py`、`harness/root_turn_governor.py`、`harness/short_result_validator.py`、`harness/trigger_eval_v2.py`、`hooks/sol_tool_gate_v2.py`。

* 其中 `agent_router.py` 是读 v1 policy 的执行面、`sol_tool_gate_v2.py` 是 router 分派的 v2 安全门 → 唯一 policy 收敛的关键文件反而零校验覆盖。
* `agents/plan_expander.toml` 会被 install.sh 的 `agents/*.toml` 通配安装，但两个清单都不托管。

### F8 — 当前模型 pin 无漂移（v4f 一致），但一致性靠手动同步机制维持

* 实测：两 policy v4 pin 均为 `weiwu/deepseek-v4-flash` + `ultra`；worker.toml:12-13、duty_officer.toml:15-16 一致；reviewer/verifier/plan_expander 均为 K3 max。
* `model_profile.py:110-121` 同步面：两 policy、worker/duty_officer、.codex/config.toml、config.toml.example、roles.yaml；不写 roles_v2.yaml；无 v1==v2 语义等价断言。

## 2. Exact evidence

| 证据点 | 位置 | 内容 |
|---|---|---|
| v1 执行面 | orchestration_common.py:316-333 | OrchestrationPolicy.load 默认读 orchestration_policy.toml |
| v1 执行面入口 | dispatch_v2.py:61,70；headless_wave.py:32,37 | dispatch_v2 import agent_router；headless_wave import dispatch_v2 |
| v1 值 | orchestration_policy.toml [routing]/[models] | mode="cold_start"；sol/k3/v4 键 |
| v2 值 | orchestration_policy_v2.toml [routing]/[models]/[governor]/[gate_guard] | mode="layered"；sol_model/k3_model/v4_model；fail_mode="closed"；require_dual_plane_hash=true |
| 双 policy 托管 | managed_files_v2.txt:10-11 | 两文件均入 56 行清单 |
| 只验 v2 指纹 | dual_plane_hash.py:45；layered_gate.py:378-385 | policy_sha256=sha(v2)；gate 仅比 v2 哈希+双平面相等+age |
| 最近 attestation | data/governor/dual_plane_hash.json | status=PASS，ts=1786514478，56 文件，双平面 sha 相同 |
| install 无校验 | install.sh（356 行全文） | 无 SHA256SUMS/managed_files_v2/orchestration_policy 引用 |
| hooks 三方差 | install.sh 第 4 步 vs .codex/hooks.json vs managed_files_v2.txt | 模板挂 sol_tool_gate.py；实际挂 router/ps1；清单缺 v2 门与 start_meter.sh |
| SHA256SUMS 状态 | SHA256SUMS（100 行） | 66 匹配 / 34 失配 / 0 缺失；不含 30 个 v2-only 文件 |
| 文档校验面 | README.md:10,256 | sha256sum -c SHA256SUMS 为唯一 Integrity；无 managed_files_v2 提及 |
| git 基线 | git log（唯一提交 f1d6eac）；git status --short | v2 面全部 ?? 未跟踪 |
| pin 一致 | model_profiles.toml（active_profile=v4f）；两 policy [models]；worker.toml:12-13、duty_officer.toml:15-16 | v4=weiwu/deepseek-v4-flash+ultra；K3=max |
| 同步机制 | model_profile.py:110-121 | v1/v2 同写 v4 pin；roles_v2.yaml 不写 |

## 3. Minimal recommendation（第一轮最小变更）

1. 收敛唯一 policy：orchestration_common.py:333 默认读取改为 orchestration_policy_v2.toml（agent_router 随 OrchestrationPolicy.load 跟随），从 managed_files_v2.txt:10 移除 v1 行；v1 退役或仅留历史快照。
2. install.sh 加校验门：安装前校验 SHA256SUMS（或 dual_plane_hash）对受管面（至少 orchestration_policy_v2.toml）哈希，失配即 abort；hooks.json 模板改用 sol_tool_gate_router.py 并与 hooks/hooks.json.example 对齐。
3. SHA256SUMS 重生成覆盖 v2 面全部受管文件（含 dual_plane_hash.py、managed_files_v2.txt 自身、orchestration_policy_v2.toml）并更新 README；或退役 SHA256SUMS，以 managed_files_v2.txt+dual_plane_hash.py 为唯一完整性面。
4. 14 个无覆盖文件纳入 managed_files_v2.txt（至少 agent_router.py、sol_tool_gate_v2.py、plan_expander.toml、trigger_eval_v2.py、lifecycle_supervisor_v2.py、statemachine_v2_transitions.json、triggers_v2.yaml），或明确移出交付面。
5. v2 面整体提交 git（单一 baseline 提交），使 install/audit 基于提交校验而非工作区快照。

## 4. 后续安装轮要处理的债

* hooks 三态统一：install.sh 模板 / hooks.json.example / 实际 .codex/hooks.json（含自动升级合并而非 NOTICE）。
* model_profile.py 扩展：写入并断言 roles_v2.yaml 与 plan_expander.toml；增加 v1↔v2 语义等价断言（或随唯一 policy 删除 v1 分支）。
* 单清单合并：SHA256SUMS 与 managed_files_v2.txt 合并（含 install.sh、smoke_gate.sh、README.md、AGENTS.md、VERSIONS.lock），避免双清单互不覆盖。
* v1 读取链迁移测试：tests/orchestration_v2/test_agent_router.py、conftest.py:30-31,94,146、test_cold_start_rollback.py:81-88 仍引用 v1 路径。
* 安装失败语义：明文规定 hash/清单失配 → abort 与 smoke 失败 exit 1 的优先级和输出契约。

## 5. Unresolved

* 运行期入口确认：headless_wave → dispatch_v2 → agent_router(v1) 已由 import 链证实；dispatch.py 直调与 sol_tool_gate.py 直挂是否仍有运行期消费者需运行观测（scope 不深挖并发面）。
* 外部验收是否强制 sha256sum -c SHA256SUMS（README 声明 B-level 交付，仓库内无 CI/验收脚本证据）。
* LOOP_ORCH_POLICY 环境变量覆盖路径是否有仓库外消费者依赖 v1 路径。
