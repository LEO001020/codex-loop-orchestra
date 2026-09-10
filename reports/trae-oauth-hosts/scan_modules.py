from pathlib import Path
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace")
for mod in ["98933(", "58288(", "33898(", "60947(", "1694(", "87897("]:
    i = text.find(mod)
    print("MOD", mod, i)
    if i>=0:
        print(text[i:i+900].replace("\n"," ")[:900])
        print()
# search QU export
i = text.find("QU")
n=0
start=0
while n<30:
    i=text.find("QU", start)
    if i<0: break
    ctx=text[max(0,i-40):i+60]
    if any(k in ctx for k in ["http", "trae", "api", "host", "base", "export", "d(i", "QU:"]):
        print("CTX", i, ctx.replace("\n"," "))
        n+=1
    start=i+2
