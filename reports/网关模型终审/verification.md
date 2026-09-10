# 网关模型终审 — L2 核验报告

- 范围：E:\codex-LOOP\github\codex-loop 工作区未提交 diff 中 README/INSTALL（EN+ZH）新增的双语模型声明
- 对照锚点：config/model_profiles.toml、harness/model_profile.py、实际根模型选择边界（hooks/root_agent_spawn_gate.py、dispatch/headless_wave、config.toml.example、Observer）
- 结论：P0=0，P1=0。新增声明与实际实现一致；未误称 LOOP pin/观测根模型；未暗示捆绑凭据。

## 核验结论（逐条）

1. 根模型边界：INSTALL.md:16-24 / INSTALL.zh-CN.md:16-21 / README.md:141 / README.zh-CN.md:142 均声明“根模型由用户在 Codex/OpenCodex 兼容运行时选择”，与 config/model_profiles.toml:6-7 注释（"root model is selected by the user in Codex; this file controls execution and independent review children"）一致。harness/model_profile.py 只写 [agents] default_subagent_model（:166-167,187-189）、agents/*.toml 顶层 model（project_updates :130-178 / global_updates :181-210）与 roles.yaml；从不写用户级顶层 root `model` 键。config/config.toml.example 无顶层 model 键（仅 [agents] 默认子代理模型）。
2. 子代理 pin 属实：hooks/root_agent_spawn_gate.py:88-97 对 Desktop spawn 强制 role 对应 model/reasoning_effort 与活动 profile 完全一致，拒绝 fork_context=true；INSTALL.md:144 的 “Spawn denied” 描述与其逐字对应。headless 侧 dispatch.py 从 agents/<role>.toml 读 pin（:8-16），headless_wave.py:439-457 resolve_role_pin 并对漂移 fail-visible。
3. 非 GPT/异厂商声明属实：model_profile.py 无模型白名单/目录校验，任意模型 ID 均可写入 profile（validate_updates 仅校验自洽与 TOML 语法，:213-231）；root_agent_spawn_gate 仅与 profile 值做相等比较，无 gpt- 硬编码；config/model_profiles.toml:19-26 three-family-example 演示跨厂商执行/复审；活跃默认 portable（gpt-5.6-terra/gpt-5.6）与 INSTALL.md:18-19 / INSTALL.zh-CN.md:16-17 的“默认”表述一致，且 “three-family-example 默认不激活” 与 active_profile="portable" 一致。
4. 无根模型 pin/观测误称：新增文本未出现 “LOOP 固定/选择根模型” 字样；README.md:48 / README.zh-CN.md:50 仪表盘 “observed models/实际模型” 由 launchers/loop_monitor_server.py:424-466 从 rollout turn_context 只读提取，是真实观测能力，且上下文仅指 Agent/任务，不构成对根模型的 pin 声明。
5. 无凭据捆绑暗示：所有凭据句均为否定式声明（INSTALL.md:23-24,108；README.md:141-142,148,159）；验证：仓库无受跟踪凭据文件，.gitignore:31-42 排除 .env/secrets/.codex/config.toml，scripts/verify_release.py:13-14 FORBIDDEN 含 credentials/secrets/.codex；model_profile.py 不写 provider catalog/token（docstring :5-9）。

## P0 / P1

- P0：无。
- P1：无。

## 非阻断措辞提示（不在 P0/P1 内）

- README.md:141 “three independent routing choices” 比中文版 “三个彼此独立的模型选择”（README.zh-CN.md:142）更宽泛；协调者并非 LOOP 路由对象，仅下一句即修正边界，不构成误称。如要最小修文可改为 “three independent model choices”。此条不要求修改。

## 高风险信号检查

- diff 仅触碰 README/INSTALL/AGENTS/SHA256SUMS/THIRD_PARTY_NOTICES（文档与完整性清单），无测试数减少、无凭据/CI/迁移路径变更 → 不触发确定性 L3 路由。

{"verdict": "pass", "reason": "双语新增声明与 model_profiles.toml、model_profile.py 及实际根模型选择边界一致，未误称 LOOP pin/观测根模型，未暗示捆绑凭据；仅 README.md:141 存在非阻断的措辞宽泛点。", "evidence": ["README.md:141", "README.zh-CN.md:142", "INSTALL.md:16-24", "INSTALL.zh-CN.md:16-21", "config/model_profiles.toml:6-26", "harness/model_profile.py:166-167", "harness/model_profile.py:181-210", "harness/model_profile.py:213-231", "hooks/root_agent_spawn_gate.py:88-97", "config/config.toml.example:12-20", "launchers/loop_monitor_server.py:424-466", "scripts/verify_release.py:13-14", ".gitignore:31-42"]}