from pathlib import Path
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace")
for pat in ["WEBSITE_HOST", "APP_ID", "api.trae.cn", "www.trae.cn", "https://api", "cloudide", "passport", "QU", "TRA_E", "cnHost", "CN_HOST", "trae.cn"]:
    print(pat, text.count(pat))
print("==== 50720 module ====")
i = text.find("50720(")
print("50720 at", i)
if i>=0:
    print(text[i:i+2500])
print("==== us.WEBSITE ====")
start=0
n=0
while n<12:
    i=text.find("WEBSITE_HOST", start)
    if i<0: break
    print(i, text[max(0,i-120):i+200].replace("\n"," "))
    print()
    start=i+12
    n+=1
