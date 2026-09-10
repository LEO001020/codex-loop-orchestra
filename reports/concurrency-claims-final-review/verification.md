# 并发声明终审 — L2 核验报告

日期: 2026-08-24 | 模式: 只读核验 | 对象: E:\codex-LOOP\github\codex-loop 双语 README
核验人: L2 Verifier | 结论: 1 项 P1，无 P0

## 结论

README 中 sustained concurrency / 20/80 / 自动补位等核心声明均有仓库实现、配置与测试支撑，
且稳定性与并发数字均已作“可配置目标、非保证、非官方限制”式限定。
唯一 P1: README.md:29 与 README.zh-CN.md:29 对“Claude 风格编排”作出无证据的绝对否定断言。

## 声明 ↔ 证据对照

| 声明 | 位置 | 证据 | 判定 |
|---|---|---|---|
| 计数 Desktop+headless 实际运行 Agent（有效并发计数器） | README.md:27/29 | harness/refill_controller_v2.py:283-371（native_roster+exec_roster+8765 observer 叠加，仅 running 计有效）；launchers/loop_monitor_server.py:1119（8765 跨平面聚合） | 支撑 |
| 自动补位完成/失败/空槽，无需用户插话 | README.md:27; zh:27 | hooks/subagent_lifecycle.py:29（terminal markers 含 turn_failed/task_complete）、:51-106（recompute_refill+schedule_refill_actuator）、:444-445,564-572；hooks/hooks.json.example:52-53（默认注册 PreToolUse/SubagentStop）；harness/refill_consumer_v2.py:204-224（debt 防止衰减） | 支撑 |
| 有界任务池（bounded backlog） | README.md:27 | harness/parent_manifest_importer.py:154-166（max_backlog=target_total，超额拒绝） | 支撑 |
| 确定性补位控制器 | README.md:29/81 | harness/refill_controller_v2.py + refill_consumer_v2.py（代码化观察→决策→执行）；tests/orchestration_v2/test_refill_controller_v2.py（31 测试，覆盖 stale native/exec 不计有效、observer 防超填、父级债务） | 支撑 |
| 默认 20/父任务、80 双通道封顶 | README.md:29/90-91; zh:29/91-92 | config/refill_policy.toml:13-14（dialogue_target=20, target_total=80）；测试断言 ctl.target_total()==80（test_refill_controller_v2.py:121） | 支撑 |
| 前 8 个 child 偏好 Desktop（非上限） | README.md:92 | config/global_working_agreement.md:36（"Prefer keeping only the first 8 children Desktop-native"）；README 已标注为工作协议偏好 | 支撑 |
| 每会话 50 子线程 | README.md:93 | config/config.toml.example:16（max_concurrent_threads_per_session=50）；README 已标注“本包配置值，非 Codex 默认” | 支撑 |
| ≤25% 有效根生产 token | README.md:94 | metering/model_token_share.py:70（WARN=0.20, BLOCK=0.25） | 支撑 |
| 带节奏与低水位的持续目标 | README.md:148 | config/refill_policy.toml:24-25（spawn_interval_ms=1000, max_initializing=8）；v4/k3 low_water 同文件 | 支撑 |
| 10–20 Desktop 不稳定为轶事观察、非 Codex 限制 | README.md:69; zh:70 | 已自带限定（anecdotal, environment-specific, no benchmark），无过度断言 | 通过 |
| 20/80/50/25 均为 LOOP 策略非官方承诺 | README.md:172; zh:173 | 已自带免责声明 | 通过 |

## P0

无。

## P1

1. 竞品绝对结论（无证据）— README.md:29 与 README.zh-CN.md:29
   问题: "Prompt-only or chat-only orchestration—including Claude-style workflows—can request
   another batch, but the prompt itself does not own durable lifecycle state, an
   effective-concurrency counter, a supervised task backlog, or a deterministic refill controller."
   该句将“Claude 风格编排”整体断言为不具备上述机制；仓库内无任何对照实验、源码依据或
   公开资料支撑此竞品结论，且 Claude Code 自身提供 hooks/subagents 等状态化扩展点，
   该断言可被证伪。EN/ZH 两版一致。
   最小修文（EN，任选其一）:
   - 删除竞品指名: "Prompt-only or chat-only orchestration can request another batch, but a
     prompt alone does not own durable lifecycle state, ..."
   - 或加限定: "including Claude-style workflows that rely on prompting alone"
   最小修文（ZH）:
   - 删除 "——包括 Claude 风格的编排——"；或改为 "包括仅依赖提示词的 Claude 风格编排"

## 备注（非阻塞，P2）

- "Most multi-agent demos show launch concurrency"（README.md:27）为无数据支撑的泛指量词；
  非指名竞品、非稳定性/性能承诺，仅建议改为 "Many/Some multi-agent demos"。

## 验证动作

只读: README 双语全文、config（refill_policy/config.toml.example/global_working_agreement）、
harness（refill_controller_v2/refill_consumer_v2/parent_manifest_importer/subagent_lifecycle/
headless_wave/loop_monitor_server）、hooks.json.example、metering/model_token_share.py、
tests/orchestration_v2/test_refill_controller_v2.py。未修改任何文件，未运行测试。