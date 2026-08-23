<!-- size-justified: project landing page; raw logs and generated state are excluded. -->
<div align="center">

# Codex LOOP Orchestra

**Codex already has subagents. LOOP makes them work as one controlled, continuously running engineering team.**

[![CI](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![Codex](https://img.shields.io/badge/Codex-multi--agent-111.svg)](https://learn.chatgpt.com/docs/agent-configuration/subagents)

[Quickstart](#quickstart) · [Why LOOP](#from-codex-primitives-to-a-controlled-system) · [Architecture](#architecture) · [中文](README.zh-CN.md) · [Install guide](INSTALL.md)

</div>

**In 20 seconds:** Codex can already run agents in parallel. LOOP keeps that team from fading out as tasks finish, lets a separately configured model review the work, moves wide execution into supervised background workers, and shows everything in one dashboard. The coordinator, executors, and reviewers may each use any model exposed by Codex/OpenCodex; they do not have to use GPT models. LOOP installs as reversible configuration and tooling, not a Codex fork.

**Install:** give this repository and [AGENT_INSTALL.md](AGENT_INSTALL.md) to Codex (recommended), or jump to the [manual Windows/Linux/WSL commands](#install-pause-and-restore).

Codex natively provides powerful primitives: parallel subagents, custom agents, and root-to-child communication. Primitives alone, however, do not enforce how a large task is decomposed, which paths a child may change, how useful concurrency is sustained, what evidence counts as success, when another model must review the result, or who may publish it.

**LOOP adds that missing control and operations layer.** Give it one objective: the root makes bounded decisions, workers execute in parallel, scripts enforce the lifecycle, independent reviewers challenge the result, the Observer shows the real state, and a human retains release authority. LOOP is not another chat UI and does not patch Codex binaries.

## High concurrency that stays high

Most multi-agent demos show **launch concurrency**: start a batch, then watch the count fall. LOOP controls **sustained concurrency**. Set a target and, while useful work and real capacity remain, LOOP counts actual Agents across Desktop and headless execution, then refills completed, failed, or empty slots from a bounded backlog—without user nudges.

That is the difference between asking an LLM to “please keep 20 Agents busy” and making 20 a runtime control target. Prompt-only orchestration—whether the underlying model is Codex, Claude, or something else—can request another batch, but a prompt alone does not own durable lifecycle state, an effective-concurrency counter, a supervised task backlog, or a deterministic refill controller. LOOP puts those responsibilities in code. Defaults target 20 active Agents per parent and 80 across one machine; configure other targets within real Codex/gateway and hardware limits.

## The control loop is the product

LOOP's value is not in starting many agents—Codex already supports parallel subagents. Its value is the enforceable loop around them:

- **The root model is consumed by search, bulk reads, tests, and status work.** LOOP makes the root emit a bounded decision skeleton instead of doing batch work itself, preserving its attention for decomposition, genuine anomalies, and final synthesis.
- **Delegated work drifts because scope and completion criteria are implicit.** Each task packet declares its goal, authorized paths, and acceptance commands, while the decision skeleton carries governing constraints. A worker can finish from bounded input, and both scope and definition of done remain auditable.
- **Parallel tasks may contain dependency cycles or write the same paths.** A DAG (dependency graph) gate rejects cycles and overlapping write paths before dispatch, stopping unsafe plans before they consume model time or create merge conflicts.
- **Workers may share state or inherit the wrong role and model.** Desktop or headless workers run in isolated worktrees with explicit role, model, and reasoning-effort pins. Writes remain separated, routing is inspectable, and execution and review may use different providers through Codex/OpenCodex.
- **An Agent's self-reported `PASS` is easy to over-trust.** Scripts replay acceptance commands, validate diff boundaries, and emit typed lifecycle events. Integration depends on reproducible evidence, not confidence in prose.
- **A reviewer that can also publish collapses verification and authority into one role.** L2 independent model review may pass, request redo, rank candidates, or escalate—but cannot publish. Weak work can be blocked without giving the reviewer release power.
- **Returning every unusual event to the root wastes expensive turns; automatic release grants too much authority.** Only genuine anomalies return to the root, while final merge and release are human-triggered. Routine work stays inexpensive; ambiguous and irreversible decisions reach the right authority.
- **Asking an LLM to wait, poll, count, retry, or remember lifecycle state makes control costly and probabilistic.** Scripts and state machines handle deterministic transitions, so routine control consumes no extra root-model turns and remains replayable.

Layer roles: **L0/L1 are scripted mechanical checks, L2 is independent model review, L3 is bounded root adjudication, and L4 is human release authority.**

In short: **LLMs handle judgment; code handles state; independent models handle review; humans handle release.**

## See the system, not a wall of threads

![Codex LOOP live dashboard in English, showing active agents, execution planes, observed models, and semantic task names](docs/assets/dashboard.en.png)

Codex exposes native subagent activity. LOOP adds ordered task IDs and correlates native children with supervised headless workers, semantic task names, observed models, lifecycle freshness, health, and remaining capacity. The Observer is read-only: it reports what is actually running but cannot dispatch, adjudicate, or publish.

## Quickstart

Give this repository to Codex or another coding agent and paste:

```text
Install Codex LOOP Orchestra from https://github.com/LEO001020/codex-loop-orchestra.
Read AGENT_INSTALL.md first. Inspect my environment, show the dry-run and backups,
wait for my approval, then activate LOOP and verify the installation.
Never read, print, or change my API credentials.
```

Prefer manual installation? Jump to [Install, pause, and restore](#install-pause-and-restore), or read the complete [Windows/Linux/WSL guide](INSTALL.md).

## From Codex primitives to a controlled system

LOOP preserves native Codex capabilities and adds policies that make them sustainable at larger task shapes:

- **Desktop exposes native Agents and root-child messaging, but very wide waves may pressure its conversation transport.** LOOP keeps a light native plane and moves wider execution to supervised WSL/headless workers. **Payoff: native visibility without putting all heavy execution on Desktop.** The maintainer's instability around 10–20 busy children is anecdotal, environment-specific, unpublished, and not a Codex limit.
- **Ordinary calls reconstruct parsed data and intermediate computation.** LOOP can give WSL/headless workers an optional, lazy, session-scoped IPybox Python workbench. **Payoff: reuse dataframes, indexes, counters, and digested output while that worker's kernel session lasts.**
- **Without a duty split, the root may spend high-tier turns on execution and bookkeeping.** LOOP restricts the root to planning and genuine adjudication; workers execute and scripts manage routine lifecycle work. **Payoff: spend the strongest available model on judgment and measure root-token share as a governance signal.**
- **Native activity and headless processes appear separately.** LOOP aggregates both into a read-only Observer and maps ordered IDs to tasks. **Payoff: one view for task, model, plane, health, freshness, and capacity.**

## Architecture

![Codex LOOP Orchestra architecture: root coordination, deterministic control, Desktop and headless execution, independent review, human release, and live observation](docs/assets/architecture-overview.en.svg)

Read the diagram from top to bottom:

1. **Plan once.** The root supplies decisions and boundaries; packets form a conflict-checked DAG.
2. **Control deterministically.** Budgets, dispatch, refill, retry, dead letters, and lifecycle transitions live in code—so routine control costs no root-model rounds and remains replayable.
3. **Execute on two planes.** Desktop retains native visibility and messaging; supervised headless workers absorb wider waves.
4. **Accept with evidence.** Mechanical L0/L1 checks feed independent L2 review; redo returns to execution and genuine uncertainty escalates.
5. **Release with human authority.** Accepted work enters a serial integration queue and adversarial release review, but only a human can merge or publish.

### Control targets have a purpose

| LOOP policy | Why it exists—and its boundary |
|---|---|
| **20 active Agents per parent task** | Keeps a useful wave populated instead of treating the initial spawn count as success. A configurable target, not a guarantee. |
| **80 Agents across both execution planes** | Lets several dialogues and both planes share one single-machine capacity budget. Not an official Codex limit. |
| **First 8 children prefer Desktop-native transport** | Keeps the visible plane useful while steering wider work headless. A working-agreement preference, never a concurrency cap. |
| **50 configured child threads per session** | Leaves room for a 20-Agent wave, reviewers, and replacements. A package configuration value, not a universal Codex default. |
| **≤25% effective root production tokens** | Detects bulk work or mechanical control leaking into the coordinator. A rollout-based governance target, not a cost or quality promise. |

## A persistent Python tool plane

WSL is not only overflow capacity; it is also LOOP's persistent computation plane. IPybox is an optional service that runs Python in a separate sandbox. When you install and register its upstream MCP server yourself, LOOP's lazy wrapper lets WSL/headless execution workers keep Python objects alive across tool calls and digest large output outside the model context.

The state is **worker-session scoped**, not permanent: reset, crash, cleanup, or worker teardown removes it, and kernels are not shared between workers. Desktop-native IPybox remains disabled so Desktop stays a light control and observation plane. The upstream dependency is optional and not bundled; see [VERSIONS.lock](VERSIONS.lock) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Install, pause, and restore

The recommended path is **agent-assisted but script-controlled**. [AGENT_INSTALL.md](AGENT_INSTALL.md) requires environment inventory, dry-run, backup explanation, human approval, deterministic installation, and mechanical verification.

Requirements: Codex CLI with subagents/hooks, Python 3.11+, Node.js 22+, Git 2.40+, and PowerShell or Bash.

### Windows

```powershell
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate

# Optional Observer and workspace launcher
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
./launchers/Start-Codex-LOOP-Monitor.ps1

# Pause: stop LOOP injection for new tasks; keep the installation and backups
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# Roll back: restore pre-install managed files from the verified backup
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

### Linux / WSL

```bash
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./install.sh --repo "$PWD"

# Roll back managed files and remove unchanged LOOP Agent TOMLs
./uninstall.sh
```

Fully restart Codex and create a new task after activation. See [INSTALL.md](INSTALL.md) for isolated installs, explicit `CODEX_HOME`, headless prerequisites, model profiles, and troubleshooting.

## Configure separation without bundling secrets

The coordinator, executors, and reviewers are three independent routing choices. The root model is selected by the user in Codex or an OpenCodex-compatible runtime; LOOP pins execution and review children through the active profile. **None of the three roles is required to use a Codex GPT model:** use any model IDs exposed by your Codex/OpenCodex gateway, including different vendors for execution and audit. Provider registration and credentials stay in your own environment.

The installer merges only documented Codex settings. LOOP policy remains in repository-owned files:

| File | Design purpose |
|---|---|
| `config/model_profiles.toml` | Makes execution and review routing explicit and rotatable. |
| `config/refill_policy.toml` | Turns concurrency into a sustained target with pacing and low-water marks. |
| `config/orchestration_policy_v2.toml` | Centralizes routing, budgets, governors, and IPybox plane policy. |
| `config/retry_classes.yaml` | Keeps ordinary retry/dead-letter decisions deterministic. |
| `config/triggers_v2.yaml` | Converts mechanical risk signals into review or escalation. |
| `agents/*.toml` | Reuses native custom-agent instructions, sandboxes, and ordered nickname candidates. |

```bash
python harness/model_profile.py list --root .
python harness/model_profile.py set portable --root . --no-global --no-wsl
```

The portable profile works without a private gateway. `three-family-example` is intentionally inactive: replace its placeholders with model IDs already available in your own Codex or gateway setup before activation. LOOP never edits provider credentials or catalogs.

Official references: [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Hooks](https://learn.chatgpt.com/docs/hooks), and [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## Trust, safety, and limits

- LOOP is an independent community project, not an OpenAI product or endorsement.
- It uses documented configuration, custom agents, hooks, and `codex exec`; it does not distribute or patch Codex binaries.
- Hooks run with the current user's permissions. Gates deny operations; they do not grant privileges.
- Credentials remain in the user's authenticated Codex or gateway environment and are excluded from release archives.
- L0/L1/L2 may block, redo, rank, or escalate; none may publish. Final merge and release remain human-triggered.
- The current release is a single-machine control plane, not a distributed scheduler.
- Multi-model separation is an available design mechanism, not a benchmarked quality guarantee.
- 20/80, 50, the Desktop transport preference, and ≤25% are LOOP policies—not official Codex limits or universal performance promises.

Report vulnerabilities privately via [SECURITY.md](SECURITY.md).

## Verify, explore, and contribute

```bash
python -m pytest tests -q
python scripts/gen_filelist.py .
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

CI checks syntax, attention budgets, PowerShell parsing, isolated installers, managed-file closure, archive boundaries, and secrets. Public CI cannot test private provider routing, so that remains an explicit local smoke test.

Source map: `agents/` defines roles, `config/` policy, `harness/` the state machine and gates, `hooks/` lifecycle enforcement, `launchers/` activation and Observer startup, `metering/` token attribution, `schemas/` contracts, and `tests/` mechanical evidence. Runtime `data/` and `reports/` are ignored and never belong in a public release.

The public release builds on Codex's documented extension points rather than maintaining a binary fork. MIT © 2026 [LEO001020](https://github.com/LEO001020). See [LICENSE](LICENSE), [CONTRIBUTING.md](CONTRIBUTING.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
