<!-- size-justified: project landing page; raw logs and generated state are excluded. -->
<div align="center">

# Codex LOOP Orchestra

**Turn one Codex task into a coordinated team of parallel coding agents.**

[![CI](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![Codex](https://img.shields.io/badge/Codex-multi--agent-111.svg)](https://learn.chatgpt.com/docs/agent-configuration/subagents)

[Get started](#start-in-30-seconds) · [How it works](#how-it-works) · [中文](README.zh-CN.md) · [Documentation](INSTALL.md)

</div>

Codex is already good at coding. LOOP gives it a **managed team**.

Install LOOP on top of Codex Desktop or CLI, give the root chat one goal, and let the system coordinate the rest: parallel workers execute, a different model family reviews, finished slots refill automatically, and one live dashboard shows what every agent is doing.

## Why developers use LOOP

- **Finish larger tasks faster.** Keep a configurable pool of useful agents working instead of waiting for one long serial conversation.
- **Catch same-model blind spots.** Run coordination, execution, and review on separate model families or providers.
- **Keep the best model focused.** The root plans and decides; workers and deterministic scripts do the searching, coding, testing, waiting, and counting.
- **See the whole system.** Ordered task IDs and a live Observer replace a wall of random child names and invisible headless processes.
- **Scale past the Desktop UI.** Keep visible native children while supervised headless workers handle wider waves.
- **Install without lock-in.** Activation is backed up, inspectable, reversible, and does not patch Codex binaries.

![Codex LOOP Observer showing Desktop and headless agents, semantic task names, actual models, and live capacity](docs/assets/dashboard.png)

<p align="center"><sub>One view for native and headless agents: task, role, model, plane, health, and remaining capacity.</sub></p>

## Start in 30 seconds

```powershell
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

Restart Codex Desktop, open a new task, and ask the root to use LOOP. To let an agent inspect your environment and guide the installation, give it [AGENT_INSTALL.md](AGENT_INSTALL.md). Linux/WSL and full manual instructions are in [INSTALL.md](INSTALL.md).

> [!NOTE]
> Codex LOOP Orchestra is an independent community project, not an OpenAI product. It installs configuration, custom agents, lifecycle hooks, and `codex exec` tooling; it does not distribute or patch Codex binaries.

## How it works

![Codex LOOP Orchestra architecture: root coordination, deterministic control, Desktop and headless execution, independent review, human release, and live observation](docs/assets/architecture-overview.en.svg)

1. **One root coordinates.** It turns the request into bounded packets and a dependency graph.
2. **Two execution planes run in parallel.** Codex Desktop keeps native visibility and messaging; supervised headless workers absorb wide waves.
3. **LOOP keeps real work moving.** A deterministic controller handles lifecycle state, refill, retry, and dead letters without spending root-model rounds on polling.
4. **A different model family checks the result.** Mechanical evidence feeds independent verification, redo, or bounded escalation.
5. **Humans keep release authority.** Integration is serialized, release review is adversarial, and only the maintainer can merge or publish.

The default operating targets are 20 active agents per parent task, an 80-agent cross-plane envelope, and at most 25% root-model effective production tokens. They are configurable LOOP goals—not guarantees or official Codex limits.

## What LOOP changes

| Plain Codex workflow | With LOOP Orchestra |
|---|---|
| One conversation mixes planning, execution, and status work | The root coordinates; workers execute; scripts manage routine lifecycle work |
| A parallel batch shrinks as agents finish | Eligible work continuously refills open slots |
| One model often reviews its own assumptions | Execution and review can use independent model families |
| Desktop children have low-information identities | Ordered `task_01`–`task_50` IDs plus semantic names in the Observer |
| Wide work is tied to the Desktop conversation layer | Native Desktop and supervised headless execution run together |
| Agent-reported success is easy to over-trust | Mechanical evidence and independent verification gate integration |

The portable profile works without a private gateway. Production profiles may reference provider-routed model IDs already configured by the user; credentials always remain outside this repository.

## Agent-first installation

The recommended installation is **agent-assisted but script-controlled**. Give the repository to Codex or another coding agent and ask it to follow [AGENT_INSTALL.md](AGENT_INSTALL.md):

```text
Read AGENT_INSTALL.md. Ask whether I want 中文 or English first.
Inspect my environment without writing, run the supported dry-run, and explain
every planned change and backup. Wait for my confirmation, then call only the
repository's deterministic installer and verify the result. Do not read,
print, or modify API credentials.
```

The agent discovers the actual environment and explains the operation. Deterministic PowerShell, Python, and Bash code performs installation and restoration. Manual commands remain available.

### Requirements

- Codex CLI with subagents and hooks support; authenticate with `codex login`
- Python 3.11+
- Node.js 22+
- Git 2.40+
- PowerShell on Windows, or Bash on Linux/WSL

### Windows

```powershell
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

Activation installs the portable custom agents, merges only supported Codex multi-agent settings, backs up managed files, renders absolute hook paths, and activates global LOOP mode. Fully restart Codex Desktop and create a new task.

```powershell
# Optional: start a workspace and the Observer
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
./launchers/Start-Codex-LOOP-Monitor.ps1

# Pause without removing the verified backup
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# Restore the pre-install managed files
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

### Linux / WSL

```bash
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./install.sh --repo "$PWD"
```

Restart Codex and create a new task. Restore the pre-activation managed files with:

```bash
./uninstall.sh
```

See [INSTALL.md](INSTALL.md) and [INSTALL.zh-CN.md](INSTALL.zh-CN.md) for isolated test installs, explicit `CODEX_HOME` handling, model profiles, headless prerequisites, and troubleshooting.

## Configuration authority

The installer merges only documented Codex settings from `config/config.toml.example`:

```toml
[features]
multi_agent = true

[agents]
enabled = true
max_concurrent_threads_per_session = 50
default_subagent_model = "gpt-5.6-terra"
default_subagent_reasoning_effort = "medium"
```

Official references: [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Hooks](https://learn.chatgpt.com/docs/hooks), and [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference).

| File | Authority |
|---|---|
| `config/model_profiles.toml` | Execution and independent-review model pins |
| `config/refill_policy.toml` | Parent/cross-plane targets, pacing, and low-water marks |
| `config/orchestration_policy_v2.toml` | Routing mode, budgets, governor, and family pins |
| `config/retry_classes.yaml` | Deterministic retry and dead-letter classification |
| `config/triggers_v2.yaml` | Mechanical escalation signals |
| `agents/*.toml` | Custom-agent instructions, sandbox, and ordered nickname candidates |

Inspect or switch a package-local profile without touching user credentials:

```bash
python harness/model_profile.py list --root .
python harness/model_profile.py set portable --root . --no-global --no-wsl
```

The `three-family-example` profile is intentionally inactive. Replace its placeholders with model IDs already available in your Codex or gateway setup, keep the root on a third independent family, and then activate it. The profile switcher never edits provider catalogs or credentials.

## Control loop

1. The root emits a bounded decision skeleton instead of doing bulk work.
2. Packets declare goal, authorized paths, acceptance commands, and constraints.
3. The DAG gate rejects cycles and overlapping write scopes before dispatch.
4. Desktop or headless workers run in isolated worktrees with explicit role/model/effort pins.
5. Scripts replay acceptance, validate diff boundaries, and emit typed lifecycle events.
6. L2 can pass, request a redo, rank alternatives, or escalate; it cannot release.
7. Only genuine anomalies return to the root. Final merge and release remain human-triggered.

Waiting, polling, tallying, ordinary retries, and state transitions are handled by scripts or the state machine, not by extra root-model turns.

## Repository layout

```text
agents/      custom Codex roles and ordered nickname candidates
config/      routing, concurrency, retry, trigger, and managed-hook policy
harness/     dispatch, state machine, refill, lifecycle, gates, and recovery
hooks/       Codex lifecycle enforcement and context injection
launchers/   Windows activation, Desktop startup, and Observer
metering/    per-role/model token attribution and budget signals
schemas/     packet and report contracts
scripts/     release packaging and integrity helpers
tests/       unit, state-path, installer, security, and orchestration tests
```

Runtime state belongs under `data/` and reports under `reports/`; both are ignored and must never be committed.

## Verification

```bash
python -m pytest tests -q
python scripts/gen_filelist.py .
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

CI validates source/configuration syntax, instruction attention budgets, PowerShell parsing, isolated installers, managed-file closure, and secrets. Live provider routing is an explicit local smoke test because public CI has no user credentials.

## Security and limitations

- Hooks run as the current user and do not elevate privileges.
- Provider credentials remain in the user's authenticated Codex or gateway setup.
- Tool and spawn gates deny operations; they do not grant privileges.
- L1/L2 may block or escalate; only a human can publish.
- The 20/80 targets and 25% root-token target are configurable LOOP policy, not guarantees or official Codex limits.
- The current implementation is a single-machine control plane, not a distributed scheduler.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## History and license

The first LOOP design was developed independently before the current Codex harness surfaces were broadly available as open source. The public release builds on documented extension points instead of maintaining a Codex binary fork.

MIT © 2026 [LEO001020](https://github.com/LEO001020). See [LICENSE](LICENSE), [CONTRIBUTING.md](CONTRIBUTING.md), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
