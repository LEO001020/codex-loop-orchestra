# VOL-03 — 架构总览：尺度阶梯、认知闭环与运行拓扑

> **ZLoop Spec v1.0** · 卷 03/22 · 层级 L1 · 依赖：VOL-01, VOL-02

---

## 1. 尺度阶梯（大→小的可解耦结构 → 对应卷）

| 尺度 | 结构 | 卷 |
|---|---|---|
| S1 系统 | ZCode 认知内核 + ZLoop 外挂体系 | 本卷 |
| S2 平面 | 认知面（root）/ 恢复面（H0/H1/H2）/ 物理执行面（supervisor+worker）/ 正确性面（oracle）/ 信息面（Research）/ 异种面（C2C） | 03, 05–06, 09–13, 14, 15, 16 |
| S3 组件 | zloop CLI、zloop-hook、S control DB、wave 引擎、materialization、promotion、backend、workspace、scheduler、broker、auditor | 05–16 |
| S4 模块 | 仓库内 Python 模块（见 §6） | 04 + 各卷 |
| S5 接口 | CLI 命令面、hook 输入/输出、Backend ABC、文件/JSON 格式 | 04, 05, 12 |
| S6 数据结构 | S 表、NDJSON envelope、blob CAS、packet/stage/launch 记录 | 04 |

## 2. 系统上下文

```text
┌─────────────────────────── Windows 主机（authority host）────────────────────────────┐
│                                                                                       │
│  ZCode Desktop (GLM-5.3 root)                                                          │
│   │  Bash/Read/Write…工具        Browser(main-agent only)      Agent(native subagent) │
│   │   `zloop …` CLI  ←──────────── C2C-P/A（root 驱动 ChatGPT Web fresh thread）        │
│   ▼                                                                                    │
│  zloop CLI (短进程, 每 mutation 一个事务) ──┐                                           │
│  zloop-hook (ZCode 每 event 拉起的微型进程) ─┼──► ~/.zloop/projects/<pid>/             │
│  zloop wave run (长进程, 持 run lease)      ─┘    ├─ control.sqlite3  ← S (fail-closed)│
│   │  派生 zloop-worker-host ×p (Job Object)        ├─ history/…ndjson  ← H0 (fail-soft)│
│   ▼      每个 host 内跑 Codex SDK turn             ├─ blobs/sha256/…                 │
│  first-class workers (Codex, 每 launch 独立 workspace, network=none)  ├─ workspaces/<stage>/<packet>/<launch>/ │
│                                                      └─ runs/ research/ c2c/        │
│                                                                                       │
│  Git canonical repo（用户工作区, checked-out） ◄── ff-only promotion（唯一写通道）        │
│  Research lanes: Luna(Codex auth) / Kimi(server/CLI) — 独立 cwd, 无项目秘密            │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

## 3. G-COG：认知闭环形态（平台事实已定调）

架构不变式：**并行动作/证据在逻辑步 t 产生，必须在不要求用户重述任务的前提下，成为之后某次串行决策的因果输入**。不要求"同一个产品 round"。

| 模式 | 判据 | 平台现状（2026-09-02） |
|---|---|---|
| A `SYNC_REENTER` | 活动 Goal 的一个 round 内前台等待并同轮综合 | 仅适用于 **<10min** 的 wave（前台 Bash ≤600s 上限 [P0-2]） |
| B `WAITABLE_CROSS_ROUND` | 真实 completion wake 或单次有界 await | **默认预期**：后台任务退出→task-notification 重新唤起 root（实测 [OBSERVED]）；M0 需确认 `/goal` round 吸收 notification（P-GC1） |
| C `DETACHED_NO_WAKE` | 无 wake，只能靠用户/授权自动化唤醒 | B 失败时的降级；诚实记录 human wake cost |
| D `INCOMPATIBLE` | Goal×wave 共存导致状态损坏 | 硬回退：Goal/Loop 双模式 + 显式交接 |

**wave 使用的决策规则（root 侧，写入 Playbook）**：预计 <5min → `zloop wave run`（前台）；5–10min → 前台 `wave await` 一次；>10min → `zloop wave start` + root 结束当前 turn 或做其他工作，**等待 notification 唤醒后** 单次 `wave await/status` 消费结果。禁止忙轮询 status。

## 4. 因果循环（Stage Cycle 主线）

```text
G_t: root 串行认知（/goal round）
  → R_t: 并行 research（Broker lanes）/ native helpers
  → G'_t: 串行综合（吸收 bounded Evidence Manifest）
  → C2C-P_t: 异种 fresh-thread 计划审计（与 Research 并发隐藏墙钟）
  → G''_t: 裁决 + StageCommit（H1.semantic piggyback）
  → L_t: 冷隔离并行执行（wave, first-class workers）
  → O_t: 机械现实（compiler/tests/runtime/backtest）
  → host MATERIALIZES 已验收 delta 进 private stage snapshot
  → G_{t+1}: 串行综合（新 snapshot 上的证据；多 wave 可重复 L→O→G）
  → C2C-A_t: 异种 fresh-thread 结果审计（HIGH/CRITICAL 硬门）
  → P_t: 受控 canonical promotion（ff-only）
  → H_t: 可恢复检查点（H0 全程 fail-soft 记录）
  → 目标未完成 ? 重复 : 结束
```

## 5. 运行拓扑（进程模型）

**没有常驻 daemon。**

| 进程 | 生命周期 | 职责 | 死亡影响 |
|---|---|---|---|
| ZCode Desktop | 用户会话 | root 认知；触发 hooks；持有 Browser | H0 停止增量；S 不受影响 |
| `zloop <cmd>` | 秒级短进程 | 单个 S mutation（事务+run lock+epoch 校验） | 事务原子性保证无半状态 |
| `zloop wave run/start` | 分钟–小时长进程 | 派生 worker、收集、物化；持有 **controller token（S 内 CAS，D-8）** | dangling intents 由接管者 reconcile |
| `zloop-worker-host` | = worker 生命周期 | 每 launch 一个，包住 Codex SDK turn；置于 Job Object [P1-9] | Job Object 保证进程树回收 |
| `zloop-hook` | 毫秒–秒级 | H0 capture / bind-token claim / SessionStart 恢复注入 | fail-soft：任何异常 exit 0 |
| Research lanes | 分钟级 | Luna/Kimi 查询 | lane 熔断，不影响主推理 |

**单 owner**（D-8 重设计）：所有权 = `runs` 表内 controller 字段的 CAS claim（nonce + pid + pid_start_time），**禁止任何进程长持 OS 锁**；外部 cancel 只写 `cancel_requested`，由 owner 在下一 tick 执行 CANCELLING→interrupt→CANCELLED；crash 接管需先机械证明旧 owner 死亡再 CAS。短 CLI mutation 只靠 `BEGIN IMMEDIATE` 串行。TTL 只做 UI 卫生（I43）。

## 6. 组件清单（组件 / 拥有 / 不得 / 卷）

| 组件 | 拥有 | 不得 | 卷 |
|---|---|---|---|
| zloop CLI | 全部 root-facing 操作面 | 认知决策；暴露多义工具 | 04,09,22 |
| zloop-hook | H0 capture、bind claim、恢复注入 | 网络/模型/扫描；non-zero exit | 05 |
| H0/H1/H2 | 可观察历史、检查点、回查 | 当 task authority | 06 |
| S (SQLite) | 生命周期、绑定、租约、revision、intent | 存大 blob；放网络 FS | 07 |
| Stage 引擎 | Stage FSM、risk floor、stage base | 绕过 dirty-base 阻断 | 08 |
| Wave 引擎 | packet 验证、launch、fencing | 收非 active launch 的结果 | 09 |
| Materialization | delta 重建、host 验收、snapshot 推进 | 信任 worker commit | 10 |
| Promotion | ff-only 晋升、CAS、对账 | 裸 update-ref、覆盖用户改动 | 11 |
| CodexBackend | worker 派生/中断/收集 | 把 provider status 当权威 | 12 |
| Workspace | worktree_fast/clone_strong | 让 worker 触 Git 管理 | 13 |
| Scheduler | frontier 选择、p_class cap | 为数字造 filler work | 14 |
| Research Broker | Luna/Kimi lanes、evidence manifest | 接触项目秘密；决定架构 | 15 |
| C2C Auditor | prepare/record、P/A 线程、coverage | launch/integrate/promote | 16 |
| Security | 数据分类、redaction、allowlist env | denylist 式排除 | 17 |

仓库模块布局（v1，全部 Python）：

```text
zloop-gen8/
  pyproject.toml            # deps: openai-codex；entry: zloop, zloop-hook, zloop-worker-host
  src/zloop/
    cli.py  hook.py  db.py  evidence.py  checkpoint.py
    stage.py  wave.py  materialize.py  promote.py  sched.py
    backend/{base.py,codex_sdk.py,app_server.py}
    workspace.py  oracle.py  integration.py  security.py  redact.py
    research/{broker.py,luna.py,kimi_server.py,kimi_cli.py,fetch.py}
    c2c.py  supervisor.py  doctor.py
  commands/loop.md          # /loop 用户入口（若 ZCode command 形式需要）
  tests/  docs/  artifacts/capabilities/
```

## 7. 端到端走查（一个 substantive Stage 的标准时序）

1. root：`zloop run start "<objective>"`（**前台**）→ 输出首行 `ZLOOP_BIND_TOKEN=<nonce>` → PostToolUse hook 原子 claim → binding 建立（I32）。
2. root 判断 research_required → `zloop research start spec.json`（后台可）→ root 并行做 C2C-P（Browser 新线程）→ `research await` 消费 bounded manifest。
3. root 综合 → `zloop stage begin`（host 计算 risk_floor、锁定 expected_head+dirty digest；dirty ⇒ `BLOCKED_DIRTY_BASE` [I37]）。
4. root 提出 wave：`zloop wave propose packets.json` → host 验证 schema/DAG/scope/资源。
5. `zloop wave start W1` → 每 packet 建 launch（新 workspace、network=none、agents 禁用）→ root 按 §3 规则等待/被唤醒。
6. worker REPORTED → host 在**当前** private snapshot 上重套 delta + 重跑验收 → ACCEPTED → MATERIALIZED（snapshot_{k+1}）。
7. root 在新 snapshot 上综合 → 需要则再 wave（回到 4）。
8. 前沿完成 → final staged candidate → integration oracles → STAGED。
9. C2C-A（HIGH/CRITICAL 硬门）→ root 裁决。
10. `zloop stage promote` → 校验 HEAD/dirty 未漂移 → `merge --ff-only` → post-oracle → PROMOTED → StageClose（H1 检查点若语义有变化）。

## 8. 故障域地图

| 故障 | 影响 | 恢复 |
|---|---|---|
| ZCode app 崩溃/compaction | 丢失 in-context 细节 | SessionStart(compact) 恢复注入（exact binding）；H2 回查 |
| `zloop wave run` 被杀 | worker 可能仍在跑 | 新 controller epoch+1；dangling launch 按 VOL-12 §7 对账（quarantine 优先） |
| worker 崩溃 | 单 packet FAILED | 同 revision attempt+1、新 launch、新 workspace |
| S 损坏 | 一切 mutation 停止 | fail-closed → 最新 backup + physical oracle 对账（VOL-07 §7） |
| Git 已晋升但 S 未写 | dangling promotion | VOL-11 §5 对账表（物理 oracle 补账） |
| 用户 wave 期间改 canonical | 不阻止 | promotion 时 HEAD/dirty 漂移 ⇒ REBASE_REQUIRED/BLOCKED |
| 旧 LOOP 机器级 Codex hooks | 命中每个 Codex worker | **M0 前清场** [P0-3]；P-HYG1 |
