# Codex-LOOP C2C E2E boundary

Codex-LOOP reuses `XiaoDuoYa/codex-with-chatgpt`; it does not implement a
second bridge, OAuth flow, tunnel, connector store, browser controller, or
conversation manager.

- Local read-only status adapter: `python harness/c2c_e2e.py doctor --workspace <workspace>`
- Browser/setup/chat lifecycle: installed `codex-with-chatgpt` Skill.
- Browser invariant: one Codex in-app ChatGPT tab, claimed and reused.
- Before setup the Skill asks once for temporary-address versus Cloudflare
  fixed-domain mode. This adapter never makes that user choice.
- The adapter exposes only `status`, `doctor --no-fix`, `session get`, and
  `workspace`; it cannot launch a browser or mutate connection state.

`CODEX_WITH_CHATGPT_CLI` may point to `bin/c2c.js`; alternatively set
`CODEX_WITH_CHATGPT_HOME`. The final fallback is
`~/codex-with-chatgpt/bin/c2c.js`.
