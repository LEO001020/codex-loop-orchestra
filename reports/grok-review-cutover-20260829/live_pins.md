# Live V4 Flash / V4 Pro spawn-pin scan

Date: 2026-08-29
Scope (only these roots):
- `E:\codex-LOOP\codex-loop-s-f2\config`
- `E:\codex-LOOP\codex-loop-s-f2\harness`
- `E:\codex-LOOP\codex-loop-s-f2\hooks`
- `C:\Users\hzq00\.codex`
- `E:\codex-LOOP\launchers`

Method: exact-string scan for `deepseek-v4-flash`, `deepseek-v4-pro`, `weiwu/deepseek-v4`, `alibaba-token-plan/deepseek-v4`, and display labels `V4 Flash` / `V4 Pro`. No API keys or secrets are reproduced.

Classification:
- **YES** = currently on the active spawn path (active profile, v2 policy pin that dispatch/gates read, role table used by spawn, or Codex agent TOML / default_subagent used at birth).
- **SELECTABLE** = still a real profile pin that `model_profile.py set` would make live, but not the current `active_profile`.
- **NO** = historical alias, catalog listing, backup, comment, UI label, allowlist, or unused v1 policy.

Current active profile (direct evidence): `config/model_profiles.toml` `active_profile = "grok-v4p"`.
- execution = `snaillmou/grok-4.6` / `max` (not V4)
- review = `weiwu/deepseek-v4-pro` / `ultra` (**this is the remaining live V4 Pro spawn pin**)

There is **no currently selected execution pin** on V4 Flash. V4 Flash remains only as inactive profiles, aliases, catalogs, comments, and UI labels.

## 1. LIVE spawn = YES (V4 Flash or V4 Pro is the current default / review / execution pin)

| path | line | model | role / field | live spawn | recommended change if review+execution both `snaillmou/grok-4.6` `max` |
|---|---|---|---|---|---|
| `E:\codex-LOOP\codex-loop-s-f2\config\model_profiles.toml` | 2 | (profile name `grok-v4p`) | `active_profile` | YES (selects the live review pin) | Keep as active, or add/select a dual-Grok profile after editing pins below. |
| `E:\codex-LOOP\codex-loop-s-f2\config\model_profiles.toml` | 59 | `weiwu/deepseek-v4-pro` | `[profiles.grok-v4p].review_model` | YES | `review_model = "snaillmou/grok-4.6"` and `review_reasoning = "max"` (line 60 currently `ultra`). |
| `E:\codex-LOOP\codex-loop-s-f2\config\orchestration_policy_v2.toml` | 30 | `weiwu/deepseek-v4-pro` | `[models].k3_model` (logical review family; dispatch_v2 `ROLE_FAMILY` maps reviewer/verifier/plan_expander here) | YES | `k3_model = "snaillmou/grok-4.6"`; also set `k3_reasoning = "max"` (line 31). |
| `E:\codex-LOOP\codex-loop-s-f2\config\roles.yaml` | 49 | `weiwu/deepseek-v4-pro` | `reviewer.model` | YES | `model: snaillmou/grok-4.6` and `reasoning_effort: max`. |
| `E:\codex-LOOP\codex-loop-s-f2\config\roles.yaml` | 56 | `weiwu/deepseek-v4-pro` | `verifier.model` | YES | same as reviewer. |
| `E:\codex-LOOP\codex-loop-s-f2\config\roles.yaml` | 63 | `weiwu/deepseek-v4-pro` | `plan_expander.model` | YES | same as reviewer. |
| `C:\Users\hzq00\.codex\agents\reviewer.toml` | 13 | `weiwu/deepseek-v4-pro` | Desktop/CSV spawn pin (`model`) | YES | `model = "snaillmou/grok-4.6"`; `model_reasoning_effort = "max"` (line 14). |
| `C:\Users\hzq00\.codex\agents\verifier.toml` | 12 | `weiwu/deepseek-v4-pro` | L2 spawn pin | YES | same. |
| `C:\Users\hzq00\.codex\agents\plan_expander.toml` | 3 | `weiwu/deepseek-v4-pro` | plan-expander spawn pin | YES | same. |

Evidence that these are the live spawn path:
- `hooks/root_agent_spawn_gate.py` reads **active** `model_profiles.toml` `review_model` / `review_reasoning` for roles `verifier|reviewer|plan_expander` and denies any other model.
- `harness/dispatch.py` `agent_overrides()` / `release_review_pins()` load `agents/<role>.toml` and require `orchestration_policy_v2.toml` `[models].k3_model` to match the reviewer TOML.
- `harness/dispatch_v2.py` `ROLE_FAMILY` maps those three roles to family `k3`, resolved from `[models].k3_model`.
- `harness/model_profile.py` atomically copies profile `review_model` into policy v2 `k3_model`, `roles.yaml` review roles, and `%USERPROFILE%\.codex\agents\{reviewer,verifier,plan_expander}.toml`.

Note: `harness/dispatch.py` also searches `LOOP_ROOT/agents/<role>.toml` first. That package directory is **outside this packet's five search roots**. The Windows Codex-home copies above are in-scope and currently identical (`weiwu/deepseek-v4-pro` / `ultra`). `model_profile.py set` keeps both trees in sync.

## 2. SELECTABLE (real spawn pins, but not currently active)

These are still operator-selectable via `model_profile.py set <name>`. They would become live spawn pins if selected. They are **not** historical aliases.

| path | line | profile | field | current model | live spawn | recommended change for dual Grok max |
|---|---|---|---|---|---|---|
| `...\config\model_profiles.toml` | 8 | `v4f` | `execution_model` | `weiwu/deepseek-v4-flash` | SELECTABLE | Leave as the named V4F profile, or retarget if that profile should also become Grok. Not required for current `grok-v4p`. |
| `...\config\model_profiles.toml` | 43 | `v4f-sonnet-review` | `execution_model` | `weiwu/deepseek-v4-flash` | SELECTABLE | same. |
| `...\config\model_profiles.toml` | 50 | `v4f-v4p` | `execution_model` | `weiwu/deepseek-v4-flash` | SELECTABLE | same. |
| `...\config\model_profiles.toml` | 52 | `v4f-v4p` | `review_model` | `weiwu/deepseek-v4-pro` | SELECTABLE | If this dual-V4 profile is kept, leave it; do not use it if the operator wants Grok review. |
| `...\config\model_profiles.toml` | 7, 42, 49, 56 | labels | display only | `DeepSeek V4 Flash` / `... V4 Pro Review` | NO (label) | Rename labels if profiles are retargeted. |

No other selectable profile currently uses V4 Flash/Pro for **review** except `v4f-v4p` and active `grok-v4p`. Profiles `v4f`, `glm`, `k3-only`, `sonnet46-dual` still review on `weiwu-k3/kimi-k3`.

There is **no existing profile** where both `execution_model` and `review_model` are `snaillmou/grok-4.6`. Dual-Grok therefore requires either editing `grok-v4p` or adding a new profile, then `set`.

## 3. Historical aliases / catalog / comments / unused v1 (live spawn = NO)

| path | line | snippet | why NO | recommended change if dual Grok |
|---|---|---|---|---|
| `...\config\orchestration_policy_v2.toml` | 24 | comment: execution uses DeepSeek V4 Flash; review on Kimi K3 | stale comment vs current grok+V4P | rewrite comment to Grok exec + Grok review (if that is the new policy). |
| `...\config\orchestration_policy_v2.toml` | 44 | `execution_aliases = ["weiwu/deepseek-v4-flash", "alibaba-token-plan/glm-5.2"]` | metering/history bucket, **not** the spawn pin (`v4_model` on line 35 is already `snaillmou/grok-4.6`) | add `snaillmou/grok-4.6` if Grok tokens must stay in the execution bucket; keep Flash as a legacy alias. |
| `...\config\orchestration_policy.toml` | 35 | `v4 = "weiwu/deepseek-v4-flash"` | v1 policy; live dispatch/gates read **v2** | leave, or mirror Grok if any leftover v1 reader is still used. Uncertain whether any production reader still loads this file. |
| `...\config\roles.yaml` | 12 | comment: worker/scout/duty_officer pinned V4 Flash ultra | stale; those roles are already Grok | rewrite comment. |
| `...\config\roles_v2.yaml` | 33, 43, 91 | comments `# weiwu/deepseek-v4-flash` on `model_ref: models.v4_model` | comments only; live value is policy `v4_model` (currently Grok) | rewrite comments. `reviewer/verifier/plan_expander` already use `models.k3_model` with no Flash/Pro string. |
| `...\config\model_catalog_allowlist.toml` | 8 | `weiwu = [..., "deepseek-v4-flash", "deepseek-v4-pro", ...]` | catalog allowlist, not a spawn pin | keep if V4 must remain selectable; optional if cutting over. |
| `...\config\model_catalog_allowlist.toml` | 10 | `alibaba-token-plan = ["deepseek-v4-pro", ...]` | same | same. |
| `...\harness\orchestration_common.py` | 417 | docstring example ``deepseek-v4-flash`` | documentation of "do not hardcode"; not a pin | optional docstring update. |
| `...\hooks\sol_tool_gate.py` | 52 | `MODEL_FAMILY["weiwu/deepseek-v4-flash"] = "v4"` | family classifier for the Sol gate; **no V4 Pro entry here** | add Grok already mapped to `"sol"` (line 47). Flash mapping can remain for leftover children. |
| `...\hooks\sol_tool_gate_v2.py` | 38 | comment "execution currently uses V4 Flash" | stale vs policy-driven `v4_model` | rewrite comment. Gate itself reads policy, not a hardcoded Flash/Pro id. |
| `E:\codex-LOOP\launchers\loop_monitor_server.py` | 1015, 1019, 1049, 1053 | UI bucket `V4 Flash`; `shortModel()` maps any `deepseek-v4` slug to the label `V4 Flash` | display alias (also mislabels V4 **Pro** as Flash) | split Pro vs Flash vs Grok in the dashboard if operators will keep mixed catalogs. |
| `C:\Users\hzq00\.codex\agents\worker.toml` | 7 | comment "DeepSeek V4 Flash, ultra" | comment only; `model` line 12 is already Grok | rewrite comment. |
| `C:\Users\hzq00\.codex\agents\duty_officer.toml` | 8 | comment "DeepSeek V4 Flash ultra" | same | rewrite comment. |
| `C:\Users\hzq00\.codex\opencodex-catalog.json` | 69-70, 377-378, 602-603 | catalog slugs `weiwu/deepseek-v4-pro`, `alibaba-token-plan/deepseek-v4-pro`, `weiwu/deepseek-v4-flash` | availability catalog, not spawn | no spawn change; `model_route_manager.py catalog-sync` may recreate them from the allowlist. |
| `C:\Users\hzq00\.codex\models_cache.json` | 501-502, 631-632, 3049-3050 | same slugs | cache, not spawn | ignore / let sync refresh. |
| `C:\Users\hzq00\.codex\models_cache.before-wm-*.bak` | 583-584, 777-778 | Flash + Alibaba V4 Pro | dated backup | ignore. |
| `C:\Users\hzq00\.codex\.codex-global-state.json` (+ `.bak`) | (minified JSON) | historical UI/chat mentions of `deepseek-v4-flash` / `deepseek-v4-pro` | session chrome / past prompts, not a spawn pin. **Contains secrets elsewhere in the same blob; not copied here.** | do not edit as a pin. |

Not a V4 pin (checked, current execution/default is already Grok):
- `C:\Users\hzq00\.codex\config.toml` line 1 `model = "snaillmou/grok-4.6"`; line 54 `default_subagent_model = "snaillmou/grok-4.6"` (fallback only; role TOML still wins).
- `E:\codex-LOOP\codex-loop-s-f2\config\config.toml.example` lines 52-53 same Grok default.
- `C:\Users\hzq00\.codex\agents\worker.toml` / `duty_officer.toml` `model = "snaillmou/grok-4.6"`.
- `orchestration_policy_v2.toml` line 35 `v4_model = "snaillmou/grok-4.6"`.
- `roles.yaml` `sol` / `executor` / `scout` / `duty_officer` already Grok.
- `launchers\loop_model.ps1` has no model ids (wrapper around `model_profile.py`; ValidateSet is only `v4f|glm|k3-only`, so it cannot select `grok-v4p`).
- `C:\Users\hzq00\.codex\opencodex.config.toml` and `requirements.toml`: no V4 model pins.

## 4. How to switch `review_model` without WSL

There is **no dedicated `--review-model` CLI flag**. Review is always taken from the selected profile's `review_model` / `review_reasoning` and then written into policy/roles/agent TOMLs.

`harness/model_profile.py` (atomic switch; this is the tool that actually changes review pins):

```
py -3 E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py list
py -3 E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py status
py -3 E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py set <profile> --no-wsl
```

Flags:
- `command`: `list` | `status` | `set`
- `profile`: required for `set`; must already exist under `[profiles.*]`
- `--root` (default: LOOP package)
- `--codex-home` (default: `%USERPROFILE%\.codex`)
- `--wsl-root` (default: `\\wsl.localhost\Ubuntu\home\codexloop\codex-loop-s-f2`)
- `--no-wsl` : **skip the WSL tree and WSL `.codex`** — this is the Windows-only switch
- `--no-global` : skip `%USERPROFILE%\.codex` (usually do **not** use this if Desktop spawn TOMLs must change)
- `--json`

`set` writes, among other files: `active_profile`, `orchestration_policy_v2.toml` `[models].v4_*` and `k3_*`, `roles.yaml` execution+review rows, package + Codex-home `agents/*.toml`, and `[agents] default_subagent_*` (execution model only).

`harness/model_route_manager.py` does **not** take a review-model override. Commands:
- `enable-grok` → `model_profile.py set grok-v4p` (Grok execution + **V4 Pro review**; opposite of dual-Grok)
- `restore` → previous profile from `data/governor/temporary_model_route.json`
- `catalog-sync` / `status`
- `--no-sync` skips OpenCodex `sync`

Windows-only dual-Grok procedure (no WSL), after a profile exists with both fields set to `snaillmou/grok-4.6` / `max`:

```
py -3 E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py set <that-profile> --no-wsl
py -3 E:\codex-LOOP\codex-loop-s-f2\harness\model_profile.py status
```

Do **not** use `model_route_manager.py enable-grok` for this cutover: it pins review back to V4 Pro.

`launchers\loop_model.ps1` cannot select `grok-v4p` or a dual-Grok profile today (`ValidateSet` is `status|list|v4f|glm|k3-only`).

## 5. Cutover implication (not executed)

If review+execution should both be `snaillmou/grok-4.6` `max`:
1. Edit `[profiles.grok-v4p]` (or add e.g. `grok-all`) so **both** `execution_model` and `review_model` are `snaillmou/grok-4.6` and both reasoning keys are `max`.
2. Apply with `model_profile.py set ... --no-wsl` so live YES rows in section 1 rewrite together.
3. Leave Flash/Pro in allowlist/catalog unless the operator also wants them removed from Desktop pickers.
4. Cross-family hedge currently provided by V4 Pro review vs Grok execution would collapse; that is a policy choice, not a mechanical blocker.

## 6. Bottom line

- **Live V4 Flash spawn pins: none** (execution already Grok).
- **Live V4 Pro spawn pins: the whole review family** — active profile `grok-v4p.review_model`, policy v2 `k3_model`, `roles.yaml` reviewer/verifier/plan_expander, and `%USERPROFILE%\.codex\agents\{reviewer,verifier,plan_expander}.toml`.
- Everything else in-scope is a selectable inactive profile, catalog/allowlist, stale comment, v1 leftover, UI alias, or backup.
