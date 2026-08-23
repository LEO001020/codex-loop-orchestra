# Agent-assisted installation / 由 Agent 协助安装

This is the recommended installation path for Codex LOOP Orchestra. Give this
repository to a local Codex agent and ask it to follow this file. The scripts
remain deterministic; the agent only discovers the real environment, explains
the plan, and selects the correct entry point.

这是 Codex LOOP Orchestra 的推荐安装方式。让本地 Codex agent 打开本仓库并严格
遵循此文件。实际写入仍由确定性脚本完成；agent 只负责识别真实环境、解释计划并
选择正确入口。

## Installer protocol / 安装协议

The installing agent MUST follow this order:

1. Ask the user to choose `中文` or `English`. Do not continue until the
   language is known.
2. Read `README.md` or `README.zh-CN.md`, then `INSTALL.md` or
   `INSTALL.zh-CN.md` in the selected language.
3. Inspect without changing state:
   - operating system and whether the shell is native Windows, WSL, or Linux;
   - package root and intended permanent install directory;
   - `CODEX_HOME` or the default `~/.codex` / `%USERPROFILE%\.codex`;
   - Python 3.11+, Node.js 22+, Git 2.40+, and Codex CLI availability;
   - whether `codex login` has already been completed;
   - existing `config.toml`, `AGENTS.md`, `hooks.json`, and
     `requirements.toml` files (report existence only; never print secrets).
4. Run the non-mutating preview:

   ```text
   python harness/install_user_config.py --root . --dry-run
   ```

5. Show the user a concise plan listing files that will be created, merged, or
   backed up. Explain that existing config keys are preserved and provider
   credentials are never copied into this repository.
6. Obtain explicit confirmation before making installation changes.
7. Use exactly one supported entry point:

   **Windows PowerShell**

   ```powershell
   ./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
   ```

   **Linux / WSL**

   ```bash
   ./install.sh --repo "$PWD"
   ```

8. Verify mechanically:
   - the installer exits zero;
   - the active profile resolves without private placeholder models;
   - managed requirements contain no `<LOOP_INSTALL_DIR>` placeholder;
   - `Status` reports active on Windows, or the global mode marker reports
     active on Linux/WSL;
   - the package-local Python/TOML syntax checks pass;
   - port 8765 can start when the user requests the Observer.
9. Tell the user to fully restart Codex Desktop/CLI and open a new task.
10. End with the exact rollback command for the detected platform.

## Safety boundaries / 安全边界

- Do not edit Codex binaries or application installation files.
- Do not request, display, copy, or store API keys, cookies, or auth tokens.
- Do not overwrite existing user keys merely to match the example config.
- Do not delete runtime data, backups, or user custom agents during install.
- Do not enable a three-provider example profile until every referenced model
  is available in the user's own Codex/gateway configuration.
- If any required interface or path is uncertain, inspect the installed files
  or official Codex documentation instead of guessing.

## Ready-to-paste prompt / 可直接复制的提示词

English:

```text
Install Codex LOOP Orchestra from this repository. Follow AGENT_INSTALL.md
exactly. Ask me for the installation language first, inspect my environment,
run the dry-run preview, explain all intended changes and backups, wait for my
confirmation, then install and verify. Do not expose credentials or modify
Codex binaries. Finish with the rollback command.
```

中文：

```text
请从当前仓库安装 Codex LOOP Orchestra。严格遵循 AGENT_INSTALL.md：第一步先询问
我使用中文还是 English；只读检查真实环境，运行 dry-run，说明将修改和备份哪些
文件，得到我确认后再安装并机械验证。不得显示或复制凭据，不得修改 Codex 二进制。
最后给出适用于当前平台的一条恢复命令。
```
