# 8765 display cutover: grok4.6 / V4 Pro / V4 Flash / grok→execution pool

Read-only extract. Source files were not edited.
Evidence date: 2026-08-29. Paths are absolute.

## Goal vs current behavior

Required display:

| raw model | 8765 must show |
|---|---|
| grok-4.6 (any prefix, e.g. `snaillmou/grok-4.6`) | `grok4.6` |
| deepseek-v4-pro | `V4 Pro` |
| deepseek-v4-flash | `V4 Flash` |
| grok workers | execution pool (`pools.v4`), **not** `other` |

Current defects (direct evidence):

1. `shortModel` treats **every** `deepseek-v4*` string as `V4 Flash`, so Pro is mislabeled.
2. `shortModel` has **no grok branch**; grok falls through to the raw slug (or `—`).
3. `model_family` maps all `deepseek-v4*` → `v4` and grok → `other`. Chip counts therefore dump grok into **其他**.
4. `pool_for` only treats `glm-5.2` / `deepseek-v4` as execution. A grok model with no worker-like role returns **`other`**. A grok *worker role* already lands in `v4` via the role fallback — the hole is model-first classification when role is missing/unknown.

Two counters must not be confused:

- **Execution pool** = `pools[pool_for(...)]` (capacity; keys `v4`/`k3`/`sol`/`other`).
- **Chip counts** = `models[model_family(...)]` (observed-model chips). Grok can be in `pools.v4` and still show on the **其他** chip.

---

## Current snippets (with line numbers)

File: `E:\codex-LOOP\launchers\loop_monitor_server.py`

### `model_family` — L129–139

```python
def model_family(model: Any) -> str:
    text = str(model or "").casefold()
    if "claude-sonnet-4.6" in text:
        return "sonnet"
    if "deepseek-v4" in text:
        return "v4"
    if "k3" in text:
        return "k3"
    if "sol" in text:
        return "sol"
    return "other"
```

Observed mapping today:

- `weiwu/aws.claude-sonnet-4.6` → `sonnet`
- `weiwu/deepseek-v4-flash` **and** `weiwu/deepseek-v4-pro` → `v4`
- `snaillmou/grok-4.6` / `grok-4.6` → **`other`**
- `weiwu-k3/kimi-k3` → `k3`
- `gpt-5.6-sol` → `sol`

### `pool_for` — L142–157

```python
def pool_for(model: Any, role: Any = None) -> str:
    model_text = str(model or "").casefold()
    role_text = str(role or "").casefold()
    if any(word in model_text for word in ("gpt-5.6-sol", "gpt-5.5-sol")):
        return "sol"
    if "k3" in model_text:
        return "k3"
    if any(word in model_text for word in ("glm-5.2", "deepseek-v4")):
        return "v4"
    if any(word in role_text for word in ("reviewer", "verifier", "plan_expander")):
        return "k3"
    if any(word in role_text for word in ("worker", "duty_officer", "executor", "scout")):
        return "v4"
    if "sol" in role_text:
        return "sol"
    return "other"
```

Grok path today:

| input | result | why |
|---|---|---|
| `pool_for("snaillmou/grok-4.6", "worker")` | `v4` | no model match; role `worker` fallback |
| `pool_for("snaillmou/grok-4.6", "reviewer")` | `k3` | no model match; role review fallback |
| `pool_for("snaillmou/grok-4.6", None)` | **`other`** | no model match, no role |
| `pool_for("weiwu/deepseek-v4-pro", "reviewer")` | `v4` | `deepseek-v4` substring, model wins over role |
| `pool_for("weiwu/deepseek-v4-flash", "reviewer")` | `v4` | same; pinned by test L511 |

`grok-4.6` does **not** contain `k3`, so the K3 branch is not the grok bug.

### Chip increment vs pool increment

Pool (execution) — L734–735 (native running) and L766 (headless):

```python
pool = pool_for(model, row.get("agent_role"))
pools[pool] += 1
```

`pools` keys are only `v4`/`k3`/`sol`/`other` (L677). Execution pool **is** `v4`.

Chips — L945–949:

```python
model_counts = {name: 0 for name in ("v4", "sonnet", "k3", "sol", "other")}
for row in tasks:
    if row.get("state") in {"running", "open~"}:
        family = model_family(row.get("model"))
        model_counts[family] += 1
```

Returned as `"models": model_counts` (L970). PAGE JS binds `m.v4` → chip labeled **V4 Flash**.

Preferred-target logic also uses `model_family` (L879–901). Splitting grok/pro families does **not** by itself change a grok profile's `preferred` dict: a non-sonnet exec family still falls into the else branch `{v4, k3}`. That is compatible with “grok counts as execution pool”.

### `shortModel` (live PAGE) — L1053

```javascript
function shortModel(value){const v=clean(value),l=v.toLowerCase();if(l.includes('deepseek-v4'))return 'V4 Flash';if(l.includes('sonnet'))return 'Sonnet 4.6';if(l.includes('kimi-k3')||l==='k3')return 'K3';if(l.includes('gpt-5.6-sol')||l==='sol')return 'Sol';return v||'—'}
```

Identical body is copied in `_LEGACY_PAGE` at **L1019**.

Task-row labels go through `taskModel` → `shortModel(x.model)` (L1054). Profile line also uses `shortModel` (L1059).

---

## PAGE chip labels

Live HTML served on `/` and `/index.html` is `PAGE` (handler L1112). Chip row is **L1049**:

```html
<span class="model">V4 Flash <b id="model-v4">0</b></span>
<span class="model">Sonnet 4.6 <b id="model-sonnet">0</b></span>
<span class="model">K3 <b id="model-k3">0</b></span>
<span class="model">Sol <b id="model-sol">0</b></span>
<span class="model">其他 <b id="model-other">0</b></span>
```

JS binders **L1058**:

```javascript
$('model-v4').textContent=m.v4||0;
$('model-sonnet').textContent=m.sonnet||0;
$('model-k3').textContent=m.k3||0;
$('model-sol').textContent=m.sol||0;
$('model-other').textContent=m.other||0;
```

There is **no** grok chip and **no** V4 Pro chip. One `V4 Flash` chip is bound to every `model_family=="v4"` row (Pro + Flash collapsed). Grok increments `m.other`.

`_LEGACY_PAGE` L1015 has the **same five chip labels** (no global-mode card, no sonnet provider-health JS).

---

## Whether PAGE is embedded twice

**Yes — two full HTML documents; only one is served.**

| symbol | lines | served? | inspected by tests? |
|---|---|---|---|
| `_LEGACY_PAGE` | L1003–1033 | **No.** Zero references after definition. | No (`monitor.PAGE` only) |
| `PAGE` | L1036–1067 | **Yes.** `Handler.do_GET` L1112 `PAGE.encode("utf-8")` | Yes |

Both copies currently contain the same chip labels and the same `shortModel` that maps all `deepseek-v4*` → `V4 Flash`.

Implication: a display cutover that only edits `PAGE` is enough for 8765 and for existing tests. `_LEGACY_PAGE` would remain a stale duplicate. Prefer either (a) apply the same `shortModel`/chip patch to both, or (b) delete `_LEGACY_PAGE` in the same change. Do not leave two live copies of the classifier.

They are not byte-identical: `PAGE` adds LOOP 全局模式, 4-column grid, sonnet provider-health JS, and `执行平面` vs legacy `执行面`.

---

## Which tests will break

File: `E:\codex-LOOP\codex-loop-s-f2\tests\unit\test_loop_monitor_server.py` (740 lines). No test currently mentions grok, `V4 Pro`, or `shortModel(` body text.

### Will keep passing if the change is additive

Keep `id="model-v4"`, keep `taskModel` / `审核池·模型待观测` / `执行池·模型待观测`, keep `pool_for("weiwu/deepseek-v4-flash", "reviewer") == "v4"`, keep empty-role reviewer→k3 and worker→v4.

| test | lines | pins |
|---|---|---|
| `test_page_does_not_render_logical_pool_as_observed_model` | 79–84 | `function taskModel(x)` in `PAGE`; 审核/执行池 placeholders; forbids `shortModel(x.model\|\|x.pool)` |
| `test_dashboard_shows_observed_models_without_controller_pool_semantics` | 531–552 | `id="model-v4"` **and** sonnet/k3/sol ids; `shortModel` present; no controller-debt copy |
| `test_dashboard_javascript_references_only_existing_dom_ids` | 582–588 | every `$('id')` in `PAGE` must have `id="..."`; requires `model-sonnet` referenced |
| `test_dashboard_shows_sonnet_provider_health_for_active_sonnet_tasks` | 732–739 | `d.provider_health?.sonnet` and `Sonnet上游` in `PAGE` |
| `test_pool_classification_prefers_actual_model_over_role` | 508–513 | k3 worker→k3; **v4-flash reviewer→v4**; empty reviewer→k3; empty worker→v4 |
| `test_preferred_v4_k3_preserved_for_legacy_profile` | 654–681 | flash execution profile → `preferred` has `v4`+`k3`, no `sonnet` |
| `test_unnamed_task_labels_never_expose_runtime_uuid` | 499–505 | `unnamed_task_label("worker", "v4") == "未命名执行任务"` |

### Will break if the edit is naive

| mistaken edit | failing test |
|---|---|
| Remove `id="model-v4"` (replace chip instead of adding Pro/grok beside it) | `test_dashboard_shows_observed_models_without_controller_pool_semantics` L538 |
| `$('model-grok')` / `$('model-v4-pro')` without matching HTML ids | `test_dashboard_javascript_references_only_existing_dom_ids` L587 `referenced <= declared` |
| Make `pool_for` send grok reviewers to `v4` **and** also change flash-reviewer away from `v4` | `test_pool_classification_prefers_actual_model_over_role` L511 |
| Drop role fallback so `pool_for("", "worker")` is no longer `v4` | same test L513 |
| Reintroduce `shortModel(x.model\|\|x.pool)` | L84 |
| Change `model_family("weiwu/deepseek-v4-flash")` off `v4` **and** also change preferred-family branching to require exact `v4` | `test_preferred_v4_k3_preserved_for_legacy_profile` only if the else-branch condition is rewritten; current code uses `exec_family == "sonnet"` / `pool_for(read_execution_model)=="k3"` / else, so renaming flash family to `v4_flash` still hits else **unless** that if/else is tightened |

### Gaps (no current test will catch a wrong grok cutover)

There is **no** assertion that:

- `shortModel` returns `V4 Flash` / `V4 Pro` / `grok4.6`
- chip HTML contains the string `V4 Flash`
- `model_family("…grok-4.6")` is not `other`
- `pool_for("…grok-4.6", None) == "v4"`
- `snapshot()["models"]` splits pro/flash/grok

A passing existing suite is **not** proof the display goal is met. New tests belong next to `test_pool_classification_prefers_actual_model_over_role` and the PAGE string tests.

---

## Proposed function bodies

Order is load-bearing: Pro **before** generic `deepseek-v4`; grok **before** the `other` default; grok must be classified as execution **before** the role fallback so a grok row with empty role is not `other`. Do not put a bare `"k3" in grok` check that could fire — `grok-4.6` is safe today.

### Python `model_family` (chips + preferred-family)

Keep execution-pool identity in `pool_for`. Use `model_family` for **observed-model chips**.

```python
def model_family(model: Any) -> str:
    text = str(model or "").casefold()
    if "claude-sonnet-4.6" in text:
        return "sonnet"
    if "grok-4.6" in text or "grok4.6" in text:
        return "grok"
    if "deepseek-v4-pro" in text:
        return "v4_pro"
    if "deepseek-v4" in text:
        return "v4"
    if "k3" in text:
        return "k3"
    if "sol" in text:
        return "sol"
    return "other"
```

Also widen chip tallies at L945:

```python
model_counts = {name: 0 for name in (
    "v4", "v4_pro", "grok", "sonnet", "k3", "sol", "other")}
```

`KeyError` would occur if `model_family` returns `grok`/`v4_pro` without this setdefault/init.

Preferred-target block L879–901 can stay: grok/pro exec profiles are not sonnet, so they keep `{v4, k3}` execution-pool targets. That matches “grok workers count in execution pool”.

### Python `pool_for` (execution pool)

```python
def pool_for(model: Any, role: Any = None) -> str:
    model_text = str(model or "").casefold()
    role_text = str(role or "").casefold()
    if any(word in model_text for word in ("gpt-5.6-sol", "gpt-5.5-sol")):
        return "sol"
    if "k3" in model_text:
        return "k3"
    if any(word in model_text for word in (
            "glm-5.2", "deepseek-v4", "grok-4.6", "grok4.6")):
        return "v4"
    if any(word in role_text for word in ("reviewer", "verifier", "plan_expander")):
        return "k3"
    if any(word in role_text for word in ("worker", "duty_officer", "executor", "scout")):
        return "v4"
    if "sol" in role_text:
        return "sol"
    return "other"
```

This makes `pool_for("snaillmou/grok-4.6", None) == "v4"` and `pool_for("snaillmou/grok-4.6", "reviewer") == "v4"` (model wins, same rule as flash reviewer). Existing L511 still holds.

### JS `shortModel` (task table + profile line) — patch **PAGE L1053** and, if kept, **`_LEGACY_PAGE` L1019**

```javascript
function shortModel(value){
  const v=clean(value), l=v.toLowerCase();
  if (l.includes('grok-4.6') || l.includes('grok4.6')) return 'grok4.6';
  if (l.includes('deepseek-v4-pro')) return 'V4 Pro';
  if (l.includes('deepseek-v4-flash') || l.includes('deepseek-v4')) return 'V4 Flash';
  if (l.includes('sonnet')) return 'Sonnet 4.6';
  if (l.includes('kimi-k3') || l === 'k3') return 'K3';
  if (l.includes('gpt-5.6-sol') || l === 'sol') return 'Sol';
  return v || '—';
}
```

Pro must be tested before the generic `deepseek-v4` include.

### PAGE chips (L1049 + L1058)

Keep `model-v4` as Flash so L538 does not break. Add Pro and grok beside it:

```html
<span class="model">grok4.6 <b id="model-grok">0</b></span>
<span class="model">V4 Pro <b id="model-v4-pro">0</b></span>
<span class="model">V4 Flash <b id="model-v4">0</b></span>
<span class="model">Sonnet 4.6 <b id="model-sonnet">0</b></span>
<span class="model">K3 <b id="model-k3">0</b></span>
<span class="model">Sol <b id="model-sol">0</b></span>
<span class="model">其他 <b id="model-other">0</b></span>
```

```javascript
$('model-grok').textContent=m.grok||0;
$('model-v4-pro').textContent=m.v4_pro||0;
$('model-v4').textContent=m.v4||0;
$('model-sonnet').textContent=m.sonnet||0;
$('model-k3').textContent=m.k3||0;
$('model-sol').textContent=m.sol||0;
$('model-other').textContent=m.other||0;
```

New `$('…')` calls **must** match new `id=` attributes or L587 fails.

If chips are **not** split and only `shortModel` is fixed: task rows and the profile line would show `grok4.6` / `V4 Pro` / `V4 Flash`, but the summary chips would still show grok under **其他** and Pro under **V4 Flash**. That would miss half the goal.

---

## Exact edit surface (do not expand)

| location | must change? | why |
|---|---|---|
| `model_family` L129–139 | yes | grok is `other`; pro/flash collapsed |
| `pool_for` L149–150 | yes | grok model without role → `other` |
| `model_counts` L945 | yes if family keys grow | else `KeyError` / silent drop |
| `PAGE` chips L1049 + JS L1053/L1058 | yes | live 8765 UI |
| `_LEGACY_PAGE` L1015/L1019/L1023 | only if the duplicate is kept | not served; stale copy |
| Handler L1112 | no | already serves `PAGE` |
| tests L508–513, L531–552, L582–588 | extend, do not invert existing asserts | add grok/pro cases |

---

## Uncertainty

- Exact grok slug in production (`snaillmou/grok-4.6` vs `grok-4.6`) is not observed in these two files. Matching both `grok-4.6` and `grok4.6` covers the stated goal; other grok versions would still be `other` until listed.
- Whether grok **reviewers** should stay in the review pool (`k3`) is not stated. The proposed `pool_for` follows the existing “actual model wins over role” rule (flash reviewer already → `v4`). If grok review must remain `k3`, do **not** add grok to the execution-model tuple; only special-case `role in worker-like` plus `model_family==grok` for chips. That would leave `pool_for("grok", None)` as `other` — conflicting with the packet text.
- Chip key `v4_pro` vs `v4-pro`: JS `m.v4_pro` must match the Python dict key. Hyphenated `m.v4-pro` is invalid JS.

