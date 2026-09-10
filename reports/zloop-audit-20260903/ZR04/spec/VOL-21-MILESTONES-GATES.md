# VOL-21 — 里程碑与发布闸门

> **ZLoop Spec v1.0** · 卷 21/22 · 层级 L3 · 依赖：VOL-20
> 每个里程碑：入口条件 / 交付物 / 出口 gate。**顺序不可跳**；测试先于下一个功能。

---

## M0 — Archaeology + Capability Truth（任何实现之前）

- 入口：VOL-00/01/22 在工作集；PROGRESS.md 初始化。
- 交付：VOL-20 §6 全部六项。
- gate：见 VOL-20 §6。**任一 P0 未闭合，不得假装设计成立；选 fallback 后继续。**

## M1 — No-op 插件 + 安装/卸载

- 交付：`zloop install/uninstall/doctor`；空 hook 注册（七事件全 no-op）。
- gate：disable/uninstall 后 native 语义恢复（I1 实测：无注入、无 hook 进程、turn 延迟与 Z0 无可感差）；配置改动经新 session 生效的文档验证。

## M2 — H0 + Session Binding

- 交付：zloop-hook capture（七事件）、redaction、coverage、ACL、fail-soft；bind-token claim（含 `--wait-claim`、`binding status`）；attach/detach；compact/startup/resume/clear 规则。
- gate：I2/I3/I13/I18/I21/I22/I23/I28 全绿；P-HK1/HK4 复测通过；hook 输出 schema fixture 固化。

## M3 — SQLite S + H1/H2

- 交付：S schema/事务/OS 锁/controller_epoch/备份与损坏恢复（先于任何 scheduler）；H1.machine 重建、H1.semantic 检查点（piggyback、去重、evidence-ref 校验）、H2 命令。
- gate：I4/I5/I14/I15/I33/I43/44 + 损坏恢复 drill；**未完成本 gate 不得写 wave 引擎**。

## M4 — Research Broker

- 交付：Luna 行为对等 adapter（entitlement canary）、Kimi K1/K2、research sandbox、bounded Evidence Manifest、source promotion、SSRF-safe fetch、cache/singleflight、SearchHealth 独立熔断。
- gate：I11/I18/I27/I29(部分)/I42 + #1897 fixture 绿；fake/private URL 负测试绿。

## M5 — C2C Stage Auditor

- 交付：真实 transport 链（prepare → root Browser fresh thread → record）；P/A 分线程；audit_coverage；风险分层；late remediation；数据分类边界。
- gate：I12/I13/I16/I17/I41/I41b/I30；C2C outage 注入不阻塞 NORMAL 机械路径。

## M6 — Supervisor + MockBackend + WorkspaceBackend

- 交付：Stage/Packet FSM、四级 fencing、one-workspace-per-launch、clean immutable Stage base、host 当前快照重验收（含批量化+bisect）、worktree_fast/clone_strong、checked-out-safe ff 晋升与 S 对账——全部用 MockBackend 驱动。
- gate：I6/I7/I8/I9/I23/I26/I34/I37/I38/I39 + VOL-18 D 全 kill 点 chaos。

## M7 — CodexSdkBackend

- 交付：SDK 后端 + worker-host（Job Object）；P-CDX1/2/3 修正后接入。
- gate：I24/I25/I44；嵌套 catalog 探测与双 canary 绿；stale-active 场景 bounded；1/2/4/8 并发真实跑通后扩上限。

## M8 — G↔L 真实循环

- 交付：真实 `/goal` 下 ≥3 次 serial→research/C2C→serial→wave→materialize→serial。
- gate：G-COG 定档模式下端到端 trace（I14）；wake/human cost 如实记录进报告。

## M9 — Scale 10–30

- 交付：四类 workload 的 p_class 实测与静态 cap；anti-thrashing 信号。
- gate：I26 容量语义（不为 30 造 filler）；J(p) 曲线落盘。

## M10 — 真实 benchmark + freeze

- 交付：VOL-19 全部（Z0–Z5、C0/C1/C2、摩擦数字）；diff 审计；行为契约追溯表；rollback drill。
- gate（release）：下表全过 ⇒ 标记 production candidate。

## 发布闸门（M10 checklist）

```text
[ ] Z0.5 vs Z0 无可感回归（hook 延迟/注入/工具面）
[ ] I1–I44 逐条有测试且绿（追溯表覆盖）
[ ] chaos：double-spawn/double-promote = 0；stale materialize = 0；恢复 drill 成功
[ ] uninstall 演练：插件+supervisor 全关 → 纯 ZCode 语义、用户 repo 无损
[ ] rollback：`zloop rollback --run` 演练通过（含用户 repo 状态保全）
[ ] secrets：全树扫描 0 命中；env allowlist 审计记录
[ ] 摩擦审查数字齐全（VOL-19 §6）
[ ] 诚实边界：因证据不足而明确不实现的清单成文
```
