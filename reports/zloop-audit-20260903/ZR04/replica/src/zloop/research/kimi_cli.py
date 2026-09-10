"""zloop.research.kimi_cli — Kimi K2 CLI backup research lane.

Provides CLI fallback when HTTP server endpoint is unavailable or offline.
Executes query via `kimi -p "<query>" --output-format stream-json` CLI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .kimi_server import (
    HEALTH_ERROR,
    HEALTH_OK,
    HEALTH_QUOTA_EXHAUSTED,
    HEALTH_SERVER_UNAVAILABLE,
)


class KimiCliLane:
    """Backup research lane using `kimi` CLI."""

    def __init__(self, exe_path: Optional[str] = None):
        self.exe_path = exe_path or self._locate_kimi_exe()

    @staticmethod
    def _locate_kimi_exe() -> str:
        """Locate kimi executable on PATH."""
        return shutil.which("kimi") or "kimi"

    def ensure_server() -> bool:
        """Check whether kimi CLI binary is available."""
        exe = shutil.which(self.exe_path) or self.exe_path
        if os.path.isabs(exe) or os.path.exists(exe):
            return True
        return shutil.which(self.exe_path) is not None

    def openapi_digest(self) -> str:
        """Return a pseudo-digest for CLI lane surface."""
        return "cli:kimi-k2"

    def token_fingerprint() -> Optional[str]:
        """CLI lane operates using local credentials."""
        return "cli-local"

    def ask(self, query: str, cwd: Optional[Path] = None, timeout_s: int = 180) -> Dict[str, Any]:
        """Execute question through `kimi -p` CLI and parse stream-json lines."""
        if not self.ensure_server():
            return {
                "answer": None,
                "provider_health": HEALTH_SERVER_UNAVAILABLE,
                "last_turn_reason": "failed",
                "raw_messages": [],
                "error": f"kimi CLI executable {self.exe_path!r} not found",
            }

        cmd = [self.exe_path, "-p", query, "--output-format", "stream-json"]
        try:
            res = subprocess.run(
                cmd,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return {
                "answer": None,
                "provider_health": HEALTH_ERROR,
                "last_turn_reason": "timeout",
                "raw_messages": [],
                "error": f"kimi CLI timed out after {timeout_s}s",
            }
        except Exception as e:
            return {
                "answer": None,
                "provider_health": HEALTH_SERVER_UNAVAILABLE,
                "last_turn_reason": "failed",
                "raw_messages": [],
                "error": f"subprocess execution failed: {e}",
            }

        if res.returncode != 0:
            stderr = res.stderr or ""
            health = HEALTH_ERROR
            if "usage limit" in stderr or "quota" in stderr.lower() or "exceeded" in stderr.lower():
                health = HEALTH_QUOTA_EXHAUSTED
            elif "not found" in stderr.lower() or "command not found" in stderr.lower():
                health = HEALTH_SERVER_UNAVAILABLE
            return {
                "answer": None,
                "provider_health": health,
                "last_turn_reason": "failed",
                "raw_messages": [],
                "error": f"kimi CLI exited with code {res.returncode}: {stderr[:300]}",
            }

        messages = []
        assistant_contents = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                messages.append(obj)
                if isinstance(obj, dict) and obj.get("role") == "assistant":
                    content = obj.get("content")
                    if content:
                        assistant_contents.append(str(content))
            except json.JSONDecodeError:
                continue

        final_answer = "\n".join(assistant_contents).strip() if assistant_contents else None
        last_turn_reason = "completed" if final_answer else "no_content"

        return {
            "answer": final_answer,
            "provider_health": HEALTH_OK if final_answer else HEALTH_ERROR,
            "last_turn_reason": last_turn_reason,
            "raw_messages": messages,
            "error": None if final_answer else "empty assistant response",
        }
