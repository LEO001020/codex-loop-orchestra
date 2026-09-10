# Global Codex working agreement
<!-- size-justified: canonical policy document; contains all routing, delegation, evidence, and attention-budget rules. Reduction below 16 KB would require removing enforceable policy. -->

## Mandatory LOOP model routing

The Sol root must never spawn a roleless/default/inherited-model child.  A
spawn without an explicit role or model inherits Sol in the current Desktop
runtime and defeats LOOP's purpose even if it is later displayed as an
"execution" agent.

**Routing invariant (hard; model pins are read from the active profile):**
Root Sol may orchestrate 20–30 concurrent children.  Every child spawn must carry
all of: the explicit execution or review `model` and `reasoning` selected for
its role by `config/model_profiles.toml`, an explicit approved `agent_type`,
and `fork_context=false`.

Prohibited child types — each is a routing violation regardless of how the UI labels it:

- **Roleless / default / inherited**: no explicit `agent_type`, or model resolved
  by inheritance or default.
- **Sol children**: no child may inherit or be assigned the Sol (root) model.
- **K3 children**: `weiwu-k3/kimi-k3` and every alias are prohibited.
- **Retired-family children**: a profile review pin such as
  `weiwu/aws.claude-sonnet-4.6` is permitted only as the explicit
  `review_model` of the active profile — never inherited by a child.
- **Forked context**: `fork_context=true` on any spawn is prohibited.
- **L3 is explicit only**: a Sol child is permitted only for a justified L3
  adjudication packet; it must never fill a normal execution or review wave.
- **No L3 exception**: even a justified L3 adjudication child must carry the
  explicit approved `agent_type`, the profile-pinned `model`/`reasoning`, and
  `fork_context=false`; no exception route bypasses the routing invariant.

Approved `agent_type` values:

- `worker` — execution, implementation, exploration, and read-only audit.
- `verifier` or `reviewer` — verification, ranking, release review, adversarial audit.

Enforcement:

- If the spawn interface cannot guarantee explicit role/model, use WSL/headless
  `codex exec`; never fall back to a default Desktop child.
- Before refilling, verify the actual child `turn_context.model`.  Any child
  whose observed model or effort differs from the approved pin is a routing
  violation: stop refilling, report it, correct before new work is launched.
- Monitoring must classify actual models independently.  A role label, family
  nickname, or requested override is not a substitute for the observed
  `turn_context.model` and effort level.
- When the active profile changes, the approved child model changes with it;
  this invariant survives profile rotation.

## Conversation meta-framework (root and every subagent)

Apply this problem-solving program to every non-trivial root turn and packet;
it is not a persona or prose style. Keep internal representations private:
return conclusions, evidence, uncertainty, and concise rationale, never hidden
chain-of-thought.

### 学科程序

先在内部构造与任务匹配的“最小充分专家程序”，不扮演人物或模仿口吻。
只保留必要的对象本体与关键概念、证据层级、标准方法与推断规则、原始资料
类型、核心争议、常见混淆和失效模式。跨学科时只组合必要程序，并检查证据
标准是否相容。

### 自由求解

先完成必要的检索、推理、计算、比较和核验，再写答案。最新、专业、争议或
高风险事实主动检索，优先原始论文、原典、官方资料、标准和原始数据，并交叉
核验关键结论。区分来源直接支持的事实、来源观点、综合推论与未知；关键论断
紧邻引用或证据路径。不能核实就说明“不确定”和缺失证据，不得编造。本机
问题以当前文件、运行状态和机械测试优先于历史报告或自报结论。

### 依赖结构

写作或实施前，在内部建立有类型的依赖结构：中心命题、定义、前提、证据、
机制、替代解释、反例、边界和未决问题，可用支持、解释、反驳、比较、限定或
反馈连接。它可为链、树或网，不强制写成 JSON、表格或显式思维链。前提未
建立不下结论；优先解决最影响中心判断的未决问题；论证未闭合不无故换题。

### 工程交互纪律

以下八条都是硬性要求；任何一条未满足时，不得标记任务完成或
`PASS`，纠正后方可继续。

1. 未知 API 或格式：MUST 先查文档或源码确认；MUST NOT 猜测接口。
2. 需求不明确：MUST 先确认再动手；MUST NOT 在需求未对齐时实施。
3. 业务规则或偏好拿不准：MUST 先询问；MUST NOT 自行脑补。
4. 实现所需能力：MUST 优先复用现有组件；MUST NOT 新增冗余组件。
5. 改动完成后：MUST 验证并完成相应测试；MUST NOT 省略检查。
6. 实现过程中：MUST 遵循现有架构与命名规范；MUST NOT 随意改动架构。
7. 遇到不确定事项：MUST 明确说“不确定”；MUST NOT 不懂装懂。
8. 大改动：MUST 拆成小步且每步可验证；MUST NOT 批量乱改。

This framework never authorizes extra side effects, hidden-reasoning
disclosure, fabricated citations, or bypassing LOOP gates. Higher-level
constraints and user scope remain controlling.

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
`E:\codex-LOOP\codex-loop-s-f2\harness\headless_wave.py` with a bounded task
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

## Engineering expert program

Minimal-sufficient engineering discipline for every agent in this repository.
Select only task-relevant disciplines; investigate before answering.

### Discipline selection (task-relevant only)

Apply proportional to task risk and complexity; omit disciplines not relevant to
the current task:

- **Systems / software engineering**: architecture constraints, component
  boundaries, API contracts, build and test infrastructure.
- **Reliability / correctness**: failure modes, error handling, race conditions,
  idempotency, state consistency.
- **Security**: least-privilege, input validation, secret management,
  supply-chain — only when the task has a security surface.
- **Performance**: throughput, latency, resource budgets — only when
  performance-sensitive.
- **Data integrity**: schema migration, backward compatibility, rollback paths —
  only when touching persistent state.

### Evidence standards

Investigate before answering: read source files, configs, and tests; run
commands to observe actual state.  Do not rely on memory alone.

Classify every material claim:

- **Direct evidence**: observed in current-session code, config, or output.
- **Inference**: derived from evidence but not directly observed; label it and
  state the basis.
- **Unknown**: not determinable without more information; admit it and state
  what would resolve it.

### Typed dependency graph

Before non-trivial work, build an internal typed dependency graph of affected
components: inputs, outputs, call chains, shared state.  Identify side effects
and sequencing constraints.  **Resolve highest-impact unknowns first** — those
whose resolution would most change the approach.

### Operating rules

1. **Investigate before answering**: read source, configs, tests; verify
   interfaces against documentation or code.
2. Clarify ambiguity before acting when it would materially change the output.
3. Verify interfaces: do not assume an API, config key, or flag exists.
4. Reuse existing components; prefer existing utilities over new ones.
5. Test after changes: run the most targeted available test.
6. Preserve architecture: do not change call conventions or module boundaries
   unless explicitly authorised.
7. Admit uncertainty: label inferences; state unknowns.
8. Stop at sufficient completion: stop when acceptance criteria are met.

## Artifact attention-budget invariant

Every file routinely injected into LLM context (README, AGENTS.md, instruction
files, working agreements, config summaries) is a **transformer attention
consumer**.  Bloated context files reduce the effective attention budget
available for task-relevant tokens and can degrade reasoning quality
proportional to the noise injected.

### The 30 MB README failure mode

The canonical failure is a README (or equivalent instruction artifact) that
accumulates recursive content: build logs, test output, manifest dumps,
NDJSON event records, and generated reports appended inline rather than
referenced by path.  The result is a file so large it overwhelms the model
context window or consumes the bulk of the attention budget before task tokens
are read.  This failure mode has occurred in this repository; do not repeat it.
The incident is recorded here as a named reference only — its content must
never be embedded in any instruction file.

### Mandatory size gate

Instruction artifacts injected into LLM context must remain **minimal-sufficient**:
they contain only what changes agent behaviour, not what documents history.

Gate thresholds (all violations = gate FAIL):

- **Default gate (16 KB)**: any single instruction file ≥ 16 KB triggers a gate
  FAIL unless the file carries an explicit size justification annotation.  This
  is the primary enforcement threshold; its purpose is to catch gradual bloat
  early.
- **Hard cap (64 KB)**: any single file > 64 KB is unconditionally rejected.
- **Aggregate cap (256 KB)**: sum of all files injected as developer context in
  one turn must not exceed 256 KB.

### Prohibited content in instruction files

The following content classes must **never** appear inline in README, AGENTS.md,
working agreements, or any file structurally injected into LLM context:

- Raw log output (build logs, test stdout/stderr, shell transcripts)
- NDJSON / JSONL / JSON event streams or ledger dumps
- Generated manifests, file listings, or directory trees longer than ~20 lines
- Token usage reports, meter snapshots, or budget ledger records
- Git history, diff output, or patch content
- Any content whose primary audience is a downstream parser, not a human or model reader

Such content belongs in `reports/`, `data/`, or purpose-specific artifact files
referenced by path.  A path reference costs ~10 tokens; inlining costs thousands.

### Duplication gate

Any prose block of >= 200 characters that appears verbatim (or near-verbatim) in
two or more files that are candidates for LLM injection is a **duplication
violation**.  Resolve by extracting to a single canonical file and referencing it.
The working agreement itself is a primary canonical source; policy excerpts in
other files must not paraphrase or re-state sections already present here.

### Mechanical enforcement

The gate is checked by `tests/orchestration_v2/test_attention_budget_gate.py`.
It runs as part of the standard test suite and must pass before any instruction
file modification is merged.  Gate failures are **FAIL-CLOSED**: the change is
rejected until the file is trimmed or the content is moved to an appropriate
non-injected artifact location.

The gate does **not** apply to:
- Source code files (`*.py`, `*.sh`, `*.toml`, `*.yaml`, `*.json`) — not injected as LLM context.
- Report artifacts under `reports/` and data records under `data/` — referenced by path, not injected.
- Files whose first line contains exactly `<!--nogate-->` and whose exemption
  is listed in this file.  No such exemptions are currently approved.
