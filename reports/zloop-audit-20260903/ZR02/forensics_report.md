# Command Forensics Report


### Timeline
09/02/2026 16:33:01 - call_cdc7ff48ab3743a496a1d962-stdout.log
09/02/2026 19:22:32 - call_4ad859e2eb50472ead7bf71d-stdout.log
09/02/2026 23:13:19 - call_ed523f98cd7e4ebeb48a25a4-stdout.log
09/03/2026 02:36:29 - call_83a267c2788f4d699a400d30-stdout.log
09/03/2026 03:04:22 - call_b4cc095b34c142e88dafd8fd-stdout.log
09/03/2026 05:16:39 - call_ebe9b3c37dd1484c830c39b1-stdout.log
09/03/2026 17:33:25 - call_61bcd8638908e865-stdout.log
09/03/2026 17:37:05 - call_e276fd3b506a9da4-stdout.log
09/03/2026 20:30:59 - call_6a5549026de5c3df-stdout.log
09/03/2026 20:48:55 - call_4362605197bd358f-stdout.log
09/03/2026 20:55:26 - call_8793d54d4fc601d8-stdout.log
09/03/2026 21:03:22 - call_a5121d8c8e53b6c0-stdout.log


### Log Excerpts


Log: call_4362605197bd358f-stdout.log
FAILED tests/test_cli.py::test_wave_start_mock - AssertionError: ERROR: wave ...
FAILED tests/test_cli.py::test_stage_promote_e2e - AssertionError: ERROR: wav...
FAILED tests/test_cli.py::test_stage_promote_automated_c2c_gate - AssertionEr...
FAILED tests/test_cli.py::test_stage_promote_dirty_canonical_and_staging_missing
============ 6 failed, 294 passed, 2 skipped in 124.61s (0:02:04) =============


Log: call_4ad859e2eb50472ead7bf71d-stdout.log
VENV_READY


Log: call_61bcd8638908e865-stdout.log
FAILED tests/test_supervisor.py::test_dead_owner_takeover_allows_wave - asser...
FAILED tests/test_supervisor.py::test_validation_errors_returned_nothing_written
FAILED tests/test_supervisor.py::test_failed_worker_status_fails_packet - ass...
FAILED tests/test_supervisor.py::test_scope_violation_blocks_packet - assert ...
============ 13 failed, 279 passed, 2 skipped in 320.97s (0:05:20) ============


Log: call_6a5549026de5c3df-stdout.log
FAILED tests/test_supervisor.py::test_dead_owner_takeover_allows_wave - asser...
FAILED tests/test_supervisor.py::test_validation_errors_returned_nothing_written
FAILED tests/test_supervisor.py::test_failed_worker_status_fails_packet - ass...
FAILED tests/test_supervisor.py::test_scope_violation_blocks_packet - assert ...
============ 15 failed, 285 passed, 2 skipped in 124.02s (0:02:04) ============


Log: call_83a267c2788f4d699a400d30-stdout.log
...............................................s........................ [ 27%]
........................................................................ [ 54%]
..................................................................s..... [ 81%]
..................................................                       [100%]
264 passed, 2 skipped in 164.83s (0:02:44)


Log: call_8793d54d4fc601d8-stdout.log
FAILED tests/test_cli.py::test_wave_start_mock - AssertionError: ERROR: wave ...
FAILED tests/test_cli.py::test_stage_promote_e2e - AssertionError: ERROR: wav...
FAILED tests/test_cli.py::test_stage_promote_automated_c2c_gate - AssertionEr...
FAILED tests/test_cli.py::test_stage_promote_dirty_canonical_and_staging_missing
============ 6 failed, 294 passed, 2 skipped in 125.60s (0:02:05) =============


Log: call_a5121d8c8e53b6c0-stdout.log

tests\test_cli.py:918: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_cli.py::test_stage_promote_automated_c2c_gate - assert 'c2c...
============ 1 failed, 299 passed, 2 skipped in 132.61s (0:02:12) =============


Log: call_b4cc095b34c142e88dafd8fd-stdout.log
........................................................................ [ 53%]
....................................................................s... [ 80%]
....................................................                     [100%]
266 passed, 2 skipped in 183.88s (0:03:03)


Log: call_cdc7ff48ab3743a496a1d962-stdout.log
GCOG-B WAKE PROBE: background task exited at 2026-09-02T16:33:01+08:00


Log: call_e276fd3b506a9da4-stdout.log
tests\test_c2c_gate.py:91: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_c2c_gate.py::test_wave_start_automated_c2c_and_waiver - sub...
FAILED tests/test_c2c_gate.py::test_promote_requires_result_c2c_or_automated_waiver
=================== 2 failed, 1 passed in 161.42s (0:02:41) ===================


Log: call_ebe9b3c37dd1484c830c39b1-stdout.log
........................................................................ [ 79%]
s.......................................................                 [100%]
270 passed, 2 skipped in 183.61s (0:03:03)


Log: call_ed523f98cd7e4ebeb48a25a4-stdout.log
9198178 round 3 (audit-of-audit fixes, 6 agents): D-16 hook cwd-scoping+plugin pkg; D-17 kimi-server gate; D-18 three-axis research semantics; D-19 searcher-only tools; D-20 controller death proof; D-21 redact adjacency + token sentinel oracle; zloop stage promote (M8 path closed); honest status ledger
86f8eac D-15: live vendor-test budget rules (probe <=2 turns; impl agents stub-only; 403=stop; log-auditable) — quota-exhaustion lesson
b071f0a D-14: opencodex/cliproxy route live-verified (P-CDX1 PASS 14.8s; P-CDX2 network canary blocked, no spawn_agent family, web_search present); manifest+artifacts corrected
<stdin>:11: SyntaxWarning: "\P" is an invalid escape sequence. Such sequences will not work in the future. Did you mean "\\P"? A raw string is also an option.
PROGRESS.md updated
