# fxip bind + fail-closed check (2026-08-30 01:41)

Read-only. No proxy credentials printed. 06-10 not started.
`scripts/verify-egress.py` was not run: it iterates all 10 accounts, requires PROXY_06-10, and would probe 03/05.
Live check used process env + explicit proxy for 01/02/04, then HTTPS fail-closed for 03/05 without unsetting HTTP(S)_PROXY.

## Bind source

Instance `.env` / `envs/accountNN.env` do **not** contain HTTP_PROXY/HTTPS_PROXY (orchestrator injects them at process start from `.cluster.env`).
Live uvicorn env for 01-05 has both HTTP_PROXY and HTTPS_PROXY set; 06-10 have no pid.

| acc | process HTTP(S)_PROXY host | live HTTPS egress | healthz | verdict |
| --- | --- | --- | --- | --- |
| 01 | c525.fxip.cc:9345 | 222.93.12.58 Jiangsu Nanjing Chinanet | 200 | PASS |
| 02 | c562.fxip.cc:9345 | 183.226.172.136 Chongqing CMCC | 200 | PASS |
| 03 | c1031.fxip.cc:9345 present | ProxyError 502; no IP | 200 loopback | PASS fail-closed |
| 04 | c1109.fxip.cc:9345 | 124.164.19.100 Shanxi Linfen Unicom | 200 | PASS |
| 05 | c765.fxip.cc:9345 present | ProxyError 502; no IP | 200 loopback | PASS fail-closed |
| 06-10 | none / not running | n/a | ConnectError | stopped |

Forbidden IPs 125.66.144.76 and 111.243.72.60 did not appear.
ipip.net city labels differ (01 Suzhou vs ip-api Nanjing; 04 Jincheng vs ip-api Linfen); egress IPs match expected last-known.

Evidence: [live.json](E:\codex-LOOP\codex-loop-s-f2\reports\fxip-bind-failclosed\live.json)
