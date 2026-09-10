from pathlib import Path
import re
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace")
patterns = [
    r"https?://[a-zA-Z0-9._-]+",
    r"api\.trae[a-zA-Z0-9._-]*",
    r"trae-api[a-zA-Z0-9._-]*",
    r"mchost[a-zA-Z0-9._-]*",
    r"passport[a-zA-Z0-9._/-]*",
    r"cloudide[a-zA-Z0-9._/-]*",
    r"baseURL[^\n]{0,200}",
    r"baseUrl[^\n]{0,200}",
    r"API_HOST[^\n]{0,200}",
    r"trae\.cn[^\n]{0,80}",
]
for pat in ["api.trae", "trae.cn", "mchost", "core-normal", "api5-normal", "passport", "login-api", "sso", "byteoversea", "volc", "account.trae", "open.trae"]:
    idxs=[]
    start=0
    low=text.lower()
    while True:
        i=low.find(pat.lower(), start)
        if i<0: break
        idxs.append(i)
        start=i+len(pat)
        if len(idxs)>=15: break
    print("PAT", pat, "count_total", low.count(pat.lower()), "shown", len(idxs))
    for i in idxs[:8]:
        snip=text[max(0,i-120):i+180].replace("\n"," ")
        print(" ", i, snip)
        print()
