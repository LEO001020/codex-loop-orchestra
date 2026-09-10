# trae-relay-cluster — local deployment notes

Deployment directory for 10 isolated Trae2api-cn relays.
**Deliberately kept OUTSIDE the `codex-loop-orchestra` source tree.**

- Upstream: `autumnsentiment/Trae2api-cn` @ `b658d9c` (unmodified)
- Cluster root: `E:\trae-relay-cluster`
- LOOP repo (untouched): `E:\codex-LOOP\codex-loop-s-f2`

## Current run state (2026-08-29)

Live test slice (2026-08-30): **account01 / account02 / account04 only**.

```
account01 http://127.0.0.1:18001   fxip c525.fxip.cc:9345   PASS
account02 http://127.0.0.1:18002   fxip c562.fxip.cc:9345   PASS
account04 http://127.0.0.1:18004   fxip c1109.fxip.cc:9345   PASS
account03/05 process-up, fxip HTTPS 502, keep fail-closed
account06-10 stopped
OAuth helper http://127.0.0.1:18765  (account01)
```

Control:

```
python scripts/cluster_ctl.py status
python scripts/cluster_ctl.py restart --only 1
python scripts/cluster_ctl.py oauth-help
```

Do **not** run bare `start` / `stop` (those walk 01-10). Do **not** start 06-10 without a proxy. `--allow-missing-proxy` is scaffolding only.

Docker path is prepared (`compose.yml`) but Docker is not installed. When Docker exists:

```
docker build -t trae2api-cn:b658d9c upstream/Trae2api-cn
docker compose --env-file .cluster.env up -d
```

## Source audit

Audit performed against the locked SHA below. No upstream source has been
modified (`git status --short` in `upstream/Trae2api-cn` is clean).

```
upstream commit : b658d9c74a7bfb20be6a40a0e145b5642656f80f
upstream short  : b658d9c
```

| # | Claim under test | Verdict | Evidence |
|---|---|---|---|
| 1a | `GET /healthz` exists | **YES** | `src/main.py:4722` |
| 1b | `GET /v1/models` exists | **YES** | `src/main.py:4774` |
| 1c | `POST /v1/responses` exists | **YES** | `src/main.py:4799` |
| 1d | `GET /web/login` exists | **YES** | `src/main.py:4811` |
| 1e | `GET /api/accounts` exists | **YES** | `src/main.py:5620` |
| 2 | `web-login` writes credentials to `.env` / account data file | **YES** | `add_account()` (`src/auth.py:949`) calls `_save_accounts()` (`:764`) **and** `_save_env_snapshot()` (`:658`). `ENV_PATH = APP_DIR / ".env"` (`src/auth.py:29`); `ACCOUNTS_PATH = APP_DIR / "data" / "accounts.json"` (`src/auth.py:30`). |
| 3 | `previous_response_id` depends on in-process cache | **YES** | `_RESPONSE_SESSIONS` in `src/responses_api.py` is an `OrderedDict` + TTL 3600s / max 1024, no disk. Miss: `previous_response_id was not found or has expired`. |
| 4 | Dockerfile runs as UID/GID 999 | **YES** | `groupadd --gid 999 relay` / `useradd --uid 999`. |
| 5 | `UPSTREAM_MODE` default | **DEFAULT IS `remote`, NOT `raw`** | `src/main.py:82`. Explicit `UPSTREAM_MODE=raw` is mandatory. |
| 6 | raw/model client uses httpx default env-proxy behaviour | **YES** | zero `trust_env=False`; live probe: unreachable proxy -> ConnectError, no direct fallback. |
| 7 | token refresh / OAuth calls also use normal httpx clients | **YES** | `src/auth.py` plain `httpx.AsyncClient`. |
| 8 | Device-fingerprint auto-rotation | **NOT PRESENT AS A ROTATOR** | stable JWT-derived device_id; untouched. |

Additional facts:

- `TRAE_AUTH_SOURCE=web-login` starts with no token (live: 10 processes initialized `auth source=web-login`).
- Public (no relay key): `/healthz`, `/v1/models`, `/web/login`, `/api/accounts`, `/api/web-auth`. Only `/v1/responses` requires Bearer when `RELAY_API_KEYS` is set. Loopback-only is therefore mandatory.
- `web_login.py --port` default **8765** collides with LOOP monitor. Use `--port 18765`. Sequential OAuth only.
- `GET /v1/models` is a hybrid static catalog (62 ids observed) plus optional live merge. COMMON_MODELS must be proven with real `/v1/responses`.
- requirements.txt has no SOCKS extra.

## Native APP_DIR isolation (verified)

`APP_DIR = Path(__file__).resolve().parent.parent` has **no env override**. Windows junctions are resolved through, so ten processes on one checkout would share one `.env`. File hardlinks do **not** change `resolve()`, so each `instances/accountNN/src/auth.py` reports `APP_DIR=instances/accountNN`. Live check:

```
APP_DIR E:\trae-relay-cluster\instances\account01
ENV_PATH ...\instances\account01\.env
ACCOUNTS_PATH ...\instances\account01\data\accounts.json
```

Isolation test: POST `/api/web-auth` on account01 did not appear in account02.

## OAuth (waiting on you)

1. Fill `.cluster.env` from `cluster.env.example` (10 HTTP/HTTPS proxies + RELAY_API_KEY).
2. Restart without `--allow-missing-proxy` after proxies exist: `python scripts/cluster_ctl.py stop` then `start`.
3. Run `python scripts/verify-egress.py` (hard gate).
4. One helper: `python upstream/Trae2api-cn/web_login.py --relay http://127.0.0.1:18001 --port 18765`
5. Browser-login account01, then repeat `--relay` 18002..18010. Do not start 10 helpers. Do not use port 8765.

## Hard invariants

1. One Trae account ⇄ one relay instance ⇄ one fixed proxy.
2. Per-instance isolated `.env` and `data/accounts.json`.
3. Loopback-only bind (`127.0.0.1`).
4. No stateless load balancer — `previous_response_id` is in-process.
5. Proxy failure must never silently fall back to the host public IP.

See `DEPLOYMENT_REPORT.md` for PASS/FAIL gates.
