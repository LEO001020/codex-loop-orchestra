# codex-loop-s-f2 — Codex LOOP Orchestration Environment (F2 Cold Start Form)
<!-- size-justified: repository README; documents architecture overview, harness layout, and operational notes. -->

Multi-agent orchestration harness for the OpenAI Codex CLI: Sol (high tier) plans and
adjudicates; a zero-token deterministic script layer runs the state machine, acceptance,
retry, worktree isolation, and escalation routing; low-tier subagents execute. Files are
the single source of truth; Sol is only ever invoked on **planning** and **adjudication**
events.

- Package version pins: see `VERSIONS.lock` (codex-cli 0.147.0, Node v22.23.2, gpt-5.6 family).
- Integrity: `sha256sum -c SHA256SUMS` from the package root.
- Third-party model providers: LOOP is gateway-agnostic and needs no gateway to run.
  To route through an operator-managed external sidecar, see `docs/OPENCODEX.md`
  (one optional variable, `CODEX_LOOP_EXTERNAL_READY_URL`).
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

For a homogeneous CSV wave, `dispatch.py --mode csv` writes both
`data/dispatch/batch_w<N>.csv` and `batch_w<N>.call.json`. Invoke the
`spawn_agents_on_csv` call first. After its `output_csv_path` exists, execute
the call pack's `required_postprocess.argv`, followed by
`required_postprocess.then_argv`. These are mandatory production steps:
`csv_reconcile.py` copies each worktree-local report into LOOP root and emits
generation-aware terminal events, then `statemachine.py reconcile` consumes
them. Any nonzero postprocess exit stops wave advancement.

Golden case `tests/golden/test_g1_two_packet_parallel.py` is an executable end-to-end
example of exactly this flow (mock-backed).

### Desktop-wide LOOP mode (arbitrary target workspaces)

Codex Desktop binds each task to its own working directory, while user-level
hooks load for every project.  A LOOP launcher must therefore select an
application-wide orchestration mode; merely opening this package as a Desktop
project does not make tasks under other projects part of LOOP.

On this Windows deployment, activate the managed global mode with:

```powershell
E:\codex-LOOP\launchers\Set-Codex-LOOP-Mode.ps1 -Mode Activate
# Or open any target workspace and activate in one step:
E:\codex-LOOP\launchers\Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace E:\VPS
```

Activation performs a one-time, hash-recorded backup of the existing global
`AGENTS.md` and `hooks.json`, installs conditional user-level hooks, and writes
`E:\codex-LOOP\state\global-loop-mode.json`.  While the marker is active:

- `SessionStart` and `SubagentStart` inject the global working agreement plus
  this package's complete LOOP discipline into every task, regardless of cwd;
- lifecycle and PreToolUse hooks use this package as `LOOP_CONTROL_ROOT`;
- the target repository remains only the task workspace; F2 state, reports,
  packets, model routing, and the 8765 observer remain anchored here.

Switch ordinary Desktop work back to non-LOOP behavior with:

```powershell
E:\codex-LOOP\launchers\Set-Codex-LOOP-Mode.ps1 -Mode Deactivate
```

`Deactivate` keeps the conditional hooks installed but makes them no-ops.
`-Mode Restore` verifies and restores the exact pre-install global files for a
full rollback.  Start or resume a task after changing modes so its
`SessionStart` context reflects the selected mode.

---

## 2. F2 Switch-to-Production Toggles (all OFF as shipped)

The package ships in the **F2 cold start form** — semantically identical to the pure
subtraction stack F1 for safety and quality. All three production switches are
**single-key flips, no code changes**.

| # | Toggle | Where | Shipped state | Semantics when OFF (cold start) | Semantics when ON (production) | Switch condition (§9) |
|---|--------|-------|---------------|--------------------------------|-------------------------------|----------------------|
| 1 | `escalation.passthrough_enabled` | `~/.codex/config.toml` `[escalation]` | `false` | Every packet goes through L3; Sol per-packet/wave review NOT reduced; `escalation_log.jsonl` still records every trigger evaluation (free calibration data) | L0 all-green + no trigger hit → straight to serial merge queue, exempt from Sol per-packet review. Mechanical acceptance + human release gate STILL apply — L1/L2 can never release | Trigger-threshold calibration complete: enough escalation-log data from the cold-start period to confirm trigger table hit/miss rates (§10 S5) |
| 2 | `duty_officer.enforce` | `~/.codex/config.toml` `[duty_officer]` | `false` | Duty-officer triggers fire and are LOGGED ONLY; failures continue down the original dead-letter path to Sol | Duty rulings routed through the whitelist gate (`harness/duty_gate.py`): retryable/fixable with confidence ≥ θ re-dispatch; terminal/low-confidence → DEAD_LETTER | Recorded rulings during cold start show misclassification ≤ 5%; if it later exceeds 5%, narrow the whitelist (§10 S6) |
| 3 | ipybox block | `~/.codex/config.toml` `[mcp_servers.ipybox]` | Desktop default `enabled=false`; headless workers pass an explicit `enabled=true` override | Desktop stays a light control plane and does not pre-spawn one WSL/SRT sidecar per native agent | Headless workers retain the isolated persistent kernel for >5,000-token outputs and cross-call state | A real workload need (large-output digestion / cross-call state); guardrails in §3 below must hold. Kernel deadlock >1/30 runs → ops re-evaluation (§10 S7) |

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

**Dependency constraint (critical, VERSION_BOUND):** ipybox 0.9.2 is incompatible with
`mcp 2.0.0` — its dependency `mcpygen 0.1.4` imports `streamablehttp_client`, which was
removed in mcp 2.x, so an unconstrained install crashes on startup with an `ImportError`.
Always install/run with the constraint **`mcp<2`** (resolves to mcp 1.x). The pin may be
dropped once a future ipybox/mcpygen release fixes this.

**Enable steps — Option 1, pip form (preferred; verified in the install test):**
1. `pip install ipybox "mcp<2"` (installs ipybox 0.9.2 + mcp 1.x; pin per `VERSIONS.lock`).
2. Keep the Desktop-global block disabled and let `harness/dispatch.py` enable it
   explicitly for headless workers. For a dedicated non-Desktop CLI session,
   override `mcp_servers.ipybox.enabled=true` on that invocation. Do not enable
   the block globally for a wide Desktop-native wave: every native agent would
   otherwise pre-spawn a separate WSL/SRT MCP sidecar before its first cell.
   The pip-form command and arguments remain documented in
   `config/config.toml.example`.
3. Restart the Codex session (config is read at session start).

**Enable steps — Option 2, uvx form (alternative; no persistent pip install):**
1. Ensure `uv`/`uvx` is installed.
2. Uncomment the uvx-form block instead (`command = "uvx"`,
   `args = ["--with", "mcp<2", "ipybox", "--workspace", "./data"]` — the `--with` pin
   applies the `mcp<2` constraint on every run).
3. Restart the Codex session.

**Docker image note (fallback form only):** `ghcr.io/gradion-ai/ipybox:latest` is a
**kernel runtime container** (jupyter kernelgateway + resource server) managed by the
ipybox Python library's container-execution API — the image does NOT contain the ipybox
module and is NOT a stdio MCP server. Do not wire `command = "docker"` directly into
`[mcp_servers.ipybox]`; the Docker form requires an extra MCP bridge (the pip/uvx-installed
ipybox host process).

**Security guardrails (§10 S7 — do not relax):**
- ipybox is a **second execution boundary**; it does NOT inherit Codex `sandbox_mode`
  (0.9.2's `--sandbox` option uses Anthropic sandbox-runtime for kernel isolation;
  Docker isolation applies only to the kernel-runtime-container fallback form).
- **No network**: default deny egress from the kernel.
- **Credentials never mounted**: `~/.aws`, `~/.ssh`, `.env` files must never be mounted or
  copied into the workspace. The shipped blocks point `--workspace` at `./data` only.
- mcpygen external tool calls stay disabled in phase 1.
- Cell code enters the session log and is locally readable — no secrets in cells.
- Discipline (AGENTS.md): print ≤ 50 lines; return variable-name handles, not data bodies.
- Ops failure condition: kernel deadlock >1/30 runs triggers re-evaluation.

---

## 4. Five-layer lifecycle reclamation

Lifecycle truth is split deliberately. A green or stale Desktop activity row
is never accepted as proof that an agent or process is still running.

| Layer | Owner and trigger | Mechanical action | Durable evidence |
|---|---|---|---|
| 1. Logical task | `SubagentStop`, worker exit, or terminal rollout event | Mark the semantic task terminal; repeated terminal signals are idempotent | `data/lifecycle/native_roster.json`, `exec_roster.json` |
| 2. Native Codex slot | Host runtime after a terminal native-agent signal | `subagent_lifecycle.py` emits one `host_close_agent_required` request; the orchestrating host consumes it with `close_agent` | `data/lifecycle/close_requests.ndjson` and `close_request_emitted` |
| 3. Worker process tree | `lifecycle_supervisor.py` owns every single-mode `codex exec` handle | Wait; on non-zero exit emit `exec_failed`; on timeout/parent Stop terminate the Windows Job Object or POSIX process group | bounded stderr, exit code, timeout event, roster history |
| 4. Persistent reconciliation | Parent `Stop` and `SessionStart(startup/resume/clear/compact)` | Session-scoped generation cancellation, recover terminal rollouts, reap lost POSIX groups, and classify stale records without an LLM round | lifecycle event log and cold-start roster update |
| 5. Desktop-derived edge/UI | Offline maintenance only | Default dry-run; require strong terminal evidence; offline, create a SQLite API backup and update only `open -> closed` by exact child id | edge plan, backup hash, changed-row count |

The project never edits a live `state_5.sqlite`. Run the read-only plan first:

```text
python harness/desktop_edge_reconcile.py --state-db <CODEX_HOME>/state_5.sqlite \
  --roster data/lifecycle/native_roster.json --out data/lifecycle/desktop-edge-plan.json
```

`--apply --backup-dir <dir>` is an explicit offline repair operation and
fails closed if Desktop is running, the schema changed, or the database hash
differs from the dry-run snapshot.

The host-only native slot close is the one remaining platform boundary: a
normal hook process cannot invoke Codex's in-memory `close_agent` tool. The
request queue makes this visible and idempotent; it is not represented as an
already-released slot.

Claude Code's official lifecycle documentation informed this separation:
background sessions use a separate supervisor, agent-team shutdown uses an
explicit request/approve-or-reject exchange, SIGTERM terminates Bash process
trees, and completed background subagents may remain listed until the session
cleans its task list. The completed-task-list behavior is version-scoped to
Claude Code v2.1.208+. These are design references, not claims about Codex.
Sources (accessed 2026-08-10): [sub-agents](https://code.claude.com/docs/en/sub-agents),
[agent view](https://code.claude.com/docs/en/agent-view),
[agent teams](https://code.claude.com/docs/en/agent-teams), and
[headless mode](https://docs.anthropic.com/en/docs/claude-code/headless).

The configured open-slot ceiling is 50 (excluding the primary agent). V4 and
K3 are independent pools: each targets 16 with a low-water mark of 12 whenever
that pool has queued work, so both pools may sustain 32 active agents together.
`harness/refill_controller.py` counts only `running` agents as effective
concurrency. Idle/completed/shutdown-pending agents are reused when possible,
otherwise they generate `idle_reclaim_required`, `host_close_agent_required`,
and persistent refill debt. A spawn intent never clears that debt; only an
observed running agent does. Queue exhaustion or explicit `release_finalize`
is the only normal way to clear refill demand. The aggregate remains bounded
by the 50-slot ceiling, leaving capacity for retries and replacements.

---

## 5. Known Platform Bugs and Mitigations

All verified OPEN/current as of 2026-08-08 (research phase 2C/2D). Re-check before upgrades.

| Issue | Problem | Mitigation shipped in this package |
|-------|---------|-----------------------------------|
| [#28058](https://github.com/openai/codex/issues/28058) (OPEN) | MultiAgentV2 encrypts delegated prompts (`spawn_agent`/`send_message`) — your OWN session logs lose the readable audit trail | **Report-landing secondary channel**: executors land full reports in `data/reports/<pid>/` and the dispatcher logs every packet it dispatches to `events.ndjson`; the audit trail never depends on Codex rollouts. Alternative: pin codex < 0.137.0. (Also a reason `multi_agent_v2` is left unset — V1 is unaffected.) |
| [#35541](https://codexissues.com/issue/35541-root-agent-gets-stuck-emitting-wait-instead-of-a-requested-second-spawn-agent-ca) (OPEN) | Root agent perseverates on `wait` instead of issuing a second `spawn_agent`; in-session retries do NOT recover (related: [#34653](https://codexissues.com/issue/34653-bug-spawn-agent-hangs-indefinitely-without-returning-control) — spawn can hang >5 h) | **Session restart is the recovery path.** The file-based data plane makes this cheap: state lives in `progress_ledger.json`/`events.ndjson`, so a fresh session resumes exactly where the old one stopped (`statemachine.py reconcile`). `retry_classes.yaml` carries a `spawn_hang_or_lost` class; wrap spawns in `job_max_runtime_seconds`. |
| [#12862](https://github.com/openai/codex/issues/12862) (OPEN, enhancement) | No native `--worktree` flag — CLI offers no built-in write-parallel isolation (note: this is a feature request, not a leftover-worktree bug) | **`harness/worktree_pool.sh`** provides the worktree pool: per-packet `git worktree add` off a frozen base SHA, branch exclusivity, serial merge under a single `flock` lockfile, rebase-per-merge. |
| [#32031](https://codexissues.com/issue/32031-critical-ux-regression-multi-agent-v2-spawn-agent-hides-model-overrides-and-reje) (OPEN) | V2 `spawn_agent` hides model overrides (`hide_spawn_agent_metadata` defaults true) and full-history forks reject model overrides | **Config workaround**: this package pins models in the agent TOML files (highest-priority static guarantee) and leaves `multi_agent_v2` unset (V1 semantics). If you must use V2: set `[features.multi_agent_v2] hide_spawn_agent_metadata = false` and always spawn with `fork_turns: "none"` + explicit model/effort. |

---

## 6. First Deployment Verification Checklist (B-level delivery — you MUST run this)

This package was verified at **B-level**: the build environment had codex-cli 0.147.0
installed but no credentials (401 Unauthorized), so all executor behavior was proven
against `tests/mock_codex/`. Before trusting the harness with real work, verify on your
authenticated machine:

- [ ] `./install.sh` completed with no FAIL lines; re-run once to confirm idempotency (all SKIP).
- [ ] **Smoke gate against real codex**: `harness/smoke_gate.sh "$(pwd)"` → `SMOKE GATE: ALL
      ASSERTIONS PASS` (① all 4 roles spawnable, ② Codex event stream/rollout model matches each TOML pin,
      ③ write outside worktree rejected). Version line matches `VERSIONS.lock` (0.147.0) —
      on drift, re-run the gate after reading the changelog.
- [ ] **TOML loading**: `ls $CODEX_HOME/agents/` shows worker/reviewer/verifier/duty_officer;
      in a Codex session, spawning each by name uses the pinned model
      (worker/duty_officer → DeepSeek V4 Flash ultra; verifier/reviewer → Kimi K3 max;
      Sol remains the root orchestrator and final fallback).
- [ ] **Role spawning test**: `codex exec --skip-git-repo-check "reply OK"` returns rc=0; spawn a
      worker on a trivial packet and confirm `data/events.ndjson` gains a `SubagentStart` metering
      line (hook trusted via `/hooks` or `--dangerously-bypass-hook-trust`).
- [ ] **Lifecycle test**: complete one semantic-name child and verify exactly
      one native close request; timeout one test worker and confirm its
      descendant tree is gone; run Desktop edge reconciliation in dry-run
      mode and never use `--apply` while Desktop is online.
- [x] **Test suite** (mock-backed): WSL authority run with bytecode/cache disabled → 289 passed
      in 47.33s; raw log archived as lifecycle evidence.
- [ ] Run one real G1-level task (§1 step 3) and check: both packets MERGED, `wave-check` reports
      WAVE_DONE, Sol was woken ≤3 times (plan + finale + final review).
- [ ] Weekly: `python3 metering/e0_annotate.py` then `python3 metering/usage_reconcile.py`
      (exit 1 = discrepancy = investigate; the 25× price differential makes mis-routing visible).

---

## 7. Security Semantics Change Declaration (§10, S1–S9)

| # | Change | Direction | Content / failure condition |
|---|--------|-----------|------------------------------|
| S1 | Audit chain | fail-closed crypto → **fail-visible observation** (weakened — the only weakening) | Hash chain/dual-implementation voided; replaced by report-file secondary channel + SubagentStart single-line JSON + usage diff. **Failure condition:** multi-tenant/compliance scenario requires re-evaluation. |
| S2 | Audit threat model | anti-tamper → **anti-loss** (explicit restatement) | `reports/` + git history = loss prevention, not tamper prevention; while #28058 is unresolved the report-landing secondary channel must remain. |
| S3 | Execution semantics | Sol judges-then-acts → **script acts per predetermined table** (delegation) | Scripts execute only Sol-planning-authorized transitions; off-table events fail-visible to DEAD_LETTER (never silent). |
| S4 | Hook function | observation + lifecycle control plane | Hooks meter routes, enforce the Sol model-family tool gate, and write generation-scoped cancellation/close requests. They still cannot release native slots themselves; synchronous acceptance and offline DB repair remain fail-closed. |
| S5 | EK power semantics | **not weakened** | L1/L2 can only block or escalate, NEVER release; "pass" only exempts Sol per-packet review; high-risk classes deterministically direct to L3+L4 (hardcoded, non-overridable — verified against a doctored trigger table); passthrough stays closed until threshold calibration completes. |
| S6 | Duty officer | Tier 1 → **Tier 2 controlled expansion** (the only expansion, scope nailed) | Scope = whitelist retry/feed-back (two reversible actions); read-only, zero write tools, no spawn power; ruling ≠ authorization; ruling inputs appended to `events.ndjson`. **Failure condition:** misclassification >5% narrows the whitelist. |
| S7 | ipybox | **new second execution boundary** (explicit fence) | Docker isolation outside Codex sandbox_mode; default deny egress; credentials never mounted; mcpygen disabled phase 1; cell code locally readable. **Failure condition:** kernel deadlock >1/30 runs → ops re-evaluation. |
| S8 | Release gate | **unchanged** | Sol final review + human-triggered merge preserved; unattended release permanently out of bounds. |
| S9 | Injection surface | **narrowed + new surfaces declared** | Hook de-load-bearing removes a dynamic injection channel. New surfaces: failure reports → duty officer (mitigated: read-only role + enum-output whitelist gate) and report content → Sol summaries (mitigated: `sanitize.py` desensitization + instruction/data channel separation). |

---

## 8. Directory Structure

```
codex-loop-s-f2/
├── install.sh                 # idempotent installer (this file’s §1; --skip-smoke supported)
├── README.md                  # this file
├── VERSIONS.lock              # codex/node/model/ipybox(0.9.2, mcp<2) pins + verification level
├── SHA256SUMS                 # sha256sum -c verifiable full-file checksums
├── AGENTS.md                  # Sol discipline: single-pass planning, anti-polling, return
│                              #   convention, recoverable compression, kernel trigger rules
├── search_log.md              # 118 merged research searches (floor evidence)
├── work_commencement_certificate.md   # first-tool-call-was-search attestation
├── agents/                    # 4 role TOMLs (worker, reviewer, verifier, duty_officer)
├── config/
│   ├── config.toml.example    # [agents] knobs + 3 F2 toggles + commented ipybox blocks (pip/uvx)
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

## 9. Operating Rules That Must Hold (endpoint invariants, §13)

- Sol is invoked ONLY for planning and adjudication; waiting/polling/tallying/retry
  decisions/state recaps are script work (AGENTS.md negative discipline).
- L1/L2 can never release an artifact; release merge is human-triggered, always.
- Any off-table event → DEAD_LETTER + Sol wake summary. No silent discard path exists.
- Report files are the second truth source; hooks are fail-open and never load-bearing.
- Re-run the smoke gate after every Codex CLI upgrade (near-daily upstream releases).

### 9.1 Current Run View and Run Evidence Entry (PRESENT / PAST)

Two zero-LLM, read-only modules make the logical run outlive every model
context; the ledger remains the only execution authority:

- `python harness/run_view.py` — deterministic Current Run View derived from
  `data/progress_ledger.json` (+ exec-roster liveness): active/awaiting-root/
  recent-terminal packets with task, scope, worktree, attempt, dead-letter
  reason, and the `event_cursor` provenance. Injected once per task boundary
  (worker spawn prompts, SubagentStart) and as a small pointer at Root
  SessionStart — never per turn.
- `python harness/run_evidence.py build|show` — rebuildable index at
  `data/evidence/index.json` linking each packet to its reports (including
  archived prior attempts under `previous/`), exec-roster runs, dead letters,
  duty tickets, sol wakes, and recorded Root decisions. It is derived data,
  never a second task authority.
- `python harness/run_evidence.py record-decision --packet P --decision
  accept --ref reports/P/report.json` — durable provenance for a Root
  external decision that would otherwise exist only in the transcript
  (append-only `data/decisions/decisions.ndjsonl`; drives no transition).
- `worktree_pool.sh release` refuses dirty worktrees without a recoverable
  representation (`worktree_dirty_preserved`); `--save-patch` archives the
  exact tracked diff plus untracked content before removal. `merged` events
  record the post-merge integration commit SHA.

Historical conclusions may have been superseded: before relying on an old
report or decision, inspect that packet's later history (ledger history or
evidence-index disposition).

---

## 10. Runtime Success Closure (2026-08-10)

- Live four-role routing passed in one Codex Desktop task: worker and duty
  officer use the configured execution pin (currently `weiwu/glm-5.2`) at `ultra`; verifier and release
  reviewer used `weiwu-k3/kimi-k3` at `max`. Sol remains the root
  orchestrator, final adjudicator, and fallback.
- Release review is permanently pinned to K3 max in a read-only sandbox. It
  cannot release directly; both approval and rejection return to
  `SOL_ADJUDICATE` with per-wave provenance and idempotency checks.
- V4 and K3 use independent sustained pools, each target 16 with low-water
  12. Only agents observed as `running` count toward effective concurrency.
  `idle`, `completed`, and `shutdown_pending` agents do not count: pending
  work first reuses an idle agent; an agent that cannot be reused is closed,
  and the per-pool refill debt remains until a replacement is actually
  observed running. The shared spawned-agent ceiling is 50.
- The latest model-token-share report remains honestly blocked: cumulative
  `0.5682`, rolling 24 hours `0.3493`, and rolling 7 days `0.4461`, all above
  the 25 percent scheduling threshold. This confirms that the BLOCK signal is
  active; it is not converted to PASS merely because live routing succeeded.
- Codex Desktop was not restarted because both live provider routes returned
  successful responses with their exact configured model and effort.
