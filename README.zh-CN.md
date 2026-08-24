<!-- size-justified: 中文项目主页；不内联日志、清单和运行状态。 -->
<div align="center">

# Codex LOOP Orchestra

**让 Codex 的高并发智能体团队持续运转。**

[![CI](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)

[控制回路](#控制回路就是产品) · [快速开始](#快速开始) · [为什么需要 LOOP](#为什么需要-loop) · [系统架构](#系统架构) · [English](README.md) · [安装指南](INSTALL.zh-CN.md)

</div>

Codex 可以并行启动子智能体，但一次并发不等于一支能够持续工作的团队。LOOP 在 Codex 之上加入持续调度、隔离执行、独立审计和实时监控，把一次性的并发调用变成一套能够自行补位的多智能体工程系统。

你只需告诉 LOOP 任务目标和希望保持的并发数。它会拆分任务、分派执行，并在某个子智能体结束后自动补位，直到任务池清空。少量任务可以直接在 Codex Desktop 中运行，方便随时查看；大批任务则可交给受监督的 WSL 无界面执行进程。根智能体、执行智能体和审计智能体可以分别选用不同厂商的模型；Observer 监控面板会实时显示每个智能体正在做什么。你不必反复催促“继续”，最终是否合并仍由你决定。

![Codex LOOP 实时面板：活跃智能体、执行环境、实际模型和任务名称](docs/assets/dashboard.png)

<p align="center"><sub>在一个界面中查看所有 Desktop 与无界面智能体的任务、模型、执行环境、运行状态和剩余容量。</sub></p>

## 控制回路就是产品

许多智能体运行框架都能一次启动一批智能体。LOOP 真正解决的是另一件事：只要任务池中还有可以并行处理的工作，它就持续维持用户设定的并发规模，并保证各项修改彼此隔离、结果可以复验、最终发布权始终掌握在人手中。

> **简单来说：** 你设定目标和并发数，LOOP 负责拆分、派工、补位和检查。一个智能体完成后，新的智能体会接替空位；常规等待与重试由程序处理；只有真正需要判断的异常才会返回根对话。
>
> **你能直接感受到的变化：** 不必反复要求“继续”或“再启动几个智能体”。更多工作可以同时推进，任务更快完成；隔离的工作目录避免相互覆盖，独立模型审计则降低同一种模型重复犯错的风险。

这套控制回路遵守以下规则：

1. 根对话只给出边界明确的决策方案，不亲自承担批量执行工作。
2. 每个任务包都写明目标、允许修改的路径、验收命令和约束条件。
3. 派发前先经过 DAG（有向无环图）门禁；存在循环依赖或写入范围重叠的任务不会进入同一批次。
4. Desktop 子智能体与无界面执行进程都在相互隔离的 Git 工作树中运行，并明确指定角色、模型和推理强度。
5. 脚本会重新运行验收命令、检查实际改动是否越界，并记录结构化的生命周期事件。
6. L2 独立审计层可以通过、退回重做、比较多个方案或上报异常，但无权发布。
7. 只有无法按既定规则处理的异常才返回根对话；最终合并和发布始终由人触发。
8. 等待、状态检查、数量统计、常规重试和状态迁移全部交给脚本或状态机，不额外消耗根模型轮次。

**模型负责判断，程序负责状态，异构模型负责审计，人类负责发布。**

## 快速开始

把这个仓库交给 Codex 或其他编码智能体，然后粘贴下面这段话：

```text
请从 https://github.com/LEO001020/codex-loop-orchestra 安装 Codex LOOP Orchestra。
先阅读 AGENT_INSTALL.md，检查当前环境并向我展示变更预览和备份方案；得到我确认后，
再启用 LOOP 并验证安装结果。不要读取、显示或修改任何 API 凭据。
```

如果希望手动安装，可直接查看[完整安装说明](#完整安装说明)或 [Windows、Linux 与 WSL 安装指南](INSTALL.zh-CN.md)。

## 为什么需要 LOOP

LOOP 的目标并不只是“多启动几个智能体”。当 Codex 这类原生智能体运行框架承担持续、高并发、多模型的软件工程任务时，会出现一组具体问题。LOOP 的每项设计都对应一个真实问题：

| 原生框架的局限 | LOOP 的做法 | 实际收益 |
|---|---|---|
| 一批子智能体会随着任务完成逐渐退出；提示词本身无法稳定维持并发数。 | 根据 Desktop 和无界面执行进程的实际运行数量，从有限任务池中自动补位。 | **持续维持高并发**，无需用户反复催促。 |
| 同一个模型既执行又自查，容易保留相同的推理盲点。 | 将根智能体、执行智能体和审计智能体分成三个独立阶段，每个阶段都可单独指定模型；第三方模型可通过 OpenCodex 等 Codex 兼容网关接入。 | **用异构模型交叉审计**，减少同源错误。 |
| 根据维护者的实际使用经验，Codex Desktop 中同时运行约 10–20 个繁忙的原生子智能体时，对话层容易失稳甚至崩溃。 | 保留少量可见的 Desktop 子智能体，把更大规模的执行任务交给受监督的 WSL 无界面执行进程。 | **绕开 Desktop 对话层的承载瓶颈**，同时保留根对话与子智能体之间的原生通信。 |
| 普通工具调用经常重复读取文件、解析数据并重建中间计算结果。 | 为无界面执行进程提供可选的 IPybox 持久 Python 内核，并按需启动。 | **跨多次调用保留计算状态**，DataFrame、索引和计数器无需反复重建。 |
| 高能力协调模型可能把大量轮次耗在搜索、测试、等待、轮询和重试上。 | 根智能体只负责规划与裁决；凡是能够按固定规则处理的生命周期工作，全部交给脚本。 | **把最强模型留给关键决策，同时缩短实际完成时间**。数十个执行智能体可以并行提高总吞吐量；使用 Flash 或扩散式、草稿加速架构的执行模型时，吞吐优势更加明显。根模型的生产 token 占比则按策略控制在 25% 以内。 |
| 原生随机昵称与独立的无界面进程分散在不同界面中，无法形成统一的运行视图。 | 使用有序编号，并将编号映射到任务名称、模型、执行环境、健康状态和剩余容量。 | **实时看清每个智能体的工作状态**，从一个 Observer 监控面板中发现补位缺口和异常。 |

默认策略是：每个父任务维持 20 个活跃智能体，单机 Desktop 与无界面执行环境合计最多维持 80 个活跃智能体。这些数值都可以调整，实际并发还会受到可并行任务数量、模型服务商容量和本机硬件的限制；它们不是 Codex 官方限制，也不是性能承诺。

表中提到的 10–20 个原生子智能体，是维护者在一个实际环境中的观察结果，也是采用双执行环境的直接原因；它不是公开基准，也不是 Codex 官方给出的上限。

## 系统架构

![Codex LOOP Orchestra 架构：根智能体协调、程序化控制、Desktop 与无界面执行、独立审计、人工发布和实时监控](docs/assets/architecture-overview.zh-CN.svg)

LOOP 将决策、执行、审计、状态管理和发布权限明确分开：根智能体负责规划与裁决；执行智能体在 Desktop 或 WSL 无界面环境中完成任务；审计智能体独立复核结果；脚本和状态机维护日常运行状态；Observer 统一展示两个执行环境的实际情况。任何智能体都不能自行完成最终发布。

### 持久 Python 计算环境

WSL 还承担 LOOP 的持久计算层。可选的 IPybox 集成会按需启动 Python 内核，并在同一会话中保留 DataFrame、解析后的索引、计数器等状态。这样，无界面执行进程可以在模型上下文之外处理大量文件、数据和计算结果，不必在每次调用时从头开始；Codex Desktop 则可以继续作为轻量的控制与监控界面。

> [!NOTE]
> Codex LOOP Orchestra 是独立的社区项目，并非 OpenAI 官方产品。它只安装 Codex 配置、自定义智能体、生命周期钩子和基于 `codex exec` 的执行工具，不分发也不修改 Codex 二进制文件。默认配置不依赖私有网关，所有凭据始终保存在仓库之外。

## 完整安装说明

推荐使用“**智能体引导、脚本执行**”的安装方式。[AGENT_INSTALL.md](AGENT_INSTALL.md) 会要求安装智能体先识别系统环境，展示变更预览与备份方案，等待用户确认，然后再调用与手动安装相同的确定性脚本。真正修改和恢复文件的是 PowerShell、Python 与 Bash 脚本，而不是模型临时生成的命令。

### 环境要求

- 支持子智能体和钩子的 Codex CLI，并已通过 `codex login` 完成登录
- Python 3.11 或更高版本
- Node.js 22 或更高版本
- Git 2.40 或更高版本
- Windows 使用 PowerShell；Linux/WSL 使用 Bash

### Windows

```powershell
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate
```

激活脚本会安装随项目提供的自定义智能体，只合并 Codex 支持的多智能体配置，备份需要管理的原有文件，写入钩子的绝对路径，并启用全局 LOOP 模式。安装完成后，请彻底退出并重新启动 Codex Desktop，然后新建一个任务。

```powershell
# 可选：用 LOOP 打开指定工作区并启动 Observer 监控面板
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
./launchers/Start-Codex-LOOP-Monitor.ps1

# 暂停 LOOP，但保留安装内容和已验证的备份
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# 恢复安装前的受管文件
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

### Linux/WSL

```bash
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./install.sh --repo "$PWD"
```

安装后重新启动 Codex 并新建任务。如需恢复激活前的受管文件，请运行：

```bash
./uninstall.sh
```

隔离测试安装、指定 `CODEX_HOME`、切换模型配置方案、启用无界面执行环境和故障排查等内容，请参阅[中文安装指南](INSTALL.zh-CN.md)或[英文安装指南](INSTALL.md)。

## 配置与模型路由

安装程序只会合并 `config/config.toml.example` 中列出的、Codex 官方支持的配置项：

```toml
[features]
multi_agent = true

[agents]
enabled = true
max_concurrent_threads_per_session = 50
default_subagent_model = "gpt-5.6-terra"
default_subagent_reasoning_effort = "medium"
```

官方文档：[子智能体](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[钩子](https://learn.chatgpt.com/docs/hooks)和[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

| 文件 | 作用 |
|---|---|
| `config/model_profiles.toml` | 指定执行智能体与审计智能体使用的模型和推理强度 |
| `config/refill_policy.toml` | 设置单个父任务和双执行环境的并发目标、启动节奏与补位阈值 |
| `config/orchestration_policy_v2.toml` | 设置模型路由、预算、并发调节和模型家族约束 |
| `config/retry_classes.yaml` | 定义自动重试规则和死信分类 |
| `config/triggers_v2.yaml` | 定义需要升级处理的确定性触发条件 |
| `agents/*.toml` | 定义自定义智能体的指令、沙箱权限和有序编号候选 |

以下命令可以查看或切换项目内的模型配置方案，不会读取或修改用户凭据：

```bash
python harness/model_profile.py list --root .
python harness/model_profile.py set portable --root . --no-global --no-wsl
```

根智能体、执行智能体和审计智能体都不必使用 GPT 模型。根智能体可以使用 Codex 或 OpenCodex 中当前选择的模型；执行和审计阶段则可在配置方案中固定为网关提供的其他模型 ID。`three-family-example` 只是一个未启用的三模型示例；切换工具不会修改模型服务商目录，也不会接触任何凭据。

## 目录结构

```text
agents/      Codex 自定义智能体与有序编号候选
config/      模型路由、并发、重试、触发条件和钩子策略
harness/     任务派发、状态机、自动补位、生命周期、门禁与恢复
hooks/       Codex 生命周期钩子和上下文注入
launchers/   Windows 激活、Desktop 启动和 Observer 监控面板
metering/    按角色与模型统计 token 用量并执行预算控制
schemas/     任务包与报告的数据结构
scripts/     发布打包与完整性校验工具
tests/       单元测试、状态迁移、安装、安全和编排测试
```

运行状态只能写入 `data/`，详细报告只能写入 `reports/`。这两个目录都已被 Git 忽略，不应提交到 GitHub。

## 验证

```bash
python -m pytest tests -q
python scripts/gen_filelist.py .
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

CI 会检查源代码和配置文件语法、指令文件的注意力预算、PowerShell 脚本解析、隔离安装、受管文件清单完整性以及凭据泄漏。由于公共 CI 不包含用户凭据，真实模型服务商的路由只在本地显式执行冒烟测试。

## 安全边界与已知限制

- 生命周期钩子以当前用户身份运行，不会提升系统权限。
- 模型服务商凭据保存在用户自己的 Codex 或网关配置中，不会写入本仓库。
- 工具调用门禁和子智能体启动门禁只能拒绝操作，不能授予额外权限。
- L1 与 L2 只能阻止流程或上报异常，最终发布权始终属于人类。
- 20/80 并发目标和 25% 根模型 token 占比都是可调整的 LOOP 策略，不是性能保证，也不是 Codex 官方限制。
- 当前版本面向单机编排，不是分布式调度系统。

如需报告安全问题，请按照 [SECURITY.md](SECURITY.md) 中的方式私下联系维护者。

## 项目历史与许可证

在 Codex 当前的智能体运行框架完整开源之前，LOOP 的最初设计已经完成。现在的开源版本建立在 Codex 已公开、已有文档的扩展接口之上，不维护 Codex 二进制分支。

MIT © 2026 [LEO001020](https://github.com/LEO001020)。详情参见 [LICENSE](LICENSE)、[CONTRIBUTING.md](CONTRIBUTING.md) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
