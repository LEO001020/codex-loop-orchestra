# CHANGELOG — ZCode-ZLoop Orchestra

All notable changes to this project will be documented in this file.

## [v0.3.0] — 2026-09-03 (Triple-Audit & Concurrency Hardening)
### Added
- **D-25 Triple-Audit Architecture**:
  - Pre-planning counter-plan review gate (C2C-P) via ChatGPT Web (fresh thread).
  - Mandatory wave-start plan gate (`c2c_plan_gate_required`) blocking unauthorized HIGH/CRITICAL waves.
  - Role-aware promotion gate (`role=result` required) preventing bypass via planning records.
  - CLI commands: `zloop c2c prepare` and `zloop c2c record` supporting stdin and file inputs.
- **D-24 Heterogeneous Audit Direct Route**:
  - Full-qualified model routing `af9697f5-a1f2-4616-8350-e14311d14fda/glm-5.3` direct to independent GLM-5.3 provider.
  - Subagent profile `zloop-auditor.md` with read-only contract.
- **D-23 Physical Concurrency Hardening (8–15 Workers)**:
  - Asynchronous JIT ThreadPoolExecutor (16 workers) in `CodexSdkBackend` with non-blocking `poll()`.
  - Rate-limit jittered exponential backoff retry (up to 3 retries).
  - Atomic `git reset --hard` + `git clean -fdx` on staging materialization failure.
  - Git worktree creation index.lock backoff retry.
- **Metrics Subsystem (`zloop.metrics`)**:
  - `tokens.py`: S0 vs S1 reduction ratio and cost calculations.
  - `latency.py`: Supervisor and worker latency percentiles (p50, p90, p95, p99) via linear interpolation.
  - `concurrency.py`: Timeline analysis, overlap ratio calculations, and concurrency reporting.
  - `c2c_stats.py`: Pass rates and statistics for plan and result audits.
- **High-Definition Architecture SVGs**:
  - Light/Dark adaptive Keynote-style SVG diagrams (Simplified & Full Overview, English and Chinese).
  - Interactive HTML presentation at `docs/architecture-interactive.html`.

### Changed
- Pruned 14 built-in skills and MCP tools from user configuration, resulting in **-53.38% (-14,336 tokens)** reduction on first-turn input tokens.
- Raised SQLite `busy_timeout` to 30,000ms.
- Enforced non-empty visible text contract on `zloop-worker.md` to prevent trivial 0-token completions.

---

## [v0.2.0] — 2026-09-02 (Foundation & Research Broker)
### Added
- Control SQLite database S with DELETE+EXTRA version gate (I22).
- Immutable H0 NDJSON journal and CAS blob store with secret redaction (I13).
- Kimi K1 research broker with searcher-only tool surface (`disabled_tools`).
- CAS controller token ownership and mechanical process death proof (D-8, D-20).
- Process hooks with strict cwd project-scoping filter (D-16).
