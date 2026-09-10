from pathlib import Path
import re, json
root = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts")
files = list(root.glob("*.js")) + [root / "authorization.html"]
HOST_RE = re.compile(r"https?://[a-zA-Z0-9._:-]+")
BARE_RE = re.compile(r"[a-zA-Z0-9.-]+\.(?:cn|com|net|guru|cc|io|ai|com\.cn)")
needles = [
    "api.trae", "www.trae", "solo.trae", "passport", "login.trae", "sso",
    "ExchangeToken", "cloudide", "authorization", "127.0.0.1",
    "mchost.guru", "api5-normal", "core-normal", "zijieapi", "snssdk",
    "bytedance", "volces", "byteimg", "ibytedapm", "bytegoofy", "lf-cdn",
    "trae.com.cn", "login", "oauth", "account", "passport-api",
]
rows = []
all_hosts = set()
for f in files:
    if not f.exists():
        continue
    text = f.read_text(encoding="utf-8", errors="replace")
    hosts = set(HOST_RE.findall(text))
    all_hosts.update(hosts)
    hits = {}
    low = text.lower()
    for n in needles:
        c = low.count(n.lower())
        if c:
            hits[n] = c
    # extract nearby snippets for api/login hosts
    snippets = []
    for n in ["api.trae", "passport", "cloudide", "ExchangeToken", "login.trae", "sso.", "mchost", "authorization"]:
        idx = 0
        found = 0
        while found < 6:
            i = text.lower().find(n.lower(), idx)
            if i < 0:
                break
            snippets.append({"needle": n, "at": i, "snip": text[max(0,i-90):i+140].replace("\n"," ")[:230]})
            idx = i + len(n)
            found += 1
    rows.append({"file": f.name, "size": f.stat().st_size, "hosts": sorted(hosts), "hits": hits, "snippets": snippets})
(root / "js_host_scan.json").write_text(json.dumps({"files": rows, "all_hosts": sorted(all_hosts)}, ensure_ascii=False, indent=2), encoding="utf-8")
print("files", len(rows))
print("ALL_HOSTS")
for h in sorted(all_hosts):
    print(h)
print("---HITS---")
for r in rows:
    print(r["file"], r["hits"])
print("---SNIPS---")
for r in rows:
    if r["snippets"]:
        print("FILE", r["file"])
        for s in r["snippets"][:12]:
            print(" ", s["needle"], s["snip"])
