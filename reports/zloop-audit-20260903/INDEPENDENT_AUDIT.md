# ZLoop 独立架构、运行与 C2C 事故审计

日期：2026-09-03（Asia/Shanghai）  
目标工作区：`E:\zcode\zloop-gen8`  
规范库：`E:\zcode\zloop-spec`  
审计性质：只读目标代码；审计报告及隔离副本位于 LOOP 控制根。交接材料中的完成声明均按待验证线索处理。

## 1. 总结判定

当前工作树不能判定为可发布或端到端可运行。基础 Python 包与 CLI 入口可以启动，但未提交的 C2C 改动引入了 P0 级回归：每次高风险 C2C 准备都会无条件启动外部 Edge；没有标签复用、冷却、幂等锁或“已准备”去重；同时 CLI 把 `PREPARED` 当成可放行结果，在没有 `c2c_recorded` 外部审计证据时继续启动 wave 或 promotion。

这不是参考项目 `codex-with-chatgpt` 的行为。当前本机技能契约明确要求只使用 Codex 内置浏览器、一个 ChatGPT 标签全会话复用、doctor 通过后才打开网页，并在已保存会话/Project 中继续。ZLoop 当前未提交实现直接使用 `cmd.exe /c start msedge https://chatgpt.com`，与该契约正面冲突。

## 2. P0：疯狂弹出 ChatGPT 的确定因果链

直接证据：

1. `src/zloop/c2c_runner.py:34-43` 只查找 `c2c_recorded`。如果只有 `c2c_prepared`，重试不会命中去重。
2. `src/zloop/c2c_runner.py:49-58` 每次调用创建新的 C2C 包。
3. `src/zloop/c2c_runner.py:60` 声称包在 `project_dir/.zcode/c2c/<id>.json`。
4. `src/zloop/c2c.py:172-189` 实际把包写到 `project_dir/c2c/<id>.json`。返回路径错误。
5. `src/zloop/c2c_runner.py:63` 每次准备都调用 `_try_open_edge_browser`。
6. `src/zloop/c2c_runner.py:74-81` 无条件执行 `cmd.exe /c start msedge https://chatgpt.com`；参数 `packet_path` 完全未使用，异常被静默吞掉。
7. `src/zloop/c2c_runner.py:65-71` 没有等待网页审计、没有提交包、没有读取回复、没有调用 `record_c2c`，立即返回 `verdict="PREPARED"`。
8. `src/zloop/cli.py:737-747` 与 `1189-1199` 分别在高/关键风险 promotion 和 wave start 调用该 runner，并把 `PREPARED` 纳入放行集合。
9. 当前未提交 diff 删除了原先“没有 plan/result `c2c_recorded` 就阻断”的分支。因此 PREPARED 已从“待审状态”被错误提升成“门禁完成状态”。

重复机制：

`wave start / stage promote / 控制器重试` → 没有对应 `c2c_recorded` → 新建 C2C### → 启动新的 Edge ChatGPT 标签 → 立即返回 PREPARED 并放行。由于网页端从未被代码接管，也没有结果回写，该循环可无限重复。

## 3. P0：测试套件自身具有真实弹窗副作用

`tests/test_c2c_gate.py:76-89` 和 `92-105` 通过子进程执行真实 CLI 的高风险 wave/promotion，却没有在子进程中拦截浏览器启动。当前测试还把期望从 `c2c_recorded/waiver` 改为仅存在 `c2c_prepared`。因此运行全量 pytest 本身就会触发外部 Edge。

本次审计启动的隔离全测残留了 pytest/Python 进程，用户报告弹窗持续后已终止这些审计进程并停止全部子代理。随后 15 秒高频采样未捕获新的 `cmd.exe /c start msedge` 创建事件。Edge 自身仍可能创建 renderer/extension 子进程，这不等于 ZLoop 再次发出 URL 启动命令。

在修复前，不应运行当前全量测试，也不应对 HIGH/CRITICAL stage 执行 `wave start` 或 `stage promote`。

## 4. C2C 与参考实现的核心偏差

本机 `codex-with-chatgpt` 技能的硬约束：

- 每个 ChatGPT 步骤只使用 Codex 内置浏览器，禁止启动或控制 Edge/Chrome 等外部浏览器。
- 每个 Codex 会话只创建一个 ChatGPT 标签；之后只在同一标签导航，存在标签时必须 claim/reuse。
- 先执行本地 doctor；本地未绿不得打开 ChatGPT 或发送 C2C。
- 同一 workspace 复用已保存的 conversation/Project，不得每轮回到 `https://chatgpt.com/` 新建会话。
- 控制消息很小，ChatGPT 通过只读连接自行读取工作区；不是打开空白主页后等待人工复制文件。

ZLoop 未提交实现没有上述 session、tab、doctor、browser-runtime、connector、reply-wait、record-result 任一闭环。其“Web-Native”注释与实际行为不一致。

参考仓库本地 checkout 为 `C:\Users\hzq00\codex-with-chatgpt`。更新检查显示本地 `9ac01e9`、远端 `fe79d2d`，存在更新；但 checkout 有用户未提交修改（README、中文 README、OAuth 文件及 Windows 文档/脚本），`git pull --ff-only` 因防止覆盖而中止。审计没有 stash、覆盖或丢弃这些修改，因此最新远端版本的完整网页 E2E 尚未执行。

## 5. 测试证据可信度

不能接受“301+ passed、100% 绿灯”的交接声明。

- 历史执行日志中存在 `6 failed, 294 passed, 2 skipped`。
- 另一个历史日志是 `270 passed, 2 skipped`，不能证明当前工作树。
- 当前一次错误工作目录的运行收集到 293 项并出现 2 个 collection error（`ModuleNotFoundError: tests`）；该命令不等于交接指定的从项目根运行，因此只说明测试入口对 cwd/import 布局敏感。
- 本次隔离全测在弹窗事故止血时被终止；日志只有进度点，没有 pytest 总结和退出码，代理此前声称“全部通过”无机械证明，作废。
- 定向 `tests/test_materialize.py` 在独立副本中有机械结果：7 passed in 20.93s。这只能支持该七项物化测试，不支持全系统。
- `python -m build --no-isolation` 未运行成功，因为当前解释器缺少 `build` 模块；这不是运行时核心失败，但说明发布构建链未就绪。

## 6. 版本与工作树状态

Git 现场：

- 分支 `main`，HEAD `80c13ef`，相对 `origin/main` ahead 1。
- D-23 提交存在：`ee653ba`。
- C2C 网页启动、PREPARED 放行及相关测试改动位于未提交工作树，不在 HEAD 中。
- 已修改：`src/zloop/c2c_runner.py`、`src/zloop/cli.py`、`src/zloop/research/broker.py`、`src/zloop/supervisor.py`、若干测试与 egg-info。
- 未跟踪：`src/zloop/research/kimi_cli.py`、`port_discovery.py`、其测试及 `packets.json`。
- 存在 S01/P01..P08/staging 等遗留 worktree，均需在修复前核对是否仍被活动 run 引用。

因此 D-25/D-26/D-27/D-29 的完成度不能从 HEAD 重建；当前行为依赖脏工作树，交接缺少可复现版本边界。

## 7. 其他发现

- CLI 基础入口可运行：`.venv/Scripts/python.exe -m zloop.cli --help` 退出 0；`.venv/Scripts/zloop.exe --version` 输出 `0.1.0`、退出 0。“完全无法运行”应理解为端到端控制流/C2C 失效，不是 Python 包完全无法 import。
- 当前 venv 使用 Python 3.14.3，`zloop 0.1.0` 以 editable 方式指向目标仓库，已装 pytest 9.1.1 和 openai-codex 0.147.0。
- `CodexSdkBackend` 静态审计发现 `_ensure_dispatched` 的检查-再提交缺少显式锁，若同一 launch_id 被并发 poll，存在重复 submit 的 TOCTOU 风险；`shutdown(wait=False)` 也不能终止已运行 SDK 调用。该项尚未以并发机械测试复现，严重度暂定 P1/待证。
- 失败会话 `sess_e4c3756f-28cf-4e05-8a95-c52e142a86c3` 的直接首个错误是 ZCode/GLM 标题生成调用的 `AI_APICallError: Invalid JSON response`，不是交接稿所称的 PREPARED `CliError`。后续会话可能另有 PREPARED 问题，但不能用该 session ID 证明。
- `timeout_s` 在当前 `run_automated_c2c_audit` 中未使用；所谓“15 秒 fail-soft”不存在于未提交实现。

## 8. 修复顺序

### P0-A：先彻底禁止外部浏览器副作用

立即移除/禁用 `_try_open_edge_browser` 及所有 `cmd /c start msedge` 路径。修复完成前，测试中必须从进程边界拦截该调用，保证 `pytest` 永远不能打开真实浏览器。

### P0-B：恢复门禁语义

`PREPARED` 只能表示“包已生成”，不能表示 PASS。HIGH/CRITICAL 的 plan/result 门禁必须要求经过 SHA 校验的 `c2c_recorded` 且 verdict 满足策略；没有结果时保持可恢复的待审状态，而不是放行或无限重建包。

### P0-C：修复幂等与路径

同一 `(run_id, stage_id, role)` 若已有未完成 `c2c_prepared`，必须返回原 c2c_id，不得创建新包。统一 `project_dir/c2c` 与返回路径；不要保留未使用的 packet_path 参数。

### P0-D：按参考架构接管网页端

网页控制属于 Codex 根会话，而不是普通 Python runner。通过内置 browser runtime 只创建/claim 一个标签，复用保存的 conversation/Project URL；doctor 未绿不进入网页；收到 ChatGPT 回复后再显式 `record_c2c`。不得启动 Edge。

### P1：重建可信测试门

增加以下机械断言：

1. 两次调用同一 stage/role 只产生一个 prepared id，浏览器启动计数为零。
2. PREPARED 时 wave/promotion 不得进入放行终态。
3. 只有有效 `c2c_recorded` 才能放行；REJECT 必须阻断。
4. 返回 packet_path 必须存在且 SHA 与事件一致。
5. pytest 子进程不产生 `cmd/msedge`。
6. 从项目根按 README 精确命令跑全套，保存完整命令、退出码和总结；不能只报点号或代理自报。

### P1：固化版本边界

先盘点并保存当前脏工作树，明确哪些是 GLM 生成、哪些是人工改动；把每项决议绑定到提交 SHA 和测试 artifact。不要在当前状态执行 reset/clean，也不要以未提交文件宣称 D-29 已完成。

## 9. 审计证据索引

- C2C 源码审计：`reports/zloop-audit-20260903/ZA03/ZA03_audit_report.txt`
- CLI PREPARED 分支：`reports/zloop-audit-20260903/ZA02/audit_report.txt`
- 后端并发静态审计：`reports/zloop-audit-20260903/ZA04/report.txt`
- 命令历史取证：`reports/zloop-audit-20260903/ZR02/forensics_report.md`
- Git 现场取证：`reports/zloop-audit-20260903/ZR12/forensics_report.log`
- 物化定向测试：`reports/zloop-audit-20260903/ZR07/test_log.txt`
- 构建工具缺失：`reports/zloop-audit-20260903/ZR08/verification.log`
- 失败会话取证：`reports/zloop-audit-20260903/ZR11/log_summary.txt`（48 MB 原始摘录，不应注入模型上下文）
- 审计计划：`data/plans/zloop-audit-20260903/dag.json` 与 `packets/`

## 10. 审计限制

为停止真实弹窗，本次全量 pytest 被主动中止；因此没有当前工作树的可信全量绿灯。最新 `codex-with-chatgpt` 参考 checkout 因用户未提交修改未被更新。网页端没有传输项目内容、没有创建/修改 connector、没有发送 ChatGPT 消息。上述限制不会影响 P0 弹窗/C2C 假放行结论，因为这些由当前源码、Git diff、测试源码和进程行为直接支持。
