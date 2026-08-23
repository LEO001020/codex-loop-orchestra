<!-- size-justified: 中文项目主页；不内联日志、清单和运行状态。 -->
<div align="center">

# Codex LOOP Orchestra

**把一个 Codex 任务，变成一支分工明确、并行工作的编码 Agent 团队。**

[![CI](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)

[30 秒开始](#30-秒开始) · [工作原理](#工作原理) · [English](README.md) · [完整文档](INSTALL.zh-CN.md)

</div>

Codex 已经很会写代码；LOOP 给它配上一支**可管理的团队**。

把 LOOP 安装到 Codex Desktop 或 CLI 之上，只给根对话一个目标：并行 worker 负责执行，不同模型家族负责复审，空闲槽位自动补充，一个实时面板显示每个 Agent 正在做什么。

## 为什么值得安装

- **更快完成大任务。** 不再等待一条漫长的串行对话，让可配置的 Agent 池持续处理真正有用的工作。
- **发现同模型的共同盲点。** 协调、执行和审查可以分别使用不同模型家族或供应商。
- **让最强模型专注决策。** 根对话只规划和裁决；搜索、编码、测试、等待和计数交给 worker 与确定性脚本。
- **看见整个系统。** 有序任务编号和实时 Observer，取代一墙随机英文名字与不可见的 headless 进程。
- **突破 Desktop 对话层承载。** 保留可见的原生子任务，同时把更宽的波次交给受监督 headless worker。
- **安装可检查、可撤销。** 自动备份、状态检查和完整还原；不修改 Codex 二进制文件。

![Codex LOOP Observer：统一展示 Desktop/headless Agent、语义任务名、实际模型与实时容量](docs/assets/dashboard.png)

<p align="center"><sub>一个界面查看原生与 headless Agent：任务、角色、模型、平面、健康状态和剩余容量。</sub></p>

## 30 秒开始

```powershell
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

重启 Codex Desktop，新建任务，然后要求根对话使用 LOOP。若希望让 Agent 先检查你的环境并引导安装，把 [AGENT_INSTALL.md](AGENT_INSTALL.md) 交给它。Linux/WSL 与完整手动说明见 [INSTALL.zh-CN.md](INSTALL.zh-CN.md)。

> [!NOTE]
> Codex LOOP Orchestra 是独立社区项目，不是 OpenAI 官方产品。它安装配置、custom agents、生命周期 hooks 和 `codex exec` 工具，不分发、不修改 Codex 二进制文件。

## 工作原理

![Codex LOOP Orchestra 架构：根协调、确定性控制、Desktop/headless 执行、独立审查、人工发布与实时观察](docs/assets/architecture-overview.zh-CN.svg)

1. **一个根对话负责协调。** 把用户目标拆成有边界的 packet 和依赖图。
2. **两个执行平面并行工作。** Codex Desktop 保留原生可见性与通信；受监督 headless worker 承接宽波次。
3. **LOOP 持续推动真实工作。** 确定性控制器处理状态、补位、重试与死信，不让根模型把轮次浪费在轮询上。
4. **不同模型家族检查结果。** 机械证据进入独立验证，决定通过、重做或有界升级。
5. **人类保留发布权。** 串行集成、反证式发布复审，最终只有维护者可以合并或发布。

默认控制目标是：每个父任务维持 20 个活跃 Agent、跨平面 80 个 Agent 包络、根模型有效生产 token 占比不超过 25%。这些是可配置的 LOOP 目标，不是保证值，也不是 Codex 官方限制。

## LOOP 改变了什么

| 普通 Codex 工作方式 | 使用 LOOP Orchestra |
|---|---|
| 一个对话混合规划、执行与状态工作 | 根对话协调，worker 执行，脚本管理常规生命周期 |
| 并发波次随 Agent 完成不断萎缩 | 只要还有合格工作，就持续补充空闲槽位 |
| 同一个模型复查自己的假设 | 执行与审查可使用独立模型家族 |
| Desktop 子任务名称信息量很低 | `task_01`–`task_50` 有序编号 + Observer 语义任务名 |
| 宽波次被绑定在 Desktop 对话层 | 原生 Desktop 与受监督 headless 执行协同运行 |
| 容易过度相信 Agent 自报成功 | 机械证据与独立验证共同把关集成 |

便携默认 profile 不依赖私有网关。生产 profile 可以引用用户已配置的路由模型 ID；凭据始终留在仓库之外。

## 面向 Agent 的安装方式

推荐方式是：**由 Agent 引导，但由确定性脚本执行。** 把仓库交给 Codex 或其他编码 Agent，并要求它遵循 [AGENT_INSTALL.md](AGENT_INSTALL.md)：

```text
请阅读 AGENT_INSTALL.md，先询问我选择中文还是 English。
只读检查我的环境，运行仓库支持的 dry-run，解释所有计划修改和备份；
得到我确认后，只调用仓库提供的确定性安装器并机械验证结果。
不要读取、输出或修改任何 API 凭据。
```

Agent 负责识别真实环境并解释操作；PowerShell、Python 和 Bash 脚本负责实际安装与还原。用户也可以完全手工执行相同入口。

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
