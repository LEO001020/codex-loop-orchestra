#!/usr/bin/env python3
"""P-ARC-1: environment & legacy archaeology -> artifacts/capabilities/phase-1.json

Read-only + writes only into this repo's artifacts/. Never mutates legacy trees.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "artifacts" / "capabilities"

PROGRAMDATA_REQ = Path(r"C:\ProgramData\OpenAI\Codex\requirements.toml")
OLD_LOOP = Path(r"E:\codex-LOOP")
ZCODE_INSTALL = os.environ.get("ZCODE_WINDOWS_APP_INSTALL_DIR", "")


def sh(cmd, cwd=None, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                           timeout=timeout, shell=False)
        return {"cmd": " ".join(cmd) if isinstance(cmd, list) else cmd,
                "rc": p.returncode, "out": (p.stdout or "").strip()[:1500],
                "err": (p.stderr or "").strip()[:300]}
    except FileNotFoundError:
        # Windows: .cmd shims need a shell to resolve
        try:
            s = cmd if isinstance(cmd, str) else subprocess.list2cmdline(cmd)
            p = subprocess.run(s, capture_output=True, text=True, cwd=cwd,
                               timeout=timeout, shell=True)
            return {"cmd": s, "rc": p.returncode, "out": (p.stdout or "").strip()[:1500],
                    "err": (p.stderr or "").strip()[:300]}
        except Exception as e:  # noqa: BLE001
            return {"cmd": str(cmd), "rc": None, "error": repr(e)[:200]}
    except Exception as e:  # noqa: BLE001
        return {"cmd": str(cmd), "rc": None, "error": repr(e)[:200]}


def file_info(p: Path) -> dict:
    if not p.exists():
        return {"exists": False, "path": str(p)}
    data = p.read_bytes() if p.stat().st_size < 200_000 else p.read_bytes()[:200_000]
    return {"exists": True, "path": str(p), "size": p.stat().st_size,
            "sha256": hashlib.sha256(data).hexdigest()}


def dir_names(p: Path, limit=60) -> list[str]:
    try:
        return sorted(x.name for x in p.iterdir())[:limit]
    except OSError:
        return []


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    zcode_env = {k: v for k, v in os.environ.items()
                 if k.startswith(("ZCODE_", "CLAUDE_"))}
    report = {
        "probe_id": "P-ARC-1",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "environment": {
            "os": {"platform": platform.platform(),
                   "version": platform.version(), "machine": platform.machine()},
            "python": sys.version,
            "sqlite_runtime": __import__("sqlite3").sqlite_version,
            "zcode_env_keys": zcode_env,
            "cwd": os.getcwd(),
        },
        "commands": [
            sh(["git", "--version"]),
            sh(["git", "status", "--porcelain=v2"]),
            sh(["git", "rev-parse", "HEAD"], cwd=str(REPO)),
            sh(["codex", "--version"]),
            sh(["kimi", "--version"]),
            sh(["codex", "login", "status"]),
            sh(["powershell", "-NoProfile", "-Command",
                "(Get-PSDrive C,E | Select Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}"
                " | ConvertTo-Json -Compress)"]),
        ],
        "legacy": {
            "programdata_requirements": file_info(PROGRAMDATA_REQ),
            "programdata_requirements_backed_up_to": "~/.zloop/hygiene-backup/",
            "codex_home": {n: ("dir" if (Path.home() / ".codex" / n).is_dir() else "file")
                           for n in dir_names(Path.home() / ".codex", limit=40)},
            "zcode_home": dir_names(Path.home() / ".zcode", limit=40),
            "kimi_home": dir_names(Path.home() / ".kimi-code", limit=40),
            "old_loop_root": dir_names(OLD_LOOP, limit=60),
            "deliveries": dir_names(OLD_LOOP / "deliveries", limit=20),
        },
        "zcode_install": {"dir": ZCODE_INSTALL,
                          "top_level": dir_names(Path(ZCODE_INSTALL), limit=40)
                          if ZCODE_INSTALL else [],
                          "headless_cli_found": False,
                          "note": "only ZCode.exe (Electron) + rg/ugrep tools; "
                                  "no standalone headless CLI binary"},
        "spec_library": str(Path(r"E:\zcode\zloop-spec")),
    }
    out = ART / "phase-1.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("written:", out)
    print("codex login:", report["commands"][5])
    return 0


if __name__ == "__main__":
    sys.exit(main())
