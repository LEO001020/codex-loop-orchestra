# VOL-07 — 控制数据库 S：SQLite 权威面（fail-closed）

> **ZLoop Spec v1.0** · 卷 07/22 · 层级 L2 · 依赖：VOL-02, VOL-04
> 取代 Gen-8 §14。S 是 correctness-critical 生命周期权威：**S 写失败 ⇒ 不 launch、不物化、不晋升**（I4）。S ≠ H0。

---

## 1. 定位与反定位

- S 管：lifecycle、session binding、租约、revision fencing、promotion intents、controller epoch。
- S 不管：大 payload（进 H0 blob）、防篡改证明（未签名 hash-chain 不提供真保证——**旧 LOOP 树中本无 hash-chain；它是被本设计否决的 Gen-6/7 提案**）、认知状态。
- 选 SQLite 的理由：lifecycle mutation 天然要求 `validate version + INSERT event + UPDATE state + lease fencing` 单事务原子完成；自实现 torn-write/cursor/Windows 原子替换等于重交学费（旧树学费清单见 VOL-02 §7）。

## 2. Authority placement（不可协商）

- 位于**单一 authority host 的本机可靠文件系统**：`~/.zloop/projects/<id>/control.sqlite3`。
- 禁止：NFS/SMB/网络同步盘/DrvFs 跨语义共享。**"两个进程都看得见一个路径"≠该路径可作 SQLite authority**（WAL 要求同主机共享内存，sqlite.org/wal.html）。
- ZCode Remote/WSL 使 hook 与 supervisor 不同主机时：把 supervisor 移到 hook 执行主机，或走已验证的本地 IPC；否则降级（放弃自动 Session recovery / first-class wave），不得为共享把 DB 放网络 FS。
- M0 记录：hook / Terminal / zloop CLI / supervisor 各自实际运行的 OS、主机、路径命名空间（P-SQL2）。

## 3. 版本闸门与 PRAGMA 配置 [P0-1，I22]

**WAL-reset 事实**：受影响 3.7.0–3.51.2；修复 3.51.3；backport 3.50.7/3.44.6；当前最新 3.53.4。**本机 stdlib = 3.50.4 ⇒ 受影响且低于 backport**。

启动时（每次打开 S 前）执行并记录：

```python
v = sqlite3.connect(db).execute("SELECT sqlite_version()").fetchone()[0]
```

决策规则（实现为 `db.py:open_control()` 内的硬逻辑）：

```text
if sqlite_version() 满足 (>=3.51.3 或 ==3.50.7 或 ==3.44.6 或 >=3.53.x):
    journal_mode=WAL; synchronous=FULL
else:
    journal_mode=DELETE; synchronous=EXTRA      # 本机 3.50.4 的 v1 默认路径
    doctor 警告："WAL available after SQLite upgrade (see VOL-07 §3)"
```

- 修复途径（M0 评估后择一，记 DECISIONS）：(a) `pysqlite3` wheel（若 cp314/Windows 可用）；(b) 捆绑 sqlite.org 3.53.4 DLL 并显式加载；(c) 保持 DELETE+EXTRA（S 写频低，性能可接受）。**在版本闸门通过前，禁止手工 `PRAGMA journal_mode=WAL`。**
- 永远：`foreign_keys=ON`、`busy_timeout=5000`；禁止为性能设 `synchronous=OFF`。
- 每次启动跑 `PRAGMA quick_check`；`zloop doctor` 跑 `integrity_check`。

## 4. 单 Controller（I5, I43；**D-8 重设计：禁止长持 OS 锁**）

剃刀审计发现的 **P0 自相矛盾**：若 wave 长进程持独占 `run.lock`，而 `wave cancel` 也要进 S 事务，控制面自我锁死。v1 修正为**所有权 = runs 表内的 controller 字段（CAS），无长持 OS 锁**：

```text
1. supervisor 启动：单事务 CAS claim controller（expected：controller_nonce IS NULL，
   或旧 owner 的 (pid, pid_start_time) 已被机械证明死亡）
2. 每个 lifecycle mutation 在同一事务内验证 controller_nonce == mine
3. 外部 cancel 只写 runs.cancel_requested = 1 —— 它是 command input，不是 transition
4. owner 在下一个 loop tick 观察 cancel_requested → CANCELLING → interrupt（bounded）→ CANCELLED
5. crash 接管：证明旧 owner 死亡 → CAS 新 nonce → reconcile dangling intents
   （launches.intent_state ∈ ('INTENDED','BOUND','RUNNING','AMBIGUOUS')、
    promotion_intents.state='INTENDED'，against physical oracles：VOL-12 §7 / VOL-11 §5）
```
- `BEGIN IMMEDIATE` 已序列化全部事务；短 CLI mutation 不需要 OS 锁。
- TTL/heartbeat 只用于 UI 卫生；系统休眠、长 GC、时钟跳变都不得制造第二 controller。
- `controller_epochs` 降级为审计日志（保留可选），不再是所有权机制；`run.lock` 保留为可选的短临界区辅助，**任何进程不得跨分钟持有它**。

## 5. 事务纪律

- 所有 mutation 走 VOL-04 §4 配方（validate→event→state 同事务）。
- 只有一个写进程（持 run lock 者）；`status/history/binding` 等读连接并发允许。
- 禁止"先写 event 再慢慢改 state"的两段式——那正是旧类 bug 的形态。

## 6. 备份与恢复

- 触发：每个 CLOSED Stage / 每次 PROMOTED 之后（低频在线备份，SQLite backup API），文件名含时间戳 + schema version + sha256。
- 正常运行**不**为每 event 备份。
- 恢复 drill（M10 演练项）：故意损坏主库 → fail-closed → 从最新一致 backup 恢复 → 依据 Git/backend/H0 physical oracles 对账补齐 → 继续。不得"跳过坏页继续 launch"。

## 7. 损坏语义（fail-closed 具体化）

```text
integrity_check / quick_check 失败，或打开异常：
  1) 所有 zloop mutation 命令返回 exit 3 + "S_DEGRADED"（不猜、不绕过）
  2) zloop doctor --repair 引导：最近 backup + oracle 对账报告
  3) 修复完成前：research 只读可用；wave/promote/attach 一律拒绝
```

## 8. 测试要点（详见 VOL-18）

WAL/DELETE 双模式跑全 chaos 集：commit 前后 kill、AV 锁、disk full、进程树 kill、multi-reader/single-writer、torn write、备份恢复、epoch 接管、双 supervisor 恰一可写、网络 FS 负测试（拒绝打开）。
