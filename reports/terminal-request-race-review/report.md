# 终态请求竞态测试 — 只读审查报告

审查对象：`E:\codex-LOOP\codex-loop-s-f2` 未提交改动中的
`harness/lifecycle_supervisor.py` 与 `harness/terminal_packet_epilogue.py`
（durable terminal request sequence）。

版本基线（注意：审查期间文件被并发修改，行号以最后读取的版本为准）：

| 文件 | 最后修改 | 说明 |
|---|---|---|
| `harness/lifecycle_supervisor.py` | 2026-08-13 05:14:52 | 本次审查基线 |
| `harness/terminal_packet_epilogue.py` | 2026-08-13 05:20:02 | 本次审查基线（含 `route_terminal_retries` 与 marker 归属检查） |
| `harness/orchestration_epilogue.py` | 2026-08-13 04:51:08 | 旧 CLI epilogue，`__main__` 会调用新消费端 |

审查期间观察到上述文件在 05:09:27 → 05:14:52 → 05:20:02 被连续改写（第一版无
marker 归属检查、无 `route_terminal_retries`）。若继续编辑，需重新核对行号。

## 0. 协议摘要

- 生产端 `schedule_epilogue()`（lifecycle_supervisor.py:468-523）：
  在 `.terminal_epilogue.lock` 下读 `terminal_requests.json` → `requested_seq += 1`
  → 检查 marker（`terminal_epilogue.pid`，含 pid + 进程创建 FILETIME）→
  存活且代次一致则 coalesce；否则 Popen 消费端并把 marker 写为该子进程。
- 消费端 `run()`（terminal_packet_epilogue.py:110-166）：
  在 `.terminal_packet.lock` 下循环：读 requested → 执行
  normalize/statemachine/retry 路由/refill → 在 claim 锁下重读，
  `consumed_seq = max(consumed, requested)`；若最新 `requested_seq <= requested`
  且 marker.pid == 自己 → 删 marker、break；否则再循环一轮。

## 1. 已验证事实（当前代码 + 现场数据）

### 1.1 正常锁路径下 T1 运行时 T2 到达不会丢（F4，结论：PASS）

- 生产端 increment 与消费端“是否追上”判定在同一把 claim 锁内串行化：
  lifecycle_supervisor.py:479-490（increment）与 terminal_packet_epilogue.py:144-160
  （重读 + 删 marker）。
- marker 只在 claim 锁内写（lifecycle_supervisor.py:519-520）与删
  （terminal_packet_epilogue.py:151-157），且删除有归属检查
  （`marker_value.pid == os.getpid()`，153 行），旧 runner 不会再删新 runner 的
  marker。
- 每个调用方先 increment 再 coalesce（487-490 → 501-503），coalesce 不会吞边：
  消费端在 claim 内看到 `requested > 快照` 就继续循环（150 行 → 120 行 while）。
- 可复现时序（正确路径）：
  1. T1：increment 4→5，spawn R，marker=R（全部持 claim）；
  2. R 顶部读到 5，开始工作（124-143）；
  3. T2：increment 5→6，`runner_generation_alive(R)=True` → coalesced；
  4. R claim 段重读 6 > 5 → 循环 → 第二轮消费 6 → 删自己 marker → break。
  T2 不丢。

### 1.2 PID 复用防护是 fail-safe 方向（F4，结论：PASS）

- `runner_generation_alive`（lifecycle_supervisor.py:526-546）用
  `proc_start_ticks`（Windows 创建 FILETIME，549-567 行）比对代次；PID 被复用会
  导致 ticks 不匹配 → 判定不可 coalesce → 重新 spawn，方向安全。
- marker 中 ticks 为 null / 缺失时（495-500 行 TypeError 分支 → `prior_pid=None`）
  同样走重新 spawn。
- 未观察到实际 PID 复用事件（只读审查无法构造）。

### 1.3 现场数据：序列号曾连续 9 次塌缩为 1（F3，旧代码产生）

`data/orchestration/terminal_epilogue.log`：

- 52 次旧格式 `codex-loop-epilogue/v2`（source=cli，04:07–04:50）——旧 CLI/常驻
  supervisor 在跑旧 `orchestration_epilogue.py`；
- 16 次新格式 `codex-loop-terminal-packet/v1`（05:07:02–05:12:18），其中最后 9 次
  （05:10:15–05:12:18）全部 `requested_seq=1, consumed_seq=1`；
- 当前 `terminal_requests.json`（mtime 05:12:18，此后无新运行）内容只有
  `{consumed_at, consumed_seq:1, requested_seq:1}` —— 恰好是消费端
  “两处读取都失败、用默认值回写”的指纹（无 producer 写入的 `source`/`updated_at`
  字段），对应第 2.2 节幻影回写路径。

重要限定：这些运行全部早于当前文件版本（最后编辑 05:20:02）。当前仍在运行的
supervisor 进程启动于 04:54–05:05（旧代码常驻内存），05:07–05:12 的运行由旧代码
与 legacy CLI 产生。**当前版本尚未在生产运行过**，因此“序列塌缩”是旧代码的已
观测事实，不是当前代码的已验证缺陷（见第 4 节假设）。

## 2. 当前代码中的缺陷（按严重度）

### 2.1 P0：claim 段读失败 → 静默回写陈旧状态 → 抹掉已 coalesce 的 T2（F1）

位置：terminal_packet_epilogue.py:144-149。

```python
with file_lock(claim):
    latest = read_json(requests, request_state) or request_state   # 145
    latest["consumed_seq"] = max(...)                               # 146-147
    latest["consumed_at"] = time.time()                             # 148
    atomic_write_json(requests, latest)                             # 149
```

`read_json` 在 OSError/ValueError 时返回默认值（orchestration_common.py:270-275）。
145 行把“顶部快照”当作 `latest` 并**无条件回写**（149 行）：

- 若 claim 段读取失败（Windows 上 AV/索引短暂独占文件——本仓库作者自己在
  lifecycle_supervisor.py:85-92 承认该类瞬时 WinError 5 真实存在），
  producer 在 R 工作期间写入的增量被回滚；
- 随后 150 行 `latest.requested <= requested` 成立 → 151-157 删自己 marker →
  160 break → 进程退出。被抹掉的 T2 边缘没有任何 runner/marker 再处理；
- 下一次 T3 从回滚后的序列继续，T2 边缘**永久丢失**（除非恰好有后续终态事件）。

可复现时序（当前代码）：
1. t0：R 顶部读 `{requested:5, consumed:4}`（120-123）；
2. t1：T2 increment → `{6,4}`（487-490），R 存活 → coalesced；
3. t1→t2：R 执行 124-143（含 retry.py 子进程，最长 30s/包）；
4. t2：R 持 claim，`read_json` 瞬时失败 → `latest`= 顶部快照 `{5,4}`；
5. 149 行回写 → 文件变 `{5,5}`；150 行 5<=5 → 删 marker → break；
6. 结果：requested==consumed==5，T2 边缘消失；期间 FAILED/TIMED_OUT 的 packet
   不再获得 retry 路由，直到下一次终态事件。

最小修复（二选一，推荐前者）：

```python
with file_lock(claim):
    latest = read_json(requests, None)
    if not isinstance(latest, dict):
        raise RuntimeError(
            "terminal_requests.json unreadable under claim; "
            "keeping marker for respawn")
```

或合并时不回退 `requested_seq`：
`latest["requested_seq"] = max(int(latest.get("requested_seq", 0) or 0),
int(request_state.get("requested_seq", 0) or 0))`（仍应 fail-visible 记录）。

生产端同位置的处理是正确的（lifecycle_supervisor.py:480-490 只 update 成功的
读取，失败保持内存态再 increment），消费端应保持一致。

### 2.2 P1：文件缺失时幻影请求（默认 requested_seq=1）（F2）

位置：terminal_packet_epilogue.py:121-123。

```python
request_state = read_json(requests, {"requested_seq": 1, "consumed_seq": 0}) or {}
requested = int(request_state.get("requested_seq", 1) or 1)
```

- 无持久请求时（文件缺失/被清），消费端把“没有请求”当成 `requested_seq=1`：
  执行完整副作用事务（124-143，包括 retry.py 子进程与 refill），写
  `{requested:1, consumed:1}` 并（若拥有 marker）删除 marker；
- 现场 3-key 文件指纹即此路径产物（见 1.3）；
- 旧 CLI 入口 `orchestration_epilogue.py:132-138` 的 `__main__` 直接
  `run_terminal_packet(ROOT)`——任何仍调用该 CLI 的旧 supervisor/监控都会成为
  幻影消费者，与真实 runner 抢同一把 `.terminal_packet.lock` 并重复执行事务
  （marker 归属检查已防止误删 marker，但重复执行与文件覆写仍在）。

最小修复：默认值改为 0，`requested <= 0` 时不执行任何工作/写入直接退出：

```python
request_state = read_json(requests, None) or {}
requested = int(request_state.get("requested_seq", 0) or 0)
if requested <= 0:
    break
```

### 2.3 P1：runner 无活性/年龄上限 → coalesce 无限期延迟（F5）

位置：lifecycle_supervisor.py:526-546（`runner_generation_alive` 只查“进程存在”），
terminal_packet_epilogue.py:118-143（工作全在 `.terminal_packet.lock` 内）。

- R 卡死（statemachine/refill/retry.py 子进程）时，所有后续 T2、T3… 都 coalesce
  到 R 上（501-503 只判存活），等待无界；
- R 崩溃后 marker 残留死 pid → 下一次终态事件重新 spawn 才能恢复；若崩溃的就是
  最后一个终态请求，`requested_seq > consumed_seq` 会永久挂起——全仓库只有这两个
  文件引用 `terminal_requests`（已用 rg 验证），**没有 cold-start sweep**；
- `.terminal_packet.lock` 在 Windows 上无显式超时（orchestration_common.py:248-251
  msvcrt `LK_LOCK` 内部约 10×1s 重试后抛 OSError），卡死的 runner 会阻塞所有
  消费端。

最小修复：
1. marker 增加 `spawned_at`，`runner_generation_alive` 增加年龄上限
   （如 > 120s 视为失效 → 重新 spawn）；
2. 增加启动/周期 sweep：发现 `requested_seq > consumed_seq` 且无活 marker 时
   调用 `schedule_epilogue`。

### 2.4 P2：retry 路由在锁内串行执行，放大终态事务延迟（F6）

位置：terminal_packet_epilogue.py:47-65、130-134。

- `route_terminal_retries` 对每个 FAILED/TIMED_OUT packet 执行
  `subprocess.run(retry.py, timeout=30.0)`（54-60 行），且整体在
  `.terminal_packet.lock` 内；
- 现场状态机快照显示 FAILED/TIMED_OUT 曾达数十个 → 单次终态事务可达数分钟，
  阻塞后续所有终态事务与周期 CLI 消费端（延迟放大，非丢失）；
- 幂等性本身安全：retry.py 有 state 守卫（retry.py:152）、attempts 预算
  （198）、`retry_dispatch` 事件去重（204），重复调用不会双派。

最小修复：把 retry 路由移出 `.terminal_packet.lock`（先消费/删 marker 再执行），
或对每轮路由加总预算/批大小；至少缩短单包超时并记录进度。

## 3. 最小修复汇总（按优先级）

1. P0 F1：claim 段读失败禁止回写（raise 或 max 合并），保住 marker 等下一次
   respawn（terminal_packet_epilogue.py:145-149）。
2. P1 F2：无请求时不执行、不写、不删（terminal_packet_epilogue.py:121-123），
   并让旧 CLI 入口（orchestration_epilogue.py:137）改为直接退出或仅当文件存在时
   才运行。
3. P1 F5：marker 加年龄上限 + `requested > consumed` 的冷启动 sweep。
4. P2 F6：retry 路由移出 `.terminal_packet.lock`。

## 4. 假设 / 未验证项

- H1：05:07–05:12 的 9 次 req=1 运行由拆分前常驻内存的旧代码与 legacy CLI 产生；
  当前版本（05:20:02）尚未在生产运行，其真实行为未验证。若旧 supervisor 继续
  存活并触发 CLI epilogue，2.2 的幻影消费路径仍会复现。
- H2：Windows `msvcrt.locking` 的 `LK_LOCK`（消费端 file_lock）与 `LK_NBLCK`
  （生产端 locked）在同一文件字节 0 互斥——按 MSDN 语义成立，但本次只读审查
  未做并发实测。
- H3：F1 的触发需要 claim 段瞬时读失败（低概率）；但该失败类别已被本仓库自己的
  `atomic_json` 重试逻辑（lifecycle_supervisor.py:85-92）认定为该主机真实存在。
- H4：PID 复用防护（创建 FILETIME 比对）理论正确且 fail-safe，但未观测到真实
  复用事件。

## 5. 证据清单

- `data/orchestration/terminal_requests.json`（mtime 05:12:18）：3-key 指纹。
- `data/orchestration/terminal_epilogue.log`：16 次 v1 + 52 次 v2 运行时间线。
- `data/orchestration/terminal_packet_status.json`（mtime 05:12:18）：
  `requested_seq=1, consumed_seq=1`。
- `data/orchestration/epilogue_status.json.tmp.36244`（mtime 04:37:34）：旧 CLI
  epilogue 进程被强杀留下的 tmp（旧 `_write` 格式 `.tmp.<pid>`），佐证该主机存在
  进程/写盘中断史。
- 运行中的 supervisor 进程启动于 04:54–05:05（Win32_Process 查询），早于当前
  文件版本，携带旧代码。
