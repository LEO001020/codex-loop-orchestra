"""Safety regression for the Windows OpenCodex service supervisor."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _script() -> Path:
    path = Path(__file__).resolve().parents[3] / ".bootstrap" / "OpenCodex-Service-Supervisor.ps1"
    if not path.exists():
        pytest.skip("Windows OpenCodex supervisor is not present on this plane")
    return path


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell supervisor")
def test_live_exact_listener_is_never_killed_by_health_failure():
    script = _script()
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-SelfTest"],
        text=True, capture_output=True, timeout=20,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"status":"PASS"' in proc.stdout
    assert "health failure never kills live exact listener" in proc.stdout


def test_destructive_health_failure_phrase_is_absent():
    text = _script().read_text(encoding="utf-8")
    assert "stopping exact OpenCodex listener" not in text
    assert "degraded-live-listener" in text
    assert "automatic restart suppressed" in text
