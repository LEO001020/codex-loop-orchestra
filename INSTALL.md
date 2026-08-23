# Installation

The recommended path is to ask a local Codex agent to follow
`AGENT_INSTALL.md`. The same deterministic commands are available below for
manual installation.

## Before you start

- Keep the cloned repository in a permanent location. Managed hooks point to
  this control root; moving it after activation breaks those paths.
- Install Python 3.11+, Node.js 22+, Git 2.40+, and Codex CLI.
- Run `codex login` and confirm a normal Codex task works.
- Provider credentials belong to your Codex or gateway configuration, never in
  this repository.

## Portable model profile

The default profile uses `gpt-5.6-terra` for execution and `gpt-5.6` for
independent review. The root model remains selected by the user in Codex.

The coordinator, executor, and reviewer do not have to use Codex GPT models.
Select the root in your Codex/OpenCodex-compatible runtime, and put any model
IDs already exposed by that runtime into the execution/review profile. The
three roles may use different vendors; gateway registration and credentials
remain outside this repository.

Preview profiles:

```bash
python harness/model_profile.py list --root .
```

The `three-family-example` profile is intentionally not active. Replace its
example model IDs with models available in your own environment before using
it. Never add provider tokens to `model_profiles.toml`.

## Windows

Open PowerShell in the repository:

```powershell
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

Activation performs two reversible phases:

1. Install portable custom-agent TOMLs and merge missing documented
   `[features]`/`[agents]` keys into `%USERPROFILE%\.codex\config.toml`.
   Existing keys are preserved and changed files are backed up.
2. Install managed requirements and the active LOOP agreement, then write the
   global-mode marker. Pre-existing `AGENTS.md`, `hooks.json`, and
   `requirements.toml` are recorded in a verified restore ledger.

Fully exit and restart Codex Desktop. Start a new task; existing tasks do not
retroactively reload all configuration layers.

```powershell
# Inspect current state
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Status

# Start the read-only dashboard
./launchers/Start-Codex-LOOP-Monitor.ps1

# Open a specific workspace with LOOP active
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
```

Pause versus restore:

```powershell
# Disable injection for new/resumed tasks; keep installation and backups
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# Restore the pre-install managed files from the verified backup
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

Use `-CodexHome <path>` when `CODEX_HOME` is not the standard user directory.

## Linux / WSL

```bash
./install.sh --repo "$PWD"
```

The installer validates Python, Node, Git, Codex, TOML inputs, installs custom
agents, merges only missing documented Codex keys, initializes the package-local
control state, activates managed global hooks, and runs the smoke gate.

Use `--skip-smoke` only when the real provider route is intentionally not
available yet. Before production use, run the smoke gate after configuring the
models:

```bash
./harness/smoke_gate.sh "$(pwd)"
```

Restore the managed global files and remove unchanged LOOP agent TOMLs:

```bash
./uninstall.sh
```

## Headless plane

Headless workers require a working `codex` executable on the Linux/WSL PATH or
`CODEX_HEADLESS_BIN`. A third-party OpenCodex gateway is optional; when used,
its health endpoint and model IDs must be configured by the operator. LOOP does
not bundle gateway credentials.

A lifecycle-visible wave uses:

```bash
python harness/headless_wave.py --root . --manifest path/to/manifest.json --wait-all
```

For sustained parent-task refill, import a bounded parent manifest once and let
the existing refill consumer own births. Do not launch the same packets through
a second path.

## Isolated verification

These checks avoid the user's real Codex home:

```bash
tmp="$(mktemp -d)"
export CODEX_LOOP_STATE_DIR="$tmp/state"
python harness/install_user_config.py --root . --codex-home "$tmp/.codex" --dry-run
python harness/global_desktop_mode.py activate --root . --codex-home "$tmp/.codex"
python harness/global_desktop_mode.py status --root . --codex-home "$tmp/.codex"
python harness/global_desktop_mode.py restore --root . --codex-home "$tmp/.codex"
```

Then run:

```bash
python -m pytest tests -q
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

## Troubleshooting

- **Spawn denied:** the requested role/model/effort does not match the active
  profile, or `fork_context=true` was used.
- **Dashboard has no semantic task:** the lifecycle roster is stale or the
  task prompt did not begin with `任务名：...`.
- **Headless deficit remains:** verify `codex`/gateway health and check
  `data/lifecycle/exec_roster.json` rather than counting process starts.
- **Hooks do not run:** verify global mode Status, absolute managed-hook paths,
  hook trust, and that Codex was fully restarted.
- **Restore refuses:** do not bypass a backup hash mismatch. Inspect the backup
  and install-state ledger before proceeding.
