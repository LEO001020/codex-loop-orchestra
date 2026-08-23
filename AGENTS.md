<!-- AGENTS.md — Codex LOOP Orchestra discipline section (~100 lines).
     This file is a BYTE-STABLE PREFIX: it is loaded at the start of every
     session and must not change between runs of the same package version,
     so prompt caching stays effective. Do not append run-specific content,
     timestamps, or state here — state lives on disk under data/. -->

# LOOP Discipline

## 1. Decomposition discipline (single-pass Plan-and-Solve)

- Planning is a SINGLE PASS: read the task, then emit the complete decomposition
  in one planning event — `packets/*.json` (exactly 4 fields: `goal`,
  `authorized_paths`, `acceptance`, `constraints`, plus `packet_id`) and `dag.json`.
- No incremental re-planning mid-wave. Plans change only at an adjudication
  event (dead-letter, merge conflict, L3 escalation, wave finale).
- Each packet must be self-contained: an Executor with only the 4 fields and its
  worktree can finish it. If a packet needs "context from the main thread", the
  decomposition is wrong — split or rewrite it.
- Intra-wave packets must have non-intersecting `authorized_paths`; the DAG
  assertion enforces this before any dispatch.
- No 5th required packet field. No idempotency_key, no embedded runtime_resources.

## 2. Anti-polling negative discipline (what Sol rounds are NOT for)

Sol is invoked on exactly two event types: **planning** and **adjudication**.
Sol rounds are NEVER used for:

- **Waiting** — native wait-all blocks without producing rounds.
- **Polling** — status lives in `data/events.ndjson`; scripts consume it.
- **Tallying** — report counting is the missing-item check script's job.
- **Retry decisions** — `config/retry_classes.yaml` + retry script decide.
- **State recap** — the state machine holds state; never ask Sol "where were we".

Any transition decidable by if/else, exit code, or a state machine does not go
through an LLM. Sol's ideal round count per task approaches **2 + anomaly count**
(one planning round, one finale round, plus one per genuine anomaly). If a run
uses more, find which of the five forbidden uses leaked back in.

## 3. Return convention (all subagents, mandatory)

- **Success:** exactly 1 line of conclusion + the artifact path(s).
- **Failure:** 1-line conclusion + the last 50 lines of the failing log + the
  report file path.
- Full logs, test output, and exploration notes go to `reports/<packet_id>/`
  on disk — never into the reply body. Self-reported PASS carries zero weight;
  mechanical acceptance replays the commands independently.

## 4. Recoverable compression directive

- Files are the single source of truth; bytes do not reside in Sol context.
- When compressing or compacting: **delete content, keep paths.** A path plus a
  ≤500-token structured summary is always recoverable; inlined content is not.
- Every compaction summary MUST carry: (a) the report index (every report path
  produced so far) and (b) a read/unread checklist so nothing is silently
  dropped. A compaction that loses a path is a defect.
- To re-read detail, use path handles: `head`/`sed` to locate the exact lines,
  never re-ingest whole files.

## 5. Kernel trigger rules (ipybox, when enabled)

Route work to the ipybox persistent kernel when either holds:

- A step will produce **>5,000 tokens of output** — digest it in-process and
  return a variable-name handle plus truncated stdout (≤50 lines printed).
- State must **survive across calls** (dataframes, parsed indexes, counters) —
  kernel state is compression-immune and lives off-heap from the context.

Otherwise stay with plain tools. In this deployed LOOP environment every root and
headless worker can see ipybox, but its Jupyter gateway and IPython kernel start
lazily on the first kernel tool call. Workers that never use it pay no Jupyter
process cost. Kernel discipline: print ≤50 lines, return handles.

## 6. Spawn scale heuristics

- **Homogeneous batch** (same instruction over many rows: per-file review,
  migration checklist, N similar packets): use `spawn_agents_on_csv` with an
  `output_schema` and a stable `id_column`; each worker calls
  `report_agent_job_result` exactly once. After the tool writes
  `output_csv_path`, execute the generated call pack's
  `required_postprocess.argv` and then `required_postprocess.then_argv`.
  The first command copies worktree reports back to LOOP root and emits
  generation-aware terminal events; the second advances the state machine.
  A nonzero postprocess result stops the wave and is never optional.
- **Unique task** (one-off packet, distinct instructions): single `spawn_agent`
  with the packet's 4 fields as the prompt.
- Desktop host slots remain capped by
  `agents.max_concurrent_threads_per_session` (50) per root session, while
  LOOP's normal **cross-plane combined effective target is 80**. A parent
  dialogue sustains 20 agents. The preferred mixed load is execution=60 and
  review=20; these are borrowable reservations, not hard partitions. If one
  pool has no ready work, the other may use all 80 slots.
- Wide execution normally uses WSL/headless `codex exec` workers so Desktop is
  a light control and observation plane. Existing Desktop/CSV entry points stay
  available; routing to headless is a low-friction default, not a global ban.
- Ad-hoc headless waves use `harness/headless_wave.py` so each birth is owned by
  `lifecycle_supervisor.py`, appears in `exec_roster.json`, and is visible on
  8765. A raw unregistered `codex exec` process does not satisfy refill debt.
  Only the matching generation stably observed as `running` is effective;
  `starting`, spawn intent, deny messages, stale/lost rows, and Popen success do
  not reduce the 20/80 deficit.
- A one-shot ad-hoc manifest is observable but is not a sustained-concurrency
  backlog. When a parent dialogue must remain near 20, compile 21-80 real,
  bounded read-only packets into `codex-loop-parent-refill/v1` and admit them
  once through `harness/parent_manifest_importer.py` with `target_active=20`.
  The importer wakes the existing refill consumer; do not launch the same
  packets separately through a second wave.
- Never synthesize filler packets to satisfy a number. If the admitted parent
  backlog is exhausted, 8765 must show `parent_backlog_empty` and zero
  spawnable work rather than pretending spare capacity is refill debt.
- Births are submitted at least 1 second apart. No fixed group pause is added
  while healthy; at most eight agents may still be in the initializing handshake
  at once, and every eighth birth performs one lightweight OpenCodex health
  check. A failed check pauses new births for 30 seconds but does not cancel
  already-running work or clear refill demand.
- Sustain the combined target across the task while independent work remains;
  refill completed/failed slots without requiring another user command.
- Depth stays at 1: children never spawn grandchildren.
- After a batch, failed rows are detected from the output CSV `status` /
  `last_error` columns by script — only failed rows are re-dispatched.

## 7. Power and safety reminders (restated, enforced elsewhere)

- L1/L2 can only block or escalate; they can never release to publication.
- High-risk classes (path boundary, test-count decrease, credential/CI/
  migration paths) route deterministically to L3+L4; no verdict overrides them.
- Off-table events go to DEAD_LETTER and wake Sol — fail-visible, never silent.
- Release merge is human-triggered. Always.

## 8. Sol mechanical discipline: no L0 data processing (enforced, not advisory)

- Sol NEVER performs L0 data processing itself: searching files or the web,
  computing statistics, aggregating logs, bulk-reading files, or running
  tests/counts. Every such step is either zero-token script work (harness/,
  metering/) or a dispatched worker packet — even when doing it inline would
  feel faster. "The main thread just did it quickly" is exactly how Sol share
  breaks its budget.
- **PreToolUse gate (mechanical enforcement):** `hooks/sol_tool_gate.py` is
  registered as a PreToolUse hook. When the LOOP state is not `planning`,
  `adjudication`, or `release_finalize`, root-session shell / search /
  bulk-read / test / statistics tool calls are DENIED with the instruction to
  dispatch an L0/L1 packet instead. Subagents are never gated. The gate fails
  open on an unreadable ledger (with a stderr note) — discipline enforcement
  must not paralyze the session.
- **Token share budget (measured, not aspirational):**
  `metering/model_token_share.py` aggregates Codex's own rollout JSONL into
  Sol/worker/verifier/reviewer/maintenance buckets and computes
  `share_effective = (sol − sol cached input) / (total − cached input)` as the
  primary control metric, per task / per wave / rolling 24h / rolling 7d /
  cumulative. Over 20% → WARNING; over 25% → BLOCK: no new
  non-planning/adjudication Sol work until the share is back under budget.
  Reviewer tokens sit in their own bucket. Historical Sol-model reviewer
  tokens remain part of the Sol KPI; current review-family accounting follows
  the actual observed model selected by the active model profile, with no
  hardcoded family exemption.
  Install/acceptance runs are maintenance and never enter production KPIs.
