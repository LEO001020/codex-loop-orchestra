# grok-v4p review-model apply entry (do not execute)

Date: 2026-08-29
Sources (read-only):
- `E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py`
- `E:\codex-LOOP\codex-loop-s-f2\harness\model_route_manager.py`
- `E:\codex-LOOP\launchers\loop_model.ps1`
- `E:\codex-LOOP\codex-loop-s-f2\config\model_profiles.toml` (current pins; not written here)

This note documents the apply API only. The switch was **not** executed.

## Exact command (WSL must be skipped)

`model_profile.py set` is the apply API. There is **no** CLI flag to override `review_model` or `review_reasoning`. Those values are read from `[profiles.<name>]` in `config/model_profiles.toml` by `profile_values()`.

Required because WSL is broken: **`--no-wsl`**. Without it, `set` also writes into `\\wsl.localhost\Ubuntu\...` (`DEFAULT_WSL_ROOT` and `{wsl_root.parent}/.codex`).

Canonical apply (Windows / no WSL):

```powershell
python E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py set grok-v4p --no-wsl
```

Equivalent with explicit roots (same defaults as the script):

```powershell
python E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py set grok-v4p `
  --root E:\codex-LOOP\codex-loop-s-f2 `
  --codex-home "$env:USERPROFILE\.codex" `
  --no-wsl
```

Optional: add `--json` for machine-readable stdout. Optional: `--no-global` skips `~/.codex` and `~/.opencodex` (not required for this cutover).

## Prerequisite: grok-v4p currently does **not** pin review to Grok

Observed `[profiles.grok-v4p]` in `config/model_profiles.toml`:

| field | current value | desired for this cutover |
| --- | --- | --- |
| `execution_model` | `snaillmou/grok-4.6` | unchanged |
| `execution_reasoning` | `max` | unchanged |
| `review_model` | `weiwu/deepseek-v4-pro` | `snaillmou/grok-4.6` |
| `review_reasoning` | `ultra` | `max` |
| `active_profile` | already `grok-v4p` | still `grok-v4p` after apply |

`apply`/`set grok-v4p` will copy **whatever is in that profile block** onto the runtime pins. Running the command above *as the tree stands now* would keep review on DeepSeek V4 Pro ultra, not Grok 4.6 max.

There is no `set --review-model` API. To make apply write `snaillmou/grok-4.6` / `max` into the review family, first change the profile definition:

```toml
[profiles.grok-v4p]
execution_model = "snaillmou/grok-4.6"
execution_reasoning = "max"
review_model = "snaillmou/grok-4.6"
review_reasoning = "max"
```

Then run the `--no-wsl` `set grok-v4p` command. `set` also writes `active_profile = "grok-v4p"` in the same file.

## APIs that are **not** the cutover entry

| entry | why not |
| --- | --- |
| `E:\codex-LOOP\launchers\loop_model.ps1` | `ValidateSet` is only `status,list,v4f,glm,k3-only`. No `grok-v4p`. Calls `python model_profile.py set $Profile` with **no `--no-wsl`**. |
| `harness/model_route_manager.py enable-grok` | Does call `set grok-v4p` via `run_profile()`, but the argv is hardcoded **without `--no-wsl`**, then curates OpenCodex catalog / `opencodex-catalog.json` and may `opencodex sync`. Wrong for a WSL-broken host. Also does not change `review_model`. |
| `model_route_manager.py restore` / `catalog-sync` | Restore previous profile / catalog only. |

`model_route_manager.run_profile()` argv (no WSL skip):

```text
<python> E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py set <name> --root <LOOP root> --codex-home ~/.codex --wsl-root \\wsl.localhost\Ubuntu\home\codexloop\codex-loop-s-f2 --json
```

## What `set` / apply writes

Implementation: `project_updates()` always; `global_updates(~/.codex)` unless `--no-global`; `wsl_updates()` + `global_updates({wsl_root.parent}/.codex)` unless `--no-wsl`; then `apply_transaction()`; then marker `data/governor/model_profile.json`.

Review family is the logical K3 slot: `k3_model` / `k3_reasoning` and reviewer/verifier/plan_expander agent files. Execution family is `v4_*` plus worker/duty_officer.

### A. Always (project tree, `--root` = `E:\codex-LOOP\codex-loop-s-f2`)

| path | keys written |
| --- | --- |
| `config\model_profiles.toml` | top-level `active_profile` = profile name |
| `config\orchestration_policy_v2.toml` | `[models] v4_model`, `v4_reasoning` (execution); `k3_model`, `k3_reasoning` (review) |
| `agents\worker.toml` | `model`, `model_reasoning_effort` = execution |
| `agents\duty_officer.toml` | `model`, `model_reasoning_effort` = execution |
| `agents\reviewer.toml` | `model`, `model_reasoning_effort` = review |
| `agents\verifier.toml` | `model`, `model_reasoning_effort` = review |
| `agents\plan_expander.toml` | `model`, `model_reasoning_effort` = review |
| `.codex\config.toml` | `[agents] default_subagent_model`, `default_subagent_reasoning_effort` = **execution only** |
| `config\config.toml.example` | same `[agents]` defaults = execution |
| `config\roles.yaml` | roles `executor`, `scout`, `duty_officer` = execution; `reviewer`, `verifier`, `plan_expander` = review |

After a successful transaction, also:

| path | content |
| --- | --- |
| `data\governor\model_profile.json` | marker `codex-loop-model-profile-state/v1` (`profile`, execution/review pins, `updated_files`) |

### B. Conditional global (unless `--no-global`; only if the file already exists)

`--codex-home` default: `%USERPROFILE%\.codex` (`C:\Users\hzq00\.codex` on this host).

| path | written when |
| --- | --- |
| `%USERPROFILE%\.codex\config.toml` | file exists; `[agents]` default subagent = execution |
| `%USERPROFILE%\.codex\agents\worker.toml` | exists (or cloned from project canonical) = execution |
| `%USERPROFILE%\.codex\agents\duty_officer.toml` | exists / canonical = execution |
| `%USERPROFILE%\.codex\agents\reviewer.toml` | exists / canonical = review |
| `%USERPROFILE%\.codex\agents\verifier.toml` | exists / canonical = review |
| `%USERPROFILE%\.codex\agents\plan_expander.toml` | exists / canonical = review |
| `%USERPROFILE%\.codex\opencodex-catalog.json` | exists; `_ensure_model_catalog` for **both** execution and review slugs |
| `%USERPROFILE%\.opencodex\config.json` | only if `--codex-home` resolves equal to default `~/.codex` **and** the file exists; `_ensure_opencodex_model` for both slugs (`providers.*.models/selectedModels`, `subagentModels`, `customModels`) |

### C. Skipped by `--no-wsl` (do not use these on this host)

- Mirror of every **project-relative** path from A under `\\wsl.localhost\Ubuntu\home\codexloop\codex-loop-s-f2\`
- `global_updates` against `\\wsl.localhost\Ubuntu\home\codexloop\.codex` (and WSL `~/.opencodex` is **not** targeted unless that tree is the default Windows `~/.codex`)

`--no-wsl` does **not** skip Windows `~/.codex` / `~/.opencodex`.

## Expected review pins after a correct apply

Once `[profiles.grok-v4p].review_model/review_reasoning` are Grok/`max` and `set grok-v4p --no-wsl` succeeds:

- `orchestration_policy_v2.toml`: `k3_model = "snaillmou/grok-4.6"`, `k3_reasoning = "max"`
- `agents/{reviewer,verifier,plan_expander}.toml`: `model = "snaillmou/grok-4.6"`, `model_reasoning_effort = "max"`
- `config/roles.yaml` roles `reviewer`, `verifier`, `plan_expander`: same model/effort
- execution pins remain `snaillmou/grok-4.6` / `max` (already the grok-v4p execution family)

Verify later with (read-only; not an apply):

```powershell
python E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py status --json
```
