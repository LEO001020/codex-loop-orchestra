# ZLoop Defect / Contract Traceability (VOL-18 §G)

> Map: every **currently-implemented** production invariant (VOL-01 §4, I1–I44) → the automated test(s) that verify it; every not-yet-covered invariant → the milestone that owns it.
> Invariants NOT enforced by code today are listed under "pending" — never claimed as covered (no-fake-success, VOL-01 §3.3). VOL-01 §4 allows either an automated test **or** a contract probe as evidence; probe evidence cites `artifacts/capabilities/manifest.json`.
> Old-LOOP defect traceability follows the actual code tree (VOL-02 §7; 4 phantom items excluded per P0-4 / D-4).
> Snapshot: 2026-09-02, round 2 (post razor pass D-7..D-12). Parallel agents landed during this snapshot window: `materialize.py`+`promote.py` + `backend/` + `c2c.py` + `research/` + `worker_env.py`, **with** test files (`test_materialize.py`, `test_promote.py`, `test_backend.py`, `test_c2c.py`, `test_research.py`, `test_worker_env.py`, observed 21:14–21:17). These suites are owned by the parallel test agent and were **not executed** by the documentation agent; rows below cite the test files as written, not as run-green.

---

## 1. Covered invariants (code + at least one automated test)

| Invariant | Test(s) | Status |
|---|---|---|
| **I1** disable(ZLoop) ⇒ native semantics restored | `tests/test_install.py::test_install_preserves_unrelated_keys_and_uninstall`, `test_uninstall_no_config`, `test_install_refuses_foreign_hooks` (uninstall removes ONLY the zloop hooks key; foreign hooks never touched; hook process is fail-soft, always exit 0) | covered (config level). Live check "no injection / no hook process / latency parity" needs a new session (P-HK4); `zloop rollback` drill is M10 |
| **I3** H0 write failure ⇒ degraded, native cognition continues | `tests/test_hook.py::test_malformed_stdin_fail_soft`, `test_hook_module_always_returns_zero`; `tests/test_foundation.py::test_journal_fail_soft_on_garbage`; `tests/test_history.py::test_search_fail_soft` | covered |
| **I4** S commit failure ⇒ stop lifecycle mutation (fail-closed) | `tests/test_foundation.py::test_mutation_rollback`, `test_corrupt_db_fail_closed`; `tests/test_cli.py::test_serror_run_list_exit_3` (exit 3); `tests/test_wave.py::test_run_wave_fences_and_validates` (invalid wave writes nothing) | covered |
| **I5** one controller per run at any time | `tests/test_foundation.py::test_runlock_exclusive` (OS `run.lock`: second owner refused) | covered (OS-lock arm). CAS-token arm is I43 (pending M6) |
| **I6** accept iff `stage_revision ∧ packet_revision ∧ active_launch_id` match (and packet RUNNING) | `tests/test_wave.py::test_accept_result_i6_fence` (all four failure reasons), `test_run_wave_reports_all`, `test_run_wave_stale_result_rejected` | covered |
| **I7** stale attempt/launch/revision ⇒ evidence only, never materialize/promote | `tests/test_wave.py::test_late_result_guard`, `test_run_wave_stale_result_rejected` (stays RUNNING + rejection event), `test_supersede_stage_revision_fencing` (SUPERSEDED + active_launch cleared) | covered |
| **I9** concurrent writable resources disjoint or explicit ordering | `tests/test_wave.py::test_validate_scope_overlap_needs_dep`, `test_validate_ok_disjoint_scopes` (write_scope overlap without `depends_on` refused) | covered (wave-level scope disjointness). General `resource_leases` path pending M6 |
| **I13** raw secret never persists before redaction/hash | `tests/test_foundation.py::test_redact_patterns`, `test_redact_pem_block`, `test_redact_recursive_and_secret_filenames`, `test_scan_secrets`, `test_journal_append_and_redaction`; `tests/test_hook.py::test_redaction_never_leaks_into_journal`; `tests/test_history.py::test_search_substring_case_insensitive_and_redaction`; `tests/test_worker_env.py` (P1-8: worker/research child envs built by ALLOWLIST only — machine secrets like `ALIBABA_TOKEN_PLAN_API_KEY` never reach a subprocess; secret-shaped packet extras rejected); `tests/test_c2c.py::test_content_and_response_redacted`; `tests/test_research.py::test_redaction_in_blob_and_claim` | covered |
| **I14** current reality > rebuilt H1.machine > H1.semantic prose | `tests/test_checkpoint.py::test_machine_fields_stripped` (machine state never reaches disk); `tests/test_hook.py::test_recovery_compact_with_binding` (recovery block carries the "Current files/runtime/oracles override stale checkpoint prose" tail) | covered (semantic arm). H1.machine rebuild-ordering test pending M8 |
| **I15** facts/decisions without resolvable evidence refs ⇒ unverified_notes | `tests/test_checkpoint.py::test_invalid_evidence_refs_moved_to_unverified_notes` | covered |
| **I20** production vendor deps DOCUMENTED or OBSERVED+fallback | Contract-probe arm: `docs/VENDOR_CONTRACTS.md` + `artifacts/capabilities/manifest.json` (every load-bearing contract + re-confirm probe + fallback; D-6/D-7 record the two BLOCKED/DEGRADED entries) | covered (registry, per VOL-01 §4 contract-probe arm) |
| **I21** Git/external = physical-effect oracle; S = logical authority | `tests/test_workspace.py::test_enumerate_delta_untracked_modified_renamed`, `test_enumerate_delta_clean_and_deleted` (delta machine-parsed from `git status --porcelain=v2 -z`, never worker self-reports) | covered (delta-reconstruction arm). Promote-side Git-oracle reconcile tests pending with M6 |
| **I22** SQLite version gate; no production WAL on affected runtime | `tests/test_foundation.py::test_journal_profile_gate` (3.50.4 ⇒ DELETE+EXTRA; 3.50.7/3.51.3/3.53.4 ⇒ WAL); probe P-SQL1 (gate readback + crash-atomicity, `artifacts/probes/P-SQL1.json`) | covered (D-1/D-7: local v1 = DELETE+EXTRA) |
| **I28** SessionStart recovers exact session binding only; `clear` default no-recovery | `tests/test_hook.py::test_recovery_clear_requires_resume_after_clear`, `test_recovery_unbound_session_stays_silent`, `test_recovery_compact_with_binding`; `tests/test_cli.py::test_attach_marker_and_binding_status` (`--resume-after-clear`) | covered |
| **I32** binding = exact single-use claim token; no cwd/latest-run guessing | `tests/test_foundation.py::test_claim_lifecycle` (replay + forged rejected), `test_claim_expiry`, `test_binding_epoch_increments`; `tests/test_hook.py::test_bind_claim_happy_path`, `test_bind_replay_same_nonce_rejected`, `test_forged_nonce_stays_silent`, `test_bind_requires_bash_tool`; `tests/test_cli.py::test_run_start_marker_first_and_pending_claim`, `test_wait_claim_timeout_exit_4`, `test_wait_claim_success`, `test_commands_require_registered_project` (unregistered cwd ⇒ exit 5, never a guess) | covered |
| **I34** independent mutable workspace per launch_id | `tests/test_wave.py::test_run_wave_reports_all` (fresh launch_id + `workspace_id = stage/packet/launch` per packet); `tests/test_workspace.py::test_create_and_remove_worktree`, `test_create_clone` (physical provisioning; probe P-WS1: clone_strong fully isolates, worktree_fast does not) | covered (fresh-workspace arm). Quarantine-then-retry for ambiguous crashes pending M6/M7 |
| **I37** production writable wave from verified immutable clean Stage base | `tests/test_stage.py::test_check_stage_base` ('' ⇒ clean, else BLOCKED_DIRTY_BASE, fail-closed); `tests/test_wave.py::test_run_wave_fences_and_validates` (dirty base refuses the wave) | covered |
| **I27** Kimi lane must see explicit terminal completion; process exit / tool event ≠ success | `tests/test_research.py::test_extract_answer_last_real_assistant`, `test_extract_answer_no_assistant`, `test_ask_full_flow_against_stub` (terminal answer = last real assistant message; tool-only and trailing `meta:` records skipped — P-KIM1 #1897 discipline); backend plane: `tests/test_backend.py::test_collect_completed_with_none_final_response` (`final_response=None` judged on explicit terminal completion, not text presence) | covered (stub-server contract; the one quota-consuming live check is gated behind `ZLOOP_KIMI_LIVE`) |
| **I30** canonical HEAD/dirty drift ⇒ no promotion (re-stage/rebase required) | `tests/test_promote.py::test_dirty_state_clean_and_dirty`, `test_promote_dirty_canonical_blocked`, `test_promote_dirty_digest_mismatch_blocked`, `test_promote_head_drift_blocked` (all pre-checks before any repo write — failures leave the repo untouched) | covered |
| **I38** host re-applies worker delta onto CURRENT stage snapshot + re-runs acceptance before MATERIALIZED | `tests/test_materialize.py::test_materialize_success_commit_and_trailers`, `test_materialize_second_packet_lands_on_current_snapshot` (delta applied onto the CURRENT snapshot, not the worker's base), `test_materialize_acceptance_failure_no_transition` (worker/host-green on its own snapshot proves nothing; failed acceptance leaves the candidate commit but no state transition), `test_materialize_scope_violation`, `test_materialize_requires_reported_packet` | covered |
| **I39** checked-out promotion keeps ref/index/worktree consistent; no bare update-ref | `tests/test_promote.py::test_promote_ff_only_success` (`git merge --ff-only` + exact-landing check), `test_promote_not_descendant_blocked`, `test_promote_requires_intent_and_stage` (intent-first ordering, VOL-11 §2), `test_reconcile_recovered_when_ref_already_at_staged` / `test_reconcile_retryable_when_ref_still_at_expected_head` / `test_reconcile_blocked_on_third_party_change` / `test_reconcile_blocked_on_dirty_worktree` / `test_reconcile_blocked_when_trailers_do_not_match` / `test_reconcile_no_dangling_intents` (Git as physical oracle for dangling intents, VOL-11 §4) | covered |
| **I42** research lanes run outside canonical writable project/secrets | `tests/test_research.py::test_lane_runs_in_isolated_temp_cwd` (temp cwd per question, never the project dir or any parent, cleaned up afterwards, independent per question) | covered |

## 2. Pending invariants (not yet enforced by code, or code present but untested at snapshot)

| Invariant | Owning milestone | Note |
|---|---|---|
| I2 kill(model context) ⇒ captured H0 survives | M2 gate / M8 chaos | Per-event append+flush and torn-line detection exist (`test_verify_chain_ok_and_break`); explicit kill-context drill pending |
| I8 deps consume only MATERIALIZED stage snapshot lineage | M6 | `validate_wave` checks dep existence only; MATERIALIZED-only gate pending |
| I10 worker/model cannot write S / H authority paths / canonical refs | M6/M7 | Structural today (MockBackend holds no S handle); enforce when real backends land |
| I11 H2/derived index deletion ⇒ rebuildable | M4 gate / M8 | H2 is currently a live derived view (nothing persisted); delete/rebuild drill pending |
| I12 web/C2C cannot change scope/risk/S/promotion policy | M5 | Host-side layers landed this round and are evidence-only by construction (`c2c.py` prepare/record writes packets + audit events; research broker records `trust=external_untrusted` — `test_c2c.py`, `test_research.py::test_trust_always_external_untrusted`). Outage-injection / bridge-privilege chaos still pending |
| I16 C2C evidence ≠ task authority; host risk floor controls blocking | M5 | Stage risk floors already implemented+tested (`test_floor_rules_builtin`); C2C linkage pending |
| I17 NORMAL C2C outage must not block mechanical path | M5 | |
| I18 SearchHealth ≠ InferenceHealth (independent breakers) | M4 | Broker failure isolation exists (`test_failure_isolation_broker_never_raises`); the independent circuit breaker itself pending. KEEP source: old `provider_health.py` (VOL-02 §7) |
| I19 native subagent lifecycle ≠ first-class worker lifecycle | M7/M8 | By construction (workers only via `AgentBackend`); test with real lanes |
| I23 shared dataset/cache inputs immutable or lease-protected | M6 | `resource_leleases` table reserved in schema, unused |
| I24 no worker reads another worker's mutable workspace | M7 | Partial probe evidence P-WS1 (HIGH/CRITICAL ⇒ clone_strong) |
| I25 G-COG RETURN/WAKE measured; detached-no-wake ≠ autonomous | M7 | P-PLAT1 partial (wake primitive observed 2026-09-02 ⇒ D-2); full probe BLOCKED-requires-new-session |
| I26 10–30 is a cap; actual p by frontier + measured Pareto | M9 | |
| I29 first-class worker network_policy=none physically enforced | M7 | Shape validated now (`test_validate_bad_network_policy`); `CodexSdkBackend` documents that `network`/`max_turns` are NOT physically enforced yet (SDK default `network_access=false` + double canary = M8 probe); canaries = P-CDX2 (BLOCKED-manual, D-6) |
| I31 live/irreversible actions always explicit human authorization | M8/M10 | Classification arm covered (keyword floors raise live trading/withdrawal/deploy to CRITICAL, `test_floor_rules_builtin`); the human-authorization gate itself pending |
| I33 checkpoint frequency follows durable handoff, not fixed cadence | M8 | By construction (no periodic writer exists); distortion test pending |
| I35 worker nested-agent spawn closed by verified config/tool catalog | M7 | Config arm landed+tested: `tests/test_backend.py::test_agents_disabled_config` (`agents.enabled=false` + `features.multi_agent=false` at client construction). Catalog verification is a live probe — BLOCKED-manual (D-6) |
| I36 network policy enforced by execution boundary, not prompt | M7 | (see I29 note) |
| I40 HIGH/CRITICAL external claims need promoted/verified primary evidence | M8 | |
| I41 / I41b C2C transport measured; no claim to drive ZCode Browser; unknown ≠ cross-family | M5 | Honest-coverage arm landed: `c2c.py` does no browser automation, records `audit_coverage="text_packet_only"` and observable-only identity (`test_c2c.py`). Live transport measurement = P-C2C1 (PENDING) |
| I43 S single owner = controller token CAS (pid+pid_start liveness, no long-held OS lock) | M6 | D-8 implemented in `src/zloop/db.py` (`claim_controller`/`release_controller`/`request_cancel`, schema v2) — **no dedicated unit test at snapshot**; lands with the supervisor |
| I44 provider thread state is physical evidence only; stale-active never revives a launch | M7 | Discipline arm landed: `tests/test_backend.py::test_wait_unknown_maps_to_failed` (SDK error ⇒ `unknown`, launch treated ambiguous — provider status never becomes S authority). Live stale-active repro = P-CDX3, BLOCKED-manual (D-6) |

## 3. Old-LOOP defect traceability (VOL-02 §7 ground truth; P0-4 / D-4)

Extraction table follows the **actual** old code tree, not the Gen-8 draft table (4 phantom items excluded).

| Old-LOOP behavior | New contract | Test |
|---|---|---|
| torn-line skip (journal readers) | `evidence.read_journal` torn-line marker + chain verify flags it | `test_verify_chain_ok_and_break`, `test_history_verify_detects_tampering` |
| `os.replace` atomic writes | `install._atomic_write`, `checkpoint._atomic_write` (tmp + replace) | `test_install_writes_five_events_and_status`, `test_valid_capsule_roundtrip` |
| one packet = one worktree = one branch | I34 fresh workspace per launch | `test_run_wave_reports_all` |
| attempts ledger | `attempts` table (D-12: ordinal derived from launches; table drop planned v1.1) | `test_run_wave_reports_all` |
| double hook registration (global_hooks.json + ProgramData mirror) | ZLoop registers ONLY user-level config; the ProgramData mirror is the P-HYG1 legacy, mitigated via `CODEX_LOOP_REQUIREMENTS_TOML` (D-5), removal pending user confirmation | `test_install_over_empty_hooks_ok_and_no_home_write` (real home never written) |
| advisory locks | `db.RunLock` (cross-process OS lock) + journal file lock | `test_runlock_exclusive`, `test_two_journals_interleave_safely` |
| `provider_health.py` (SearchHealth ≠ InferenceHealth) | I18 (M4, pending) | — |
| refill debt / role pool / duty queue / roster reconciliation | **NOT APPLICABLE** — hard ban VOL-01 §3.11; deliberately not carried | — |
| Blackboard/DAG "dump" | **NOT APPLICABLE** — was `emit_context` working-agreement injection; ZLoop injects only a bounded (≤1200 chars) SessionStart recovery block | `test_recovery_compact_with_binding` (budget asserted) |
| vector DB/RAG manager; Compatibility Gateway; IPybox-as-memory-authority | **NOT APPLICABLE** — phantom/misplaced per P0-4; excluded from the extraction table | — |
| old tree had no `prev_event_hash` chain | new contract: journal `prev_line_hash` chain + payload hash | `test_verify_chain_ok_and_break`, `test_history_verify_detects_tampering` |

## 4. Maintenance rule

- New code that enforces an invariant MUST land with its test in the same round; this table is updated in the same commit (VOL-21: "测试先于下一个功能").
- A "covered" row without a passing test is a defect; a "pending" row claimed as done is a project failure (VOL-01 §3.3).
- Rows citing the round-2 parallel suites (`test_materialize/test_promote/test_backend/test_c2c/test_research/test_worker_env`) were verified as **written**, not as executed; flip them to "run-green" only after the suite owner reports a green run.
- When I43's supervisor tests land (M6), move that row into §1 with the concrete test names.
