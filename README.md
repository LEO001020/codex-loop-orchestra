# codex-loop-s-f2 — Codex LOOP Orchestration Environment (F2 Cold Start Form)

Multi-agent orchestration harness for the OpenAI Codex CLI: Sol (high tier) plans and
adjudicates; a zero-token deterministic script layer runs the state machine, acceptance,
retry, worktree isolation, and escalation routing; low-tier subagents execute. Files are
the single source of truth; Sol is only ever invoked on **planning** and **adjudication**
events.

- Package version pins: see `VERSIONS.lock` (codex-cli 0.147.0, Node v22.23.2, gpt-5.6 family).
- Integrity: `sha256sum -c SHA256SUMS` from the package root.
- Delivery verification level: **B-level** — built and tested against the `tests/mock_codex/`
  layer; no authenticated Codex session was available in the build environment (401).
  You MUST run the First Deployment Verification Checklist (below) on your machine.

---

## 1. 5-Minute Deployment Path

Prerequisites: Node 22+, Codex CLI installed and authenticated (`codex login`), git repo to work in.

```bash
# 1. Unpack and install (~1 min). Installer is idempotent — safe to re-run.
tar -xzf codex-loop-s-f2-20260808.tar.gz
cd codex-loop-s-f2
./install.sh --repo /path/to/your/git/repo
#   ① checks Node 22+ / codex (prints install commands if missing, never auto-installs)
#   ② copies agents/*.toml -> $CODEX_HOME/agents/   (CODEX_HOME respected, default ~/.codex)
#   ③ merges config.toml.example keys into your config.toml (your keys never overwritten; diff printed)
#   ④ creates data/ skeleton + mounts the SubagentStart metering hook in your repo
#   ⑤ runs harness/smoke_gate.sh automatically (skip with --skip-smoke)

# 2. Smoke gate must be ALL PASS (~2 min). Re-run it after EVERY codex upgrade.
harness/smoke_gate.sh "$(pwd)"

# 3. First G1-level small task (~2 min): two parallel packets, disjoint paths.
#    Sol (your main Codex session) writes data/packets/w1-p01.json, w1-p02.json + dag.json
#    (4-field packet schema: packet_id / goal / authorized_paths / acceptance / constraints), then:
python3 harness/dag_assert.py                      # acyclic + non-intersecting paths gate
python3 harness/dispatch.py --mode single          # spawn executors in isolated worktrees
python3 harness/statemachine.py reconcile          # advance states from events + report files
python3 harness/diffvalidator.py ...               # L0 mechanical acceptance per packet
harness/worktree_pool.sh merge-queue               # serial merge under lockfile
python3 harness/statemachine.py wave-check         # missing-item check -> WAVE_DONE
```

Golden case `tests/golden/test_g1_two_packet_parallel.py` is an executable end-to-end
example of exactly this flow (mock-backed).

---

## 2. F2 Switch-to-Production Toggles (all OFF as shipped)

The package ships in the **F2 cold start form** — semantically identical to the pure
subtraction stack F1 for safety and quality. All three production switches are
**single-key flips, no code changes**.

| # | Toggle | Where | Shipped state | Semantics when OFF (cold start) | Semantics when ON (production) | Switch condition (§9) |
|---|--------|-------|---------------|--------------------------------|-------------------------------|----------------------|
| 1 | `escalation.passthrough_enabled` | `~/.codex/config.toml` `[escalation]` | `false` | Every packet goes through L3; Sol per-packet/wave review NOT reduced; `escalation_log.jsonl` still records every trigger evaluation (free calibration data) | L0 all-green + no trigger hit → straight to serial merge queue, exempt from Sol per-packet review. Mechanical acceptance + human release gate STILL apply — L1/L2 can never release | Trigger-threshold calibration complete: enough escalation-log data from the cold-start period to confirm trigger table hit/miss rates (§10 S5) |
| 2 | `duty_officer.enforce` | `~/.codex/config.toml` `[duty_officer]` | `false` | Duty-officer triggers fire and are LOGGED ONLY; failures continue down the original dead-letter path to Sol | Duty rulings routed through the whitelist gate (`harness/duty_gate.py`): retryable/fixable with confidence ≥ θ re-dispatch; terminal/low-confidence → DEAD_LETTER | Recorded rulings during cold start show misclassification ≤ 5%; if it later exceeds 5%, narrow the whitelist (§10 S6) |
| 3 | ipybox block | `~/.codex/config.toml` `[mcp_servers.ipybox]` (commented) | disabled | No persistent kernel; large outputs land on disk as files | Persistent Docker-isolated Python kernel for >5,000-token outputs and cross-call state | Docker available + a real workload need (large-output digestion / cross-call state); guardrails in §3 below must hold. Kernel deadlock >1/30 runs → ops re-evaluation (§10 S7) |

**Deployment path summary (§9): cold start → calibration → production.**

1. **Cold start (as shipped):** all three toggles off. Full Sol review on every packet;
   escalation and duty-officer logs accumulate at zero risk delta vs F1.
2. **Calibration:** periodically review `data/escalation_log.jsonl` (trigger hit rates,
   raw_action vs upgraded action) and duty-officer recorded rulings (would-have-been
   accuracy). Run `metering/e0_annotate.py` + `metering/usage_reconcile.py` weekly.
3. **Production:** flip toggle 1 when trigger thresholds are calibrated; flip toggle 2
   when duty misclassification ≤5%; enable ipybox only on demonstrated need. Each flip
   is independent and reversible.

---

## 3. ipybox: Enable Steps and Security Guardrails

Disabled by default. Enable only when a workload needs >5,000-token output digestion or
compression-immune cross-call state.

**Enable steps:**
1. Install Docker; pull the pinned image: `docker pull ghcr.io/gradion-ai/ipybox:latest`
   (pin the tag recorded in `VERSIONS.lock`).
2. Uncomment the entire `[mcp_servers.ipybox]` block in `~/.codex/config.toml`
   (a ready block with the correct docker args ships in `config/config.toml.example`).
3. Restart the Codex session (config is read at session start).

**Security guardrails (§10 S7 — do not relax):**
- ipybox is a **second execution boundary**: Docker isolation; it does NOT inherit Codex
  `sandbox_mode`.
- **No network**: default deny egress (`--network none` in the shipped block).
- **Credentials never mounted**: `~/.aws`, `~/.ssh`, `.env` files must never be bind-mounted.
  The shipped block mounts only `./data`.
- mcpygen external tool calls stay disabled in phase 1.
- Cell code enters the session log and is locally readable — no secrets in cells.
- Discipline (AGENTS.md): print ≤ 50 lines; return variable-name handles, not data bodies.
- Ops failure condition: kernel deadlock >1/30 runs triggers re-evaluation.

---

## 4. Known Platform Bugs and Mitigations

All verified OPEN/current as of 2026-08-08 (research phase 2C/2D). Re-check before upgrades.

| Issue | Problem | Mitigation shipped in this package |
|-------|---------|-----------------------------------|
| [#28058](https://github.com/openai/codex/issues/28058) (OPEN) | MultiAgentV2 encrypts delegated prompts (`spawn_agent`/`send_message`) — your OWN session logs lose the readable audit trail | **Report-landing secondary channel**: executors land full reports in `data/reports/<pid>/` and the dispatcher logs every packet it dispatches to `events.ndjson`; the audit trail never depends on Codex rollouts. Alternative: pin codex < 0.137.0. (Also a reason `multi_agent_v2` is left unset — V1 is unaffected.) |
| [#35541](https://codexissues.com/issue/35541-root-agent-gets-stuck-emitting-wait-instead-of-a-requested-second-spawn-agent-ca) (OPEN) | Root agent perseverates on `wait` instead of issuing a second `spawn_agent`; in-session retries do NOT recover (related: [#34653](https://codexissues.com/issue/34653-bug-spawn-agent-hangs-indefinitely-without-returning-control) — spawn can hang >5 h) | **Session restart is the recovery path.** The file-based data plane makes this cheap: state lives in `progress_ledger.json`/`events.ndjson`, so a fresh session resumes exactly where the old one stopped (`statemachine.py reconcile`). `retry_classes.yaml` carries a `spawn_hang_or_lost` class; wrap spawns in `job_max_runtime_seconds`. |
| [#12862](https://github.com/openai/codex/issues/12862) (OPEN, enhancement) | No native `--worktree` flag — CLI offers no built-in write-parallel isolation (note: this is a feature request, not a leftover-worktree bug) | **`harness/worktree_pool.sh`** provides the worktree pool: per-packet `git worktree add` off a frozen base SHA, branch exclusivity, serial merge under a single `flock` lockfile, rebase-per-merge. |
| [#32031](https://codexissues.com/issue/32031-critical-ux-regression-multi-agent-v2-spawn-agent-hides-model-overrides-and-reje) (OPEN) | V2 `spawn_agent` hides model overrides (`hide_spawn_agent_metadata` defaults true) and full-history forks reject model overrides | **Config workaround**: this package pins models in the agent TOML files (highest-priority static guarantee) and leaves `multi_agent_v2` unset (V1 semantics). If you must use V2: set `[features.multi_agent_v2] hide_spawn_agent_metadata = false` and always spawn with `fork_turns: "none"` + explicit model/effort. |

---

## 5. First Deployment Verification Checklist (B-level delivery — you MUST run this)

This package was verified at **B-level**: the build environment had codex-cli 0.147.0
installed but no credentials (401 Unauthorized), so all executor behavior was proven
against `tests/mock_codex/`. Before trusting the harness with real work, verify on your
authenticated machine:

- [ ] `./install.sh` completed with no FAIL lines; re-run once to confirm idempotency (all SKIP).
- [ ] **Smoke gate against real codex**: `harness/smoke_gate.sh "$(pwd)"` → `SMOKE GATE: ALL
      ASSERTIONS PASS` (① all 4 roles spawnable, ② metering model field matches each TOML pin,
      ③ write outside worktree rejected). Version line matches `VERSIONS.lock` (0.147.0) —
      on drift, re-run the gate after reading the changelog.
- [ ] **TOML loading**: `ls $CODEX_HOME/agents/` shows worker/reviewer/verifier/duty_officer;
      in a Codex session, spawning each by name uses the pinned model
      (worker/duty_officer → gpt-5.6-luna, verifier → gpt-5.6-terra, reviewer → gpt-5.6).
- [ ] **Role spawning test**: `codex exec --skip-git-repo-check "reply OK"` returns rc=0; spawn a
      worker on a trivial packet and confirm `data/events.ndjson` gains a `SubagentStart` metering
      line (hook trusted via `/hooks` or `--dangerously-bypass-hook-trust`).
- [ ] **Test suite** (mock-backed, should pass anywhere): `python3 -m pytest tests/ -q` → 154 passed.
- [ ] Run one real G1-level task (§1 step 3) and check: both packets MERGED, `wave-check` reports
      WAVE_DONE, Sol was woken ≤3 times (plan + finale + final review).
- [ ] Weekly: `python3 metering/e0_annotate.py` then `python3 metering/usage_reconcile.py`
      (exit 1 = discrepancy = investigate; the 25× price differential makes mis-routing visible).

---

## 6. Security Semantics Change Declaration (§10, S1–S9)

| # | Change | Direction | Content / failure condition |
|---|--------|-----------|------------------------------|
| S1 | Audit chain | fail-closed crypto → **fail-visible observation** (weakened — the only weakening) | Hash chain/dual-implementation voided; replaced by report-file secondary channel + SubagentStart single-line JSON + usage diff. **Failure condition:** multi-tenant/compliance scenario requires re-evaluation. |
| S2 | Audit threat model | anti-tamper → **anti-loss** (explicit restatement) | `reports/` + git history = loss prevention, not tamper prevention; while #28058 is unresolved the report-landing secondary channel must remain. |
| S3 | Execution semantics | Sol judges-then-acts → **script acts per predetermined table** (delegation) | Scripts execute only Sol-planning-authorized transitions; off-table events fail-visible to DEAD_LETTER (never silent). |
| S4 | Hook function | load-bearing → **pure observation metering** (delegation) | Hooks are fail-open and bear no security function; all security assertions live in synchronous acceptance scripts, fail-closed. |
| S5 | EK power semantics | **not weakened** | L1/L2 can only block or escalate, NEVER release; "pass" only exempts Sol per-packet review; high-risk classes deterministically direct to L3+L4 (hardcoded, non-overridable — verified against a doctored trigger table); passthrough stays closed until threshold calibration completes. |
| S6 | Duty officer | Tier 1 → **Tier 2 controlled expansion** (the only expansion, scope nailed) | Scope = whitelist retry/feed-back (two reversible actions); read-only, zero write tools, no spawn power; ruling ≠ authorization; ruling inputs appended to `events.ndjson`. **Failure condition:** misclassification >5% narrows the whitelist. |
| S7 | ipybox | **new second execution boundary** (explicit fence) | Docker isolation outside Codex sandbox_mode; default deny egress; credentials never mounted; mcpygen disabled phase 1; cell code locally readable. **Failure condition:** kernel deadlock >1/30 runs → ops re-evaluation. |
| S8 | Release gate | **unchanged** | Sol final review + human-triggered merge preserved; unattended release permanently out of bounds. |
| S9 | Injection surface | **narrowed + new surfaces declared** | Hook de-load-bearing removes a dynamic injection channel. New surfaces: failure reports → duty officer (mitigated: read-only role + enum-output whitelist gate) and report content → Sol summaries (mitigated: `sanitize.py` desensitization + instruction/data channel separation). |

---

## 7. Directory Structure

```
codex-loop-s-f2/
├── install.sh                 # idempotent installer (this file’s §1; --skip-smoke supported)
├── README.md                  # this file
├── VERSIONS.lock              # codex/node/model/ipybox pins + verification level
├── SHA256SUMS                 # sha256sum -c verifiable full-file checksums
├── AGENTS.md                  # Sol discipline: single-pass planning, anti-polling, return
│                              #   convention, recoverable compression, kernel trigger rules
├── search_log.md              # 118 merged research searches (floor evidence)
├── work_commencement_certificate.md   # first-tool-call-was-search attestation
├── agents/                    # 4 role TOMLs (worker, reviewer, verifier, duty_officer)
├── config/
│   ├── config.toml.example    # [agents] knobs + 3 F2 toggles + commented ipybox block
│   ├── triggers.yaml          # L1 trigger table: EK partition (31) + duty partition (4)
│   ├── escalation_ladder.yaml # L0–L4 ladder + power semantics
│   ├── roles.yaml             # role quadruple table (model/effort/sandbox/tier)
│   └── retry_classes.yaml     # 14 regex retry classes + budgets + jitter params
├── harness/                   # zero-token deterministic layer (15 scripts):
│   │                          #   statemachine.py (23 transitions), dag_assert.py,
│   │                          #   dispatch.py, diffvalidator.py, acceptance_replay.sh,
│   │                          #   retry.py, worktree_pool.sh, missing_check.sh,
│   │                          #   verdict_check.py, sanitize.py, trigger_eval.py,
│   │                          #   verdict_aggregate.py, duty_gate.py, summary_synth.py,
│   └── smoke_gate.sh          #   smoke gate (3 assertions + version check)
├── hooks/subagent_start_meter.sh      # SubagentStart metering hook (fail-open, 1-line JSON)
├── metering/                  # e0_annotate.py (per-turn T1–T10 annotation),
│                              # usage_reconcile.py (weekly 4-check reconciliation)
├── data/                      # runtime skeleton: packets/ reports/ dead_letters/,
│                              # events.ndjson, escalation_log.jsonl, progress_ledger.json,
│                              # lessons.jsonl (install.sh replicates this in your repo)
└── tests/                     # 154 tests: unit (119) + 23 transitions & adversarial (28)
                               # + golden G1–G5 (7); mock_codex/ = B-level codex stand-in
```

## 8. Operating Rules That Must Hold (endpoint invariants, §13)

- Sol is invoked ONLY for planning and adjudication; waiting/polling/tallying/retry
  decisions/state recaps are script work (AGENTS.md negative discipline).
- L1/L2 can never release an artifact; release merge is human-triggered, always.
- Any off-table event → DEAD_LETTER + Sol wake summary. No silent discard path exists.
- Report files are the second truth source; hooks are fail-open and never load-bearing.
- Re-run the smoke gate after every Codex CLI upgrade (near-daily upstream releases).
