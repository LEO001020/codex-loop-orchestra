# 安装指南

推荐让本地 Codex 智能体严格按照 `AGENT_INSTALL.md` 完成安装。该文件要求智能体先检查环境、展示变更预览和备份方案，并在得到用户确认后调用确定性安装脚本。如果希望手动安装，可直接使用下面列出的同一组脚本。

## 安装前准备

- 将仓库克隆到一个不会随意移动的长期目录。受管钩子会引用 LOOP 控制目录的绝对路径；激活后移动仓库会导致这些路径失效。
- 安装 Python 3.11 或更高版本、Node.js 22 或更高版本、Git 2.40 或更高版本以及 Codex CLI。
- 运行 `codex login`，并确认普通 Codex 任务可以正常执行。
- 模型服务商凭据只能保存在用户自己的 Codex 或网关配置中，绝不能写入仓库。

## 模型配置方案

默认配置方案使用 `gpt-5.6-terra` 执行任务，使用 `gpt-5.6` 进行独立审计；根智能体使用用户在 Codex 中选择的模型。

查看可用的配置方案：

```bash
python harness/model_profile.py list --root .
```

`three-family-example` 是一个未启用的三模型示例。使用前，请先把示例模型 ID 替换为当前环境中实际可用的模型。不要把 token 或其他凭据写入 `model_profiles.toml`。

## Windows

在仓库目录中打开 PowerShell，然后运行：

```powershell
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

激活过程分为两个可恢复的阶段：

1. 安装随项目提供的自定义智能体，并把缺失的官方 `[features]` 和 `[agents]` 配置项合并到 `%USERPROFILE%\.codex\config.toml`。已有配置不会被覆盖，所有受影响的文件都会先行备份。
2. 安装由 LOOP 管理的依赖文件和全局工作约定，写入全局模式标记。原有的 `AGENTS.md`、`hooks.json` 和 `requirements.toml` 会记录到带哈希校验的恢复清单中。

安装完成后，请彻底退出并重新启动 Codex Desktop，再新建一个任务。已有任务不会自动重新加载全部配置。

```powershell
# 查看当前状态
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Status

# 启动只读的 Agent 监测 Web UI
./launchers/Start-Codex-LOOP-Monitor.ps1

# 在指定工作区中启动 LOOP
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
```

“暂停”与“恢复”是两种不同操作：

```powershell
# 暂停向新建或恢复的任务注入 LOOP，但保留安装内容和备份
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# 从已验证的备份中恢复安装前的受管文件
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

如果 Codex 主目录不在默认位置，请添加 `-CodexHome <path>`。

## Linux/WSL

```bash
./install.sh --repo "$PWD"
```

安装脚本会检查 Python、Node.js、Git、Codex 和 TOML 输入，安装自定义智能体，只合并缺失的 Codex 官方配置项，初始化项目内的控制状态，启用全局受管钩子，并运行冒烟测试门禁。

只有在真实模型调用链路尚未配置完成时，才应使用 `--skip-smoke`。正式使用前，必须在模型配置完成后补跑冒烟测试：

```bash
./harness/smoke_gate.sh "$(pwd)"
```

如需恢复受管的全局文件，并删除安装后未被用户修改的 LOOP 智能体配置，请运行：

```bash
./uninstall.sh
```

## 无界面执行环境

无界面执行进程需要能够在 Linux/WSL 的 `PATH` 中找到可用的 `codex`，也可以通过 `CODEX_HEADLESS_BIN` 显式指定可执行文件。OpenCodex 等第三方网关不是必需项；如需使用，应由用户自行配置健康检查地址和模型 ID。LOOP 不附带也不保存网关凭据。

启动一批能够被生命周期系统追踪的无界面任务：

```bash
python harness/headless_wave.py --root . --manifest path/to/manifest.json --wait-all
```

如果需要为某个父任务持续自动补位，只应导入一次数量有限、范围明确的父任务清单，并由现有补位程序统一启动执行进程。不要通过第二条路径重复启动同一批任务包。

## 隔离验证

下面的命令使用临时目录，不会接触用户真实的 Codex 主目录：

```bash
sandbox="$(mktemp -d)"
export CODEX_LOOP_STATE_DIR="$sandbox/state"
python harness/install_user_config.py --root . --codex-home "$sandbox/.codex" --dry-run
python harness/global_desktop_mode.py activate --root . --codex-home "$sandbox/.codex"
python harness/global_desktop_mode.py status --root . --codex-home "$sandbox/.codex"
python harness/global_desktop_mode.py restore --root . --codex-home "$sandbox/.codex"
```

运行完整测试和完整性校验：

```bash
python -m pytest tests -q
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

## 常见问题

- **子智能体启动被拒绝：** 请求的角色、模型或推理强度与当前模型配置方案不一致，或者使用了 `fork_context=true`。
- **监控面板没有显示任务名称：** 生命周期运行清单已经过期，或者任务提示的第一行没有使用 `任务名：...`。
- **无界面执行进程始终没有补足：** 检查 `codex` 或网关的健康状态，并查看 `data/lifecycle/exec_roster.json`。仅仅看到操作系统进程启动，并不代表该任务已经进入 LOOP 的生命周期管理。
- **生命周期钩子没有执行：** 检查全局模式状态、受管钩子的绝对路径和 Codex 对钩子的信任设置，并确认 Codex 已彻底重启。
- **恢复操作被拒绝：** 不要绕过备份哈希不一致错误。请先核对备份内容和安装状态清单，再决定如何处理。
