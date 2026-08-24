# Contributing to Codex LOOP Orchestra

Thank you for your interest in contributing!

## Code of Conduct

Read and follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Be respectful and
constructive.

## How to Contribute

### Reporting Bugs

1. Check existing issues to avoid duplicates.
2. Open a GitHub issue with:
   - Codex CLI version from `codex --version`
   - Python version from `python3 --version`
   - Node version from `node --version`
   - Operating system
   - Steps to reproduce
   - Expected vs actual behaviour
   - A minimal, redacted excerpt that shows the failure; attach no raw rollout,
     session, authentication, or provider logs

### Suggesting Enhancements

Open a GitHub issue with the `enhancement` label. Describe the use case and
why the current behaviour is insufficient.

### Pull Requests

1. **Fork** the repository and create a feature branch from `main`.
2. **Write tests** for any new harness logic (see `tests/` for patterns).
3. **Run the full test suite** before submitting:
   ```bash
   python3 -m pytest tests/ -q
   harness/smoke_gate.sh "$(pwd)"
   ```
4. **Ensure no private paths** appear in your diff. Run the desensitization
   check: `python3 harness/sanitize.py --in <packet.json>` on any test fixtures.
5. **Sign your commits** if possible.
6. **Open a PR** with a clear description of what changes and why.

## Development Setup

```bash
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
python3 -m pytest tests/ -q
```

## Code Style

- Python: PEP 8. Line length ≤ 100. No external formatter required, but keep
  consistent with the existing style.
- Shell: `set -euo pipefail`. Use `say()`/`step()` helpers defined in each
  script for consistent output.
- PowerShell: `Set-StrictMode -Version Latest`, `$ErrorActionPreference = 'Stop'`.
- TOML/YAML: keep comments; policy files are the declaration face — clarity
  matters more than brevity.

## Testing Conventions

- Unit tests live in `tests/`. Mock-backed; no live Codex session required.
- New harness scripts must include at minimum: happy path, empty/missing input,
  and boundary/escape test cases.
- The `tests/mock_codex/` layer provides a stub `codex` binary for CI.
- Do not decrease the test count. `diffvalidator.py` will reject such diffs.

## Sensitive Information

- **Never** commit API keys, tokens, session cookies, or private paths.
- Use `<YOUR_CODEX_HOME>` as a placeholder in documentation when a path is
  installation-specific.
- All config examples use placeholder values (e.g. `model = "your-model-id"`
  for custom enterprise endpoints).

## Release Process

Releases are cut by maintainers only through the tag or manual release workflow.
The merge and release gates are always human-triggered. L1/L2 automated layers
can block but never publish.
