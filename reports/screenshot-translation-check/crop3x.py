from PIL import Image
import os

base = r"E:\codex-LOOP\github\codex-loop\docs\assets"
outdir = os.path.join(os.environ["TEMP"], "dash_ocr3")
os.makedirs(outdir, exist_ok=True)

zh = Image.open(os.path.join(base, "dashboard.png"))
en = Image.open(os.path.join(base, "dashboard.en.png"))

# Column header / card region and table region of the Chinese original.
crops = {
    "zh_cards": (zh, (780, 430, 2900, 1130)),
    "zh_rows": (zh, (780, 1130, 2900, 2070)),
    "zh_all": (zh, (0, 0, 2900, 2070)),
}
for name, (img, box) in crops.items():
    c = img.crop(box)
    c = c.resize((c.width * 3, c.height * 3), Image.LANCZOS)
    c.save(os.path.join(outdir, f"{name}.png"))
print("done")
