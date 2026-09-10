# L2 验证报告：架构叙事一致性（中英 README × Mermaid 架构图）

- 任务名：架构叙事一致性
- packet_id：调用上下文未提供，采用稳定目录名 l2-arch-narrative-consistency
- 范围：仅 E:\codex-LOOP\github\codex-loop（只读；未修改任何文件）
- 方法：UTF-8 逐行转储 README.md / README.zh-CN.md，机械核对 config/refill_policy.toml、
  config/orchestration_policy_v2.toml、config/model_profiles.toml、agents/*.toml、
  harness/global_desktop_mode.py、launchers/loop_monitor_server.py、仓库 AGENTS.md、dashboard.png 存在性
- 证据分类：已证实 = 本会话文件/命令直接观察；推断 = 由证据推导；未知 = 本会话无法确定

## 一行结论

中英两版在五大设计要点上主体一致，但存在 1 处中等定性分歧（执行池：低成本 vs fast）、
5 处低等级措辞/框架分歧（25% 边界、20/80 可配置 vs 默认、Observer 左右布局、根职责清单、Restore 哈希细节），全部有 file:line 证据且不涉及安全/凭据/CI/迁移路径。

## 五大要点核对

| 要点 | 结论 | 关键证据 |
|---|---|---|
| Sol 25% 目标 | 数值一致，边界措辞分歧（低置信） | EN README.md:30 "at or below 25%"；ZH README.zh-CN.md:29 "25% 以下"；orchestration_policy_v2.toml:108-112（sol_target_high=0.25，"20-25% operating band"）；AGENTS.md:149（over 25% BLOCK） |
| 20/80 策略 | 数值一致，框架措辞分歧 | EN README.md:36/232 "Configurable…20-active…80-worker envelope"；ZH README.zh-CN.md:35/227 "默认维持 20…总目标 80"；refill_policy.toml:10-14（dialogue_target=20，target_total=80） |
| 三模型族建议 | 结构一致，执行池定性分歧 | EN README.md:26/55（execution pool / fast bounded workers）；ZH README.zh-CN.md:25/54（低成本执行池 / 低成本任务）；model_profiles.toml:24-26（three-family-example，fast-executor）；agents/worker.toml:11（"Low tier"） |
| Desktop/headless | 两版一致（已证实） | EN README.md:28/53-54 vs ZH README.zh-CN.md:27/52-53（10–20 不稳定、双平面、受监督 codex exec） |
| task_XX+Observer | 主体一致；布局措辞分歧 | task_01–task_50：EN README.md:27 / ZH README.zh-CN.md:26，agents/worker.toml:17-27 等 4 个角色文件均含 50 个候选（已证实）；:8765：EN README.md:69 / ZH README.zh-CN.md:68，loop_monitor_server.py:1125 默认 8765（已证实）；ZH:92 "左侧/右侧" 布局表述 EN:93 无对应 |

## 主要不一致（含最小建议）

1. 执行池定性：ZH 说"低成本"（README.zh-CN.md:25、:54），EN 说 "fast"（README.md:55）或无属性（README.md:26）。
   仓库两义都有部分支撑（agents/worker.toml:11 "Low tier"；model_profiles.toml:26 "fast-executor"），
   但同层同图两种语言讲不同属性。建议：两版统一（如"低层级（low-tier）fast bounded 执行池"或明确二选一）。
2. 25% 边界：EN "at or below 25%"（README.md:30）vs ZH "25% 以下"（README.zh-CN.md:29）。
   策略带为 "20-25% operating band" 且 sol_target_high=0.25（orchestration_policy_v2.toml:108-112），
   EN 与策略一致；中文"以下"通常含本数，属低置信措辞分歧。建议：ZH 改为"不超过 25%（含）"。
3. 20/80 框架：EN 强调 Configurable（README.md:25、:36），ZH 强调"默认维持/系统设定值"且未提可配置（README.zh-CN.md:24、:35）。
   两者与 refill_policy.toml:10-14 均不矛盾，但叙事属性不同。建议：ZH 补"可配置"。
4. Observer 布局：ZH 有"左侧 Desktop / 右侧 Observer"空间表述（README.zh-CN.md:92），EN 无（README.md:93）。
   docs/assets/dashboard.png 存在，但本会话未做视觉比对（未知）。建议：两版统一并以实图为准。
5. 根职责清单：EN "Shell work…routine verification"（README.md:24）vs ZH "搜索、测试、轮询、计数、重试"（README.zh-CN.md:23），
   清单项不一致；仓库 AGENTS.md:131-132 的 L0 清单（搜索/网页、统计、聚合日志、批量读、测试/计数）更接近 ZH。建议：两版对齐策略原文。
6. Restore 细节：ZH "经过哈希验证的 Restore"（README.zh-CN.md:42）vs EN "verified restore"（README.md:43）。
   harness/global_desktop_mode.py:235、:251 在备份时记录 sha256（已证实）；restore 读取时是否复核未确认（推断）。建议：EN 补 "hash-verified" 或 ZH 放宽为 "verified"。

## 次要不一致

- 徽章：EN README.md:11 有 Codex multi-agent 徽章，ZH README.zh-CN.md:8-10 无（外观级）。
- 章节结构：EN 有 "### Codex-native settings"（README.md:151）且 Contributing/License 分立（README.md:242、:246）；ZH 合并为 "## 参与贡献与许可证"（README.zh-CN.md:237）且无对应子标题。
- 配置来源：EN 指明合并自 "config/config.toml.example"（README.md:153），ZH 未指明来源文件（README.zh-CN.md:148）。
- 属性差异：ZH 多出 "可恢复"（README.zh-CN.md:21），EN 仅有 "sustained, observable"（README.md:22）。
- 重启措辞：ZH "必须完全退出并重启"（README.zh-CN.md:116）vs EN "Fully restart"（README.md:119），强度差异可忽略。

## 已证实一致（要点内）

- task_01–task_50 范围：两版一致，agents/worker.toml、verifier.toml、reviewer.toml、duty_officer.toml 均含 50 个候选。
- Observer :8765 与 "任务语义/实际模型/平面/健康度"：两版一致，loop_monitor_server.py:1125 默认 8765。
- Desktop/headless 双平面与 10–20 不稳定叙事：两版一致。
- 模型家族 Mermaid（ROOT/EXEC/VERIFY/HUMAN 四节点）：两版逐节点一致（README.md:75-85 vs README.zh-CN.md:74-84）。
- 50 会话上限 ≠ LOOP 目标、20/80 非官方限制：两版一致（README.md:166、:232 vs README.zh-CN.md:161、:227）。

## 未确定/推断

- ZH:92 左右布局是否与 dashboard.png 实际一致（未做视觉比对，未知）。
- Restore 读取时是否重新校验 sha256（仅确认备份期记录摘要，推断）。
- "25% 以下"按中文惯例是否含 25% 本数（语义惯例，低置信分歧）。

## 机械依据清单

- E:\codex-LOOP\github\codex-loop\README.md（248 行，UTF-8 逐行读）
- E:\codex-LOOP\github\codex-loop\README.zh-CN.md（241 行，UTF-8 逐行读）
- config/refill_policy.toml:7、:10-14
- config/orchestration_policy_v2.toml:108-112、:122-132
- config/model_profiles.toml:2、:9、:24-26
- agents/worker.toml:11、:17-27；verifier/reviewer/duty_officer.toml 同构 nickname 列表
- harness/global_desktop_mode.py:235、:251、:283-285
- launchers/loop_monitor_server.py:1125；launchers/Start-Codex-LOOP-Monitor.ps1:1、:8
- 仓库 AGENTS.md:131-132、:149
- docs/assets/dashboard.png（存在性：Test-Path = True）
- 未修改任何文件；未触碰 Git、凭据、CI、迁移或发布路径