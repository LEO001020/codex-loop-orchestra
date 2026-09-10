# VOL-09 — Wave / Packet / Launch：模型提议、host 验证、四级 fencing

> **ZLoop Spec v1.0** · 卷 09/22 · 层级 L2 · 依赖：VOL-04, VOL-07, VOL-08
> 含审计修正 P0-2（root 侧等待协议）。

---

## 1. 提议 → 验证 → 启动

```text
root: zloop wave propose <packets.json>     # VOL-04 §8 schema；CLI 做 schema 校验后写 S(PENDING)
host 验证（wave start 时最终裁决）：
  - stage 处于 EXECUTING ∧ stage_revision 一致
  - DAG：无环；depends_on 存在且已 MATERIALIZED 到目标 lineage（未满足→PENDING 挂起）
  - write_scope 两两不交（或经同一 resource lease 串行化）
  - resource_scope 可用（dataset hash 存在、lease 可获得）
  - risk_class ≥ 继承的 stage risk floor
  - network_policy 合法（默认 none；allowlist 引用已定义条目）
root: zloop wave start W1 → host 为每个 READY packet 建 launch（VOL-12）
```

## 2. Packet 语义规则（修订的核心）

| 身份 | 何时变化 | 结果处置 |
|---|---|---|
| `packet_revision` | goal/write_scope/acceptance/constraints/deps/risk **实质修改** | +1；旧 revision 一切结果永不 integrate [I7] |
| `attempt` | 同 packet_revision 的物理重试 | +1（配新 launch） |
| `launch_id` | 每次实际 spawn | 全新 uuid + **全新 workspace** [I34] |
| `stage_revision` | Stage 语义重规划 | +1；旧 revision active launches 尽力 interrupt，立即失去 materialization 权限；无法取消的旧进程只进 H0/quarantine |

**接受结果的必要条件（I6，全部 AND）**：
```text
result.stage_revision == current.stage_revision
AND result.packet_revision == current.packet_revision
AND result.launch_id == current.active_launch_id
AND packet.state 期望该 launch（RUNNING 期）
```
迟到的同 revision attempt-1 结果（attempt-2 已启动）⇒ quarantine 进 H0，不改 S/staged/canonical。

## 3. DAG 与并发约束

- 依赖是对 **private Stage snapshot materialization** 的依赖（I8）。
- write_scope 冲突的两 packet：要么由 host 拒绝同 wave 并行，要么显式 `depends_on` 串行。
- 共享 mutable resource（DB/cache/global registry）必须 `resource_scope` 显式 + EXCLUSIVE/SHARED lease（VOL-04 §3）。

## 4. Worker prompt envelope（worker 看到什么）

```json
{"run_id":"R012","stage_id":"S03","stage_revision":3,
 "packet_id":"P07","packet_revision":4,"attempt":2,"launch_id":"L9c1f0e2a4b7d",
 "goal":"…","write_scope":["src/foo/**"],"acceptance":["pytest tests/foo -q"],
 "constraints":["…"],"base_stage_snapshot":"snap_2",
 "context_bundles":["blob:sha256:…"],       // root 挑选的只读上下文（hash+data_class 标注）
 "max_turns":20,"network_policy":"none"}
```
worker **看不到**：S/H0 路径、其他 packet、canonical repo 凭据、任何 secret（env allowlist，VOL-17 §3）。worker 的自报（commit/tests/summary）一律视为 evidence，不作 authority（VOL-10）。

## 5. Launch 生命周期与 workspace

```text
launch_intent(S 事务) → workspace 创建（VOL-13，每 launch 独立目录）
→ worker-host 派生（Job Object，VOL-12 §6）→ worker_bound(handle, pid, pid_start_time)
→ RUNNING → TERMINAL(completed/incomplete/failed) | AMBIGUOUS → QUARANTINED
```
- backend_handle（Codex thread id）只是物理证据 [I44]。
- retry：同 packet_revision、attempt+1、新 launch_id、**新 workspace**；旧 workspace 转只读 quarantine 后延迟回收。
- crash/歧义处置见 VOL-12 §7（先 interrupt+核对身份、再隔离、只在"明确 terminal 且 exact launch 仍 active"时收集）。

## 6. Network policy

- first-class coding worker 默认 `network_policy=none`，且必须是**执行边界实证**（I29/I36）：workspace_write 的 `network_access` 默认 false（已核实，仍需 P-CDX3 物理 canary）。
- `allowlist:<id>` 例外（package install、API probe packet）：条目 = {host, port, direction, ttl}，host 在 probe 时生成最小放行；Research lane 的网络独立开放（VOL-15），不共享项目 cwd/秘密。

## 7. root 侧等待协议 [P0-2，平台契约写死]

| 预计时长 | 动作 |
|---|---|
| <5 min | `zloop wave run W1`（前台，Bash timeout ≤600,000ms 硬上限） |
| 5–10 min | `zloop wave start W1` → 前台单次 `zloop wave await W1 --timeout 540` |
| >10 min | `zloop wave start W1` → **结束当前 turn 或做其他工作** → 后台任务完成的 task-notification 唤醒 → 单次 `wave await/status` 消费 |

- **禁止忙轮询 status**；`await` 语义 = 阻塞至 wave 终态或超时（前台受 600s 限制）。
- `wave start` CLI 本身可后台运行：其完成 notification 即"wave 结束"的 wake（G-COG-B）。
- G-COG 模式由 M0 P-GC1 定档；若 C/D 档，改由用户/授权自动化唤醒并**如实记录 human wake cost**。

## 8. 取消与 supersede

- `zloop wave cancel W1`（D-8）：**只写 `runs.cancel_requested=1`**（单事务、无需任何锁——它是对 owner 的 command input，不是 lifecycle transition）；owner wave 进程在下一 loop tick 观察到 → CANCELLING → 逐 launch interrupt（bounded timeout）→ workspace quarantine → CANCELLED。owner 已死则由接管 controller 按对账流程处置。
- `stage_revision +1`：旧 revision launches 同上处置；结果只进 H0。
- 用户中断（Ctrl-C / 杀进程）：dangling intents 由下一 controller epoch reconcile（VOL-07 §4），不双 spawn。
