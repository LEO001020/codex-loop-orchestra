from pathlib import Path
import re
html = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace")
# extract more of r.u including hash suffix default
idx = html.find('r.u=')
chunk = html[idx:idx+8000]
# find default hash concatenation
m = re.search(r'static/js/async/"\+[^;]{0,400}', chunk)
print("async concat snippet:")
print(chunk[-1500:])
print("\n==== search hash join ====")
i = html.find('+".')
print("plusdot", html[i-80:i+80] if i>=0 else None)
# typical pattern: +"."+{55521:"d99224de1a"}[e]+".js"
idx2 = html.find('55521:"d99224de1a"')
print(html[idx2-200:idx2+250])
print("\n==== after hash map ====")
print(html[idx2+2000:idx2+2800][:800])
