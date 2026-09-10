# Codex LOOP convergence wave 3 — release evidence

Generated for bounded K3 release review. This is evidence, not a release PASS.

## Scope

- Manifest-driven package installer and WSL production synchronization.
- WSL Codex hooks/config normalization.
- Desktop/headless concurrency observation on port 8765.
- ipybox lazy start, sandbox boundary, disconnect and parent-death cleanup.
- Provider failure attribution and bounded smoke probes.

## Current routing truth

- Active execution profile: `v4f`.
- Worker: `weiwu/deepseek-v4-flash`, `ultra`.
- Verifier/reviewer/plan expander: `weiwu-k3/kimi-k3`, `max`.
- Target: total 48; preferred V4/K3 36/12; normal sustained wave 16.
- WSL global default subagent: V4 Flash/ultra; explicit K3 role TOMLs override it.
- ipybox global default disabled; headless worker enables it per policy; K3 defaults off.

## Mechanical results

- Windows orchestration + monitor: `432 passed, 10 skipped`.
- WSL orchestration: `433 passed`.
- Installer transaction suite: `10 passed` before final deployment; WSL full suite includes it.
- Dual-plane managed hash: PASS, 92 files, zero mismatches.
- ipybox: `IPYBOX_LAZY_OK tools=4 gateway_on_first_cell=1`.
- ipybox parent death: `IPYBOX_PARENT_DEATH_OK orphan_delta=0 gateway_delta=0`.
- OpenCodex health: HTTP 200, version 2.10.0, stable PID 13024 during convergence.
- 8765 health: HTTP 200; post-restart status LIVE and actual-model pool classification verified.

## Installer guarantees now exercised

- Full source-manifest pre-validation and dry-run zero writes.
- Platform-specific `.codex` state excluded from portable manifest.
- Per-run unique backup roots.
- Atomic package, agent TOML, config and hooks replacement.
- Package-copy journal rollback on injected mid-copy failure.
- Malformed existing hooks/config fails before first user-state write.
- `VERSIONS.lock` and bounded `harness/smoke_gate.sh` are managed.
- Every real provider smoke is TERM→KILL bounded (default 45 seconds).

## Production backup handles

- `/home/codexloop/codex-loop-s-f2/backup-v2-20260812T114143Z`
- `/home/codexloop/codex-loop-s-f2/backup-v2-20260812T120443Z-2980689`
- `/home/codexloop/codex-loop-s-f2/backup-v2-20260812T120648Z-3021559`
- `/home/codexloop/codex-loop-s-f2/.codex/hooks.json.bak.20260812T114302Z`
- `/home/codexloop/.codex/config.toml.bak.20260812T114302Z`

## Open release blocker

K3 release review is not complete. Two explicit 16-wide audit attempts were born
with correct role/model and were visible in the lifecycle roster, but provider
sampling ended in 502/504. A direct `/v1/responses` K3 canary returned HTTP 504
with `upstream_server_error` / CDN origin timeout. The WSL supervised plan probe
ended `provider_stall_no_first_response`, rc=124, and set a five-minute backoff.
No K3 PASS may be inferred from these transport failures.

## Required K3 review after recovery

Read the wave-3 patch set and this evidence. Re-test installer transaction
boundaries, WSL hooks/config, 8765 freshness/model truth, ipybox lifecycle, and
the absence of roleless Sol births. Return `PASS` or `REDO` with P0/P1 evidence.
