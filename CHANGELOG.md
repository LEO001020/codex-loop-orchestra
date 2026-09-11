# Changelog

All notable changes to Codex LOOP Orchestra are documented here. The project
uses semantic versioning where practical.

## [Unreleased]

### Added

- Current F2 control-plane coverage for observed-running refill, generation-aware
  lifecycle recovery, bounded parent backlogs, present/past evidence views, and
  instruction attention-budget enforcement.

### Restored

- Product-oriented English and Chinese landing pages, installation guides,
  license and community documents, Windows launchers, CI/release metadata, and
  dashboard/architecture assets from the pre-force-push public release.

### Changed

- Model-profile documentation now matches the checked-in `gemini38f` execution
  and independent-review routing and explicitly requires operator-configured
  compatible providers.
- Profile switching now treats project-local `.codex/config.toml` as optional,
  so the public package can remain free of machine-specific hook paths while
  installed workspaces still receive profile updates.
- Restored test fixtures no longer shadow the current root test configuration;
  metering tests use the public hook source, and the release allowlist includes
  the current JavaScript probe.
- Cross-platform CI uses repository-local Linux and Windows Codex shims for
  resolver tests, preserves executable modes for shell fixtures, uses a
  hermetic Codex config fixture, and bounds each real provider smoke probe to
  45 seconds.
- Text files previously carrying mixed CRLF/LF bytes are normalized to the
  repository's LF policy so release checksums reproduce on Windows and Linux.
- Generated runtime ledgers, reports, backups, nested workspace copies, and
  Python caches are removed from the public source tree and covered by the
  restored ignore policy.

## [0.1.0] - 2026-08-24

### Added

- MIT open-source release under the Codex LOOP Orchestra name.
- Agent-assisted bilingual installation protocol.
- Portable model profile without private provider dependencies.
- Reversible global-mode activation for Windows and Linux/WSL.
- Ordered native task nicknames plus semantic Agent Monitoring Web UI mapping.
- Dual-plane concurrency, continuous refill, layered review, and CI gates.
