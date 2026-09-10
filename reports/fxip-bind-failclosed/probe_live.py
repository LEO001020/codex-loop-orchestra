#!/usr/bin/env python3
"""Read-only fxip bind/fail-closed check. Never prints proxy credentials."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil

sys.path.insert(0, r"E:\trae-relay-cluster\scripts")
import cluster_ctl as c  # noqa: E402

LOCAL = "125.66.144.76"
TUN = "111.243.72.60"
EXPECT = {
    "01": {
        "host": "c525.fxip.cc:9345",
        "egress": "222.93.12.58",
        "region": "Jiangsu",
        "city": "Nanjing",
        "isp_needles": ["chinanet", "telecom"],
    },
    "02": {
        "host": "c562.fxip.cc:9345",
        "egress": "183.226.172.136",
        "region": "Chongqing",
        "city": "Chongqing",
        "isp_needles": ["mobile", "cmcc"],
    },
    "04": {
        "host": "c1109.fxip.cc:9345",
        "egress": "124.164.19.100",
        "region": "Shanxi",
        "city": "Linfen",
        "isp_needles": ["unicom", "cnc", "china169"],
    },
    "03": {"host": "c1031.fxip.cc:9345"},
    "05": {"host": "c765.fxip.cc:9345"},
}


def host_only(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.hostname:
        return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
    return url.split("@", 1)[-1]


def extract_ip(text: str) -> str:
    text = (text or "").strip()
    for token in text.replace("：", " ").replace(":", " ").split():
        parts = token.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return token
    return ""


def redact(text: str) -> str:
    return re.sub(r"://[^/\s:@]+:[^/\s:@]+@", "://REDACTED@", text)


def env_proxy_flags(path: Path) -> dict:
    keys: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, val = raw.split("=", 1)
            keys[key.strip()] = val.strip()
    return {
        "path": str(path),
        "exists": path.exists(),
        "HTTP_PROXY": bool(keys.get("HTTP_PROXY") or keys.get("http_proxy")),
        "HTTPS_PROXY": bool(keys.get("HTTPS_PROXY") or keys.get("https_proxy")),
    }


def process_rec(i: int) -> dict:
    pid = c.read_pid(i)
    rec: dict = {"pid": pid, "alive": bool(pid)}
    if not pid:
        return rec
    try:
        proc = psutil.Process(pid)
        env = proc.environ()
        rec.update(
            {
                "alive": proc.is_running(),
                "cwd": proc.cwd(),
                "HTTP_PROXY_present": bool(env.get("HTTP_PROXY") or env.get("http_proxy")),
                "HTTPS_PROXY_present": bool(env.get("HTTPS_PROXY") or env.get("https_proxy")),
                "HTTP_PROXY_host": host_only(env.get("HTTP_PROXY") or env.get("http_proxy")),
                "HTTPS_PROXY_host": host_only(env.get("HTTPS_PROXY") or env.get("https_proxy")),
                "NO_PROXY": env.get("NO_PROXY") or env.get("no_proxy"),
            }
        )
    except Exception as exc:
        rec["err"] = type(exc).__name__
    return rec


def healthz(i: int) -> dict:
    url = c.relay_url(i) + "/healthz"
    t0 = time.time()
    try:
        with httpx.Client(timeout=3.0, trust_env=False) as client:
            resp = client.get(url)
            return {
                "ok": resp.status_code == 200,
                "status": resp.status_code,
                "body": (resp.text or "")[:120],
                "s": round(time.time() - t0, 2),
            }
    except Exception as exc:
        return {"ok": False, "err": type(exc).__name__, "s": round(time.time() - t0, 2)}


def probe_https(i: int, cluster: dict[str, str]) -> dict:
    env = c.child_env(i, cluster, allow_missing_proxy=False)
    proxy = env.get("HTTPS_PROXY") or env.get("HTTP_PROXY")
    rec = {"host": host_only(proxy), "ok": False}
    t0 = time.time()
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, trust_env=False, proxy=proxy) as client:
            resp = client.get("https://myip.ipip.net")
            text = (resp.text or "").strip()[:220]
            eip = extract_ip(text)
            rec.update(
                {
                    "status": resp.status_code,
                    "egress": eip,
                    "text": text,
                    "s": round(time.time() - t0, 2),
                    "local": eip == LOCAL,
                    "tun": eip == TUN,
                }
            )
            rec["ok"] = bool(eip and not rec["local"] and not rec["tun"] and resp.status_code == 200)
    except Exception as exc:
        rec.update({"err": redact(f"{type(exc).__name__}: {exc}")[:240], "s": round(time.time() - t0, 2)})
        rec["https_502"] = "502" in rec["err"]
    if rec.get("ok") and rec.get("egress"):
        try:
            geo = httpx.get(
                f"http://ip-api.com/json/{rec['egress']}?fields=status,country,countryCode,regionName,city,isp,query",
                timeout=8,
                trust_env=False,
            ).json()
            rec["geo"] = geo
        except Exception as exc:
            rec["geo"] = {"err": type(exc).__name__}
    return rec


def judge() -> dict:
    cluster = c.load_cluster_env()
    out: dict = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "local_forbidden": LOCAL,
        "tun_forbidden": TUN,
        "cluster_proxy_hosts": {},
        "instance_env_files": {},
        "process": {},
        "healthz": {},
        "live_egress": {},
        "verdict": {},
    }
    for i in range(1, 11):
        url = c.proxy_for(i, cluster)
        out["cluster_proxy_hosts"][c.nn(i)] = (
            host_only(url) if url and not c.is_placeholder(url) else None
        )
        out["process"][c.nn(i)] = process_rec(i)
        out["healthz"][c.nn(i)] = healthz(i)
    for i in range(1, 6):
        out["instance_env_files"][c.nn(i)] = {
            "envs": env_proxy_flags(c.env_template(i)),
            "instance": env_proxy_flags(c.instance_dir(i) / ".env"),
        }
    for i in (1, 2, 4, 3, 5):
        out["live_egress"][c.nn(i)] = probe_https(i, cluster)

    for acc in ("01", "02", "03", "04", "05"):
        proc = out["process"][acc]
        live = out["live_egress"][acc]
        expect = EXPECT[acc]
        reasons = []
        host_ok = proc.get("HTTP_PROXY_host") == expect["host"] and proc.get("HTTPS_PROXY_host") == expect["host"]
        if not proc.get("HTTP_PROXY_present") or not proc.get("HTTPS_PROXY_present"):
            reasons.append("process HTTP(S)_PROXY missing")
        if not host_ok:
            reasons.append(
                f"host mismatch process={proc.get('HTTP_PROXY_host')}/{proc.get('HTTPS_PROXY_host')} expect={expect['host']}"
            )
        if out["cluster_proxy_hosts"][acc] != expect["host"]:
            reasons.append(f"cluster host {out['cluster_proxy_hosts'][acc]} != {expect['host']}")
        if acc in ("01", "02", "04"):
            if not live.get("ok"):
                reasons.append(live.get("err") or f"egress not ok status={live.get('status')}")
            if live.get("egress") != expect["egress"]:
                reasons.append(f"egress {live.get('egress')} != {expect['egress']}")
            if live.get("local") or live.get("tun"):
                reasons.append("forbidden local/tun IP")
            geo = live.get("geo") or {}
            if geo.get("regionName") and geo.get("regionName") != expect["region"]:
                reasons.append(f"region {geo.get('regionName')} != {expect['region']}")
            isp = (geo.get("isp") or "").lower()
            if isp and not any(n in isp for n in expect["isp_needles"]):
                reasons.append(f"isp {geo.get('isp')} missing {expect['isp_needles']}")
            verdict = "PASS" if not reasons else "FAIL"
        else:
            fail_closed = bool(live.get("https_502") or (live.get("err") and "ProxyError" in str(live.get("err"))))
            leaked = bool(live.get("ok") or live.get("local") or live.get("tun") or live.get("egress") in {LOCAL, TUN})
            if leaked:
                reasons.append("not fail-closed")
            if not fail_closed:
                reasons.append(live.get("err") or f"expected HTTPS 502, got {live}")
            if not proc.get("HTTP_PROXY_present") or not proc.get("HTTPS_PROXY_present"):
                reasons.append("03/05 proxy dropped")
            verdict = (
                "PASS"
                if fail_closed
                and not leaked
                and host_ok
                and proc.get("HTTP_PROXY_present")
                and proc.get("HTTPS_PROXY_present")
                else "FAIL"
            )
        out["verdict"][acc] = {
            "result": verdict,
            "reasons": reasons,
            "host": proc.get("HTTP_PROXY_host"),
            "egress": live.get("egress") or live.get("err"),
        }

    six_running = [nn for nn, rec in out["process"].items() if int(nn) >= 6 and rec.get("alive")]
    out["accounts06_10_stopped"] = not six_running
    out["accounts06_10_running"] = six_running
    return out


def main() -> int:
    dest = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\fxip-bind-failclosed\live.json")
    out = judge()
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", dest)
    for acc, rec in out["verdict"].items():
        print(acc, rec["result"], rec["host"], rec["egress"], "; ".join(rec["reasons"]) or "ok")
    print("06-10 stopped", out["accounts06_10_stopped"], out["accounts06_10_running"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
