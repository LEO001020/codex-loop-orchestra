from pathlib import Path
import re
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace")
# webpack chunk map around authorization
m = re.search(r"55521:[^\n]{0,400}", text)
print("55521 map", m.group(0) if m else None)
# also extract chunk ids from script tags
scripts=re.findall(r"static/js/([0-9]+)\.[a-f0-9]+\.js", text)
print("numeric chunks", scripts)
# look for webpack public path / API env
for pat in ["CN_API", "TRA_E", "host:", "VITE_", "REACT_APP", "apiHost", "API_ORIGIN", "origin:"]:
    print("html pat", pat, text.lower().count(pat.lower()))
