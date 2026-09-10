# PROGRESS — ZLoop 执行状态（活文档：每完成一个可验证单元立即更新）

> 规则见 VOL-22 §8。这是跨会话的唯一进度记忆——不依赖模型回忆。

## 当前阶段

**Live 任务闭环与 Metrics 运维子系统就绪（2026-09-03 晨 VII）**：断开会话故障排查与 metrics 业务闭环落地——① 根因追溯：排查 `sess_01162d8d-ce5d-4f16-94ea-a435156ecb6c` 数据库底层，证实因 4 个并发 worker 产生逾 100 万 tokens 集中回流至主会话（31,811 tokens 输入），模型思考 54 tokens 后触发空回复（0 output tokens）停机；② 在 `~/.zcode/agents/zloop-worker.md` 中加固了 Reporting 强制文本输出契约（严格禁止 0 token / 空消息退出）；③ 补齐并完善 `src/zloop/metrics/` 全部 4 个模块（tokens, latency, concurrency, c2c_stats）及其配套的 23 项高覆盖率单测（全部绿灯）；④ 全套 **293 测试通过 / 2 skip**（由 270 增长至 293 项，测试集零失败，提交 `b57cd48`）。

**D-25（三重审计接线完成，用户架构裁定落地）（2026-09-03 晨 VI）**：三重审计规划编排前置介入落地——① `cli.py` 接线 `zloop c2c prepare / record` 子命令（VOL-16 §1），支持从文件或 stdin 读取，输出规范 bounded digest 与结构化脱敏元数据；② `_wave_start` 前置计划门（HIGH/CRITICAL 强制要求已记录 role=plan 的 `c2c_recorded` 事件，否则阻塞在 PLANNING 状态禁止派发；支持显式 `--skip-c2c` 并记录 `c2c_waiver` 事件且带 `gate=wave_start_plan`）；③ `stage promote` 门升级为角色感知（必须有 role=result 的 `c2c_recorded` 记录，plan 角色不再能混充通过，保留 `c2c_gate_required` 断言字串）；④ `tests/test_cli.py` 夹具 `_run_mock_wave` 适配 `--skip-c2c`；⑤ 新增 `tests/test_c2c_gate.py`（4 个测试用例：往返与 I41b 身份过滤、计划门阻断与解锁、role=result 角色感知、豁免审计事件）。全套 **270 测试通过 / 2 skip**（新增 4 项，零回归）。证据链：决策 D-25；施工计划书 `zloop-D25-triple-audit-execution-plan.md`。

**D-23（8–15 物理并发加固）完成（2026-09-03 晨）**：针对 8–15 真实并发负载的 5 项物理缺陷闭环落地——① `CodexSdkBackend` 引入 JIT `ThreadPoolExecutor(16)` 异步派发 + 非阻塞 `poll()`（通过 `future.done()` 毫秒级反馈）+ `wait(timeout)` 硬超时中断，彻底消除 supervisor 收集循环同步阻塞在单 worker 上的假并发（P0-1）；② `materialize_packet` 增加原子回滚保障（`rollback_on_failure=True`），验收测试失败时自动调用 `git reset --hard parent_sha` + `git clean -fdx` 彻底清除坏代码，杜绝公共 Staging 分支被坏提交污染而引发级联雪崩（P0-2）；③ `_apply_delta` 递归向上修剪空父目录（P0-4）；④ `create_worktree` 与 `remove_worktree` 增加针对 Git `index.lock` 与 Windows 句柄延迟释放的 4 次指数退避重试（P1-1/P1-3）；⑤ SQLite `busy_timeout` 扩容至 30000ms（P1-2）；⑥ supervisor 增加微弱启动错峰（P1-4）。全套 **266 测试通过 / 2 skip**（新增 `tests/test_concurrency_fixes.py` 6 项；`d55263c` 跟进：诚实账本恢复 + 429 退避 + 真争用测试，经 GLM 异种审计闭环并独立复现）。证据链：决策 D-23 + D-23 跟进。

**D-22（系统提示词替换，用户 P0）完成 + 第四轮勘误（2026-09-03 凌晨 II）**：机制全链闭合——① ZCode 子代理**从不出载**原生 to-C 静态块，其系统提示词 = agent 定义文件正文（`~/.zcode/agents/*.md`，**机制 DOCUMENTED**：官方文档+bundle storage.dir 默认+磁盘三方一致；**勘误**：初版误部署于输出根 `~/.zcode/cli/agents/`，已迁移）；② 已部署 `zloop-worker.md`（实现型，~520 tok，tools 白名单 9 项）与 `zloop-auditor.md`（只读型，~460 tok，6 项）——tools 白名单同时切断 26 个 computer-use MCP 工具 schema 注入；"re-read" 条款已精化为条件式 re-verify；③ 主会话无任何 systemPrompt 入口（config 白名单/env/CLI 旗标全排除）；④ GPT 第四轮审计的 14 项引用经独立一手复核：10 全验证 / 3 部分 / 1 未验证，我方两项旧主张一复现（codex 1,221/3,509 词）一降级（Claude Code 500+ = 第三方提取）；⑤ 采纳 D-22a（root prompt = 一等实验面，freeze 前配对评估）+ D-22b（overlay 永不为正确性依赖，fail-soft）——customSystemPrompt 分支坐实跳过六节（含不可逆动作确认规则）⇒ root 只做 section 级手术；sentinel-first 协议落地 `tools/prompt-lab/`（sha-gated、preimage-anchored、一键 apply/restore，bundle sha 35971604…cbabc 已锁定）。**新会话生效**：Agent 工具 subagent_type 应出现 zloop-worker/zloop-auditor（路径已修正）。证据链：`zloop-gen8/artifacts/prompt-engineering/D22-native-prompt-mechanism.md`；决策 D-22 + D-22 勘误与升格。

**第三轮（剃刀后审计执行）完成（2026-09-02 深夜 II）**：GPT 第三次审计全部采纳项已执行——P-SEC1 哨兵探针（**读隔离 FAIL + loopback 可达 = P0 坐实**，D-17 缓解已落码）；D-16 hook cwd 域过滤 + plugin/ 部署包；D-18 Research 三轴语义；D-19 Kimi 搜索器化（disabled_tools）；D-20 controller 死亡证明（takeover_controller）；D-21 redact 邻接精化 + token 哨兵回归；`zloop stage promote` CLI（M8 执行面闭合）。全套 **260 测试通过 / 2 skip**。研究核查：MRCR 溯源（Michelangelo 2409.12640 → OpenAI HF 数据集；"GDM-MRCRv2" 无 DeepMind 一手来源，如实标注）；#42184 核实仍开放；**工作集假说获直接文献支持（Distractor-Aware Truncation 2608.03297：25-75% 保留率 ≥ 全量上下文，部分模型显著更高）**——M9 原生对照臂与 M10 上下文质量臂已入 VOL-19 §7。功能冻结纪律生效：本轮零新子系统，仅修正。

## 用户解锁清单（不变）

1. 开一个**新 ZCode 会话**（hook 首验 + P-HK/BIND/G-COG + **D-22 验证：Agent 工具 subagent_type 应出现 zloop-worker / zloop-auditor** + 顺手验证 ZCODE_DESKTOP_CONTEXT_PROMPT_ENABLED=0 与 computer-use 插件关闭前后的 token 面）
2. 确认移除 `C:\ProgramData\OpenAI\Codexequirements.toml`（已备份；renamed-first 程序在 repo docs/OPERATIONS.md §6）

## 用户解锁清单（剩余 P0 全部依赖此三项）

1. ~~codex login~~ **已解锁（D-14）**：opencodex/cliproxy 路由实测可用，P-CDX1/P-CDX2 转 PASS，M7/M9 可直接实测
2. 开一个**新 ZCode 会话**（hook 已装；首验清单在 repo `docs/OPERATIONS.md` 第 7 节；解锁 P-HK1-4/P-BIND1/P-GC1/P-PLAT1 与 M8）
3. 确认移除 `C:\ProgramData\OpenAI\Codex
equirements.toml`（已备份）

## 下一轮代码债（无阻塞，按优先级）

- `zloop stage promote` CLI 命令（promote.py 库函数已备，未接命令面）
- attempts 表删除（D-12；launches 已含 attempt 列）
- D-8 接管的 (pid,start_time) 死亡证明核验
- M8 真实 G-L 循环、M9 实测扩宽、M10 benchmark/chaos/freeze

## 执行期新事实（已按"现实优先"回写 VOL-02）

- Codex 登录态损坏（`codex login status` = invalid ID token format）⇒ P-CDX1/2/3、P-LUNA1 BLOCKED-manual（等用户重登）。
- ZCode 无 headless CLI ⇒ P-HK/GC 组探针需新交互 session；hook 按 D-3 保守契约实现，装好后用户下一个新 session 即首验。

## 环境（已由 2026-09-02 审计预填，P-ARC-1 时核对漂移）

- 机器：Windows 10.0.26200 / ZCode 3.10.2 / Python 3.14.3（stdlib SQLite **3.50.4 = WAL-reset 受影响**）
- codex-cli 0.147.0（最新 0.152.1）/ kimi 0.28.1（最新 0.40.0；#1897 修复 PR 开放中）/ git 2.55.0.windows.3
- 旧树：`E:\codex-LOOP`（只读归档）；AI 审计包 `deliveries\codex-loop-ai-audit-20260901`
- 待清场：`C:\ProgramData\OpenAI\Codex\requirements.toml`（旧 LOOP 机器级 hook，P-HYG1）

## 下一步（按序）

1. P-ARC-1 环境考古 → `artifacts/capabilities/phase-1.json`
2. P-HYG1 旧 hook 清场（需用户确认移除）
3. P-SQL1/P-SQL2（SQLite 修复路径 + authority placement）
4. P-HK1/2/3/4、P-BIND1、P-GC1、P-PLAT1、P-NAT1（ZCode 平台组）
5. P-WS1、P-CDX1/2/3、P-KIM1、P-LUNA1、P-C2C1
6. 汇总 manifest → VOL-20 §6 六项退出判据 → 进 M1

## 已完成

- [x] 2026-09-02 独立审计（Gen-8 核查：4 P0 / 7 P1 / 6 P2 已并入规范库）
- [x] 2026-09-02 规范库 VOL-00…VOL-22 冻结（`E:\zcode\zloop-spec\`）

## 阻塞 / 待用户

- 无（尚未启动执行）

## 决策指针

见 `DECISIONS.md`（D-1…D-4 为审计期预置决策）。
