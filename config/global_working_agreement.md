# Global Codex working agreement

## Mandatory LOOP model routing

The Sol root must never spawn a roleless/default/inherited-model child.  A
spawn without an explicit role or model inherits Sol in the current Desktop
runtime and defeats LOOP's purpose even if it is later displayed as an
"execution" agent.

- Execution, implementation, repository exploration, evidence collection,
  and ordinary read-only audit packets must explicitly use `agent_type=worker`
  or the execution model and effort named by the active profile in
  `<LOOP_INSTALL_DIR>\config\model_profiles.toml`.
- Cross-source verification, ranking, release review, and adversarial audit
  packets must explicitly use `verifier`/`reviewer`; their actual model and
  effort must equal `review_model` / `review_reasoning` from the active profile
  in `config/model_profiles.toml`. Never hardcode a retired review family.
- Sol children are permitted only for an explicitly justified L3 adjudication
  packet; they must never be used to fill a normal concurrency wave.
- Every child birth uses `fork_context=false`; inherited root context is not a
  substitute for a self-contained packet.
- If the available spawn interface cannot guarantee the explicit role/model,
  use WSL/headless `codex exec`; do not fall back to a default Desktop child.
- Before refilling a wave, verify the actual child `turn_context.model`.  Any
  inherited Sol child is a routing violation: stop refilling, report it, and
  correct the route before new work is launched.
- Monitoring must classify actual models independently. A role label, family
  nickname, or requested override is not a substitute for the observed
  `turn_context.model` and effort.

## Throughput-first parallel delegation

For every non-trivial request, parallel delegation is the default and the burden of justification is on running fewer agents, not on running more. The primary agent must proactively identify independent, bounded work without waiting for the user to request delegation, name subtasks, or specify an agent count. Persistent user priority is minimum wall-clock latency.

Desktop transport rule: preserve the 20-agent wave target and the 80-agent
LOOP target.  Prefer keeping only the first 8 children Desktop-native and
placing the rest of a wide wave on the existing headless `codex exec` plane,
especially while an in-app browser tab is open.  Eight is a transport
preference, never an effective-concurrency cap.  A native birth may be blocked
only after the same task has been durably submitted headless and stably
observed as `running`.  A denied or merely intended birth never clears refill debt;
if atomic handoff is unavailable, do not hard-deny solely because 8 native
children are active.  The 8765 observer must aggregate both planes and keep
showing any unfilled deficit against 20/80.
For lifecycle-visible headless waves, use
`<LOOP_INSTALL_DIR>\harness\headless_wave.py` with a bounded task
manifest.  Do not use an unregistered raw `codex exec` process as a claimed
refill: only a matching exec-roster generation stably observed as `running`
counts as effective concurrency.

Launch the first parallel wave before beginning lengthy local exploration whenever useful work packets can be inferred safely. For both execution work and review work, target 20 concurrent subagents and normally launch 16–20 in the first wave for that model family. Reach 20 whenever capacity and side-effect safety allow. Review work is not intrinsically serial: when verification, ranking, research, audit, or release-review work decomposes into independent packets, use the same on-demand 20-agent wave policy as execution. This is a sustained-concurrency requirement, not only a first-wave target: throughout the task, while meaningful independent bounded work remains, keep total active supporting agents near 20 and immediately refill completed, failed, or closed slots with follow-up investigation, verification, retries, or fresh packets. Do not let the wave decay merely because the user did not repeat the concurrency instruction. Never exceed 50 active spawned subagents in one Desktop root session or any lower runtime limit; the primary agent is excluded from that count. LOOP's cross-dialogue/headless aggregate target is 80. Reserve capacity above a normal 20-agent wave for cross-family auditors, reviewers, retries, replacement agents, and unusually wide independent waves.

If the obvious decomposition yields fewer than 16 packets, increase useful coverage with distinct read-only slices such as separate repository areas, alternate hypotheses, independent evidence checks, regression dimensions, security review, Windows/WSL review, attribution review, and adversarial verification. Redundant agents are encouraged when each has a concrete independent lens. Do not suppress delegation merely to save subagent tokens, API calls, or model cost.

Keep available slots busy while meaningful bounded work remains. As agents finish, immediately refill the wave with follow-up investigation, verification, or retries instead of waiting for the entire batch. A first wave below 10 agents is appropriate only when the task is genuinely trivial, tightly sequential, unsafe to parallelize because of shared side effects, or constrained by runtime capacity. For a non-trivial read-heavy task, lack of an explicit user command is never a reason to avoid delegation.

Prefer delegation especially for repository exploration, evidence gathering, code-review dimensions, log analysis, test-plan analysis, comparisons, documentation checks, and other bounded supporting work. Keep concurrent writes and side-effecting tests subject to the shared-workspace safety rules below.

## Primary-agent responsibility

The primary agent remains the owner of the whole task. It must retain responsibility for requirements, planning, shared decisions, critical-path work, integration, conflict resolution, verification, and the final answer. Subagent output is supporting evidence or a bounded contribution, not an automatically accepted conclusion.

While subagents run, the primary agent should continue any useful independent work that remains. It should wait only when a required dependency cannot be advanced locally. If a delegated task becomes obsolete or misdirected, steer, interrupt, or stop it instead of waiting unnecessarily.

The primary agent must reconcile disagreements itself by inspecting the underlying evidence. Use an additional independent reviewer only when a genuinely material uncertainty benefits from separate verification; do not transfer final judgment or accountability to an arbiter.

## Delegation contract

Every delegated task must be concrete, bounded, and sufficiently self-contained. Proportion the detail to the task's risk and complexity, but include the decisive parts of:

- Objective and definition of done.
- Relevant context and already-made decisions.
- Scope, including applicable files, systems, or questions.
- Allowed and prohibited actions, especially whether the task is read-only.
- Acceptance criteria and required evidence.
- A concise return format that separates verified findings from hypotheses or unresolved uncertainty.

Do not assume inherited conversation history is complete, current, or sufficient. Even when a subagent receives parent context, repeat the minimum information that determines task correctness. Avoid copying irrelevant transcript history.

Prefer distilled results with concrete file references, line references, commands, or source citations when applicable. Ask a subagent to write a report file only when the artifact must persist, is too large to return safely, will be consumed programmatically, or is itself part of the user's requested deliverable.

## Task-oriented agent naming and lifecycle

Every delegated packet must have a short, specific task name derived from its objective. Prefer concise Chinese task names when the user is working in Chinese. Put `任务名：<specific task name>` on the first line of every spawn prompt. Never identify an agent to the user only by an automatically generated English nickname such as a philosopher or scientist name.

Maintain an explicit mapping for every wave: `task_name -> agent_id -> runtime nickname`. In commentary, progress reports, evidence, waits, retries, and final integration, refer to agents by `task_name`; the runtime nickname is only a secondary transport label. If a future spawn API exposes `task_name`, `name`, or `nickname`, populate it with the semantic task name. When the current API does not expose such a field, do not pretend the random UI nickname was customized.

Track every spawned `agent_id` until it reaches a verified terminal status. After collecting a completed, errored, interrupted, or cancelled agent result, call `close_agent` in a best-effort cleanup step so completed agents do not occupy slots or remain visually active. Use a `finally`-style cleanup for each wave, including partial spawn failures and parent-task cancellation when control returns. Do not rely on the Desktop activity panel as the source of truth for runtime state; use runtime status and rollout terminal evidence. If `close_agent` returns `not_found` for an agent whose rollout is already terminal, record it as a stale UI/runtime-registry mismatch rather than reporting the agent as running.

## Shared-workspace and side-effect safety

Assume subagents share the primary agent's filesystem and may share the same working directory unless isolation has been explicitly verified. In a shared workspace, the primary agent is the sole writer by default. Use subagents primarily for read-only investigation and review.

Treat tests, builds, coverage tools, formatters, package managers, generators, local servers, snapshots, caches, databases, Git operations, and generated outputs as potentially side-effecting even when they do not intentionally edit source files. Do not run them concurrently unless their working directories, output paths, ports, databases, caches, and other shared state are known to be isolated.

Delegate file modifications only when at least one of the following is true:

- The agent runs in a verified independent worktree or separate physical workspace and the integration path is clear.
- The write scope and all indirect side effects are demonstrably non-overlapping, and concurrent execution is materially beneficial.

Otherwise, serialize the edits or keep all writes in the primary agent. Assign explicit file or directory ownership for every parallel write task. The primary agent must review and integrate all changes, resolve conflicts, and run final verification.

When a read-only sandbox or agent profile is available, prefer it for exploration and review. A natural-language instruction to avoid edits is a behavioral constraint, not a hard permission boundary.

## Orchestration discipline

Prefer a continuously refilled pipeline over a single rigid batch. Start independent work immediately when capacity is available, incorporate early results as they arrive, and refill freed slots while useful investigation or verification remains. Filling the 16-agent target with distinct review or evidence lenses is valid work, even when some redundancy is intentional.

Reuse or steer an existing agent thread for a closely related follow-up when its context remains relevant. Use a fresh subagent when reuse would introduce stale assumptions or unrelated context. Subagents must not create additional subagents unless the primary agent explicitly authorizes it for a clearly justified reason.

Do not expose raw orchestration overhead to the user. Briefly mention parallel delegation when it materially helps the user understand progress or provenance, but deliver one integrated result rather than a collection of disconnected agent responses.

## Evidence and product claims

For claims about what this specific environment can do now, prefer verified current-session capabilities and observed local state. Do not generalize an internal or session-specific tool to all Codex users.

For public claims about Codex behavior, configuration, availability, or supported interfaces, prefer current official OpenAI documentation. Treat official schemas and implementation as supporting evidence, GitHub issues as historical reports rather than normative guarantees, reputable third-party analysis as secondary evidence, and unverified secondary claims as hypotheses only.

When sources conflict, state the scope of the conflict and distinguish current-environment behavior from generally documented product behavior.

## User control

If the user explicitly requests no delegation, a specific execution order, a specific number of agents, or a different safe coordination strategy, follow that instruction for the current task. User instructions override this delegation policy but do not override higher-priority system, developer, safety, administrator, or runtime constraints.
