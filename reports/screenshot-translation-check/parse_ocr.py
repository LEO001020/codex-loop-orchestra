import csv, os, sys
sys.stdout.reconfigure(encoding="utf-8")

def dump(tag):
    p = os.path.join(os.environ["TEMP"], "dash_ocr", f"{tag}.tsv")
    rows = list(csv.DictReader(open(p, encoding="utf-8"), delimiter="\t"))
    lines = {}
    for r in rows:
        if r["level"] != "5" or not r["text"].strip():
            continue
        key = (r["block_num"], r["par_num"], r["line_num"])
        lines.setdefault(key, []).append(r)
    print(f"===== {tag} ({len(lines)} lines) =====")
    out = []
    for key in sorted(lines, key=lambda k: (int(k[0]), int(k[1]), int(k[2]))):
        ws = lines[key]
        confs = [float(w["conf"]) for w in ws if w["conf"] != "-1"]
        avg = sum(confs) / len(confs) if confs else 0
        text = " ".join(w["text"] for w in ws)
        x = min(int(w["left"]) for w in ws)
        y = min(int(w["top"]) for w in ws)
        out.append((y, x, round(avg), text))
    for y, x, c, t in sorted(out):
        print(f"y={y:5d} x={x:5d} conf={c:3d} | {t}")

if __name__ == "__main__":
    for tag in sys.argv[1:] or ("zh", "en"):
        dump(tag)
