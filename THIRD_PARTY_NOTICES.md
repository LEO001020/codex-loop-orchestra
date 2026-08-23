# Third-Party Notices — Codex LOOP Orchestra

Codex LOOP Orchestra interoperates with or uses the following third-party
projects. Unless explicitly stated, they are not bundled in release archives.

---

## OpenAI Codex CLI

- **Source:** https://github.com/openai/codex
- **License:** Apache 2.0
- **Use:** Codex LOOP orchestrates sessions using the Codex CLI as an external
  process. No Codex CLI source code is included in this repository.

---

## Python Standard Library

- **License:** Python Software Foundation License 2.0
- **Modules used:** `json`, `os`, `sys`, `re`, `pathlib`, `subprocess`,
  `tomllib`, `argparse`, `hashlib`, `tarfile`, `zipfile`, `time`

---

## pytest

- **Source:** https://github.com/pytest-dev/pytest
- **License:** MIT
- **Use:** Test suite runner. Not bundled; installed by the user.

## pytest-timeout

- **Source:** https://github.com/pytest-dev/pytest-timeout
- **License:** MIT
- **Use:** Test timeout enforcement. Not bundled; development dependency only.

## PyYAML

- **Source:** https://github.com/yaml/pyyaml
- **License:** MIT
- **Use:** YAML parsing in CI and tests. Not bundled; development dependency only.

## jsonschema

- **Source:** https://github.com/python-jsonschema/jsonschema
- **License:** MIT
- **Use:** Packet schema validation in the planning pipeline.

## psutil

- **Source:** https://github.com/giampaolo/psutil
- **License:** BSD-3-Clause
- **Use:** Optional host telemetry and supervised process cleanup.

## python-dotenv

- **Source:** https://github.com/theskumar/python-dotenv
- **License:** BSD-3-Clause
- **Use:** Optional ipybox workspace environment loading.

## mcpygen

- **Source:** https://github.com/gradion-ai/mcpygen
- **License:** Apache-2.0
- **Use:** Optional ipybox MCP tool-server integration.

---

## tomllib (Python 3.11+ built-in)

- **License:** MIT / Python Software Foundation License
- **Use:** TOML configuration parsing.

---

## Node.js

- **Source:** https://nodejs.org/
- **License:** MIT (with bundled components under various OSI-approved licenses)
- **Use:** Runtime for the Codex CLI. Not bundled.

---

## ipybox (optional dependency)

- **Source:** https://github.com/gradion-ai/ipybox
- **License:** Apache 2.0
- **Version pin:** 0.9.2 with `mcp<2` (see `VERSIONS.lock` and `config/config.toml.example`)
- **Use:** Optional persistent Python kernel for large-output in-process
  digestion. Optional and not bundled.

---

## mcp (Model Context Protocol SDK)

- **Source:** https://github.com/modelcontextprotocol/python-sdk
- **License:** MIT
- **Use:** MCP server protocol for ipybox integration (optional).
  Pin: `mcp<2` required when using ipybox 0.9.2.

---

This notice is provided voluntarily for attribution and dependency clarity.
Codex CLI is invoked as an external program; no Apache-licensed Codex source is
redistributed by this repository.
