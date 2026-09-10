# Integrated Fable findings from 12 V4 + 4 K3 headless auditors

Status: primary integration by the root agent. Fifteen final reports are
available. Auditor 03 (five-hour metering) timed out in the original run and a
bounded resume, so no final report is claimed for it. Its absence is visible in
the two state files; metering defects below are independently corroborated by
auditors 04, 05, 06, 07, 10, 11, 13, 15 and local source inspection.

## P0 — must be resolved before production enablement

### P0-1: K3 has capacity but no complete demand path

K3's five-hour effective share is 0.46%. Current K3 roles are late conditional
verifier/reviewer work. `trigger_eval.py` upgrades non-`direct_l3` actions to
`direct_l3` while `passthrough_enabled=false`, and the repository lacks an
exactly-once consumer that dispatches every `send_l2` to headless K3. Raising
`k3_target=12` alone cannot create K3-ready work.

Required fix: layered routing, a K3 `plan_expander` before non-trivial V4 waves,
an exactly-once `send_l2` consumer, conditional K3 verification, and a bounded
Sol AdjudicationPacket. Preserve explicit high-risk `direct_l3` and the human
release gate.

Evidence: `config/config.toml.example:79`, `harness/trigger_eval.py:242-246,
290-291`, `config/triggers.yaml`, `harness/dispatch.py:681-714`, auditor 06.

### P0-2: the five-hour KPI is analytically computed but not reproducible

The corrected baseline is useful but not yet a mechanical release gate. The
shipped meter classifies maintenance by searching the entire rollout, emits
only cumulative/24h/7d windows, and cannot regenerate 45.93%, 22.82%, 91.57%,
or K3 0.46% from a frozen fixture. The persisted old report remains 0.5802
BLOCK, creating two apparent truths.

Required fix: turn-scoped maintenance, replay dedup golden tests, explicit
1h/5h/24h/7d windows, frozen cutoff/input manifest, per-root attribution,
minimum 2M production-effective denominator, and hysteresis. Root usage remains
inside the all-in KPI; 22.82% is diagnostic only.

Evidence: `metering/model_token_share.py:45-51,104-108,172-173,253-306`,
auditors 04, 05, 07, 10, 11, 13 and 15.

### P0-3: Windows context catalog drifted back to 272k

At audit time the WSL catalog and all headless CLI overrides are V4/K3
1,000,000 physical, 950,000 effective and 800,000 auto-compact. The Windows
`opencodex-catalog.json` and `models_cache.json` were regenerated at 23:03 with
272,000/244,800. Restarting Desktop now would not yield 950k native children.
The existing `install-wsl-model-catalog.sh` copies Windows to WSL, so rerunning
it can regress the working headless catalog.

Required fix: establish the OpenCodex catalog generator/config as the single
source of truth, make V4/K3 1M/800k survive catalog sync, then atomically refresh
both catalogs and probe a new Desktop child for effective context 950k. Do not
claim dual-plane 1M before that probe.

Evidence: current selected catalog facts, `.bootstrap/install-wsl-model-catalog.sh`,
auditors 09, 11 and 15.

### P0-4: child short-return limits are prose, not a mechanical boundary

The supervisor checks exit code and report existence, but not report schema or
summary size. CSV output schema leaves summary unbounded. Duty/reviewer paths
still contain reply-body parsing instructions that conflict with the one-line
return contract. A child can therefore return an oversized summary into root
context despite writing a report.

Required fix: validate `short_result.schema.json` and report schema before
publish, reject summaries over 500 tokens/eight findings, make duty/reviewer
verdicts file-only, and feed Sol only artifact handles and blocking finding IDs.

Evidence: `harness/lifecycle_supervisor.py:496-510`,
`harness/csv_reconcile.py:100-124`, `harness/dispatch.py:539-551,668-674`,
`agents/reviewer.toml:40-43`, auditor 05.

### P0-5: current source SHA256SUMS is stale

The source `SHA256SUMS` has 100 historical rows; 12 no longer match and new
ipybox/config/test files are absent. It cannot be the integrity authority for
this snapshot. The package therefore moves it to
`audit/upstream-SHA256SUMS-stale.txt` and uses top-level `FILELIST.sha256` as the
only current package manifest.

Required fix for a later release: generate the source release from one explicit
allowlist, regenerate source hashes atomically, and add install-time verification.

Evidence: auditors 01 and 14.

## P1 — correctness and stability gaps

### P1-1: concurrency state has multiple authorities

`refill_policy.toml` says 48/36/12, but the observed persisted refill snapshot
still held 24/18/6, `VERSIONS.lock` contains older 16-based pool statements, and
`config.toml.example` keeps a CSV concurrency default of 16. The monitor prefers
live policy and can hide stale controller state.

Required fix: make policy version/mtime part of refill state and force recompute
on mismatch; align VERSIONS/config examples; fail visible when policy cannot be
read.

Evidence: auditors 06, 07, 10 and 12.

### P1-2: CSV batch bypasses the common throttle/context path

`dispatch_csv` does not call the same birth interval, max-initializing and
health-gate functions as the headless exec path, and can inherit the stale
Desktop catalog instead of explicit context overrides.

Required fix: route every batch front end through one zero-model admission and
spawn primitive. Preserve one-second pacing, max eight initializing, every-eight
health gate, 48 target and explicit model/context pins.

Evidence: `harness/dispatch.py:487-557`, auditors 07 and 11.

### P1-3: throttle bookkeeping is not fully atomic

Concurrent dispatchers can read the same last-spawn timestamp or health counter
before a locked update. Observed minimum spacing near 0.996 s and non-uniform
gate counts are consistent with timestamp jitter/shared-counter races.

Required fix: place interval decision, spawn reservation and health counter in
one lock/transaction; define an acceptance tolerance such as >=0.9 s rather
than claiming mathematically exact 1.000 s wall time.

Evidence: `harness/dispatch.py:149-236`, auditors 07 and 16.

### P1-4: runtime logs can retain sensitive text

The source scan found no embedded credentials, but the supervisor persists an
8 KiB stderr tail into events; E0 annotation persists up to 2,000 characters of
message text; some `data/` files are tracked and permissions are broad. The
monitor exposes task-name text to any local process through an unauthenticated
loopback endpoint.

Required fix: redact or store only stderr paths/hashes, remove message bodies
from metering output, stop tracking runtime data, add retention/permissions,
and either authenticate the monitor locally or keep displayed task labels
explicitly non-sensitive.

Evidence: `harness/lifecycle_supervisor.py:397,507-510`,
`metering/e0_annotate.py:125-147,197-225`, auditor 02.

### P1-5: ipybox reaper failure is silent and the match is narrow

The parent-death and concurrent-reaper tests passed, and final observed orphans
were zero. However the wrapper suppresses reaper output/failure, while discovery
is intentionally limited to PPID 1, one venv and `jupyter-kernelgateway`.

Required fix: emit bounded reaper-failure events and an end-of-wave orphan count;
fault-inject supervisor death; decide explicitly whether non-PPID-1 or alternate
interpreter strays need a safe second matcher.

Evidence: `.bootstrap/ipybox-supervised:38-50`,
`harness/ipybox_cleanup.py:20-94`, auditors 08 and 15.

### P1-6: 48 is configured, not yet capacity-proven

The verified single-pool wave is 24/24. The user observed global 48 across
tasks as smooth, but no isolated 48/48 LOOP wave has established zero failures,
unchanged OpenCodex/app-server, max eight initializing and orphan zero.

Required gate: run one isolated 48/48 wave after external VPS load drops. This
is evidence work, not a reason to lower the configured target.

Evidence: stability report lines 181 and 271; auditors 07, 08, 10, 11, 12,
13, 15 and 16.

### P1-7: OpenCodex has split ownership after a health stall

During final packaging, `/healthz` timed out while PID 23124 still held port
10100. A new Bun process, PID 53300, appeared at 23:55:34 with an already-exited
parent and became the healthy listener. The original service wrapper remained
alive and began attempting a new `ocx start` approximately every five seconds;
each child exited with code 1 because PID 53300 already owned the port. Desktop
app-server PID 21756 did not change and the WSL orphan-gateway count stayed zero.

Required fix: one PID/port ownership lock, a wrapper that treats an already
healthy listener as an adopted steady state instead of a restart failure loop,
and a controlled unresponsive-listener path with drain/replace semantics. Do
not kill the healthy listener while external tasks may be using it merely to
clean up supervisor ownership.

Evidence: `reports/fable-audit/GATEWAY_INCIDENT_20260811.md`; observed process
creation times and bounded service-log tail.

## Changes made during this handoff

1. The 8765 monitor no longer treats rollout files under the hook-authoritative
   `E:\codex-LOOP` scope as live. After restart it showed 16 VPS `open~`
   estimates and zero stale codex-LOOP tasks, instead of 48 estimates containing
   32 crash-era LOOP rollouts.
2. Added the proposed K3 `plan_expander`, DecisionSkeleton, packet DAG,
   short-result schema and orchestration policy. These are audit artifacts, not
   production enablement.
3. Kept wide audit work headless. The Desktop app-server stayed on the same
   post-23:15 process while 16 headless sessions ran. OpenCodex stayed on PID
   23124 during that wave, then suffered a separate health stall/PID replacement
   during final packaging; the distinction is recorded rather than hidden.
4. Recovered seven of eight timed-out sessions with bounded resume prompts.
   Auditor 03 remained timed out and has no fabricated report.

## Release recommendation

Fable should approve the architecture direction but reject immediate production
activation until P0-1 through P0-5 have mechanical tests. The highest-leverage
sequence is meter truth → K3 demand path → bounded root/child contracts →
catalog persistence → isolated 48/48 capacity gate. None of these requires
disabling ipybox or reducing total concurrency.
