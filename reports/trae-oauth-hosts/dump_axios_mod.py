from pathlib import Path
text = Path(r"E:\codex-LOOP\codex-loop-s-f2\reports\trae-oauth-hosts\main.5d48f6d307.js").read_text(encoding="utf-8", errors="replace")
print(text[39200:41820])
