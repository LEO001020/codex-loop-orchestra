# VOL-19 — 因果评测：消融阶梯、统计与摩擦审查

> **ZLoop Spec v1.0** · 卷 19/22 · 层级 L2 · 依赖：VOL-18
> 原则：不把所有组件一起开然后宣布胜利（"Same Model, Different Harness"——模型与 harness 应视为一个求解器整体评测）。

---

## 1. 消融阶梯（M10）

```text
Z0   最强纯 native ZCode（GLM-5.3 root、native Goal、native 工具/子代理/搜索）
Z0.5 + 空/no-op ZLoop 插件        ← 硬 gate：native 无可感回归（hook 延迟即在此测量）
Z1   + H0 durable observable evidence
Z2   + H1/H2 recovery
Z3   + Research Broker（Luna/Kimi）
Z4   + cold isolated execution wave
Z5   + C2C Stage Auditor
```

正交变量（按 G-COG 定档与能力实测，不随机）：G-COG 档、SDK vs AppServer、worktree_fast vs clone_strong、Kimi K1 vs K2、research native/luna/kimi/组合、C2C off/advisory/hard、H1 machine-only vs +semantic。

## 2. Benchmark corpus（≥18 类，覆盖真实工作形态）

深层串行 debug（hypothesis→instrument→test→reject）；强依赖大型重构；多次 compaction 后早期 constraint 再相关；rejected hypothesis resurrection；10 独立 write domains；16 独立 workstreams；24 API/version 兼容 probe；30 独立实验（机器允许时）；docs/SDK migration（Web freshness）；大 dataframe/quant factor/backtest；serial bottleneck（验证调度器**拒绝**虚假并行）；shared cache 读污染；Luna/Kimi/C2C 冲突证据；C2C 抓出机械绿灯下的语义错误；browser/C2C outage；Kimi incomplete output；Git promotion crash 恢复；Windows 文件系统/mtime/junction/AV 竞争。

## 3. 统计纪律（方差先行）

1. 先估 `σ_task / σ_run / σ_infra` → 定 MDE 与重复数。基础协议：20 任务 × 3 配对重复起步；CI 过宽 → 5–10 重复。
2. 固定：repo snapshot、model/reasoning、machine/provider/search、acceptance、time budget。
3. 二元 outcome 用 McNemar；连续/比例指标用配对 bootstrap。**报告 pass@k 与 pass^k**，不报"最好一次"。
4. 旧 Codex LOOP 作 whole-system reference/golden failure corpus；除非同模型同条件跑两边，不把模型差异冒充 harness 效应。

## 4. 关键指标

质量：task success、mechanical acceptance、release regression、C2C 真阳性/误报/返工避免。
时间：`T_trusted`、TTFT、wall-clock、human intervention minutes。
上下文（摩擦的量化）：per-turn injected tokens、tool definition tokens、compaction count、H1 injection size、H2 exact-recovery success。
并发：makespan、E_p（**诊断量**）、R_duplicate、merge conflicts、blocker convergence、provider throttle。
Research：freshness、primary-source ratio、cross-lane unique-source gain、conflict discovery、latency。
可靠性：crash recovery success、double-spawn/double-promote = 0、stale revision/launch materialization = 0。
成本：API/search/C2C 估计成本分别报告，不作隐藏主目标。

## 5. C2C 专项 A/B

```text
C0 = no C2C
C1 = advisory C2C-P + C2C-A 每 substantive Stage；仅 HIGH/CRITICAL 阻塞（默认设计）
C2 = hard C2C-P + C2C-A 每 Stage
```
看真实缺陷捕获、误报、wall-time、返工、human minutes、最终 pass。C1 Pareto 优于 C0 ⇒ 维持近常驻；某 LOW/NORMAL gate 净负 ⇒ 只下调该风险类。

## 6. Harness Friction Review（release 前逐项给数字）

```text
non-active session automatic injected tokens = ?（目标 ≈0）
active session automatic injected tokens = ?（目标 = bounded SessionStart/compact 注入）
number of new model-facing semantic tools = ?（目标 = 0 新工具面，全走 zloop CLI）
PreToolUse governance blocks on ordinary root actions = ?（目标 = 0）
mandatory model maintenance actions per turn = ?（目标 = 0）
H1 writes per durable handoff / per Stage 分布 = ?（无 handoff 的周期性记忆仪式 = 回归）
H1 extra model calls solely for memory = ?（目标 = 0）
C2C calls per Stage 分布 = ?
root can still use native ZCode tools freely = ?
disable plugin restores native semantics = ?（I1 实测）
```
任何非零项不能指向已观察 failure/明确 benchmark gain ⇒ 删（宪章 §7）。


## 7. M9/M10 增补（2026-09-02，第三次审计采纳）

### 7.1 M9 对照臂：native ZCode 并行（不是只比串行 Z0）

ZLoop 本身由两波 15 个 ZCode 原生子 agent 建成——这构成了一个自然反事实基线：**native 并行认知已相当强**。因此 M9 的比较臂必须是：

```text
arm-native-parallel: 同一 root + N 个 native subagents（N∈{2,4,8,12}）
arm-zloop-wave:      同一 root + N 个 first-class Codex workers（同 N 阶梯）
```

同一批真实 workload、同一模型族、同一机器。ZLoop 外部平面的**留存条件**收敛为四项边际能力（Z4 必须至少显著胜出一项，否则删/降为 optional backend）：
1. durable crash recovery（provider/进程崩溃后可机械证明哪个 attempt 有效）；
2. 物理可写隔离（独立 commit/workspace，不依赖 ZCode 内部 agent 语义）；
3. generation fencing + host 验收（事务化多 Agent，而非"更多 Agent"）；
4. 超长任务（小时级 experiment/backtest）与 context sharding。
预期分工假设（待测）：约 80% 常规工作由 native Goal+subagents 承担，ZLoop 冷 wave 只在上述四项有价值时显形——若成立，这是比"大任务默认 /loop"更优的终局架构。

### 7.2 M10 上下文质量臂（工作集假说，C_max ≠ C_optimal）

背景：主流长上下文基准存在局限 + 厂商针对优化（用户与 GPT 的 GDM-MRCRv2 讨论；RULER 类研究显示"标称上下文 ≫ 有效上下文"）。核心假说：**有界工作集 + H0 精确可寻址历史，可能优于全量 raw 上下文**——若真，H0/H1 不只是灾难恢复，而是保护 Transformer 有效注意力的性能机制（工程上类比滑动窗口注意力之于全注意力）。

设计（真实 coding 历史 ≈256K 为基底）：
```text
A: root 保留全量 raw context
B: root 仅保留 ~64K 当前工作集 + H0 exact recall
C: B + 并行 workers 各自局部 20–40K（context sharding）
```
指标：任务质量 Q、T_trusted、约束丢失率、重复读取次数、被否决假设复活率。历史规模阶梯 32K→64K→128K→256K→512K→1M 观察 R(L)=S(L)/S(L₀)。
**判据**：C>B>A ⇒ memory/sharding 复杂度获得存在资格；A≥B,C ⇒ 继续削 Memory 机制。
（MRCRv2/有效上下文文献核验进行中——metrics 细节以研究结论为准，未核实前不作为已证前提。）
