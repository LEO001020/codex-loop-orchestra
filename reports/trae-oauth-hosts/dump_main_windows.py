from pathlib import Path
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace")
# dump larger window around first host/cdn and around axios client
i = text.find('p.USTTP')
print(text[i:i+1800])
print("\n==== axios/base module around 40000 ====")
print(text[40500:43000])
