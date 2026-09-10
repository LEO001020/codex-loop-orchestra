# ZLoop plugin (D-16)

ZLoop evidence & session-binding hooks, registered at **plugin scope** —
the production target per decision D-16 — so only the workspaces where
this plugin is enabled pay the hook cost, instead of every workspace on
the machine being taxed by a user-global hooks block.

## What it registers

The 5 post-execution events (decision D-9; `PreToolUse` /
`PermissionRequest` are intentionally NOT registered — they spawn a
process before every tool call):

- `SessionStart` — recovery injection (VOL-05 §5) + `session_start` capture
- `UserPromptSubmit` — prompt capture (redacted, VOL-06)
- `PostToolUse` — tool-result capture + bind-token claim (I32)
- `PostToolUseFailure` — failure capture
- `Stop` — stop capture

All entries are `type: "process"` with the single entrypoint
`${ZCODE_PLUGIN_ROOT}\zloop-hook.cmd handle` (no matcher). The hook reads
one JSON line from stdin and dispatches on `hook_event_name`.

## Install (one click)

ZCode Settings → **Plugin Management** → add this directory as a local
plugin (or add it as a local marketplace and install `zloop` from it).
Hook configuration is snapshotted per session, so a **new session** is
required after enabling.

The `zloop-hook.cmd` wrapper runs
`"E:\zcode\zloop-gen8\.venv\Scripts\python.exe" -m zloop.hook` — an
interpreter that can `import zloop` (the repo venv, editable install).

## Compatibility fallback

`zloop install` (user-level `~/.zcode/cli/config.json`, 5 events,
`zloop uninstall` to remove) remains the **explicit** compatibility
fallback per D-16. Do not use both at once — one registration surface is
enough.

## If the repo moves

`zloop-hook.cmd` bakes an absolute python path. After moving the repo
(or switching interpreters), regenerate this directory from the repo
root:

```
.venv\Scripts\python.exe -c "from pathlib import Path; from zloop.install import emit_plugin; emit_plugin(Path('plugin'), __import__('sys').executable)"
```

(and re-copy `plugin/README.md` if you regenerate elsewhere). The
`hooks/hooks.json` command itself is templated on `${ZCODE_PLUGIN_ROOT}`
and needs no regeneration.

## Privacy scoping (D-16)

Capture and bind-token claim proceed **only** when the event's `cwd` lies
inside a registered project's `git_root` (`zloop.hook.resolve_project_for_cwd`,
case-insensitive ancestor-or-equal). Workspaces outside every registered
project — or unrelated directories while a different project is
registered — are journaled nowhere: silent exit 0. SessionStart recovery
works off the exact session binding regardless of cwd.
