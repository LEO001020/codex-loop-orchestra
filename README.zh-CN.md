<!-- size-justified: 中文项目主页；不内联日志、清单和运行状态。 -->
<div align="center">

# Codex LOOP Orchestra

**把一个 Codex Agent，变成一支会分工、并行和复核的工程团队。**

[![CI](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)

[快速开始](#快速开始) · [工作原理](#loop-如何工作) · [English](README.md) · [完整文档](INSTALL.zh-CN.md)

</div>

只给 LOOP 一个目标：它会维持一支并行工作的 Agent 团队，把结果交给独立模型复审，并实时展示进度，直到任务完成。

Codex LOOP Orchestra 是安装在 Codex Desktop 与 CLI 之上的多 Agent 运行层，不是另一个聊天界面。只给它一个目标，它就会围绕这个目标组织一支受控团队，让原生 Desktop Agent 与受监督的 WSL/headless worker 协同工作。

![Codex LOOP 实时面板：活跃 Agent、执行平面、实际模型和语义任务名](docs/assets/dashboard.png)

<p align="center"><sub>一个界面查看全部原生与 headless Agent：任务、模型、执行平面、健康状态和容量。</sub></p>

## 快速开始

把这个仓库交给 Codex 或其他编码 Agent，并粘贴：

```text
从 https://github.com/LEO001020/codex-loop-orchestra 安装 Codex LOOP Orchestra。
先阅读 AGENT_INSTALL.md；检查我的环境，展示 dry-run 与备份方案，等待我确认后再
启用 LOOP 并验证安装。不要读取、输出或修改我的任何 API 凭据。
```

希望手动安装？直接跳到[完整安装说明](#完整安装说明)，或阅读完整的 [Windows/Linux/WSL 指南](INSTALL.zh-CN.md)。

## 你会得到什么

| | |
|---|---|
| **同时推进更多工作** | 只要还有有用任务，可配置 Agent 团队就会持续工作并补充空闲槽位。 |
| **发现模型共同盲点** | 执行与复审可使用不同模型家族，避免同一个模型检查自己。 |
| **突破 Desktop 界面承载** | 保留可见的原生 Agent，同时让受监督 WSL/headless worker 承接更宽的并发。 |
| **跨调用保留工作状态** | 可选 IPybox 层为 WSL/headless worker 提供持久 Python 工作台，处理文件、数据与计算。 |
| **把最强模型用在决策上** | 协调模型负责规划和裁决，脚本负责常规生命周期工作。 |
| **实时看见每个 Agent** | 面板显示每项任务、模型、执行平面、健康状态与剩余容量。 |

## LOOP 如何工作

![Codex LOOP Orchestra 架构：根协调、确定性控制、Desktop/headless 执行、独立审查、人工发布与实时观察](docs/assets/architecture-overview.zh-CN.svg)

默认配置面向持续并行工作——每个任务约 20 个活跃 Agent——并且可以调整。最终合并与发布权始终保留在人类维护者手中。

### 面向 Harness 的持久 Python 工作台

WSL 同时也是 LOOP 的持久工具平面。公开包包含按需启动的 IPybox 服务、沙箱策略和 WSL/headless 路由规则；注册可选的上游 MCP 后，执行 worker 可以跨调用保留 dataframe、解析索引、计数器等 Python 状态，在模型上下文之外消化大输出，并让 Desktop 保持轻量控制平面。

> [!NOTE]
> Codex LOOP Orchestra 是独立社区项目，不是 OpenAI 官方产品。它安装配置、custom agents、生命周期 hooks 和 `codex exec` 工具，不分发、不修改 Codex 二进制文件。便携 profile 不需要私有网关，凭据始终留在仓库之外。

## 完整安装说明

推荐方式是：**由 Agent 引导，但由确定性脚本执行。** [AGENT_INSTALL.md](AGENT_INSTALL.md) 要求 Agent 先识别环境、展示 dry-run 与备份方案、等待确认，再调用下方同一个确定性安装器；实际安装与还原由 PowerShell、Python 和 Bash 完成。

### 前置条件

- 支持 subagents 和 hooks 的 Codex CLI，并完成 `codex login`
- Python 3.11+
- Node.js 22+
- Git 2.40+
- Windows 使用 PowerShell；Linux/WSL 使用 Bash

### Windows

```powershell
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

Activate 会安装便携 custom agents、合并受支持的 Codex 多智能体设置、备份托管文件、渲染绝对 hook 路径，并开启全局 LOOP 模式。完成后完全退出并重启 Codex Desktop，再新建任务。

```powershell
# 可选：启动工作区和 Observer
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
./launchers/Start-Codex-LOOP-Monitor.ps1

# 暂停，不删除已验证备份
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# 恢复安装前的托管文件
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

### Linux / WSL

```bash
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./install.sh --repo "$PWD"
```

重启 Codex 并新建任务。恢复激活前的托管文件：

```bash
./uninstall.sh
```

隔离测试安装、显式 `CODEX_HOME`、模型 profile、headless 前提和故障排查见 [INSTALL.zh-CN.md](INSTALL.zh-CN.md) 与 [INSTALL.md](INSTALL.md)。

## 配置边界

安装器只合并 `config/config.toml.example` 中受支持的 Codex 设置：

```toml
[features]
multi_agent = true

[agents]
enabled = true
max_concurrent_threads_per_session = 50
default_subagent_model = "gpt-5.6-terra"
default_subagent_reasoning_effort = "medium"
```

官方参考：[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[Hooks](https://learn.chatgpt.com/docs/hooks) 与 [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference)。

| 文件 | 唯一职责 |
|---|---|
| `config/model_profiles.toml` | 执行与独立审查的模型 pin |
| `config/refill_policy.toml` | 父任务/双平面目标、节流与低水位 |
| `config/orchestration_policy_v2.toml` | 路由、预算、governor 与模型家族 pin |
| `config/retry_classes.yaml` | 确定性重试与 dead-letter 分类 |
| `config/triggers_v2.yaml` | 机械升级信号 |
| `agents/*.toml` | custom agent 指令、sandbox 与有序 nickname 候选 |

在不触碰用户凭据的情况下检查或切换仓库内 profile：

```bash
python harness/model_profile.py list --root .
python harness/model_profile.py set portable --root . --no-global --no-wsl
```

`three-family-example` 默认不激活。先把占位符替换成当前 Codex 或网关已经提供的模型 ID，让根对话使用第三个独立家族，再启用该 profile。切换器不会改写 provider catalog 或凭据。

## 控制循环

1. 根对话输出有界决策骨架，不亲自做批量工作。
2. 每个 packet 声明目标、授权路径、验收命令和约束。
3. DAG 门在派发前拒绝循环依赖和波次内写路径冲突。
4. Desktop 或 headless worker 在独立 worktree 中运行，并显式 pin 角色、模型和推理强度。
5. 脚本回放验收、检查 diff 边界并写入类型化生命周期事件。
6. L2 可以通过、要求重做、排序候选或升级，但不能发布。
7. 只有真正异常回到根对话；最终合并和发布始终由人触发。

等待、轮询、计数、常规重试和状态迁移由脚本或状态机处理，不额外消耗根模型轮次。

## 目录结构

```text
agents/      Codex 角色与有序 nickname 候选
config/      路由、并发、重试、触发器与托管 hook 策略
harness/     派发、状态机、补位、生命周期、门禁与恢复
hooks/       Codex 生命周期强制与上下文注入
launchers/   Windows 激活、Desktop 启动与 Observer
metering/    按角色/模型进行 token 归因和预算控制
schemas/     packet 与报告契约
scripts/     发布打包和完整性工具
tests/       单元、状态路径、安装器、安全与编排测试
```

运行状态只能写入 `data/`，报告写入 `reports/`；两者都被忽略，绝不能提交到 GitHub。

## 验证

```bash
python -m pytest tests -q
python scripts/gen_filelist.py .
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

CI 检查源码和配置语法、指令注意力预算、PowerShell 解析、隔离安装、托管文件闭包和秘密泄露。真实 provider 路由只作为显式本地 smoke test，因为公共 CI 不含用户凭据。

## 安全与限制

- hooks 以当前用户身份运行，不提升权限。
- provider 凭据留在用户自己的 Codex 或网关认证中。
- 工具门和 spawn 门只会拒绝操作，不会授予额外权限。
- L1/L2 可以阻止或升级，只有人类能够发布。
- 20/80 与根 token 25% 都是可配置 LOOP 策略，不是保证或 Codex 官方限制。
- 当前实现是单机控制平面，不是分布式调度器。

安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 历史与许可证

LOOP 的最初设计早于当前 Codex harness 能力被广泛作为开源实现提供。公开版本建立在文档化扩展面之上，不维护 Codex 二进制 fork。

MIT © 2026 [LEO001020](https://github.com/LEO001020)。详见 [LICENSE](LICENSE)、[CONTRIBUTING.md](CONTRIBUTING.md) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
