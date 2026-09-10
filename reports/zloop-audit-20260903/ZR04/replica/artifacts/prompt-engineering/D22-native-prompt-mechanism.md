# D-22 证据文档：ZCode 系统提示词机制与替换路径（2026-09-02/03）

状态：**链路全闭合，部署已执行**（zloop-worker / zloop-auditor 两个 agent profile）。
方法：对 `E:\Program Files\ZCode\resources\glm\zcode.cjs`（12,557,830 bytes，未修改）的定向反编译级探查——所有偏移可复现。
原则：不修改 bundle（本路径全部走**受支持配置**）；bundle 内部结构仅用于确认机制存在性，不进入生产依赖。

## 1. 三种提示词组装（互斥，按会话类型路由）

### 1a. 主会话（root / 桌面 / CLI 交互）— 全量原生组装
```
master build() @7740953:
  t=[are()]                                  # CLI Prefix = 单行 "You are ZCode, an interactive coding agent"
  r = config.outputStyle?.prompt ? outputStyle : undefined
  n = config.customSystemPrompt?.trim(); o = !!n
  o ? push("Custom System Prompt", n) : push(Eut(r))      # Eut = Agent Identity（身份+安全+沟通块，风格可参数化）
  if (!o) { desktop→Jut() 桌面上下文; ZSr(); Rut(envInfo); KSr(); JSr(); … }   # 全部 to-C 静态块
```
- customSystemPrompt 非空 ⇒ **跳过全部 to-C 静态块**（Desktop Context / 动态行为与沟通 / session 指引 / Memory / Environment / Context Management）。
- 喂入点 @10866586：`customSystemPrompt: this.config.systemPrompt`。

### 1b. 子代理会话（Agent 工具生成）— 精简组装（`eni` @10536440）
```
[are()]                                    # 单行身份
+ "Subagent Agent Prompt"                  # = profile 正文（.md body）
+ "Subagent Notes"（BNr：绝对路径/无 emoji/工具调用前无冒号/禁写报告文件）
+ "Subagent Environment"（cwd/git/platform/shell/OS/model）
+ AGENTS.md（若 injectAgentsMd≠false）
+ 日期 + skills 元数据
```
**子代理从不出载原生 to-C 静态块。** 子代理的系统提示词 ≈ profile 正文本身。

### 1c. workflow 子会话 — 全量组装 + customSystemPrompt 分支
- opts schema `DEn` @767539：`systemPrompt: string().min(1).optional()`（workflow agent-call 逐相位可指定）。
- 子会话默认继承 `runtimeConfig.systemPrompt` @11865968。

## 2. systemPrompt 的全部来源（穷尽：bundle 内 23 处 camel + 0 kebab + 6 snake 已全分类）

| 来源 | 用途 | 可外部设置？ |
|---|---|---|
| agent 定义文件 body | 子代理 systemPrompt（profile 加载器 @10359980：`systemPrompt: t.body.trim()`） | **是——`~/.zcode/cli/agents/*.md`** |
| workflow opts | workflow 子会话（继承或逐相位覆盖） | 是（workflow 定义文件） |
| 内置代理（Explore 等） | `systemPrompt:""` / `Dmt()`（@10357804/10358689；空时回退 `Lmt()` 工具守则） | 否 |
| MCP sampling schema | MCP 协议（@7198984 等 3 处） | 与本议题无关 |
| runtimeConfig.systemPrompt | 主会话理论字段 | **无任何入口**（见 §3） |

## 3. 主会话无入口（排除法证明）

- config.json 白名单（加载器 `MCo` @6927900）：`model, modelCatalog, modelStream, permission, storage, network, features, memory, mcp, plugins, skills, skill, command, logging, ui, toolConcurrency, modelAnomalyGuard, hooks` — **无 systemPrompt**（写入了也会被丢弃）。
- 73 个 `ZCODE_*` 环境变量：无 PROMPT/SYSTEM 相关。
- CLI 旗标：`system-prompt` kebab 0 命中。
- `outputStyle`：唯一主会话可变块——替换 `Eut` 内沟通段（部分杠杆）；来源仅插件包 `output-styles/`，无用户级目录。

## 4. profile 加载器细节（部署依据）

- 扫描根（`zun` @11895074）：`{storageRoot}/agents`（user 级 = **`~/.zcode/agents/`**，storageRoot = storage.dir，默认值 `"~/.zcode"` 见默认对象 @753560 与无覆盖分支 @942565；官方文档 subagents 页一致）+ `<repo>/.zcode/agents/`（project 级）。**勘误（第四轮）**：初版误写 `~/.zcode/cli/agents/`——那是 `qw()`（basename≠"cli" ⇒ join(e,"cli")，@11740585）追加 `cli` 后的**子代理输出根**（本机 `sess_*/agent_*` 转录即其产物）。两个 profile 已迁移至 `~/.zcode/agents/`。
- `$un`：递归收 `.md`/`.markdown`。
- frontmatter：name、description **必填**；可选 model、thoughtLevel、color、permissionMode（project 级忽略）、maxTurns、memory(user|project|local)、tools、disallowedTools、skills、mcpServers。
- 保留名：`general-purpose`、`Explore`（KPi）。
- 加载时机：bootstrap（`module:"bootstrap.subagents"`）⇒ **新会话生效**。
- 子代理工具面：profile `tools` = 白名单（`toolAllowlist`）——**同时决定工具 schema 是否注入**：不给 MCP 工具 ⇒ 26 个 computer-use MCP 工具 schema 不进该子代理请求。

## 5. 已部署（2026-09-03）

- `C:\Users\hzq00\.zcode\agents\zloop-worker.md` — 实现型（body ≈ 520 tokens；tools: Bash/Read/Edit/Write/Glob/Grep/WebFetch/WebSearch/TodoWrite）
- `C:\Users\hzq00\.zcode\agents\zloop-auditor.md` — 只读审计型（body ≈ 460 tokens；tools: Bash/Read/Glob/Grep/WebFetch/WebSearch）
- 迁移史：初版误部署于输出根 `~/.zcode/cli/agents/`（第四轮勘误，见 §4），已迁移；正文 "re-read sources" 条款已精化为条件式 re-verify（第四轮采纳）。
- 正文 = P3 家族（v3-CANDIDATES 的 P3 精简改写，子代理语境适配；BNr 已覆盖的绝对路径/无 emoji/禁报告文件不重复）。
- 回滚：删除这两个文件即可（无其他副作用；不影响既有 general-purpose/Explore）。
- 激活验证（需新会话）：新会话中 Agent 工具的 subagent_type 列表应出现 `zloop-worker`/`zloop-auditor`；生成后其系统提示词 = 单行身份 + 我们的正文 + BNr + env（无原生静态块、无 MCP schema）。

## 6. 主会话（root）的最终工程判断

- 原生 to-C 静态文本仅存在于**主会话**，量级 ~1.5–2K tokens（Part A ≈ 1,100 词）。
- 真正的"鲸"是**工具 schema**：computer-use 插件 26 个 MCP 工具（每个请求都注入，主会话与未限工具的子代理都付）；其次 skills 元数据。直接观测估计 10K+ tokens，≈ 静态文本的 5–10 倍。
- 结论：root 的提示词税大头不在静态文本。可执行的受支持动作是**插件/skill 卫生**（Settings 关闭 computer-use 等按需重开）；bundle 编辑仅省 ~2K tokens/回合，且引入对 12.5MB 压缩产物的生产依赖——按"无充分收益勿增风险"剃刀，**不执行**（procedure 保留在 v3-CANDIDATES 部署教义，作为显式可选项）。
- ZLoop 语义：worker 认知（外部 CLI + 子代理 fan）从不载原生静态块——"开源"恐惧实测仅剩 root 一个会话，且量级以工具 schema 为主。

## 7. 第四轮修订（2026-09-03 凌晨 II：GPT 审计 × 独立一手复核）

**勘误 2（论证撤回）**：§6 用 "2K 静态文本 < 10K+ 工具 schema ⇒ 不动 bundle" ——token 同质性比较不成立。系统提示词是最高优先级全局行为约束；工具 schema 是候选行动接口（OpenAI Instruction Hierarchy 页已核：system prompt 与不可信文本同优先级正是注入攻击根因）。§6 的"生产依赖风险"论证保留；"收益量级"论证作废，改由 D-22a 实验裁决。§6 结论被以下双支柱取代。

**D-22a（采纳）**：root system prompt = 一等可优化实验 harness 面。production freeze 前必须完成 stock-vs-candidate 真实配对评估（2×2 Stock/P4 × Full/Pruned tools → 上下文压力轴 32K-256K → 条款级 ablation + return 实验；私有配对语料；task-blocked 随机化 + paired bootstrap/Wilcoxon；预注册主终点）。

**D-22b（采纳）**：root prompt 定制在依赖私有 bundle 结构期间**永不成为 ZLoop 正确性依赖**——patch 失败/未知 hash/验证失败 ⇒ stock ZCode 运行，ZLoop 全功能。落地：`zloop-gen8/tools/prompt-lab/`（sha256-gated：known-builds.json 唯一锁定 35971604…cbabc；preimage-anchored：锚点 count==1 断言；node --check；原子替换；一键 restore；sentinel 候选就绪）。

**customSystemPrompt 分支风险坐实（bundle 证据）**：`!o` 守卫跳过六节 = Agent Identity（Eut）、Desktop Context（Jut，desktop-only）、**Dynamic Behavior（ZSr——含不可逆/外向动作确认规则）**、Environment Info（Rut）、Output Style（KSr）、Context Management（JSr）。root 走该分支会连行为承重节一起删——**root 只做 section 级手术，禁走 customSystemPrompt 全覆盖**。

**sentinel-first（P-SENT1）**：任何真候选之前先跑 sentinel 探针（tools/prompt-lab/README.md 协议）——首次证明 bundle patch ⇒ fresh root 行为的因果闭环（此前从未完成；此前所有"可修改"结论仅为 OBSERVED 可写性）。
