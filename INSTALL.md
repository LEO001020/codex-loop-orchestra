# Installation

The recommended approach is to ask a local Codex agent to follow
`AGENT_INSTALL.md`. It will inspect the environment, present the proposed
changes and backup plan, and wait for approval before invoking the deterministic
installer. The same scripts are documented below for manual installation.

## Before you start

- Keep the cloned repository in a permanent location. Managed hooks point to
  this control root; moving it after activation breaks those paths.
- Install Python 3.11+, Node.js 22+, Git 2.40+, and Codex CLI.
- Run `codex login` and confirm a normal Codex task works.
- Provider credentials belong to your Codex or gateway configuration, never in
  this repository.

## Model profiles

The default profile uses `gpt-5.6-terra` for execution and `gpt-5.6` for
independent audit. The user continues to select the root model in Codex.

List the available profiles:

```bash
python harness/model_profile.py list --root .
```

The `three-family-example` profile is intentionally inactive. Before enabling
it, replace the example model IDs with models that are available in your own
environment. Never add provider tokens to `model_profiles.toml`.

## Windows

Open PowerShell in the repository:

```powershell
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

Activation has two reversible phases:

1. Install portable custom-agent TOMLs and merge missing documented
   `[features]`/`[agents]` keys into `%USERPROFILE%\.codex\config.toml`.
   Existing keys are preserved and changed files are backed up.
2. Install the managed requirements and active LOOP working agreement, then
   write the global-mode marker. Pre-existing `AGENTS.md`, `hooks.json`, and
   `requirements.toml` files are recorded in a hash-verified restore manifest.

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

The installer validates Python, Node.js, Git, Codex, and the TOML inputs. It
then installs the custom agents, merges only missing documented Codex keys,
initializes the package-local control state, enables the managed global hooks,
and runs the smoke-test gate.

Use `--skip-smoke` only when the model-provider route has not been configured
yet. Before production use, configure the models and run the smoke-test gate:

```bash
./harness/smoke_gate.sh "$(pwd)"
```

Restore the managed global files and remove unchanged LOOP agent TOMLs:

```bash
./uninstall.sh
```

## Headless runtime

Headless workers require a working `codex` executable on the Linux/WSL `PATH`,
or an explicit path in `CODEX_HEADLESS_BIN`. A third-party gateway such as
OpenCodex is optional. If one is used, the operator must configure its health
endpoint and model IDs. LOOP does not bundle or store gateway credentials.

Start a headless batch that is registered with LOOP's lifecycle system:

```bash
python harness/headless_wave.py --root . --manifest path/to/manifest.json --wait-all
```

For sustained refill of a parent task, import one bounded parent manifest and
let the existing refill service start all workers. Do not launch the same task
packets through a second path.

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

- **Subagent launch denied:** the requested role, model, or reasoning effort does
  not match the active profile, or the request used `fork_context=true`.
- **Dashboard does not show a task name:** the lifecycle roster is stale, or the
  task prompt did not begin with `任务名：...`.
- **Headless worker deficit does not fall:** verify the health of `codex` or the
  gateway and inspect `data/lifecycle/exec_roster.json`. An operating-system
  process starting does not by itself mean that LOOP has registered the worker.
- **Lifecycle hooks do not run:** verify global-mode status, the absolute managed
  hook paths, hook trust, and that Codex was fully restarted.
- **Restore is refused:** do not bypass a backup hash mismatch. Inspect the
  backup and installation-state manifest before proceeding.
