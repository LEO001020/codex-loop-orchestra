"""zloop.ids — identity & time helpers (VOL-04 §1)."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_plus(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def new_nonce(nbytes: int = 32) -> str:
    """64-hex high-entropy single-use token (bind-token, I32)."""
    return secrets.token_hex(nbytes)


def new_project_id() -> str:
    return str(uuid.uuid4())


def new_launch_id() -> str:
    return "L" + uuid.uuid4().hex[:12]


def fmt_run(n: int) -> str:
    return f"R{n:03d}"


def fmt_stage(n: int) -> str:
    return f"S{n:02d}"


def fmt_packet(n: int) -> str:
    return f"P{n:02d}"


def fmt_wave(n: int) -> str:
    return f"W{n}"


def fmt_research(n: int) -> str:
    return f"RS{n:03d}"


def parse_int_suffix(prefix: str, value: str) -> Optional[int]:
    """'R012' -> 12 for prefix 'R'."""
    if value.startswith(prefix) and value[len(prefix):].isdigit():
        return int(value[len(prefix):])
    return None
