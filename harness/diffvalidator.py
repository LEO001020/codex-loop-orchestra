#!/usr/bin/env python3
# ============================================================================
# diffvalidator.py — L0 mechanical acceptance: diff validation leg of the trio
# ----------------------------------------------------------------------------
# Purpose : Zero-model mechanical acceptance (L0). Asserts that a candidate
#           diff is (a) non-empty, (b) strictly a subset of the packet's
#           authorized_paths (changes ⊆ authorized_paths), and (c) does not
#           decrease the test count relative to the frozen oracle. Also
#           enforces the packet's min_test_count constraint and optional
#           diff-line budget from constraints. Power semantics: this layer
#           can only PASS (forward to next gate) or FAIL — it can never
#           release anything to publication (release is human-triggered, §3.5).
# Input   : --packet   packets/<id>.json   (4-field packet: packet_id, goal,
#                                           authorized_paths, acceptance,
#                                           constraints)
#           --diff     unified diff file (git diff output of the worktree)
#           --oracle   frozen oracle JSON (from acceptance_replay.sh freeze):
#                      {"test_count": int, "commands": [...], ...}
#           --candidate-test-count  int (test count measured on the candidate
#                      tree by acceptance_replay.sh; omit to skip check c)
# Output  : stdout: "PASS <one-line stats>" or "FAIL <one-line reason+stats>"
#           stderr: itemized violations (fail-visible, never silent)
#           exit 0 = PASS, exit 1 = FAIL, exit 2 = usage/input error
# Lines   : 185
# ============================================================================
import argparse
import json
import os
import re
import sys

DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$")
RENAME_FROM_RE = re.compile(r"^rename from (?P<p>.+)$")
RENAME_TO_RE = re.compile(r"^rename to (?P<p>.+)$")
MINUS_FILE_RE = re.compile(r"^--- (?:a/)?(?P<p>\S+)")
PLUS_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(?P<p>\S+)")
MIN_TEST_RE = re.compile(r"^min_test_count\s*>=\s*(\d+)$")
DIFF_BUDGET_RE = re.compile(r"diff\s*(?:≤|<=)\s*(\d+)\s*lines", re.IGNORECASE)


def fail_usage(msg):
    print("usage error: %s" % msg, file=sys.stderr)
    sys.exit(2)


def load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        fail_usage("cannot read %s %r: %s" % (what, path, exc))


def parse_diff(text):
    """Return (touched_paths:set, added:int, removed:int, hunks:int)."""
    touched, added, removed, hunks = set(), 0, 0, 0
    for line in text.splitlines():
        m = DIFF_HEADER_RE.match(line)
        if m:
            touched.add(m.group("a"))
            touched.add(m.group("b"))
            continue
        m = RENAME_FROM_RE.match(line) or RENAME_TO_RE.match(line)
        if m:
            touched.add(m.group("p"))
            continue
        if line.startswith("--- "):
            m = MINUS_FILE_RE.match(line)
            if m and m.group("p") != "/dev/null":
                touched.add(m.group("p"))
            continue
        if line.startswith("+++ "):
            m = PLUS_FILE_RE.match(line)
            if m and m.group("p") != "/dev/null":
                touched.add(m.group("p"))
            continue
        if line.startswith("@@"):
            hunks += 1
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return touched, added, removed, hunks


def path_authorized(path, authorized_paths):
    """True iff normalized path is under one of the authorized entries.
    Entries ending in '/' are directory prefixes; others are exact files."""
    norm = os.path.normpath(path)
    if norm.startswith("..") or os.path.isabs(norm):
        return False  # escape attempts are never authorized
    for auth in authorized_paths:
        a = os.path.normpath(auth)
        if auth.endswith("/") or auth.endswith(os.sep):
            if norm == a or norm.startswith(a + os.sep):
                return True
        elif norm == a:
            return True
    return False


def packet_min_test_count(packet):
    for item in packet.get("acceptance", []):
        m = MIN_TEST_RE.match(str(item).strip())
        if m:
            return int(m.group(1))
    return None


def packet_diff_budget(packet):
    for item in packet.get("constraints", []):
        m = DIFF_BUDGET_RE.search(str(item))
        if m:
            return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser(description="L0 mechanical diff validator")
    ap.add_argument("--packet", required=True, help="packet JSON path")
    ap.add_argument("--diff", required=True, help="unified diff file path")
    ap.add_argument("--oracle", required=True, help="frozen oracle JSON path")
    ap.add_argument("--candidate-test-count", type=int, default=None,
                    help="test count measured on candidate tree")
    args = ap.parse_args()

    packet = load_json(args.packet, "packet")
    oracle = load_json(args.oracle, "oracle")
    try:
        with open(args.diff, "r", encoding="utf-8", errors="replace") as fh:
            diff_text = fh.read()
    except OSError as exc:
        fail_usage("cannot read diff %r: %s" % (args.diff, exc))

    authorized = packet.get("authorized_paths") or []
    violations = []

    # --- Check 1: empty diff rejection --------------------------------------
    touched, added, removed, hunks = parse_diff(diff_text)
    if not diff_text.strip() or (not touched and hunks == 0):
        violations.append("EMPTY_DIFF: candidate diff contains no changes")

    # --- Check 2: diff subset assertion (changes ⊆ authorized_paths) --------
    out_of_bounds = sorted(p for p in touched
                           if not path_authorized(p, authorized))
    if out_of_bounds:
        violations.append("PATH_BOUNDARY: unauthorized paths touched: %s"
                          % ", ".join(out_of_bounds))

    # --- Check 3: test count must not decrease vs frozen oracle -------------
    oracle_tc = oracle.get("test_count")
    cand_tc = args.candidate_test_count
    if oracle_tc is not None and cand_tc is not None and cand_tc < oracle_tc:
        violations.append("TEST_COUNT_DECREASE: oracle=%d candidate=%d"
                          % (oracle_tc, cand_tc))

    # --- Check 4: packet min_test_count constraint --------------------------
    min_tc = packet_min_test_count(packet)
    if min_tc is not None and cand_tc is not None and cand_tc < min_tc:
        violations.append("MIN_TEST_COUNT: required>=%d candidate=%d"
                          % (min_tc, cand_tc))

    # --- Check 5: diff line budget from constraints (if declared) -----------
    budget = packet_diff_budget(packet)
    diff_lines = added + removed
    if budget is not None and diff_lines > budget:
        violations.append("DIFF_BUDGET: %d changed lines > budget %d"
                          % (diff_lines, budget))

    stats = ("packet=%s files=%d +%d/-%d hunks=%d tests=%s->%s"
             % (packet.get("packet_id", "?"), len(touched), added, removed,
                hunks, oracle_tc, cand_tc))

    if violations:
        for v in violations:
            print(v, file=sys.stderr)  # fail-visible, itemized
        print("FAIL [%s] %s" % (violations[0].split(":", 1)[0], stats))
        sys.exit(1)
    print("PASS %s" % stats)
    sys.exit(0)


if __name__ == "__main__":
    main()
