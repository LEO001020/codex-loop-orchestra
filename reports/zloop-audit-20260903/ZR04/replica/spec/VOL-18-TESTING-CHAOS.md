# VOL-18 — 测试体系：六层，从 correctness 到 chaos

> **ZLoop Spec v1.0** · 卷 18/22 · 层级 L2 · 依赖：全部子系统卷
> 每个里程碑收尾必须过本卷对应层的 gate（VOL-21）。

---

## A. Unit

redaction 模式表、H0 codec/自读 guard、blob CAS、SessionBinding 状态机、SQLite schema/事务/migration、路径规范化（junction/casefold/UNC/traversal）、scope matcher、DAG 校验、Stage/Packet FSM、四级 fencing、lease、risk floor 表、H1 machine/semantic + evidence-ref 校验、Research provenance、C2C packet schema、Git trailer/CAS 解析、Kimi server/CLI 解析器（含 #1897 fixture）、bind-token claim 事务。

## B. Vendor Contract（fixtures 来自 Phase 0 真机）

- ZCode hook 真实输入/输出样本（P-HK2 抓取后固化为 `tests/fixtures/zcode-hooks/*.json`），每次 ZCode 版本变化重抓。
- Codex SDK schema/事件、Kimi live OpenAPI/AsyncAPI hash、Luna search event 样本。
- 三处"两源不一致"的裁决结果必须写进 fixture（输出宽严 / async / CLAUDE_SESSION_ID）。

## C. Integration（I1–I30；Gen-8 §25.C 全量保留，编号不变）

I1 2 disjoint packets→ACCEPTED→MATERIALIZED→STAGED→一次晋升
I2 依赖 successor 只在前驱 MATERIALIZED 进 exact lineage 后启动
I3 同 revision transient retry：新 launch；旧 attempt 迟到被拒
I4 packet 语义 revision 使旧结果全部失效
I5 write_scope/file-mode/symlink/submodule 违规拒绝
I6 merge/canonical-head 漂移 → REBASE_REQUIRED/BLOCKED
I7 失败 worker 的 dirty work 保留 artifact
I8 10-worker useful frontier（backend 允许时）
I9 scheduler 30 模拟 + anti-thrashing
I10 quant 资源租约 + immutable dataset/cache
I11 Luna/Kimi 冲突源保留
I12 C2C NORMAL outage 软降级；HIGH 阻塞晋升
I13 C2C-P 与 C2C-A 独立 fresh threads
I14 选定 G-COG 模式端到端 3 循环
I15 compact 恢复只对 exact bound session；clear 默认不恢复
I16 H1 stale machine/semantic 不覆盖当前现实
I17 Kimi 缺 final assistant ⇒ lane 失败可见 / server 恢复路径可用
I18 H0 recorder 失败不断 ZCode turn
I19 risk floor 不能被 root 降低
I20 迟到高严重度 C2C 发现开 remediation Stage
I21 bind-token 恰绑一个 session；replay/cross-claim 拒绝
I22 SessionStart capture 与 recovery 双分支独立 fail-soft
I23 每 launch 独立 workspace；crash retry 不复用歧义目录
I24 Codex worker 嵌套 subagent 被实测关闭
I25 network_policy=none 物理强制
I26 stale snapshot 的 REPORTED 重套当前 snapshot 并重验后才 MATERIALIZED
I27 dirty canonical Stage base 阻塞生产写 wave；无自动 stash/reset/commit
I28 checked-out 晋升 ff-only/safe；ref/index/worktree 不分裂
I29 HIGH/CRITICAL research 声明不能仅凭模型 citation 成为 verified
I30 C2C audit_coverage 如实区分 text-only/bridge/insufficient

## D. Chaos 注入目录

**Kill 点**（每点 ≥ kill-before / kill-after commit 两相）：SQLite 事务前后、launch_intent 前后、physical spawn 前后、worker_bound 前后、RUNNING、report、acceptance、materialization_intent、private snapshot 创建、STAGED、promotion_intent、Git ref 更新前后（S 未写 PROMOTED）、post-promotion oracle。

**环境/竞态**：worker/backend/provider kill；429/503；disk full；SQLite WAL/DELETE 双模式损坏与恢复；备份恢复 drill；H0 尾部截断/中段损坏；PID 复用；stale-active thread；旧 launch 迟到且仍写旧 workspace；Windows junction/AV 锁；C2C outage/bridge 越权；Kimi stdout backpressure（#1897 场景复现）；root/用户并发改 canonical；dirty base；checked-out 晋升中途 crash；网络 FS authority 负测试（拒绝）；嵌套 subagent 意外出现；worker 网络逃逸；research fake/private URL/redirect；并行 tool call 并发 hook；worker-host Job Object 树击杀（含孙进程）；bind-token 前台/后台路径。

## E. Performance

hook p50/p95/p99（no-op 与写盘）；SQLite 事务延迟；research 覆盖/新鲜度/吞吐；backend 1–30 扩展；worktree vs clone 创建；物化/晋升延迟；H2 10MB/100MB/1GB；C2C P/A 分别 wall-time；G-COG 用户唤醒成本。

## F. Memory/Recovery correctness

多次 compact 后早期 constraint 再相关；一个 Stage 内 3 次 serial→wave→synthesis 且中途 compact；rejected hypothesis resurrection；old API fact 变化；session 交叉污染尝试；bind-token replay；`clear` 语义；kill context；删 H2 索引；corrupt H1.semantic；external files/Git 领先 stale H1.machine。断言：exact captured evidence 可回查、handoff checkpoint 不因固定次数失真、current reality 永远胜出。

## G. 缺陷追溯

`docs/DEFECT_CONTRACT_TRACEABILITY.md`：每个 production invariant → 至少一个测试；每个旧高价值 defect → 新 behavior contract 或显式 `NOT APPLICABLE` 理由。**提取表以 VOL-02 §7 的实际代码树为准**（4 个幻影项已剔除，P0-4）。

## H. 通过标准

- 里程碑 gate 见 VOL-21；任一 P0 级反例出现 ⇒ 先修承重机制再继续功能（攻击清单 VOL-01 §4 不变式即测试清单）。
- chaos 的通过判据是**确定恢复**（对账后状态一致、无 double-spawn/double-promote、无 stale materialize），不是"碰巧没坏"。
