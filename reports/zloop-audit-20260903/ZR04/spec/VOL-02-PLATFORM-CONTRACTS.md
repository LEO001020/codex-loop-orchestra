# VOL-02 — 平台契约基线（2026-09-02 真机审计快照）

> **ZLoop Spec v1.0** · 卷 02/22 · 层级 L1 · 依赖：VOL-00
> 来源：2026-09-02 独立审计（GLM-5.3 @ ZCode 3.10.2 本机实测 + 官方文档/源码核验 + 8 个研究 agent）。
> **使用规则**：本卷是开工时的已知事实基线，不是永久 API。M0 每条都要用 VOL-20 对应 probe 重新确认；
> 任何一条被证伪 ⇒ 更新本卷 + DECISIONS.md 记录 + 触发对应 fallback。

---

## 1. ZCode 客户端（v3.10.2，本机 `ZCODE_APP_VERSION=3.10.2`，build `35824adf`）

### 1.1 Hook 协议 [DOCUMENTED]

| 事实 | 值 | 再确认 probe |
|---|---|---|
| 事件 | **7 个**：`SessionStart, UserPromptSubmit, PreToolUse, PermissionRequest, PostToolUse, PostToolUseFailure, Stop`（无 SessionEnd；`Notification/SubagentStop/PreCompact` 不支持） | P-HK2 |
| 输入 | stdin **单行 JSON**（camelCase + snake_case 双命名）。通用字段：`session_id, transcript_path, cwd, permission_mode, hook_event_name`；SessionStart +`source`(+`agent_type/model`)；UserPromptSubmit +`prompt`；PreToolUse +`tool_name, tool_input, tool_use_id`；PostToolUse +**fully structured `tool_response`**（含 name/input/call ID）；PostToolUseFailure +`error, is_interrupt`；Stop +`stop_hook_active, last_assistant_message`。**`transcript_path` 是临时文件，hook 运行后目录即被清理**——所需内容当场持久化（`ZCODE_PLUGIN_DATA`） | P-HK2 |
| source 值 | `startup / resume / clear / compact`（**含 resume**——Gen-8 只列了 3 个） | P-HK2 |
| matcher | 缺省/空/`*` 匹配全部；纯 `[A-Za-z0-9_|]` = 精确名单（如 `Write|Edit`）；含其他字符 = JS 正则（非法则跳过并记诊断）；tool 事件匹配工具名（别名 `Task↔Agent`、`Write/Edit←ApplyPatch`）；SessionStart 匹配 source（例 `"startup|clear|compact"`）；**UserPromptSubmit/Stop 不做 matcher 过滤** | P-HK2 |
| 输出 | stdout JSON：`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"…"}}`（顶层 `additionalContext` 亦接受）；PreToolUse 决策 `permissionDecision:allow/deny/ask` + `permissionDecisionReason` + `updatedInput`（整体替换、按工具 schema 重校验）；Stop 续跑 `{"decision":"block","reason":"…"}`（续跑**连续 3 次后强制结束**）；exit 0=成功、2=阻塞/续跑、其他=可恢复失败（turn 不崩）。**schema 宽严两源不一致**：官网"未知字段被忽略，已知字段类型错/事件名不匹配才失败"；本机 zcode-guide 插件文档称"多余 key 即校验失败"——实现按保守写法（只发已文档字段），P-HK2 裁决 | P-HK2 |
| 执行 | **inline 串行为主**；`process` 型 = argv 无 shell、**仅同步**；`command` 型走 shell。**`async` 两源不一致**：官网称 `async:true` = fire-and-forget（后台 stdout 不能阻塞/改输入/注入上下文），本机插件文档称无运行时效果——v1 一律按同步微型进程实现，P-HK2 裁决。timeout 解析 `timeoutMs → timeout×1000 → 配置 timeoutMs → 默认 60000ms` | P-HK2 |
| 配置 | 用户级 `~/.zcode/cli/config.json` 的 `hooks`（须 `enabled:true`，否则默认禁用）或 plugin `hooks/hooks.json`（任一 plugin hook 存在则 runner 自动启用）。**workspace 级 `.zcode/config.json` 的 hooks 被整体忽略**（`config_project_hooks_ignored`）[P1-6]。默认 `timeoutMs=60000`、**`maxOutputBytes=32768`（hook 自身 stdout 上限）**；配置按 session 快照，改动需新 session 生效 | P-HK2 |
| 模板变量 | 官网页面只列 `${ZCODE_PLUGIN_ROOT/DATA/ID/NAME}` + 兼容 `${CLAUDE_PLUGIN_ROOT/DATA}`；**`${CLAUDE_SESSION_ID}` 仅见于本机 zcode-guide 插件文档、官网未列——两源不一致** ⇒ 稳健实现一律读 stdin JSON 的 `session_id`，不依赖模板变量 | P-HK2 |
| hooks 全局设置 | `hooks.enabled / timeoutMs / maxOutputBytes` [P2-13 关联：marker 截断安全] | P-HK2 |

来源：zcode.z.ai/en/docs/hooks；本机官方插件 `zcode-guide/diagnosing-hooks`（0.1.4）。

### 1.2 Goal 模式 [DOCUMENTED]

`/goal <objective>`：按 round 工作；每轮结束自动检查目标是否达成，未达成则开下一轮；verifier 只认真实证据（changed files、command output、test results；plans/听起来确定的回答不算）；**goal state 由系统持久化，重开 session 仍在**；子命令 pause/resume/clear/replace（**pause 无损**：rounds/tool 历史/产出文件保留；任务运行中不能设 goal；停止运行中的任务会自动 pause goal）。verifier 的 todo gate：任何未完成 todo ⇒ 不判完成；每 round 标题来自上一轮 verification 产生的下一步；round 结束后由独立检查决定完成与否。
未保证（Gen-8 谨慎正确）：外部冷 supervisor 阻塞几十分钟后**回到同一 round** 继续推理——无此承诺。⇒ G-COG 不得押注同轮返回（见 VOL-03 §3）。

### 1.3 上下文管理 [DOCUMENTED + 实测]

自动 compaction 存在且不给模型侧开关：触发 ≈ 窗口 − 21K 输出保留 − ~13K 缓冲（128K→~94K；1M→~966K）。**模型侧无 remaining-token/PreCompact/new_context 信号**（root 工具面自证 2026-09-02）。手动 `/compact` 存在。⇒ VOL-06 的 H1 piggyback 设计成立；不得声称达到 Codex TokenBudget 的 native rollover。

### 1.4 子代理与工具面 [DOCUMENTED + 实测]

Agent 工具：foreground 并行（官方仅承诺 "several"，无数值上限）、background（主任务不等、可先结束当前 turn；**backgrounded Explore 子代理只读**）、isolated context、结果自行回到主对话；**子代理不能再派子代理（native 层已禁嵌套）**；定义文件 frontmatter：`maxTurns/tools/model/…`。本机存在旋钮：`CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION`、`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`、`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` [P2-15 → P-NAT1]。
Browser：root 的模型工具、**main-agent-only**；独立登录 session、不共享日常 Chrome；**Windows 暂不支持导入 Chrome 登录态**。⇒ C2C transport 现实（VOL-16）。
computer-use MCP 亦为 root 工具面。H0 对子代理只能 `native_child_result_only / native_child_surface_observed` 覆盖标注。

## 2. ZCode agent 侧（root 运行环境）[OBSERVED/DOCUMENTED 于 2026-09-02 本 session]

| 事实 | 值 | 影响 |
|---|---|---|
| 前台 Bash 超时 | 单调用硬上限 **600,000ms（10 分钟）**，默认 120,000ms | `zloop wave await` 前台有效域 <10min [P0-2] |
| 后台任务 | `run_in_background` 横跨 turn 存活；退出时 **re-invoke root**（本 session 实测：`sleep 90` → task-notification 独立 turn 到达，无需用户输入） | G-COG-B wake 原语 DOCUMENTED+OBSERVED [P0-2] |
| 通知投递时点 | 在 turn 边界（本次实测：在途工具批次完成后作为独立 turn 到达，不打断进行中的 turn） | 串行综合在轮边界吸收并行结果——与架构吻合；M0 精确计时 |
| 结果取回 | TaskOutput 工具（block/timeout） | await 消费路径 |
| 并行工具调用 | 一个 assistant message 可发多个工具调用 | 并发 hook 竞态须测 [P2-14] |
| 环境变量 | **无任何 session-id env**（全量 env 名实测）；有 `ZCODE_APP_VERSION/BASE_URL/BUILD_COMMIT_ID/PROCESS_LABEL` 等；敏感名存在：`ALIBABA_TOKEN_PLAN_API_KEY`、`ZAI_OAUTH_CLIENT_ID`、`ZAI_BUSINESS_BASE_URL` | bind-token 前提成立 [I32]；子进程环境必须 allowlist [P1-8] |
| headless 会话 | **ZCode 安装目录（Electron：app.asar + resources）无独立 headless CLI 二进制**（实测 2026-09-02，仅 ZCode.exe + rg/ugrep 工具） | hook 实弹验证（P-HK1/2/3、P-BIND1、P-GC1）需要**新交互 session**（本 session 的 hook 配置已快照为空）；实现按 D-3 保守契约，首次实弹验证发生在用户下一个新 session |

## 3. 本机部署矩阵 [实测 2026-09-02]

| 组件 | 本机 | 最新（当天核实） | 备注 |
|---|---|---|---|
| OS | Windows 10.0.26200 x64（MINGW64/Git Bash 3.6.9） | — | Windows-first |
| ZCode | 3.10.2 (35824adf) | 3.10.2 | desktop |
| Python | 3.14.3 | — | **stdlib sqlite 3.50.4** |
| SQLite (runtime) | **3.50.4** | 3.53.4 (2026-07-24) | **落在 WAL-reset 区间且 < 3.50.7 backport ⇒ I22 激活** [P0-1] |
| codex-cli | 0.147.0 | rust-v0.152.1 (2026-09-01) | 差 5 版本；M0 pin 并记录 |
| kimi | 0.28.1 | 0.40.0 (npm @moonshot-ai/kimi-code) | 0.27.0=缺陷版 #1897；修复 PR #2170 开放 ⇒ **视为未修复直到实测** |
| git | 2.55.0.windows.3 | — | — |
| zloop | 未安装 | — | 干净起点 |
| ~/.zcode/cli/config.json | **不存在** | — | 无任何用户级 ZCode hook 注册 |

### 3.5 机器卫生（M0 前置）[实测]

- **`C:\ProgramData\OpenAI\Codex\requirements.toml` 存在** = 旧 LOOP 机器级 Codex hook 镜像注册（global_loop_mode.py 等，含硬编码 `E:\codex-LOOP` 路径）。ZLoop 的 first-class worker 是 Codex 进程 ⇒ 会被旧钩子命中。**清点下线是 M0 硬前置** [P0-3 → P-HYG1]。
  - 执行期实测（2026-09-02 19:18）：内容确认 = SessionStart(context+lifecycle)、SubagentStart/SubagentStop、**PreToolUse gate 家族**（对 Bash/read/search/mcp 的宽 matcher gate + spawn-gate）、Stop，全部指向 `E:\codex-LOOP\codex-loop-s-f2\hooks\global_loop_mode.py`；sha256 `6c0f4f7b…`；已备份至 `~/.zloop/hygiene-backup/requirements.toml.20260902-191845.bak`。
  - **新发现**：该文件头声明支持 `CODEX_LOOP_REQUIREMENTS_TOML` 环境变量覆盖加载路径 ⇒ worker 进程可用空 requirements 文件隔离旧 hook，**无需改动系统文件**（M7 前默认缓解；实际删除仍需用户确认）。
- `~/.codex` 下 5 个 auth.json 变体（`expired-20260831`、`wrong-account-backup`、`pre-ws-convert`、`chatgpt-backup`、`.bak.20260731`）+ `config.toml.bak-glm53f`：禁止作为凭据来源；割接时轮换/清理（VOL-17 §6）。
  - **勘误（2026-09-02 深夜，D-14）**：19:20 的“登录态损坏”判断只适用于 ChatGPT-membership 路由。用户实际使用 **opencodex 包装**（npm `@bitkyc08/opencodex` 2.39.0；官方 codex 已改名 `codex.opencodex-real`）+ `model_provider="cliproxy"` 自建代理。`auth.json` 的 access/refresh token 完好，仅 `id_token` 为空串（故官方 `login status` 报错）。**openai-codex SDK 经此路由实测 PASS**：thread_start(cwd,sandbox) → turn → `TurnStatus.completed` + final_response（14.8s）。⇒ P-CDX1/P-CDX2 转 PASS（workspace_write 下公网 canary 000/exit 1；agents 禁用后目录无 spawn_agent 族，但含 provider 侧 `web_search`）；P-CDX3 留待 M7 流事件。
- 旧树 `E:\codex-LOOP`（quarantine/worktrees/c2c/guards…）从此只读归档。

## 4. Codex / OpenAI [DOCUMENTED/源码核实]

| 事实 | 值 |
|---|---|
| Python SDK | PyPI **`openai-codex`**（0.147.0, 2026-08-18；module `openai_codex`；Python ≥3.10；附带 pinned `openai-codex-cli-bin`）。`Codex(config)/AsyncCodex(config)`，**默认复用现有 CLI 登录**；显式流 `login_api_key/login_chatgpt/login_chatgpt_device_code`。`thread_start(*, approval_mode=auto_review, base_instructions, config, cwd, developer_instructions, ephemeral, model, model_provider, personality, sandbox)→Thread`；`thread_resume(thread_id, *…)`（无 ephemeral）；`Thread.read(*, include_turns=False)`；`thread_list(*, archived, cursor, cwd, limit, …)`。`turn(input, *, cwd, sandbox, model, effort, …)→TurnHandle`：`steer(input)/interrupt()/stream()→Iterator[Notification]/run()→TurnResult`。`Sandbox.read_only/.workspace_write/.full_access`。`TurnResult{status, error, final_response: str\|None, items, usage}`——**`final_response` 可为 None（turn 无 final-answer 时合法终止）**，完成以 `turn/completed` 通知为准。单 client 多活跃 turn，无文档上限 |
| 嵌套 agent | config `[agents].enabled`（默认 **true**）可关；`agents.max_concurrent_threads_per_session`；`features.multi_agent` 门控 `spawn_agent/send_input/resume_agent/wait_agent/close_agent` |
| app-server | `codex app-server`：stdio JSON-RPC；`generate-json-schema --out DIR`（注意是 **DIR**）；WS transport experimental/unsupported |
| sandbox | `workspace_write`：写限 cwd+writable_roots；**`network_access` 默认 `false`（codex-rs 源码）**——比 Gen-8 预期强，仍须物理 canary 实测 |
| TokenBudget | codex-rs `session/token_budget.rs`、`compact_token_budget.rs`；PR #29743（fresh window 不 summarize）；PR #39827 history/notes tools（2026-08-21）——"进入 context manager" 措辞宽松（实为 context fragments） |
| 已知故障 issue | **#37047**（thread stale-active → resume 永久挂起）；**#34220**（app-server 重启后 Completed 子代状态丢失 → wait timeout）；**#37856**（多窗口争同一 active thread）⇒ "provider status 不是 S authority" 的实证依据 |
| Symphony | SPEC：单一权威 orchestrator 状态；每 issue 独立 workspace；launch 前 validate `cwd == workspace_path` |

- **P-SEC1 实测（2026-09-02，哨兵探针，D-17）**：`workspace_write` **不限制读**（SDK 文档明示 "permits reading files"；两个哨兵文件——C 盘根与用户 Profile——均被逐字读回）；**公网被拒但 loopback 可达**（127.0.0.1:8765 canary 被完整取回）。跨 plane 升级路径在本机为真：worker → 读 `~/.kimi-code/server.token` → 控制 loopback Kimi server（session/fs/shell）。缓解：supervisor 在 Kimi server 存活时拒绝开 wave；M7 真实负载 gate 要求 worker 专用低权限 OS 身份/边界。

## 5. Kimi Code [DOCUMENTED]

- 双仓库：`MoonshotAI/kimi-code`（TS，npm `@moonshot-ai/kimi-code`，当前 0.40.0）；旧 `kimi-cli`（Python 1.50.0，收尾中）。
- `kimi web`：本地 REST+WS（默认 `127.0.0.1:58627`，loopback，端口冲突重试 ≤100）；鉴权 `Authorization: Bearer <token>`（WS 同 header 或子协议 `kimi-code.bearer.<token>`；401=envelope `40101`；防爆破 60s 内 10 次失败封禁 → `42901`）。**live `/openapi.json` `/asyncapi.json` 为准**（需鉴权）。`POST /api/v1/sessions`：`workspace_id` XOR `metadata.cwd`（同给须一致；`agent_config` 收但不生效——模型经 profile 端点设）。终止态：turn `last_turn_reason ∈ completed/cancelled/failed`；transcript agent turn `state ∈ queued/running/completed/failed/cancelled`。WS `/api/v1/ws`（先 `server_hello`；`subscribe/subscribe_v2`；事件族 `turn.started/ended`、`tool.call.*`、`assistant.delta`、`event.approval.*`；落后 >1000 事件 → `resync_required` → snapshot 端点）。中止：`POST …/sessions/{id}:abort`、`…/prompts/{id}:abort`、`…/tasks/{id}:cancel`。官方 **experimental**。
- Headless：`kimi -p "…" --output-format stream-json`：stdout=JSONL（每行一 JSON；tool call 序列 = 带 `tool_calls` 的 Assistant → Tool message → 后续 Assistant）；thinking 永不写 JSONL；**文档未定义显式 final marker 与 per-line `type` 枚举** ⇒ K2 completion 判据 = 末行是非 tool-call 的 Assistant message（缺失 ⇒ `INCOMPLETE_OUTPUT`，I27）。
- **P-KIM1 实测（2026-09-02）**：K1 server 全链路 PASS（healthz 免鉴权；openapi 63 paths，sha256 `12d0a5e2…`；`metadata.cwd` 建会话 5.7s；`last_turn_reason=completed` 4–10s；messages 恢复返回 final assistant；`:abort` code 0）。**契约发现**：token 持久化于 `~/.kimi-code/server.token` 且跨实例共享（VOL-15 "仅内存"假设**错误**）；端口占用时漂移 58627→58628；**建会话时 `agent_config.model` 被静默丢弃**（模型必须经 profile 端点设置）；HTTP 200 可携带 `code!=0`。K2 CLI：配额耗尽 = rc 1 + 空 stdout + stderr `provider.api_error 403`（与 #1897 可区分）；**stream-json 末行是 `meta:session.resume_hint` 而非 assistant message**——completion 判据必须跳过尾部 meta 行（I27 实测修正）。K1/K2 **同账号配额** ⇒ 双 lane 无互备价值（D-10：仅实现 K1）。
- **M4 实现期实测（2026-09-02 晚，K1 lane 实连验证）**：prompt 端点 `POST /api/v1/sessions/{id}/prompts`，body 为 `{"content":[{"type":"text","text":…}]}`——**content 是类型化分片数组**，纯字符串不合 schema；**默认 profile 不可用**：必须 `POST /api/v1/sessions/{id}/profile` 设模型，否则 turn 失败 `model.not_configured`；`last_turn_reason` 在 `GET /api/v1/sessions/{id}`（非 `/status`）；session id = `data.id`，messages = `data.items`；`:abort` 未列入 openapi 但实测可用；配额耗尽表现为 turn `failed` 且无 assistant 消息（broker 据此标 `source_unverified`，与 #1897 可区分）。
- 内建工具 `WebSearch`/`FetchURL`；其支撑是 `[services.moonshot_search]` / `[services.moonshot_fetch]` **TOML 配置段**（勘误：不是工具）[P2-12]；managed OAuth login 自动配置 search/fetch。
- Headless：`kimi -p "..." --output-format stream-json`。
- **Issue #1897**（0.27.0）：长多工具 stream-json turn 在 stdout backpressure 下丢 final assistant + resume hint，而 wire.jsonl 实际完整 ⇒ `exit 0 ≠ success`、`tool event ≠ success`，必须显式 terminal completion（I27）。
- 模型：K3 旗舰（2026-07-16）、K2.6、K2.7 Code、`kimi-for-coding(+highspeed)`。

## 6. SQLite [DOCUMENTED, sqlite.org]

- **WAL-reset bug**：2026-03-03 发现（官方 wal.html §11）；"likely present" 3.7.0–3.51.2；修复 **3.51.3**（2026-03-13）；backport **3.50.7 / 3.44.6**；触发条件 = 多连接 + 同时 write/checkpoint；官方严重度 "not an emergency"（发生率类比 SSD 故障）——罕见但非零，对 correctness-critical S 上闸门正确。3.52.0 撤回，3.53.0 带修复重发；当前最新 **3.53.4**。
- WAL 要求所有进程同主机（共享内存）："WAL does not work over a network filesystem"。
- `synchronous`：WAL+FULL = 每次 commit 都 sync WAL（掉电不丢已提交事务）；NORMAL = 掉电可能回滚最近事务但不损坏。

## 7. 旧 LOOP 代码树事实（Phase -1 提取的地面真相）[实测]

**真实存在**（KEEP 行为契约的来源）：`harness/root_turn_governor.py`、`sol_tool_gate{,_v2,_router}.py`、`metering/model_token_share{,_v2}.py`+`budget_controller.py`、refill debt/low-water(45/15)/borrowable reservations/duty queue/L1-L2/manual status、`desktop_edge_reconcile.py`、双重 hook 注册（global_hooks.json + ProgramData 镜像）、advisory locks、`os.replace`、torn-line skip、mtime staleness、one packet=one worktree=one branch、attempts ledger、`provider_health.py`（SearchHealth≠InferenceHealth 旧实现）。
**幻影/错位**（P0-4，提取表不得引用）：vector DB/RAG manager（零匹配）；Blackboard/DAG dump（实为 emit_context 注入 working agreement，64KB 上限）；Compatibility Gateway（已退役外部 OpenCodex sidecar，非本树）；IPybox memory authority（实为大输出消化 lazy kernel；README 明确 files 是 source of truth）。旧树**无** `prev_event_hash` 链。
无明文凭据（SECRETS-GATE 8 类 0 hits）；硬编码机器路径 60+55 处。AI 审计包：`E:\codex-LOOP\deliveries\codex-loop-ai-audit-20260901`（16 文档）。

## 8. 已核实研究事实（引用时无需重查；数字已对原始来源逐字核验）

| 来源 | 关键数字 |
|---|---|
| PRO-LONG (arXiv 2607.20064) | 结构化交互日志+程序化检索：ARC-AGI-3 平均 +18.0pp、最高 76.1% pass@1、token 4.2–5.8x 更少 |
| Anthropic Managed Agents (2026-04-08) | session=append-only durable log、getEvents() 按位置重查、存储与 context engineering 分离 |
| Anthropic multi-agent (2025-06-13) | +90.2% vs 单 Opus 4；~15x tokens；token 用量解释 80% BrowseComp 方差 |
| PoLL (arXiv 2404.18796) | 跨家族 panel 降低 intra-model bias、优于单 judge、7–8x 更便宜 |
| Correlated Errors (arXiv 2506.07962, ICML 2025) | 350+ LLM：更大更准的模型错误高度相关（跨架构/厂商）；同错时 60% 一致 |
| OrchBench (arXiv 2607.25656) | task-critical 信息保留 > 增加 agent；16→64 agent 收益近零 |
| Anthropic C compiler (2026-02-05) | 16 agent 全堵同一 blocker；known-good oracle（GCC）解锁真正并行 |
| GPT-5.6 Luna（官方模型页） | cost-sensitive/high-volume；1,050,000 context；web_search 支持；$0.20/$1.20 per MTok；**membership/Codex 路由可用性未核实 ⇒ entitlement canary 必做** |
| SKILL.state (arXiv 2608.26263) / Same Model, Different Harness (2608.26218) / Harness-Bench (2605.27922) / LongMemEval-V2 (2605.12493) / CodeAct (2402.01030) | 均核实存在且主题匹配 |
| ⚠️ RLM (arXiv 2512.24601) | 实为 "Recursive Language Models"（推理期长提示递归分解）——**引用错位，不得作为 persistent-runtime 依据** [P2-12] |
