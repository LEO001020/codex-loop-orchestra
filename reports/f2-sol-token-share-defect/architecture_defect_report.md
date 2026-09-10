# Codex LOOP F2：Sol Token 占比失控与根代理过度使用缺陷报告

报告对象：Fable / Codex LOOP F2 维护者  
报告日期：2026-08-10（Asia/Shanghai）  
严重度：**P0（调度路由错误）+ P1（预算控制缺失）**  
目标：把 Sol token 占总 token 的比例稳定控制在 **20%–25%**，其余工作由机械 L0、DeepSeek V4 Flash L1、Kimi K3 L2 承担。

## 1. 执行摘要

用户观察到：即使 F2 已配置 `worker/duty_officer = DeepSeek V4 Flash`、`verifier = Kimi K3`，实际调查、读文件、跑命令、hook 排障和统计工作仍大量由主线程 Sol 完成。

该观察成立。当前 F2 不是一个能够强制执行 20%–25% Sol 占比的闭环控制系统，而是以下三者的组合：

1. `AGENTS.md` 中的提示词纪律；
2. 若干需要主线程主动调用的 dispatch/harness 脚本；
3. 只观察 `SubagentStart`/`Stop`、但不限制根线程行为的事后 hook。

更严重的是，现有实现中有两个会机械增加 Sol 调用的硬缺陷：

- `harness/dispatch.py --mode single` 直接启动普通 `codex exec`，没有指定 worker 模型或自定义 agent；在当前根配置下，该进程就是 Sol。
- `harness/smoke_gate.sh` 虽然循环四个角色，但每轮也只是普通 `codex exec`，没有生成对应 `agent_type`；四次“角色可生成测试”实际上是四次根模型调用。

因此，当前系统即使所有 TOML 路由都正确，也无法保证 Sol 占比达到 20%–25%。在单 packet 模式或频繁 smoke/probe 的工作负载下，架构会系统性地把大量 token 记到 Sol。

## 2. 用户可见症状

本次安装和验收过程中，日志连续出现以下根会话：

- `codex-loop-f2-hook-schema-probe.log`：`model: gpt-5.6-sol`
- `codex-loop-f2-hook-empty-json-probe.log`：`model: gpt-5.6-sol`
- `codex-loop-f2-hook-command-probe.log`：`model: gpt-5.6-sol`
- `codex-loop-f2-model-update-release.log`：`model: gpt-5.6-sol`
- `codex-loop-f2-model-update-metering.log`：`model: gpt-5.6-sol`
- `codex-loop-f2-v4-max-routing.log`：根仍是 `model: gpt-5.6-sol`，只有 child 是 V4 Flash

用户看到的界面/终端标题只显示根会话模型，因此视觉上也会表现为“又在大量使用主 agent”。这并非纯 UI 假象：根会话自身确实产生了 input/output/reasoning token。

在随后准备统计 token 占比时，虽然没有生成任何子代理，所有搜索、文件读取、schema 判断和聚合脚本调试仍由当前 Sol 主线程逐步完成。从 LOOP 的设计目标看，这些可机械执行的数据处理本应属于 L0 脚本，而不应消耗多轮 Sol。

## 3. 已验证的根因

### 3.1 P0：single dispatch 实际启动根 Codex，而不是 worker

证据：`harness/dispatch.py:64-77`。

当前命令构造为：

```python
cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
       "-o", report_path, spawn_prompt(pkt, wt)]
```

问题：

- 没有 `--model weiwu/deepseek-v4-flash`；
- 没有 `--config model_reasoning_effort=ultra`；
- 没有真正的 `spawn_agent(agent_type="worker")`；
- 仅在 prompt 中写 `You are an Executor`，只能改变文字身份，不能改变实际模型路由；
- 当前全局根模型是 `gpt-5.6-sol`，因此该 `codex exec` 会以 Sol 运行。

影响：

- 所有 single packet 工作都有可能被 Sol 完整执行；
- packet 越多，Sol token 占比越高；
- `SubagentStart` 不一定产生，因为这是一个新的根 session，而不是 child agent；
- 现有 route meter 无法把它识别成“误路由 worker”，因为事件类型本身就不是 worker spawn。

这是必须优先修复的 P0 缺陷。

### 3.2 P0：smoke gate 的四角色 spawnability 是假阳性

证据：`harness/smoke_gate.sh:48-66`。

脚本循环：

```bash
for ROLE in worker reviewer verifier duty_officer; do
  "$CODEX_BIN" exec --skip-git-repo-check \
      -o /dev/null "smoke: reply exactly OK ($ROLE)"
done
```

问题：

- `$ROLE` 只进入提示文本；
- 没有传递 `agent_type`；
- 没有指定各角色模型；
- 每轮都是一个普通根 session；
- 在当前配置下，四轮都会使用 Sol。

但脚本随后输出：

```text
PASS spawnable[worker]
PASS spawnable[reviewer]
PASS spawnable[verifier]
PASS spawnable[duty_officer]
```

这个结论没有被实际命令证明。它只证明“根 Codex 能回复四次”。

额外问题：`tests/unit/test_smoke_gate.py:52-62` 使用永远返回成功的 mock Codex，并预先伪造四条符合 TOML 的 meter 记录；测试没有断言 spawn 命令包含角色或模型。因此单元测试会稳定放过该假阳性。

影响：

- 一次 smoke 至少制造四个额外根模型请求；
- 验收越频繁，Sol 占比越偏高；
- gate 可能在四个自定义角色完全不可用时仍然通过。

### 3.3 P1：Sol 的“只做规划和裁决”没有机械强制

证据：

- `AGENTS.md:23-37` 明确规定 Sol 只用于 planning/adjudication；
- `.codex/hooks.json` 只配置 `SubagentStart` 和 `Stop`；
- 没有 `UserPromptSubmit` 路由 hook；
- 没有 `PreToolUse` 根模型预算/工具闸门；
- 没有在根执行 shell、文件遍历、日志统计前要求存在 packet/授权事件。

当前结果：

- 纪律依赖主模型自行遵守；
- 主线程仍可直接调用 shell、读 500 MB rollout、调试脚本、运行测试；
- 即使这些工作是纯 if/else 或数据聚合，也不会被自动改派到 L0/L1；
- “Sol ideal round count = 2 + anomaly count”没有可执行断言。

官方 OpenAI Hooks 文档说明 `PreToolUse` 可以观察、阻断或改写工具调用；当前 F2 未利用该能力建立根模型行为边界。

### 3.4 P1：现有 metering 只检查路由，不计算 token 占比

证据：`metering/usage_reconcile.py:67-125`。

当前脚本只计算：

- dispatched 数量；
- `SubagentStart` 数量；
- 重复 meter；
- role/model 是否匹配；
- reviewer 是否多次生成；
- `cost_equivalent` 是否非零。

缺失：

- 不读取 Codex rollout 的 `token_count.last_token_usage`；
- 不聚合 input/cached input/output/reasoning/total；
- 不计算 Sol / V4 / K3 各自占比；
- 没有全历史、rolling 7d、per-wave、per-task 窗口；
- 没有 20% soft target 与 25% hard cap；
- 没有在超标后影响下一次调度。

所以当前“20%–25%”只是设计目标，不是系统不变量。

### 3.5 P1：route gate 可以复用陈旧 meter 记录

证据：`harness/smoke_gate.sh:68-84`。

route 检查只做：

```bash
grep role data/events.ndjson | tail -1 | grep pinned_model
```

它没有验证：

- 记录是否由本次 smoke 产生；
- session_id 是否等于本次测试 session；
- 时间戳是否晚于 smoke 开始；
- role spawn 是否确实成功；
- reasoning effort 是否匹配；
- 记录是 native hook 还是历史恢复记录。

因此历史上任意一条正确记录都可能让新 smoke 通过。

### 3.6 P1：配置热加载边界未进入 harness 状态机

自定义 agent 定义在任务启动时加载；磁盘更新后，已经打开的任务仍使用旧 schema。当前 F2 没有：

- agent 配置版本号；
- session 启动时记录已加载配置 hash；
- 配置变更后拒绝旧 session 继续正式 wave；
- “必须新建任务”的机械 gate。

结果是维护者会反复启动新根 session 做验证，进一步增加 Sol token。

### 3.7 P1：发布 reviewer 与根探针缺少幂等预算

虽然 `usage_reconcile.py` 能在事后报告 reviewer 多次生成，但没有在生成前用 `(wave_id, reviewer_role)` 做幂等拒绝。根 hook 探针也没有专用的无模型测试工具，导致每修一次 Stop hook 就倾向于再启动一个真实 Sol 根会话。

## 4. 责任边界

### 4.1 本次主代理执行失误

以下部分属于执行策略错误，不应全部归因于 harness：

- Stop hook 排障时启动了多个真实 Sol 根探针；
- 在已有发布审查结果后又启动过一个重复 reviewer，后被用户指出并终止；
- token 聚合调查一开始仍由 Sol 逐步读文件、试错脚本，而不是一次性落到 L0 离线程序；
- 没有在首次发现 `smoke_gate.sh` 使用普通 `codex exec` 时立即停止高层验收。

### 4.2 Harness 架构缺陷

以下问题不能靠“下次主代理更自律”解决：

- single dispatch 直接使用根模型；
- smoke gate 直接使用根模型且误报角色可生成；
- 没有 token share 计算和预算反馈；
- 没有根工具调用闸门；
- 没有 fresh-session/config-hash gate；
- 没有 reviewer 生成前幂等锁；
- 测试 mock 没验证真实模型/角色路由。

### 4.3 Codex 产品边界

官方 OpenAI 文档表明，本地 Codex 可以按直接请求、`AGENTS.md` 或技能指令委派子代理，但这种委派本身不等于“自动满足某个 token 比例”。Hooks 提供生命周期观察和 `PreToolUse` 拦截能力，但 F2 必须自行实现预算控制。Codex transcript 也不是稳定公共接口，因此解析 rollout 应有版本适配与 OpenCodex 侧交叉校验。

## 5. 建议的目标口径

必须先固定分母，否则“Sol 20%–25%”无法验收。建议同时计算三种指标：

```text
share_total(model) = model.total_tokens / all_models.total_tokens
share_effective(model) = (total_tokens - cached_input_tokens) / all_models.same
share_output(model) = model.output_tokens / all_models.output_tokens
```

主控制指标建议使用 `share_effective`，因为大量 prompt-cache 命中会让 total token 被重复缓存输入主导。`share_total` 保留作容量指标，`share_output` 观察推理/生成职责。

窗口必须至少包括：

- per task；
- per wave；
- rolling 24h；
- rolling 7d；
- F2 启用后累计。

预算规则建议：

- 目标区间：Sol effective share 20%–25%；
- soft warning：超过 20%；
- hard routing cap：超过 25% 且样本量达到最小阈值后，禁止非 planning/adjudication 的新 Sol 工作；
- reviewer 单独计入 Sol，但保留 reviewer bucket，便于区分“必要 Sol”与“根泄漏 Sol”；
- 安装/验收流量使用独立 `maintenance` bucket，不混入生产 LOOP KPI，但仍需披露。

## 6. P0 修复方案

### 6.1 修复 single dispatch

禁止以下模式继续存在：

```text
codex exec + "You are an Executor" prompt
```

可接受实现（二选一）：

1. 通过真实 multi-agent API 生成 `agent_type=worker`；或
2. 若必须使用独立 CLI 进程，则显式传递：

```text
--model weiwu/deepseek-v4-flash
--config model_reasoning_effort="ultra"
--sandbox workspace-write
```

并把 worker 的 developer instructions 作为版本化模板注入。启动后必须从新 rollout 的 `turn_context` 验证：

```text
model == weiwu/deepseek-v4-flash
effort == ultra
```

验证失败时 packet 进入 DEAD_LETTER，不得回退到根 Sol 静默执行。

### 6.2 重写 smoke gate

新的 smoke gate 不得循环四次普通根 `codex exec`。

要求：

- 只允许一个 root coordinator；
- coordinator 真实生成四个不同 `agent_type`；
- 记录四个 child agent_id；
- 从每个 child rollout 读取实际 model/effort；
- 只接受本次 session_id 且时间晚于 gate start 的 hook 记录；
- reviewer 只在 release smoke 中出现一次；普通 smoke 可只测试配置解析，不消耗 reviewer；
- gate 输出 root token 与 child token，避免验收自身把 Sol KPI 冲高却不披露。

### 6.3 修复测试

新增失败用例：

- `dispatch_single` 命令缺少 V4 model 时测试必须失败；
- `dispatch_single` effort 不是 ultra 时失败；
- smoke 中出现四次根 session 时失败；
- `spawnable[worker]` 没有对应 child agent_id 时失败；
- meter 记录不是本次 session_id 时失败；
- 仅靠预填 `events.ndjson` 不得使 smoke 通过；
- root model 执行 worker packet 时必须标记 P0 routing breach。

## 7. P1 控制面修复方案

### 7.1 增加 Sol PreToolUse 闸门

在项目 hook 中加入 `PreToolUse`：

- 当 active model 是 Sol；
- 且当前状态不是 `PLANNING`、`ADJUDICATION`、`RELEASE_FINALIZE`；
- 且工具是 shell、批量文件读取、搜索、测试、日志统计等可委派/机械操作；
- 则返回 block，并要求调度到 L0 或 worker packet。

必须提供安全逃生口：高风险事件、路由系统损坏、L1/L2 全部不可用时，可写入明确的 `sol_override` 事件；override 必须计数并进入周报。

### 7.2 将 token 计量接入状态机

新增纯离线聚合器，例如：

```text
metering/model_token_share.py
```

数据源：

1. Codex rollout 的 `turn_context.model`；
2. 每次 `token_count.last_token_usage`；
3. OpenCodex 本地请求统计/API 作交叉验证；
4. `SubagentStart` 绑定 role、agent_id、session_id；
5. wave/task ledger 绑定业务窗口。

输出：

```json
{
  "window": "wave-7",
  "tokens": {
    "sol": {},
    "deepseek_v4_flash": {},
    "kimi_k3": {}
  },
  "sol_share_total": 0.0,
  "sol_share_effective": 0.0,
  "sol_share_output": 0.0,
  "target": [0.20, 0.25],
  "status": "under|in_band|over",
  "coverage": {},
  "discrepancies": []
}
```

聚合器必须是 L0，不允许为统计本身调用任何模型。

### 7.3 配置 hash 与 session freshness

SessionStart 时记录：

- worker/reviewer/verifier/duty TOML SHA-256；
- roles.yaml SHA-256；
- active model catalog revision；
- hook hash；
- Codex CLI version。

正式 wave 开始前，若 session 记录的 hash 与磁盘不同，直接拒绝并提示创建新任务；不得在旧 schema session 中继续正式 LOOP。

### 7.4 Reviewer 幂等锁

在生成 reviewer 前原子检查：

```text
(wave_id, role=reviewer, release_revision)
```

相同 release revision 已有 reviewer 结果时不得重复生成。代码变化后产生新 revision 才允许新一轮 reviewer。

### 7.5 OpenCodex 7 天限制

OpenCodex Web 的 7 天窗口不应成为 LOOP 唯一数据源。建议本地定时导出每日聚合：

```text
data/usage/YYYY-MM-DD/model_usage.json
```

保留至少 90 天；导出过程只读 OpenCodex，不保存 API 密钥和 prompt 正文。Codex rollout 用于细粒度 role/session 归因，OpenCodex 用于代理请求总量交叉校验。

## 8. 修复后的强制验收标准

Fable 交付修复版时，必须同时满足：

1. `dispatch_single` 的真实执行模型是 V4 Flash ultra，不是 Sol。
2. smoke gate 不会为四个角色启动四个根 session。
3. 每个 role 的新鲜 child rollout 都能证明 model/effort。
4. Sol 非 planning/adjudication 的 shell/搜索/测试调用被 PreToolUse 拦截或产生显式 override。
5. token 聚合器可在零模型调用下处理全部本地 rollout。
6. 报告同时给出 total/effective/output 三种占比。
7. 支持 per-task、per-wave、24h、7d、累计窗口。
8. Sol effective share 超过 25% 会影响下一次路由，而不只是生成报告。
9. maintenance/installation token 与生产 LOOP KPI 分桶。
10. reviewer 对同一 release revision 严格单次生成。
11. 配置 hash 不一致时旧 session 不得继续正式 wave。
12. OpenCodex 与 Codex 本地统计差异超过阈值时 fail-visible。

## 9. 建议修复优先级

### P0-A（立即）

- 修复 `dispatch.py` single 模式模型路由；
- 重写 smoke gate 的角色 spawnability；
- 添加能够抓住上述两项的真实/半真实集成测试。

### P0-B（立即）

- 禁止 stale meter 让 smoke 通过；
- 每次 gate 使用独立 session_id 和临时 meter ledger。

### P1-A（下一补丁）

- 增加 model token share 离线聚合器；
- 实现 20% warning / 25% hard routing cap；
- 区分 production 与 maintenance bucket。

### P1-B（下一补丁）

- 增加 Sol `PreToolUse` gate；
- 增加 config hash/fresh-session gate；
- 增加 reviewer 幂等锁。

### P2（后续）

- OpenCodex 每日统计导出与 90 天留存；
- Codex/OpenCodex 双源差异告警；
- 可视化 rolling 7d 与 per-wave 模型占比。

## 10. 最终判断

当前 F2 已经具备“角色配置”和“模型路由观测”，但尚未具备“根模型预算控制”和“真实 worker 调度保证”。

一句话结论：

> **现有 F2 是 policy + observation，不是 enforcement；single dispatch 与 smoke gate 还会主动制造 Sol 根调用，因此在修复前无法可信承诺 Sol token 占比 20%–25%。**

在 P0 修复完成前，不建议用当前 smoke 结果宣称 LOOP 已达到成本/模型占比设计目标。此前的 179 项测试证明了状态机和脚本逻辑的广泛回归，但没有覆盖“真实 single packet 使用哪个模型”与“四角色 smoke 是否真的生成四角色”这两个关键问题。

## 11. 官方 OpenAI 参考

- Subagents：<https://learn.chatgpt.com/docs/agent-configuration/subagents>
- Hooks：<https://learn.chatgpt.com/docs/hooks>
- Codex Analytics API：<https://learn.chatgpt.com/docs/enterprise/analytics-api>

官方文档用于明确产品边界：Codex 支持按请求/AGENTS/技能进行委派，Hooks 可在生命周期和 `PreToolUse` 阶段观察或阻断；但 20%–25% 的模型 token 比例需要 LOOP 自己实现预算、计量和路由反馈控制。
