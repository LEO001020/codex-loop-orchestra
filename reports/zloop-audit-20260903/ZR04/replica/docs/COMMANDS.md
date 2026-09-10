# zloop CLI — quick reference (v0.2)

> One page, every command. Verified against `src/zloop/cli.py` (snapshot 2026-09-02 round 3: `stage begin/status/close`, `wave propose/start/cancel`, `research run` are now registered in the parser).
> Commands marked **[wiring in progress]** are being added by a parallel agent this round and are **not yet in the code** — check `zloop --help` before relying on them (no-fake-success).
> Detailed operational semantics: `docs/OPERATIONS.md` §8.

## Exit codes (uniform contract)

| code | meaning |
|---|---|
| **0** | ok (note: lazy parallel modules missing also exit 0 after printing `module not available (parallel integration pending)`) |
| **2** | usage error (argparse) |
| **3** | `S_DEGRADED` — S corrupted / unreadable, fail-closed (I4) |
| **4** | bind-token wait timeout — `--wait-claim` expired unclaimed; almost always "ran in background" (P2-13) |
| **5** | blocked — precondition not met (no registered project, unknown run/stage/checkpoint, illegal state) |
| 130 | interrupted (Ctrl-C) |

## Project & environment

| Command | Effect | Codes |
|---|---|---|
| `zloop doctor` | data root, journal profile (I22 gate), per-project `quick_check`, hooks status, old-LOOP legacy warning. Never crashes on degraded projects | 0 |
| `zloop project attach [--git-root PATH]` | register/resolve the project for a git root (default: cwd git root). Idempotent | 0 |
| `zloop project list` | list registered projects | 0 |

## Run & binding

| Command | Effect | Codes |
|---|---|---|
| `zloop run start OBJECTIVE [--wait-claim N]` | create ACTIVE run + emit one-time bind token (`ZLOOP_BIND_TOKEN=<nonce>` then JSON). **FOREGROUND only** — see OPERATIONS §8.3. `--wait-claim N` polls S up to N s for the PostToolUse hook to claim (token TTL 120 s) | 0 / 4 / 3 |
| `zloop run close RID` | close an ACTIVE run (idempotent on CLOSED) | 0 / 5 / 3 |
| `zloop run status [RID]` | one run (default: all) | 0 / 5 / 3 |
| `zloop run list` | all runs of the cwd's project | 0 / 5 / 3 |
| `zloop attach RID [--resume-after-clear] [--wait-claim N]` | emit a bind token for an existing run. **FOREGROUND only**. `--resume-after-clear` = I28 recovery-after-`/clear` intent | 0 / 4 / 5 / 3 |
| `zloop detach --session ID` | remove a session binding | 0 / 5 / 3 |
| `zloop binding status [--session ID]` | session bindings + pending unexpired claims (with `seconds_to_expiry`) | 0 / 5 / 3 |

## H0 / H1 / H2 evidence planes

| Command | Effect | Codes |
|---|---|---|
| `zloop history search QUERY [--session ID] [--run RID] [--limit N]` | H2: bounded case-insensitive search over session journals (fail-soft) | 0 / 5 |
| `zloop history verify` | H2: line-hash chain + blob existence check over every session journal | 0 / 5 |
| `zloop checkpoint write [--file PATH]` (else stdin) | H1.semantic capsule: I14 machine-field stripping, I15 evidence-ref demotion, 16 KB cap, dedupe | 0 / 2 (bad JSON) / 5 (invalid or write failed) |
| `zloop checkpoint current` | the checkpoint named by `current.json` | 0 / 5 |
| `zloop checkpoint show ID` | one capsule (`cp_0001` format) | 0 / 5 |

## Install / uninstall / verification

| Command | Effect | Codes |
|---|---|---|
| `zloop install [--timeout-ms N] [--config-path PATH]` | install user-level ZCode hooks: **5 post-execution events** (D-9), `process` type, no matcher, `enabled:true`. Refuses foreign hooks (`ok:false` in JSON + exit 0); backs up any previous config to `~/.zloop/hygiene-backup/`. Takes effect only in a NEW ZCode session | 0 / 5 (selfcheck failed) |
| `zloop uninstall [--config-path PATH]` | remove ONLY the zloop-managed `hooks` key (foreign hooks refused, data root untouched) | 0 |
| `zloop verify-run [RID]` | goal-completion check: run exists and is CLOSED. Default RID = latest run. Upgrade (stages/promotions fully landed) **[wiring in progress]** | 0 / 5 / 3 |

## v0.2 additions

Landed in the parser this round (still verify with `zloop --help` — no-fake-success); contract per VOL-08/VOL-09/VOL-15:

| Command | Effect | Codes |
|---|---|---|
| `zloop stage begin <objective-slice> [--risk R]` | create stage; deterministic risk floor, clean-base gate (I37), locked base ref/tree | 0 / 5 / 3 |
| `zloop stage status [RID]` / `zloop stage close` | stage row / FSM terminal state | 0 / 5 / 3 |
| `zloop wave propose` | host-side final ruling on a wave proposal (DAG, disjoint write_scope or explicit `depends_on`, risk ≥ floor, network policy shape) | 0 / 5 / 3 |
| `zloop wave start` | launch wave: fresh launch_id + workspace per packet (I34); results fenced by I6. **FOREGROUND** for ≤600 s; longer waves: start + end turn + wait for notification (D-2). Refuses while the Kimi loopback server is up (D-17) | 0 / 5 / 3 |
| `zloop wave cancel` | writes `cancel_requested` (D-8 command input, NOT a lifecycle transition; the owner performs the transition on its next tick) | 0 / 5 / 3 |
| `zloop research run <query>` | Research Broker lane (Kimi K1 single lane, D-10); output fields below | 0 / 5 / 3 |

**[wiring in progress this round]** (promote.py library layer implemented + tested; CLI subcommand being wired):

| Command | Effect | Codes |
|---|---|---|
| `zloop stage promote SID [--skip-c2c]` | CAS + ff-only promotion of a staged commit (I30/I39): intent-first, canonical worktree clean with unchanged dirty digest, HEAD unchanged, staged commit must descend from HEAD; HIGH/CRITICAL stages require a recorded C2C audit unless `--skip-c2c` (explicit, journaled downgrade). **exit 5 reasons:** `DIRTY_OR_DRIFT` / `HEAD_DRIFT` / `NOT_DESCENDANT` / `c2c_gate_required` / `nothing_materialized` / `staging_missing` | 0 / 5 / 3 |

### Research output fields — D-18 three-axis semantics

`zloop research run` results carry two independent axes (plus the pre-existing `verification`/`trust` evidence axis); **never collapse them**:

- `provider_health` — the LANE/PROVIDER state, not the evidence: `OK` / `QUOTA_EXHAUSTED` / `AUTH_FAILED` / `SERVER_UNAVAILABLE` / `ERROR`
- `retrieval_outcome` — what was actually obtained: `EVIDENCE_FOUND` / `NO_EVIDENCE`

Quota ⇒ `provider_health=QUOTA_EXHAUSTED` **and** `retrieval_outcome=NO_EVIDENCE` — never `source_unverified` (that would assert evidence exists with provenance merely pending). The research session is a **searcher, not a second execution agent** (D-19): coding-agent tools (Read/Write/Edit/Bash/Grep/Glob) are disabled at prompt submit; WebSearch/FetchURL only.

## Standing rules

- **FOREGROUND**: `run start` / `attach` / (once landed) `wave start` must run in the foreground — a background tool_response has no stdout, so the bind-token marker can never be claimed (P2-13). Single foreground Bash call is platform-capped at 600,000 ms.
- **Never guesses**: unregistered cwd ⇒ exit 5, never cwd/latest-run inference (I32).
- All JSON output is single-line on stdout; errors go to stderr (`ERROR:` / `S_DEGRADED:` / `WARNING:`).
