from pathlib import Path
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace")
# find export of QU
i = text.find("c.QU")
print("c.QU at", i, text[i-200:i+80])
# search module that assigns QU
for pat in ["QU=", "QU:", ".QU", "api.trae", "trae.cn/cloudide", "www.trae.cn"]:
    print("pat", pat, text.count(pat))
# dump around apiHost CN branch
i = text.find("p.CN")
print("first p.CN", i)
start=0
n=0
while n<15:
    i=text.find("p.CN", start)
    if i<0: break
    print(n, i, text[max(0,i-80):i+220].replace("\n"," "))
    print()
    start=i+4
    n+=1
