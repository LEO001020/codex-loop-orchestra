<!-- size-justified: 中文项目主页；不内联日志、清单和运行状态。 -->
<div align="center">

# Codex LOOP Orchestra

**让 Codex 超高并发团队持续运行的控制回路。**

[![CI](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)

[控制回路](#控制回路就是产品) · [快速开始](#快速开始) · [为什么需要 LOOP](#loop-为什么存在) · [架构](#loop-如何工作) · [English](README.md) · [文档](INSTALL.zh-CN.md)

</div>

Codex 原生能并行派出子 Agent；LOOP 把它们变成一套持续补位的工程系统。

你只需告诉 LOOP：要完成什么、希望同时运行多少个 Agent。它会自动拆分和派发任务；一个 Agent 完成后，就补上另一个，直到没有有用工作。任务既可在 Codex Desktop 中运行，也可交给受监督的 WSL/headless worker；执行和复审可使用不同厂家的模型；一个网页面板实时显示每个 Agent 正在做什么。你不必反复催促“继续”，最终合并仍由你决定。

![Codex LOOP 实时面板：活跃 Agent、执行平面、实际模型和语义任务名](docs/assets/dashboard.png)

<p align="center"><sub>一个界面查看全部原生与 headless Agent：任务、模型、执行平面、健康状态和容量。</sub></p>

## 控制回路就是产品

很多 harness 都能启动 Agent。LOOP 的产品特点，是用一条有界控制回路在有任务和容量时持续补位，同时隔离写入、机械验收，并把最终发布权留给人：

> **简单讲：** 你只需给出目标和希望保持的并发数。LOOP 把大任务排成安全队列；一个 Agent 完成后，自动补上新的 Agent；更宽的任务可从 Desktop 转交给受监督的 headless worker。每个 Agent 分开工作，另一种模型复审结果，脚本负责等待和常规重试。只有真正异常才找根对话，最终合并和发布仍由你确认。
>
> **对普通用户的直接结果：** 不用反复输入“继续”或“再派几个 Agent”。团队会持续工作，并行执行缩短完成时间，隔离工作与独立复审则减少互相覆盖和同模型重复犯错。

它由以下明确规则保障：

1. 根对话输出有界决策骨架，不亲自做批量工作。
2. 每个任务包声明目标、授权路径、验收命令和约束。
3. DAG（任务依赖图）门禁在派发前拒绝循环依赖和重叠写入范围。
4. Desktop 或 headless worker 在隔离 Git worktree 中运行，并显式固定角色、模型和推理强度。
5. 脚本重放验收命令、核对 diff 边界，并写入类型化生命周期事件。
6. L2 独立复审层可以放行、要求重做、排序候选或升级，但不能发布。
7. 只有真正异常回到根对话；最终合并和发布始终由人触发。
8. 等待、轮询、计数、常规重试和状态迁移由脚本或状态机处理，不额外消耗根模型轮次。

**模型负责判断，代码负责状态，独立模型负责复审，人类负责发布。**

## 快速开始

把这个仓库交给 Codex 或其他编码 Agent，并粘贴：

```text
从 https://github.com/LEO001020/codex-loop-orchestra 安装 Codex LOOP Orchestra。
先阅读 AGENT_INSTALL.md；检查我的环境，展示 dry-run 与备份方案，等待我确认后再
启用 LOOP 并验证安装。不要读取、输出或修改我的任何 API 凭据。
```

希望手动安装？直接跳到[完整安装说明](#完整安装说明)，或阅读完整的 [Windows/Linux/WSL 指南](INSTALL.zh-CN.md)。

## LOOP 为什么存在

目的不只是“启动更多 Agent”。当 Codex 这类原生 harness 被推向持续、超高并发、多模型工程时，会暴露一组具体失败模式；LOOP 的每项设计都直接对应其中一个问题：

| Codex/harness 的缺口 | LOOP 的设计 | 设计带来的优点 |
|---|---|---|
| 一次派出的 Agent 会随任务完成而减少；提示词不是补位策略。 | 统计 Desktop/headless 的真实运行数，从有界任务池自动补位。 | **让高并发持续运行**，不需要用户反复催促。 |
| 同一个模型家族执行又自查，可能共享同一盲点。 | 根模型、执行者与审计者构成三个独立阶段，每个阶段都可分别指定模型；第三方模型通过 Codex 兼容网关接入，例如 OpenCodex。 | **发现相关性错误**，让另一个模型家族独立挑战结果。 |
| 在维护者的实际使用中，Codex Desktop 原生并发达到约 10–20 个繁忙子 Agent 时，对话层容易失稳或崩溃。 | 保留可见的原生 Agent，把更宽执行交给受监督 WSL/headless worker。 | **绕开 Desktop 对话层的承载瓶颈**，同时保留根对话与子 Agent 通信。 |
| 普通工具调用会反复解析文件、数据与中间计算。 | 为 headless worker 提供可选、按需启动、会话内持久的 IPybox Python kernel。 | **跨调用继续工作**，保留 dataframe、索引与计数器。 |
| 协调模型可能把高阶轮次浪费在搜索、测试、轮询和重试上。 | 根对话只规划和裁决，确定性生命周期工作交给脚本。 | **把最好的模型用在决策上，同时缩短墙钟时间**：数十个并发执行者提高聚合吞吐；使用 Flash 或扩散/草稿加速架构的执行模型时优势更明显，同时把根生产 token 占比治理到 ≤25%。 |
| 原生 nickname 与独立 headless 进程不能形成统一运行视图。 | 用有序数字 ID，并映射到任务名、模型、平面、健康状态和容量。 | **实时看见每个 Agent**，在一个 Observer 中定位补位缺口。 |

LOOP 默认以每个父任务 20 个活跃 Agent、单机双平面合计 80 个 Agent 为目标。它们是可配置策略，受真实任务、provider 容量与硬件约束，不是 Codex 官方限制或性能保证。

上述 10–20 范围是维护者在单一环境中的观察，也是双平面设计的起因；它不是公开基准或 Codex 官方限制。

## LOOP 如何工作

![Codex LOOP Orchestra 架构：根协调、确定性控制、Desktop/headless 执行、独立审查、人工发布与实时观察](docs/assets/architecture-overview.zh-CN.svg)

这套架构把决策、执行、复审、确定性控制和发布权限分开，并让两个执行平面的真实状态进入同一个观察层。

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

### Linux/WSL

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

根对话、执行者和审计者都不必使用 GPT 模型：根对话保留用户在 Codex/OpenCodex 中选择的模型，执行与复审则可由 profile 固定到网关已提供的其他模型 ID。`three-family-example` 默认不激活；切换器不会改写 provider catalog 或凭据。

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
