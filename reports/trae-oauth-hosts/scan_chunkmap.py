from pathlib import Path
import re
html = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace")
print("r.p", html[html.find("r.p="):html.find("r.p=")+300] if "r.p=" in html else None)
# webpack mapping for 55521
for m in re.finditer(r"55521:\"[^\"]+\"", html):
    print(m.group(0), m.start())
# extract nearby mapping objects
idx = html.find('55521:"d99224de1a"')
print("idx hash", idx)
print(html[max(0,idx-400):idx+400])
print("----- scripts -----")
for s in re.findall(r"static/js/[A-Za-z0-9._-]+\.js", html):
    print(s)
