# ZLoop Vendor Contracts — M0 Baseline (2026-09-02)

Registry of every load-bearing third-party contract ZLoop depends on.
Companion machine-readable registry: `artifacts/capabilities/manifest.json`. Spec basis: `E:\zcode\zloop-spec\VOL-02-PLATFORM-CONTRACTS.md` (do not copy; that volume is authoritative).

**Rules (VOL-01 §5)**:

- Status values: `DOCUMENTED` / `OBSERVED` / `UNKNOWN` / `BLOCKED` (spec six-state also defines `EXPERIMENTAL` / `UNAVAILABLE` / `OBSERVED_DIFFERENT`).
- A production hard dependency must be `DOCUMENTED`, or `OBSERVED` with a tested fallback (I20).
- Vendor version change ⇒ re-run the mapped probe. "Worked last week" is not a contract.
- Source convention: `local probe 2026-09-02` = measured on this machine (evidence: `artifacts/capabilities/phase-1.json`); URLs = vendor docs verified 2026-09-02.
- All probe IDs (`P-xx`) are defined in `E:\zcode\zloop-spec\VOL-20-M0-PROBES.md`.

---

## 1. ZCode client (v3.10.2, build `35824adf`, `ZCODE_APP_VERSION=3.10.2`)

### 1.1 Hook protocol

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| Hook events: exactly **7** — `SessionStart, UserPromptSubmit, PreToolUse, PermissionRequest, PostToolUse, PostToolUseFailure, Stop`. No `SessionEnd`; `Notification` / `SubagentStop` / `PreCompact` unsupported. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | Register only the 7 documented events. |
| Hook input: stdin **single-line JSON** (camelCase + snake_case dual naming). Common fields: `session_id, transcript_path, cwd, permission_mode, hook_event_name`. Per-event: SessionStart +`source` (+`agent_type/model`); UserPromptSubmit +`prompt`; PreToolUse +`tool_name, tool_input, tool_use_id`; PostToolUse + fully structured `tool_response` (incl. name/input/call ID); PostToolUseFailure +`error, is_interrupt`; Stop +`stop_hook_active, last_assistant_message`. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | — |
| SessionStart `source` values: `startup / resume / clear / compact` (**resume exists**; Gen-8 draft listed only 3). | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | — |
| `transcript_path` is a **temporary file**; its directory is cleaned after the hook runs → persist needed content immediately (`ZCODE_PLUGIN_DATA`). | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | H0 capture must not rely on re-reading transcript_path later. |
| Matcher: default/empty/`*` matches all; pure `[A-Za-z0-9_|]` = exact list (e.g. `Write\|Edit`); other chars = JS regex (invalid → skipped + diagnostic). Tool events match tool name (aliases `Task↔Agent`, `Write/Edit←ApplyPatch`); SessionStart matches source (e.g. `"startup\|clear\|compact"`); **UserPromptSubmit/Stop are not matcher-filtered**. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | — |
| Output shapes: stdout JSON `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"…"}}` (top-level `additionalContext` also accepted). PreToolUse decision: `permissionDecision: allow/deny/ask` + `permissionDecisionReason` + `updatedInput` (full replacement, re-validated against tool schema). Stop continuation: `{"decision":"block","reason":"…"}`. Exit codes: 0 = success, 2 = block/continue, other = recoverable failure (turn does not crash). | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | Emit only documented output fields. |
| Stop-hook continuation is force-terminated after **3 consecutive** continuations. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | Never use Stop hook for unbounded continuation (hard ban, VOL-01 §3.6). |
| **Discrepancy 1 — output schema strictness**: web docs say unknown fields are ignored (failure only on known-field type error / event-name mismatch); local `zcode-guide` plugin doc (0.1.4) says extra keys fail validation. | UNKNOWN (two sources conflict) | both sources above + local zcode-guide plugin 0.1.4 | P-HK2 | Conservative branch: emit only documented fields (D-3). |
| **Discrepancy 2 — `async:true`**: web docs = fire-and-forget (background stdout cannot block / change input / inject context); local plugin doc = no runtime effect. | UNKNOWN (two sources conflict) | both sources above | P-HK2 | v1 implements everything as synchronous micro-processes; no async (D-3). |
| **Discrepancy 3 — `${CLAUDE_SESSION_ID}`**: listed only in local zcode-guide plugin doc, absent from web docs. | UNKNOWN (two sources conflict) | both sources above | P-HK2 | Always read `session_id` from the stdin JSON; never rely on template variables (D-3). |
| Execution: hooks run **inline, mostly serial**. `process` type = argv, no shell, **synchronous only**; `command` type = via shell. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | Use `process` type for the zloop hook. |
| Timeout resolution chain: `timeoutMs` → `timeout`×1000 → configured `timeoutMs` → default 60000 ms. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | — |
| Config scope: user-level `~/.zcode/cli/config.json` `hooks` (requires `enabled:true`, otherwise disabled by default) or plugin `hooks/hooks.json`. **Workspace-level `.zcode/config.json` hooks are ignored entirely**. Defaults: `timeoutMs=60000`, **`maxOutputBytes=32768` (hook stdout cap)**. Config is **snapshotted per session** — changes require a new session. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks | P-HK2 | Install at user scope; document that hook changes take effect only in a NEW ZCode session. |

### 1.2 `/goal` mode

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| `/goal <objective>` works in rounds; goal achievement auto-checked at each round end; verifier accepts only **real evidence** (changed files, command output, test results — plans and confident-sounding answers do not count). | DOCUMENTED | ZCode product docs | P-GC1 | — |
| Goal state is **persisted by the system** and survives session restart. Subcommands: `pause / resume / clear / replace`; `pause` is lossless (rounds/tool history/artifacts retained); cannot set a goal while a task is running; stopping a running task auto-pauses the goal. | DOCUMENTED | ZCode product docs | P-GC1 | — |
| Verifier todo gate: any incomplete todo ⇒ not judged complete; each round title comes from the previous round's verification next-step. | DOCUMENTED | ZCode product docs | P-GC1 | — |
| **NOT guaranteed**: returning to the same round after an external cold supervisor blocks for tens of minutes — no such promise exists. | DOCUMENTED | ZCode product docs | P-GC1 | G-COG must not bet on same-round return (VOL-03 §3); reference implementation = background + notification (D-2). |

### 1.3 Context management (compaction)

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| Auto compaction exists with **no model-side switch**; trigger ≈ window − 21K output reserve − ~13K buffer (128K → ~94K; 1M → ~966K). Manual `/compact` exists. | DOCUMENTED + OBSERVED (root tool-face self-check 2026-09-02) | local probe 2026-09-02 | — | — |
| **No model-side signals**: no remaining-token count, no `PreCompact`, no `new_context` notification. | OBSERVED (root tool-face self-check 2026-09-02) | local probe 2026-09-02 | — | H1 piggyback design (VOL-06) stands; never claim native TokenBudget-style rollover. |

### 1.4 Subagents and tool surface

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| Agent tool: foreground parallel (officially only "several" — no numeric cap documented); background (main task does not wait, current turn may end first); backgrounded Explore subagents are **read-only**; isolated context; results return to the main conversation by themselves. | DOCUMENTED | ZCode product docs | P-NAT1 | Do not use native helpers as durable first-class workers (I19). |
| **No nesting**: subagents cannot spawn subagents (disabled at the native layer). | DOCUMENTED | ZCode product docs | P-NAT1 | — |
| Subagent definition frontmatter: `maxTurns / tools / model / …`. | DOCUMENTED | ZCode product docs | P-NAT1 | — |
| Concurrency env knobs present on this machine: `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION=64`, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=20`, `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=3`, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. | OBSERVED (local probe 2026-09-02) | local probe 2026-09-02 (phase-1.json) | P-NAT1 | Knob effects untested; measure before relying. |
| Browser: root model tool, **main-agent-only**; independent login session (does not share the daily Chrome); **Windows: no Chrome login-state import**. | DOCUMENTED | ZCode product docs | P-C2C1 | C2C transport per VOL-16; C2C outage must not block NORMAL mechanical path (I17). |
| computer-use MCP is a root tool-face item; subagent coverage can only be annotated `native_child_result_only / native_child_surface_observed`. | DOCUMENTED | local probe 2026-09-02 | P-HK3 | — |

### 1.5 Agent-side runtime (root execution environment)

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| Foreground Bash single-call hard cap **600,000 ms (10 min)**; default 120,000 ms. | OBSERVED (2026-09-02 session) | local probe 2026-09-02 | P-PLAT1 | Waves > 10 min: `wave start` + end turn + wait for notification; `await --timeout` capped at 540 s (D-2). |
| Background tasks (`run_in_background`) survive across turns; on exit they **re-invoke the root agent** (measured 2026-09-02: `sleep 90` → task-notification arrived as an independent turn, no user input needed). | OBSERVED (local probe 2026-09-02) | local probe 2026-09-02 | P-PLAT1 | G-COG wake primitive = background + notification (D-2). |
| Notification delivery at **turn boundary**: arrives after the in-flight tool batch completes, as an independent turn; does not interrupt an ongoing turn. | OBSERVED (local probe 2026-09-02) | local probe 2026-09-02 | P-PLAT1 | Serial synthesis absorbs parallel results at round boundary (matches architecture); M0 to time precisely. |
| Result retrieval via TaskOutput tool (`block` / `timeout`). | DOCUMENTED + OBSERVED | local probe 2026-09-02 | P-PLAT1 | — |
| One assistant message may issue multiple tool calls in parallel. | DOCUMENTED | https://zcode.z.ai/en/docs/hooks (parallel calls) | P-HK1 | Concurrent PostToolUse race must be lock-tested (P-HK1). |
| Environment: **no session-id env var exists** (full env enumeration tested); `ZCODE_APP_VERSION / BASE_URL / BUILD_COMMIT_ID / PROCESS_LABEL` present; sensitive names present (`ALIBABA_TOKEN_PLAN_API_KEY`, `ZAI_OAUTH_CLIENT_ID`, `ZAI_BUSINESS_BASE_URL`). | OBSERVED (local probe 2026-09-02) | local probe 2026-09-02 | — | Worker/research child processes must use an env allowlist (P1-8, I13/I36). |

---

## 2. Codex / OpenAI (Python SDK `openai-codex`; local CLI codex-cli 0.147.0)

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| Python SDK: PyPI **`openai-codex`** (0.147.0, 2026-08-18; module `openai_codex`; Python ≥ 3.10; ships pinned `openai-codex-cli-bin`). | DOCUMENTED | PyPI: openai-codex | P-CDX1 | Pin 0.147.0 in v1 (local CLI matches). |
| Clients: `Codex(config)` / `AsyncCodex(config)`; **by default reuses the existing CLI login**; explicit flows `login_api_key / login_chatgpt / login_chatgpt_device_code`. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | — |
| `thread_start(*, approval_mode=auto_review, base_instructions, config, cwd, developer_instructions, ephemeral, model, model_provider, personality, sandbox) → Thread`. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | — |
| `thread_resume(thread_id, *…)` — **no `ephemeral`** parameter. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | — |
| `Thread.read(*, include_turns=False)`; `thread_list(*, archived, cursor, cwd, limit, …)`. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | — |
| `turn(input, *, cwd, sandbox, model, effort, …) → TurnHandle` with `steer(input)` / `interrupt()` / `stream() → Iterator[Notification]` / `run() → TurnResult`. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | — |
| `Sandbox.read_only / .workspace_write / .full_access`. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | — |
| `TurnResult{status, error, final_response: str\|None, items, usage}` — **`final_response` may be `None`** (legal termination when a turn has no final answer); completion is judged by the `turn/completed` notification. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | Judge completion on `turn/completed`, never on `final_response` presence. |
| Single client supports multiple concurrently active turns; no documented limit. | DOCUMENTED | PyPI: openai-codex | P-CDX1 | Bound concurrency by our own scheduler, not by an assumed SDK cap. |
| Nested agents: config `[agents].enabled` (**default true**) can be turned off; `agents.max_concurrent_threads_per_session`; `features.multi_agent` gates `spawn_agent / send_input / resume_agent / wait_agent / close_agent`. | DOCUMENTED | codex config docs | P-CDX2 | Strict workers: set `[agents].enabled=false` and enumerate the actual tool catalog to confirm no spawn_agent family (P-CDX2 hard gate). |
| app-server: `codex app-server` = stdio JSON-RPC; `generate-json-schema --out DIR` (**DIR**, not a file); WS transport experimental/unsupported. | DOCUMENTED | codex CLI docs | P-CDX1 | Use stdio transport only. |
| `workspace_write` sandbox: writes limited to cwd + writable_roots; **`network_access` defaults to `false`** (codex-rs source). | DOCUMENTED | codex-rs source | P-CDX2 | Still physically canary-test public + loopback/private egress (P-CDX2); prompt text does not satisfy I29/I36. |
| TokenBudget: codex-rs `session/token_budget.rs`, `compact_token_budget.rs`; PR #29743 (fresh window not summarized); PR #39827 history/notes tools (2026-08-21) — "enters context manager" wording is loose (actually context fragments). | DOCUMENTED | codex-rs repo / PRs | — | Do not claim ZCode-side parity with Codex TokenBudget. |
| Known public issues: **#37047** (thread stale-active → resume hangs forever); **#34220** (app-server restart loses Completed child state → wait timeout); **#37856** (multiple windows contending for the same active thread). | DOCUMENTED (issues open) | GitHub openai/codex issues #37047 / #34220 / #37856 | P-CDX3 | Provider thread status is not S authority; stale-active/resume ambiguity must stay bounded (I44). |
| Symphony spec: single authoritative orchestrator state; one independent workspace per issue; validate `cwd == workspace_path` before launch. | DOCUMENTED | codex Symphony spec | P-CDX1 | — |
| **Local auth currently BROKEN**: `codex login status` → `Error checking login status: invalid ID token format at line 1 column 74` (rc=1). Needs user re-login. All live Codex/Luna probes are **BLOCKED-manual** until then. | BLOCKED | local probe 2026-09-02 (artifacts/capabilities/phase-1.json) | P-CDX1 / P-CDX2 / P-CDX3 / P-LUNA1 | User runs `codex login`, then re-run the probes. Note: `~/.codex` contains 5 `auth.json` variants — forbidden as credential sources (VOL-02 §3.5). |

---

## 3. Kimi Code (local CLI 0.28.1)

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| Dual repositories: `MoonshotAI/kimi-code` (TS, npm `@moonshot-ai/kimi-code`, latest 0.40.0) vs old `kimi-cli` (Python 1.50.0, winding down). | DOCUMENTED | MoonshotAI/kimi-code repo; npm | P-KIM1 | Track the TS/npm line only. |
| `kimi web`: local REST + WS, default `127.0.0.1:58627`, loopback only, port-conflict retry ≤ 100. | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | — |
| Auth: `Authorization: Bearer <token>` (WS: same header or subprotocol `kimi-code.bearer.<token>`); 401 = envelope `40101`; brute-force guard: 10 failures within 60 s → ban `42901`. | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | — |
| Live `/openapi.json` and `/asyncapi.json` are authoritative (auth required). | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | Hash snapshots at probe time. |
| `POST /api/v1/sessions`: `workspace_id` XOR `metadata.cwd` (if both given they must match); `agent_config` accepted but **not effective** (model set via profile endpoint). | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | — |
| Terminal states: turn `last_turn_reason ∈ completed/cancelled/failed`; transcript agent turn `state ∈ queued/running/completed/failed/cancelled`. | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | Explicit terminal completion required (I27). |
| WS `/api/v1/ws`: `server_hello` first; `subscribe` / `subscribe_v2`; event families `turn.started/ended`, `tool.call.*`, `assistant.delta`, `event.approval.*`; > 1000 events behind → `resync_required` → snapshot endpoint. | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | Handle resync_required via snapshot endpoint. |
| Abort: `POST …/sessions/{id}:abort`, `…/prompts/{id}:abort`, `…/tasks/{id}:cancel`. | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | — |
| Official status of `kimi web`: **experimental**. | DOCUMENTED | MoonshotAI/kimi-code repo | — | Research lane only; not a production-critical path. |
| Headless: `kimi -p "…" --output-format stream-json` → stdout = JSONL (one JSON per line; tool-call sequence = Assistant with `tool_calls` → Tool message → subsequent Assistant); thinking is never written to JSONL; **no documented explicit final marker or per-line `type` enum** ⇒ K2 completion criterion = last line is a non-tool-call Assistant message, otherwise `INCOMPLETE_OUTPUT` (I27). | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | — |
| Built-in tools `WebSearch` / `FetchURL` are backed by `[services.moonshot_search]` / `[services.moonshot_fetch]` **TOML config sections** (erratum: they are services, not tools); managed OAuth login auto-configures search/fetch. | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | — |
| Issue **#1897** (0.27.0): long multi-tool stream-json turn under stdout backpressure drops the final assistant message + resume hint while `wire.jsonl` is actually complete ⇒ `exit 0 ≠ success`, `tool event ≠ success`. Fix PR #2170 open ⇒ **treat as unfixed on local 0.28.1 until tested**. | DOCUMENTED (issue) / UNKNOWN (local repro pending) | GitHub MoonshotAI/kimi-code issue #1897 | P-KIM1 | K1/K2 lanes require explicit terminal completion; INCOMPLETE_OUTPUT path must be fixture-tested. |
| Local version 0.28.1 vs latest 0.40.0. | OBSERVED (local probe 2026-09-02) | local probe 2026-09-02 (phase-1.json) | P-KIM1 | Pin + record; re-run probes on upgrade. |
| Models: K3 flagship (2026-07-16), K2.6, K2.7 Code, `kimi-for-coding(+highspeed)`. | DOCUMENTED | MoonshotAI/kimi-code repo | P-KIM1 | — |

---

## 4. SQLite (local runtime 3.50.4 via Python 3.14.3 stdlib)

| Capability | Status | Source | Re-confirm probe | Fallback |
|---|---|---|---|---|
| WAL-reset bug: discovered 2026-03-03 (wal.html §11); "likely present" in 3.7.0–3.51.2; fixed in **3.51.3** (2026-03-13); backports **3.50.7 / 3.44.6**; trigger = multiple connections + simultaneous write/checkpoint; official severity "not an emergency" (rare but non-zero). 3.52.0 withdrawn; 3.53.0 re-released with fix; latest **3.53.4** (2026-07-24). | DOCUMENTED | sqlite.org/wal.html §11 | P-SQL1 | Version gate in `db.py` (D-1): fixed version → WAL+FULL; otherwise DELETE+EXTRA. |
| Local runtime is **3.50.4** — inside the affected range and below the 3.50.7 backport ⇒ I22 activated. | OBSERVED (local probe 2026-09-02) | local probe 2026-09-02 (phase-1.json) | P-SQL1 | Local v1 default path: DELETE + EXTRA journaling (D-1). |
| WAL requires all processes on the same host (shared memory); "WAL does not work over a network filesystem". | DOCUMENTED | sqlite.org/wal.html | P-SQL2 | S authority is never placed on a network FS (I22). |
| `synchronous`: WAL + FULL syncs the WAL at every commit (committed transactions survive power loss); NORMAL may roll back recent transactions after power loss but never corrupts. | DOCUMENTED | sqlite.org pragma.html | P-SQL1 | — |
| Upgrade paths under evaluation: `pysqlite3` binary wheel (cp314/Windows availability unknown), bundling the sqlite.org 3.53.4 DLL, or keeping DELETE+EXTRA. | UNKNOWN | — | P-SQL1 | Decide via P-SQL1 (M0 exit criterion); no production WAL before the version gate passes (I22). |

---

## 5. Version matrix (all measured 2026-09-02)

| Component | Local | Latest (checked 2026-09-02) | Note |
|---|---|---|---|
| OS | Windows 10.0.26200 x64 (MINGW64 / Git Bash 3.6.9) | — | Windows-first. |
| ZCode | 3.10.2 (build `35824adf`) | 3.10.2 | Current. |
| Python | 3.14.3 | — | stdlib sqlite 3.50.4. |
| SQLite runtime | 3.50.4 | 3.53.4 (2026-07-24) | Inside WAL-reset affected range (I22). |
| codex-cli | 0.147.0 | rust-v0.152.1 (2026-09-01) | 5 versions behind; pin + record (P2-16). |
| kimi | 0.28.1 | 0.40.0 (npm `@moonshot-ai/kimi-code`) | #1897 assumed unfixed until tested. |
| git | 2.55.0.windows.3 | — | — |
| zloop | not installed | — | Clean start. |
| `~/.zcode/cli/config.json` | absent at baseline | — | No user-level ZCode hook registration existed before ZLoop. |
