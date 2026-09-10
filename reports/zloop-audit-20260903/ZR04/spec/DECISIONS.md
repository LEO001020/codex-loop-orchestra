# DECISIONS — 决策日志（格式见 VOL-22 §5；只记改契约/系统的决策）

## D-1 SQLite journal 决策（2026-09-02，待 M0 确认）
- 证据：本机 Python 3.14.3 → sqlite 3.50.4；官方 WAL-reset 影响 3.7.0–3.51.2，修复 3.51.3 / backport 3.50.7、3.44.6
- 决策：版本闸门（I22）内建为 `db.py` 硬逻辑：满足修复版本 → WAL+FULL；否则 DELETE+EXTRA（本机 v1 默认路径）
- 备选与弃因：升级 sqlite（pysqlite3 cp314 wheel 可用性未知 → P-SQL1 实测后再定终案）；强行 WAL（违反 I22）
- 影响：VOL-07 §3；P-SQL1 是 M0 退出判据之一

## D-2 await 参考实现 = background + notification（2026-09-02）
- 证据：本 session 实测后台任务 task-notification 重新唤起 root；前台 Bash 硬上限 600,000ms
- 决策：>10min wave 一律 `wave start` + 结束 turn 等 notification；`await --timeout` 上限 540s
- 影响：VOL-03 §3、VOL-09 §7；G-COG 预期定档 B（P-GC1 最终裁决 /goal 交互）

## D-3 hook 实现按保守分支（2026-09-02）
- 证据：官网与本机 zcode-guide 插件文档三处不一致（输出 schema 宽严 / async 语义 / CLAUDE_SESSION_ID）
- 决策：只发已文档字段、不做 async、session_id 一律读 stdin JSON；P-HK2 实测后可放宽
- 影响：VOL-02 §1.1、VOL-05 §1

## D-5 旧 LOOP worker 隔离用 CODEX_LOOP_REQUIREMENTS_TOML，不删系统文件（2026-09-02）
- 证据：requirements.toml 文件头明示 `override: CODEX_LOOP_REQUIREMENTS_TOML`；文件已备份（sha256 6c0f4f7b…）
- 决策：M7 的 Codex worker 进程统一携带 `CODEX_LOOP_REQUIREMENTS_TOML=<ZLoop 管理的空 requirements 文件>`，隔离旧 hook；系统级文件的实际移除仍走用户确认
- 备选与弃因：直接删除 ProgramData 文件（超出执行 Agent 停止条件）；不处理（旧 PreToolUse gate 会命中每个 worker）
- 影响：VOL-02 §3.5、VOL-17 §6、VOL-12 §4

## D-6 Codex 登录态损坏 → 后端探针全部转 BLOCKED-manual（2026-09-02）
- 证据：`codex login status` → "Error checking login status: invalid ID token format"（19:20 实测）
- 决策：P-CDX1/P-CDX2/P-CDX3/P-LUNA1 标记 BLOCKED-manual；SDK 安装与 API 面核对照常完成；不尝试任何登录自动化或备份 auth 换用（宪章禁令 5）
- 影响：M7 入口条件；manifest 状态；需用户执行 `codex login` 后重跑 P-CDX 组
## D-4 旧 LOOP 提取表以实际代码树为准（2026-09-02）
- 证据：源码审计证实 4 个幻影/错位项（vector DB、Blackboard dump、Compatibility Gateway、IPybox-as-memory-authority）；旧树无 hash-chain
- 决策：VOL-02 §7 为提取地面真相；Phase -1 的 KEEP/DROP 表从它派生
- 影响：VOL-18 §G 追溯表；P-ARC-1 核对

## D-7 pysqlite3 0.6.0 可装但捆绑 sqlite 3.51.1，仍在受影响区间（2026-09-02，P-SQL1 实测）
- 证据：cp314 win_amd64 wheel 安装成功（28s），但 `pysqlite3.sqlite_version` = 3.51.1 < 3.51.3
- 决策：v1 终案 = `journal_mode=DELETE + synchronous=EXTRA`（D-1 路径 c）；未来升级走捆绑 sqlite.org DLL（路径 b）
- 影响：VOL-07 §3；db.py 版本闸门维持（约 10 行，非子系统）

## D-8 controller 所有权改为 S 内 CAS token；外部 cancel 只写 cancel_requested（2026-09-02，剃刀审计 P0 修复）
- 证据：VOL-03 §5（wave 长进程持 run.lock）与 VOL-09 §8（cancel 需 S 事务）矛盾——控制面自我锁死
- 决策：禁止长持 OS 锁；runs 表 controller 字段 CAS claim；cancel_requested 是 command input 非 transition；接管需机械证明 (pid, pid_start_time) 死亡
- 影响：VOL-03 §5、VOL-07 §4、VOL-09 §8、VOL-01 I43

## D-9 hook 注册 7→5：删除 PreToolUse/PermissionRequest 生产捕获（2026-09-02）
- 证据：两事件在每次工具调用前同步拉起进程（热路径税），PostToolUse(+Failure) 已含完整结构化结果；GPT-5.6 效率报告强调 repeated-region 成本按迭代次数放大
- 决策：`zloop install` 只注册 SessionStart/UserPromptSubmit/PostToolUse/PostToolUseFailure/Stop；hook 代码保留 7 事件分派
- 影响：install.py（已改，105 测试绿）、VOL-05 §1–2

## D-10 Kimi 单路实现：K1 server 为主，K2 CLI 只作文档化 fallback（2026-09-02，P-KIM1 实测）
- 证据：K1 全链路 PASS 且 messages 恢复路径天然缓解 #1897 类 stdout 丢失；K1/K2 同账号配额 ⇒ 双 lane 无互备价值；K2 末行为 meta:session.resume_hint 需专门判据
- 决策：仅实现 K1（openapi hash 契约锁定）；K2 写成 fallback 设计注记
- 影响：VOL-15 §3、VOL-02 §5

## D-11 C2C P/A 分线程对 NORMAL 降级为 A/B 变量（2026-09-02）
- 证据：fresh-thread 抗锚定是无对照实验假设；无证据表明其 catch-rate 收益抵消 NORMAL 档的浏览器/线程重建摩擦
- 决策：HIGH/CRITICAL 保持强制 fresh；NORMAL 线程策略（C_same vs C_fresh）进 C2C A/B 矩阵
- 影响：VOL-16 §2、VOL-19 §5

## D-12 剃刀删减清单：v1 删除/退迟的实体（2026-09-02）
- 证据：第二次独立审计（GPT 奥卡姆剃刀）+ 本机探针数据
- 删除：worker-host-per-launch 与 client 分片（VOL-12 矛盾且 SDK 单 client 多 turn 已核实）；controller_epochs 作为所有权机制（并入 D-8）；H1.machine 持久化（实现本就未持久化，规范对齐）
- 退迟：批量化+bisect+oracle 缓存（无 profiling 证据前不进生产）；每 launch Job Object（interrupt 可靠性未证伪）；AppServerBackend（SDK 未失败）
- 派生：`attempt` 序数由 launches 派生，attempts 表计划 v1.1 删除
- 影响：VOL-03/04/10/12；代码待改点已记录于 PROGRESS

## D-14 勘误 D-6：Codex 认证问题仅限 ChatGPT-membership 路由；opencodex/cliproxy 路由 SDK 实测可用（2026-09-02 深夜）
- 证据：用户指出实际使用 opencodex；实测 npm `codex` shim = @bitkyc08/opencodex 2.39.0（官方在 codex.opencodex-real）；config.toml `model_provider="cliproxy"`；SDK 活回合 14.8s 完成
- 决策：P-CDX1/P-CDX2 转 PASS；M7 后端可用真实路由实测；P-LUNA1 语义变化——路由暴露的是 cliproxy 的模型与 provider 侧 web_search，GPT-5.6 Luna membership 路由仍未知（若需要再探）
- 影响：VOL-02 §3.5/§4、manifest、PROGRESS 解锁清单

## D-15 live 供应商测试预算（2026-09-02）
- 证据：今晚 Kimi 5 小时配额被测试耗尽（日志实数：CLI 6 完成回合 + server 22 turn.started ≈ 28 个最小回合；用户本人未使用）；P-KIM1 探针重跑超量 + research 实现 agent 开发期实连验证未设预算
- 决策：探针 ≤2 live 回合/次；实现 agent 只打 stub；403/429 即停；live 回合必须可从本地日志审计
- 影响：repo AGENTS.md、VOL-22 §6.5；此前 PROGRESS 中"Kimi 配额自动恢复后 research lane 恢复"结论不变

## D-16 hook 部署：plugin scope 为生产目标，user-config 降为兼容回退（2026-09-02，第三次审计采纳）
- 证据：ZCode 官方插件文档（enable 注册到当前 workspace / disable 移除）；user-global hooks 对无关 workspace 也付进程税且 disable(ZLoop)≠native
- 决策：hook.py 增加 cwd 严格项目过滤（无关 workspace 一律不落盘，隐私修复）；plugin/ 包（manifest+hooks.json+启动 cmd）为标准部署形态；user-config 安装保留为显式回退
- 影响：VOL-05 §1、install.py/hook.py

## D-17 P-SEC1：读隔离与 loopback 隔离 FAIL（2026-09-02，第一手哨兵证据）
- 证据：两个哨兵文件被逐字读回 + 127.0.0.1 canary 被取回（公网被拒）
- 决策：supervisor 在 kimi server 存活（healthz 可达）时拒绝开 wave；M7 真实负载 gate = worker 专用低权限 OS 身份/边界；过渡期 Codex worker 仅限可信内容负载
- 影响：VOL-02 §4、VOL-17、M7 gate

## D-18 Research 三轴语义：ProviderHealth ≠ RetrievalOutcome ≠ EvidenceTrust（2026-09-02）
- 证据：配额耗尽曾误标 source_unverified——"拿到了证据未验证"与"根本没拿到证据"被混同
- 决策：quota ⇒ provider_health=QUOTA_EXHAUSTED + retrieval_outcome=NO_EVIDENCE；verification/trust 仅对 EVIDENCE_FOUND 有效
- 影响：VOL-04 §10、broker.py

## D-19 Kimi research session 收缩为纯搜索器（2026-09-02）
- 证据：Kimi 内建 Read/Write/Edit/Bash/Grep/Glob + WebSearch/FetchURL；prompt 提交支持 disabled_tools
- 决策：research lane 提交时禁 Read/Write/Edit/Bash/Grep/Glob，仅留 WebSearch/FetchURL——信息服务≠执行服务，网页注入到工具层无 capability 可用
- 影响：VOL-15 §7、kimi_server.py

## D-20 D-8 死亡证明落码（2026-09-02）
- 证据：takeover capability 存在而无死亡证明比没有 takeover 更危险（split-brain 风险）
- 决策：takeover_controller 要求机械 (pid, process_start_time) 活性证明；PID 不存在=死、start 不匹配=死（复用）、匹配=拒绝、无法判定=拒绝（fail-closed）
- 影响：db.py、supervisor.py、I43

## D-21 redact 精化：凭据形邻接判定，非裸 "key"（2026-09-02）
- 证据：裸 "key" 分段会误杀 primary_key/cache_key/public_key_id 等合法证据
- 决策：api/access/private + key 邻接判定为秘密；普通 key 保留；新增 Kimi-token 哨兵回归测试（假 token 全状态树扫描）
- 影响：redact.py、tests

## D-22 系统提示词替换：子代理 profile 正门落地；主会话静态文本剃刀豁免（2026-09-03）
- 证据：bundle（未修改）定向探查全链闭合——① 子代理组装（`eni` @10536440）从不出载原生 to-C 静态块，其系统提示词 ≈ agent 定义文件正文（加载器 @10359980 `systemPrompt: t.body.trim()`，扫描 `~/.zcode/cli/agents/*.md`，@11895074）；② `customSystemPrompt` 分支（@7740953）属 workflow 子会话路径（opts schema @767539）；③ 主会话无入口：config.json 白名单（`MCo` @6927900）无 systemPrompt、73 个 ZCODE_* 环境变量无 prompt 相关、CLI kebab 旗标 0 命中；④ outputStyle 仅替换沟通块且只来自插件包。完整证据链：`zloop-gen8/artifacts/prompt-engineering/D22-native-prompt-mechanism.md`
- 决策：ZCode 侧 worker/审计 fan 改用自定义 agent profile（P3 家族正文 + tools 白名单——白名单同时切断 26 个 computer-use MCP 工具 schema 注入）；已部署 `~/.zcode/cli/agents/zloop-worker.md`（实现型，~513 tok）与 `zloop-auditor.md`（只读型，~449 tok），新会话生效，删文件即回滚。主会话：原生静态文本实测仅 ~1.5-2K tokens 且只 root 一个会话支付，真正量级是工具 schema（computer-use 26 工具 ≈ 10K+ tokens，每请求注入）——按"无明确大收益勿增实体"剃刀，bundle 编辑不执行（procedure 保留为显式可选项）；root 减负走插件/skill 卫生（Settings 关闭按需重开）
- 备选与弃因：bundle 外科手术（12.5MB 压缩产物生产依赖，省 ~2K tok/回合，ROI 不足）；API 网关改写（引入基础设施实体）
- 影响：v3-CANDIDATES 部署教义、编排层（fan 用 zloop-* 类型）、用户解锁清单（新会话验证 subagent_type 出现 + token 面对照）

## D-22 勘误与升格（2026-09-03 凌晨 II，GPT 第四轮审计 × 独立一手复核）
- 证据：① **扫描根勘误（P0）**——storage.dir 默认 `~/.zcode`（@753560 默认对象 + @942565 无覆盖分支）⇒ profile 扫描 `~/.zcode/agents/`（官方文档已核一致）；`~/.zcode/cli/agents/` 是 `qw()` 追加 cli 的**子代理输出根**——初部署误入输出根，已迁移；机制由 OBSERVED 升格 **DOCUMENTED**（bundle+文档+磁盘三方一致），"更新免疫"降格为"当前版本行为"；② customSystemPrompt 分支跳过六节（含 Dynamic Behavior 的不可逆动作确认规则）⇒ **root 禁走该分支，只做 section 级手术**；③ "2K 静态 < 10K 工具 schema ⇒ 不动 bundle"的 token 同质性比较作废（指令优先级 ≠ 工具 schema）；④ codex 字数主张完全复现（gpt_5_2=3,509 词 / gpt-5.2-codex=1,221 词，commit a94a5db6295a @2026-09-02，脚本=空白分词，五代同型旁证 1,088/3,932）；Claude Code "500+"降格为第三方提取 OBSERVED（Piebald-AI，12,552 stars，产品组织）；ZCode skill 显式调用文档口径 = `$skill-name`（3.10.2 静态提示词仍写 `/<skill-name>`——厂商静态漂移实例）
- 决策：采纳 **D-22a**（root prompt = 一等实验面；production freeze 前完成 stock-vs-candidate 配对评估）+ **D-22b**（overlay 永不为正确性依赖：sha-gated、preimage-anchored、fail-soft、一键 restore；升级后 hash 未知 ⇒ 拒打 stock 运行；**永不冻结 ZCode 更新供养补丁**）；**sentinel-first**（P-SENT1 协议在 `tools/prompt-lab/README.md`，首次闭合 patch⇒行为因果链）；P4（GPT Cluster-B 候选）注册入候选空间与 P1/P2/P3 并列，实验裁决；已部署 profile 的 "re-read" 条款精化为条件式 re-verify
- 影响：zloop-gen8/tools/prompt-lab/（patch.py+sentinel+p4+known-builds）、两个 profile 迁移至 `~/.zcode/agents/`、v3-CANDIDATES 引用台账第四轮增补、用户解锁清单（新会话验证路径已修正）

## D-23 8–15 物理并发全景加固（2026-09-03 晨）
- 证据：① CodexSdkBackend 缺失 poll() 导致 supervisor 收集循环同步阻塞在首个 worker 的 wait() 上，8–15 并发在物理执行层面退化为单线程串行阻塞（P0-1 伪并发）；② materialize_packet 验收测试失败时未回滚 candidate commit，坏代码留在 staging_ws HEAD 上，导致后续并发 worker 发生级联毒化误杀（P0-2）；③ 8–15 并发 worker 启动时争抢 .git/index.lock 偶发 File exists（P1-1）；④ SQLite DELETE+EXTRA 刷盘耗时叠加容易超出 5s 阈值（P1-2）；⑤ 删除文件时未修剪空父目录（P0-4）。
- 决策：① CodexSdkBackend 引入 JIT ThreadPoolExecutor(16) 异步派发 + 非阻塞 poll() + wait() 硬超时中断（I34/I44 落地）；② materialize_packet 支持 rollback_on_failure=True，失败时自动 git reset --hard parent_sha + git clean -fdx，同时 _apply_delta 递归修剪空目录；③ workspace.py 的 create_worktree 与 remove_worktree 增加带抖动的指数退避重试（4次）；④ db.py busy_timeout 扩容至 30000ms；⑤ supervisor.py 增加 PENDING 任务发射微弱错峰（50ms）。测试覆盖：新增 tests/test_concurrency_fixes.py，全套 264 项测试通过。
- 影响：src/zloop/backend/codex_sdk.py、src/zloop/materialize.py、src/zloop/workspace.py、src/zloop/db.py、src/zloop/supervisor.py、tests/test_concurrency_fixes.py。

## D-23 跟进（2026-09-03 晨 II，commit d55263c；异种审计闭环后）
- 证据：GLM-5.3 异种审计证实 ee653ba 主张三项实质成立（P0-1/P0-2/P0-4）、两项机制不成立（P1-2"15 并发写 S"场景不存在——worker 不写 S，唯一写者 supervisor；P1-1 index.lock 争用本机 15 并发实验 15/15 成功零复现）；Kimi token 前缀曾入 Bash 日志（I13 违例）——跟进工作经 rollout 复核**零新泄露**（新命中均为讨论引用前缀，零字母数字延续）
- 决策：诚实限制账本恢复（4/5 逐字 + wait 条款按新语义改写）；429/RateLimit 抖动退避（≤3 次，0.5·2ⁿ+U(0.1,0.4)，池内线程执行）；真实 index.lock monkeypatch 争用测试（2 败 1 成断言）；全套 266 passed / 2 skip（GLM 独立复现 183.88s）
- 遗留（M8 探针）：429 重试在**同一 handle 上重跑 run()** 的真实 SDK 语义未验证（或需重建 turn）；thread_start/turn() 创建期 429 未覆盖；非阻塞性待 live 时序日志证实

## D-24 异种执行-审计门禁：路线修正 + 原生多 Provider 证实（2026-09-03 晨 III）
- 证据：① config.json 文件 schema（Hhr）含 **`provider` map**（kind ∈ anthropic|openai|openai-compatible + options.baseURL/apiKey/headers + per-provider models 元数据）与 `model` 键（"provider/model" 格式，main/lite/available；wnt 富化链把 provider 的 baseURL/apiKey 烘进 target）——**ZCode 原生支持多网关多 Provider，不止"单聚合网关"一途**；② 本机实证（model_usage 表）：4 provider / 5 组合已在跑——glm-5.3（af9697f5）、**gemini-3.8-flash-high 与 claude-opus-5 同属聚合 provider 3f0e0bfa**、glm-5.3-flash ×2（4680eb2a/591594e8）——异构 Provider 并存已运行多日；③ 子代理 frontmatter `model:` 经 iXo 任意透传（@10361478），spawn 走 per-launch modelRef；④ Gemini 3.8 设计书的核心事实 B（"子代理不能跨 Provider ⇒ 不同域名必须走冷宿主"）**不成立**——其路线 II 的真 niche 是 headless 物化门禁（wave 自动推进时无 root 会话），非"跨域名"；⑤ 其 c2c_runner.py 示例代码从未运行过（`urllib.request.subprocess` 不存在）且 fail-open（无 key ⇒ PASS）违反其自身宣称的 fail-closed，且未脱敏直发 diff（违反 I13/D-21，无视已有 c2c.py 脱敏机制）
- 决策：**路线 I 落地**——`zloop-auditor.md` frontmatter 已加 `model: glm-5.3`（目录中无歧义：唯一 provider），P-HET1 探针验证（新会话 spawn 后查 model_usage：auditor 子会话 provider_id ≠ gemini 根的 3f0e0bfa 即 PASS；失败即删该行回滚）；**路线 II 不建新模块**——如需 headless 门禁，做现有 c2c.py/promote c2c_gate 之上的薄适配器（复用 prepare_c2c 脱敏与包格式、S 事件、D-15 预算；fail-closed 按风险分层：HIGH/CRITICAL 超时/网络异常 ≡ REJECT 阻断晋升，NORMAL 降级同模型审查并记事件）
- 影响：zloop-auditor profile（+model 字段）、P-HET1 探针、VOL-16（c2c 通道复用而非新建）、旧 codex-loop K3 骨架（dispatch.py L804 已核：fixed pins/fail-closed/per-wave 幂等）继承为风险分层策略
- **双轨澄清（2026-09-03 晨 IV，用户质询触发）**：zloop-auditor（API 直通 GLM 子代理，D-24）与 VOL-16 的 C2C（root Browser → ChatGPT Web，P/A 分线程，含 web search 与 I41b 异种性观测）**是两条并行轨道，非替代关系**——前者 = 客户端内异构、可自动化、无 ToS/登录摩擦；后者 = 跨家族异种（GPT 家族 + 原生搜索 + 去锚定纪律），仍按 VOL-16 原设计执行。**wave 路径的 supervisor 自动审计门不存在**（supervisor.py 零 c2c/audit 调用；唯一门禁 = promote 时 c2c_gate 检查 c2c_recorded 事件）；Mode L 全自动审计 = 未来工作（D-24 的薄适配器形态），任何叙事声称其已建成均为虚构

## D-25 三重审计定形 + 执行外包（2026-09-03 晨 V，用户裁定）
- 证据：用户裁定三重审计——**C2C-P 在根对话并发前的规划编排层前置介入**（"最初、信息量最少的阶段……速度与成本都最低"；"启动才是最困难的，并发执行反而是更简单的任务"）；ChatGPT 网页端（大参数 + web search）为跨家族审计者。核实：VOL-16 §2 的 C2C-P 本就是一等公民，但实现层缺三件——① `zloop c2c prepare/record` CLI 从未接线（§1 明文接口）；② wave start 无计划门；③ promote 门不辨角色（任何 c2c_recorded 都计数）。c2c.py 库层完整（role=plan|result、D-11 线程策略、脱敏、sha256、I41b 身份过滤、≤8000 包/≤2048 digest）
- 决策：三重审计链定形——**① C2C-P 计划门**（HIGH/CRITICAL 的 wave 派发前置：需 role=plan 的 c2c_recorded；`--skip-c2c` 审计豁免）→ **② 物化机械验收**（已有）→ **③ C2C-A 角色感知门**（promote 需 role=result）。执行方案已写成零歧义施工图 `E:\zcode\zloop-D25-triple-audit-execution-plan.md`（行级坐标 + 参考实现 + 4 项测试 + 验收命令 + 剃刀红线：不新建模块/不动 c2c.py 语义/不加 supervisor 自动外呼/D-15 stub-only），**交 Gemini 施工、GLM 验收**（验收只认 git diff + 测试输出 + rollout，不认 prose）。D-24 的 zloop-auditor（GLM 子代理）为客户端内快轨，与 ChatGPT 跨家族轨并行不悖
- 影响：cli.py（c2c 子命令 + wave 计划门 + promote 角色感知）、tests/test_c2c_gate.py（4 项）、test_cli 夹具适配（HIGH/CRITICAL 追加 --skip-c2c）、预期 270 passed / 2 skipped、docs/COMMANDS.md 三重审计操作面

