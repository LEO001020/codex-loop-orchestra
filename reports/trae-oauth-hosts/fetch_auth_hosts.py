#!/usr/bin/env python3
from __future__ import annotations
import json, re, ssl, urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse
OUT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
CTX = ssl.create_default_context()
HOST_RE = re.compile(r"https?://[a-zA-Z0-9._:-]+")
ATTR_RE = re.compile(r"(?:src|href|action|data-src|data-href)=['\"]([^'\"]+)", re.I)
URL_IN_JS_RE = re.compile(r"['\"](https?://[^'\"]+)['\"]")

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            body = resp.read(800000)
            headers = {k: v for k, v in resp.headers.items()}
            return {"ok": True, "status": resp.status, "final": resp.geturl(), "headers": headers, "body": body, "error": None}
    except Exception as exc:
        return {"ok": False, "status": None, "final": url, "headers": {}, "body": b"", "error": str(exc)}

def hosts_from_text(text):
    found = set(HOST_RE.findall(text))
    found.update(URL_IN_JS_RE.findall(text))
    return sorted(found)

def main():
    seeds = [
        "https://www.trae.cn/authorization",
        "https://www.trae.cn/",
        "https://trae.cn/",
        "https://api.trae.cn/",
        "https://solo.trae.cn/",
    ]
    records = []
    extra_assets = []
    all_hosts = set()
    for url in seeds:
        rec = fetch(url)
        body = rec["body"]
        text = body.decode("utf-8", errors="replace")
        rec_out = {
            "url": url,
            "ok": rec["ok"],
            "status": rec["status"],
            "final": rec["final"],
            "error": rec["error"],
            "content_type": rec["headers"].get("Content-Type") or rec["headers"].get("content-type"),
            "location": rec["headers"].get("Location") or rec["headers"].get("location"),
            "len": len(body),
            "hosts": hosts_from_text(text),
            "attrs": ATTR_RE.findall(text)[:200],
            "head": text[:2500],
        }
        records.append(rec_out)
        all_hosts.update(rec_out["hosts"])
        if rec["ok"] and rec_out["content_type"] and "html" in rec_out["content_type"].lower():
            base = rec["final"]
            for attr in rec_out["attrs"]:
                absu = urljoin(base, attr)
                parsed = urlparse(absu)
                if parsed.scheme in ("http", "https") and parsed.path.lower().endswith((".js", ".css", ".json", ".map")):
                    extra_assets.append(absu)
    extra_assets = sorted(set(extra_assets))[:40]
    asset_records = []
    for url in extra_assets:
        rec = fetch(url)
        text = rec["body"].decode("utf-8", errors="replace")
        hosts = hosts_from_text(text)
        all_hosts.update(hosts)
        parsed = urlparse(rec["final"] or url)
        if parsed.netloc:
            all_hosts.add(parsed.scheme + "://" + parsed.netloc)
        asset_records.append({
            "url": url,
            "ok": rec["ok"],
            "status": rec["status"],
            "final": rec["final"],
            "error": rec["error"],
            "content_type": rec["headers"].get("Content-Type") or rec["headers"].get("content-type"),
            "len": len(rec["body"]),
            "hosts": hosts[:200],
        })
    payload = {"seeds": records, "assets": asset_records, "all_hosts": sorted(all_hosts)}
    (OUT / "auth_page_fetch.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT / "auth_page_fetch.json")
    print("seed_count", len(records), "asset_count", len(asset_records))
    for h in sorted(all_hosts):
        print(" ", h)

if __name__ == "__main__":
    main()
