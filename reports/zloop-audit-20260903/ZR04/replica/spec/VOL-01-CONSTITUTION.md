# VOL-01 — 宪章：不变式、禁令与证据纪律（常驻）

> **ZLoop Spec v1.0** · 卷 01/22 · 层级 L0（常驻） · 依赖：VOL-00
> 取代 Gen-8 §0–§3、§30、§35。本卷是全库的最高法：任何卷、任何实现与之冲突时本卷获胜。

---

## 1. 使命（一段话）

ZLoop 不替代 ZCode 的认知，只补充四件事：**可恢复的过去**（H0/H1/H2）、**信息带宽**（Research Broker）、**异种反证**（C2C）、**隔离并发与崩溃一致性**（S + supervisor + workspace fencing）。薄的是模型可见的认知面，厚的是模型看不见的正确性冷路径。ZCode 继续像 ZCode 一样思考；ZLoop 在它看不到的地方保存时间、扩大信息、隔离物理世界、保证崩溃后确定恢复。

## 2. 所有权模型（四权威 + 两服务）

| 角色 | 拥有 | 永远不得 |
|---|---|---|
| **ZCode root（认知权威）** | 目标解释、假设、证据综合、阶段边界、前沿分解、C2C 分歧裁决 | 直接改 S/canonical lifecycle |
| **H0/H1/H2（恢复权威）** | 可观察、脱敏后的过去 | 决定过去"意味着什么"；当 task authority |
| **supervisor（物理执行权威）** | worker 生命周期、workspace lease、revision fencing、acceptance、staging、promotion | 做认知决策 |
| **机械现实（正确性权威）** | compiler/test/property/runtime/backtest/dataset hash | 被模型投票否决 |
| Research Broker（信息服务） | 外部事实获取、bounded evidence manifest | 决定架构；接触项目秘密 |
| C2C Auditor（异种审计） | 计划/结果的独立批评 | launch worker、改 scope/S、直接 integrate |

**层级固定**：`mechanical oracle > primary external evidence > heterogeneous critique > same-model introspection`。

## 3. 硬禁令（违反任一条 = 项目失败，即使测试全绿）

1. 不做全局 PreToolUse root 认知 gate；不规定 root 不得 read/search/test；不每轮注入 Active Work/DAG/roster/状态机摘要。
2. 不把 H1 当 task authority；不用 C2C/Research 直接改 canonical state。
3. 不实现假能力：没有 probe 证明的能力不得出现在文档、代码注释或对外报告中（"禁止伪造成功"）。
4. 不押注未版本化承诺的厂商行为；生产 hard dependency 必须 `DOCUMENTED`，或 `OBSERVED` 且带已测试 fallback。
5. 不逆向 `zcode.cjs`/私有 IPC/asar 内部接口；不从旧 Codex LOOP、`~/.codex` 任何 auth 备份挖 token/key。
6. 不用 Stop hook 做无限 continuation（平台上限 3 次，已核实）。
7. 不自动 stash/reset/clean/commit 用户的 canonical 未提交修改。
8. 不让 worker/model 写 S、H 权威路径、canonical refs。
9. 不为 memory 单独增加模型调用（H1.semantic 只 piggyback 在本来要发生的控制边界上）。
10. live trading / withdrawal / production deploy / 不可逆外部动作永远显式人类授权。
11. 不复活旧 LOOP 的官僚件：refill debt、role pool、duty queue、roster 对账、Blackboard 注入（其真实形态见 VOL-02 §7 与 VOL-17 §6）。
12. `zloop` 不注册一堆同义 MCP 工具面；root 通过普通 Terminal CLI 使用一切。

## 4. 生产不变式（I1–I44，全部须有自动测试或 contract probe）

I1 `disable(ZLoop)` ⇒ 恢复 native ZCode 语义
I2 kill(model context) ⇒ 已捕获且允许持久化的 H0 存活
I3 H0 写失败 ⇒ `history_degraded`；native 认知继续
I4 S commit 失败 ⇒ 停止一切 lifecycle mutation（不 launch、不 promote）
I5 同一 run 任一时刻恰一个 controller 可 mutate
I6 结果被接受当且仅当 `stage_revision ∧ packet_revision ∧ active_launch_id` 三者精确匹配
I7 stale attempt/launch/revision ⇒ 只进 H0 证据，永不 materialize/promote
I8 依赖只消费已 MATERIALIZED 的 private stage snapshot lineage
I9 并发可写资源不相交或显式租约排序
I10 worker/model 不能直接改 S/control DB/canonical refs
I11 H2/派生索引删除 ⇒ 可重建
I12 web/C2C（external_untrusted）不能改 scope/risk/S/promotion policy
I13 raw secret 永远不在 redaction/hash 之前落盘
I14 `当前验证现实 > 重建的 H1.machine > H1.semantic prose`
I15 H1.semantic 的 facts/decisions 必须有可解析 evidence ref，否则降级 unverified_notes
I16 C2C evidence ≠ task authority；risk floor 由 host 控制 blocking
I17 NORMAL C2C outage 不得阻塞强机械工作；HIGH/CRITICAL 阻塞晋升除非人类豁免
I18 SearchHealth ≠ InferenceHealth（独立熔断）
I19 native subagent 生命周期 ≠ first-class worker 生命周期
I20 生产 vendor 依赖必须 DOCUMENTED 或 OBSERVED+fallback
I21 Git/外部系统是 physical-effect oracle；S 仍是逻辑生命周期权威
I22 **[P0-1]** SQLite authority 永不放网络 FS；production WAL 要求运行时 `sqlite_version()` 含 2026-03 WAL-reset 修复（≥3.50.7 backport / ≥3.51.3；本机基线 3.50.4 = 受影响，见 VOL-02 §3.4）
I23 共享 dataset/cache 输入 immutable/content-addressed 或租约保护
I24 无 worker 读其他 worker 的 mutable private workspace
I25 G-COG 的 RETURN 与 WAKE 必须实测；detached-no-wake 不得称为自主递归
I26 10–30 是容量上限；实际 p 由 useful frontier + 实测 J/Pareto cap 决定
I27 Kimi lane 必须见显式 terminal completion；process exit / tool event ≠ success
I28 SessionStart 只恢复 exact session binding；`clear` 默认不恢复
I29 first-class worker 默认 network_policy=none（物理执行边界强制，prompt 文本不算）
I30 canonical HEAD/dirty digest 漂移时禁止晋升；必须 re-stage/rebase
I31 live/不可逆动作永远显式人类授权
I32 绑定必须 exact 单次 claim token；禁止 cwd/latest-run 猜测
I33 H1.semantic 检查点频率跟随 durable handoff，不按固定次数/turn 周期
I34 每个 launch_id 独立 mutable workspace；歧义 crash 的 launch 先 quarantine 再 retry
I35 first-class worker 嵌套 agent 生成被已验证的配置/工具目录关闭
I36 网络策略由执行边界强制；prompt 自律不满足 I29/I36
I37 生产可写 wave 从验证过的 immutable clean Stage base 出发
I38 host 在把 worker delta 套上当前 Stage snapshot 并重跑验收后才 MATERIALIZED
I39 canonical checked-out 晋升保持 ref/index/worktree 一致；禁止裸 update-ref
I40 HIGH/CRITICAL 外部声明需要 promoted/verified primary evidence 或显式 unverified 例外
I41 C2C transport/coverage 实测；zloop host 不得声称能驱动 ZCode Browser
I41b C2C provider/model 身份只在可观察时记录；unknown 不算已证跨家族独立
I42 research lanes 默认运行在 canonical 可写项目/秘密环境之外
I43 S 的单 owner = controller token CAS（含 pid+pid_start_time 活性证明；D-8：禁止长持 OS 锁）；TTL 永不单独决定 owner
I44 provider thread 状态只是物理证据；stale-active/resume 歧义不得复活旧 launch

## 5. 证据纪律

- 六档状态：`DOCUMENTED / OBSERVED / EXPERIMENTAL / UNKNOWN / UNAVAILABLE / OBSERVED_DIFFERENT`。
- 生产依赖条件：`DOCUMENTED`，或 `OBSERVED` 且架构有显式安全 fallback。
- 每条承重契约落为机器可检记录（`docs/VENDOR_CONTRACTS.md` + `artifacts/capabilities/manifest.json`，schema 见 VOL-20 §2）。
- 厂商版本变化 ⇒ 重跑相关 probe；"上周能用"不是契约。
- 三类证据不混用：分布式正确性靠故障注入；Harness 收益靠 A/B；厂商能力靠当天真机。

## 6. 审计修正登记（2026-09-02 独立审计，已并入本库）

| 编号 | 内容 | 落点 |
|---|---|---|
| P0-1 | 本机 SQLite 3.50.4 落 WAL-reset 受影响区间（3.7.0–3.51.2）且 < backport 3.50.7 ⇒ 版本闸门激活，M0 必须解决 | VOL-07 §3, VOL-20 P-SQL1 |
| P0-2 | 平台契约补录：前台 Bash 单调用 ≤600,000ms；后台任务退出以 task-notification 重新唤起 root（2026-09-02 实测）⇒ await 参考实现 = background+notification | VOL-02 §3.2, VOL-09 §7 |
| P0-3 | `C:\ProgramData\OpenAI\Codex\requirements.toml` 旧 LOOP 机器级 hook 注册仍然活着，会命中 ZLoop 的 Codex worker ⇒ M0 前清点下线 | VOL-17 §6, VOL-20 P-HYG1 |
| P0-4 | Gen-8 §20 旧代码对照表含 4 个幻影项（vector DB/RAG、Blackboard dump、Compatibility Gateway、IPybox-as-memory-authority 均不存在或错位）⇒ 提取表以实际代码树为准 | VOL-02 §7 |
| P1-5 | hook 事件为 7 个（补 PreToolUse/PermissionRequest 捕获）；SessionStart source 含 `resume`；输出为 strict-schema JSON | VOL-05 |
| P1-6 | 安装走用户级 config 或 plugin（workspace 级 hooks 被忽略） | VOL-05 §2 |
| P1-7 | hook 延迟硬 gate（inline 执行；Windows+Python+AV 实测 p95） | VOL-05 §7 |
| P1-8 | worker/research 子进程环境 allowlist 构造（本机 root env 有 ALIBABA_TOKEN_PLAN_API_KEY、ZAI_OAUTH_CLIENT_ID 等） | VOL-17 §3 |
| P1-9 | Windows 进程树击杀用 Job Object（worker-host 子进程模型） | VOL-12 §6 |
| P1-10 | 验收 batching + bisect + (tree,oracle) 缓存，控 O(k) 物化成本 | VOL-10 §5 |
| P1-11 | C2C 响应 bounded 回注 root；ChatGPT 自动化 ToS 风险入档 | VOL-16 §5/§8 |
| P2-12 | 勘误：Kimi moonshot_search/fetch 是 service 配置段；`--out DIR`；两个 Anthropic post 日期；RLM 引用错位；TokenBudget 措辞 | VOL-02 §5–§6 |
| P2-13 | bind-token 只能前台 CLI 发射（后台 tool_response 无 stdout） | VOL-05 §4.3 |
| P2-14 | 并行 tool call ⇒ 并发 PostToolUse 竞态测试 | VOL-20 P-HK1 |
| P2-15 | 记录 subagent 并发 env 旋钮（CLAUDE_CODE_MAX_*） | VOL-20 P-NAT1 |
| P2-16 | 本机版本漂移入 manifest（codex 0.147.0/kimi 0.28.1） | VOL-02 §3.5 |
| P2-17 | 多项目全局配额治理 v1 明确 out-of-scope | VOL-14 §6 |

## 7. 工程纪律（新增任何机制前的六问）

```text
它解决了哪个真机 observed failure？
它是否进入模型每轮 context/tool surface？
能否改成 host-side mechanical invariant？
能否用现有 CLI/文件/Git/测试解决？
有 A/B 或 chaos 证据吗？
关掉它会怎样？
```
答不上来就不实现。每个 subsystem 的 README 必须能回答"它解决了哪个已观察 failure/明确能力缺口"，答不出就删。
