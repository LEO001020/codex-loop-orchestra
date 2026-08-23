#!/usr/bin/env python3
"""model_token_share_v2.py — turn-scoped, windowed, hysteresis-driven meter.

Replaces ``metering/model_token_share.py`` (P0-4,
``phase1b_codebase_analysis.md``). The v1 meter classified maintenance by a
session-wide substring sweep, had no 5-hour window, no hysteresis, attributed
roles from legacy model aliases, and refreshed weekly — 168× slower than the
control window. Every one of those defects is mechanically fixed here
(``phase2_architecture_design.md`` §2.4):

1. **Turn-scoped ledger** — every model/tool call appends one record to
   ``data/usage/token_ledger.ndjsonl`` (append-only, semantic idempotency key
   per record). ``class ∈ {production, maintenance, communication, retry}`` is
   decided *per turn*: a turn is maintenance iff **its own user-turn text**
   matches a marker — never inherited from elsewhere in the rollout, and
   children never classify from inherited context.
2. **Per-agent attribution by dispatch record** — the meter joins on
   ``data/usage/run_role_map.json`` (``run_id → {role, model, packet_id}``,
   written by dispatch at spawn). Model-string fallback survives only for
   pre-F2 history; **legacy aliases land in a quarantined ``legacy`` bucket**
   so K3's band is never polluted by Sol-family tokens.
3. **Windows** — ``rolling_1h``, ``rolling_5h`` (primary), ``rolling_24h``,
   ``rolling_7d``, ``cumulative``; each report carries a frozen input manifest
   (file list + sha256) so baselines replay exactly.
4. **Denominator floor** — windows with production effective tokens below
   ``minimum_denominator`` (2M) report ``INSUFFICIENT_DATA`` and never actuate.
5. **Hysteresis controller** — enter HIGH at share > ``enter_high_sol_share``
   for ``enter_samples`` consecutive samples, leave below
   ``leave_high_sol_share`` for ``leave_samples`` samples. Kills the
   bang-bang BLOCK↔drain cycle (auditor 07).
6. **Per-root gating** — any single root above ``critical_1h_share`` raises a
   root-scoped signal the governor enforces.
7. **Event-driven refresh** — :meth:`MeterV2.refresh` is called from post-turn
   / post-report hooks with a ≥60s debounce; a report older than
   ``stale_after_s`` self-labels ``STALE`` and the governor treats STALE as
   fail-closed for non-planning turns (never "two truths" again).

Model names come from ``config/orchestration_policy_v2.toml [models]`` —
**nothing is hardcoded** (user constraint: Sol is currently
the currently configured execution model; do not bake in any model string).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field as _dc_field
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Mapping, Sequence

__all__ = [
    "TOKEN_CLASSES",
    "MAINTENANCE_MARKERS",
    "LedgerRecord",
    "TokenLedger",
    "HysteresisController",
    "WindowReport",
    "MeterV2",
    "classify_turn",
    "load_policy_models",
]

LOG = logging.getLogger("model_token_share_v2")

TOKEN_CLASSES: Final[frozenset[str]] = frozenset(
    {"production", "maintenance", "communication", "retry"})

# Markers apply ONLY to the record's own user-turn text (turn-scoped —
# the exact algorithm demanded by Phase 1b F1.1).
MAINTENANCE_MARKERS: Final[tuple[str, ...]] = (
    "smoke: reply exactly OK",
    "loop-install",
    "Stability probe:",
    "K3_OK",
    "LOOP_MAINTENANCE",
)

_WINDOWS: Final[dict[str, float | None]] = {
    "rolling_1h": 3600.0,
    "rolling_5h": 18000.0,       # PRIMARY control window
    "rolling_24h": 86400.0,
    "rolling_7d": 604800.0,
    "cumulative": None,
}


def classify_turn(user_turn_text: str | None) -> str:
    """Classify one turn from **its own** user-turn text (never inherited).

    Returns ``"maintenance"`` iff the turn's own user text carries a marker;
    otherwise ``"production"``. A production root *quoting* ``K3_OK`` in an
    assistant message stays production (§2.4 AC1) because assistant text is
    never passed here.
    """
    if user_turn_text:
        for marker in MAINTENANCE_MARKERS:
            if marker in user_turn_text:
                return "maintenance"
    return "production"


def load_policy_models(policy: Mapping[str, Any]) -> dict[str, str]:
    """Extract ``model string → family`` from policy ``[models]``.

    Families: ``sol | v4 | k3 | legacy``. Raises ``RuntimeError`` when a pin
    is missing — the meter must fail closed rather than guess attribution.
    """
    models = policy.get("models", {})
    mapping: dict[str, str] = {}
    for key, family in (("sol_model", "sol"), ("v4_model", "v4"),
                        ("k3_model", "k3")):
        name = str(models.get(key, "")).strip()
        if not name:
            raise RuntimeError(
                "policy [models].%s missing — meter attribution requires "
                "explicit model pins (fail closed)" % key)
        normalized = name.lower()
        prior = mapping.get(normalized)
        mapping[normalized] = family if prior in (None, family) else "shared"
    for alias in models.get("legacy_aliases", []):
        normalized = str(alias).lower()
        mapping.setdefault(normalized, "legacy")
    # All selectable ordinary-execution profiles are one semantic family.
    # Keeping inactive profile aliases mapped prevents still-in-window traffic
    # from becoming ``unknown`` after a temporary provider switch.
    for alias in models.get("execution_aliases", []):
        normalized = str(alias).lower()
        mapping.setdefault(normalized, "v4")
    return mapping


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerRecord:
    """One turn-scoped token event (append-only ledger row)."""

    ts: float
    task_id: str
    root_session_id: str
    agent_id: str
    role: str
    model: str
    step_id: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    latency_ms: int | None = None
    retry_reason: str | None = None
    token_class: str = "production"

    @property
    def total_tokens(self) -> int:
        """Raw total for this record."""
        return (self.input_tokens + self.output_tokens
                + self.reasoning_output_tokens)

    @property
    def effective_tokens(self) -> int:
        """Effective = total − cached input (the primary control caliber)."""
        return max(0, self.total_tokens - self.cached_input_tokens)

    def idem_key(self) -> str:
        """Semantic idempotency key (V10 k3IdemKey discipline)."""
        basis = "|".join((self.root_session_id, self.agent_id, self.step_id,
                          self.task_id, self.model, "%d" % self.total_tokens))
        return "tok:%s" % hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable ledger row (token counts + ids only — no content:
        the P1-4 privacy discipline)."""
        return {
            "idem_key": self.idem_key(), "ts": self.ts,
            "task_id": self.task_id,
            "root_session_id": self.root_session_id,
            "agent_id": self.agent_id, "role": self.role,
            "model": self.model, "step_id": self.step_id,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "latency_ms": self.latency_ms,
            "retry_reason": self.retry_reason,
            "class": self.token_class,
        }


class TokenLedger:
    """Append-only, idempotent, locked ndjson token ledger (Layer 0)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, record: LedgerRecord) -> bool:
        """Append *record*; duplicate idempotency keys are dropped.

        Returns ``True`` when appended, ``False`` on dedup. The dedup scan is
        an on-read index (keys are 24-hex prefixes; the ledger is the truth).
        """
        if record.token_class not in TOKEN_CLASSES:
            raise ValueError("illegal token class %r" % record.token_class)
        key = record.idem_key()
        with self._lock:
            if key in self._existing_keys():
                LOG.debug("ledger dedup: %s", key)
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return True

    def _existing_keys(self) -> set[str]:
        keys: set[str] = set()
        corrupt = 0
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        keys.add(json.loads(line)["idem_key"])
                    except (ValueError, KeyError):
                        corrupt += 1
                        continue
        except OSError:
            pass
        if corrupt:
            LOG.warning("token ledger contains %d corrupt row(s): %s",
                        corrupt, self.path)
        return keys

    def read(self) -> list[dict[str, Any]]:
        """All parseable rows, deduplicated by idempotency key."""
        rows: dict[str, dict[str, Any]] = {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                        rows[str(obj.get("idem_key", len(rows)))] = obj
                    except ValueError:
                        continue
        except OSError:
            pass
        return list(rows.values())


# ---------------------------------------------------------------------------
# Hysteresis
# ---------------------------------------------------------------------------


class HysteresisController:
    """Two-threshold, two-sample budget-state controller (no bang-bang).

    Enter ``HIGH`` after ``enter_samples`` consecutive samples strictly above
    ``enter_high``; return to ``NORMAL`` after ``leave_samples`` consecutive
    samples strictly below ``leave_high``. Anything in between holds state —
    the sequence 0.26, 0.26, 0.23, 0.21, 0.21 goes HIGH at sample 2 and exits
    at sample 5, with no flapping (§2.4 AC3).
    """

    def __init__(self, enter_high: float = 0.25, enter_samples: int = 2,
                 leave_high: float = 0.22, leave_samples: int = 2,
                 state: str = "NORMAL") -> None:
        self.enter_high = enter_high
        self.enter_samples = enter_samples
        self.leave_high = leave_high
        self.leave_samples = leave_samples
        self.state = state
        self._above = 0
        self._below = 0

    def sample(self, share: float) -> str:
        """Feed one share sample; returns the (possibly new) state."""
        if self.state == "NORMAL":
            if share > self.enter_high:
                self._above += 1
                if self._above >= self.enter_samples:
                    self.state, self._above, self._below = "HIGH", 0, 0
                    LOG.warning("hysteresis: NORMAL -> HIGH at share=%.4f", share)
            else:
                self._above = 0
        else:  # HIGH
            if share < self.leave_high:
                self._below += 1
                if self._below >= self.leave_samples:
                    self.state, self._above, self._below = "NORMAL", 0, 0
                    LOG.info("hysteresis: HIGH -> NORMAL at share=%.4f", share)
            else:
                self._below = 0
        return self.state

    def to_dict(self) -> dict[str, Any]:
        """Persistable controller state."""
        return {"state": self.state, "above": self._above,
                "below": self._below}

    @classmethod
    def from_policy(cls, policy: Mapping[str, Any],
                    persisted: Mapping[str, Any] | None = None
                    ) -> "HysteresisController":
        """Build from ``[hysteresis]`` policy keys + optional persisted state."""
        h = policy.get("hysteresis", {})
        ctl = cls(enter_high=float(h.get("enter_high_sol_share", 0.25)),
                  enter_samples=int(h.get("enter_samples", 2)),
                  leave_high=float(h.get("leave_high_sol_share", 0.22)),
                  leave_samples=int(h.get("leave_samples", 2)))
        if persisted:
            ctl.state = str(persisted.get("state", "NORMAL"))
            ctl._above = int(persisted.get("above", 0))
            ctl._below = int(persisted.get("below", 0))
        return ctl


# ---------------------------------------------------------------------------
# Meter
# ---------------------------------------------------------------------------


@dataclass
class WindowReport:
    """Share report for one window."""

    window: str
    status: str                       # OK | INSUFFICIENT_DATA
    production_effective: int = 0
    shares: dict[str, float] = _dc_field(default_factory=dict)
    per_root: dict[str, float] = _dc_field(default_factory=dict)
    critical_roots: list[str] = _dc_field(default_factory=list)
    by_class: dict[str, int] = _dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"window": self.window, "status": self.status,
                "production_effective_tokens": self.production_effective,
                "shares_effective": self.shares, "per_root": self.per_root,
                "sol_share_effective": self.shares.get("sol", 0.0),
                "k3_share_effective": self.shares.get("k3", 0.0),
                "v4_share_effective": self.shares.get("v4", 0.0),
                "critical_roots": self.critical_roots,
                "by_class": self.by_class}


class MeterV2:
    """Windowed, per-agent, hysteresis-governed token-share meter (Layer 1).

    Args:
        root: LOOP root (``data/usage/`` lives beneath it).
        policy: parsed ``orchestration_policy_v2.toml``.
        clock: injectable time source.
    """

    def __init__(self, root: Path | str, policy: Mapping[str, Any],
                 clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root).resolve()
        self.policy = policy
        self._clock = clock
        tokens = policy.get("tokens", {})
        self.minimum_denominator = int(tokens.get("minimum_denominator", 2_000_000))
        self.refresh_debounce_s = float(tokens.get("refresh_debounce_s", 60))
        self.stale_after_s = float(tokens.get("stale_after_s", 7200))
        hyst = policy.get("hysteresis", {})
        self.critical_1h_share = float(hyst.get("critical_1h_share", 0.35))
        self.model_family = load_policy_models(policy)
        usage = self.root / "data" / "usage"
        self.ledger = TokenLedger(usage / "token_ledger.ndjsonl")
        self.run_role_map_path = usage / "run_role_map.json"
        self.report_path = usage / "model_token_share_v2.json"
        self.controller_path = usage / "hysteresis_state.json"
        self._lock = threading.Lock()

    # -- recording (called per turn / per tool call — continuous, not weekly) --

    def record_turn(self, *, task_id: str, root_session_id: str,
                    agent_id: str, run_id: str | None, model: str,
                    step_id: str, usage: Mapping[str, int],
                    user_turn_text: str | None = None,
                    retry_reason: str | None = None,
                    latency_ms: int | None = None) -> bool:
        """Append one turn's token usage with per-turn classification.

        Role attribution: the ``run_role_map`` entry for *run_id* wins;
        model-string fallback is only for pre-F2 history. Classification uses
        **this turn's own user text** (never inherited context). Retry turns
        (``retry_reason`` set) are billed to the distinct ``retry`` class so
        retry storms are separately observable.
        """
        role = self._role_for(run_id, model)
        token_class = "retry" if retry_reason else classify_turn(user_turn_text)
        rec = LedgerRecord(
            ts=self._clock(), task_id=task_id,
            root_session_id=root_session_id, agent_id=agent_id, role=role,
            model=model, step_id=step_id,
            input_tokens=int(usage.get("input_tokens", 0)),
            cached_input_tokens=int(usage.get("cached_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            reasoning_output_tokens=int(usage.get("reasoning_output_tokens", 0)),
            latency_ms=latency_ms, retry_reason=retry_reason,
            token_class=token_class)
        return self.ledger.append(rec)

    def _role_for(self, run_id: str | None, model: str) -> str:
        role_map: dict[str, Any] = {}
        try:
            role_map = json.loads(
                self.run_role_map_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if run_id and run_id in role_map:
            return str(role_map[run_id].get("role", "unknown"))
        # fallback (pre-F2 history only): family name doubles as role bucket
        return self.model_family.get(model.lower(), "unknown")

    def family_of(self, model: str) -> str:
        """Family bucket for *model*: sol | v4 | k3 | legacy | unknown.

        ``legacy`` is quarantined — it never contributes to the k3 (or sol)
        numerator, fixing the terra→verifier mis-composition (§2.4 AC4).
        """
        return self.model_family.get(model.lower(), "unknown")

    def family_for_row(self, row: Mapping[str, Any]) -> str:
        """Attribute a ledger row by explicit role before physical model.

        Profiles may deliberately assign one physical model to both LOOP
        pools.  The role written by dispatch is then the authoritative
        semantic attribution; model lookup is only a pre-F2/history fallback.
        """
        role = str(row.get("role", "")).strip().casefold()
        if role in {"sol", "root", "root_agent"}:
            return "sol"
        if role in {"worker", "duty_officer", "executor", "scout", "v4"}:
            return "v4"
        if role in {"verifier", "reviewer", "plan_expander", "k3"}:
            return "k3"
        if role == "legacy":
            return "legacy"
        return self.family_of(str(row.get("model", "")))

    # -- computation -----------------------------------------------------------

    def compute_windows(self, now: float | None = None) -> dict[str, WindowReport]:
        """Compute every window's effective-share report from the ledger."""
        now = self._clock() if now is None else now
        rows = self.ledger.read()
        reports: dict[str, WindowReport] = {}
        for window, span in _WINDOWS.items():
            in_window = [r for r in rows
                         if span is None or (now - float(r.get("ts", 0))) <= span]
            reports[window] = self._window_report(window, in_window)
        return reports

    def _window_report(self, window: str,
                       rows: Iterable[Mapping[str, Any]]) -> WindowReport:
        eff_by_family: dict[str, int] = {}
        eff_by_root: dict[str, int] = {}
        by_class: dict[str, int] = {}
        production_total = 0
        for r in rows:
            total = (int(r.get("input_tokens", 0))
                     + int(r.get("output_tokens", 0))
                     + int(r.get("reasoning_output_tokens", 0)))
            eff = max(0, total - int(r.get("cached_input_tokens", 0)))
            cls = str(r.get("class", "production"))
            by_class[cls] = by_class.get(cls, 0) + eff
            if cls != "production":
                continue
            production_total += eff
            fam = self.family_for_row(r)
            eff_by_family[fam] = eff_by_family.get(fam, 0) + eff
            root_sid = str(r.get("root_session_id", "?"))
            eff_by_root[root_sid] = eff_by_root.get(root_sid, 0) + eff

        report = WindowReport(window=window, status="OK",
                              production_effective=production_total,
                              by_class=by_class)
        if production_total < self.minimum_denominator:
            report.status = "INSUFFICIENT_DATA"  # never actuates (§2.4.4)
        if production_total > 0:
            report.shares = {fam: round(v / production_total, 6)
                             for fam, v in sorted(eff_by_family.items())}
            report.per_root = {sid: round(v / production_total, 6)
                               for sid, v in sorted(eff_by_root.items())}
            if window == "rolling_1h" and report.status == "OK":
                report.critical_roots = [
                    sid for sid, share in report.per_root.items()
                    if share > self.critical_1h_share]
        return report

    # -- refresh / persistence (event-driven, debounced) ------------------------

    def refresh(self, force: bool = False) -> dict[str, Any] | None:
        """Regenerate the share report (post-turn hook; ≥60s debounce).

        Returns the written report, or ``None`` when debounced. The report
        embeds a frozen input manifest (ledger sha256 + row count) so any
        baseline replays exactly, plus the hysteresis state fed with the
        primary-window sample.
        """
        with self._lock:
            prior = self._read_report()
            now = self._clock()
            if (not force and prior
                    and (now - float(prior.get("generated_ts", 0)))
                    < self.refresh_debounce_s):
                return None
            windows = self.compute_windows(now)
            controller = HysteresisController.from_policy(
                self.policy, self._read_controller())
            primary = windows["rolling_5h"]
            sol_share = primary.shares.get("sol", 0.0)
            if primary.status == "OK":
                controller.sample(sol_share)
            report = {
                "schema": "codex-loop-token-share/v2",
                "generated_ts": now,
                "generated_at": now,
                "status": "FRESH",
                "primary_window": "rolling_5h",
                "sol_share_5h_effective": sol_share,
                "budget_state": controller.state,
                "windows": {k: v.to_dict() for k, v in windows.items()},
                "input_manifest": self._manifest(),
            }
            self._write_json(self.report_path, report)
            self._write_json(self.controller_path, controller.to_dict())
            return report

    def read_fresh_report(self) -> dict[str, Any]:
        """Read the persisted report; self-label ``STALE`` past the bound.

        The governor treats STALE as fail-closed for non-planning turns
        (§2.4 AC5 — never "two truths" again).
        """
        report = self._read_report()
        if not report:
            return {"status": "MISSING"}
        age = self._clock() - float(report.get("generated_ts", 0))
        if age > self.stale_after_s:
            report = dict(report)
            report["status"] = "STALE"
            report["age_s"] = round(age, 1)
        return report

    def _manifest(self) -> dict[str, Any]:
        digest = hashlib.sha256()
        count = 0
        try:
            with open(self.ledger.path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
            count = sum(1 for _ in open(self.ledger.path, encoding="utf-8"))
        except OSError:
            pass
        return {"files": [str(self.ledger.path)],
                "sha256": digest.hexdigest(), "rows": count}

    def _read_report(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _read_controller(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.controller_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write_json(path: Path, obj: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, sort_keys=True, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- budget_controller integration -----------------------------------------

    def budget_signal(self) -> dict[str, Any]:
        """Governance signal for budget_controller / root_turn_governor.

        Returns ``{status, budget_state, sol_share_5h, critical_roots,
        actuate}`` where ``actuate`` is ``True`` only for a FRESH report with
        sufficient denominator — STALE/MISSING/INSUFFICIENT_DATA must be
        treated fail-closed by the caller, never ignored.
        """
        report = self.read_fresh_report()
        primary = (report.get("windows", {}) or {}).get("rolling_5h", {})
        one_hour = (report.get("windows", {}) or {}).get("rolling_1h", {})
        return {
            "status": report.get("status", "MISSING"),
            "budget_state": report.get("budget_state", "UNKNOWN"),
            "sol_share_5h": report.get("sol_share_5h_effective"),
            "critical_roots": one_hour.get("critical_roots", []),
            "actuate": (report.get("status") == "FRESH"
                        and primary.get("status") == "OK"),
        }


def _main(argv: Sequence[str]) -> int:
    """CLI: ``refresh | report | signal`` against a LOOP root."""
    import argparse

    ap = argparse.ArgumentParser(description="token share meter v2")
    ap.add_argument("command", choices=["refresh", "report", "signal"])
    ap.add_argument("--root", default=os.environ.get("LOOP_ROOT", "."))
    ap.add_argument("--policy", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root)
    policy_path = Path(args.policy) if args.policy else (
        root / "config" / "orchestration_policy_v2.toml")
    # reuse l2_consumer's fail-closed policy loader without importing the
    # whole module at import time (keeps this file standalone).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
    from l2_consumer import load_policy  # noqa: PLC0415
    meter = MeterV2(root, load_policy(policy_path))
    if args.command == "refresh":
        out = meter.refresh(force=args.force)
        print(json.dumps(out if out is not None else {"debounced": True},
                         indent=1))
        return 0
    if args.command == "report":
        print(json.dumps(meter.read_fresh_report(), indent=1))
        return 0
    signal = meter.budget_signal()
    print(json.dumps(signal, indent=1))
    return 0 if signal["status"] == "FRESH" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))
