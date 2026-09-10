"""zloop.evidence — H0 journal (fail-soft) + content-addressed blob store.

VOL-06: the captured observable surface, redacted before hash (I13),
per-session NDJSON with a cross-process file lock, payload >4KB goes to blob.
This plane is fail-soft: any failure returns None / raises nothing to the
caller's control flow (the hook must exit 0 regardless — I3).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from . import redact

try:
    import msvcrt
    _WIN = True
except ImportError:
    import fcntl
    _WIN = False

INLINE_CAP = 4096  # bytes; larger payloads go to blob CAS
KINDS = {
    "session_start", "prompt", "tool_call", "tool_result", "tool_failure",
    "stop", "permission_request", "wave_event", "materialize", "promote",
    "research", "c2c", "checkpoint", "binding", "degraded",
}
COVERAGE = {
    "root_surface_full", "native_child_result_only", "native_child_surface_observed",
    "external_worker_sdk_events", "external_worker_final_only", "hook_capture_failed",
}


class BlobStore:
    """Content-addressed store: blobs/sha256/<2 hex>/<64 hex>."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, data: bytes) -> str:
        h = hashlib.sha256(data).hexdigest()
        p = self.root / h[:2] / h
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return h

    def path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest

    def has(self, digest: str) -> bool:
        return self.path(digest).exists()

    def get(self, digest: str) -> Optional[bytes]:
        p = self.path(digest)
        return p.read_bytes() if p.exists() else None


@contextmanager
def _file_lock(path: Path, timeout: float = 2.0):
    """Best-effort exclusive lock; yields True if acquired. Fail-soft."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    ok = False
    deadline = time.time() + timeout
    while True:
        try:
            if _WIN:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            ok = True
            break
        except OSError:
            if time.time() >= deadline:
                break
            time.sleep(0.05)
    try:
        yield ok
    finally:
        if ok:
            try:
                if _WIN:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


class Journal:
    """Per-session NDJSON journal. One writer per file, guarded by a lock."""

    def __init__(self, session_file: Path, blob_root: Path):
        self.file = Path(session_file)
        self.lock_path = self.file.with_suffix(".lock")
        self.blob = BlobStore(blob_root)
        self._seq, self._prev = self._tail()

    def _tail(self) -> tuple[int, Optional[str]]:
        if not self.file.exists():
            return 0, None
        seq, prev = 0, None
        try:
            with self.file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    seq = obj.get("seq", seq)
                    prev = obj.get("hash", prev)
        except OSError:
            return 0, None
        return seq, prev

    def append(self, *, kind: str, session_id: str,
               event: Optional[str] = None, tool: Optional[str] = None,
               payload: Optional[Any] = None,
               coverage: str = "root_surface_full",
               run_id: Optional[str] = None,
               stage_id: Optional[str] = None) -> Optional[str]:
        """Append one redacted envelope. Returns 'ev:s:<seq>' or None on failure."""
        if kind not in KINDS:
            kind = "degraded"
        if coverage not in COVERAGE:
            coverage = "hook_capture_failed"
        from . import ids
        try:
            safe = redact.redact_obj(payload if payload is not None else {})
            body = json.dumps(safe, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            payload_hash = hashlib.sha256(body).hexdigest()
            inline: Optional[str] = None
            payload_ref: Optional[str] = None
            if len(body) <= INLINE_CAP:
                inline = body.decode("utf-8")
            else:
                payload_ref = "blob:sha256:" + self.blob.put(body)
            with _file_lock(self.lock_path):
                self._seq += 1
                envelope = {
                    "seq": self._seq,
                    "ts": ids.now_iso(),
                    "session_id": session_id,
                    "run_id": run_id,
                    "stage_id": stage_id,
                    "kind": kind,
                    "event": event,
                    "tool": tool,
                    "coverage": coverage,
                    "payload_inline": inline,
                    "payload_ref": payload_ref,
                    "hash": payload_hash,
                    "prev_line_hash": self._prev,
                }
                line = json.dumps(envelope, ensure_ascii=False,
                                  separators=(",", ":"))
                self.file.parent.mkdir(parents=True, exist_ok=True)
                with self.file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                self._prev = payload_hash
            return f"ev:s:{self._seq}"
        except Exception:
            # fail-soft: H0 never breaks a native turn (I3)
            try:
                self._write_degraded(kind)
            except Exception:
                pass
            return None

    def _write_degraded(self, kind: str) -> None:
        from . import ids
        self._seq += 1
        envelope = {"seq": self._seq, "ts": ids.now_iso(), "kind": "degraded",
                    "coverage": "hook_capture_failed", "hash": "degraded",
                    "prev_line_hash": self._prev, "note": f"capture failed for {kind}"}
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(envelope, separators=(",", ":")) + "\n")


def read_journal(path: Path) -> list[dict]:
    """Read all valid lines (H2 search basis; grep-friendly file remains)."""
    out = []
    if not Path(path).exists():
        return out
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"torn_line": line[:120]})
    return out


def verify_chain(path: Path, blob_root: Path) -> dict:
    """Line-hash chain + blob existence check (history verify)."""
    lines = read_journal(path)
    prev = None
    errors = []
    for obj in lines:
        if "torn_line" in obj:
            errors.append(f"torn line at seq~{obj.get('seq', '?')}")
            continue
        if prev is not None and obj.get("prev_line_hash") != prev:
            errors.append(f"chain break at seq {obj.get('seq')}")
        prev = obj.get("hash")
        ref = obj.get("payload_ref")
        if ref and ref.startswith("blob:sha256:"):
            digest = ref.split(":", 2)[2]
            if not BlobStore(blob_root).has(digest):
                errors.append(f"missing blob {digest[:12]} at seq {obj.get('seq')}")
    return {"lines": len(lines), "errors": errors, "ok": not errors}
