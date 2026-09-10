# VOL-22 — 执行手册：防迷失协议（迷失时第一个重读的卷）

> **ZLoop Spec v1.0** · 卷 22/22 · 层级 L3 · 依赖：VOL-00
> 本卷解决一个问题：**执行期上下文被细节淹没时，如何不迷失、不即兴、不漂移**。

---

## 1. 每次会话的标准节奏

```text
① 读 VOL-00-INDEX.md（本库地图）+ VOL-01（宪章）
② 读 PROGRESS.md —— 你在哪、下一步是什么
③ 按 VOL-00 §4 装载当前里程碑的卷（≤ 任务卷 2 个）
④ 做当前最小可验证单元（一个 probe / 一个模块 / 一组测试）
⑤ 更新 PROGRESS.md（做了什么/证据 ref/下一步）；有新决策 → DECISIONS.md
⑥ 收尾：工作集卸载（下个会话不需要重新装载记忆，靠的是文件不是回忆）
```

## 2. 任务切分（多大算"一个任务"）

- 一个任务 = 一次装载 ≤2 个任务卷就能做完的事（一个 probe、一个模块+其 unit test、一组 chaos 注入）。
- 超过 ⇒ 切。判断标志：你需要同时引用三个卷的细节 ⇒ 你在做一个里程碑，不是任务。
- 每个任务的完成物必须**可机械验证**（测试绿 / manifest 有记录 / doctor 通过），否则不算完成。

## 3. 现实优先协议（reality wins）

真机观察与规范库冲突时，**永远**：
```text
① 停下当前实现分支
② 用最小实验固定事实（写成 fixture/probe 记录）
③ 更新被证伪的卷（VOL-02 优先——它是唯一的事实基线）+ 对应子系统卷
④ DECISIONS.md 记录：日期/证据 ref/旧假设/新事实/影响面
⑤ 再继续实现
```
禁止：静默偏离（代码与库不一致）；为让设计成立重跑实验直到"凑出"想要的结果；口头记录不落盘。
反向规则同样成立：**不要因为实现麻烦就改契约**——改契约需要证据，不是需要便利。

## 4. 决策树

**「我不知道下一步做什么」**
→ 读 PROGRESS.md 的"下一步"；为空 → 读 VOL-21 当前里程碑 gate，找第一个未满足项；仍不明 → 回 VOL-20 找未跑 probe；全跑完 → 你在实现期，回 VOL-03 §6 组件清单找未完成组件。
**→ 30 分钟仍找不到**：停止即兴，向用户报告当前状态与阻塞点（这是允许的，比迷失强）。

**「一个平台事实和我预期不符」** → §3 现实优先协议。

**「测试失败」** → 先判断类别：契约实现错（改代码）/ 契约本身错（按 §3 改卷）/ 环境（flake：重跑一次并记录，两次必查根因）。禁止为过测试弱化断言。

**「S 损坏/降级」** → VOL-07 §7 流程；期间只做只读工作；修复记录进 DECISIONS。

**「两卷内容冲突」** → 按优先级：VOL-01 宪章 > 本卷流程 > VOL-02 事实 > 子系统卷 > 例子；并在 DECISIONS 记录待修卷。

**「用户要求的和宪章冲突」**（例如要求加 per-turn 注入）→ 明确指出违反的条目与理由，让用户显式 override 并记录；不静默执行。

## 5. 决策日志格式（DECISIONS.md）

```markdown
## D-<n> <一句话标题>（日期）
- 证据：<probe/测试/文献 ref>
- 决策：<选择>
- 备选与弃因：<…>
- 影响：<改了哪些卷/组件；谁依赖它>
```
只记"改了系统或契约"的决策；日常工作进 PROGRESS。

## 6. 停下来等用户的条件（其余一切自主推进）

① 网页登录/2FA/CAPTCHA/账户授权；② 真实不可逆系统变更（删除大块数据、外部发布、发送邮件/消息）；③ live trading/withdrawal/production deployment；④ 需要修改用户网络/TUN/代理策略；⑤ 需要用户提供才能有的授权/凭据；⑥ ProgramData 旧 hook 移除等系统级清理（P-HYG1 的确认步骤）。
除以上：先调查、做可逆实验、继续推进。**禁止伪造成功**；能力不成立就标 `UNAVAILABLE/OBSERVED_DIFFERENT`。

## 6.5 live 供应商测试预算（D-15，2026-09-02 教训）

用户配额是真实成本：探针类 agent 对 live 供应商（Kimi/Codex 等）**每次探针最多 2 个真实回合**（一词提问，禁止无记录重跑）；实现类 agent **只准对 stub 测试**，live 验证是独立且显式限额的一步（同样 ≤2 回合）；遇 403/429 立即停止，绝不重试循环；每次 live 回合事后必须能在该供应商本地日志里数得出来（审计义务）。

## 7. 反模式清单（历史死因，见到即停）

| 死因 | 症状 | 出处 |
|---|---|---|
| 认知 governor 复活 | 想加"每轮检查 root 是否该继续"的 gate/注入 | 旧 LOOP root_turn_governor/sol_tool_gate |
| 调度官僚件 | refill debt/role pool/duty queue/心跳仪式 | 旧 LOOP（真实存在过，VOL-02 §7） |
| 幻影组件 | 为"将来可能需要"建 manager/第二 roster | 审计 P0-4（4 个幻影项） |
| 双重注册 | 同一 hook 两处注册"保险" | 旧 LOOP global_hooks + ProgramData 镜像 |
| 伪造成功 | 文档说有但没实测的能力被当作依赖 | 宪章禁令 3 |
| 单体文档依赖 | 靠重读 2322 行母稿工作 | 本库存在的原因 |
| 凭便利改契约 | "先这样跑通，回头再改文档" | §3 禁止 |
| 30=KPI | 为了并发数造 filler packet | I26 / C 编译器教训 |
| provider 状态当权威 | 拿 Codex thread status 决定 lifecycle | #37047/#34220/#37856 |
| prompt 当隔离 | "我在 prompt 里写了不许联网" | I36 |

## 8. 状态外置规则（PROGRESS.md 的纪律）

- 每个可验证单元完成后**立即**更新（不要攒）；字段：阶段/已完成（带证据 ref）/阻塞/下一步/环境快照指针。
- PROGRESS/DECISIONS 是**唯一**的跨会话记忆——不依赖模型回忆、不依赖聊天历史。
- 里程碑切换时：PROGRESS 归档快照到 `runs/` 并开新段落；VOL-02 若有实测更新同步刷新版本号。

## 9. root 侧操作循环（我自己作为 ZCode root 使用 zloop 的标准动作）

```text
zloop run start "<objective>"        # 前台！bind-token
zloop research start spec.json       # 后台可
（用 Browser 做 C2C-P，然后 research await）
zloop stage begin …                  # 锁 base（dirty ⇒ BLOCKED_DIRTY_BASE，先提交用户工作）
zloop wave propose packets.json && zloop wave start W1
  # <5min: wave run；5-10min: 前台 await；>10min: 结束 turn 等 notification
（notification 到达 → wave await 消费 bounded 结果）
（materialize 自动；需要则再 wave / stage promote）
zloop verify-run                     # Goal 完成判据
```
禁止：忙轮询 status；绕过 zloop 直接 git commit 到 canonical（会造成 HEAD 漂移 → promotion BLOCKED，白干）；在 wave 期间手动 stash/reset 用户工作区。
