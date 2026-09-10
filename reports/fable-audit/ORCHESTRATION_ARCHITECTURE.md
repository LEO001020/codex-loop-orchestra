# F2 orchestration architecture for Sol intelligence with lower Sol token share

Status: proposed P0/P1 design for Fable audit. The proposed files in this
directory are not production-enabled merely by being packaged.

## 1. Ruling

K3's corrected five-hour effective-token share of 0.46% is a topology defect.
The current graph gives K3 mostly late, conditional verifier/reviewer work,
while planning expansion and cold-start escalation return to Sol. Increasing
the K3 slot target alone cannot fix this because an empty K3-ready queue simply
leaves those slots borrowable.

The desired property is not fewer Sol decisions. It is greater Sol leverage per
new Sol token:

```text
Sol: decide the small set of facts that require the strongest intelligence
K3:  expand those decisions into a packet graph and cross-check risky results
V4:  perform the high-volume source, implementation, test, and evidence work
tools: compile packets, schedule, meter, validate schemas, and move artifacts
```

The main production path becomes:

```text
user request
  -> Sol DecisionSkeleton
  -> deterministic ControlPacket compiler
  -> K3 plan_expander
  -> deterministic DAG/schema/risk validation
  -> V4 execution packets (36 preferred slots)
  -> mechanical acceptance
  -> conditional K3 verification + one K3 release falsifier
  -> bounded AdjudicationPacket
  -> Sol conflict/ambiguity/final decision
```

This preserves Sol as the authority at both ends of the graph without making
Sol the transport, exploration, or report-summarization layer in the middle.

## 2. Verified defect in the current graph

The existing trigger table already emits `send_l2`, but the current cold-start
configuration has `passthrough_enabled=false`. `harness/trigger_eval.py`
upgrades every action except `direct_l3` to `direct_l3` in that mode. The
repository also lacks a complete automatic consumer that turns every emitted
`send_l2` action into a headless K3 verifier dispatch.

Consequences:

1. Increasing `k3_target=12` creates capacity but not K3-ready work.
2. Healthy or ordinary packets can still flow back to Sol during cold start.
3. Sol performs work that should have been K3 plan expansion or verification.
4. K3 remains near zero even when all model pins and context windows are valid.

Fable must reject a solution that only changes slot ratios or token quotas.
The routing graph and the `send_l2` consumer must be implemented and tested.

## 3. Sol's exact role

Sol is an intelligence authority, not a standing worker pool.

### 3.1 Sol intake output: `DecisionSkeleton`

For a non-trivial root, Sol emits one bounded, schema-valid object containing:

- objective and definition of done;
- immutable user constraints;
- architecture and policy decisions already made;
- allowed side effects and release authority;
- risk class;
- unresolved choices that genuinely require the user or Sol;
- evidence roots, not copied evidence bodies.

Target: at most 1,200 new Sol tokens for an ordinary skeleton. The skeleton is
a decision artifact, not a prose plan and not a source audit. Sol may inspect a
small critical-path slice when needed to make a decision, but it does not read
all files or all child logs by default.

### 3.2 Sol adjudication input: `AdjudicationPacket`

Sol never receives all child reports. A deterministic reducer constructs a
bounded packet containing only:

- decisions requested by children;
- conflicting verified claims;
- failed acceptance IDs;
- high-risk K3 findings;
- candidate choices with evidence paths and hashes;
- the exact release decision requested.

Target: at most 2,000 new input tokens for ordinary adjudication. Full reports
remain on disk and are opened by Sol only when the reducer identifies a
specific conflict that cannot be resolved from the bounded packet.

### 3.3 Sol remains responsible for

- interpreting the user's intent;
- decisions that change requirements or architecture;
- cross-packet conflicts and unsafe scope expansion;
- answering `NEEDS_DECISION`;
- final integration and release judgment.

It does not automatically re-review every passing worker packet.

## 4. K3's expanded role

### 4.1 Planning expansion

`plan_expander` receives a ControlPacket path/hash plus the DecisionSkeleton.
It produces a machine-validated packet DAG with:

- independent packet goals and write ownership;
- dependencies and concurrency groups;
- acceptance commands and expected artifacts;
- risk tags and K3 verification triggers;
- task-specific ControlPacket overlays;
- explicit `NEEDS_DECISION` items for unsupported assumptions.

K3 may expand a Sol decision but may not change one. Any packet field that
affects authority must cite a decision ID.

Plan expansion is required when any of these is true:

- three or more useful packets exist;
- multiple components or Windows/WSL boundaries are involved;
- acceptance criteria or write isolation require synthesis;
- the work is an audit, migration, environment package, or release;
- the rolling five-hour Sol share is above 25%;
- a failed wave needs a revised DAG rather than a local retry.

One clearly bounded packet can go directly from Sol to V4.

### 4.2 Verification and release falsification

The existing K3 verifier/reviewer roles remain, but `send_l2` must have a real
consumer. K3 verification is triggered by risk and evidence, not by a mandatory
review of every packet:

- trigger-table `send_l2` output;
- conflicting V4 findings;
- ranking two or three candidates;
- large or security-sensitive diffs;
- meter/controller, sandbox, hook, crash-attribution, or release changes;
- a bounded sample of otherwise healthy waves to detect blind spots.

One K3 falsification-style release review remains the final wave-level backstop.
K3 cannot release; its verdict feeds Sol/human release authority.

### 4.3 K3 token bands

Use two bands, because verification alone and total K3 work have different
healthy shapes:

- K3 verifier/reviewer sub-band: initially 2–8% of production effective tokens;
- all K3 including plan expansion: initially 10–25%;
- alert when a meaningful five-hour window has K3 below 5% while Sol exceeds
  25%; this combination indicates routing bypass, not merely low demand.

These are design diagnostics, not hard quotas. K3 should never burn tokens to
fill a ratio, and its 12 preferred slots remain borrowable by V4 when no K3-ready
work exists.

## 5. V4's role and the 48-way plane

V4 remains the high-throughput execution family:

- source exploration and evidence collection;
- isolated implementation;
- mechanical test/build invocation through supervisors;
- report generation and bounded retry work.

The shared target remains 48 with preferred 36 V4 / 12 K3 slots, one-second
birth spacing, and slot borrowing in both directions. The orchestration design
does not reduce total concurrency and does not disable ipybox. Wide workers run
headless under the lifecycle supervisor; Desktop remains the control and
observation plane.

## 6. Control context without root-context replication

The root appends small typed deltas to `decision-ledger.ndjson`. A zero-model
selector and renderer compile:

```text
shared ControlPacket = relevant immutable decisions + constraints + evidence map
task overlay         = packet goal + paths + side effects + acceptance + output schema
```

Children receive packet paths and hashes plus their overlay. They do not inherit
the full root transcript. Evidence bodies stay on disk.

Dynamic control is revision-based:

- urgent decision change: cancel affected descendants and relaunch under
  ControlPacket revision N+1;
- non-urgent correction after a turn: `codex exec resume <thread_id>` with a
  bounded revision prompt;
- child uncertainty: emit structured `NEEDS_DECISION`, then wait; no model
  polling loop;
- stale revision: supervisor rejects the result instead of merging it.

Normal root control overhead target is at most 2,000 new tokens per wave and at
most 500 new tokens for an ordinary revision.

## 7. Routing state machine

```text
INTAKE_SOL
  -> COMPILE_CONTROL
  -> EXPAND_K3 | DIRECT_V4
  -> VALIDATE_DAG
  -> EXECUTE_V4
  -> MECHANICAL_ACCEPT
  -> VERIFY_K3 (conditional or sampled)
  -> RELEASE_REVIEW_K3 (once per wave)
  -> REDUCE_RESULTS
  -> ADJUDICATE_SOL | DONE_PENDING_HUMAN_RELEASE
```

Only these conditions return to Sol before the end:

- `NEEDS_DECISION` changes scope, requirements, authority, or architecture;
- K3/V4 evidence conflicts after one bounded cross-check;
- a high-risk trigger is explicitly `direct_l3`;
- retry limits are exhausted;
- release adjudication is requested.

Ordinary progress, successful acceptance, report formatting, and refill do not
consume Sol turns.

## 8. Low-friction feedback controller

The controller observes all production effective tokens, including root usage.
It does not rewrite history or classify the whole root as maintenance.

Primary window and hysteresis:

```text
minimum denominator: 2,000,000 production effective tokens
enter high-Sol mode: 5h Sol >25% for two consecutive terminal-event samples
leave high-Sol mode: 5h Sol <22% for two consecutive samples
critical: 5h Sol >35% and 1h Sol >35%
```

High-Sol mode changes defaults rather than banning capabilities:

- all decomposable read/implementation work routes headless;
- non-trivial roots require K3 plan expansion;
- child full reports are disk-only;
- Sol receives only DecisionSkeleton and AdjudicationPacket surfaces;
- critical-path Sol work remains allowed and auditable.

No synchronous model call is added to every tool use. Sampling occurs after
terminal turns or wave transitions.

## 9. Implementation order

P0:

1. Fix turn-scoped maintenance and emit 1h/5h/24h/7d windows.
2. Add a deterministic `send_l2` consumer that dispatches the pinned K3
   verifier through the existing headless lifecycle path.
3. Add `DecisionSkeleton`, plan-expander, packet-DAG, and short-result schema
   validation.
4. Add a layered routing mode in which ordinary pass stays behind mechanical
   acceptance, `send_l2` reaches K3, and only explicit high-risk actions reach
   Sol. Do not merely flip `passthrough_enabled=true` without the consumer.

P1:

5. Add zero-model decision-ledger selection and ControlPacket rendering.
6. Add deterministic result reduction into AdjudicationPacket.
7. Add plan-expander dispatch/refill accounting and K3 demand metrics.
8. Add monitor fields for root phase, ControlPacket revision, K3-ready queue,
   and separate verifier versus plan-expander token share.

P2:

9. Tune sampling and bands from observed waves; retain borrowable capacity.
10. Measure the Desktop-native safe boundary separately from headless capacity.

## 10. Acceptance and rejection gates

Fable should accept only if all of the following are demonstrated:

1. A non-trivial fixture follows Sol → K3 plan expansion → V4 → conditional
   K3 → Sol, with schema-valid artifacts at every edge.
2. Every `send_l2` is consumed exactly once by a pinned K3 verifier; no
   `send_l2` silently becomes pass or falls back to Sol.
3. Explicit high-risk `direct_l3` behavior and human release requirements are
   unchanged.
4. The root receives no child report body larger than the short-result limit.
5. A 48-packet wave preserves 48 total, 36/12 preferred borrowable slots,
   one-second spawning, ipybox availability, zero orphan gateways, and stable
   OpenCodex/Desktop app-server processes.
6. Across three meaningful five-hour samples, all-in Sol effective share is
   at most 25%, K3 is materially above the 0.46% baseline, and V4 remains the
   largest execution family.
7. The same workload repeated from frozen inputs produces the same DAG and
   ControlPacket hashes except for explicitly volatile metadata.

Reject if the implementation obtains a lower Sol ratio by hiding root tokens as
maintenance, disabling ipybox, reducing total concurrency, hard-idling V4 to
fill K3 quota, or imposing a Sol/K3 model call before every ordinary tool use.

## 11. Official product boundary

Official OpenAI documentation describes subagents as a way to move noisy
exploration, tests, and logs off the main thread and return summaries instead of
raw intermediate output. It also notes that every subagent performs its own
model/tool work and therefore increases token use. LOOP's ControlPacket and
disk-report design follows that boundary, while the K3/V4 routing, token bands,
headless lifecycle, and 48-way pool are local F2 design choices and must be
validated from this package rather than treated as OpenAI product guarantees.

Official reference:
https://learn.chatgpt.com/docs/agent-configuration/subagents
