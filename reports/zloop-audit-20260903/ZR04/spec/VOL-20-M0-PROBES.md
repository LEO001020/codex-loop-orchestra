# VOL-20 — Phase -1 / M0 探针目录（可执行）

> **ZLoop Spec v1.0** · 卷 20/22 · 层级 L3 · 依赖：VOL-02
> **规则**：任何探针未跑，不得实现依赖它的组件。全部在无风险临时 repo/只读环境完成；输出 machine-readable manifest（§2 schema）。失败 ⇒ 按 fallback 降级，不许"为了让设计成立"重跑出不同结论。

---

## 1. Phase -1：考古与清场（无风险，只读 + 备份后删注册）

**P-ARC-1 环境/旧项目考古**
- 步骤：记录 `pwd/OS/Git status/HEAD/未提交文件/ZCode 版本（ZCODE_APP_VERSION env）/codex --version/kimi --version/python --version + sqlite3.sqlite_version/git --version/browser 状态/磁盘`；`E:\codex-LOOP` 树只读清点（VOL-02 §7 已有基线，核对无漂移）。
- 通过：manifest 完整落盘 `artifacts/capabilities/phase-1.json`。失败：无（只读）。

**P-HYG1 旧 hook 清场 [P0-3]**
- 问题：`C:\ProgramData\OpenAI\Codex\requirements.toml` 里的旧 LOOP 机器级注册是否还在打击 Codex 进程？
- 步骤：① 备份该文件到 `~/.zloop/hygiene-backup/`；② 列出其注册的 hook 命令；③ 用一个干净 Codex headless turn（临时 cwd）验证旧 hook 是否触发（观察其副作用/日志）；④ 向用户报告并获确认后移除；⑤ 复跑 ③ 确认不再触发。
- 通过：干净 Codex 进程不再触发任何旧 LOOP hook。
- Fallback：用户拒绝移除 ⇒ ZLoop first-class worker 换用隔离的 `CODEX_HOME`（全新 codex home + 显式登录），并在 manifest 记录原因。

## 2. Probe 记录 schema（所有探针共用）

```json
{"probe_id":"P-XX","question":"…","executed_at":"…","status":"PASS|FAIL|DEGRADED|BLOCKED",
 "environment":{…},"evidence_refs":["ev:…"],"fallback_triggered":null|"…",
 "contract_updates":["VOL-02 §x.y: …"] }
```
汇总进 `artifacts/capabilities/manifest.json`；**每条 fallback 触发都同步改对应卷**。

## 3. ZCode 平台探针

**P-HK1 并发 hook 竞态 [P2-14]**
- 步骤：临时 repo 注册 zloop-hook（用户级 config）；单条 assistant 消息里并行发两个 Bash 调用；检查 journal 交错与锁行为。
- 通过：两行完整落盘、无撕裂；或锁等待后串行落盘。失败：丢行/撕裂 ⇒ 修锁实现后重测。

**P-HK2 hook 输入/输出 schema 实测裁决（三处两源不一致）**
- 步骤：① 用 echo 型 hook 把收到的 stdin JSON 原样落盘（本地 fixture）；② 七事件逐一触发，记录字段全集（SessionStart 的 source 实际值枚举——含 resume 是否出现）；③ 输出侧：发含多余 key 的 JSON（验证宽/严）、`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"…"}}`（验证注入）、`async:true` command 型（验证 fire-and-forget）；④ 验证 PostToolUse 的 tool_response 是否含完整 Bash stdout（大输出是否截断——决定 marker 放置策略）。
- 通过：fixture 齐全，三处不一致有裁决。**任何与 VOL-02 §1.1 不同的实测 ⇒ 更新 VOL-02 并记 DECISIONS**。

**P-HK3 子代理可见性**
- 步骤：root 派一个 native subagent 执行 Bash；观察是否触发 PostToolUse、session_id 是否与主会话一致。
- 通过：语义明确记录（无论哪种）；binding 设计按结果微调（session 级不受影响，H0 coverage 标注要改）。

**P-HK4 hook 延迟**
- 步骤：no-op 与真实写盘 × 100 次（1/8/64KB payload），记录 p50/p95/p99；对比纯 native turn 延迟。
- 通过：p95 在 VOL-05 §6 目标内且 Z0.5 无可感回归。失败 ⇒ 评估 tiny native helper（先记录数据）。

**P-BIND1 bind-token 全路径 [P2-13]**
- 步骤：① 前台 `run start`（单 session）→ claim 成功、binding_epoch 正确；② 两个并发 ZCode session 各自 start → 各 claim 各的，cross-claim 失败安全；③ 后台 `run start`（run_in_background）→ tool_response 无 stdout → token 过期 NOT BOUND；④ replay 同一 token → 拒绝；⑤ `binding status`/`--wait-claim` 行为。
- 通过：I21 全绿。

**P-GC1 G-COG 定档 [P0-2 关联]**
- 问题：`/goal` round 如何与后台任务 notification、wave 状态交互？
- 步骤：临时 repo 设 `/goal "完成 W 三轮循环…"`；① wave 类任务后台启动（sleep 模拟）→ notification 到达时 goal 是否继续/开新 round；② goal verifier 是否在 pending wave 存在时误判完成；③ `/goal pause`（无损）与 resume 后 binding/H1 恢复；④ 构造 1/5/15/30/60min 并行任务记录时序 trace。
- 通过：输出 `gcog_mode=A|B|C|D` + 时序 trace + 用户介入次数；**未出结果不得实现上层自动 Stage orchestration**。
- 预期（基线证据）：B（wake 已实测）；若 verifier 误判 ⇒ 按 VOL-03 §3 降级。

**P-PLAT1 平台契约固化**
- 步骤：实测前台 Bash timeout 上限（预期 600,000ms）；TaskOutput block/timeout 语义；notification 到达时点（turn 内 vs 边界）。
- 通过：数字进 VENDOR_CONTRACTS；`await` 实现参数化据此设定。

**P-NAT1 原生子代理容量 [P2-15]**
- 步骤：N=2/4/8/12/16/24/30 foreground/background 各一轮；记录 requested/actual/峰值重叠/makespan/失败/回传大小/RAM；同时实测 `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION / MAX_CONCURRENT_SUBAGENTS / EXPERIMENTAL_AGENT_TEAMS` 旋钮效果。
- 通过：native 容量画像落盘；**不据此把 native helper 当 durable first-class worker**（I19）。

## 4. 存储探针

**P-SQL1 SQLite 版本闸门 [P0-1]**
- 问题：本机 3.50.4 受 WAL-reset 影响（<3.50.7 backport）——修复路径选哪条？
- 步骤：① 记录 `SELECT sqlite_version()` + compile options + `journal_mode/synchronous` 实际回读；② 评估三路径：pysqlite3 wheel（cp314/Windows 可用性）、捆绑 sqlite.org 3.53.4 DLL、维持 DELETE+EXTRA；③ 选中路径跑 crash/kill/AV lock/multi-reader/integrity 全套；④ 网络盘/DrvFs 负测试（拒绝打开）。
- 通过：S 的 journal 模式确定 + 全套 crash 测试绿。**在版本闸门通过前禁止 production WAL**（I22）。

**P-SQL2 authority placement**
- 步骤：确认 hook / Terminal / zloop CLI / supervisor 实际运行的主机与路径命名空间一致（无 WSL/Remote 跨界）。
- 通过：单 authority host；失败 ⇒ 按 VOL-07 §2 处置（迁移或降级），绝不放网络 FS。

## 5. Backend / Research / C2C 探针

**P-WS1**：worktree_fast 与 clone_strong 各自：创建延迟/磁盘、common-ref 篡改被拒、credential 不可见、host delta 重建完整（junction/symlink/casefold/untracked/mode）。

**P-CDX1 SDK contract**：`openai-codex`（pin 0.147.0）：thread_start/list/read/resume、per-workspace cwd、sandbox、单 client 多 turn、stream 终止（turn/completed）、interrupt、overload、crash/reconnect；stale-active/unknown-turn 全部 bounded。

**P-CDX2 strict worker 双硬门**：① 嵌套禁用：`[agents].enabled=false` 后枚举实际 tool catalog，确认无 spawn_agent 族；② 断网：公网 canary + loopback/private canary 均被拒。任一失败 ⇒ 该 backend 判不满足 strict contract（fail-visible）。

**P-CDX3 已知故障复现性**（0.147.0）：构造 #37047（stale-active resume）、#34220（重启后子代状态）、#37856（ownership 争用）的最小场景，记录是否复现与规避法。

**P-KIM1 Kimi 双 lane**：K1：`kimi web` 起服务、openapi/asyncapi hash、session create（metadata.cwd）、WS 订阅、terminal 判据、`:abort`、messages 恢复；K2：`-p --output-format stream-json` 在长多工具 turn + stdout backpressure 下复现 #1897（本机 0.28.1）→ INCOMPLETE_OUTPUT 路径验证。

**P-LUNA1 Luna entitlement**：LunaCodexLane 真实调用；只有出现真实 webSearch tool/event + source URL 才 AVAILABLE；404/model-not-found/no-tool ⇒ UNAVAILABLE/OBSERVED_DIFFERENT；记录 rate limit 与 4–12 并发。

**P-C2C1 C2C transport**：Browser enabled/登录/fresh-thread 创建/超时/重开；P/A 双线程不串；`prepare→Browser→record` hash 校验；不可观察身份如实 unknown。

## 6. M0 退出判据（全部满足才进 M1）

1. `artifacts/capabilities/manifest.json` 覆盖上表全部探针（含 FAIL 的 fallback 记录）；
2. `gcog_mode` 定档 + raw trace；
3. SQLite journal 决策落定（版本/模式/crash 套件绿）；
4. 旧 hook 清场完成或隔离方案生效；
5. VOL-02 更新为实测版（所有两源不一致裁决完毕）；
6. ZCode/Codex/Kimi exact 版本 + 来源 URL/快照 hash 入 `docs/VENDOR_CONTRACTS.md`。
