# Observer 父活性 R2 审计报告

审计时间: 2026-08-14
审计范围: launchers/loop_monitor_server.py + tests/unit/test_loop_monitor_server.py
审计重点: child-renewed parent liveness、每次扫描 terminal 采样次数、90 秒切片删除

---

## VERIFIED（已验证事实）

### 1. child 活动可以续活父会话（child-renewed parent liveness）

**文件/行:** `launchers/loop_monitor_server.py:441-443`

```python
# Child rollout activity is therefore equally valid evidence that
# this parent group belongs to the current live task.
group_activity = max(float(parent["mtime"]), float(item["mtime"]))
if now - group_activity > ROLLOUT_PARENT_FRESH_SECONDS:
    continue
```

**事实:** 当前 `scan_rollout_subagents` 使用 `group_activity = max(parent_mtime, child_mtime)` 来
判定父组是否存活，而不再单独要求父文件的 mtime 满足鲜活窗口。只要子 rollout 最近有写入，父组就被
视为活跃，不会被修剪。这正是"child 续活父"的核心逻辑。

**测试覆盖:** `test_active_child_keeps_quiet_parent_group_visible`（test_loop_monitor_server.py:91-119）
该测试将父 rollout 的 mtime 设为 200.0（远超 `ROLLOUT_ACTIVE_SECONDS`=120），将子 mtime 设为
990.0（距 now=1000 仅 10 秒），断言子任务仍然出现在结果中。**测试通过前提：** 生产代码使用
`max(parent_mtime, child_mtime)` 而不是单独检查父文件新鲜度。

---

### 2. 每次扫描每个 rollout 路径只调用一次 `rollout_terminal`

**文件/行:** `launchers/loop_monitor_server.py:424-431`

```python
# Take one lifecycle snapshot per rollout for this scan.  Re-reading a
# live parent in both passes creates an avoidable race where a newly
# appended task_started/task_complete changes the classification
# halfway through the same dashboard sample.
terminal_by_path = {
    str(item["path"]): rollout_terminal(item["path"])
    for item in sessions.values()
}
```

**事实:** 正式代码在扫描开始时对所有已收集的 session 路径统一调用一次 `rollout_terminal`，结果
缓存到 `terminal_by_path`。后续对父/子的 terminal 判断都查字典，不再重读文件。

**测试覆盖:** `test_rollout_terminal_is_sampled_once_per_scan`（test_loop_monitor_server.py:137-163）
该测试注入了一个计数 wrapper，断言父路径和子路径各被精确调用一次。

---

### 3. 90 秒波次切片已删除

**文件/行:** `launchers/loop_monitor_server.py:474-483`

注释明确记录了历史背景：
```
# The former 90-second wave slicing silently dropped those live children
# and fed a false deficit back into the refill controller.
```

而当前代码（line 483）：
```python
tasks.extend(sorted(rows, key=lambda row: float(row.get("created_at", 0))))
```

直接将该父组下所有候选子任务按 `created_at` 升序追加，不再设置任何基于时间戳的截断阈值。
**没有任何变量名、常量或逻辑使用 90 秒值**（已用 `rg "90"` 确认，仅出现在注释中，
无对应的阈值过滤代码）。

**快照常量确认:**
- `ROLLOUT_PARENT_FRESH_SECONDS = 600.0`（line 26）  
- `ROLLOUT_ACTIVE_SECONDS = 120.0`（line 29）  
- `ROLLOUT_SCAN_TTL_SECONDS = 5.0`（line 24）  
无 90 秒相关常量。

---

### 4. `open_sessions` 计数器语义正确

**文件/行:** `launchers/loop_monitor_server.py:464-473`

```python
tasks: list[dict[str, Any]] = []
open_sessions = 0
for parent_id, rows in candidates.items():
    parent = sessions.get(parent_id)
    group_activity = max(...)
    if (not parent or terminal_by_path.get(...) or now - group_activity > ROLLOUT_PARENT_FRESH_SECONDS):
        open_sessions += len(rows)
        continue
    tasks.extend(sorted(rows, ...))
```

**事实:** `open_sessions` 仅统计那些**无法归入活跃组**的候选子任务数量（父会话丢失、已终止或
超时的孤儿），而不是所有子任务。这与测试 `test_later_birth_does_not_hide_earlier_nonterminal_wave`
断言 `result["open_sessions"] == 0` 一致（两个子任务均应被列入活跃 tasks）。

---

## HYPOTHESIS（有根据的推断，未完全追踪）

### H1. 旧 snapshot 函数对 `ROLLOUT_PARENT_FRESH_SECONDS` 的双重检查

**证据:** `snapshot()` 内在 `rollout_by_id` 之外的 rollout 任务循环（line 732 附近）未见
二次 `now - group_activity` 过滤；但整个 `candidates` 分组循环（line 465-473）已集中处理，
推断 `snapshot` 层不需要再重复鲜活检查。由于未能全量展开 `snapshot` 中间段（约 line 686-735），
该推断未 100% 验证。

### H2. `ROLLOUT_ACTIVE_SECONDS = 120` 是旧 90 秒阈值的替代

代码注释 (line 29-31) 写道 "older open sessions remain visible but never inflate effective
concurrency"，120 秒窗口控制的是 `recent_activity` 字段（非入队门控），用于区分"本轮活跃"
与"静默但未结束"的子任务。旧 90 秒切片是**删除**子任务，现在 120 秒仅是打标签，语义不同。
推断两者不冲突，但未在提交历史中追踪确认。

---

## UNRESOLVED（无法确认）

### U1. 测试中 `result["open_sessions"]` 断言来自尚不存在于快照代码中的字段

`test_later_birth_does_not_hide_earlier_nonterminal_wave` 断言 `result["open_sessions"] == 0`，
但该字段由 `scan_rollout_subagents` 返回（已确认），而 `snapshot()` 中是否将其透传到
返回值 `counts["open_sessions"]` 已在 line 568 确认：
```python
counts["open_sessions"] = int(rollout.get("open_sessions", 0) or 0)
```
**此项已解决，归入 VERIFIED 补充。**

### U2. snapshot 完整中间段（line 686-735）未展开

由于输出截断，`snapshot` 函数内 rollout fallback 循环的完整内容未全部逐行验证，
特别是 native 行的 stale_native 路径与 rollout_by_id 交叉判断的正确性仅通过
`test_stale_native_row_without_current_rollout_stays_stale` 的测试语义推断，
未在代码层面逐行确认（已读取 line 605-688，覆盖关键路径）。

---

## 结论

| 条目 | 状态 |
|---|---|
| child 续活父（child-renewed parent liveness） | **VERIFIED** |
| 每次扫描每路径恰好一次 terminal 采样 | **VERIFIED** |
| 90 秒波次切片已删除 | **VERIFIED** |
| `open_sessions` 孤儿计数逻辑正确 | **VERIFIED** |

证据文件:
- `launchers/loop_monitor_server.py` lines 424-483
- `tests/unit/test_loop_monitor_server.py` lines 91-163

