# Why thread 01a04d94 is not LOOP

Verdict: **not a LOOP-lifecycle parent**. Thread `01a04d94-552e-77d0-9431-22159cf9d99f` is a Codex Desktop native multi-agent session. It birthed children with the host tool `spawn_agent`, not with `harness/headless_wave.py` / `lifecycle_supervisor.py` / packets / parent-backlog. **None of those Desktop children are registered** in `native_roster.json` or as `exec_roster.json` jobs. A later unparented `headless_wave` did create `adhoc-2b95a2a0fb-*` jobs; those are LOOP-visible headless workers, not the `01a04d9b-*` / `01a04ddc-*` Desktop children.

## 1. What 01a04d94 actually is

| Field | Evidence |
| --- | --- |
| Thread id | `01a04d94-552e-77d0-9431-22159cf9d99f` |
| Origin | Codex Desktop / `source=vscode` / `thread_source=user` / `cli_version=0.150.0-alpha.8` |
| CWD | `C:\Users\hzq00\Documents\Codex\2026-08-29\hy4-ai-ai-ai-ai-01a02ee0` (Honor AI-sidebar follow-up, not LOOP control root) |
| Root model | `snaillmou/grok-4.6` effort `max` (`turn_context`) |
| Session start | `2026-08-29T12:52:41.903Z` (rollout filename `T20-52-41` CST) |
| LOOP instructions | Present as injected `AGENTS.md` user message (rollout line 4). That is policy text, not a lifecycle registration. |
| LOOP planning artifacts | **Absent** for this thread: no `packets/*.json` / `dag.json` mentioning `01a04d94`; `data/refill/parent_sessions.json` only has `019feacd-184e-7472-9f11-ea364c22e595` from 2026-08-14. |

Parent rollout files (same session_id, compacted/paginated):

- `C:\Users\hzq00\.codex\sessions\2026\08\29\rollout-2026-08-29T20-52-41-01a04d94-552e-77d0-9431-22159cf9d99f.jsonl`
- `...T22-08-27-01a04d94-..._01a04dd9-af83-...jsonl` (live continuation)
- later compact stubs `..._01a04df1-2a0d-...`, `..._01a04df1-be85-...`, `..._01a04df3-e69b-...`
- `...T22-38-00-01a04d94-..._01a04df4-c097-...jsonl` (third native wave)

## 2. Tools used by 01a04d94

Function-call names across all six parent rollouts (counts are call sites, not unique children):

| Tool | Count | LOOP-lifecycle? |
| --- | ---: | --- |
| `exec_command` | 60 | no (local shell) |
| `spawn_agent` | 51 | **no** — Codex Desktop native birth |
| `close_agent` | 22 | Desktop host cleanup, not LOOP `close_requests.ndjson` |
| `write_stdin` | 4 | no |
| `read_thread` | 3 | Desktop MCP inspect |
| `list_threads` | 2 | Desktop MCP inspect |
| `wait_agent` | 2 | Desktop wait, not LOOP wait-all |
| `get_goal` | 1 | no |

**Not used:** `spawn_agents_on_csv`, `mcp__codex_app__create_thread`, `mcp__codex_app__fork_thread`, `lifecycle_supervisor.py` as a parent birth path.

`spawn_agent` arguments were LOOP-shaped (`agent_type=worker`, `fork_context=false`, first line task-name) but the transport was still the Desktop collaboration tool. Returned payload was only `agent_id` + `nickname` (e.g. `01a04d9a-d30d-...` / `task_47`). No `run_id`, no `packet_id`, no roster write.

Spawn mix:

- 28 calls: `model=weiwu/deepseek-v4-flash`, `reasoning_effort=ultra`
- 23 calls: `model=snaillmou/grok-4.6`, `reasoning_effort=max` (third wave)

## 3. Desktop children exist, and they are native subagents

Child session_meta is conclusive. Example `01a04d9b-cc6e-7e32-9ec0-fb82aa4d5a9d`:

- `originator`: Codex Desktop
- `thread_source`: `subagent`
- `parent_thread_id`: `01a04d94-552e-77d0-9431-22159cf9d99f` (top-level **and** nested under `source.subagent.thread_spawn`)
- `agent_role`: `worker`
- `agent_nickname`: `task_12`

Same pattern for `01a04ddc-5bd9-...` (`task_46`, `T22-11-22`) and `01a04d9a-d30d-...` (`task_47`, `T20-59-47`).

`spawn_agent` outputs produced **51 unique child ids**. Prefix groups:

| Prefix | n | When |
| --- | ---: | --- |
| `01a04d9a` | 8 | first Desktop wave ~20:59 |
| `01a04d9b` | 8 | first Desktop wave ~21:00 |
| `01a04ddc` | 8 | second Desktop wave ~22:11 |
| `01a04de1` | 4 | second Desktop wave (follow-up) |
| `01a04df9` / `dfa` / `dfb` / `dfc` | 23 | third Desktop wave ~22:38+ |

On-disk child rollouts exist for the requested prefixes (`*01a04ddc*` = 8 files, `*01a04d9b*` = 8 files).

## 4. Was headless_wave called? Yes, later, and it is a different plane

Timeline inside the parent:

1. **First turn (~20:52-21:00):** only *read* `headless_wave.py` (`Get-Content`, `--help`, `Select-String`) plus an old honor manifest. Then immediately issued 16 `spawn_agent` calls. No `--manifest` launch.
2. **Continuation ~22:08:** closed the `01a04d9b-*` wave, spawned `01a04ddc-*` natively, *then* wrote `work\sidebar_fix\headless_wave_manifest.json` and ran:

```text
python E:\codex-LOOP\codex-loop-s-f2\harness\headless_wave.py
  --manifest C:\Users\hzq00\Documents\Codex\2026-08-29\hy4-ai-ai-ai-ai-01a02ee0\work\sidebar_fix\headless_wave_manifest.json
  --root E:\codex-LOOP\codex-loop-s-f2
  --detach --spawn-interval-ms 1200 --observe-timeout 90
```

That is a real LOOP headless birth. It is **not** how `01a04d9b-*` / `01a04ddc-*` were created (those already existed at 21:00 / 22:11; headless `started_at` is ~22:16:34 local).

`headless_wave.py` CLI (docstring + argparse): `--manifest` required; optional `--root/--timeout/--observe-timeout/--spawn-interval-ms/--health-every/--dry-run/--wait-all/--detach`. A birth counts only after `lifecycle_supervisor.py` publishes the run-id in `exec_roster.json`. `--parent-session-id` is forwarded **only if the task dict has `parent_session_id`**. This manifest did not.

## 5. Roster / backlog registration

### native_roster.json — no children registered

- Path: `E:\codex-LOOP\codex-loop-s-f2\data\lifecycle\native_roster.json`
- Schema: `codex-loop-native-roster/v1`
- Bytes / mtime: 108061 bytes, **2026-08-12 23:55:22** (updated_at 1786550122.3341637)
- Agents: 182, newest still `019ff6af-*` with parent `019ff6aa-...`
- Substring search for `01a04d94`, `01a04d9a`, `01a04d9b`, `01a04ddc`, `01a04de1`, `01a04df9`, `01a04dfa`, `01a04dfb`, `01a04dfc`: **all 0**
- Same 0 hits in `name_map.jsonl`, `data/lifecycle/events.ndjson`, `close_requests.ndjson`
- Last successful `subagent_started` events are 2026-08-12. After that only degraded noise (`subagent_start_degraded` / `parent_stop_degraded` on 2026-08-14). **No 2026-08-29 native lifecycle events.**

### exec_roster.json — adhoc jobs exist, but they are not these Desktop children

- Path: `E:\codex-LOOP\codex-loop-s-f2\data\lifecycle\exec_roster.json`
- Schema: `codex-loop-exec-roster/v2`
- mtime: 2026-08-29 22:28:44
- `01a04d94` / `01a04d9b` / `01a04ddc`: **not present**
- 12 jobs `adhoc-2b95a2a0fb-*` **are** present: `launcher-hotarea`, `observer-uri`, `dock-max-num`, `breathe-gate`, `gesture-vs-back`, `settings-ui-path`, `disabled-pkgs`, `desired-json`, `stage5-keepalive-audit`, `apk-manifest-components`, `multiwin-vs-sidebar`, `settings-default-values`

Every matching job:

- `plane`: `Windows CLI`
- `model`: `snaillmou/grok-4.6`
- `cwd`: the hy4 Desktop workspace
- **`parent_session_id`: `null`**
- **`manifest_id`: `null`**
- command: `codex.exe exec --skip-git-repo-check --sandbox read-only ...` via `lifecycle_supervisor.py`

So 8765 can see those 12 as **headless** workers, but cannot attribute them to thread `01a04d94`. Apparent `01a04de1` hits inside exec_roster are **false positives**: failed-job stderr sampling `turn_id` values, not agent ids `01a04de1-42ff-...`.

### packets / dag / parent backlog

- `data/packets/*.json`: 64 files, **0** mention `01a04d94` or the child prefixes
- Root `packets/` and `dag.json`: absent
- Only `dag.json` files are old honor plans (`honor-magicos-20260822-212526`, `honor-nmb-20260822-222255`)
- `data/refill/parent_sessions.json`: single parent `019feacd-...`, source `parent_manifest_importer`, **not this thread**

## 6. Why lifecycle did not register Desktop children

Intended path (from `hooks/subagent_lifecycle.py` plus `config/global_hooks.json` / `requirements.toml`):

- `PreToolUse` on `spawn_agent` -> pending native row
- `SubagentStart` -> `native_roster.json` agent + `lifecycle/events.ndjson`
- `SubagentStop` -> terminal + close-request

Live Desktop hook file `C:\Users\hzq00\.codex\hooks.json` (mtime **2026-08-29 01:48:44**, 140 bytes):

```json
{
  "description": "Codex LOOP global mode: conditional context, lifecycle, routing gate, and recursive-birth prevention.",
  "hooks": {}
}
```

That empty `hooks` object is **intentional cutover output** from `harness/global_desktop_mode.py` `install()`: it strips LOOP entries from `hooks.json` and writes managed hooks to `requirements.toml` so user-hooks cannot double-fire. Marker `E:\codex-LOOP\state\global-loop-mode.json` is `active=true` with the same timestamp. `AGENTS.md` and `requirements.toml` were rewritten in the same second.

What still worked: instruction injection (`AGENTS.md` / SessionStart additionalContext). The parent therefore talked like LOOP and chose `spawn_agent`.

What did **not** work for this thread: the writer that updates `native_roster.json`. If `SubagentStart` / spawn `PreToolUse` had succeeded, mtime would not still be 2026-08-12 and the 51 child ids would appear. They do not. Direct evidence: **lifecycle hook did not persist any 01a04d94 child**.

Whether Desktop 0.150.0-alpha.8 actually invoked `requirements.toml` managed hooks for `spawn_agent` on 2026-08-29 is not observable from roster bytes; the observable fact is the side effect is missing. Empty `hooks.json` is sufficient to miss registration if the runtime still keys off that file.

## 7. Why 8765 may miss these Desktop children

8765 (`E:\codex-LOOP\launchers\loop_monitor_server.py`) builds `/api/status` from three planes:

1. **native_roster** (Desktop, authoritative)
2. **exec_roster** (headless; parent attribution only via `parent_session_id`)
3. **rollout fallback** `scan_rollout_subagents(%USERPROFILE%\.codex\sessions)` for Desktop children *not* in native_roster

For 01a04d94:

1. Native plane is **empty** of these children (stale 2026-08-12 roster). Dashboard Desktop count from roster = 0.
2. Headless plane shows `adhoc-2b95a2a0fb-*` if heartbeats are fresh, but `remember_parent(parent_session_id)` is a no-op because the field is `null`. The parent row for `01a04d94` is not incremented.
3. Rollout fallback *can* see them: child `session_meta` has top-level `parent_thread_id`. `scan_rollout_subagents` uses `meta.get("parent_thread_id")`. Fallback rows are labeled `state=open~` / `plane=Desktop rollout` and counted as `estimated`.

Fallback still drops children when any of these hold:

- Parent or child classified **terminal** from the last 128 KiB (`task_complete` / `turn_aborted` / `turn_failed` / `thread_closed`). Compacted parent stubs (`..._01a04df1-2a0d-...` 3467 bytes) can win the `sessions[id]` map depending on `rglob` order and hide a live parent.
- Group activity older than **600s** (`ROLLOUT_PARENT_FRESH_SECONDS`) while the parent is quiet waiting.
- Child file mtime older than current `codex.exe app-server` epoch (`desktop_app_server_started_at`); a Desktop restart fences pre-restart rollouts as crash evidence.
- Lookback **6 hours** (`ROLLOUT_FILE_LOOKBACK_SECONDS`).
- Semantic-name dedup against already-counted headless jobs with the same task_name/pool (the adhoc wave reused the same Chinese task names as the native wave).

Net: 8765's **authoritative Desktop roster never learned these births**. At best they appear as estimated rollout ghosts, not as LOOP-registered children of `01a04d94`. Headless `adhoc-2b95a2a0fb-*` inflate the headless count without parent linkage.

## 8. Causal chain (short)

1. User continued Honor sidebar work in a **Desktop** thread, cwd outside LOOP root.
2. Global mode injected LOOP *text* (`AGENTS.md`), which tells the root to keep ~20 workers and allows the first 8 as Desktop-native.
3. The model therefore called **`spawn_agent`** (Desktop host), not packets/`headless_wave` as the primary transport.
4. `hooks.json` is an empty managed-cutover file; `native_roster` last moved on **2026-08-12**; no child id was recorded.
5. Hours later the same parent *also* launched an unparented `--detach` `headless_wave`. Those 12 jobs are LOOP-visible CLI workers with `parent_session_id=null`, disjoint from `01a04d9b-*` / `01a04ddc-*`.
6. 8765 therefore cannot treat 01a04d94 as a LOOP parent: native roster miss + exec roster unlink + fallback-only Desktop estimate.

## Evidence index

- Parent/child rollouts: `C:\Users\hzq00\.codex\sessions\2026\08\29\rollout-*01a04d94*`, `*01a04d9b*`, `*01a04ddc*`
- Native roster: `E:\codex-LOOP\codex-loop-s-f2\data\lifecycle\native_roster.json` (mtime 2026-08-12)
- Exec roster jobs: `adhoc-2b95a2a0fb-*` in `E:\codex-LOOP\codex-loop-s-f2\data\lifecycle\exec_roster.json`
- Hooks: `C:\Users\hzq00\.codex\hooks.json` (`hooks: {}`); managed copy `E:\codex-LOOP\codex-loop-s-f2\config\global_hooks.json` and `C:\Users\hzq00\.codex\requirements.toml`
- Marker: `E:\codex-LOOP\state\global-loop-mode.json`
- Headless CLI: `E:\codex-LOOP\codex-loop-s-f2\harness\headless_wave.py`
- 8765 merge/fallback: `E:\codex-LOOP\launchers\loop_monitor_server.py` (`merge_native_rosters`, `merge_exec_rosters`, `scan_rollout_subagents`, `snapshot`)
- Parent backlog: `E:\codex-LOOP\codex-loop-s-f2\data\refill\parent_sessions.json`
