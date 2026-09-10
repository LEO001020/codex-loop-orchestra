"""zloop.wave — Wave/Packet/Launch lifecycle and four-level fencing (VOL-09).

Pure state-machine logic over the S control DB (VOL-04 §3
``packets``/``attempts``/``launches`` tables):

- ``validate_wave``: host-side final ruling on a wave proposal (VOL-09 §1-3)
  — required fields, dep existence (existing + current packets), DAG
  acyclicity (Kahn over packet ids including deps on existing), same-wave
  write_scope pairwise disjointness unless one packet depends on the other,
  ``risk_class`` >= the stage risk floor, network policy shape;
- ``accept_result``: the I6 hard fence — a result integrates only when
  stage_revision AND packet_revision AND launch_id all match the current
  packet identity AND the packet is RUNNING (all conditions AND);
- ``run_wave``: launch every validated packet (fresh launch_id + fresh
  workspace per launch [I34]; packet PENDING -> RUNNING, attempt 1, launch
  intent INTENDED -> BOUND -> RUNNING), then collect each report through the
  I6 fence: accepted -> packet REPORTED, rejected -> packet stays RUNNING and
  only a rejection event is recorded;
- ``supersede_stage_revision``: VOL-09 §2/§8 — a stage replan bumps
  stage_revision, marks every older-revision packet SUPERSEDED and clears its
  active launch; their results are evidence only [I7];
- ``late_result_guard``: a result whose launch is not the active launch is
  late/stale — evidence only, never a state change [I7].

All mutations follow the VOL-04 §4 transaction recipe (events row + state row
inside one ``store.mutation()``).
"""
from __future__ import annotations

import json

from . import ids
from .stage import RISK_LEVELS, check_stage_base, get_stage

_LEVEL_ORDER: dict[str, int] = {name: i for i, name in enumerate(RISK_LEVELS)}


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _json_or_none(v) -> str | None:
    return json.dumps(v, ensure_ascii=False) if v is not None else None


# ---------------------------------------------------------------- validation


def validate_wave(packets: list[dict], existing: dict[str, dict] | None = None, *,
                  stage_floor: str = "NORMAL") -> dict:
    """Host-side wave validation (VOL-09 §1-3). Never raises on bad packet
    data — every problem is collected into ``errors``.

    ``existing`` maps already-known packet ids (earlier waves of the stage)
    to packet dicts; ``depends_on`` may reference those or other packets in this
    wave. ``stage_floor`` is the stage risk floor the packet risk_class must
    not fall below. Returns ``{"ok": bool, "errors": [str, ...]}``.
    """
    existing = dict(existing or {})
    if stage_floor not in _LEVEL_ORDER:
        raise ValueError(
            f"invalid stage_floor {stage_floor!r}; expected one of {RISK_LEVELS}")
    if not isinstance(packets, list) or not packets:
        return {"ok": False, "errors": ["wave must be a non-empty list of packets"]}

    errors: list[str] = []
    checked: list[tuple[str, dict]] = []   # (packet_id, packet) with usable ids
    seen: set[str] = set()
    for idx, p in enumerate(packets):
        if not isinstance(p, dict):
            errors.append(f"packet[{idx}]: not an object")
            continue
        pid = p.get("packet_id")
        if not _nonempty_str(pid):
            errors.append(f"packet[{idx}]: missing packet_id")
            continue
        if pid in seen:
            errors.append(f"{pid}: duplicate packet_id in wave")
            continue
        if pid in existing:
            errors.append(f"{pid}: packet_id already exists in this stage")
            continue
        seen.add(pid)
        checked.append((pid, p))

    deps_of: dict[str, list[str]] = {}
    for pid, p in checked:
        if not _nonempty_str(p.get("goal")):
            errors.append(f"{pid}: missing goal")
        ws = p.get("write_scope")
        if not isinstance(ws, list) or not ws:
            errors.append(f"{pid}: write_scope must be a non-empty list")
        elif not all(_nonempty_str(s) for s in ws):
            errors.append(f"{pid}: write_scope entries must be non-empty strings")
        acc = p.get("acceptance")
        if not isinstance(acc, list) or not acc:
            errors.append(f"{pid}: acceptance must be a non-empty list")
        elif not all(_nonempty_str(s) for s in acc):
            errors.append(f"{pid}: acceptance entries must be non-empty strings")
        risk = p.get("risk_class")
        if risk not in _LEVEL_ORDER:
            errors.append(f"{pid}: invalid risk_class {risk!r}")
        elif _LEVEL_ORDER[risk] < _LEVEL_ORDER[stage_floor]:
            errors.append(
                f"{pid}: risk_class {risk} is below the stage risk floor {stage_floor}")
        policy = p.get("network_policy")
        if not isinstance(policy, str) or not (policy == "none"
                                               or policy.startswith("allowlist:")):
            errors.append(
                f"{pid}: invalid network_policy {policy!r}"
                f" (expected 'none' or 'allowlist:<id>')")
        deps = p.get("depends_on") or []
        if not isinstance(deps, list):
            errors.append(f"{pid}: depends_on must be a list")
            deps = []
        deps_of[pid] = deps

    # depends_on must reference existing or same-wave packets (VOL-09 §1)
    known = set(existing) | {pid for pid, _ in checked}
    for pid, _ in checked:
        for dep in deps_of[pid]:
            if dep not in known:
                errors.append(f"{pid}: depends_on references unknown packet {dep!r}")

    # DAG acyclicity: Kahn over wave + existing ids, edges include deps on
    # existing packets (VOL-09 §1/§3)
    nodes = set(known)
    indeg = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = {n: [] for n in nodes}

    def _edge(dep: str, dependent: str) -> None:
        if dep in nodes:
            adj[dep].append(dependent)
            indeg[dependent] += 1

    for pid_key, pkt in existing.items():
        if isinstance(pkt, dict):
            for dep in (pkt.get("depends_on") or []):
                _edge(dep, pid_key)
    for pid, _ in checked:
        for dep in deps_of[pid]:
            _edge(dep, pid)
    queue = [n for n in sorted(nodes) if indeg[n] == 0]
    processed = 0
    while queue:
        n = queue.pop()
        processed += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if processed != len(nodes):
        cyclic = sorted(n for n in nodes if indeg[n] > 0)
        errors.append("dependency cycle detected involving: " + ", ".join(cyclic))

    # write_scope pairwise disjointness within the wave (VOL-09 §3): overlap
    # is allowed only when one packet explicitly depends_on the other.
    for i in range(len(checked)):
        for j in range(i + 1, len(checked)):
            a_id, a = checked[i]
            b_id, b = checked[j]
            sa, sb = a.get("write_scope"), b.get("write_scope")
            if not (isinstance(sa, list) and isinstance(sb, list)):
                continue  # malformed scopes already reported above
            overlap = sorted(set(sa) & set(sb))
            if not overlap:
                continue
            if b_id not in deps_of.get(a_id, []) and a_id not in deps_of.get(b_id, []):
                errors.append(
                    f"write_scope overlap between {a_id} and {b_id} on {overlap}"
                    f" without depends_on serialization")

    return {"ok": not errors, "errors": errors}


# ----------------------------------------------------------------- fencing


def accept_result(current: dict, result: dict) -> tuple[bool, str]:
    """I6 hard fence (VOL-09 §2): all four conditions must hold (AND).

    A result may only be accepted when it carries the CURRENT packet identity
    — stage_revision, packet_revision and launch_id must all match — and the
    packet state expects that launch (RUNNING). Any miss returns
    ``(False, reason)`` with a specific reason: ``stale_stage_revision`` /
    ``revision_mismatch`` / ``stale_launch`` / ``not_running``.
    """
    if result.get("stage_revision") != current.get("stage_revision"):
        return (False, "stale_stage_revision")
    if result.get("packet_revision") != current.get("packet_revision"):
        return (False, "revision_mismatch")
    if result.get("launch_id") != current.get("active_launch_id"):
        return (False, "stale_launch")
    if current.get("state") != "RUNNING":
        return (False, "not_running")
    return (True, "ok")


def late_result_guard(current: dict, result: dict) -> bool:
    """True when the result's launch is no longer the packet's active launch
    (late/stale attempt, retried or superseded packet): evidence only [I7]."""
    return result.get("launch_id") != current.get("active_launch_id")


# ------------------------------------------------------------ mock backend


class MockBackend:
    """Deterministic in-memory worker backend (no real process).

    ``start`` records the launch spec and returns a fresh handle
    ("mock-1", "mock-2", ...); ``collect`` rebuilds a WorkerReport-shaped
    dict (VOL-04 §9) from that spec.
    """

    def __init__(self) -> None:
        self._specs: dict[str, dict] = {}
        self._counter = 0

    def start(self, spec: dict) -> dict:
        self._counter += 1
        handle = f"mock-{self._counter}"
        self._specs[handle] = dict(spec)
        return {"launch_id": spec["launch_id"], "handle": handle}

    def collect(self, handle: str, *, status: str = "completed") -> dict:
        spec = self._specs[handle]
        return {
            "launch_id": spec["launch_id"],
            "run_id": spec["run_id"],
            "stage_id": spec["stage_id"],
            "stage_revision": spec["stage_revision"],
            "packet_id": spec["packet_id"],
            "packet_revision": spec["packet_revision"],
            "attempt": spec["attempt"],
            "status": status,
            "final_summary_ref": None,
            "delta_manifest_ref": None,
            "terminal_marker_seen": True,
        }


# ------------------------------------------------------------- row helpers


def _packet_row(store, run_id: str, stage_id: str, packet_id: str) -> dict:
    row = store.conn.execute(
        "SELECT * FROM packets WHERE run_id=? AND stage_id=? AND packet_id=?",
        (run_id, stage_id, packet_id)).fetchone()
    if row is None:
        raise ValueError(f"packet {run_id}/{stage_id}/{packet_id} not found")
    return dict(row)


def _row_to_packet(row: dict) -> dict:
    """Rebuild a wave-proposal-shaped packet dict from a packets table row."""

    def _loads(v, default):
        try:
            return json.loads(v) if v else default
        except (TypeError, ValueError):
            return default

    return {
        "packet_id": row["packet_id"],
        "packet_revision": row["packet_revision"],
        "stage_revision": row["stage_revision"],
        "state": row["state"],
        "goal": row["goal"],
        "write_scope": _loads(row["write_scope_json"], []),
        "acceptance": _loads(row["acceptance_json"], []),
        "depends_on": _loads(row["deps_json"], []),
        "risk_class": row["risk_class"],
        "network_policy": row["network_policy"],
    }


# --------------------------------------------------------------- wave run


def run_wave(store, run_id: str, stage_id: str, stage_revision: int,
             packets: list[dict], backend, *, wave: str = "W1") -> dict:
    """Launch and collect one wave (VOL-09 §1/§2/§5) against ``backend``.

    Host-side final ruling first: the stage must be EXECUTING at exactly
    ``stage_revision``, the base recorded at stage creation must be clean
    [I37], and the proposal must pass ``validate_wave`` (packet risk_class
    must be >= the stage's operative risk, ``risk_effective``). Any failure
    raises ValueError and writes nothing (fail-closed, I4).

    Per packet: fresh attempt-1 launch with a brand-new launch_id and
    workspace [I34]; packet PENDING -> RUNNING, launch intent INTENDED ->
    BOUND -> RUNNING; then the collected report is fenced by ``accept_result``
    (I6). Returns ``{"wave": ..., "reported": N, "rejected": [...]}``.
    """
    stage = get_stage(store, run_id, stage_id)
    if stage is None:
        raise ValueError(f"unknown stage {run_id}/{stage_id}")
    if stage["stage_revision"] != stage_revision:
        raise ValueError(
            f"stage_revision mismatch for {stage_id}: wave proposed against "
            f"{stage_revision}, stage is at {stage['stage_revision']}")
    if stage["state"] != "EXECUTING":
        raise ValueError(
            f"stage {stage_id} is {stage['state']}, not EXECUTING (VOL-09 §1)")
    clean, why = check_stage_base(stage["canonical_dirty_digest"])
    if not clean:
        raise ValueError(
            f"wave blocked for {stage_id}: {why} (dirty canonical base, I37)")
    existing = {
        row["packet_id"]: _row_to_packet(dict(row))
        for row in store.conn.execute(
            "SELECT * FROM packets WHERE run_id=? AND stage_id=?",
            (run_id, stage_id))}
    verdict = validate_wave(packets, existing,
                            stage_floor=stage["risk_effective"])
    if not verdict["ok"]:
        raise ValueError("wave validation failed: " + "; ".join(verdict["errors"]))

    launched: list[dict] = []
    for p in packets:
        packet_id = p["packet_id"]
        launch_id = ids.new_launch_id()
        workspace_id = f"{stage_id}/{packet_id}/{launch_id}"
        now = ids.now_iso()
        # (1) durable intent: packet PENDING, attempt 1, launch INTENDED
        with store.mutation():
            store.conn.execute(
                "INSERT INTO packets(run_id, stage_id, stage_revision, packet_id,"
                " packet_revision, goal, write_scope_json, acceptance_json,"
                " constraints_json, deps_json, resource_scope_json,"
                " evidence_refs_json, risk_class, network_policy, max_turns,"
                " state, active_launch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, stage_id, stage_revision, packet_id, 1,
                 p.get("goal") or "",
                 json.dumps(p.get("write_scope") or [], ensure_ascii=False),
                 json.dumps(p.get("acceptance") or [], ensure_ascii=False),
                 _json_or_none(p.get("constraints")),
                 json.dumps(p.get("depends_on") or [], ensure_ascii=False),
                 _json_or_none(p.get("resource_scope")),
                 _json_or_none(p.get("evidence_refs")),
                 p.get("risk_class") or "NORMAL",
                 p.get("network_policy") or "none",
                 p.get("max_turns"),
                 "PENDING", None))
            store._event("packet_created",
                         {"packet_id": packet_id, "state": "PENDING"},
                         run_id=run_id, stage_id=stage_id)
            store.conn.execute(
                "INSERT INTO attempts(run_id, stage_id, packet_id,"
                " packet_revision, attempt, created_at, note)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, stage_id, packet_id, 1, 1, now,
                 f"wave {wave} attempt 1"))
            store._event("attempt_created",
                         {"packet_id": packet_id, "attempt": 1},
                         run_id=run_id, stage_id=stage_id)
            store.conn.execute(
                "INSERT INTO launches(launch_id, run_id, stage_id, stage_revision,"
                " packet_id, packet_revision, attempt, workspace_id, backend,"
                " intent_state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (launch_id, run_id, stage_id, stage_revision, packet_id, 1, 1,
                 workspace_id, "mock", "INTENDED", now))
            store._event("launch_intended",
                         {"packet_id": packet_id, "launch_id": launch_id,
                          "workspace_id": workspace_id},
                         run_id=run_id, stage_id=stage_id)
        # (2) spawn the worker, then record BOUND -> RUNNING + packet RUNNING
        spec = {
            "run_id": run_id, "stage_id": stage_id,
            "stage_revision": stage_revision, "packet_id": packet_id,
            "packet_revision": 1, "attempt": 1, "launch_id": launch_id,
            "goal": p.get("goal") or "",
            "write_scope": p.get("write_scope") or [],
            "acceptance": p.get("acceptance") or [],
            "constraints": p.get("constraints") or [],
            "network_policy": p.get("network_policy") or "none",
            "max_turns": p.get("max_turns"),
        }
        started = backend.start(spec)
        with store.mutation():
            store.conn.execute(
                "UPDATE launches SET intent_state='BOUND', backend_handle=?"
                " WHERE launch_id=?",
                (started.get("handle"), launch_id))
            store._event("launch_bound",
                         {"packet_id": packet_id, "launch_id": launch_id,
                          "backend_handle": started.get("handle")},
                         run_id=run_id, stage_id=stage_id)
            store.conn.execute(
                "UPDATE launches SET intent_state='RUNNING' WHERE launch_id=?",
                (launch_id,))
            store._event("launch_running",
                         {"packet_id": packet_id, "launch_id": launch_id},
                         run_id=run_id, stage_id=stage_id)
            store.conn.execute(
                "UPDATE packets SET state='RUNNING', active_launch_id=?"
                " WHERE run_id=? AND stage_id=? AND packet_id=? AND state='PENDING'",
                (launch_id, run_id, stage_id, packet_id))
            store._event("packet_running",
                         {"packet_id": packet_id, "launch_id": launch_id,
                          "state": "RUNNING"},
                         run_id=run_id, stage_id=stage_id)
        launched.append({"packet_id": packet_id, "launch_id": launch_id,
                         "handle": started.get("handle")})

    # (3) collect every report, fenced by I6 before anything moves
    reported = 0
    rejected: list[dict] = []
    for item in launched:
        report = backend.collect(item["handle"])
        with store.mutation():
            current = _packet_row(store, run_id, stage_id, item["packet_id"])
            ok, reason = accept_result(current, report)
            if ok:
                store.conn.execute(
                    "UPDATE packets SET state='REPORTED'"
                    " WHERE run_id=? AND stage_id=? AND packet_id=?"
                    " AND state='RUNNING' AND active_launch_id=?",
                    (run_id, stage_id, item["packet_id"], item["launch_id"]))
                store._event("packet_reported",
                             {"packet_id": item["packet_id"],
                              "launch_id": item["launch_id"],
                              "status": report.get("status")},
                             run_id=run_id, stage_id=stage_id)
            else:
                store._event("result_rejected",
                             {"packet_id": item["packet_id"],
                              "launch_id": report.get("launch_id"),
                              "reason": reason},
                             run_id=run_id, stage_id=stage_id)
        if ok:
            reported += 1
        else:
            rejected.append({"packet_id": item["packet_id"],
                             "launch_id": report.get("launch_id"),
                             "reason": reason})
    with store.mutation():
        store._event("wave_completed",
                     {"wave": wave, "reported": reported, "rejected": rejected},
                     run_id=run_id, stage_id=stage_id)
    return {"wave": wave, "reported": reported, "rejected": rejected}


# --------------------------------------------------------------- supersede


def supersede_stage_revision(store, run_id: str, stage_id: str,
                              new_revision: int) -> dict:
    """Bump stages.stage_revision (VOL-09 §2/§8, VOL-08 §1).

    Every packet of an older revision is marked SUPERSEDED with its
    active_launch_id cleared in the same transaction: old-revision results
    immediately lose integration rights and only ever become H0 evidence
    [I7]. Returns a small summary dict.
    """
    with store.mutation():
        row = store.conn.execute(
            "SELECT * FROM stages WHERE run_id=? AND stage_id=?",
            (run_id, stage_id)).fetchone()
        if row is None:
            raise ValueError(f"unknown stage {run_id}/{stage_id}")
        old_revision = row["stage_revision"]
        if (not isinstance(new_revision, int) or isinstance(new_revision, bool)
                or new_revision <= old_revision):
            raise ValueError(
                f"new_revision must be an integer greater than the current "
                f"stage_revision {old_revision}")
        store.conn.execute(
            "UPDATE stages SET stage_revision=?, updated_at=?"
            " WHERE run_id=? AND stage_id=?",
            (new_revision, ids.now_iso(), run_id, stage_id))
        store._event("stage_revision_superseded",
                     {"from": old_revision, "to": new_revision},
                     run_id=run_id, stage_id=stage_id)
        superseded: list[str] = []
        rows = store.conn.execute(
            "SELECT packet_id, stage_revision FROM packets"
            " WHERE run_id=? AND stage_id=? AND stage_revision<?"
            " ORDER BY packet_id",
            (run_id, stage_id, new_revision)).fetchall()
        for prow in rows:
            store.conn.execute(
                "UPDATE packets SET state='SUPERSEDED', active_launch_id=NULL"
                " WHERE run_id=? AND stage_id=? AND packet_id=?",
                (run_id, stage_id, prow["packet_id"]))
            store._event("packet_superseded",
                         {"packet_id": prow["packet_id"],
                          "stage_revision": prow["stage_revision"]},
                         run_id=run_id, stage_id=stage_id)
            superseded.append(prow["packet_id"])
    return {"stage_id": stage_id, "stage_revision": new_revision,
            "superseded_packets": superseded}
