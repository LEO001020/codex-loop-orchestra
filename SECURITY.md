# Security Policy — Codex LOOP Orchestra

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest `0.x` release | Yes |
| Older prereleases | Best effort |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Use GitHub's private vulnerability reporting for this repository:

<https://github.com/LEO001020/codex-loop-orchestra/security/advisories/new>

If that interface is unavailable, open a non-sensitive coordination issue that
contains no exploit details or private logs. Include privately:

1. Description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Suggested fix (optional)

We aim to acknowledge a complete report within 3 business days and provide a
status update after initial triage. These are response targets, not a service
level agreement.

## Security Scope

### In scope

- Vulnerabilities in LOOP harness scripts (`harness/`, `hooks/`, `metering/`)
- Credential leak or path-traversal in the installer (`install.sh`, PowerShell launchers)
- Privilege escalation in hook execution
- Unauthorized file access beyond declared `authorized_paths`

### Out of scope

- Vulnerabilities in the Codex CLI itself (report to OpenAI)
- Vulnerabilities in third-party model APIs
- Social engineering attacks

## Security Design Notes

### No credentials in source

This package never stores API keys, OAuth tokens, session cookies, or any
credentials in source files. All authentication is delegated to the Codex CLI
through `codex login`.

### gitignore guardrails

The following directories are excluded from git by `.gitignore` and must
**never** be committed:

- `data/` — per-task runtime state (may contain task descriptions)
- `logs/` — execution logs
- `state/` — session and lifecycle state
- `.codex/` — Codex home directory (contains auth tokens)
- `secrets/`, `credentials/` — should never be created in the repo root

### Hook execution model

LOOP hooks run as the current user process. They do not elevate privileges.
The `sol_tool_gate.py` PreToolUse hook is a deny-gate; it can only block tool
calls, never execute arbitrary commands on behalf of a subagent.

### Worktree isolation

Each executor packet runs in an isolated git worktree. The `diffvalidator.py`
L0 gate enforces that diffs are strictly within the packet's `authorized_paths`
and rejects any path-escape attempt (absolute paths, `..` traversals).

### Supply chain

Runtime requirements and tested versions are recorded in `VERSIONS.lock`.
Development dependencies are declared in `requirements-dev.txt`. Verify a
release archive against its adjacent checksum file with:

```bash
sha256sum -c SHA256SUMS
```

## Vulnerability Disclosure

We coordinate disclosure according to severity, exploitability, fix readiness,
and the reporter's needs. We do not promise a fixed public-disclosure date.
