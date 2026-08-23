"""test_attention_budget_gate.py — mechanical enforcement of the
attention-budget invariant defined in config/global_working_agreement.md.

Gate thresholds (from GWA §Artifact attention-budget invariant):

  Default gate  : any single instruction file >= 16 KB is a FAIL.
  Hard cap      : any single instruction file > 64 KB is unconditionally FAIL.
  Aggregate cap : sum of all injected developer-context files > 256 KB FAIL.
  Prohibited    : raw logs, NDJSON/JSONL streams, generated file listings
                  (> ~20 lines), token/meter ledger records, git diff.
  Duplication   : prose block >= 200 chars verbatim in two+ files is a violation.

Not applicable to: *.py/*.sh/*.toml/*.yaml/*.json, reports/, data/,
or files whose first line is exactly <!--nogate-->.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# File sets
# ---------------------------------------------------------------------------
IMPL = Path(__file__).resolve().parent.parent
if not (IMPL / "config").is_dir():
    IMPL = Path(__file__).resolve().parents[2]

_REPO = IMPL

_INSTRUCTION_GLOBS = [
    "config/global_working_agreement.md",
    "README*.md",
    "AGENTS.md",
]


# Root-level *.md files that are NOT instruction artifacts (excluded from gate)
_ARTIFACT_MD_PATTERNS = re.compile(
    r'(?i)(search.?log|audit|report|certificate|evidence|changelog|release)',
    re.IGNORECASE,
)


def _instruction_files() -> list[Path]:
    """Collect instruction-file candidates that exist and are not nogateed.

    Root-level *.md files are included only if their name does not match
    known artifact patterns (logs, reports, certificates, etc.).
    """
    found: list[Path] = []
    for pat in _INSTRUCTION_GLOBS:
        found.extend(_REPO.glob(pat))
    for p in _REPO.glob("*.md"):
        if p not in found and not _ARTIFACT_MD_PATTERNS.search(p.name):
            found.append(p)
    out: list[Path] = []
    for p in dict.fromkeys(found):
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if lines and lines[0].strip() == "<!--nogate-->":
            continue
        out.append(p)
    return out


_KB = 1024
_DEFAULT_GATE_BYTES = 16 * _KB
_HARD_CAP_BYTES = 64 * _KB
_AGGREGATE_CAP_BYTES = 256 * _KB


# ---------------------------------------------------------------------------
# Size gate
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def instruction_files() -> list[Path]:
    return _instruction_files()


def test_no_instruction_file_exceeds_hard_cap(instruction_files):
    """No instruction file may exceed 64 KB."""
    violations = [
        (p, p.stat().st_size)
        for p in instruction_files
        if p.stat().st_size > _HARD_CAP_BYTES
    ]
    assert not violations, (
        "Instruction file(s) exceed 64 KB hard cap: %s"
        % [(str(p), sz) for p, sz in violations]
    )


def test_no_instruction_file_triggers_default_gate(instruction_files):
    """No instruction file may be >= 16 KB without a size-justification annotation.

    A file may carry an inline annotation starting with ``<!-- size-justified:``
    to acknowledge its size with a brief reason.
    """
    violations = []
    for p in instruction_files:
        sz = p.stat().st_size
        if sz >= _DEFAULT_GATE_BYTES:
            text = p.read_text(encoding="utf-8", errors="replace")
            if "<!-- size-justified:" not in text:
                violations.append((str(p), sz))
    assert not violations, (
        "Instruction file(s) >= 16 KB without size-justification annotation: %s"
        % violations
    )


def test_aggregate_injected_context_under_cap(instruction_files):
    """Aggregate of all instruction files must be <= 256 KB."""
    total = sum(p.stat().st_size for p in instruction_files)
    assert total <= _AGGREGATE_CAP_BYTES, (
        "Aggregate instruction context %d bytes exceeds 256 KB cap" % total
    )


# ---------------------------------------------------------------------------
# Prohibited content
# ---------------------------------------------------------------------------
_NDJSON_LINE_RE = re.compile(r'^\..*\}\s*$')


def _has_ndjson_block(text: str, min_lines: int = 3) -> bool:
    """True if min_lines consecutive JSON-object lines appear."""
    consecutive = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}") and len(stripped) > 5:
            consecutive += 1
            if consecutive >= min_lines:
                return True
        else:
            consecutive = 0
    return False


def _has_long_file_listing(text: str, threshold: int = 20) -> bool:
    """Detect generated file-listing blocks (many path-like lines in a row)."""
    path_like = re.compile(r'\s{0,6}[\w./\\-]+/[\w./\\-]+\s*$')
    consecutive = 0
    for line in text.splitlines():
        if path_like.match(line):
            consecutive += 1
            if consecutive > threshold:
                return True
        else:
            consecutive = 0
    return False


def _has_build_log_markers(text: str) -> bool:
    """Detect obvious build-log / test-output indicators."""
    patterns = [
        r'PASSED\s+\[\s*\d+%\]',
        r'FAILED\s+\[\s*\d+%\]',
        r'collected \d+ items?',
        r'\d+ passed,?\s+\d+ failed',
        r'\bTraceback \(most recent call last\)',
    ]
    for pat in patterns:
        if re.search(pat, text, re.MULTILINE):
            return True
    return False


def _has_recursive_dump(text: str) -> bool:
    """Detect recursive content dumps: NDJSON blocks or token ledger fields."""
    if _has_ndjson_block(text, min_lines=5):
        return True
    ledger_patterns = [
        r'"input_tokens"\s*:\s*\d+',
        r'"output_tokens"\s*:\s*\d+',
        r'"production_effective_tokens"',
    ]
    matches = sum(1 for p in ledger_patterns if re.search(p, text))
    return matches >= 2


@pytest.mark.parametrize("kind,check_fn", [
    ("ndjson_block", _has_ndjson_block),
    ("long_file_listing", _has_long_file_listing),
    ("build_log_marker", _has_build_log_markers),
    ("recursive_dump", _has_recursive_dump),
])
def test_no_prohibited_content_inline(instruction_files, kind, check_fn):
    """Prohibited content must not appear inline in instruction files."""
    violations = [
        str(p)
        for p in instruction_files
        if check_fn(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not violations, (
        "Instruction file(s) contain prohibited inline content (%s): %s"
        % (kind, violations)
    )


# ---------------------------------------------------------------------------
# Duplication gate
# ---------------------------------------------------------------------------
def _extract_prose_blocks(text: str, min_len: int = 200) -> list[str]:
    """Extract paragraph-level text blocks of at least min_len chars."""
    blocks = re.split(r'\n{2,}', text)
    return [b.strip() for b in blocks if len(b.strip()) >= min_len]


def test_no_verbatim_duplication_across_instruction_files(instruction_files):
    """Prose blocks >= 200 chars must not appear verbatim in more than one
    instruction file (duplication gate)."""
    if len(instruction_files) < 2:
        return
    block_files: dict[str, list[str]] = {}
    for p in instruction_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        for b in _extract_prose_blocks(text):
            block_files.setdefault(b, []).append(str(p))
    violations = {b: files for b, files in block_files.items() if len(files) > 1}
    assert not violations, (
        "Verbatim prose duplication across instruction files: %s"
        % [(b[:80] + "...", files) for b, files in list(violations.items())[:5]]
    )


# ---------------------------------------------------------------------------
# Routing invariant static check
# ---------------------------------------------------------------------------
def test_gwa_routing_invariant_present():
    """GWA must contain all routing invariant keywords."""
    gwa_path = _REPO / "config" / "global_working_agreement.md"
    assert gwa_path.exists(), "global_working_agreement.md not found"
    text = gwa_path.read_text(encoding="utf-8")
    required_phrases = [
        "fork_context=false",
        "active profile",
        "Sol children",
        "roleless",
        "explicitly justified L3",
    ]
    folded = text.casefold()
    missing = [phrase for phrase in required_phrases
               if phrase.casefold() not in folded]
    assert not missing, (
        "GWA routing invariant is missing required phrases: %s" % missing
    )
