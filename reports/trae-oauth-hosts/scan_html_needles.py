from pathlib import Path
html = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace")
# dump interesting substrings
needles = ["api.trae", "trae.cn", "passport", "sso", "login", "oauth", "ExchangeToken", "bytedance", "volces", "snssdk", "mcs.zijieapi", "mon.zijieapi", "ibytedapm", "bytegoofy", "cdn", "authorization", "127.0.0.1"]
low = html.lower()
out = []
for n in needles:
    idx = 0
    count = 0
    while True:
        i = low.find(n.lower(), idx)
        if i < 0:
            break
        count += 1
        start = max(0, i-80)
        end = min(len(html), i+120)
        out.append(f"{n}@{i}: {html[start:end].replace(chr(10),' ')[:200]}")
        idx = i + len(n)
        if count >= 8:
            break
Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization_needles.txt").write_text("\n".join(out), encoding="utf-8")
print("needles", len(out))
for line in out[:40]:
    print(line)
