# 安装指南

推荐让本地 Codex agent 严格执行 `AGENT_INSTALL.md`。需要手工安装时，使用下面
相同的确定性入口。

## 安装前

- 把仓库克隆到长期不移动的目录。托管 hooks 会引用该控制根；激活后移动目录会
  让绝对路径失效。
- 安装 Python 3.11+、Node.js 22+、Git 2.40+ 和 Codex CLI。
- 执行 `codex login`，先确认普通 Codex 任务能正常运行。
- provider 凭据只放在用户自己的 Codex 或网关配置中，绝不能写进仓库。

## 便携模型 profile

默认使用 `gpt-5.6-terra` 执行、`gpt-5.6` 独立审查；根模型由用户在 Codex
中选择。

根协调器、执行者和审计/复审者都不要求使用 Codex GPT 模型。根模型在你的
Codex 或兼容 OpenCodex 的运行环境中选择；执行与复审可以填写该环境已经提供
的任意模型 ID，三种角色可以来自不同厂商。网关注册与凭据始终留在仓库之外。

```bash
python harness/model_profile.py list --root .
```

`three-family-example` 只是生产配置模板。先把示例 ID 替换成当前环境真实可用的
模型，再进行激活；不要向 `model_profiles.toml` 写入 token。

## Windows

在仓库目录打开 PowerShell：

```powershell
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

激活分两步：

1. 安装便携 custom-agent TOML，只把缺失的官方 `[features]`/`[agents]` 键
   合并到 `%USERPROFILE%\.codex\config.toml`；已有键不覆盖，变更前会备份。
2. 安装托管 requirements 和 LOOP 协议，写入全局模式标记；原有 `AGENTS.md`、
   `hooks.json`、`requirements.toml` 进入带哈希的恢复账本。

完成后必须完全退出并重启 Codex Desktop，再新建任务。

```powershell
# 查看状态
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Status

# 启动只读观察器
./launchers/Start-Codex-LOOP-Monitor.ps1

# 用 LOOP 打开指定工作区
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
```

暂停与恢复不是一回事：

```powershell
# 暂停对新建/恢复任务的注入，保留安装和备份
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# 恢复安装前的托管文件
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

非标准 Codex 主目录使用 `-CodexHome <path>`。

## Linux / WSL

```bash
./install.sh --repo "$PWD"
```

脚本会检查 Python、Node、Git、Codex 和 TOML，安装 custom agents，只合并缺失
的官方 Codex 键，在包内建立控制状态，激活全局托管 hooks，并运行冒烟门。

只有在真实 provider 尚未配置时才使用 `--skip-smoke`。正式使用前必须补跑：

```bash
./harness/smoke_gate.sh "$(pwd)"
```

恢复托管全局文件并删除未被用户修改的 LOOP agent TOML：

```bash
./uninstall.sh
```

## Headless 平面

Headless worker 需要 Linux/WSL PATH 中存在可用 `codex`，或设置
`CODEX_HEADLESS_BIN`。OpenCodex 等第三方网关是可选项；使用时由运维者配置
健康端点和模型 ID，LOOP 不携带网关凭据。

```bash
python harness/headless_wave.py --root . --manifest path/to/manifest.json --wait-all
```

需要持续补位时，只导入一次有界 parent manifest，让 refill consumer 独占出生权；
不要再从第二条路径重复启动同一批 packet。

## 隔离验证

下面的命令不接触真实 Codex 主目录：

```bash
sandbox="$(mktemp -d)"
export CODEX_LOOP_STATE_DIR="$sandbox/state"
python harness/install_user_config.py --root . --codex-home "$sandbox/.codex" --dry-run
python harness/global_desktop_mode.py activate --root . --codex-home "$sandbox/.codex"
python harness/global_desktop_mode.py status --root . --codex-home "$sandbox/.codex"
python harness/global_desktop_mode.py restore --root . --codex-home "$sandbox/.codex"
```

完整验证：

```bash
python -m pytest tests -q
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

## 常见问题

- **spawn 被拒绝：**角色、模型或推理强度与活动 profile 不一致，或使用了
  `fork_context=true`。
- **观察器没有语义任务名：**生命周期 roster 已陈旧，或 prompt 首行没有
  `任务名：...`。
- **Headless 缺口不下降：**检查 `codex`/网关健康和
  `data/lifecycle/exec_roster.json`，不能只看进程是否启动。
- **hooks 不执行：**检查 Status、托管绝对路径、hook trust，并确认已完全重启 Codex。
- **Restore 拒绝：**不能绕过备份哈希错误，应先核对备份与安装状态账本。
