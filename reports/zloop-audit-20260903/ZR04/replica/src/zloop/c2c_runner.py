"""zloop.c2c_runner — C2C Web-Native Browser Bridge & Audit Runner (VOL-16).

Transport Truth (I41): C2C is native to ChatGPT Web via the ZCode root agent's
browser interaction. This runner prepares bounded redacted C2C packets and manages
the web-native audit lifecycle without HTTP API shortcuts.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from . import c2c, db
from .research import broker

logger = logging.getLogger(__name__)


def run_automated_c2c_audit(
    project_dir: Path,
    store: db.ControlStore,
    run_id: str,
    stage_id: str,
    role: str,
    *,
    content_extra: Optional[str] = None,
    data_class: str = "project_internal",
    risk_effective: str = "HIGH",
    timeout_s: int = 15,
) -> dict[str, Any]:
    """Prepare and manage ChatGPT Web-Native C2C audit packet lifecycle (I41)."""
    project_dir = Path(project_dir)
    for r in store.conn.execute(
        "SELECT detail_json FROM events WHERE kind='c2c_recorded' AND run_id=? AND stage_id=?",
        (run_id, stage_id),
    ):
        try:
            d = json.loads(r["detail_json"])
            if d.get("role") == role:
                return {"ok": True, "already_recorded": True, "c2c_id": d.get("c2c_id")}
        except Exception:
            continue

    content = f"ZLoop Wave Stage Audit [{stage_id}] role={role}"
    if content_extra:
        content += f"\n\nContext:\n{content_extra}"

    prep = c2c.prepare_c2c(
        project_dir,
        store,
        run_id,
        stage_id,
        role,
        content=content,
        data_class=data_class,
        risk_effective=risk_effective,
    )
    c2c_id = prep["c2c_id"]
    packet_file = project_dir / ".zcode" / "c2c" / f"{c2c_id}.json"

    # Try browser automation launch helper if available
    _try_open_edge_browser(str(packet_file))

    return {
        "ok": True,
        "verdict": "PREPARED",
        "c2c_id": c2c_id,
        "packet_path": str(packet_file),
        "surface": "chatgpt_web",
    }


def _try_open_edge_browser(packet_path: str) -> None:
    """Best-effort launch edge with chatgpt web url when preparing packet."""
    try:
        import subprocess
        subprocess.Popen(["cmd.exe", "/c", "start", "msedge", "https://chatgpt.com"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def run_automated_research(
    project_dir: Path,
    store: db.ControlStore,
    run_id: str,
    stage_id: str,
    query: str,
) -> dict[str, Any]:
    """Execute research probe query."""
    res = broker.run_research(store, run_id, stage_id, query)
    return {"ok": True, "result": res}
