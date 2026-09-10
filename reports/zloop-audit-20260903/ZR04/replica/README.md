# zcode-loop-orchestra — Native ZCode Multi-Agent Harness & Triple-Audit Loop
<!-- size-justified: repository README; architecture overview, harness layout, verification gates, and operational playbook. -->

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests: 293 passed](https://img.shields.io/badge/Tests-293%20passed-brightgreen.svg)]()
[![Platform: Windows 10/11 x64](https://img.shields.io/badge/Platform-Windows%2010%2F11%20x64-blue.svg)]()
[![Python: 3.14+](https://img.shields.io/badge/Python-3.14+-informational.svg)]()
[![ZCode: v3.10.2](https://img.shields.io/badge/ZCode-v3.10.2-blueviolet.svg)]()

Multi-agent orchestration harness and resilient control loop built natively around **ZCode**:
- **Sole Cognition Authority**: The ZCode root session (Gemini 3.8 Flash / GLM-5.3) plans, directs, and adjudicates.
- **Triple-Audit Architecture (D-25)**:
  1. **C2C-P Planning Gate**: Strategic counter-plan review via ChatGPT web before wave dispatch (lowest token cost, highest reasoning value).
  2. **Mechanical Materialization Gate**: Automated local test suite execution with atomic `git reset --hard` rollback on failure (P0-2 poison-proof).
  3. **C2C-A Promotion Gate**: Heterogeneous code review (GLM-5.3 dedicated subagent or cross-family panel) before canonical branch fast-forward promotion.
- **8–15 Physical Concurrency**: Non-blocking asynchronous thread pool driver, isolated git worktrees with jittered index.lock backoff, and 429 rate-limit self-healing.
- **Context Hygiene**: 53%+ input token reduction via native skill/MCP pruning, keeping subagents under 3,000 tokens per round.

---

## Architectural Visuals

![ZCode-ZLoop Live Dashboard](docs/assets/dashboard.en.png)

<p align="center"><sub>Unified overview of all active agents, execution runtimes, assigned models, and concurrent task workloads.</sub></p>

![ZCode-ZLoop Simplified Architecture](docs/assets/architecture-simplified.en.svg)

<p align="center"><sub>Simplified topology: root agent plans, cold supervisor orchestrates physical concurrency, mechanical tests eliminate poison, heterogeneous model audits, and humans retain release control. Open <a href="docs/architecture-interactive.html">docs/architecture-interactive.html</a> for interactive presentation.</sub></p>

![ZCode-ZLoop Full Control Loop Overview](docs/assets/architecture-overview.en.svg)

<p align="center"><sub>Full control loop: pre-planning counter-plan review, 8–15 async worktree concurrency, atomic staging rollback, isolated GLM-5.3 auditor gate, and immutable H0/H1 evidence.</sub></p>

---

## 1. Quick Start (5-Minute Deployment)

### Prerequisites
- Windows 10/11 x64
- Git 2.40+ (Git Bash recommended)
- Python 3.14+ (stdlib SQLite 3.50.4+ supported via `DELETE+EXTRA` crash-consistent gate)
- ZCode v3.10.2+

### 1. Install ZLoop

```bash
# Clone the repository
git clone https://github.com/LEO001020/zcode-loop-orchestra.git
cd zcode-loop-orchestra

# Set up virtual environment
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"

# Verify environment & hook status
.venv/Scripts/python -m zloop.cli doctor
```

### 2. Attach Workspace & Install ZCode Hooks

```bash
# Attach the current project to ZLoop data plane
.venv/Scripts/python -m zloop.cli project attach

# Install user-level ZCode hooks (SessionStart, UserPromptSubmit, PostToolUse, etc.)
.venv/Scripts/python -m zloop.cli install
```

### 3. Deploy Lean Subagent Profiles

Copy the hardened worker and auditor profiles to your user agent root:

```bash
mkdir -p "$USERPROFILE/.zcode/agents"
cp plugin/agents/zloop-worker.md "$USERPROFILE/.zcode/agents/"
cp plugin/agents/zloop-auditor.md "$USERPROFILE/.zcode/agents/"
```

---

## 2. Architecture & The Triple-Audit Protocol

```text
==================================================================================================
                                    Developer / Operator
                                             │
                                             ▼
                      [ Root Agent: Gemini 3.8 Flash / GLM-5.3 ]
                                             │
════════════════════════════════════ 1. Planning Layer ══════════════════════════════════════════
  [Root creates goal slice & constraints]
            │
            ▼
  [zloop c2c prepare --role plan] ─────────> Bounded, redacted packet (≤8000 chars, no secrets)
            │
            ▼
  [Audit Gate 1: C2C-P] ───────────────────> 【ChatGPT Web / Fresh Thread (D-11)】
  (High reasoning + web search,               │  - Evaluates hidden assumptions
   lowest token cost at initial phase)         ▼  - Returns counter-plan / blind spots
            │
            ▼
  [zloop c2c record --c2c <id>] ───────────> SHA256 verified, recorded in S-events
            │
            ▼
  [Wave Dispatch Gate] ────────────────────> HIGH/CRITICAL requires plan C2C record
                                             (Blocked at PLANNING stage if missing)
                                                │ (Passed)
════════════════════════════════════ 2. Execution Layer ═════════════════════════════════════════
                                                ▼
                                    [Wave Dispatch: 8–15 Workers]
                                (ThreadPoolExecutor + non-blocking poll())
                                 Worker 1      Worker 2  ...  Worker 15
                                (worktree_1)  (worktree_2)   (worktree_15)
                                    │             │              │
                                    └──────┬──────┴──────────────┘
                                           ▼
                                [Reconstructed Filesystem Deltas]
                                           │
════════════════════════════════════ 3. Materialization Layer ═══════════════════════════════════
                                           ▼
                              [Audit Gate 2: Host Mechanical Test]
                                 (pytest / cargo test / build)
                                           │
                        ┌──────────────────┴──────────────────┐
                     (Failure)                             (Success)
                        ▼                                     ▼
           [Atomic Staging Rollback]                 [Candidate Staged Commit]
             git reset --hard parent_sha             (Packet state: MATERIALIZED)
             git clean -fdx                                   │
         (P0-2: Zero cascading poisoning)                     │
                                                              │
════════════════════════════════════ 4. Promotion Layer ════════════════════════════════════════
                                                              ▼
                                             [zloop c2c prepare --role result]
                                              (Bounded diff review packet)
                                                              │
                                                              ▼
                                             [Audit Gate 3: C2C-A Result Audit]
                                              (GLM-5.3 auditor subagent / ChatGPT)
                                                              │
                                                              ▼
                                             [zloop c2c record --c2c <id>]
                                                              │
                                                              ▼
                                             [Stage Promote Gate]
                                              (Role-aware: requires role=result)
                                                              │
                                                     ┌────────┴────────┐
                                                 (Reject)           (Pass)
                                                     ▼                 ▼
                                              [Block Promotion]   [Fast-Forward Promote]
                                                                  (HEAD moves to staged SHA)
==================================================================================================
```

---

## 3. Daily Operational Command Flow

### Step 1: Create Run & Propose Wave

```bash
# 1. Start a new run
zloop run start "Implement payment module"

# 2. Begin a stage with HIGH risk floor
zloop stage begin "payment_core" --risk HIGH

# 3. Propose packets (disjoint write scopes)
zloop wave propose packets.json
```

### Step 2: C2C-P Planning Review

```bash
# Prepare redacted plan packet
zloop c2c prepare --role plan --file plan_summary.txt

# Paste packet into ChatGPT Web (fresh thread), copy response
zloop c2c record --c2c C2C001 --identity surface=chatgpt_web --file chatgpt_review.txt
```

### Step 3: Run Wave (8–15 Concurrency)

```bash
# Wave start passes the plan gate and dispatches workers in parallel
zloop wave start W1 --backend codex
```

### Step 4: Mechanical Acceptance & C2C-A Result Review

```bash
# Prepare result diff packet
zloop c2c prepare --role result --file diff.txt

# Record result verdict
zloop c2c record --c2c C2C002 --identity surface=chatgpt_web --file result_verdict.txt

# Promote stage to canonical branch
zloop stage promote S01
```

---

## 4. Repository Layout

```
zcode-loop-orchestra/
├── src/zloop/
│   ├── backend/          # CodexSdkBackend (async pool, poll, 429 backoff)
│   ├── metrics/          # Tokens, latency percentiles, concurrency, C2C stats
│   ├── research/         # Kimi research broker & web searcher lane
│   ├── c2c.py            # Cross-model audit packet serialization & redaction
│   ├── cli.py            # Unified CLI (run, stage, wave, c2c, doctor, install)
│   ├── db.py             # Control SQLite (S) with DELETE+EXTRA version gate
│   ├── evidence.py       # H0 immutable NDJSON journal & CAS blob store
│   ├── hook.py           # Process hooks with cwd project-scoping filter
│   ├── materialize.py    # Delta application, host acceptance, atomic rollback
│   ├── promote.py        # CAS fast-forward promotion & Git physical oracle
│   ├── supervisor.py     # Cold wave supervisor & stage revision fencing
│   └── workspace.py      # Git worktree isolation with index.lock backoff
├── spec/                 # Formal architecture specification (VOL-00 to VOL-22)
│   ├── DECISIONS.md      # Architecture decisions log (D-1 to D-25)
│   ├── PROGRESS.md       # Milestones & verification ledger
│   └── VOL-*.md          # Platform contracts, data model, security constitution
├── tests/                # 293 automated unit, integration, and chaos tests
├── tools/prompt-lab/     # SHA256-gated prompt experiment bench & sentinel probe
├── plugin/               # ZCode plugin package distribution files
└── pyproject.toml
```

---

## 5. Automated Verification & Invariants

ZLoop is validated against a strict test suite enforcing **Invariants I1–I44**:

```bash
# Run all 293 automated tests
.venv/Scripts/python -m pytest tests -v
```

Key invariants covered:
- **I1**: `disable(ZLoop) => native ZCode semantics restored` (zero leftover hooks/dependencies).
- **I3**: H0 logging failure is fail-soft (`history_degraded=true`, native cognition continues).
- **I4**: S transaction failure is fail-closed (stop lifecycle mutation).
- **I6**: Packet acceptance gated by `stage_revision ∧ packet_revision ∧ active_launch_id`.
- **I13**: Secrets never persist unredacted in journals or blobs.
- **I30/I39**: Checked-out promotion requires clean HEAD and matching dirty digest (ff-only).
- **I34**: Independent mutable workspace (worktree) per launch.
- **P0-1**: Non-blocking `poll()` and ThreadPoolExecutor prevents single-thread driver hang.
- **P0-2**: Atomic `git reset --hard` on materialization failure completely eliminates cascading poisoning.

---

## 6. License

This project is licensed under the [MIT License](LICENSE).
