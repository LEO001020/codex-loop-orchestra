from pathlib import Path
import re
html = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace")
# webpack runtime around r.u / chunk hash map
for key in ["r.u=", "function(e){return", "static/js/", ".js\"+"]:
    i = html.find(key)
    print("KEY", key, i)
    if i>=0:
        print(html[max(0,i-80):i+500].replace("\n"," ")[:600])
        print()
# extract hash map entries for auth-related chunks
need = ["55521","59783","66493","52277","17143","52021","7665"]
for cid in need:
    for m in re.finditer(cid + r":\"[0-9a-f]+\"", html):
        print("HASH", m.group(0))
