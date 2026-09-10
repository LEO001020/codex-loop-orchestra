"""zloop.c2c — C2C host-side packets (VOL-16, M5).

Transport truth (I41): the browser interaction with the external auditor is
performed by the ZCode root agent itself (native Browser, main-agent-only).
zloop only PREPARES the bounded redacted packet and RECORDS the response the
root agent pastes back. There is no browser automation in this module.

- ``prepare_c2c``: validate role / data class (``secret`` never leaves the
  machine, VOL-16 §4), redact the content (belt — the caller should already
  have redacted; I13 enforces it anyway), bound the content to
  ``CONTENT_CHAR_LIMIT`` chars (the full redacted text goes to blob CAS and
  is referenced via ``content_ref``), then write the packet file plus the
  ``c2c_prepared`` S event carrying ``packet_sha256`` for later verification.
- ``record_c2c``: verify the packet file against the hash recorded at prepare
  time (missing / tampered / corrupt packets fail gracefully), redact and
  blob the response, write the ``c2c_recorded`` S event with the honest
  ``audit_coverage="text_packet_only"`` and the observable identity (I41b:
  only observable fields, ``unknown`` when not observed — never fabricated),
  and write the redacted ``<id>-result.json`` record.
- ``bounded_digest``: D-13-era rule — the root context only ever receives a
  bounded digest (≤2KB by default, P1-11); the full text lives in the store.

D-11 (razor-audit adoption): fresh threads are mandatory only for
HIGH/CRITICAL; at other risk levels the thread policy is a C2C A/B variable
(``C_same`` vs ``C_fresh``, VOL-16 §2 / VOL-19 §5), recorded per packet in
``thread_policy_note``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

from . import ids, redact
from .evidence import BlobStore
from .stage import RISK_LEVELS

ROLES: tuple[str, ...] = ("plan", "result")
DATA_CLASSES: tuple[str, ...] = ("public", "project_internal", "sensitive")
# "secret" is a legal data classification (VOL-04 §11) but never a legal C2C
# packet class: secrets do not leave the machine (VOL-16 §4).
FORBIDDEN_DATA_CLASS = "secret"
FRESH_THREAD_RISK: tuple[str, ...] = ("HIGH", "CRITICAL")
CONTENT_CHAR_LIMIT = 8000   # packet content bound (chars)
DIGEST_CHAR_LIMIT = 2048   # bounded digest shown to the root context (P1-11)
AUDIT_COVERAGE = "text_packet_only"  # honest coverage: text packet, no bridge

# Observable identity fields (I41b, VOL-16 §3). Only these are recorded;
# anything not observable is stored as "unknown", never fabricated.
IDENTITY_FIELDS: tuple[str, ...] = (
    "surface", "ui_model_label", "search_mode", "thread_id_hint", "timestamp",
)

_C2C_ID_RE = re.compile(r"C2C\d{3,}")


# ---- shared helpers ---------------------------------------------------------

def _blobs(project_dir: Path) -> BlobStore:
    return BlobStore(Path(project_dir) / "blobs" / "sha256")


def _write_json_atomic(path: Path, obj: dict) -> bytes:
    """Write JSON atomically (tmp + replace); returns the exact bytes written."""
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return data


def _next_c2c_id(store, c2c_dir: Path) -> str:
    """Sequential "C2C###" (VOL-04 §1): max over S events and existing files."""
    nums: list[int] = []
    for row in store.conn.execute(
            "SELECT detail_json FROM events WHERE kind IN"
            " ('c2c_prepared','c2c_recorded')"):
        try:
            detail = json.loads(row["detail_json"])
        except Exception:
            continue
        n = ids.parse_int_suffix("C2C", str(detail.get("c2c_id", "")))
        if n is not None:
            nums.append(n)
    if c2c_dir.is_dir():
        for p in c2c_dir.glob("*.json"):
            n = ids.parse_int_suffix("C2C", p.stem)
            if n is not None:
                nums.append(n)
    return f"C2C{(max(nums) + 1) if nums else 1:03d}"


def _thread_policy_note(risk_effective: str) -> str:
    if risk_effective in FRESH_THREAD_RISK:
        return (f"D-11: fresh thread MANDATORY at {risk_effective} — this packet "
                "must be sent in a NEW thread, never the sibling role's "
                "(anchoring guard, VOL-16 §2).")
    return (f"D-11: fresh thread is not an invariant at {risk_effective} — "
            "thread policy is a C2C A/B variable (C_same = same-stage thread "
            "reuse vs C_fresh = fresh P/A threads, VOL-16 §2 / VOL-19 §5).")


def _prepared_detail(store, c2c_id: str) -> Optional[dict]:
    """The c2c_prepared event detail for c2c_id (S is the hash authority)."""
    for row in store.conn.execute(
            "SELECT detail_json FROM events WHERE kind='c2c_prepared' ORDER BY seq"):
        try:
            detail = json.loads(row["detail_json"])
        except Exception:
            continue
        if isinstance(detail, dict) and detail.get("c2c_id") == c2c_id:
            return detail
    return None


def _observed_identity(observed_identity: Optional[dict]) -> dict:
    """I41b: keep ONLY the observable fields; missing/None -> "unknown"."""
    src = observed_identity if isinstance(observed_identity, dict) else {}
    ident = {}
    for field in IDENTITY_FIELDS:
        value = src.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            value = "unknown"
        ident[field] = value
    return redact.redact_obj(ident)


# ---- public API --------------------------------------------------------------

def prepare_c2c(project_dir: Path, store, run_id: str, stage_id: str,
                role: str, *, content: str,
                data_class: str = "project_internal",
                risk_effective: str = "NORMAL") -> dict:
    """Prepare a bounded, redacted C2C packet (VOL-16 §1/§4, VOL-04 §10).

    The packet is written to ``c2c/<c2c_id>.json`` and audited with a
    ``c2c_prepared`` S event carrying ``packet_sha256`` (the sha256 of the
    exact file bytes, computed after redaction — I13), which ``record_c2c``
    later verifies. Returns the packet dict.
    """
    project_dir = Path(project_dir)
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    if data_class == FORBIDDEN_DATA_CLASS:
        raise ValueError(
            "data_class 'secret' is never allowed in a C2C packet: secrets do "
            "not leave the machine (VOL-16 §4)")
    if data_class not in DATA_CLASSES:
        raise ValueError(
            f"data_class must be one of {DATA_CLASSES}, got {data_class!r}")
    if risk_effective not in RISK_LEVELS:
        raise ValueError(
            f"risk_effective must be one of {RISK_LEVELS}, got {risk_effective!r}")
    if not isinstance(content, str):
        raise ValueError("content must be a string")

    # Belt (I13): the caller should already have redacted — enforce anyway.
    content = redact.redact_str(content)

    content_ref: Optional[str] = None
    if len(content) > CONTENT_CHAR_LIMIT:
        content_ref = "blob:sha256:" + _blobs(project_dir).put(
            content.encode("utf-8"))
        content = (content[:CONTENT_CHAR_LIMIT]
                   + f"\n[C2C: content truncated at {CONTENT_CHAR_LIMIT} chars; "
                     f"full redacted text stored as {content_ref}]")

    c2c_dir = project_dir / "c2c"
    c2c_id = _next_c2c_id(store, c2c_dir)
    packet = {
        "c2c_id": c2c_id,
        "role": role,
        "stage_id": stage_id,
        "run_id": run_id,
        "risk_effective": risk_effective,
        "data_class": data_class,
        "content": content,
        "fresh_thread_required": risk_effective in FRESH_THREAD_RISK,
        "thread_policy_note": _thread_policy_note(risk_effective),
        "created_at": ids.now_iso(),
    }
    if content_ref is not None:
        packet["content_ref"] = content_ref

    data = _write_json_atomic(c2c_dir / f"{c2c_id}.json", packet)
    detail = {
        "c2c_id": c2c_id,
        "role": role,
        "data_class": data_class,
        "risk_effective": risk_effective,
        "fresh_thread_required": packet["fresh_thread_required"],
        "packet_sha256": hashlib.sha256(data).hexdigest(),
    }
    if content_ref is not None:
        detail["content_ref"] = content_ref
    with store.mutation():
        store._event("c2c_prepared", detail, run_id=run_id, stage_id=stage_id)
    return packet


def record_c2c(project_dir: Path, store, c2c_id: str, response_text: str, *,
               observed_identity: Optional[dict] = None) -> dict:
    """Record the external auditor's response for a prepared C2C packet.

    Verifies the packet file against the ``packet_sha256`` stored in its
    ``c2c_prepared`` S event (VOL-16 §1: host checks packet hash), redacts and
    blobs the full response (VOL-16 §5), writes the ``c2c_recorded`` S event
    and the redacted ``c2c/<id>-result.json`` record (packet summary +
    response + observable identity, I41b). Failure results are graceful:
    ``{"ok": False, "reason": ...}`` — never an exception for unknown,
    tampered or corrupt packets.
    """
    project_dir = Path(project_dir)
    if not isinstance(response_text, str):
        raise ValueError("response_text must be a string")

    def fail(reason: str) -> dict:
        return {"ok": False, "reason": reason}

    if not isinstance(c2c_id, str) or not _C2C_ID_RE.fullmatch(c2c_id):
        return fail("unknown_c2c_id")   # also blocks path traversal shapes
    packet_path = project_dir / "c2c" / f"{c2c_id}.json"
    if not packet_path.exists():
        return fail("unknown_c2c_id")
    prepared = _prepared_detail(store, c2c_id)
    if prepared is None:
        # the S event is the authority — a file without one is not a packet
        return fail("packet_event_missing")
    try:
        raw = packet_path.read_bytes()
    except OSError:
        return fail("packet_unreadable")
    if hashlib.sha256(raw).hexdigest() != prepared.get("packet_sha256"):
        return fail("packet_integrity_mismatch")
    try:
        packet = json.loads(raw.decode("utf-8"))
        if not isinstance(packet, dict):
            raise ValueError("packet is not a JSON object")
    except Exception:
        return fail("packet_unreadable")

    # Belt (I13): redact the response BEFORE hashing/storing (VOL-16 §5).
    response = redact.redact_str(response_text)
    response_sha256 = _blobs(project_dir).put(response.encode("utf-8"))
    ident = _observed_identity(observed_identity)

    packet_summary = {
        k: packet[k] for k in (
            "c2c_id", "role", "stage_id", "run_id", "risk_effective",
            "data_class", "fresh_thread_required", "thread_policy_note",
            "content_ref", "created_at")
        if k in packet
    }
    packet_summary["content_chars"] = len(packet.get("content") or "")
    result = {
        "ok": True,
        "c2c_id": c2c_id,
        "audit_coverage": AUDIT_COVERAGE,
        "trust": "external_untrusted",       # VOL-16 §4: mark external output
        "packet_sha256": prepared.get("packet_sha256"),
        "packet_summary": packet_summary,
        "response": response,
        "response_ref": "blob:sha256:" + response_sha256,
        "response_sha256": response_sha256,
        "response_digest": bounded_digest(response),
        "observed_identity": ident,
        "recorded_at": ids.now_iso(),
    }
    _write_json_atomic(project_dir / "c2c" / f"{c2c_id}-result.json", result)
    with store.mutation():
        store._event(
            "c2c_recorded",
            {"c2c_id": c2c_id, "role": packet.get("role"),
             "response_sha256": response_sha256,
             "audit_coverage": AUDIT_COVERAGE,
             "observed_identity": ident},
            run_id=packet.get("run_id"), stage_id=packet.get("stage_id"))
    return result


def bounded_digest(response_text: str, limit: int = DIGEST_CHAR_LIMIT) -> str:
    """First ``limit`` chars of the (redacted) response (D-13-era rule).

    The root context only ever receives this bounded digest; the full text
    lives in the store (blob CAS). Redaction is applied first as a belt (I13).
    """
    if not isinstance(response_text, str):
        raise ValueError("response_text must be a string")
    text = redact.redact_str(response_text)
    n = DIGEST_CHAR_LIMIT if limit is None else limit
    return text[:n] if n > 0 else ""
