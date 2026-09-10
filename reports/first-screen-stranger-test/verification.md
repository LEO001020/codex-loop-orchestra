# L2 verification: first-screen stranger test (README.md)

Packet: 首屏陌生人测试 — read-only audit. Repo: E:\codex-LOOP\github\codex-loop.
Verdict: pass. Date: 2026-08-24. Verifier role: L2 (single-candidate).

## Method
Read working-tree README.md first 70 lines (file is UTF-8, 221 lines total).
Verified all 22 markdown links: local targets + anchors on disk, remote URLs via
HTTP HEAD, clone URL via `git ls-remote`. Confirmed repo unmodified before/after
(git status identical at start and end of session; no writes to repo, no subagents).

## Finding 1: three stranger questions (first 70 lines)
- 是什么 (what): answerable. Name at README.md:4, tagline :6, positioning :17,:19
  ("installable runtime for Codex Desktop and CLI—not another chat UI").
- 解决什么 (what problem): answerable. Parallel team + independent review + live
  progress (:17), benefit table (:50-:55).
- 如何安装 (how): answerable. Quickstart at :25-:44: paste prompt for an agent, or
  `git clone` + `./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate` + restart
  Desktop. Command file exists (launchers/Set-Codex-LOOP-Mode.ps1).
10-second claim is inference, not a timed human run: decisive info sits in lines
1-44 and headings/tagline are high-signal, so a stranger can answer all three in
a fast scan.

## Finding 2: broken links — none
Local (all exist): LICENSE, README.zh-CN.md, INSTALL.md, INSTALL.zh-CN.md,
AGENT_INSTALL.md, SECURITY.md, CONTRIBUTING.md, THIRD_PARTY_NOTICES.md,
docs/assets/dashboard.en.png (valid PNG magic), docs/assets/architecture-overview.en.svg (valid SVG).
Anchors: #quickstart (README.md:25), #how-loop-works (README.md:57) exist.
Remote (all HTTP 200): repo page, actions/workflows/ci.yml page + badge.svg,
img.shields.io badges x3, learn.chatgpt.com docs x3, python.org, github.com/LEO001020.
Clone URL verified anonymously: `git ls-remote` HEAD = 80c8abb = local HEAD.

## Finding 3: jargon (unexplained within first 70 lines)
- WSL (README.md:19,:44,:52,:53,:65) — never expanded in README; real jargon.
- MCP (README.md:65) — acronym never expanded.
- headless (README.md:19,:23,:52,:53,:65) — never defined.
- execution plane (README.md:23,:55, alt :21) — never defined.
- codex exec (README.md:70), lifecycle hooks (README.md:70) — tooling terms, undefined.
- IPybox (README.md:53,:65) — function explained (:63-:67) but term itself undefined.
- Codex itself is inferable from context (tagline :6, :19) — acceptable.
None of these blocks the three core questions; they are first-screen polish items.

## Constraint compliance (direct evidence)
- Read-only: `git status --short` identical at session start and end; only reads ran.
- No subagents created (no spawn tool invoked).
- File encoding verified: earlier console mojibake was PowerShell ANSI decoding of
  UTF-8, not a file defect.

## Note (unknown / flagged)
- Working-tree README differs from committed HEAD (35 insertions / 35 deletions);
  live GitHub today still serves HEAD 80c8abb (older "Start in 30 seconds" layout).
  This audit tested the working-tree file per packet; the new landing page is not
  yet live until committed/pushed.
