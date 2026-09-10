# L2 Verification — 依赖与许可证 (deps & license closure audit)

Audited workspace: E:\codex-LOOP\github\codex-loop (read-only; no files modified).
Method: AST import scan of all 171 .py files, shell-script external-binary scan,
requirements/LICENSE/notices diff against actual usage; upstream license claims
cross-checked (web) for Codex CLI, ipybox, mcpygen.
Caveat: repo files were being modified concurrently during this audit
(requirements-dev.txt length changed 225->240 bytes, VERSIONS.lock 7697->964
bytes between reads); line numbers below are from the final read.

## Verified gaps (direct file evidence)

1. jsonschema: hard import at harness/orchestration/plan_pipeline.py:23;
   production component (config/orchestration_policy_v2.toml:172
   `require_plan_pipeline = true`, invoked by harness/plan_consumer.py:210).
   Missing from requirements-dev.txt (only pytest/pytest-timeout/PyYAML,
   lines 4-6) and missing from THIRD_PARTY_NOTICES.md.
   Tests load the module at collection: tests/unit/test_plan_pipeline.py:12
   `SPEC.loader.exec_module(MOD)`; CI installs only requirements-dev.txt
   (.github/workflows/ci.yml:25), and no package in that set pulls jsonschema
   -> clean-runner collection failure (inference, high confidence).
   No runtime requirements file exists anywhere in the repo (only
   requirements-dev.txt + VERSIONS.lock; no pyproject/setup.py), and
   install.sh/install_v2.sh never run `pip install` -> fresh-host runtime
   failure for the plan pipeline (inference).

2. psutil: launchers/loop_monitor_server.py:23 (guarded ImportError, optional)
   and hard import harness/ipybox_cleanup.py:13. Not in requirements-dev.txt
   nor THIRD_PARTY_NOTICES.md.

3. python-dotenv: hard import harness/ipybox_lazy.py:12. Not declared anywhere.

4. mcpygen: hard import harness/ipybox_lazy.py:16; separate PyPI package
   gradion-ai/mcpygen (Apache-2.0, v0.1.4). Absent from THIRD_PARTY_NOTICES.md
   (only ipybox and mcp SDK are listed, lines 60-76).

5. THIRD_PARTY_NOTICES.md:41 calls PyYAML "CI and tests ... development
   dependency only" — inaccurate: runtime use at harness/retry.py:97,
   harness/trigger_eval.py:70, harness/trigger_eval_v2.py:46 (all guarded with
   fallback -> soft runtime dependency).

6. THIRD_PARTY_NOTICES.md:20-21 "Modules used" lists 11 stdlib modules; AST
   scan of the repo finds 47 distinct stdlib modules imported (datetime,
   threading, socket, http, urllib, asyncio, sqlite3, uuid, logging, typing,
   contextlib, shutil, tempfile, signal, importlib, functools, dataclasses,
   csv, ctypes, ...). The list is inaccurate/incomplete.

7. hooks/subagent_start_meter.sh:44-47 uses `jq` when present (graceful
   degraded path exists); jq is mentioned nowhere in prereqs/notices — an
   undeclared optional external binary.

## Verified accurate items

- LICENSE:1-21 is the complete standard MIT text; holder `Copyright (c) 2026
  LEO001020` matches README.md:246 and VERSIONS.lock:37 (`license = "MIT"`).
- requirements-dev.txt:4-6 covers the three declared dev deps; CI
  `--timeout=300` (.github/workflows/ci.yml:27) matches pytest-timeout.
- Notice license claims checked against upstream: Codex CLI Apache-2.0 (web,
  github.com/openai/codex), ipybox Apache-2.0 (web, gradion-ai/ipybox),
  mcpygen Apache-2.0 (web, PyPI). mcp SDK stated MIT — consistent with
  modelcontextprotocol/python-sdk; not re-verified this turn (search limit).
- VERSIONS.lock:30-33 ipybox 0.9.2 + `mcp<2` matches notices lines 60-75.
- External binaries `codex`, `node`/`npm`, `git`, `python3` used by install
  scripts are covered by install.sh prerequisites/notices.

## Unknown

- Whether an out-of-repo bootstrap provisions psutil/dotenv/jsonschema on the
  production host: no evidence of one in the repo; treat as undeclared.

## Minimal suggestions (order by impact)

- Add `jsonschema` to requirements-dev.txt and to THIRD_PARTY_NOTICES.md
  (MIT license), or add a runtime requirements file and reference it.
- Add psutil (BSD-3-Clause), python-dotenv (BSD-3-Clause), mcpygen
  (Apache-2.0) to THIRD_PARTY_NOTICES.md as runtime/optional entries, and
  declare them in an optional-deps section of a requirements file.
- Fix PyYAML line 41 wording to "runtime YAML parsing (with JSON fallback)
  plus CI/tests".
- Replace the partial stdlib module list (lines 20-21) with the accurate set
  or drop the enumeration.
- Mention `jq` as an optional external tool in THIRD_PARTY_NOTICES.md or
  INSTALL.md.
