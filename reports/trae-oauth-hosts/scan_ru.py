from pathlib import Path
import re
html = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace")
# extract r.u function fully enough to resolve hashes, plus hash map
idx = html.find("r.u=")
print(html[idx:idx+2500])
print("\n===== HASH NEED =====")
for cid in ["55521","59783","66493","52277","17143","52021","7665","4299"]:
    print(cid, re.findall(cid + r":\"[0-9a-f]+\"", html))
