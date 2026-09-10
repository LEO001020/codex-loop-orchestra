from PIL import Image
import os

base = r"E:\codex-LOOP\github\codex-loop\docs\assets"
outdir = os.path.join(os.environ["TEMP"], "dash_ocr5")
os.makedirs(outdir, exist_ok=True)

zh = Image.open(os.path.join(base, "dashboard.png"))
en = Image.open(os.path.join(base, "dashboard.en.png"))

# Task-name cells in zh table (rows start ~1207, pitch ~90px; task col x 800-1420)
for i, y0 in enumerate([1207, 1301, 1391, 1481, 1572, 1662, 1752, 1838, 1932, 2023], start=1):
    c = zh.crop((790, y0 - 30, 1420, y0 + 55))
    c = c.resize((c.width * 5, c.height * 5), Image.LANCZOS)
    c.save(os.path.join(outdir, f"zh_row{i}.png"))

# zh table header row
ch = zh.crop((790, 1105, 2900, 1210))
ch = ch.resize((ch.width * 3, ch.height * 3), Image.LANCZOS)
ch.save(os.path.join(outdir, "zh_header.png"))

# en table header row
eh = en.crop((60, 555, 1800, 640))
eh = eh.resize((eh.width * 3, eh.height * 3), Image.LANCZOS)
eh.save(os.path.join(outdir, "en_header.png"))

print("done")
