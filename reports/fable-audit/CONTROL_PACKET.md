# LOOP ControlPacket · Fable audit handoff

Packet ID: `CP-FABLE-20260810-01`

Parent authority: root Sol session `019feacd-184e-7472-9f11-ea364c22e595`

This file is the compiled control context for headless V4/K3 auditors. It is not
a transcript summary and must not be treated as evidence by itself. Decisions
and constraints below are authoritative for the packet; factual claims must be
checked against the referenced files or runtime evidence.

## Objective

Produce a desensitized, reproducible snapshot of the current Codex LOOP F2
overlay and a low-friction design that reduces **all-in rolling 5-hour Sol
effective-token share** to 20–25%, while preserving 48-way execution, ipybox
availability, root judgment, and user visibility.

## Root decisions that children must preserve

1. Fable's ipybox work is an overlay on the existing F2/old-LOOP environment.
   Never rerun an old package's `install.sh` over the current environment.
2. Desktop is the control/observation plane. Wide execution and audit run in
   WSL/headless processes; they must remain visible through `exec_roster`.
3. LOOP's normal single-pool target is 48. Preferred V4/K3 reservations are
   36/12 and borrowable. Birth interval remains one second.
4. ipybox is available to every worker. Stability may use lazy startup,
   sandboxing, process groups, parent-death handling, and orphan cleanup; it may
   not be obtained by globally disabling ipybox.
5. V4 and K3 have a 1,000,000 physical context window, 950,000 effective window,
   and an 800,000 auto-compaction threshold. GPT/Sol remains at its current
   353,400 effective window.
6. Avoid friction-heavy global bans, fixed long pauses, or review gates on every
   ordinary packet. Controls should be measurement-driven and fail-visible.
7. Root Sol retains requirements, cross-packet decisions, integration, conflict
   resolution, and final release accountability. Delegation may reduce its
   context load but may not erase its authority.

## Frozen measurements

The following baseline was computed before the current Fable audit agents were
launched. Do not mix their usage into this baseline.

- Window: Windows Desktop + WSL/headless, rolling five hours.
- Corrected maintenance scope: per user turn, not whole rollout.
- Sol share of raw total tokens: 71.10%.
- Sol share of effective tokens (`total - cached_input`): 45.93%.
- V4 effective share: 53.61%.
- K3 effective share: 0.46%.
- Current install/audit root: 91.57% Sol effective and about 67% of all Sol
  effective tokens in the window.
- Excluding only that exceptional root, remaining production share: 22.82% Sol
  effective. This diagnostic split is not permission to exclude root usage from
  the final all-in KPI.

Meter defect to verify: `metering/model_token_share.py` currently sets an entire
rollout to maintenance when any marker appears anywhere in the raw file. Long
productive roots that quote smoke evidence are therefore misclassified. The
fix must classify maintenance per turn and add an explicit rolling-five-hour
window.

## Crash evidence and execution-plane decision

At 23:13:11–23:13:37, sixteen Desktop-native 950k subagent rollouts were created
at one-second intervals. At 23:15:06 the Codex `codex.exe` app-server restarted.
The Electron/ChatGPT shell retained its 20:55 start time; OpenCodex retained PID
23124; WSL did not restart; WSL had ample free memory; orphan gateways were zero.

Ruling for this audit: do not create a wide Desktop-native agent wave. The audit
uses `harness/lifecycle_supervisor.py` and headless `codex exec`, with reports on
disk and precise tasks in `exec_roster`.

## Evidence map

- `AGENTS.md`
- `agents/{worker,duty_officer,verifier,reviewer}.toml`
- `config/refill_policy.toml`
- `config/ipybox_sandbox.json`
- `harness/dispatch.py`
- `harness/lifecycle_supervisor.py`
- `harness/ipybox_lazy.py`
- `harness/ipybox_cleanup.py`
- `metering/model_token_share.py`
- `reports/ipybox-headless-stability-20260810.md`
- `/mnt/e/codex-LOOP/launchers/loop_monitor_server.py`
- `/mnt/e/codex-LOOP/.bootstrap/ipybox-supervised`
- `/mnt/e/codex-LOOP/.bootstrap/run-fable-headless-audit.py`

## Child return contract

Each child receives this control information plus one bounded objective. It must:

- remain read-only;
- cite concrete `file:line` evidence;
- separate verified facts, hypotheses/risks, and recommendations;
- return at most eight findings;
- never paste large logs or source bodies into its final response;
- write/return only its audit report; the parent integrates and decides.

## Control-channel protocol

The control channel has four layers:

1. **Spawn control:** the root compiles a versioned ControlPacket and injects its
   decisive facts, constraints, evidence paths, and output schema into the child
   prompt. Raw root chat history is not inherited.
2. **Evidence control:** referenced artifacts are immutable for that packet and
   identified in the package manifest/SHA-256 list. Children verify facts from
   files rather than trusting narrative summaries.
3. **Lifecycle control:** the root can cancel a running child through the
   lifecycle supervisor. An urgent changed decision cancels and relaunches the
   packet with a new ControlPacket revision; the child does not poll.
4. **Follow-up control:** after a child turn completes, the root may send a
   bounded correction with `codex exec resume <thread_id> <prompt>`. The resume
   prompt must name the previous ControlPacket ID and the new revision. It may
   not silently mutate an already accepted decision.

This provides real parent authority without multiplying the root's full context
by every child.

## Fable questions

1. Does the proposed control channel preserve enough root intent without hidden
   transcript inheritance?
2. Is all-in five-hour Sol effective share the correct control KPI, and what
   minimum denominator prevents small-window oscillation?
3. Which root actions are safe to delegate to isolated worktrees, and which must
   remain root-only?
4. How should K3 usage rise above 0.46% without creating mandatory review on
   every ordinary packet?
5. Can the Desktop-wide-wave crash be reproduced with MCP disabled and again
   with 272k/950k context to isolate app-server object/context pressure?
6. Are `cancel + relaunch` and `resume after turn` sufficient dynamic-control
   semantics for headless workers?
