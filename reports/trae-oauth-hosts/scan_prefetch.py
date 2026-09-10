from pathlib import Path
import re
html = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace")
for cid in ["59783","66493","68481","52277","17143","4299","2158","7665","55521"]:
    print("CID", cid)
    for m in re.finditer(cid + r":\"[^\"]+\"", html):
        print(" ", m.group(0), "at", m.start())
# dns-prefetch / preconnect
print("PRE")
for m in re.finditer(r"<(?:link|script)[^>]{0,300}>", html, re.I):
    tag=m.group(0)
    if any(k in tag.lower() for k in ["prefetch","preconnect","preload","dns"]):
        print(tag[:300])
# yhgfb context
i=html.find("yhgfb")
print("yhgfb", html[max(0,i-200):i+250] if i>=0 else None)
