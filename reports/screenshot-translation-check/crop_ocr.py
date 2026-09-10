from PIL import Image
import os

base = r"E:\codex-LOOP\github\codex-loop\docs\assets"
outdir = os.path.join(os.environ["TEMP"], "dash_ocr")
os.makedirs(outdir, exist_ok=True)

# Chinese original: 2899x2067. Browser chrome ~y 0-450; dashboard below.
zh = Image.open(os.path.join(base, "dashboard.png"))
# English mockup: 1800x1120
en = Image.open(os.path.join(base, "dashboard.en.png"))

crops = {
    "zh_header": (zh, (700, 300, 2899, 1150)),
    "zh_table": (zh, (700, 1120, 2900, 2067)),
    "zh_sidebar": (zh, (0, 440, 700, 2067)),
    "en_header": (en, (0, 0, 1800, 560)),
    "en_table": (en, (0, 560, 1800, 1120)),
}
for name, (img, box) in crops.items():
    c = img.crop(box)
    c = c.resize((c.width * 2, c.height * 2), Image.LANCZOS)
    c.save(os.path.join(outdir, f"{name}.png"))
print("done")
