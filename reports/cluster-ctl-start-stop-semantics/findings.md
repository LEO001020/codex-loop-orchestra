# cluster_ctl 启停语义 (read-only)

Packet: 核对cluster_ctl启停语义
Date: 2026-08-30
Mode: read-only; no start/stop/restart was executed in this check.
Primary source: `E:\trae-relay-cluster\scripts\cluster_ctl.py`

## Verdict

There is **no per-account CLI**. `cluster_ctl.py start` / `stop` always walk accounts 01-10. After OAuth, restart 01 then 02 then 04 only via `stop_one` + `start_one(..., allow_missing_proxy=False)`. Do **not** run `start`, `stop`, `start --allow-missing-proxy`, or `restart_fxip_01_05.py` (the last one starts 03/05 and stops 06-10).

`--allow-missing-proxy` still exists on `start` only. It must not be used for 06-10: `.cluster.env` has no `PROXY_06`..`PROXY_10`, so a missing-proxy start would launch those processes without `HTTP_PROXY` (direct-egress risk). 03/05 have proxies set, so they *would* start; do not start them because those proxies currently 502 at request time.

## CLI surface (exact)

Observed `python scripts/cluster_ctl.py -h`:

```
{materialize,compose,start,stop,status,verify-isolation,verify-write,verify-failclosed,oauth-help}
```

`start -h`:

```
--allow-missing-proxy
```

No `--account`, no `restart` subcommand. Parser: `cluster_ctl.py:506-518`.

| CLI | Scope | Flags | Safe after OAuth for 01/02/04 only? |
|---|---|---|---|
| `start` | materialize + compose + `start_one` for `ACCOUNTS=1..10` then healthz | `--allow-missing-proxy` | **NO** — starts 03/05/06-10 |
| `stop` | `stop_one` for 1..10 | none | **NO** — kills 01-05 too |
| `status` | pid + `/healthz` for 1..10 | none | yes (read-only) |
| `oauth-help` | prints helper command | none | yes (print only) |
| `verify-failclosed` | httpx against `http://127.0.0.1:1` | none | yes (does not start relays) |

Library API used by helper scripts:

- `stop_one(i)` `cluster_ctl.py:347-361`
- `start_one(i, cluster, allow_missing_proxy)` `cluster_ctl.py:301-329`
- `wait_health(i, timeout=25.0)` `cluster_ctl.py:332-344`
- `child_env(i, cluster, allow_missing_proxy)` `cluster_ctl.py:243-273`

`start_one` if pid alive: prints `already running` and **does not re-inject env**. A restart that must pick up `.cluster.env` **must stop first**.

## Exact restart commands (account01, then 02, then 04)

Use venv Python. `allow_missing_proxy=False` is a function argument, not a CLI flag. Do not pass `--allow-missing-proxy`.

Account 01:

```
E:\trae-relay-cluster\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, r'E:\trae-relay-cluster\scripts'); import cluster_ctl as c; cluster=c.load_cluster_env(); assert cluster.get('PROXY_01'); c.stop_one(1); c.start_one(1, cluster, allow_missing_proxy=False); c.wait_health(1); print('account01', c.relay_url(1), 'log', c.log_path(1))"
```

Then account 02:

```
E:\trae-relay-cluster\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, r'E:\trae-relay-cluster\scripts'); import cluster_ctl as c; cluster=c.load_cluster_env(); assert cluster.get('PROXY_02'); c.stop_one(2); c.start_one(2, cluster, allow_missing_proxy=False); c.wait_health(2); print('account02', c.relay_url(2), 'log', c.log_path(2))"
```

Then account 04:

```
E:\trae-relay-cluster\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, r'E:\trae-relay-cluster\scripts'); import cluster_ctl as c; cluster=c.load_cluster_env(); assert cluster.get('PROXY_04'); c.stop_one(4); c.start_one(4, cluster, allow_missing_proxy=False); c.wait_health(4); print('account04', c.relay_url(4), 'log', c.log_path(4))"
```

OAuth helper after each restart (sequential, port 18765 not 8765):

```
E:\trae-relay-cluster\.venv\Scripts\python.exe E:\trae-relay-cluster\upstream\Trae2api-cn\web_login.py --relay http://127.0.0.1:18001 --port 18765
```

Then change `--relay` to `http://127.0.0.1:18002`, then `http://127.0.0.1:18004`. Printed by `oauth-help` (`cluster_ctl.py:497-503`).

### Commands that are unsafe here

```
python E:\trae-relay-cluster\scripts\cluster_ctl.py start
python E:\trae-relay-cluster\scripts\cluster_ctl.py start --allow-missing-proxy
python E:\trae-relay-cluster\scripts\cluster_ctl.py stop
python E:\trae-relay-cluster\scripts\restart_fxip_01_05.py
```

`restart_fxip_01_05.py` stops 06-10 then `start_one` for 01-05 with `allow_missing_proxy=False` — that **starts 03 and 05**.

## HTTP_PROXY preservation on restart

Yes, if you `stop_one` then `start_one(..., allow_missing_proxy=False)`.

`child_env` (`cluster_ctl.py:243-273`):

1. Copies `os.environ`, then **deletes** parent `http_proxy`/`https_proxy`/`all_proxy`/`ftp_proxy` (any case).
2. Sets `HOST=127.0.0.1`, `PORT=1800N`, `TRAE_AUTH_SOURCE=web-login`, `UPSTREAM_MODE=raw`, `NO_PROXY=localhost,127.0.0.1`.
3. Reloads proxy from `load_cluster_env()` → `PROXY_NN` / `PROXY_N`.
4. If proxy is non-empty and not a placeholder, sets `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy` to that value.
5. Else if `allow_missing_proxy` is false: `ClusterError: PROXY_NN missing in .cluster.env; use --allow-missing-proxy only for loopback health/isolation`.

`.cluster.env` keys present now (values not printed): `RELAY_API_KEY`, `PROXY_01`..`PROXY_05`. No `PROXY_06`..`PROXY_10`.

A live pid is **not** updated in place. Skipping `stop_one` leaves the old process env.

## Fail-closed if PROXY_03/05 are 502

Two layers; they are not the same:

**Start-time (cluster_ctl):** fail-closed only on **missing/placeholder** proxy, not on 502. `start_one(3)` / `start_one(5)` would still spawn uvicorn because `PROXY_03`/`PROXY_05` are set. `start_one` does not probe the proxy. Evidence: `child_env` `cluster_ctl.py:266-273`; `start_one` `cluster_ctl.py:301-329`.

**Request-time (httpx):** fail-closed. Trae2api-cn uses default `trust_env=True`; CONNECT/forward non-2xx becomes `httpx.ProxyError`; no retry-direct. Live 03/05: `ProxyError: 502 Bad Gateway` in `runtime/fxip-retry-35.json` and `runtime/fxip-bound-01-05.json`. `cmd_verify_failclosed` only proves unreachable `127.0.0.1:1`, not 502.

So: do not start 03/05 after OAuth even though start would succeed. Traffic would fail closed through the 502 proxy rather than leak the host IP, but OAuth/token refresh would also fail.

06-10: missing `PROXY_NN` → `start_one(..., False)` raises before spawn. That is the start-time fail-closed that protects 06-10.

## `--allow-missing-proxy`

Still exists (`cluster_ctl.py:512`, `cmd_start` `cluster_ctl.py:364-379`).

Must **not** be used for 06-10: it bypasses the missing-proxy `ClusterError` and starts processes with no `HTTP_PROXY*` (parent proxy vars already stripped). README.local.md: flag is only for loopback health/OAuth scaffolding.

## RELAY_API_KEYS injection

Native (`child_env` `cluster_ctl.py:259-263`):

- reads `.cluster.env` key `RELAY_API_KEY` (singular)
- if set and not placeholder: `env["RELAY_API_KEYS"] = relay_key` (plural)
- else: `env["RELAY_API_KEYS"] = ""`

Docker compose generator (`cluster_ctl.py:213`): `RELAY_API_KEYS: ${RELAY_API_KEY}`.

`envs/accountNN.env` does not contain `RELAY_API_KEYS` or proxies (`gen_envs.sh` comment: injected by orchestrator). `cluster.env.example:15-16` documents the same mapping.

Current `.cluster.env` has `RELAY_API_KEY` set (non-placeholder). Restart 01/02/04 with `allow_missing_proxy=False` therefore re-injects `RELAY_API_KEYS`.

## Log / pid paths under runtime/

Defined `cluster_ctl.py:24-25,50-55,321-325`:

- logs: `E:\trae-relay-cluster\runtime\logs\accountNN.log` (append binary, stdout+stderr)
- pids: `E:\trae-relay-cluster\runtime\pids\accountNN.pid`
- materialize report: `runtime\materialize.txt`

Observed this check (read-only): pid files for 01-05 only; logs for 01-10 (06-10 stale). Ports: `18000+i` → 18001/18002/18004.

## Ports / bind

`port(i)=18000+i`, `HOST=127.0.0.1` in process env and uvicorn `--host 127.0.0.1 --port 1800N` (`cluster_ctl.py:42-43,247-248,307-318`).

## Uncertainty

- Whether 03/05 502 is still live **now** was not re-probed (read-only; last evidence 2026-08-30 in `runtime/fxip-bound-01-05.json`).
- `start_one` healthz success does not prove proxy egress; only `wait_health` loopback `/healthz`.
