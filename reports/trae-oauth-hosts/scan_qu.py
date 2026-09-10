from pathlib import Path
import re
files = {
  "html": Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\authorization.html").read_text(encoding="utf-8", errors="replace"),
  "main": Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace"),
  "89080": Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\89080.a73fc33541.js").read_text(encoding="utf-8", errors="replace"),
}
for name, text in files.items():
    print("====", name, "====")
    for cid in ["55521","59783","66493","52277","17143","c.QU","QU:","api.trae.cn","www.trae.cn"]:
        print(name, cid, "count", text.count(cid))
    # extract webpack chunk mapping objects around numeric ids with hashes
    for m in re.finditer(r"55521[:\"][^,]{0,80}", text):
        print(" map", m.group(0)[:80])
    for m in re.finditer(r"59783[:\"][^,]{0,80}", text):
        print(" map59783", m.group(0)[:80])
# search QU assignment in main
text=files["main"]
for m in re.finditer(r"QU[:\s=][^,]{0,120}", text):
    s=m.group(0)
    if "http" in s or "trae" in s or "api" in s or "QU:" in s[:5]:
        print("QU", s)
print("---- nearby QU ----")
i=text.find("QU:")
print("first QU:", i, text[i:i+200] if i>=0 else None)
# find all QU occurrences context
start=0
n=0
while n<20:
    i=text.find("QU", start)
    if i<0: break
    ctx=text[max(0,i-30):i+80]
    if "http" in ctx or "trae" in ctx or "api" in ctx or "base" in ctx.lower() or "host" in ctx.lower():
        print(i, ctx.replace("\n"," "))
        n+=1
    start=i+2
