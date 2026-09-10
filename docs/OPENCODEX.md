# OpenCodex as an optional external sidecar

LOOP is gateway-agnostic. It does not know what OpenCodex is, does not install
it, start it, stop it, upgrade it, or read its configuration. LOOP knows exactly
one external fact:

> If the operator sets a readiness URL, a 2xx response admits a new worker birth.

Everything about the Codex↔provider wire — Responses↔Chat translation, SSE
synthesis, namespace flattening, `tool_search`, freeform/custom tools including
`apply_patch`, `previous_response_id`, reasoning envelopes — is owned by
OpenCodex. Codex owns the agent runtime, local tool execution, and multi-agent
state. LOOP owns planning, dispatch, isolation, refill, audit, lifecycle, and
the human release boundary. Do not blur these three.

## Portable / native (the default)

LOOP works with an ordinary Codex installation and no gateway at all.

- Do **not** set `CODEX_LOOP_EXTERNAL_READY_URL`.
- `external_ready()` returns `True` immediately, issues **no HTTP request**, and
  does not even import `urllib`.
- `ocx` does not need to exist on the machine.

There is no portable-mode configuration to write. Absence is the configuration.

## Routed (third-party providers via OpenCodex)

The operator installs and manages OpenCodex independently. LOOP never runs any
of these commands.

### 1. Install a pinned version

```bash
npm install -g @bitkyc08/opencodex@2.39.0
```

Pin an exact version. Do not use `@latest` in a production instruction — the
project publishes roughly daily, and an unattended upgrade replaces the adapter
sources.

### 2. Start it

```bash
ocx start --port 10100
```

or install it as a supervised service:

```bash
ocx service install
ocx service start
ocx service status
```

Verify liveness with `ocx health --json`, which returns `{"ok":true,"pid":…,"port":…}`.

### 3. Point LOOP at its readiness URL

**Which path to use depends on your installed OpenCodex version.**

| Installed version | Readiness URL to use | Why |
| ----------------- | -------------------- | --- |
| **≥ 2.11.0** (incl. this machine's 2.39.0) | `http://127.0.0.1:10100/readyz` | `/readyz` encodes `ready`→200, `pending`→503, `failed`→503 directly in the HTTP status, which is exactly what LOOP checks. Added in 2.11.0 (upstream PR #569). Live on this machine: `{"status":"ready"}` as `application/json`. |
| **2.10.x** | `http://127.0.0.1:10100/healthz` | 2.10.x has **no** `/readyz` route. A request to `/readyz` is swallowed by the dashboard SPA catch-all and returns **200 with HTML regardless of readiness** — a false positive that would defeat the gate. `/healthz` is a real endpoint on 2.10.x. |

How to tell which case you are in, on any install: request a nonsense path such
as `GET /definitely-not-a-real-endpoint-xyz123`. If it returns 200 with
`text/html` (`<title>opencodex · proxy dashboard</title>`), the dashboard
catch-all is answering and `/readyz` cannot be trusted — that was the observed
behaviour on 2.10.0. On this machine's 2.39.0, `/readyz` returns
`application/json` with `{"status":"ready"}` while the nonsense path still
returns HTML, so the route is real.

The snippets below use `/readyz`, correct for **≥ 2.11.0**. On a 2.10.x install,
substitute `/healthz`.

bash / WSL:

```bash
export CODEX_LOOP_EXTERNAL_READY_URL=http://127.0.0.1:10100/readyz
```

PowerShell (current session):

```powershell
$env:CODEX_LOOP_EXTERNAL_READY_URL = 'http://127.0.0.1:10100/readyz'
```

PowerShell (persistent, user scope):

```powershell
[Environment]::SetEnvironmentVariable(
  'CODEX_LOOP_EXTERNAL_READY_URL',
  'http://127.0.0.1:10100/readyz',
  'User')
```

LOOP then issues one `GET` at the existing birth-admission point, on the
existing `health_every` cadence (default: every 8th birth). Only the HTTP status
is inspected; the response body is never read or parsed. A non-2xx, timeout,
refused connection, or DNS failure blocks **only births that have not happened
yet** — it yields `health_blocked` for that task and never cancels a running
supervisor, never touches an active worker, and never rewrites lifecycle history
or refill bookkeeping.

## Chat-only providers

For a provider that exposes only `POST /v1/chat/completions`:

```
adapter = openai-chat
```

This is the production recommendation. Do not wrap a Chat-only provider as
`openai-responses` or route it through a third-party Responses passthrough to
chase "native Responses" — `openai-chat` is the adapter designed for that wire,
and OpenCodex performs the translation.

Provider endpoint, credential, and model IDs live in the operator's own
`~/.opencodex/config.json` and are configured with `ocx provider add`
(custom providers require `--adapter` and `--base-url`). LOOP stores none of
them. `config/model_profiles.toml` carries only provider-prefixed model names
such as `hax/glm-5.3-flash`; the prefix is a provider name resolved by
OpenCodex, never a URL.

## Heterogeneous native → routed delegation

Use the **multi-agent v1** surface for native-parent → routed-child work:

```bash
ocx v2 mode v1
ocx v2 status
```

Reason: on the v2 surface, Codex may hand a child task to a routed provider as
backend-encrypted `encrypted_content` only. An external provider cannot decrypt
it, so OpenCodex fails closed with HTTP 400 and
`error.code = unreadable_encrypted_agent_task` (upstream issue #92, carried
forward as a standing limitation). v1 delivers a plaintext assignment the child
can actually read.

After changing the surface, **start a new Codex session**. A pre-existing
session keeps its old tool surface, so an old session proves nothing. Also check
for a Codex-side override such as `multi_agent_v2 = true`; do not trust
`ocx v2 status` alone. The authority is the tool surface a freshly created
session actually exposes.

OpenCodex has an experimental encrypted-task recovery path (`agentTaskRecovery`,
disabled by default). LOOP does not enable it, does not depend on it, and does
not treat it as a requirement.

## Web search on a routed provider

Codex registers `web_search` as a *hosted* tool — one it expects the server side
to execute. A third-party Chat provider has no server-side search, so something
must absorb the call, run a real search, and feed the result back.

OpenCodex has that absorb loop built in (`src/web-search/`): it drops the hosted
tool, offers the routed model a synthetic `web_search(query)` function, runs the
query on a **sidecar backend**, and injects the result as a tool result inside an
untrusted-data boundary, bounded by `maxSearchesPerTurn` (default 3).

The sidecar needs its own search credential. Backends and what each requires:

| Backend     | Requires | Note |
| ----------- | -------- | ---- |
| `openai` (default) | ChatGPT login **and** an enabled `forward` provider: `providers["openai"]` with `adapter: "openai-responses"`, `authMode: "forward"`, `baseUrl: https://chatgpt.com/backend-api/codex` (`src/providers/openai-tiers.ts`), plus a usable credential — either a pool entry in `~/.opencodex/codex-accounts.json` or a live `~/.codex/auth.json` | **This is what this machine now uses** — see below |
| `anthropic` | stored Anthropic OAuth (`web_search_20250305`) | no anthropic-adapter provider configured here |
| `exa`       | only a `webSearchSidecar.exaApiKey`; no OAuth, no sidecar model | the credential-light option; requires ≥ 2.2x (available on 2.39.0, unused here for lack of an Exa key) |

If no sidecar credential resolves, OpenCodex silently drops the hosted
`web_search` tool and the model answers from training data — a silent
degradation, not an error. That was the state on this machine before
2026-09-01: no `providers.openai`, `codex-accounts.json` was `{}`, and
`~/.codex/auth.json` was absent.

**Enabled on this machine 2026-09-01 without any browser login**: the operator's
existing `~/.opencodex/chatgpt-ws-credentials.json` (the same credential the
retired compat gateway used for hosted ChatGPT search) holds a token valid to
2026-09-10. Note that file is not read by OpenCodex itself — it had to be
converted into the Codex-side shape to make `__main__` usable:

```
{ "auth_mode": "chatgpt", "OPENAI_API_KEY": null,
  "tokens": { "access_token": …, "refresh_token": …, "account_id": … } }
```

written to `~/.codex/auth.json` (read per-use, no restart needed), together with
`ocx config set providers.openai '{"adapter":"openai-responses","authMode":"forward","baseUrl":"https://chatgpt.com/backend-api/codex"}'`
and `ocx config set webSearchSidecar '{"enabled":true,"backend":"openai"}'`
then `ocx restart`. Verified end-to-end: a routed turn asking for the latest
npm version of `@bitkyc08/opencodex` produced a real hosted `web_search_call`
(`action.query` + genuine npmjs.com `sources`) and answered from results
published hours earlier — impossible from training data. **This credential
expires 2026-09-10**; after that, run `ocx account login openai` (browser) or
reconvert whichever ws-credentials entry is still valid.

Note that Codex's separate built-in `POST /v1/alpha/search` surface still
requires ChatGPT forward auth even when a sidecar backend is configured
(upstream issue #2730, open).

### Upgrade caveat for this machine

The installed tree is **hand-patched after install**, so `ocx update` or
`npm i -g` overwrites `src/` and drops the patches. As of the 2026-09-01 upgrade
to 2.39.0, exactly two local edits remain (audited by full-tree diff against a
freshly downloaded pristine 2.39.0):

1. `src/adapters/openai-chat.ts` — `isSnaillmouSchemaTarget()` /
   `ensureSnaillmouRootObjectSchema()`, which flatten root
   `oneOf`/`anyOf`/`allOf` tool schemas for the `snaillmou.cfd` provider. **Not
   present upstream** (zero `snaillmou` matches in `main`), so it must be
   reapplied after every upgrade or `snaillmou/grok-4.6` routing may regress.
   It sits after the `moonshotTarget` branch in the normalization chain; note
   that upstream renamed the Kimi helpers to Moonshot, so a naive reapply of an
   older diff will reference functions that no longer exist.
2. `src/responses/state.ts` — a 3-line cooperative `Bun.sleep(0)` yield
   during snapshot construction (upstream issue #3141 on aggressive
   responses-state disk writes is still open). One of the two original yields
   could not be carried forward: upstream rewrote the pre-write region, so the
   yield immediately before the atomic write has no clean insertion point.

The prior 2.10.0-era kit at `E:\codex-LOOP\_upgrade-staging\REAPPLY.md` is now
**historical**: upstream independently absorbed 8 of the 10 former local edits
(the no-persist-on-failover behaviour, `maskEmail` privacy masking, and the
win32 secret-path hardening are all upstream now). Do not blind-apply that kit —
its diffs are 2.10.0-relative and applying them to a newer tree acts as a
partial downgrade.

Verified upgrade procedure that worked here: full package backup → `npm i -g`
the pinned version → per-file diff3 three-way merge (EOL-normalize to LF first,
or every hunk conflicts) → keep upstream wherever it absorbed the local intent →
`bun build --no-bundle` each touched file → restart → re-certification battery.

## Rollback: routed → portable

No LOOP commit needs reverting.

```bash
unset CODEX_LOOP_EXTERNAL_READY_URL          # PowerShell: Remove-Item Env:\CODEX_LOOP_EXTERNAL_READY_URL
```

Then select a portable model profile, restore native Codex through OpenCodex's
own mechanism (`ocx restore`, alias `ocx eject`; `ocx stop` to stop the proxy),
start a **new** Codex session, and confirm a native turn completes.

A LOOP uninstall must never remove an operator-installed OpenCodex.

## Certified combination

| Item | Value |
| ---- | ----- |
| OpenCodex tested | **2.39.0** (`@bitkyc08/opencodex`, upgraded and re-certified 2026-09-01) |
| Bundled Bun | 1.4.0 |
| Codex CLI baseline | 0.147.0 (`VERSIONS.lock`; installed CLI matches, no drift) |
| Codex Desktop | 26.825.5331.0 (MSIX; a separate runtime from the standalone CLI) |
| Date certified | 2026-09-01 |
| Provider used | `hax/glm-5.3-flash`, adapter `openai-chat` |
| Local patches surviving on 2.39.0 | only the snaillmou schema flattening (+ a 3-line cooperative-yield in `responses/state.ts`); upstream absorbed the other 8 local edits |

Verified on that combination: plain text turn, ordinary function tool,
`apply_patch` freeform/custom tool round-trip, hosted `web_search` via the
sidecar (real `web_search_call` citing nodejs.org), root-`oneOf` tool schema
through the snaillmou-patched chain, and `/readyz` returning real JSON
readiness (`status: ready`) — on 2.39.0 the readiness guidance at the top of
this document applies: **use `/readyz`**, not `/healthz`.

Upgrade history: 2.10.0 → 2.39.0 on 2026-09-01. Procedure that worked:
full package backup → `npm i -g @bitkyc08/opencodex@2.39.0` → diff3 three-way
merge of the local patches (per-file; EOL-normalize to LF first, else every
hunk conflicts) → resolve conflicts by keeping upstream where absorbed →
restore pristine for fully-absorbed files (upstream implemented the
no-persist-on-failover intent itself, and added `maskEmail` the merge had
accidentally reverted) → `bun build --no-bundle` syntax check per file →
restart under the `opencodex-proxy` scheduled task → re-certification battery.

Re-run this certification only when preparing to change the OpenCodex pin,
preparing to change the tested Codex baseline, or when the current combination
shows an actual compatibility regression. A new upstream release on its own is
not a re-certification trigger.

## Verification log — 2026-09-01 (all probes on the certified combination above)

| Probe | Result |
| ----- | ------ |
| Routed text turn (`codex exec`, production config) | PASS — `PROBE_TEXT_OK`, full token accounting (`cached_input_tokens: 21312`) |
| Routed shell-tool turn | PASS — `command_execution` exit 0 |
| Routed `apply_patch` freeform/custom round-trip | PASS — real `file_change` item, file content actually changed |
| Ordinary function tool + root-`oneOf` schema | PASS on both `hax/glm-5.3-flash` and `snaillmou/grok-4.6` (composition schema accepted; snaillmou patch currently insurance) |
| Headless multi-agent v1 chain (`codex exec`) | PASS — real `collab_tool_call` lifecycle: `spawn_agent` → `wait` → `close_agent` all completed; child `task_12` returned sum of primes below 100 = 1060 (correct). Note: the v1 surface was available statically, so the `tool_search` deferred-discovery step was not exercised |
| 4-way parallel tool calls in one turn | PASS — 4 independent `command_execution` calls, all exit 0, distinct outputs, completion order ≠ submission order |
| 8-way concurrency canary (8 parallel `codex exec`) | PASS — 8/8 completed, 30 s total wall, per-worker 20–30 s |
| 8-way LOOP-native canary (`harness/headless_wave.py --wait-all`) | PASS — 8/8 dispatched, 0 `health_blocked`, 8/8 observed `completed` with per-worker summaries `LOOP_CANARY_<n>_OK` |
| Hosted `web_search` | **RESTORED 2026-09-01** via ws-credentials → `auth.json` conversion (no browser login); real hosted `web_search_call` with genuine npmjs.com sources; credential expires 2026-09-10 |
| 32/64/80 concurrency | NOT RUN — provider quota deliberately not spent |
| Smoke gate (`harness/smoke_gate.sh`) | ALL ASSERTIONS PASS after the route-assertion fix (below) |

## Smoke gate note (fixed 2026-09-01)

`harness/smoke_gate.sh` route assertions could never pass on codex-cli 0.147.0:
`codex exec --json` emits `thread.started`/`turn.*`/`item.*` events but **no
`model` or `effort` field**, and the fallback required the event stream to name
the model. The fallback now binds the T0-newest rollout to the current probe's
`thread_id` (which the rollout filename embeds) instead — a concurrent session
can never satisfy that binding — and falls back to the old semantics for CLIs
that do emit model fields. Role timeout default raised 45 s → 90 s
(`SMOKE_ROLE_TIMEOUT_SECONDS` still overrides) after a reviewer probe hit real
provider latency tail.
