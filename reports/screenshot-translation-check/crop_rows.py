from PIL import Image
import os

base = r"E:\codex-LOOP\github\codex-loop\docs\assets"
outdir = os.path.join(os.environ["TEMP"], "dash_ocr4")
os.makedirs(outdir, exist_ok=True)

zh = Image.open(os.path.join(base, "dashboard.png"))

# Rows of interest in the task table (x: task-name column 780-1350).
# Row y-ranges estimated from earlier OCR (rows ~1207..2025, ~82px pitch).
crops = {
    "row5": (zh, (780, 1540, 1450, 1635)),
    "row6": (zh, (780, 1620, 1450, 1715)),
    "row7": (zh, (780, 1700, 1450, 1795)),
    "row8": (zh, (780, 1780, 1450, 1875)),
    "row9": (zh, (780, 1860, 1450, 1955)),
    "row10": (zh, (780, 1940, 1450, 2067)),
}
for name, (img, box) in crops.items():
    c = img.crop(box)
    c = c.resize((c.width * 4, c.height * 4), Image.LANCZOS)
    c = c.convert("L")
    c.save(os.path.join(outdir, f"{name}.png"))
print("done")
