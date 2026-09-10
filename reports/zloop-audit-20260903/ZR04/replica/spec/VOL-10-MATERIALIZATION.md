# VOL-10 — 物化：REPORTED ≠ ACCEPTED，验收在 host 当前快照上重跑

> **ZLoop Spec v1.0** · 卷 10/22 · 层级 L2 · 依赖：VOL-08, VOL-09, VOL-13
> 含审计修正 P1-10（批量化验收）。本卷消灭"各自绿、合并红"：worker 在旧 snapshot 上的绿灯不作数。

---

## 1. 核心算法（单一职责链）

```text
REPORTED(worker_delta, worker_local_evidence)
→ 验证 launch/revision/scope/artifact（I6 fencing + §2 delta 检查）
→ 在 private integration workspace 把 candidate delta 应用到 CURRENT stage_snapshot_k
→ conflict？ → BLOCKED/REPLAN（不拿 worker 原 commit 强塞）
→ 在该 candidate 上重跑 packet-required host mechanical acceptance
→ ACCEPTED(candidate_hash, oracle_refs)
→ materialization_intent(snapshot_k, packet_revision, launch_id, candidate_hash) [S 事务]
→ host 生成自己的 materialization commit（带 provenance trailers）
→ packet MATERIALIZED(snapshot_{k+1})   [I38]
```

**为什么**：P2 基于 S₀ 绿，不代表基于 S₁（P1 已物化）仍绿——`P2 green@S₀ ⇏ green@S₁`。host 必须在实际 candidate 上重验收。

## 2. Delta 重建（worker 的一切自报不可信）

- 来源 = **受信 base（stage_base_tree）+ worker 最终 filesystem 状态**；worker commit 的 parent/author/trailers 全部忽略。
- 枚举必须 NUL-safe + path-canonicalization-safe：tracked/staged/unstaged/untracked/deleted/renamed、file mode、symlink、submodule/gitlink、Windows junction/reparse、casefold/UNC/traversal。
- 校验：`actual_changed_paths ⊆ write_scope`；越界即拒。
- 额外拒绝/需显式批准：`.gitmodules`/submodule target、junction/reparse/symlink escape、对 Git 管理 refs 的修改。
- **不要只解析人类可读 `git status`**（旧树学费）；实现用 `git status --porcelain=v2 -z` + `git diff` 家族的机器输出。

## 3. 验收分层

```text
packet-local oracle（delta 上的快速门：format/lint/type/unit 相关子集）
→ materialization/integration oracle（candidate snapshot 上的 packet-required 验收）
→ final staged oracle（整个最终 candidate）
→ post-promotion smoke/oracle（canonical 上，VOL-11 §4）
```
quant packet：dataset/time split/no-lookahead/determinism/backtest sanity/performance bounds。

## 4. Host materialization commit

- host 从受信 base + final delta 构造 commit（作者=ZLoop bot），trailers：
  `ZLoop-Run / ZLoop-Stage / ZLoop-Stage-Revision / ZLoop-Packet / ZLoop-Packet-Revision / ZLoop-Launch`。
- 私有 ref：`refs/zloop/<run>/<stage>/snap_<k+1>`；`current_snapshot` 指针更新在**同一 S 事务**（VOL-04 §4 配方）。

## 5. 吞吐修正 [P1-10]：批量化 + bisect + oracle 缓存（**DEFER 至 D-12**）

> 剃刀审计裁定：在测出 `T_host_acceptance / T_trusted` 的实际占比以前，本节全部机制**不进入 v1 生产代码**——v1 只做逐 packet 串行物化；只有 profiling 显示占比显著（建议阈值 ≥15%），才按本节实现。保留本文仅作为届时的设计草案。

k 个 packet 的 wave 逐个物化 = O(k) 次验收（可能 O(k×套件时长)。**同*验收套件*成本下按批合并**：

```text
1. 按 (write_scope 不交 ∧ acceptance 划分相同) 把同 wave 的 ACCEPTED-pending packet 分批
2. 每批：一次性应用整批 delta → 生成一个 candidate → 跑一次组合验收
3. 通过 → 逐 packet 标 MATERIALIZED（同事务逐个写 events + 各自 candidate_hash）
4. 失败 → bisect：二分批重放定位坏 packet（每步复用 oracle 缓存）
5. oracle 缓存 key = (base_tree_hash, oracle_id, 输入集 hash)；命中直接引用上次结果
   —— 缓存只对确定性 oracle 生效；非确定性（网络/live 数据）禁用缓存
```
批量化**不得**改变正确性语义：每 packet 仍然要在"包含其 delta 的某 candidate"上被验收过才 MATERIALIZED（bisect 的中间 candidate 满足该性质）。

## 6. 失败/取消时的 dirty work 保留

失败/取消 worker 的 workspace 在回收前：导出 recoverable patch + 产物清单（带 stage/packet revision、attempt、launch_id、base snapshot）到 `runs/<run>/waves/<wave>/recover/`；进 H0 引用。**先保存再删除** [I7 之外的经验教训，旧树 KEEP 行为]。

## 7. 测试锚点

I26（stale snapshot 的 REPORTED 被重套+重验）、I5/I3（fencing）、bisect 正确性（batch 失败定位到单 packet）、oracle 缓存命中/失效（非确定性 oracle 禁缓存）、write_scope 逃逸（symlink/junction/untracked binary/mode/case-only rename）。
