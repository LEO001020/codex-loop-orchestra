# headless_wave CLI + stale exec_roster recycle

Read-only audit. No files outside this report were modified.

## 1. harness/headless_wave.py CLI

Parser: `harness/headless_wave.py:585-598`

```
py -3 E:\codex-LOOP\codex-loop-s-f2\harness\headless_wave.py ^
  --manifest <manifest.json> ^
  --root E:\codex-LOOP\codex-loop-s-f2 ^
  [--detach | --wait-all] ^
  [--timeout 1800] [--observe-timeout 5] [--spawn-interval-ms 1000] [--health-every 8] [--dry-run]
```

Flags:
- `--manifest` required. JSON `{tasks:[{task_id, task_name, prompt, cwd, role?}]}`.
- `--root` default = package root (`Path(__file__).parents[1]`), i.e. `E:\codex-LOOP\codex-loop-s-f2`. Resolves `LoopPaths.data = <root>/data`.
- `--detach` and `--wait-all` are mutually exclusive (`run()` raises `WaveError`).
- Wait policy (`run()` 412-420): if any task has `parent_session_id` and caller did not pass `--detach`, wait-all is ON by default. Unparented operational waves stay detached unless `--wait-all`.
- `--wait-all` blocks in `wait_for_terminal()` until observed generations are terminal, timeout = `--timeout + 30`.

Windows roster write (plane=Windows CLI):

`headless_wave.py` does **not** write `exec_roster.json` itself. It Popen's `lifecycle_supervisor.py`, which is the writer.

Adhoc path (`build_commands` 391-403):

```
py -3 E:\codex-LOOP\codex-loop-s-f2\harness\lifecycle_supervisor.py
  --data-dir E:\codex-LOOP\codex-loop-s-f2\data
  --packet adhoc-<sha10>-<task_id>
  --run-id <packet>-<uuid>
  --attempt 0
  --task-name <name> --role <role> --model <pin.model>
  --plane "Windows CLI"          # os.name == "nt"
  --cwd <resolved cwd>
  --stdout/--stderr/--report ... --timeout ...
  -- <codex.exe exec ...>
```

`lifecycle_supervisor.run()` (`harness/lifecycle_supervisor.py:627-655`):
- first `Store.update(..., state="starting", plane=args.plane or ("Windows CLI" if os.name=="nt" else "WSL CLI"), supervisor_pid=os.getpid(), supervisor_proc_start_ticks=...)`
- after `spawn_boundary` (Windows Job Object): `state="running", os_pid=boundary.pid, worker_proc_start_ticks=..., heartbeat_at=time.time()`
- heartbeat every `HEARTBEAT_INTERVAL_S = 10.0`
- lock: `data/lifecycle/.exec_roster.lock`; file: `data/lifecycle/exec_roster.json` schema `codex-loop-exec-roster/v2`

Packet/ledger path uses `DispatcherV2.dispatch()` -> `dispatch.py.supervisor_command()`. That command currently omits `--plane`; supervisor then defaults to `"Windows CLI"` on `os.name=="nt"` (same result on Windows).

Observed `plane` in current roster: Windows CLI=1100, WSL CLI=43, None=4.

## 2. cold_reconcile_exec trigger / recycle safety

Function: `hooks/subagent_lifecycle.py:713-794` `cold_reconcile_exec(roster: NativeRoster)`.

Trigger (only production call site besides tests):
- `handle("SessionStart", ...)` at 803-807 calls `cold_reconcile` (native roster) then `cold_reconcile_exec` then `recompute_refill` then `schedule_refill_actuator(..., source="desktop_session_start")`.
- Global Desktop hook: `config/global_hooks.json` SessionStart -> `hooks/global_loop_mode.py --component lifecycle --event SessionStart` -> subprocess `hooks/subagent_lifecycle.py --event SessionStart` with `LOOP_ROOT=<f2 root>`.
- Project-local `.codex/hooks.json` has `"SessionStart": []`, so local F2 project hooks do **not** fire this. Global LOOP mode does.

Selection rule:
- jobs with `state in {starting, running}`
- skip if `process_matches(supervisor_pid, supervisor_proc_start_ticks)` (Windows: OpenProcess + STILL_ACTIVE=259 + GetProcessTimes ticks)
- else candidate

Cleanup:
- Windows: `cleaned = True` immediately; **does not kill** leftover worker. Comment: Job Objects kill on supervisor-handle close.
- POSIX: may `killpg(os_pid, 15/9)` only if worker pid identity still matches.

Write:
- recheck under `.exec_roster.lock` that `run_id`/`state` unchanged and supervisor still unmatched
- if cleaned: `state="lost", stop_reason="supervisor_lost", cleanup_status="confirmed_gone"` + `exec_failed` event `why=supervisor_lost`
- else: keep running, `cleanup_status="cleanup_failed_live"`

Safety for a one-shot now:
- **Not read-only.** It mutates `exec_roster.json` and appends `data/events.ndjson`.
- On Windows it does **not** signal PIDs; identity mismatch (including PID reuse) is treated as dead supervisor and marked `lost`.
- Live identity-matching supervisors are skipped. Snapshot: 2 fresh jobs with matching ticks would be kept.
- Side effect of full `SessionStart` handle: also `cold_reconcile` on **native_roster**, refill recompute, refill actuator. That is broader than exec-roster recycle.

Recommended one-shot (exec roster only, still writes lost rows; no SessionStart native/refill):

```
cd /d E:\codex-LOOP\codex-loop-s-f2
py -3 -c "from pathlib import Path; import hooks.subagent_lifecycle as m; m.cold_reconcile_exec(m.NativeRoster(Path(r'E:\codex-LOOP\codex-loop-s-f2\data')))"
```

Need `LOOP_ROOT` only if using `handle("SessionStart")` / `data_dir()` / `project_root()`. Direct `cold_reconcile_exec(NativeRoster(data_dir))` does not need SessionStart.

Do **not** use `desktop_edge_reconcile.py` for this: it targets Desktop `native_roster.json` + `state_5.sqlite`, never `exec_roster.json`.

Can it run now: **yes, Windows-safe for dead/reused supervisor identity; not read-only; do not use SessionStart unless native-roster reconcile + refill actuator are also wanted.** Prefer the direct function call above. There is no official dry-run flag.

## 3. exec_roster stale snapshot

File: `data/lifecycle/exec_roster.json` (~7.3 MB). Counts via json.load, not printed.

Snapshot unix `1788021927` (~2026-04-28? wait: 1788021927 is 2026-08-30 local; current_date 2026-08-30):

- n_jobs=1147
- running=60, starting=0, lost=1 (plus completed/failed/timed_out/cancelled/spawn_failed)
- heartbeat>30s among running/starting: **58**
- fresh hb<=30s: **2** (identity-matched live)
- stale planes: Windows CLI=48, WSL CLI=10
- stale supervisor raw-PID alive: 4, but **identity match=0** (all 4 are PID reuse / ticks_mismatch)
- stale worker identity match=0
- cold_reconcile_exec would candidate **58** dead-supervisor-identity rows; keep the 2 live matched jobs

Samples:
1. Dead: packet `adhoc-0ce6fe0373-test-taxonomy`, plane=Windows CLI, supervisor_pid=7316, os_pid=56816, hb_age~1.33e6 s, OpenProcess failed (err 87), both PIDs dead.
2. PID reuse: packet `adhoc-2a28776306-fl18-sonnet-trust-order-r2`, plane=Windows CLI, supervisor_pid=38548 still_active but ticks 134324833746042248 != expected 134311715299898375; os_pid=49340 dead. Reconcile would mark lost, not kill.

Live PID count (identity-matched supervisor+worker): **2** at this snapshot (fresh jobs). Raw still_active PIDs on stale rows are reused, not the original generation.

Note: a few minutes earlier running was 65/4 live; live wave is draining. Treat counts as snapshot, not a lock.

## 4. Tests (lost/stale related)

`tests/unit/test_headless_wave.py`:
- test_lost_live_supervisor_is_recovered
- test_pid_reuse_token_prevents_lost_generation_recovery
- test_lost_success_with_report_recovers_completed
- related: test_starting_is_not_effective_concurrency (starting != effective running)

`tests/unit/test_desktop_edge_reconcile.py`: **no exec_roster lost/stale tests**. It covers Desktop native-roster/sqlite edges. Closest names: none with lost/stale in the test name. Body uses native `status=terminal|running`, not exec `lost`.

Exec lost/stale tests actually live in `tests/unit/test_subagent_lifecycle_hook.py`:
- test_cold_start_marks_dead_supervisor_generation_lost
- test_cold_exec_reconcile_keeps_matching_supervisor_identity
- test_cold_exec_reconcile_never_kills_reused_worker_pid
- test_cold_exec_reconcile_cleanup_failure_keeps_reservation
- test_session_start_triggers_cold_reconcile_actuator

## Landing summary

Command template: see section 1.
Recycle now: direct `cold_reconcile_exec` yes on Windows; skip SessionStart unless native+refill wanted.
stale hb>30s: 58
live identity-matched jobs: 2
