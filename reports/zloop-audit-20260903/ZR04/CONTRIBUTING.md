# Contributing to ZCode-ZLoop Orchestra

Thank you for your interest in contributing to **zcode-loop-orchestra**!

## Core Engineering Constitution

Before submitting any code, familiarize yourself with our core architectural rules:

1. **Sole Cognition Authority**: ZCode root agent owns cognition; the harness owns state, recovery, and isolation.
2. **Invariants I1–I44 are Non-Negotiable**:
   - Tests precede features.
   - Any commit failing invariants will be rejected.
   - Mechanical evidence always supersedes model claims.
3. **Fail-Closed by Default**: When in doubt (missing credentials, corrupted logs, ambiguous state), the system must halt and refuse to guess.
4. **Context Economy**: Never inject bulky tools or schemas into general subagents. Keep worker profiles under 600 tokens.

## Development Setup

```bash
git clone https://github.com/LEO001020/zcode-loop-orchestra.git
cd zcode-loop-orchestra

python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"

# Run full test suite
.venv/Scripts/python -m pytest tests -v
```

All Pull Requests must maintain 100% pass rate on existing 293+ tests.
