# VOL-11 — 晋升：受控 canonical 写入与悬垂对账

> **ZLoop Spec v1.0** · 卷 11/22 · 层级 L2 · 依赖：VOL-08, VOL-10, VOL-16

---

## 1. Final staging

```text
stage_staging_intent(expected_canonical_head, final_snapshot) [S 事务]
→ 在 private integration workspace 组装最终 candidate（staged_head）
→ integration-level oracles（全量）
→ STAGED(staged_head, artifact_hash)
→ C2C-A fresh-thread 结果审计（按风险档，VOL-16 §6）
→ root 裁决发现（能用机械现实回答的生成 targeted oracle，不靠模型投票）
```

## 2. Promotion intent 与 CAS 晋升

```text
promotion_intent{expected_canonical_head, expected_dirty_digest, staged_head,
                 stage_revision, final_audit_ref}   [S 事务，state=INTENDED]
→ 复读校验：current HEAD == expected_canonical_head ∧ dirty digest 未变
   不满足 → REBASE_REQUIRED/BLOCKED（I30；在 private integration workspace 重建/rebase candidate，
            resulting diff 变化 ⇒ 重跑受影响 oracle 与 hard C2C-A）
→ checked-out-worktree-safe 晋升：git merge --ff-only <staged_ref>
   （前置：staged_head 是 expected head 的后代；不是 → 先在私有区重建/rebase）
→ post-promotion oracle（smoke + 关键套件）
→ S 事务：intent state=APPLIED；stages.state=PROMOTED(actual_head)；events{promoted}
```

**I39：禁止在普通 checked-out canonical branch 上裸 `git update-ref`**——那会让 ref/index/worktree 立即分裂。`update-ref <ref> <new> <old>`（带期望旧值的 CAS）只用于私有 refs / bare refs / 已显式处理 worktree+index 的受控实现。

## 3. 晋升提交 trailers

```text
ZLoop-Run: R012
ZLoop-Stage: S03
ZLoop-Stage-Revision: 3
```
（materialization commit 已带 packet 级 trailers；晋升 merge 若产生 merge commit 也带。）

## 4. 悬垂 promotion 对账（crash 落在 intent 与 COMMIT 之间）

读 dangling `promotion_intents(state=INTENDED)` → 查 physical oracle（Git）：

| 物理观测 | 处置 |
|---|---|
| ref 已指向 staged_head ∧ trailers/expected old head 吻合 | 物理效应已发生 → 补写 `promotion_recovered`，**不重复 apply** |
| ref 仍是 expected old head ∧ worktree 干净 | 可安全重试 |
| ref/脏工作区被第三方改变 | `REBASE_REQUIRED/BLOCKED`，重新 staging/rebase，重跑受影响 oracle + hard C2C-A |
| 无法判定 | BLOCKED + 报告（fail-visible） |

Git 是**真实代码 promotion 的 physical oracle，不是 S 的替代** [I21]：Git 不知道 launch 存活、attempt superseded、workspace lease、provider session、cancel、acceptance、quant 资源租约——那些只在 S。

## 5. 不可逆/高风险动作（I31）

- live trading/withdrawal/production deploy/破坏性迁移：**promotion 之外**的显式 human approval 流程（`zloop approve --intent <id>` 由人执行）；C2C/模型/worker 均无权代行。
- normal promotion 也要求 canonical worktree 处于验证过的安全状态；绝不覆盖、绝不自动 stash/reset/clean。

## 6. 测试锚点

I28（ff-only 一致性：晋升后 ref/index/worktree/tree 三方一致）、dangling 四情形对账、HEAD 漂移各注入点（wave 前/中/C2C-A 后/intent 后）、double-promote=0、merge/promotion 串行化（同一 canonical ref 不双写）。
