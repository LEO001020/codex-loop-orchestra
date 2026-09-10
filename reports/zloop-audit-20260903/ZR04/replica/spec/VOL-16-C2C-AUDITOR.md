# VOL-16 — C2C Stage Auditor：异种批评，不是第二个老板

> **ZLoop Spec v1.0** · 卷 16/22 · 层级 L2 · 依赖：VOL-02, VOL-09
> 含审计修正 P1-11（bounded 回注 + ToS 入档）。

---

## 1. Transport 真相（不可绕过）

**ZCode Browser 是 root 的模型工具，不是 `zloop` host API**（main-agent-only，已核实）。不存在能后台自动浏览 ChatGPT 的 `zloop audit`。真实分工：

```text
host:  zloop c2c prepare plan|result --stage S03
        → 生成 bounded packet（hash/revision/allowlist evidence 视图）+ c2c_id
root:  用 native Browser → ChatGPT Web → NEW THREAD（P/A 分开）
        → 发送 bounded packet（或让 ChatGPT 读取已验证 read-only bridge 视图）
        → 读取响应 → zloop c2c record --c2c <id>
host:  校验 packet hash/stage revision → 脱敏后写 H0/audit evidence
```
wall-clock 隐藏：`zloop research start` → root 做 C2C-P → `research await` 一次。**不假设 Browser 可被外部进程后台化**。

## 2. P/A 线程纪律与去锚定

- **C2C-P（计划审计）**：新线程；第一包只给 objective slice、constraints、host risk floor、verified machine facts、selected primary evidence refs、unknowns、success criteria——**不给 root 的详细 plan**；要求它独立形成 plan/counterplan、补 Web 主源、指出 blind spots。
- **C2C-A（结果审计）**：**再开一个新线程**（不得复用 P 的）；读真实 staged diff/artifacts、oracle digest、失败/冲突、root 当前 claim。
- 同一 thread 审 P 和 A = auditor 被自己的 plan 锚定。**D-11（剃刀审计采纳）**：fresh 分线程对 **HIGH/CRITICAL 保持强制**（安全默认）；对 **NORMAL 不再是不变量**——线程策略降级为 C2C A/B 的一个变量（`C_same`=同 Stage 复用线程 vs `C_fresh`=P/A 各开新线程，并入 VOL-19 §5 的 C0/C1/C2 矩阵），测出 catch-rate/墙钟差异后再定默认。

## 3. 可观察身份记录（I41b）

每次 C2C 记录当时可观察的：`surface/provider`、`UI model label`、`Search/Research mode`、fresh thread identity（非敏感引用）、timestamp。**不可观察就写 `unknown`**——"网页标题叫 ChatGPT"不构成跨家族独立性证明（UI routing 存在）。异种性是被观测的属性，不是架构硬编码事实。

## 4. Packet 数据分类

进入 Browser/C2C 前执行数据分类：`public | project_internal | sensitive | secret`；`secret` 绝不出机，`sensitive` 需 project policy 明确允许。优先让 ChatGPT 读受限 staged audit view（若存在 read-only bridge），而非倾倒 repo/history。所有外部输出标 `external_untrusted`。

## 5. Bounded 回注 [P1-11]

ChatGPT 响应经 Browser 进入的是 **root 的 context**。规则：
- 响应全文 → `zloop c2c record` → blob（脱敏后）；
- root 面只保留 bounded digest（发现清单 + severity + 指向 evidence ref），**默认 ≤2KB**；
- C2C 是 Stage 边界服务，不是 turn-level 服务：每 Stage P/A 各一次为默认节奏。

## 6. 风险分层（参与 ≠ 阻塞）

| 风险 | C2C-P | C2C-A | 阻塞语义 |
|---|---|---|---|
| LOW | optional | optional | 无 |
| NORMAL | default ON（与 Research 并发） | default ON（与长 oracle 重叠） | soft advisory；timeout/outage 不阻断机械工作 |
| HIGH | REQUIRED | REQUIRED（canonical promotion 前） | outage ⇒ STAGED/BLOCKED，除非 human waiver |
| CRITICAL/不可逆/live | REQUIRED | REQUIRED | + 机械 oracles + **human approval** [I31] |

audit_coverage 必须如实记录：`text_packet_only | readonly_bridge | model_relayed | insufficient`。HIGH/CRITICAL 若 diff/evidence 大到当前 coverage 无法诚实检查 ⇒ 保持 STAGED/BLOCKED 或 human waiver——**不得用一句摘要冒充完整审计**。

## 7. 迟到发现

NORMAL soft C2C-A 在 Stage 已 promotion 后到达：写 H0；severity ≥ HIGH ⇒ **开新的 remediation Stage**；绝不回写/回滚已关闭 Stage 历史，绝不自动回滚外部不可逆效应（I17）。

## 8. 运营风险入档

- 自动化访问 ChatGPT Web 可能违反 OpenAI ToS，存在账号风控/封禁风险——写入 `docs/SECURITY.md` 与用户可见 README；C2C 频率默认克制（每 substantive Stage P/A 各一次），不做高频轮询式对话。
- 登录/2FA/CAPTCHA 是 **manual prerequisite**（执行 Agent 停下来等用户的少数情形之一）。
- C2C 与 root 分歧能被机械现实回答 ⇒ 生成 targeted oracle，不靠模型投票（`mechanical > primary > C2C > introspection`）。

## 9. A/B 评测（VOL-19 §5）

C0 = 无 C2C；C1 = 每 substantive Stage advisory P+A（仅 HIGH/CRITICAL 阻塞）；C2 = 全 Stage 硬门。看真实缺陷捕获、误报、wall-time、返工、human minutes。若 LOW/NORMAL 某 gate 净负收益 ⇒ 只下调该风险类，不砍 HIGH/CRITICAL。
