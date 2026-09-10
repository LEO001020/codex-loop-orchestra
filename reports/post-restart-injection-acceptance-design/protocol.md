# 重启后注入验收协议（设计稿 v1，只读设计，未执行）

- 适用范围：Desktop 重启后的 fresh new-session 验收；区分三条注入路径 P1/P2/P3。
- 总判定：步骤 1–8 每项检查均给出机械可判定的 PASS/FAIL；整体 PASS 当且仅当第 8 步 verdict.json 中全部布尔项为 true。
- 无标题泄露约定：run_id 只存在于 LOOP root 报告路径与判定文件内，绝不进入被测会话可见路径（workspace 路径、首条消息、标题 thread_name、子代理提示词）；被测会话标题与上下文中出现 run_id 或协议标题即 FAIL。

## 判定对象（三条路径）

- P1 全局 AGENTS 注入：Codex 原生加载 `C:\Users\hzq00\.codex\AGENTS.md`（用户全局），以 `# AGENTS.md instructions` 包裹进入 developer context。该文件首行即 `# Active Codex LOOP global mode`，是 hook 注入文本的落盘副本（31,880 B，2026-08-22T22:41:23Z）。
- P2 managed hook 注入：`C:\Users\hzq00\.codex\requirements.toml`（managed，`[features] hooks=true`，`windows_managed_dir='E:\codex-LOOP\codex-loop-s-f2\hooks'`）注册 SessionStart/SubagentStart 钩子，执行 `hooks\global_loop_mode.py --component context`；该脚本仅在 marker 激活时输出 `hookSpecificOutput.additionalContext`（`instruction_text()` 于 global_loop_mode.py:64，`emit_context()` 于 :82）。注入文本含字面量 `# Active Codex LOOP global mode`、`LOOP_CONTROL_ROOT=E:\codex-LOOP\codex-loop-s-f2` 与 `<!-- source: ... -->` 两处来源注释。
- P3 角色 developer_instructions 注入：`C:\Users\hzq00\.codex\agents\{worker,verifier}.toml` 的 `developer_instructions`（各在第 30 行）在按角色生成子代理时成为其 developer/system prompt；角色 TOML 同时钉模型（worker.toml:12-13 `weiwu/deepseek-v4-flash`/ultra；verifier.toml:12-13 `weiwu/deepseek-v4-pro`/ultra）。
- 关键判别：P1 与 P2 当前注入字节级同源文本，不能靠文本内容归属；P2 靠 rollout JSONL 中的 hook 执行记录（命令串 + exit_code + stdout JSON），P1 靠 `# AGENTS.md instructions` 包裹存在性。P3 只应出现在子代理上下文，根会话必须为负。

## 常量

- ROOT = `E:\codex-LOOP\codex-loop-s-f2`；MARKER = `E:\codex-LOOP\state\global-loop-mode.json`
- WS = `C:\Users\hzq00\codex-acceptance\fresh`（固定路径，不含 run_id）
- 根会话首条消息常量（字节级固定，不得改写）：`请回复 READY`
- 子代理提示词常量：`回复 OK`
- SESS = `C:\Users\hzq00\.codex\sessions\<YYYY>\<MM>\<DD>\rollout-*.jsonl`；TITLE = `C:\Users\hzq00\.codex\session_index.jsonl` 的 `thread_name` 字段

## 步骤（每步含机械检查与 PASS/FAIL）

1. 预检基线快照（重启前后各执行一次，重启前快照存 baseline.json）
   - 对 11 个文件计算 SHA256+size：`requirements.toml`、`hooks.json`、`AGENTS.md`（用户级）、MARKER、`agents\{worker,verifier,reviewer,duty_officer,plan_expander}.toml`、`config\model_profiles.toml`、`config\global_working_agreement.md`、`AGENTS.md`（ROOT 下）。
   - 断言：MARKER 解析为 schema=`codex-loop-global-mode/v1`、active=true、control_root=ROOT；`model_profiles.toml:2` active_profile=`v4f-v4p` 且 :48-53 为 v4-flash/ultra + v4-pro/ultra。
   - 离线校验 hook 可用：`py -3 "E:\codex-LOOP\codex-loop-s-f2\hooks\global_loop_mode.py" --component context --event SessionStart`（空 stdin），stdout 必须是含 `hookSpecificOutput.additionalContext` 的 JSON；将其 additionalContext 的 SHA256 与用户级 AGENTS.md 比较（期望相等，检测 P1/P2 文本漂移）。
   - PASS：全部断言为真且哈希写入 baseline.json；任一失败即 FAIL 并终止（前置条件未满足）。

2. 中性工作区准备
   - 创建/复用 WS（空目录），递归检查 WS 树内无任何 `AGENTS.md`、无 `.codex`、无 run_id 字串（`rg -F "<run_id>"` 0 命中）。
   - PASS：三项检查均 0 命中。

3. 开 fresh Desktop 新会话（New session，不 resume），cwd=WS，只发送首条消息常量，不在会话内粘贴任何验收内容
   - 记录会话：取 TITLE 中 id 最新一条；取 SESS 中 mtime>=启动时刻的最新 rollout 文件 R。
   - PASS：R 存在且会话首条 user 消息字节等于常量（泄漏检查见步骤 6）。

4. 根会话 R 的三路径归属判定
   - P2：R 中存在含 `global_loop_mode.py --component context --event SessionStart` 的事件，该事件 exit_code=0 且输出 JSON 解析后 `hookSpecificOutput.additionalContext` 经 unescape 后包含 `LOOP_CONTROL_ROOT=` 与 ROOT 路径及 `<!-- source:`；PASS=存在且满足，FAIL=记录缺失或非 0（managed hook 链断裂：requirements.toml 注册 / marker / 脚本三选一定位）。
   - P1：R 中存在含 `# AGENTS.md instructions` 包裹且同一事件含 `Active Codex LOOP global mode` 的文本；PASS=存在，FAIL=缺失（用户全局 AGENTS 未加载）。
   - P3 负向：R 中 `You are the L2 Verifier` 与 `You are an Executor` 均为 0 命中；FAIL=出现（根会话被角色指令污染）。
   - 观测项（非阻塞）：`Active Codex LOOP global mode` 期望出现 2 次（P1 原生 + P2 hook 各 1），实际次数记入 verdict.observations。

5. 角色探针（P3 正向 + 子代理 P1/P2 + 模型钉）
   - 从根会话以 `agent_type=verifier` 与 `agent_type=worker` 各生成 1 个子代理，提示词为常量；若根会话 spawn 接口无法显式钉角色，则退回用 Desktop agent 选择器各开 1 个新会话（同为常量提示词）。生成后取对应子 rollout C_v、C_w。
   - P3：C_v 含 `You are the L2 Verifier` 且响应 payload 含 `"model":"weiwu/deepseek-v4-pro"`；C_w 含 `You are an Executor` 且响应含 `"model":"weiwu/deepseek-v4-flash"`；PASS=双双命中，FAIL=缺任一条。
   - P2 子代理：C_v/C_w 均含 `--component context --event SubagentStart` 记录且 exit_code=0；P1 子代理：均含 `# AGENTS.md instructions` 包裹；PASS=命中，FAIL=缺任一条。

6. 无标题泄露检查
   - `rg -F "<run_id>"` 与 `rg -F "重启后验收|post-restart-injection-acceptance"` 对 R、C_v、C_w 全文及 TITLE 中该会话 thread_name 执行，均须 0 命中。
   - PASS：0 命中；FAIL：任一命中（验收标识泄漏进被测会话）。

7. 重启后配置平价
   - 重算步骤 1 的 11 个文件 SHA256 并与 baseline.json 比对，须全部一致；`hooks.json` 仍为 `{"hooks":{}}`（无二次注册）；MARKER active=true；active_profile 仍为 v4f-v4p。
   - PASS：全部一致；FAIL：任一漂移（列出漂移文件）。

8. 汇总判定落盘
   - 输出 `verdict.json`：布尔项 p1_agents、p2_session_hook、p2_subagent_hook、p3_worker、p3_verifier、pin_worker、pin_verifier、no_title_leak、config_parity，及 observations（步骤 4 计数、各步证据文件路径）。
   - overall PASS 当且仅当全部布尔项为 true；控制台仅回一行结论 + verdict.json 与证据路径。

## 证据要求（每步必须留痕）

- 步骤 1/7：`baseline.json`、`parity.json`（哈希列表+断言结果），hook 离线 stdout 原文。
- 步骤 3–6：R、C_v、C_w 的绝对路径及每项 grep 的命中行号导出（`rg -n` 输出保存为 `evidence_stepN.txt`）。
- 步骤 6：TITLE 中目标会话的 `thread_name` 原值。
- 步骤 8：`verdict.json`（机器可解析）。
- 全部证据统一存于 `reports/<run_id>/` 下；run_id 为该目录名，不写入 WS 或任何被测会话可见路径。

## 已验证事实（本次只读探查，2026-08-22）

- active_profile=`v4f-v4p`，execution `weiwu/deepseek-v4-flash`/ultra、review `weiwu/deepseek-v4-pro`/ultra：`E:\codex-LOOP\codex-loop-s-f2\config\model_profiles.toml:2,48,50,52`
- managed 注册：`C:\Users\hzq00\.codex\requirements.toml:5,9,11-16,27-32`；`C:\Users\hzq00\.codex\hooks.json` 为 `{"hooks":{}}`（空注册）
- 注入器实现与 marker 门控：`E:\codex-LOOP\codex-loop-s-f2\hooks\global_loop_mode.py:64,82,94-96,145`
- marker 激活：`E:\codex-LOOP\state\global-loop-mode.json` active=true、control_root=ROOT（2026-08-22T14:41:23Z）
- 用户全局 AGENTS 为注入文本落盘副本：`C:\Users\hzq00\.codex\AGENTS.md` 首行 `# Active Codex LOOP global mode`
- 角色 TOML：`C:\Users\hzq00\.codex\agents\worker.toml:12,13,30`、`verifier.toml:12,13,30`（模型钉与 developer_instructions 行号）
- Desktop 默认子代理：`C:\Users\hzq00\.codex\config.toml:45-46` = v4-flash/ultra
- 会话证据可机械检索：`C:\Users\hzq00\.codex\sessions\2026\08\22\rollout-*.jsonl` 实测含 `AGENTS.md instructions` 与 `global_loop_mode` 字串；标题字段为 `session_index.jsonl` 的 `thread_name`（实测第 1 行）

## 假设（首次执行时用步骤 1/4 一并确认）

- Codex Desktop 将 AGENTS 包裹文本与 hook 执行记录持久化进 rollout JSONL（已实测存在，事件 schema 未钉死，故步骤 4 用“命令串 + hookSpecificOutput”双条件检索）。
- 子代理的 developer_instructions 首行原文进入子 rollout；响应 payload 的 `model` 字段可用 `"model":"<pin>"` 精确检索。
- 首条消息常量会生成稳定可预期的 thread_name；泄露检测以 run_id 0 命中为硬判据，不依赖标题生成的确定性。

## 未决不确定性

- 本 L2 调用未附带任何候选 diff/协议稿（packets/plans/reports/l2_queue 均无本任务条目），本文档为 L2 只读设计产出，非对既有候选的验证结论；是否采纳需 Sol 判定。
- 两源同文注入是否会被运行时去重合并（步骤 4 的 2 次计数）未经实测，故计数仅作观测项。
- 根会话 spawn 接口能否显式钉 `agent_type`（步骤 5 主路径 vs agent 选择器回退路径）待执行时确认。
- 步骤 1 离线执行 hook 脚本是否会触发主线程 Sol 工具门（gate 按状态拒绝），若拒绝则该断言改由子代理只读执行。
