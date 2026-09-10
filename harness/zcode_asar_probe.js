"use strict";

const path = require("path");
const asar = require("C:/Users/hzq00/AppData/Roaming/npm/node_modules/asar");

const archive = process.argv[2];
if (!archive) {
  throw new Error("usage: node zcode_asar_probe.js <app.asar>");
}

const terms = [
  "/goal",
  "injectAgentsMd",
  "thoughtLevel",
  "maxTurns",
  "SessionStart",
  "UserPromptSubmit",
  "PreToolUse",
  "PermissionRequest",
  "PostToolUse",
  "PostToolUseFailure",
  "additionalContext",
  "additional_context",
  "hookSpecificOutput",
  "transcript_path",
  "transcriptPath",
  "session/goal",
  "sessionGoal",
  "continuation",
  "stop_hook_active",
  "decision",
  "ZCODE_PLUGIN_ROOT",
  "CLAUDE_PLUGIN_ROOT",
  ".mcp.json",
  "plugin.json"
];

const files = asar.listPackage(archive)
  .map((name) => name.replace(/^[/\\]+/, ""))
  .filter((name) => /^out[\\/](host|main)[\\/].+\.js$/i.test(name));

const result = Object.fromEntries(terms.map((term) => [term, []]));
for (const file of files) {
  const normalized = path.normalize(file);
  const text = asar.extractFile(archive, normalized).toString("utf8");
  for (const term of terms) {
    let start = 0;
    let count = 0;
    const snippets = [];
    while (true) {
      const index = text.indexOf(term, start);
      if (index < 0) break;
      count += 1;
      if (snippets.length < 2) {
        snippets.push({
          offset: index,
          text: text.slice(Math.max(0, index - 100), Math.min(text.length, index + term.length + 140))
            .replace(/\s+/g, " ")
        });
      }
      start = index + term.length;
    }
    if (count > 0) result[term].push({ file, count, snippets });
  }
}

process.stdout.write(JSON.stringify({
  archive,
  scanned_files: files.length,
  terms: result
}, null, 2));
