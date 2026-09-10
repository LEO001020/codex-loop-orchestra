# VOL-08 — Stage 管线：语义边界、风险下限与私有快照谱系

> **ZLoop Spec v1.0** · 卷 08/22 · 层级 L2 · 依赖：VOL-04, VOL-07

---

## 1. Stage 是什么、不是什么

Stage = 一个可解释的工程小节（假设/架构决策单位），**不是 turn、不是单测**。创建必须至少命中一条：

1. 主要 hypothesis/architecture decision 改变；2. 需要新一轮外部事实；3. 将启动真实写 wave；4. 风险等级变化；5. data/backtest 语义边界变化；6. 需要形成独立 staged candidate；7. blocker 导致前沿重分解；8. 用户目标实质改变。

**反例**（不开 Stage）：一次 shell、grep、小 edit、单测修复、纯读研究。
Stage 记录字段见 VOL-04 §3 `stages` 表；`stage_revision` 只在 Stage specification/语义改变时 +1（与 packet retry 无关）。

## 2. Risk：root 只能提，host floor 不能静默降

```text
risk_effective = max(risk_requested, deterministic_host_floor)
```

| floor | 触发（确定性规则，不靠模型分类） |
|---|---|
| CRITICAL | live trading/order/withdrawal；production deploy/release（不可逆外部效应）；destructive data migration；secret/credential/permission authority 变更 |
| HIGH | auth/security 边界；schema/data migration；CI/release 基础设施；signing/packaging；可能影响资金的策略语义；依赖/supply-chain 政策 |
| NORMAL/LOW | 未命中上述任何规则 |

root 可提高风险；**降低 host floor 必须 human override 且留 S/H0 证据**。floor 规则放在 `zloop` 内置表 + 项目配置可加严（`zloop project config`），不可放宽内置规则。

## 3. Stage base：干净、不可变、有证据

Stage 开始时记录并锁定：

```text
expected_canonical_head   # git rev-parse HEAD
canonical_dirty_digest   # git status --porcelain=v2 -z 的规范化哈希（空树=clean）
stage_base_ref           # 由此创建的私有 base ref（如 refs/zloop/<run>/<stage>/base）
stage_base_tree          # tree sha
```

**v1 生产不变式 [I37]**：启动 first-class 可写 wave 前，canonical workspace 在 wave 依赖范围内必须可证明来自 immutable Stage base；**最安全的默认是 canonical worktree clean**。

dirty（`git status --porcelain=v2` 非空）时：
- read-only Research/C2C/native Explore 可继续；
- 可写 wave → `BLOCKED_DIRTY_BASE`（exit 5 + 明确报告）；
- root/用户先把 coherent work 正常提交，再重开 wave；
- "dirty-snapshot 实验模式"（临时 index/private commit 捕获脏基）**v1 不实现**，除非未来单独通过 chaos/rollback 评审。

wave 期间用户/root 改 canonical：**不阻止任何工具**（宪章禁令 1）；promotion 前重新读取 `current_canonical_head + current_dirty_inventory`，漂移 ⇒ `REBASE_REQUIRED/BLOCKED`（VOL-11 §3）。绝不 stash/reset/merge 用户的改动。

## 4. Stage FSM（guards 表）

```text
PLANNING → EXECUTING : stage base 锁定成功 ∧ wave 提案通过验证
EXECUTING → EXECUTING: 每 wave 循环（不动 state）；snapshot 推进
EXECUTING → STAGED    : 最终 private candidate 组装 ∧ integration oracles 通过
STAGED → PROMOTING    : C2C-A（按风险档）完成/豁免 ∧ promotion_intent 写入
PROMOTING → PROMOTED  : ff-only 晋升成功 ∧ post-promotion oracle 通过
PROMOTED → CLOSED     : H1 检查点（若有语义 delta）+ 备份 + 收尾记录
任意 → BLOCKED        : dirty base / C2C 硬门失败 / HEAD 漂移 / S 降级（附原因码）
任意 → CANCELLED      : 用户或 root 显式取消（launch 处置见 VOL-09 §8）
```

## 5. 私有 Stage snapshot 谱系（materialization 的地基）

- `current_snapshot` = 一个私有 Git ref（`refs/zloop/<run>/<stage>/snap_<k>`），指向 host 生成的 materialization commit。
- `snap_0 = stage_base_ref`；每 MATERIALIZED 一批 packet ⇒ `snap_{k+1}`（VOL-10）。
- 依赖 successor 的可启动条件：`all deps MATERIALIZED into the exact snapshot lineage it will read` [I8]——不是"前置 packet 已 promotion 到 main"。
- canonical main 在 Stage 期间**不被污染**；唯一写通道是最终 promotion。

## 6. Stage 内多 wave 结构（H1 的真实节律）

```text
serial → wave_1 → materialize(snap_1) → serial → wave_2 → materialize(snap_2) → serial → … → STAGED
```
H1.semantic 检查点跟随每个 durable handoff（StageCommit/wave 提交/综合后/Close），与 wave 数成正比，无固定上限 [I33]。

## 7. StageClose 语义

- 触发备份（VOL-07 §6）；写 `events{stage_closed}`；
- late C2C 高严重度发现到达 ⇒ **新 remediation Stage**，绝不回写/回滚已关闭 Stage 历史（VOL-16 §7）；
- `verify-run` 判 CLOSED 才算 Goal 完成的约定（VOL-03 §4 / P-GC1 关联）。
