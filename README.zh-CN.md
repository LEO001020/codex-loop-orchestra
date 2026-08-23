<!-- size-justified: 中文项目主页；不内联日志、清单和运行状态。 -->
<div align="center">

# Codex LOOP Orchestra

**Codex 已经有了 subagents；LOOP 让它们成为一支受控、持续运行的工程团队。**

[![CI](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml/badge.svg)](https://github.com/LEO001020/codex-loop-orchestra/actions/workflows/ci.yml)
[![MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![Codex](https://img.shields.io/badge/Codex-multi--agent-111.svg)](https://learn.chatgpt.com/docs/agent-configuration/subagents)

[快速开始](#快速开始) · [为什么需要 LOOP](#从-codex-原生能力到受控工程系统) · [架构](#架构) · [English](README.md) · [安装指南](INSTALL.zh-CN.md)

</div>

**20 秒看懂：** Codex 原生就能并行派出多个 Agent，但任务一完成，这一批 Agent 就会逐渐散掉。LOOP 让团队持续运转：有空位就补上新任务，把结果交给另一个配置好的模型复审，大批量执行转到受控的后台 worker，全部状态集中在一个面板。根对话、执行者和审计者都可以使用你的 Codex/OpenCodex 环境提供的任意模型，不必使用 Codex 的 GPT 模型。LOOP 通过可逆的配置与工具安装，不改动 Codex 程序本体。

**安装：** 把本仓库和 [AGENT_INSTALL.md](AGENT_INSTALL.md) 交给 Codex（推荐），或者直接查看 [Windows/Linux/WSL 手动命令](#安装暂停与还原)。

Codex 原生已经提供了非常强的基础能力：并行 subagents、custom agents，以及根对话与子 Agent 之间的通信。但有这些能力，不等于大型任务就自动有了受控的工程流程：任务应该怎样拆分、子 Agent 可以修改哪些路径、并发结束后由谁补上新任务、什么证据才算完成、什么时候需要换一个模型复审、最终谁有权发布——这些问题仍然需要一套控制系统回答。

**LOOP 补的正是这层控制与运行系统。** 你只给它一个目标：根对话只做规划与裁决，worker 并行执行，脚本管理任务状态，由独立审查模型挑战执行结果，Observer（观察器）展示真实运行状态，最终发布权留在人类手中。LOOP 不是另一个聊天界面，也不修改 Codex 程序本体。

## 真正的高并发，是高并发不会自己掉下去

大多数多 Agent 演示展示的是**启动并发**：一次派出一批 Agent，然后数量不断下降。LOOP 控制的是**持续并发**。用户设定目标后，只要还有真实工作和可用容量，LOOP 就会核对 Desktop 与 headless 通道中真正运行的 Agent，并从有界任务池实时补上完成、失败或空出的槽位，不需要用户反复插话。

这就是“在提示词里要求保持 20 并发”和“让 20 成为运行时控制目标”的区别。只靠提示词编排——无论底层使用 Codex、Claude 还是其他模型——可以再次请求一批 Agent，但提示词本身并不拥有持久生命周期状态、有效并发计数器、受控任务池和确定性补位控制器；LOOP 把这些职责写进了代码。公开配置默认以每个父任务 20 个活跃 Agent、单机双通道合计 80 个 Agent 为目标；用户可以在自己的 Codex/网关和硬件真实容量内设置其他目标。

## 控制循环就是产品

LOOP 的价值不在于“能启动很多 Agent”——Codex 原生已经支持并行 subagents。真正的产品，是包围这些 Agent 的强制控制循环：

- **根模型把上下文消耗在搜索、批量读取、测试和状态汇报上。** LOOP 让根对话只输出有界决策骨架，不亲自做批量工作，把注意力留给任务分解、真正异常的裁决和最终综合。
- **委派目标只有一句自然语言，工作范围和完成条件都没写清。** 每个任务包（packet）声明目标、授权路径和验收命令，决策骨架同时声明全局约束。worker 只依靠有界输入就能完成任务，范围与完成标准随时可以核查。
- **并行任务可能循环依赖，或者写入相同路径。** DAG（任务依赖图）门禁在派发前拒绝循环依赖和写路径冲突，不安全的计划会在浪费模型时间、制造合并冲突之前被拦下。
- **worker 可能共享状态，或悄悄继承错误的角色和模型。** Desktop/headless worker 在独立 worktree 工作目录中运行，并固定指定角色、模型和推理强度。写入彼此隔离，路由可以检查，执行与复审还可以通过 Codex/OpenCodex 使用不同厂商的模型。
- **Agent 自己说 `PASS`，很容易被当成已经完成。** 脚本重跑验收命令、核对代码改动范围并写入结构化任务状态。能否被接受由可重复验证的证据决定，而不是相信 Agent 的自信表述。
- **审查者同时拥有发布权，会把验证和授权合并成同一个角色。** L2（独立模型复审层）可以通过、要求重做、排序候选或升级，但不能发布。独立审查可以阻止薄弱结果，却不会因此获得发布权。
- **所有异常都回根对话会浪费昂贵轮次；自动发布又拥有过大权限。** 只有真正异常回到根对话，最终合并和发布始终由人触发。常规工作保持低成本，含糊判断和不可逆操作进入正确的权限层级。
- **让 LLM 等待、轮询、计数、重试或记忆生命周期，会让控制变贵且不确定。** 确定性状态迁移由脚本与状态机处理，常规控制不额外消耗根模型轮次，并且可以回放与检查。

层级含义：**L0/L1 是脚本机械检查，L2 是独立模型复审，L3 是根对话的有界裁决，L4 是人类发布权。**

一句话概括：**LLM 负责判断，代码负责状态，独立模型负责复审，人类负责发布。**

## 看见系统，而不是满屏线程

![Codex LOOP 实时面板：活跃 Agent、执行平面、实际模型和语义任务名](docs/assets/dashboard.png)

Codex 原生能够显示子 Agent 活动。LOOP 在此基础上增加有序任务编号，并把原生子任务与受控的 headless（无界面后台）worker、有意义的任务名、实际模型、状态多久没更新、健康状态和剩余容量关联起来。Observer 只读：它报告真实状态，但不能派发、裁决或发布。

## 快速开始

把这个仓库交给 Codex 或其他编码 Agent，并粘贴：

```text
从 https://github.com/LEO001020/codex-loop-orchestra 安装 Codex LOOP Orchestra。
先阅读 AGENT_INSTALL.md；检查我的环境，展示 dry-run 与备份方案，等待我确认后再
启用 LOOP 并验证安装。不要读取、输出或修改我的任何 API 凭据。
```

希望手动安装？直接跳到[安装、暂停与还原](#安装暂停与还原)，或阅读完整的 [Windows/Linux/WSL 指南](INSTALL.zh-CN.md)。

## 从 Codex 原生能力到受控工程系统

LOOP 保留 Codex 原生能力，在上面增加让大型任务可以持续运行的工程策略：

- **Desktop 提供可见原生 Agent 和根子通信，但超大批量任务可能给对话传输层带来更多压力。** LOOP 保留轻量的 Desktop 执行通道，把更大批量任务交给受控 WSL/headless worker。**优势：原生体验不丢，重负载也不再全部压在 Desktop。** 维护者只在自己的环境中观察到约 10–20 个繁忙子任务时的不稳定；这是没有公开基准或复现记录的经验观察，不是 Codex 限制。
- **普通工具调用经常重复解析数据、重算中间结果。** LOOP 可为 WSL/headless worker 提供按需启动、会话内持久的 IPybox Python 工作台。**优势：在该 worker 的 kernel 会话存续期间复用数据表、索引、计数器和处理后的输出。**
- **没有显式职责分离时，根对话可能把高阶模型轮次花在执行与常规记账上。** LOOP 让根对话只负责规划与真正异常，worker 负责执行，脚本处理常规生命周期。**优势：把最强可用模型用在判断上，并把根 token 占比作为治理信号持续测量。**
- **原生子任务活动与独立 headless 进程出现在不同位置。** LOOP 用只读 Observer 聚合两种执行通道，并把有序编号映射到有意义的任务名。**优势：在一个视图里看到任务、模型、执行位置、健康状态、更新时间和容量。**

## 架构

![Codex LOOP Orchestra 架构：根协调、确定性控制、Desktop/headless 执行、独立审查、人工发布与实时观察](docs/assets/architecture-overview.zh-CN.svg)

从上到下阅读这张图：

1. **一次规划。** 根对话提供决策与边界；packet 组成经过冲突检查的 DAG。
2. **确定性控制。** 预算、派发、补位、重试、死信和生命周期迁移都留在代码里——常规控制不消耗根模型轮次，且可以重放检查。
3. **双平面执行。** Desktop 保留原生可见性与通信，受监督 headless worker 承接更宽波次。
4. **用证据验收。** L0/L1 机械检查进入独立 L2 复审；重做返回执行，真正不确定性才升级。
5. **由人掌握发布权。** 已验收产物进入串行集成与反证式发布复审，但只有人类可以合并或发布。

### 每个控制数字都有目的

| LOOP 策略 | 为什么存在，以及它的边界 |
|---|---|
| **每个父任务维持 20 个活跃 Agent** | 持续保持有用任务规模，而不是把首次派发数量当成成功。可配置目标，不是保证值。 |
| **两个执行通道合计最多 80 个 Agent** | 让多个对话与两种执行通道共享单机容量预算。不是 Codex 官方限制。 |
| **前 8 个 child 优先走 Desktop 原生传输** | 保持可见通道可用，把更大批量工作引导到 headless。工作协议中的传输偏好，绝不是并发上限。 |
| **每个会话配置 50 个子线程** | 为 20-Agent 团队、复审与替换任务留出空间。本项目写入的值，不是 Codex 通用默认值。 |
| **根模型有效生产 token ≤25%** | 检测批量工作或机械控制是否重新泄漏给协调模型。由 rollout 证据测量的治理目标，不是成本或质量承诺。 |

## 持久 Python 工具平面

WSL 不只是并发的后备通道，也是 LOOP 的持久计算通道。IPybox 是一个可选服务，用于在独立沙箱中运行 Python。用户自行安装并注册上游 MCP 服务后，LOOP 的按需启动封装可让 WSL/headless 执行 worker 保留跨工具调用的 Python 对象，并在模型上下文之外处理大输出。

这些状态是**单个 worker 的会话内状态**，不是永久存储：reset、崩溃、清理或 worker 退出都会移除它，不同 worker 之间也不共享 kernel。Desktop 原生 IPybox 保持禁用，使 Desktop 继续承担轻量控制与观察。上游依赖是可选项，不随仓库打包；版本边界见 [VERSIONS.lock](VERSIONS.lock) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 安装、暂停与还原

推荐方式是：**由 Agent 引导，但由确定性脚本执行。** [AGENT_INSTALL.md](AGENT_INSTALL.md) 要求先识别环境、展示 dry-run、解释备份、等待人工确认，再由安装器实施并机械验证。

前置条件：支持 subagents/hooks 的 Codex CLI、Python 3.11+、Node.js 22+、Git 2.40+，以及 PowerShell 或 Bash。

### Windows

```powershell
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Activate

# 可选：启动工作区与 Observer
./launchers/Start-Codex-LOOP-Desktop.ps1 -TargetWorkspace C:\path\to\repo
./launchers/Start-Codex-LOOP-Monitor.ps1

# 暂停：停止向新任务注入 LOOP，保留安装与备份
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# 回滚：从经过验证的安装前备份恢复托管文件
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

### Linux / WSL

```bash
git clone https://github.com/LEO001020/codex-loop-orchestra.git
cd codex-loop-orchestra
./install.sh --repo "$PWD"

# 回滚托管文件，并删除未被用户修改的 LOOP Agent TOML
./uninstall.sh
```

激活后完全重启 Codex，再新建任务。隔离安装、显式 `CODEX_HOME`、headless 前提、模型 profile 与故障排查见 [INSTALL.zh-CN.md](INSTALL.zh-CN.md)。

## 模型分离，不碰你的凭据

根对话、执行者和审计/复审者是三个彼此独立的模型选择。根模型由用户在 Codex 或兼容 OpenCodex 的运行环境中选择，LOOP 再通过当前 profile 固定执行与复审子 Agent。**三种角色都不要求使用 Codex 的 GPT 模型：** 只要模型 ID 已由你的 Codex/OpenCodex 网关提供，就可以让它们使用不同厂商的模型，包括专门的执行模型与独立审计模型。provider 注册与凭据始终留在用户自己的环境中。

安装器只合并 Codex 已文档化的配置；LOOP 策略保留在项目自己的文件中：

| 文件 | 设计目的 |
|---|---|
| `config/model_profiles.toml` | 让执行与审查路由显式、可检查、可轮换。 |
| `config/refill_policy.toml` | 把并发变成带节奏和下限阈值的持续目标。 |
| `config/orchestration_policy_v2.toml` | 集中管理路由、预算、配额控制与 IPybox 执行通道策略。 |
| `config/retry_classes.yaml` | 让常规重试与 dead-letter 决策保持确定性。 |
| `config/triggers_v2.yaml` | 把机械风险信号转换为复审或升级。 |
| `agents/*.toml` | 复用 Codex 原生 custom-agent 指令、sandbox 与有序 nickname 候选。 |

```bash
python harness/model_profile.py list --root .
python harness/model_profile.py set portable --root . --no-global --no-wsl
```

便携 profile 不需要私有网关。`three-family-example` 默认不激活：启用前先把占位符换成你自己的 Codex 或网关已经提供的模型 ID。LOOP 不编辑 provider 凭据或 catalog。

官方参考：[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[Hooks](https://learn.chatgpt.com/docs/hooks) 与 [Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference)。

## 信任、安全与边界

- LOOP 是独立社区项目，不是 OpenAI 官方产品，也不代表 OpenAI 背书。
- 项目使用文档化配置、custom agents、hooks 与 `codex exec`，不分发、不修改 Codex 二进制文件。
- hooks 使用当前用户权限；门禁只能拒绝操作，不能授予额外权限。
- 凭据始终留在用户自己的 Codex 或网关认证环境中，并被排除在发布包之外。
- L0/L1/L2 可以阻止、重做、排序或升级，但都不能发布；最终合并与发布始终由人触发。
- 当前版本是单机控制平面，不是分布式调度器。
- 多模型分离是一种可用设计机制，不是已经通过基准证明的质量保证。
- 20/80、50、Desktop 传输偏好和 ≤25% 都是 LOOP 策略，不是 Codex 官方限制或普遍性能承诺。

安全问题请通过 [SECURITY.md](SECURITY.md) 私下报告。

## 验证、阅读与贡献

```bash
python -m pytest tests -q
python scripts/gen_filelist.py .
python scripts/gen_sha256sums.py . --output SHA256SUMS
sha256sum -c SHA256SUMS
```

CI 检查语法、注意力预算、PowerShell 解析、隔离安装器、托管文件闭包、归档边界和秘密泄漏。公共 CI 不含私有 provider 路由，因此真实路由仍然是显式本地 smoke test。

源码地图：`agents/` 定义角色，`config/` 定义策略，`harness/` 实现状态机与门禁，`hooks/` 强制生命周期，`launchers/` 负责激活和 Observer，`metering/` 归因 token，`schemas/` 定义契约，`tests/` 提供机械证据。运行态 `data/` 与 `reports/` 都被忽略，绝不能进入公开发布包。

公开版本建立在 Codex 已文档化的扩展点之上，不维护二进制 fork。MIT © 2026 [LEO001020](https://github.com/LEO001020)。详见 [LICENSE](LICENSE)、[CONTRIBUTING.md](CONTRIBUTING.md) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
