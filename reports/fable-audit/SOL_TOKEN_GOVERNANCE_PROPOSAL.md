# Proposal · Reduce all-in Sol token share without reducing LOOP capability

Status: design for Fable audit; not yet an authorization to deploy every item.

## 1. Problem statement

The corrected rolling-five-hour baseline is:

| Metric | Sol | V4 | K3 |
|---|---:|---:|---:|
| Raw total-token share | 71.10% | 28.80% | 0.11% |
| Effective-token share (`total - cached input`) | 45.93% | 53.61% | 0.46% |
| Output-token share | 21.15% | 78.62% | 0.23% |

The current install/audit root is 91.57% Sol-effective and contributes about
67% of all Sol-effective tokens in the window. Removing only that exceptional
root leaves 22.82%, which demonstrates that the V4 execution plane is working,
but it does not satisfy the all-in KPI. The final controller must include root
usage rather than hiding it as maintenance.

## 2. Design goals

1. All-in rolling-five-hour Sol effective share: 20–25% after a statistically
   meaningful denominator.
2. Preserve Sol authority over requirements, planning, ambiguity, integration,
   conflict resolution, adjudication, and release.
3. Preserve a 48-worker execution plane and one-second headless birth interval.
4. Keep ipybox available to every worker and retain user-visible concurrency.
5. Avoid global tool bans, mandatory review of every packet, and fixed pauses on
   healthy execution.
6. Prevent Desktop UI/app-server load from being proportional to wide worker
   count or child context size.

## 3. P0 · Correct the meter before controlling anything

### 3.1 Turn-scoped maintenance

Current defect: `model_token_share.py` searches the entire rollout text. A
productive long-lived root becomes maintenance forever if it quotes one smoke
marker. Replace rollout-wide classification with turn-scoped classification:

```text
event_msg.user_message starts a turn
  -> classify that prompt as production or maintenance
token_count events before the next user_message
  -> inherit that turn classification
```

Inherited parent context in a child rollout must not classify the child's own
turn. Add golden tests where a production root quotes `K3_OK` or
`IPYBOX_LAZY_OK` in an assistant/report message and remains production.

### 3.2 Add explicit windows

Emit at least:

- rolling 1h: early signal only;
- rolling 5h: primary controller;
- rolling 24h and 7d: trend/reporting;
- per root task and per project.

Expose raw total, cached input, effective total, output, and reasoning. Never
present `share_total` as a cost-equivalent KPI without the cached-input caveat.

### 3.3 Minimum denominator and hysteresis

Do not react to a small window. Proposed controller conditions:

```text
minimum production effective tokens: 2,000,000
enter high-Sol mode: 5h share > 25% for 2 consecutive samples
leave high-Sol mode: 5h share < 22% for 2 consecutive samples
critical mode: 5h share > 35% and 1h share > 35%
```

Sampling is event-driven after terminal worker/root turns, not model polling.

## 4. P1 · Compile root context into a ControlPacket

Full transcript inheritance is expensive and destabilizes Desktop; zero context
causes child drift. Compile a bounded, versioned packet containing:

- objective and definition of done;
- root decisions and non-negotiable constraints;
- evidence paths plus hashes;
- known facts versus unresolved hypotheses;
- task-specific scope and allowed side effects;
- output schema and token/line budget;
- ControlPacket ID and revision.

Target size: normally 2–8k tokens; hard evidence stays on disk. The compiler may
summarize only root decisions, never silently weaken a user constraint.

The compiler itself must be zero-model on the normal path. A naive Sol process
that rereads the transcript and rewrites every child prompt merely moves token
cost from child inheritance to root summarization. Use this pipeline instead:

```text
user/root decision
  -> append a small typed decision delta to decision-ledger.ndjson
  -> deterministic selector chooses deltas by scope/tag
  -> deterministic renderer emits one shared ControlPacket + task overlay
  -> N children receive packet path/hash and only their small overlay
```

Sol should add or approve only the decision delta, normally tens to hundreds of
tokens. A V4 context-steward may propose a delta from a long transcript, but Sol
reviews only the proposed diff. The shared packet is not copied N times into
root output. Per-wave root control overhead target: <=2,000 new tokens; ordinary
revision target: <=500 new tokens.

Current root pressure confirms the need: its latest turn carried 267,751 input
tokens in a 353,400 effective window (about 75.8% full), although 265,344 were
cached. The same root has accumulated roughly 96.18M raw tokens through repeated
long-context turns. Cache reduces cost, not context-object pressure or the need
to keep root reasoning concise.

Dynamic control:

- urgent change: lifecycle cancel + relaunch with revision N+1;
- non-urgent correction after a turn: `codex exec resume <thread_id>` with a
  bounded revision prompt;
- child clarification: return a structured `NEEDS_DECISION` result; root
  answers through a new revision/resume, not a polling loop.

## 5. P1 · Route wide work to headless execution

The 23:13 failure shows that one-second staggering does not make sixteen
Desktop-native 950k agent objects safe. The app-server restarted while Electron,
OpenCodex, and WSL remained alive.

Execution policy:

```text
Desktop-native agents: small interactive set only
headless WSL workers: wide implementation, audit, research, tests
monitor: merge both planes and show semantic task names
```

This is not a reduction of total concurrency. It limits only UI-hydrated child
objects; the execution target remains 48. A practical starting policy is to
keep no more than four native children initializing/running for one root and
route additional bounded work to headless supervisors. Fable should validate
whether the native threshold should be 2, 4, or based on measured renderer/
app-server memory rather than a fixed number.

## 6. P1 · Root work routing without a global Sol tool gate

Do not add another synchronous PreToolUse gate. Instead, classify work packets
at planning/dispatch boundaries:

| Work | Default owner |
|---|---|
| Requirements, ambiguity, architecture decision | Sol root |
| Independent source inspection/evidence collection | V4 headless |
| Isolated implementation in a worktree | V4 headless |
| Mechanical tests/builds | zero-model supervisor/tooling |
| Cross-source verification and candidate ranking | K3 |
| Release falsification | one K3 reviewer per wave |
| Integration/conflict/final judgment | Sol root |

When five-hour Sol share is above 25%, any decomposable implementation or
read-heavy audit identified by the root is automatically converted into a
headless packet. Critical-path integration remains local; the controller does
not block it.

## 7. P1 · Child output contract

Large child results must not return to root chat. Required artifact layout:

```text
reports/<packet-id>/report.md        complete evidence
reports/<packet-id>/events.jsonl     model/tool events
reports/<packet-id>/stderr.log       bounded diagnostics
child final response                 <= 8 findings or <= 500 tokens
```

Root receives only:

- status enum;
- one-line conclusion;
- artifact paths;
- blocking finding IDs;
- `NEEDS_DECISION` payload if applicable.

The lifecycle supervisor mechanically rejects a successful exit with a missing
report. Add schema validation for the short result so a child cannot paste a
200k-token report into the return channel.

## 8. P1 · Add K3 plan expansion between Sol and V4

K3's five-hour effective share is 0.46%. This is a topology defect, not merely
low demand: K3 currently appears mostly as a late verifier/reviewer, so most
planning expansion remains in Sol and execution goes directly to V4.

Adopt this nontrivial-work path:

```text
Sol decision skeleton
  -> K3 plan_expander
       -> bounded packet DAG + acceptance + dependencies + risk tags
       -> task-specific ControlPacket overlays
  -> V4 workers
  -> conditional K3 verification/release falsification
  -> Sol only for ambiguity, conflict, adjudication, final release
```

Sol's skeleton contains only objective, hard decisions, boundaries, risk class,
and unresolved choices. K3 may expand but may not change them. Every expanded
field cites a decision ID or evidence path; unsupported assumptions are emitted
as `NEEDS_DECISION`, not silently invented.

The K3 expander emits machine-validated JSON:

- packet IDs and goals;
- authorized paths/side effects;
- dependency DAG and concurrency groups;
- acceptance commands and expected artifacts;
- risk/verification triggers;
- ControlPacket decision/evidence references;
- unresolved questions requiring Sol.

Trigger K3 expansion when any condition is true:

- the task decomposes into three or more packets;
- work spans multiple components or Windows/WSL boundaries;
- acceptance criteria need synthesis;
- the root five-hour Sol share is above 25%;
- the user asks for a thorough audit, environment package, migration, or release;
- a retry needs a revised DAG rather than a local fix hint.

One-packet trivial work can still go directly from Sol to V4. K3 is not a
mandatory reviewer for every ordinary packet.

Additional K3 uses remain:

- cross-family verification after a high-risk signal;
- ranking 2–3 competing V4 candidates;
- falsification-style release review once per wave;
- meter/controller changes and crash attribution.

Initial observed design band, not a hard quota:

```text
Sol: 20–25% of all production effective tokens
K3:  10–25%
V4:  50–65%
```

The existing 36/12 V4/K3 concurrency preference already gives K3 up to 25% of
preferred slots; reservations remain borrowable, so empty K3 demand cannot idle
the execution plane. The controller reports a design warning when a meaningful
five-hour window has K3 below 5% while Sol exceeds 25%, because that combination
strongly suggests plan expansion remained in Sol.

## 9. P2 · Monitor and feedback semantics

The UI should show separately:

- global observed concurrency;
- authoritative running versus rollout estimate;
- LOOP single-pool target 48;
- Desktop-native count and headless count;
- rolling 1h/5h Sol effective share;
- per-project share and top root contributor;
- ControlPacket revision for headless tasks.

Deduplicate stale Desktop rollout fallbacks after app-server restart. A task
that is present in authoritative `exec_roster` and has the same semantic audit
identity must not be double-counted with a crash-era Desktop rollout.

## 10. Acceptance gates

Fable should reject the proposal unless the implementation demonstrates:

1. Meter golden tests for replay dedup and turn-scoped maintenance.
2. Five-hour report reproducible from a frozen fixture.
3. All-in Sol effective share <=25% for three consecutive meaningful samples,
   each with >=2M production effective tokens.
4. A 48/48 headless wave: zero failed, OpenCodex PID unchanged, orphan gateway
   zero, no fixed healthy pause.
5. Sixteen 950k headless auditors do not restart Desktop app-server and remain
   visible through `exec_roster`.
6. Native-wave comparison at controlled counts to locate the safe UI boundary.
7. Child large-report fixture does not enter the root return channel.
8. ControlPacket cancellation/revision and resume flows are auditable.

## 11. Explicit non-goals

- No rewriting historical token usage.
- No global ban on root tools or ipybox.
- No reduction of LOOP execution target below 48 as the primary fix.
- No mandatory K3 review for every worker packet.
- No full root transcript replication into each child.
- No claim that ipybox was the unique initiator of every Desktop crash.
