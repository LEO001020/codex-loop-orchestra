"""zloop.stage — Stage pipeline: risk floors, base gate, FSM (VOL-08).

Pure state-machine logic over the S control DB (VOL-04 §3 ``stages`` table):

- deterministic host risk floors (VOL-08 §2): ``risk_effective =
  max(risk_requested, deterministic_host_floor)`` — root may raise, the host
  floor is never silently lowered; project-config extra rules can only RAISE
  the computed floor, never lower it;
- the stage-base cleanliness gate [I37] (VOL-08 §3): a first-class writable
  wave requires a provably-clean canonical base — the empty dirty digest
  means clean, anything else is dirty and blocks with ``BLOCKED_DIRTY_BASE``;
- the stage FSM guard table (VOL-08 §4).

All lifecycle mutations follow the VOL-04 §4 transaction recipe: validate
preconditions, write the events row, update the state row — inside a single
``store.mutation()``.
"""
from __future__ import annotations

from . import ids

# VOL-04 §11: risk enum, ordered LOW < NORMAL < HIGH < CRITICAL.
RISK_LEVELS: tuple[str, ...] = ("LOW", "NORMAL", "HIGH", "CRITICAL")
_LEVEL_ORDER: dict[str, int] = {name: i for i, name in enumerate(RISK_LEVELS)}

# Deterministic keyword -> floor rules (VOL-08 §2). Matching is a plain
# case-insensitive substring test on the objective slice; the resulting floor
# is the MAX over the built-in NORMAL default and every matched rule, so a
# keyword hit can only ever RAISE the floor — broader/overlapping keywords
# (e.g. "auth" inside "permission authority") are safe by construction.
RISK_FLOOR_RULES: tuple[tuple[str, str], ...] = (
    # CRITICAL: live money movement, irreversible production effects,
    # destructive operations, secret/credential/permission authority.
    ("live trading", "CRITICAL"),
    ("live order", "CRITICAL"),
    ("live withdrawal", "CRITICAL"),
    ("production deploy", "CRITICAL"),
    ("production release", "CRITICAL"),
    ("deploy to production", "CRITICAL"),
    ("release to production", "CRITICAL"),
    ("destructive", "CRITICAL"),
    ("secret", "CRITICAL"),
    ("credential", "CRITICAL"),
    ("permission authority", "CRITICAL"),
    # HIGH: auth/security boundaries, schema/data migration, CI/release
    # infrastructure, signing/packaging, fund-affecting strategy semantics,
    # dependency/supply-chain policy.
    ("auth", "HIGH"),
    ("security boundary", "HIGH"),
    ("schema migration", "HIGH"),
    ("data migration", "HIGH"),
    ("migration", "HIGH"),
    ("migrate", "HIGH"),
    ("ci pipeline", "HIGH"),
    ("release infrastructure", "HIGH"),
    ("release pipeline", "HIGH"),
    ("signing", "HIGH"),
    ("packaging", "HIGH"),
    ("trading strategy", "HIGH"),
    ("strategy semantics", "HIGH"),
    ("supply chain", "HIGH"),
    ("supply-chain", "HIGH"),
    ("dependency", "HIGH"),
)


def _extra_rules(extra_rules) -> list[tuple[str, str]]:
    """Validate project-config extra rules: (pattern, floor) tuples only."""
    if extra_rules is None:
        return []
    if isinstance(extra_rules, (str, bytes)):
        raise ValueError("extra_rules must be an iterable of (pattern, floor) tuples")
    rules: list[tuple[str, str]] = []
    for rule in extra_rules:
        if (not isinstance(rule, (tuple, list)) or len(rule) != 2
                or not isinstance(rule[0], str) or not rule[0]
                or rule[1] not in _LEVEL_ORDER):
            raise ValueError(f"invalid extra floor rule: {rule!r}")
        rules.append((rule[0], rule[1]))
    return rules


def compute_risk_floor(objective_slice: str, extra_rules: list | None = None) -> str:
    """Deterministic host floor for an objective slice (VOL-08 §2).

    Case-insensitive keyword substring match; the result is the max of the
    built-in NORMAL default and every matched rule (built-in table plus
    ``extra_rules`` from project config). Because the result is a max, extra
    rules can only RAISE the floor, never lower it.
    """
    text = objective_slice.casefold() if isinstance(objective_slice, str) else ""
    rank = _LEVEL_ORDER["NORMAL"]
    for pattern, level in RISK_FLOOR_RULES + tuple(_extra_rules(extra_rules)):
        if pattern.casefold() in text:
            rank = max(rank, _LEVEL_ORDER[level])
    return RISK_LEVELS[rank]


def risk_effective(requested: str, floor: str) -> str:
    """``risk_effective = max(risk_requested, host_floor)`` (VOL-08 §2).

    Raises ValueError when either level is outside the four-level enum.
    """
    for level in (requested, floor):
        if level not in _LEVEL_ORDER:
            raise ValueError(
                f"unknown risk level {level!r}; expected one of {RISK_LEVELS}")
    return (requested if _LEVEL_ORDER[requested] >= _LEVEL_ORDER[floor]
            else floor)


def check_stage_base(dirty_digest: str) -> tuple[bool, str]:
    """[I37] (VOL-08 §3): writable waves need a provably-clean base.

    The empty digest means the canonical worktree is clean; any non-empty
    digest (or non-string value) is treated as dirty — fail-closed.
    """
    if dirty_digest == "":
        return (True, "ok")
    return (False, "BLOCKED_DIRTY_BASE")


# Stage FSM guard table (VOL-08 §4). "any -> CANCELLED" covers explicit
# user/root cancellation from every non-terminal state; CLOSED and CANCELLED
# are terminal — late findings open a NEW remediation stage (VOL-08 §7).
STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "PLANNING": frozenset({"EXECUTING", "CANCELLED"}),
    "EXECUTING": frozenset({"EXECUTING", "STAGED", "BLOCKED", "CANCELLED"}),
    "STAGED": frozenset({"PROMOTING", "BLOCKED", "CANCELLED"}),
    "PROMOTING": frozenset({"PROMOTED", "BLOCKED", "CANCELLED"}),
    "PROMOTED": frozenset({"CLOSED", "CANCELLED"}),
    "BLOCKED": frozenset({"CANCELLED"}),
    "CLOSED": frozenset(),
    "CANCELLED": frozenset(),
}


def get_stage(store, run_id: str, stage_id: str) -> dict | None:
    """Full stages row for (run_id, stage_id), or None."""
    row = store.conn.execute(
        "SELECT * FROM stages WHERE run_id=? AND stage_id=?",
        (run_id, stage_id)).fetchone()
    return dict(row) if row else None


def create_stage(store, run_id: str, objective_slice: str, risk_requested: str, *,
                 expected_head: str, dirty_digest: str,
                 stage_base_ref: str, stage_base_tree: str) -> dict:
    """Create the next stage of a run in PLANNING state (VOL-08 §1/§3).

    Records the locked stage base (expected canonical head, dirty digest,
    private base ref + tree), computes the deterministic risk floor and the
    effective risk, and inserts the row plus its audit event in one mutation.
    Returns the full new stages row as a dict.
    """
    if store.run(run_id) is None:
        raise ValueError(f"unknown run {run_id!r}")
    if not isinstance(objective_slice, str) or not objective_slice.strip():
        raise ValueError("objective_slice must be a non-empty string")
    if not isinstance(dirty_digest, str):
        raise ValueError("dirty_digest must be a string ('' means clean)")
    for name, value in (("expected_head", expected_head),
                        ("stage_base_ref", stage_base_ref),
                        ("stage_base_tree", stage_base_tree)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
    floor = compute_risk_floor(objective_slice)
    effective = risk_effective(risk_requested, floor)  # validates risk_requested
    now = ids.now_iso()
    with store.mutation():
        nums = [ids.parse_int_suffix("S", r["stage_id"]) or 0
                for r in store.conn.execute(
                    "SELECT stage_id FROM stages WHERE run_id=?", (run_id,))]
        stage_id = ids.fmt_stage((max(nums) + 1) if nums else 1)
        store.conn.execute(
            "INSERT INTO stages(run_id, stage_id, stage_revision, objective_slice,"
            " risk_requested, risk_floor, risk_effective, expected_canonical_head,"
            " canonical_dirty_digest, stage_base_ref, stage_base_tree,"
            " current_snapshot, state, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, stage_id, 1, objective_slice, risk_requested, floor,
             effective, expected_head, dirty_digest, stage_base_ref,
             stage_base_tree, None, "PLANNING", now, now))
        store._event("stage_created",
                     {"stage_id": stage_id, "state": "PLANNING",
                      "risk_requested": risk_requested, "risk_floor": floor,
                      "risk_effective": effective},
                     run_id=run_id, stage_id=stage_id)
        row = store.conn.execute(
            "SELECT * FROM stages WHERE run_id=? AND stage_id=?",
            (run_id, stage_id)).fetchone()
    return dict(row)


def transition_stage(store, run_id: str, stage_id: str, to_state: str, *,
                      expected_fields: dict | None = None) -> dict:
    """Move a stage along the FSM (VOL-08 §4) inside one mutation.

    ``expected_fields`` is an optional compare-and-set precondition on the
    current row (e.g. ``{"stage_revision": 3}``, ``{"state": "EXECUTING"}``);
    any mismatch aborts before a single write. Illegal transitions raise
    ValueError (with the reason) and change nothing. Returns the updated row.
    """
    if to_state not in STAGE_TRANSITIONS:
        raise ValueError(f"unknown stage state {to_state!r}")
    with store.mutation():
        row = store.conn.execute(
            "SELECT * FROM stages WHERE run_id=? AND stage_id=?",
            (run_id, stage_id)).fetchone()
        if row is None:
            raise ValueError(f"unknown stage {run_id}/{stage_id}")
        current = dict(row)
        for field, expected in (expected_fields or {}).items():
            if current.get(field) != expected:
                raise ValueError(
                    f"stage {stage_id}: expected {field}={expected!r}, "
                    f"found {current.get(field)!r}")
        if to_state not in STAGE_TRANSITIONS[current["state"]]:
            raise ValueError(
                f"illegal stage transition for {stage_id}: "
                f"{current['state']} -> {to_state} is not allowed by the "
                f"VOL-08 §4 guard table")
        store.conn.execute(
            "UPDATE stages SET state=?, updated_at=? WHERE run_id=? AND stage_id=?",
            (to_state, ids.now_iso(), run_id, stage_id))
        store._event("stage_transition",
                     {"from": current["state"], "to": to_state},
                     run_id=run_id, stage_id=stage_id)
        new_row = store.conn.execute(
            "SELECT * FROM stages WHERE run_id=? AND stage_id=?",
            (run_id, stage_id)).fetchone()
    return dict(new_row)
