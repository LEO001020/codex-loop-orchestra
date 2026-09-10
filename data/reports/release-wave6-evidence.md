# Codex LOOP F2 release-wave6 bounded evidence

Scope: current managed package at `E:\codex-LOOP\codex-loop-s-f2` and
`/home/codexloop/codex-loop-s-f2`. This packet is evidence, not authority;
reviewers must falsify the stated invariant against only the named files.

## Current production profile

- routing mode: `cold_start` (zero new layered behavior until the mechanical gate authorizes it)
- concurrency policy: total 48, V4 preferred 36, K3 preferred 12, borrowable; normal wave 16
- execution model: `weiwu/deepseek-v4-flash`, effort `ultra`
- verifier/reviewer/plan_expander: `weiwu-k3/kimi-k3`, effort `max`
- non-GPT context: 1,000,000; auto-compaction: 800,000
- OpenCodex health: same live listener PID 13024; supervisor never kills a live exact listener solely for failed health probes

## Latest mechanical acceptance

- Windows orchestration: `437 passed, 14 skipped`
- Windows unit: `376 passed, 21 skipped`
- WSL orchestration: `451 passed`
- installer slice in WSL: `14 passed`
- lifecycle supervisor slice: `18 passed`
- monitor slice: `14 passed`
- ipybox: `IPYBOX_LAZY_OK tools=4 gateway_on_first_cell=1`
- ipybox parent death: `IPYBOX_PARENT_DEATH_OK orphan_delta=0 gateway_delta=0`
- `sha256sum -c SHA256SUMS`: PASS on both source and WSL production
- dual-plane managed hash: PASS, 98 files, no mismatches

## Changes under this release gate

1. Provider birth truth
   - `harness/headless_wave.py`: packet and generic K3 births check active backoff after dry-run/already-active handling and before physical birth.
   - `harness/l2_consumer.py`: K3 backoff preserves pending records without claim or demand; retries after expiry. V4 is unaffected.
   - `harness/provider_health.py`: only transport/upstream/no-first-response failures create K3 backoff; local failures do not poison provider health.

2. Process/lifecycle truth
   - `hooks/subagent_lifecycle.py`: cold POSIX reconcile writes `exec_failed` only after owned process-group absence is proven; cleanup failure keeps the existing running reservation as `cleanup_failed_live`, suppressing duplicate refill.
   - `harness/lifecycle_supervisor.py`: a new run generation clears prior terminal diagnostics from the current row; old evidence remains in history/events.
   - Windows remains `CREATE_SUSPENDED -> AssignProcessToJobObject -> ResumeThread` with kill-on-job-close.

3. Plan and L2 integration
   - `harness/orchestration_epilogue.py`: cold_start remains zero birth, shadow stays local, layered schedules the potentially long plan consumer off the state-machine critical path.
   - `harness/plan_consumer.py`: request-level `O_EXCL` claim is the exactly-once authority and only bounded DecisionSkeleton/ControlPacket file handles reach K3.
   - `harness/statemachine_v2.py`: `l2_claim_reaped` is audit-only; t4 requires a non-empty parseable JSON-object report and rejects a mismatched packet identity.
   - L2 completion writes the immutable completion marker before emitting verdict; the stale reaper checks completion before moving a claim, so a published verdict cannot race into redispatch.

4. Release-review exactly-once
   - `harness/dispatch.py`: a per-wave record is atomically claimed as `launching` before physical birth with a pre-generated run_id.
   - concurrent callers treat both `launching` and `dispatched` as owned and do not seed or birth a second reviewer.
   - explicit pre-spawn exceptions transition the same generation to `launch_failed`; stale launchers cannot overwrite another run_id.

5. Headless MCP compatibility
   - Windows headless explicitly disables the complete Desktop `node_repl` MCP table.
   - WSL has no node_repl table and passes no partial override; a lone `enabled=false` would synthesize an invalid transport.
   - Desktop node_repl remains available. WSL headless has zero node_repl process by absence; Windows headless has zero by explicit disable.

6. 8765 observer truth
   - stale native and stale headless rows remain forensic-visible but do not count as effective concurrency.
   - a fresh refill timestamp cannot mask an expired headless heartbeat.
   - runtime UUIDs are never public task names.
   - target remains 48 and the deficit remains visible; no transport preference is an effective concurrency cap.

7. Release integrity
   - `config/managed_files_v2.txt` is the portable production boundary.
   - `SHA256SUMS` covers exactly every non-self managed file; `SHA256SUMS` is itself the 98th managed file and is protected by the dual-plane manifest hash. Self-hashing is intentionally impossible/recursive.
   - runtime `data/` is intentionally plane-local and excluded from package-copy/hash identity except explicit governor attestations.

## Known non-release observations

- K3 provider recently returned HTTP 504 and 15/16 broad audits reached their 900-second supervision timeout. They remained lifecycle-visible and were removed from effective concurrency on timeout; one completed audit passed provider birth handling.
- A single historical `stale_headless` row is retained for forensics and is not counted.
- The working tree contains pre-existing user/legacy changes and backups; no reset or broad cleanup was performed.

Any REDO must identify a current, reproducible contradiction in the named files. Historical rows, old backup directories, or a provider timeout alone are not implementation failures.
