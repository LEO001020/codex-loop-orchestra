# OpenCodex 2.10.0 trae01/trae02/trae04 provider add (read-only)

Packet: 核对OpenCodex provider接入
Scope: confirm how to add custom providers `trae01` / `trae02` / `trae04` against local opencodex 2.10.0.
Constraint honored: no OpenCodex config mutation (`ocx provider add/edit/test/remove/set-default` was not executed against live names).

## Machine facts

- CLI: `ocx` -> `C:\Users\hzq00\AppData\Roaming\npm\ocx.ps1`
- Package: `@bitkyc08/opencodex` 2.10.0 at `C:\Users\hzq00\AppData\Roaming\npm\node_modules\@bitkyc08\opencodex`
- `ocx --version` / `ocx version`: `opencodex 2.10.0`
- Config path: `C:\Users\hzq00\.opencodex\config.json` (`ocx status`; `getConfigPath()` = `join(homedir(), ".opencodex", "config.json")` unless `OPENCODEX_HOME` is set)
- Proxy: listening on `127.0.0.1:10100` (PID 64908, `bun.exe`, state Listen). `ocx health`: `Proxy healthy (PID 64908, port 10100)`.
- Relay listeners already up (not mutated by this packet): `127.0.0.1:18001` PID 56768, `127.0.0.1:18002` PID 23548, `127.0.0.1:18004` PID 25768.

## Exact add/test commands

`trae01`/`trae02`/`trae04` are **not** in the 2.10.0 registry (`src/providers/registry.ts` has no `trae01`). Custom add therefore requires `--adapter` and `--base-url`. Names pass `PROVIDER_NAME_PATTERN`. None of these names currently exist in `config.json`, so `--force` is unnecessary and must be omitted to avoid overwrite behavior. `--set-default` must be omitted so `openai` stays default.

Do **not** put `${VAR}` in `--base-url`. `provider.baseUrl` is used literally (`createResponsesPassthroughAdapter` concatenates `provider.baseUrl`; `resolveEnvValue()` is not applied to `baseUrl` anywhere in `src/`). Config even warns on unresolved `{...}` placeholders. Env interpolation exists only for `apiKey` / `proxy` via `resolveEnvValue()`.

```text
ocx provider add trae01 --adapter openai-responses --base-url http://127.0.0.1:18001/v1 --api-key <RELAY_API_KEY> --default-model <TRAE_POOL_MODEL> --allow-private-network
ocx provider add trae02 --adapter openai-responses --base-url http://127.0.0.1:18002/v1 --api-key <RELAY_API_KEY> --default-model <TRAE_POOL_MODEL> --allow-private-network
ocx provider add trae04 --adapter openai-responses --base-url http://127.0.0.1:18004/v1 --api-key <RELAY_API_KEY> --default-model <TRAE_POOL_MODEL> --allow-private-network
ocx provider test trae01
ocx provider test trae02
ocx provider test trae04
```

Optional after add (not required by this packet; does not overwrite other providers): `ocx sync`.

`--allow-private-network` is required: `127.0.0.1` is classified as loopback, and `providerDestinationConfigError()` rejects it unless `allowPrivateNetwork:true` (or a registry local-provider default, which these names do not have). Without the flag, `validateAndSave()` / `saveConfig()` fails closed.

## Flag confirmation (2.10.0 CLI)

`ocx provider add` with no name prints:

```text
Usage: ocx provider add <name> [--adapter <adapter>] [--base-url <url>] [--api-key <key>] [--api-key-transport <x-api-key|bearer>] [--default-model <model>] [--allow-private-network] [--set-default] [--force] [--json] [--sync]
```

Source: `src/cli/provider.ts` `ADD_USAGE` and `handleAdd()`.

`ocx provider test` with no name prints `ocx provider test <name> [--json]`. Source: `src/cli/provider-runtime.ts` `testProvider()` POSTs `/api/providers/test?name=...` on the live proxy. For `openai-responses` this hits `{baseUrl}/models` with `Authorization: Bearer <apiKey>` (`src/oauth/index.ts` `buildModelsRequest`).

`--force` and `--set-default` exist but must stay omitted:

- `--force` overwrites an existing same-name provider (`handleAdd`: `if (hasOwnProvider(...) && !force) error`).
- `--set-default` rewrites `config.defaultProvider` (currently `openai`).

`--sync` is optional; omitting it is safe. After add, CLI prints `Apply to Codex: ocx sync`.

Adapter `openai-responses` is a first-class custom adapter (`src/server/adapter-resolve.ts` `case "openai-responses": createResponsesPassthroughAdapter`). For non-forward auth, request URL becomes `{baseUrl minus trailing /v1}/v1/responses`. Passing `--base-url http://127.0.0.1:1800N/v1` is therefore the intended OpenAI-compatible form.

## Existing providers (names only; do not overwrite)

Configured (`ocx provider list` / `ocx provider list --json`):

- `openai` (default, registry, adapter=openai-responses)
- `weiwu` [custom]
- `weiwu-k3` [custom]
- `snaillmou` [custom]

Protected names from the packet vs live state:

- `openai`: configured, default. Must not be overwritten; omit `--force` / `--set-default`.
- `weiwu`, `weiwu-k3`, `snaillmou`: configured custom. Adding `trae0N` does not touch them.
- `cursor`: **not configured**. Present in registry as oauth (`id: "cursor"`). `ocx status` shows `cursor ✓ logged in`. Do **not** run `ocx provider add cursor`.
- `alibaba-token-plan`: **not configured**. Present in registry as key provider. Do **not** run `ocx provider add alibaba-token-plan` (registry add would seed Alibaba Beijing, not a trae relay).

`trae01`/`trae02`/`trae04` are absent from both configured providers and the registry.

## Interpolation / private-network evidence

- `src/config.ts` `resolveEnvValue()` only expands a **whole** value matching `^\$\{(\w+)\}$` or `$NAME`. Call sites are `apiKey` and `config.proxy`, not `baseUrl`.
- Repo-wide `*.ts` search: 0 lines containing both `resolveEnvValue` and `baseUrl`.
- `src/adapters/openai-responses.ts` uses `provider.baseUrl` raw.
- `src/config.ts` `configPlaceholderWarnings()` warns if `baseUrl` contains `{...}`.
- `src/lib/destination-policy.ts`: `127.0.0.1` is loopback; requires `allowPrivateNetwork:true`.
- `src/cli/provider.ts`: `--allow-private-network` sets `provConfig.allowPrivateNetwork = true`.

## Commands intentionally not run

No `ocx provider add/edit/remove/set-default`, no `--force`, no writes to `C:\Users\hzq00\.opencodex\config.json`. Config mtime after this packet: `1788021250.7305684` (pre-existing).

## Uncertainty

- `<RELAY_API_KEY>` and `<TRAE_POOL_MODEL>` were not resolved here (placeholders as required).
- `ocx provider test` was not executed against `trae0N` because those providers do not exist yet and the packet forbids config mutation. After add, test talks to the live proxy on :10100 and then to `http://127.0.0.1:1800N/v1/models`.
- Whether the WorkBuddy processes on 18001/18002/18004 actually speak OpenAI `/models` + `/v1/responses` was not probed (would be a live upstream test, not a CLI-flag question).
