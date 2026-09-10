# Same-model review cutover: which gates DENY reviewer/verifier/plan_expander

Date: 2026-08-29
Scope: mechanical spawn/dispatch gates only (read-only audit).
Hypothesis: change active `review_model` / `review_reasoning` to
`snaillmou/grok-4.6` / `max` (same physical model+effort as current
root/execution on `grok-v4p`).
Question: which gates would DENY `reviewer` / `verifier` / `plan_expander`
births.

Evidence class: direct (current source + unit tests). No source edits.

## Current pins (unchanged; hypothetical only)

`config/model_profiles.toml` active profile `grok-v4p`:

- execution = `snaillmou/grok-4.6` / `max`
- review = `weiwu/deepseek-v4-pro` / `ultra`

`harness/model_profile.py:4-6` states the intended architecture: roles and
pools stay distinct even when a temporary profile assigns both families to
the same physical model. `sonnet46-all` already does this for Claude. The
special case below is only when that shared physical model is in
`SOL_MODELS`.

`SOL_MODELS` (`harness/dispatch.py:38`):
`snaillmou/grok-4.6`, `gpt-5.6-sol`, `gpt-5.6`.

Review roles (`hooks/root_agent_spawn_gate.py:23`):
`verifier`, `reviewer`, `plan_expander`.

## Verdict (spawn DENY unique to review=grok)

Only **one** mechanical gate uniquely DENIES review-role births because the
child model is Grok/Sol:

- `harness/dispatch.py` `sol_budget_block` — DENY when token-share status is
  `BLOCK` (or the report is present-but-malformed) and LOOP state is not
  `{planning, adjudication, release_finalize}`.

These do **not** DENY a correctly pinned review=grok spawn:

- `hooks/root_agent_spawn_gate.py` — ALLOW if spawn model/effort match the
  updated profile pins (`snaillmou/grok-4.6` / `max`) and `fork_context` is
  not true.
- `hooks/sol_tool_gate.py` — does not gate `spawn_agent`; `verified_leaf`
  exists specifically so a child may share Grok with root after birth.

Desktop `--release-review` currently **bypasses** `sol_budget_block` (see
gate 3). `--role reviewer|verifier` does not.

## Gate 1 — `hooks/root_agent_spawn_gate.py`

| Field | Value |
| --- | --- |
| file:line | `hooks/root_agent_spawn_gate.py:22-25` roles; `:44-58` route keys; `:68-103` decision |
| current rule | Root `spawn_agent` must carry explicit approved `agent_type`, model **exactly** equal to the active-profile pin for that role, `reasoning_effort` **exactly** equal to that pin, and `fork_context` must not be `true`. Review roles read `review_model` / `review_reasoning`. Fail closed if the profile is unreadable. Child sessions are left to the leaf gate (`:73-76`). |
| reviewer-on-grok denied? | **No**, after the profile actually pins review to `snaillmou/grok-4.6` / `max`. There is no extra "child must differ from Sol/root model" check. Same physical model is allowed if it is the review pin. |
| still denied after cutover | Roleless/`default` (`:81-82`); unapproved `agent_type` (`:83-84`); model != `snaillmou/grok-4.6` (`:92-93`); effort != `max` (`:96-99`); `fork_context=true` (`:101-102`); invalid profile (`:87-89`). |
| tests | `tests/unit/test_root_agent_spawn_gate.py:42-51` denies `gpt-5.6-sol` on a **verifier** because it is not the current `review_model` pin (today V4 Pro), not because Sol-family is banned. `:54-62` allows whatever `approved_route("verifier")` returns. `:65-79` asserts execution vs review routes are separate **keys**, not that the strings must differ. After cutover those two tuples may be equal; the test still passes. |
| minimal safe exemption | **None required** for same-model review. Do not add a Sol-model blacklist here: it would fight `model_profile.py` same-physical-model profiles. Keep requiring explicit role + pin + independent context. |

## Gate 2 — `hooks/sol_tool_gate.py` (`verified_leaf`)

| Field | Value |
| --- | --- |
| file:line | `hooks/sol_tool_gate.py:37-41` `GATED_TOOLS`; `:46-53` `MODEL_FAMILY`; `:109-122` `verified_leaf`; `:131-191` main |
| current rule | PreToolUse L0 gate for **root Sol** (`MODEL_FAMILY["snaillmou/grok-4.6"] == "sol"`). Prefix-matched tools: shell/search/bulk-read/test/statistics **and** any `mcp__*`. `verified_leaf` is true iff `LOOP_LEAF_AGENT=1` or `session_is_child(...)`. Comment at `:112-114`: keep root Grok under the Sol gate while allowing a temporary execution profile to use the **same physical model**. Untrusted role labels / agent ids are not enough. Temporary Grok planning guard (`:55-56`, `:151-180`) additionally DENIES root Grok L0 during `planning` (or if the ledger is unreadable while the guard is on). |
| reviewer-on-grok spawn denied? | **No for `spawn_agent`**. `GATED_TOOLS` does not include `spawn_agent` / `Agent`. Unit tests (`tests/unit/test_sol_tool_gate.py:97`) expect `spawn_agent` allowed under the temporary Grok guard. After birth, a Grok reviewer/verifier/plan_expander that is a verified leaf is **not** gated (`:135-136`), which is the same-physical-model exemption. |
| related (not a review-model spawn DENY) | If the Desktop birth tool name starts with `mcp__` (e.g. `mcp__codex_app__create_thread`), **root** Grok already hits this gate for **any** child during non-allowed LOOP states, and during planning when the temporary Grok guard is on. That is a root-model rule, already true on `grok-v4p`; changing `review_model` does not add it. An **unverified** Grok-labeled payload is treated as Sol root (`:137-149`) and then L0-denied outside allowed states. |
| minimal safe exemption | **None required for spawn**. Keep `verified_leaf` as the child exemption (env or persisted child session), not payload `agent_type`. Do not add review-role labels as a bypass. If Desktop review births use `mcp__*` from the root during `execution`, that is a pre-existing root-Grok L0 issue, not a review-cutover issue. |

## Gate 3 — `harness/dispatch.py` `sol_budget_block`  **(the DENY)**

| Field | Value |
| --- | --- |
| file:line | `harness/dispatch.py:38-39` `SOL_MODELS` / exempt states; `:324-347` function; `:918-927` CLI bind (`--role`); **not** called from `--release-review` (`:907-909`) |
| current rule | If `model in SOL_MODELS` and LOOP state not in `{planning, adjudication, release_finalize}` and `data/usage/model_token_share.json` has any window `status == "BLOCK"` (or the file is present but malformed): return a block dict. Missing report = cold-start allow (`:335-336`). `--force` does not skip this. **Role exemption** (`:329-331`) is only `worker`, `executor`, `scout`, `duty_officer`. Review roles are **not** exempt. Docstring `:326-327` says "Explicit child roles stay available even when a temporary profile assigns them the same physical model as Sol" — that sentence is true for **execution** children only. |
| reviewer-on-grok denied? | **Yes**, for harness dispatch of `reviewer` / `verifier` (and `plan_expander` if that role string were passed into the function) when share is `BLOCK` or the share file is malformed, and state is ordinary execution. Test: `tests/unit/test_dispatch_model_pin.py:172-186` writes `agents/reviewer.toml` model `snaillmou/grok-4.6` and expects rc=3 `Sol token-share hard cap` even with `--force`. `:189-199` shows `verifier` still dispatches under BLOCK when its pin is **not** a SOL model (current K3/V4). `:202-213` allows reviewer-on-grok in `release_finalize`. |
| not denied | LOOP in exempt states; no share file; share file with no `BLOCK` windows; execution-role children on Grok (already the grok-v4p worker path). |
| CLI hole | `main()` calls `sol_budget_block` only on the normal `--role` path (`:918-927`). `--release-review` (`:907-909` -> `dispatch_release_review` `:850-891`) never calls it, so a Grok-pinned release reviewer can still launch under BLOCK. `plan_expander` is not in `--role` choices (`:898-899`), so this CLI cannot dispatch that role at all (independent of model). |
| minimal safe exemption | If the operator **explicitly** wants review=grok, extend the role exemption at `:329-331` to the approved review set `reviewer`, `verifier`, `plan_expander` — matching the docstring and `model_profile.py` same-physical-model contract — **or** (tighter) exempt those roles only when the active profile `review_model` is in `SOL_MODELS`. Do **not** exempt by model name alone (that would also pass roleless/default Sol births). Do **not** disable the 25% Sol share KPI: AGENTS.md still counts Sol-model reviewer tokens in the Sol bucket. Update `test_sol_budget_block_stops_any_explicit_sol_role_even_with_force` to the new contract (keep a test that roleless/unknown Sol work still BLOCKS). Optionally bind `sol_budget_block` inside `--release-review` so the two reviewer entries cannot diverge. |

## Gate 4 — `harness/model_profile.py` (not a deny gate)

| Field | Value |
| --- | --- |
| file:line | `harness/model_profile.py:4-6` (module docstring); `:102-110` `profile_values`; `:139-144` writes review TOMLs |
| current rule | Switching a profile copies `review_model`/`review_reasoning` into `agents/reviewer.toml`, `verifier.toml`, `plan_expander.toml`, policy `k3_model`/`k3_reasoning`, and `config/roles.yaml`. Same physical model is an explicit supported state. |
| reviewer-on-grok denied? | **No**. Applying a profile with review=grok/max would succeed and then make Gate 1 **require** that pin. |
| minimal safe exemption | None. Keep role files distinct even when the model strings are equal. |

## Cutover matrix (review = `snaillmou/grok-4.6` / `max`)

| Path | Gate | Result |
| --- | --- | --- |
| Desktop `spawn_agent` reviewer/verifier/plan_expander, pins match, `fork_context=false` | root_agent_spawn_gate | ALLOW |
| Same, model or effort mismatch, or fork | root_agent_spawn_gate | DENY (pin/fork rule, not same-model) |
| Desktop `spawn_agent` tool name (not `mcp__*`) | sol_tool_gate | ALLOW (not a gated tool) |
| Root `mcp__*` birth during execution / guarded planning | sol_tool_gate | DENY root L0 (pre-existing Grok-root rule) |
| Verified leaf Grok reviewer after birth | sol_tool_gate | ALLOW (`verified_leaf`) |
| `dispatch.py --role reviewer\|verifier` while share BLOCK and state=execution | sol_budget_block | **DENY** |
| Same, state in `{planning, adjudication, release_finalize}` | sol_budget_block | ALLOW |
| `dispatch.py --release-review` while share BLOCK | sol_budget_block | currently ALLOW (not bound) |
| `dispatch.py --role plan_expander` | argparse | not a legal `--role` (independent) |

## Minimal safe exemption (if user explicitly wants review=grok)

Single mechanical change, smallest blast radius:

1. In `sol_budget_block` (`harness/dispatch.py:329-331`), treat explicit
   approved review roles the same as explicit execution child roles:
   `reviewer`, `verifier`, `plan_expander` -> return `None`.
2. Keep the BLOCK on missing/unknown role + Sol model (root Sol work,
   roleless births).
3. Keep `root_agent_spawn_gate` pin-matching and `fork_context=false`.
4. Keep `sol_tool_gate.verified_leaf` as the only L0 child bypass.
5. Retarget `tests/unit/test_dispatch_model_pin.py:172-186` to the new
   exemption; add a negative test that Sol-model + empty/`default` role
   still blocks.
6. Optional tighter variant: apply (1) only when active `review_model` is in
   `SOL_MODELS`, so a future non-Sol review family cannot accidentally ride
   a Sol pin.

Do not: blacklist Grok in `root_agent_spawn_gate`; trust payload
`agent_type` inside `sol_tool_gate`; or drop token-share accounting for
Grok review tokens.

## Uncertainty

- Whether the live Desktop birth tool is `spawn_agent` vs an `mcp__*` name
  was not executed in this session. Source says `spawn_agent` is ungated;
  any `mcp__` prefix is gated for root Sol. This does not change the unique
  review=grok DENY, which is `sol_budget_block`.
- Live `data/usage/model_token_share.json` BLOCK status was not required
  for this hypothetical; the deny is conditional on that file as specified
  in Gate 3.
