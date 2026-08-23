"""Provider-sampling health truth for the active logical review route."""
from __future__ import annotations

import json
import hashlib
import os
import re
import time
import tomllib
from pathlib import Path
from typing import Any

from orchestration_common import file_lock, read_json

_HTTP_5XX = re.compile(r"(?:status|error|provider error)[^\n]{0,40}\b(50[234])\b", re.I)
_TRANSPORT = re.compile(
    r"upstream|bad gateway|gateway timeout|service unavailable|"
    r"stream disconnected|socket connection was closed|econnreset|"
    r"connection reset|connection refused|timed out before (?:the )?first|"
    r"no first response", re.I)


def is_k3(model: str) -> bool:
    return "k3" in str(model or "").casefold()


def _policy_review_model(root: Path | str) -> str:
    """Return the active logical review pin, or an empty string.

    K3 is a logical LOOP pool, not necessarily a substring in the physical
    model name.  Temporary dual-pool profiles therefore need policy-based
    recognition while old installations without policy retain the K3-name
    compatibility path.
    """
    path = Path(root).resolve() / "config" / "orchestration_policy_v2.toml"
    try:
        with path.open("rb") as handle:
            return str(tomllib.load(handle).get("models", {}).get(
                "k3_model", "")).strip()
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        return ""


def is_tracked(root: Path | str, model: str) -> bool:
    model = str(model or "").strip()
    return bool(model) and (is_k3(model) or model == _policy_review_model(root))


def health_path(root: Path | str, model: str) -> Path:
    # Preserve the long-standing k3.json path for the native K3 route.  A
    # non-K3 physical model temporarily serving the review pool receives its
    # own path, so stale K3 backoff cannot poison a profile switch.
    if is_k3(model):
        name = "k3.json"
    else:
        digest = hashlib.sha256(str(model).casefold().encode("utf-8")).hexdigest()[:16]
        name = "route-%s.json" % digest
    return Path(root).resolve() / "data" / "provider_health" / name


def _atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def classify_failure(rc: int, stderr: str, events: str = "",
                     *, timed_out: bool = False) -> dict[str, Any]:
    text = "%s\n%s" % (stderr or "", events or "")
    match = _HTTP_5XX.search(text)
    if match:
        return {"kind": "upstream_5xx", "transport": True,
                "http_status": int(match.group(1)), "backoff_seconds": 300}
    if _TRANSPORT.search(text):
        return {"kind": "transport_error", "transport": True,
                "http_status": None, "backoff_seconds": 180}
    # A task timeout is provider evidence only when Codex never produced an
    # event.  Once an event exists, the bounded task may simply have run long.
    if timed_out and not (events or "").strip():
        return {"kind": "provider_stall_no_first_response", "transport": True,
                "http_status": None, "backoff_seconds": 300}
    return {"kind": "local_failure", "transport": False,
            "http_status": None, "backoff_seconds": 0, "rc": int(rc)}


def _update(root: Path | str, model: str, mutate) -> dict[str, Any] | None:
    if not is_tracked(root, model):
        return None
    path = health_path(root, model)
    lock = path.with_suffix(path.suffix + ".lock")
    with file_lock(lock):
        old = read_json(path, {}) or {}
        value = mutate(dict(old))
        value.update({
            "schema": "codex-loop-provider-health/v2",
            "provider": str(model).split("/", 1)[0], "model": model,
            "logical_pool": "k3",
            "plane": "windows" if os.name == "nt" else "wsl",
            "ts": time.time(),
        })
        _atomic(path, value)
        return value


def record_success(root: Path | str, model: str, *, run_id: str) -> dict[str, Any] | None:
    def mutate(old):
        old.update({"status": "healthy", "backoff_until": 0,
                    "last_error_kind": None, "http_status": None,
                    "last_rc": 0, "last_task_outcome": "success",
                    "last_run_id": run_id})
        return old
    return _update(root, model, mutate)


def record_failure(root: Path | str, model: str, *, run_id: str, rc: int,
                   stderr: str, events: str = "", timed_out: bool = False
                   ) -> dict[str, Any] | None:
    classification = classify_failure(rc, stderr, events, timed_out=timed_out)

    def mutate(old):
        old.update({"last_task_outcome": classification["kind"],
                    "last_run_id": run_id, "last_rc": int(rc)})
        if classification["transport"]:
            old.update({
                "status": "unhealthy",
                "last_error_kind": classification["kind"],
                "http_status": classification["http_status"],
                "backoff_until": time.time() + classification["backoff_seconds"],
            })
        return old
    return _update(root, model, mutate)


def backoff_active(root: Path | str, model: str,
                   now: float | None = None) -> tuple[bool, dict[str, Any]]:
    if not is_tracked(root, model):
        return False, {}
    doc = read_json(health_path(root, model), {}) or {}
    now = time.time() if now is None else now
    return float(doc.get("backoff_until", 0) or 0) > now, doc
