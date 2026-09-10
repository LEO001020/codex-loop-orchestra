#!/usr/bin/env python3
"""ZCode root-prompt performance overlay (prompt-lab).

D-22b doctrine: this overlay is NEVER a ZLoop correctness dependency.
Unknown build hash -> refuse to patch; stock ZCode runs; ZLoop keeps working.
Never freeze ZCode updates to keep a patch alive: after an upgrade the hash
changes, this tool refuses, and you re-characterize on the new build (or
drop the patch entirely). Offsets are forensic evidence only — every patch
is anchored to an exact unique preimage string, never a byte range.

Usage:
  python patch.py status
  python patch.py apply candidates/sentinel.json
  python patch.py restore
"""
import argparse, hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

BUNDLE = Path(r"E:\Program Files\ZCode\resources\glm\zcode.cjs")
BACKUP = BUNDLE.parent / "zcode.cjs.zloop-bak"
STATE = Path(__file__).with_name("state.json")
KNOWN = Path(__file__).with_name("known-builds.json")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stock_shas() -> dict:
    return json.loads(KNOWN.read_text(encoding="utf-8"))["builds"]


def node_check(p: Path) -> bool:
    try:
        subprocess.run(["node", "--check", str(p)], check=True, capture_output=True, timeout=120)
        return True
    except Exception:
        return False


def atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".zloop-tmp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "apply", "restore"])
    ap.add_argument("candidate", nargs="?", help="candidate json path (apply only)")
    args = ap.parse_args()
    known = stock_shas()
    cur = sha256(BUNDLE)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None

    if args.cmd == "status":
        print(f"bundle : {BUNDLE}")
        print(f"sha256 : {cur}")
        print(f"known  : {'STOCK (known build)' if cur in known else 'UNKNOWN BUILD — apply will refuse (upgraded or foreign)'}")
        print(f"backup : {'present' if BACKUP.exists() else 'absent'}")
        print(f"state  : {json.dumps(state) if state else 'none (clean)'}")
        return 0

    if args.cmd == "apply":
        if not args.candidate:
            sys.exit("apply requires a candidate json path")
        if state:
            sys.exit("already patched — run restore first")
        if cur not in known:
            sys.exit(f"REFUSING: bundle sha {cur[:12]}... is not a known stock build. "
                     "Re-characterize the new build before patching. ZLoop is unaffected.")
        cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        for k in ("name", "anchor", "insertion"):
            if k not in cand:
                sys.exit(f"candidate missing key: {k}")
        data = BUNDLE.read_bytes()
        anchor = cand["anchor"].encode("utf-8")
        ins = cand["insertion"].encode("utf-8")
        n = data.count(anchor)
        if n != 1:
            sys.exit(f"REFUSING: anchor occurs {n} times (need exactly 1) — preimage drifted")
        patched = data.replace(anchor, anchor + ins, 1)
        fd, tmp = tempfile.mkstemp(dir=str(BUNDLE.parent), suffix=".zloop-tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(patched)
        if not node_check(Path(tmp)):
            os.unlink(tmp)
            sys.exit("REFUSING: node --check failed on patched file")
        if BACKUP.exists():
            os.unlink(tmp)
            sys.exit("backup already exists — restore first (or delete it deliberately)")
        atomic_write(BACKUP, data)
        os.replace(tmp, BUNDLE)
        STATE.write_text(json.dumps({
            "candidate": cand["name"], "original_sha": cur,
            "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2), encoding="utf-8")
        print(f"applied {cand['name']}: open a FRESH ZCode session to verify, then run restore")
        return 0

    if args.cmd == "restore":
        if not BACKUP.exists():
            sys.exit("no backup to restore")
        bsha = sha256(BACKUP)
        if bsha not in known:
            sys.exit(f"REFUSING: backup sha {bsha[:12]}... is not a known stock build — inspect manually")
        atomic_write(BUNDLE, BACKUP.read_bytes())
        BACKUP.unlink()
        if STATE.exists():
            STATE.unlink()
        print("restored stock bundle; the sentinel must NOT fire in the next fresh session")
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
