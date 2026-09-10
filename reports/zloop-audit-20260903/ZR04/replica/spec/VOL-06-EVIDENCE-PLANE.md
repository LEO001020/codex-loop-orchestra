# VOL-06 — 证据平面：H0 捕获、H1 检查点与 H2 精确回查

> **ZLoop Spec v1.0** · 卷 06/22 · 层级 L2 · 依赖：VOL-04, VOL-05
> 取代 Gen-8 §7。核心定位：**被成功捕获且允许持久化的可观察过去，不因 context eviction 消失；当前工作语义有有界检查点；过去可程序化精确定位；恢复不把别的 run 注入当前 session。**
> ZLoop 不替换 ZCode native transcript/compaction（其归 ZCode 所有）；不伪装 Codex TokenBudget / SKILL.state / persistent runtime。

---

## 1. H0：Exact Captured Observable Evidence（fail-soft）

### 1.1 "Exact" 的边界（诚实性）

只记录**captured observable surface**：UserPromptSubmit（脱敏后 prompt）、Stop/final assistant、可观察 tool call/result/failure、web/research provenance、C2C 选定包与发现、wave/worker 结果引用、Git/diff/commit 引用、compiler/tests/property/backtest 输出、stage/session/compact 边界、integration/promotion 证据。
**不记录** hidden CoT；native 子代理内部步骤不可见时标 `native_child_result_only`，不假称全历史。

### 1.2 捕获管线（顺序是硬不变量 [I13]）

```text
RAW EVENT → schema 校验 → 递归 secret redaction → SAFE PAYLOAD
          → serialize → hash(SAFE PAYLOAD) → journal/blob
```
- redaction-before-hash：任何 raw secret 不出现在 blob 或 hash 输入中。
- 默认排除/强脱敏：`.env*`、PEM/SSH 私钥、wallet/keystore、token/key/password/cookie/authorization header 模式、`.zloop`/`.zcode` 权威路径内容。
- 递归自读 guard：`zloop history/*` 读历史不产生新 history 行（kind 白名单外的不记）。

### 1.3 物理布局与并发

- 每 session 单 NDJSON writer（`history/sessions/<session_id>.ndjson`），跨进程用 per-session lockfile（msvcrt/fcntl）——**并行 tool call 可能并发触发 PostToolUse** [P2-14]，锁是必须而非优化。
- payload >4KB → blob；行级 `prev_line_hash` 用于损坏检测（非防篡改权威）。
- 写失败（磁盘满/ACL/解析）⇒ 尽量写 `history_degraded` 诊断行；连诊断都写不了 ⇒ 静默 exit 0（native turn 不得失败 [I3]）。

## 2. H1：Machine Envelope + Semantic Capsule

### 2.1 触发规则（durable handoff，不是次数仪式 [I33]）

| 边界 | 动作 |
|---|---|
| StageCommit / wave submission | root 本来就要表达 objective/decisions/unknowns ⇒ 同 payload 派生 H1.semantic（piggyback，0 次额外模型调用） |
| post-wave synthesis（吸收证据后、跨 round/下一 wave 前） | `semantic_state_hash` 变化才写 |
| StageClose | 自上次检查点无语义 delta 则不写 |

简单 Stage 通常 0–2 次；多 wave Stage 可更多，但与真实 handoff 次数成正比。回归指标：`H1 writes per durable handoff` 与 `per Stage` 分布（VOL-19 §4）。

### 2.2 恢复优先级（硬编码，不可配置）

```text
CURRENT VERIFIED REALITY（S + Git/files + backend + 当前机械 oracle）
  > REGENERATED H1.machine
    > H1.semantic prose
```
冲突时当前现实获胜并记录 `checkpoint_stale`（测试 I16 fixture：external files 领先 stale H1.machine）。

### 2.3 恢复注入（仅 SessionStart）

- `source=compact`：对 exact bound session 注入 bounded 块（模板见 §4）。
- `source=startup|resume`：仅当该 session 已绑定/恢复到某 run 时注入。
- `source=clear`：默认不注入；`resume_after_clear=true` 或用户显式 `zloop attach R… --resume-after-clear` 例外（I28）。
- 禁止每 UserPromptSubmit 注入；禁止全量 Active Work/DAG/history summary；禁止 auto-RAG。

## 3. H2：Programmable exact recall（v1 命令面）

```text
zloop history search <query> [--session|--run|--stage] [--limit N]
zloop history around <event_id> [--before N --after N]
zloop history show <event_id>
zloop evidence show|verify <ref>
zloop checkpoint current|show <id>
zloop history verify            # 行级 hash 链 + blob 存在性校验
```
- 底层 NDJSON+blob 保持 grep/Python-friendly；任何索引（FTS 等）只是可重建派生物 [I11]。
- 输出必须 bounded（默认 ≤50 行 / ≤16KB），全文引导用 `evidence show`。

## 4. bounded 恢复注入模板（compact 时，总预算 ≤2.5K tokens）

```text
ACTIVE ZLOOP RUN R012 / STAGE S03 rev3
Machine envelope: <fresh 重建的 H1.machine 有界摘要>
Semantic checkpoint: <H1.semantic 有界摘要>
Exact captured evidence: zloop history/evidence …
Current files/runtime/oracles override stale checkpoint prose.
```

## 5. 与外部工作的关系（职责切面）

- SessionStart 的 capture 与 recovery 是**两个逻辑分支**，可共用一个进程但故障隔离：capture 失败不影响 recovery 读；recovery 失败（DB busy/corrupt/binding 不明）返回无 additionalContext 且 exit 0。
- research/c2c/worker 的原始输出进 blob + evidence ref；root 面只出现 bounded manifest。
- H0 的 authority 在用户数据目录（user-only ACL）；repo 内只放 derived pointer。
