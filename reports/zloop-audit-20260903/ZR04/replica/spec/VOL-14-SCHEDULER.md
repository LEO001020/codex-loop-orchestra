# VOL-14 — 调度器：前沿驱动，容量而非 KPI

> **ZLoop Spec v1.0** · 卷 14/22 · 层级 L2 · 依赖：VOL-09, VOL-19
> 核心立场：**10–30 是可用容量上限，不是要填满的 KPI**；`E_p = T1/(p·Tp)` 只是诊断量，绝不做 gate（固定作业并行效率度量不适配异构前沿 + T_human 主导的经济函数）。

---

## 1. 运行时选择规则

```text
p_t = min( p_class[workload], |F_t|, P_provider, P_backend, P_workspace, P_machine )
```
- `F_t` = READY 且真正独立的前沿（依赖已 MATERIALIZED、资源可租）；
- 各 `P_*` 为实测后静态写入的上限；`zloop sched status` 可见当前各瓶颈项。

## 2. p_class 的测定（M9，先于任何"冲 30"）

四类 workload 分别固定**同一黄金 DAG**，p ∈ {1,2,4,8,12,16,24,30}，记录：

```text
T_trusted, E_p, P_failure, R_duplicate, provider throttle,
CPU/RAM/IO, startup overhead, human intervention minutes, API/search cost
```
优化目标（VOL-00 约定的经济函数）：

```text
J(p) = T_trusted(p) + λ·T_human(p) + μ·P_failure(p) + ν·R_duplicate(p) + ε·C_API(p)
       （ε ≪ λ, μ, ν —— 用户明确更在意等待/返工/错误扩散/人工救火）
```
选择规则 = 同 workload 下 J(p) 的 Pareto/边际改善（**不是** `E_p≥0.5`）。例：24 workers 把可靠结果从 90min 压到 22min 且错误/返工不升 ⇒ 用 24，即使 E_p=0.35。

## 3. Anti-thrashing（收窄而非盲目补员）

以下信号触发 `stop widening → root serial synthesis → targeted Research/C2C-P → 机械 oracle 重划前沿 → next wave`：

- `R_duplicate` 连续两个 wave 上升；
- 多数 worker 命中同一 blocker（**C 编译器教训的实测化**：16 agents 全堵同一 bug，known-good oracle 才解锁真并行）；
- repeated merge conflicts；evidence gain 很低；
- provider 429/503 持续；
- packet 间出现未预期共享可变资源；
- critical path 已变单一串行 blocker。

## 4. 明确不实现（v1）

refill debt、low-water、role reservations、borrowable pools、duty queue、heartbeat ceremonies、manual status updates、**在线自适应控制器**。加入任何一个的门槛：真实 benchmark/事故证明 simple event-driven refill 不够，且过宪章六问。

## 5. 跨 run 资源隔离

- 不同 run 的 wave 互不知晓；provider 配额冲突表现为 429/503 → 熔断与 backoff（VOL-15 §7），不引入全局协调器。

## 6. Out-of-scope（记录在案）

- **多项目并发全局配额治理** [P2-17]：v1 不做；两个项目同时各跑 30 workers 的配额互抢是已知限制，只做 per-run 限制与日志。
