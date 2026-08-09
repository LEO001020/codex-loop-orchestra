<!-- AGENTS.md — Codex-LOOP-Build-F2 discipline section (~100 lines).
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

Otherwise stay with plain tools. In the F2 cold start form the kernel is
disabled; the rules above only apply after the `[mcp_servers.ipybox]` block in
config.toml is uncommented. Kernel discipline: print ≤50 lines, return handles.

## 6. Spawn scale heuristics

- **Homogeneous batch** (same instruction over many rows: per-file review,
  migration checklist, N similar packets): use `spawn_agents_on_csv` with an
  `output_schema` and a stable `id_column`; each worker calls
  `report_agent_job_result` exactly once.
- **Unique task** (one-off packet, distinct instructions): single `spawn_agent`
  with the packet's 4 fields as the prompt.
- Concurrency is capped by `agents.max_concurrent_threads_per_session` (4);
  excess rows queue — do not tune per-call concurrency above the session cap.
- Depth stays at 1: children never spawn grandchildren.
- After a batch, failed rows are detected from the output CSV `status` /
  `last_error` columns by script — only failed rows are re-dispatched.

## 7. Power and safety reminders (restated, enforced elsewhere)

- L1/L2 can only block or escalate; they can never release to publication.
- High-risk classes (path boundary, test-count decrease, credential/CI/
  migration paths) route deterministically to L3+L4; no verdict overrides them.
- Off-table events go to DEAD_LETTER and wake Sol — fail-visible, never silent.
- Release merge is human-triggered. Always.
