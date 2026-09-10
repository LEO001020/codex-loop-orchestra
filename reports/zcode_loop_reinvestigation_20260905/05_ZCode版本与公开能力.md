收到——那个 checkout 审计子代理卡死被取消了。我先自己花两条命令把真实 checkout 定位下来（单点事实），然后用一份「逐条列好具体命令、禁止原样重试」的更硬性脚本重新派一个审计代理，避免它再陷入循环。

E:\zcode 本身不是 git 仓库，但下面有三个候选 checkout：`zcode-loop-orchestra`、`codex-loop-orchestra`、`zloop-gen8`。我快速确认哪个是规格针对的活跃 zcode-loop checkout。

两个 checkout 都在（`zcode-loop-orchestra` 较新但干净、`zloop-gen8` 昨天还有提交且有 44 个未提交文件、带完整 plugin/commands/agents）。我先亲自核两个单点事实（zloop 插件的 command 定义与 hooks.json——这决定 R8 的 hook/命令设计是否对得上真实 ZCode 契约），然后重派 checkout 审计。

两个关键事实已到手：真实插件用的是 `${ZCODE_PLUGIN_ROOT}` 变量和 `type: "process"` hook 形状，且现有 `/zloop` 命令自带“禁用 Codex SDK”硬契约。现在重派 checkout 审计——这次我直接把命令写死成一次性脚本，让它没有任何重试空间：

三路审计已回来，几条载重结论（`/goal` 不存在、`injectAgentsMd` 字段、SessionStart 注入机制）按纪律我先亲自复核一遍，再给最终判定：

`/goal` 在整个 `~/.zcode` 下确实为 0 命中；cli 里也搜不到 `injectAgentsMd` 字段（倒是发现现有 gen8 仓库的 zloop-worker.md 自己就写着这个字段——R8 把这个幻觉字段原样继承了）。最后亲自读一眼官方 hooks 文档，确认 SessionStart 注入契约：

三路审计与我的交叉复核都已完成，可以给出最终判定了。

# R8 FINAL 方案独立审计结论

**总判定：R8 的大方向能真实适配 ZCode，但不能直接作为施工规范。** 它对 ZCode 宿主契约的核心声明约七成成立且比上一版显著更准（hook 事件、matcher、transcript_path、插件 MCP 声明方式全部对得上），但它引用的续接机制 `/goal` 在本机 ZCode 3.11.2 中**根本不存在**，`injectAgentsMd` 字段在 CLI 中**不存在**，"v3.7.1"版本史**无从考证**；另外它要重建的“真实 checkout”实际有两个候选且其一带 44 个未提交文件，方案自身对此没有消歧。还有一点必须先说明：你贴给我的 R8 文本在 §26「Windows K runtime」中途截断（停在"Jupyter Z"），MCP 生命周期、oracle、E2E 等后半部分我审不到。

## 一、致命/高优先级事实错误（3 项，均已亲测复核）

**1. `/goal` 命令不存在 —— 致命。** R8 §4 把“数小时任务推荐 `/goal <objective>` + `/zloop`”、"`/goal` 负责跨 round 的继续和自己的完成判断”作为整个续接模型的前提。我在整个 `C:\Users\hzq00\.zcode`（commands、skills、插件缓存、CLI 内置）检索 `goal`，**零命中**。本机可用的续接原语其实只有官方文档明确的：Stop hook “可请求 continuation（最多 3 次）"。R8 又明文禁止 Stop 无限续接循环——两件事叠加后，R8 的跨 round 续接故事**没有任何宿主机制承载**，这是全案最大缺口，必须在施工前改为：自定义 command + 会话内多轮、或明确接受 3 次 continuation 上限。

**2. `injectAgentsMd` 字段不存在。** 全 CLI 内容 grep 零命中。R8 §7 要求 zloop-worker.md frontmatter 写 `injectAgentsMd: true`——该字段会被忽略。AGENTS.md 注入是宿主默认行为（本会话本身就是证据），无需也不能声明。顺带发现：现有 `zloop-gen8\plugin\agents\zloop-worker.md` 的 frontmatter 里已经写着这个幻觉字段（还有 `thoughtLevel: max`、`maxTurns: 128`），R8 把存量错误原样继承了下来。

**3. "v3.7.1 起 subagent 注入 AGENTS.md”版本史不可信。** 本机实际版本是 **3.11.2**（ZCode.exe PE 版本 3.11.2.6792），无任何 changelog 佐证 3.7.1 这个里程碑。机制本身真实，但施工不应硬编码版本判断。另外“tools:'*' 时继承 primary session 的 MCP”我只找到间接证据，标 PARTIAL——这直接影响 R8“packet 不必重复 MCP 清单”的假设，施工时应实测一个 subagent 能否看到 zloop-code。

## 二、验证成立的关键声明（可放心施工）

- **Hook 契约全部对得上**：官方文档确认恰好七种事件（SessionStart/UserPromptSubmit/PreToolUse/PermissionRequest/PostToolUse/PostToolUseFailure/Stop）；SessionStart 触发源含 startup/clear/compact（还有 R8 漏写的 `resume`）；PostToolUse matcher 按工具名正则匹配，写 `"Agent"` 正确（Task↔Agent 别名存在）；hook stdin 确有 `transcript_path`/`session_id`/`cwd`——R8 §16 的 transcript 增量捕获在输入侧可行。
- **一处施工级修正**：SessionStart 注入不能像 R8 §15 那样裸文本 stdout——官方 schema 是严格 JSON，**必须输出 `{"additionalContext": "..."}`**，多余字段直接校验失败；诊断走 stderr、退出码 0/2/其它 的语义与 R8 的 fail-soft 设计兼容。现有 gen8 插件用 `type:"process"` + `${ZCODE_PLUGIN_ROOT}` 的形状是官方推荐且可跨平台的，R8 未写明，照抄现有 hooks.json 即可。
- **插件形状成立**：`.zcode-plugin/plugin.json` + 根级 `.mcp.json`（android-emulator 实例确认 stdio server 字段形状与 `${CLAUDE_PLUGIN_ROOT}` 插值可用）。
- **Windows 运行时前提成立且有本机实测背书**：本机 Python 3.14.3 默认 `ProactorEventLoop`，R8“不要全局 SelectorEventLoopPolicy、Jupyter 用独立 owner thread + BlockingKernelClient”的判断方向正确。但注意**本机当前完全没装 jupyter 栈**（无 ipykernel/jupyter_client/mcp/pyzmq），K 平面要从零安装。
- **上游版本声明全部属实**：PyPI 实测 ipykernel 7.3.0（2026-06-10）、6.31.0 存在、jupyter_client 8.10.0 为最新且无 9.x、mcp 2.1.1（2026-08-25，描述明言 "major rework"）。R8 的"7.3.x 优先 + conformance gate + 6.31.x 回退”策略有事实基础——唯一保留意见是 Python 3.14 × ipykernel 7.3 的组合本身就要过 conformance 才知道。
- **Git 机制成立**：本机 git 2.55.0.windows.3，`merge-tree --write-tree`、三种 clone 变体、`cat-file -e` 全部可用。clone-seed 的一个隐患比 R8 担心的小：实测本地路径 fetch 一个“不在 ref 尖端但可达”的 sha 是成功的（只有完全 unreachable 的对象才报 `not our ref`）；而 base_oid 永远等于 run ref 尖端，所以按 refspec（`refs/heads/zloop/run/<id>`）fetch 就绝对安全——建议 R8 把 seed 刷新明确写成 fetch ref 而非裸 sha。

## 三、对“真实 checkout”的指认歧义（R8 §5 无法按原文执行）

`E:\zcode` 本身不是 git 仓库，下面有三个候选：`zcode-loop-orchestra`（干净、9/3 最新提交、plugin 只有 hooks）、`codex-loop-orchestra`（另一项目）、`zloop-gen8`（9/4 最新提交、**44 个未提交文件、领先远端 13 个 commit、挂着约 16 个 worktree**、plugin 完整含 commands/agents）。两仓的 `src/zloop/` 各约 6300/7000 行，R8 删除清单命中真实代码：`db.py`（SQLite 确认在用，R8“不使用 SQLite"的删除目标真实存在）、`stage.py`、`wave.py`、`supervisor.py`、`promote.py`、`backend/zcode_native.py`、`research/broker.py`（仅 gen8）。R8 自己规定“不得盲删未知用户数据、不 reset/stash"——那么施工方第一个问题就是：**R8 没说重建落在哪个仓**。gen8 的 44 个未提交文件按 R8 自己的规则一个都不能动。另外两仓 `tests/` 实际是零测试函数，R8 §5 要求“跑当前测试并报告 collected/passed"会得到空集，“旧系统测试基线”这根安全网实际不存在。

## 四、裁定汇总

| 类别 | 项目 | 结论 |
|---|---|---|
| 致命 | `/goal` 续接前提 | 不存在，续接模型需重设计 |
| 致命 | 目标 checkout 未消歧 | gen8 带未提交工作，不能按 §5 直接开工 |
| 高 | `injectAgentsMd` / v3.7.1 | 虚构字段与不可考版本史，删掉即可 |
| 中 | SessionStart 注入格式 | 须改为 `additionalContext` JSON |
| 中 | 本机无 jupyter/MCP 栈、Python 3.14 兼容 | 施工前置条件，conformance 先行 |
| 低 | seed fetch 应按 refspec、`resume` 触发源 | 措辞补丁 |
| 成立 | hook 七事件/matcher/transcript_path、插件与 MCP 声明、Windows Proactor 架构分离、ipykernel 7.3/MCP 2.1.1 版本策略、merge-tree fan-in、无第二套权限系统 | 全部实测通过 |

一句话：**R8 的工程判断（单事件循环分离、事务性 fan-in、seed 缓存、conformance gate）质量高于前版且与真实环境相容，但它对 ZCode 宿主的续接层有一处致命幻觉、两处虚构契约，且没有回答“在哪个仓上重建”。修掉这四点、并补上被截断的后半份文档后，才够格当施工规范。**
我把 Opus5 的 R7 从头到尾按“可直接施工”的标准重新审了一遍，并重新核了它新增的关键证据。结论不是简单地选 R6 或 R7：R7 在 Git fan-in、缓存经济学、IPython 文献证据、多 Agent 多样性这几处确实比 R6 前进了一步；但它又在 Windows/Jupyter 运行时、Git clone、一等多样性协议和 MCP 生命周期上引入了新的过度设计或事实错误。

更关键的是，R7 本身甚至没有真正写完：上传稿停在 native ZCode E2E 的“中途触发……”处。 因而它不能直接成为施工规范。

下面这版我称为 R8 FINAL。这里的“FINAL”是指：以目前 ZCode 公开运行契约、Windows 环境、Project Jupyter、Git 2.55、MCP Python SDK 2.x 与我们已有研究证据为基础，作为实际重建 zcode-loop 的施工基线已经闭合。以后若 ZCode/Jupyter 本身改变，应该通过 conformance benchmark 替换实现，而不是继续往本体里加实体。

对 Opus5 R7 的最终审计

R7 最值得保留的判断，是它终于把你的 IPython 经验放到了正确的证据层级。Anthropic 的 Programmatic Tool Calling 已经直接测到：复杂研究任务平均 token 从 43,588 降到 27,297；一次代码块编排 20+ 个工具调用时，可以消掉 19 次以上模型推理往返。 Anthropic 的 MCP code-execution 例子又把约 150,000 token 的中间工具材料压到约 2,000 token，并明确采用“工具定义作为文件、按需读取”的 progressive disclosure。 CP-Agent 更直接：它以 persistent IPython kernel 为核心，只用通用 coding tools 和 prompt，在其 CP-Bench 101 个问题上全部解决。 所以 \(K\) 不是 optional accelerator，而是 ZLoop 的正式 computation plane。这里 R7 是对的。

但 R7 对 Jupyter 的实际施工结论过度保守。它规定 ipykernel>=6.30,<7，理由是 7.0 引入 subshell 及单独 shell-channel thread。 上游原话其实是：不使用 subshell 时意图保持与 6.x 向后兼容；如果遇到问题，“可以” pin <7，不是要求所有下游永久 pin。 更重要的是，现在最新稳定版已经是 ipykernel 7.3.0；7.1 又修过 7.x 的线程启动、background-thread output routing、ContextVar、Matplotlib 和 Windows KeyboardInterrupt 等具体问题。 所以最终策略不是“默认退回 6.x”，而是：

$$ \boxed{\text{latest-tested 7.3.x first}\rightarrow \text{conformance gate}\rightarrow \text{6.31.x fallback only on reproduced failure}} $$

真正严重的错误反而是 R7 要在 Windows 全局设置 WindowsSelectorEventLoopPolicy。 Python 官方明确指出：Windows 的 SelectorEventLoop 不支持 subprocess，而默认 Proactor 支持；stdio MCP 恰恰需要 subprocess。 如果照 R7 施工，为了适配 zmq.asyncio，反而会破坏 zloop-code parent 最重要的 stdio MCP 运行路径。

因此 R8 不再把 Jupyter 异步 socket 与 MCP asyncio 塞进同一个事件循环。父进程保持 Windows 默认 Proactor；每一个活跃 IPython session 由自己的 owner thread 持有同步 KernelManager + BlockingKernelClient。这不是奇怪的自造方案：当前 jupyter_client.KernelManager 默认 client 本来就是 BlockingKernelClient，Project Jupyter还直接提供 start_new_kernel() 这一同步 manager/client组合。 OpenAI 自己当前 gpt-oss 的本地 Jupyter backend 也直接采用 KernelManager + BlockingKernelClient。

这样 Windows 运行时得到一个非常干净的分离：

zloop-code parent
    │
    ├── asyncio / Proactor
    │      └── MCP stdio / HTTP clients
    │
    ├── K owner thread: root
    │      └── KernelManager + BlockingKernelClient → ipykernel
    │
    ├── K owner thread: J1
    │      └── KernelManager + BlockingKernelClient → ipykernel
    │
    └── K owner thread: J2 ...

这比 R7 的 AsyncMultiKernelManager + SelectorEventLoop + custom LocalProvisioner 少掉了一整个跨平台耦合层。

Windows interrupt 那条 R7 则应该保留。Jupyter kernelspec 默认 interrupt mode 是 signal；官方 KernelManager 源码同时明确说 Windows 的 signal path 只真正适用于 Unix，而 interrupt_mode="message" 会通过 control channel 发 interrupt_request。 所以 R8 的 runtime kernelspec 在 Windows 上明确写 "interrupt_mode":"message"。

Git 方面，R7 做出了重要改进：merge-tree --write-tree 的确优于 cherry-pick --no-commit。Git 官方说明它执行真实三方 merge 能力，却完全不碰 index/worktree，因此 conflict 可以成为一个零副作用 observation。 Claude Code 最近真实出现过 parallel linked-worktree 的 .git lock contention；13 个并行 Agent 的案例中 8 个 commit 失败，而且自动清理随后丢掉未提交工作。 因此 writer 使用独立 repository 仍然是正确结论。

不过 R7 宣称 --no-hardlinks 就能解决 local clone 的并发风险，这不成立。Git 文档把两件事分开写得很清楚：local clone 与正在变化的 source repo 可能竞态；--no-hardlinks 只是把 .git/objects 的硬链接改成复制。 因而 R8 使用一个更精确的两级方案：以正规 Git transport 从 Root 当前 Run HEAD 建立/刷新一个 immutable clone seed，然后在这个 seed 不再改变的 clone burst 内使用快速 local clone；如果 seed capability test 失败，就直接 per-Job --no-local。clone seed 只是可重建 cache，不是新的领域实体。

R7 的 merge-tree 后又创建两父 merge commit 也没有必要。它自己规定 -p old -p candidate，而 Git 明确规定一个 parent 是普通 commit，多个 parent 才是 merge commit。 ZLoop 的 candidate 本来就是一个暂存的候选世界，不应该永久污染 Run 的主拓扑。因此 R8：

merge-tree(old, candidate)
        ↓
merged tree
        ↓
commit-tree <tree> -p <old>
        ↓
single-parent integration commit

候选 provenance 写进 trailers：

ZLoop-Job:
ZLoop-Candidate:
ZLoop-Base:

更进一步，R8 不会“先推进 Run，再测试失败后 rollback”。merge-tree 得到 merged tree 后先构造未引用的新 commit，在一个短生命周期 host-only detached worktree上执行 checks；只有 checks PASS 才 CAS 更新 Run ref。于是成功前 Root checkout 和 Run ref 都不改变。这实际上把 fan-in 变成了一次接近事务性的：

$$ \text{compute}\rightarrow\text{materialize}\rightarrow \text{oracle}\rightarrow\text{CAS publish} $$

而且仍然没有 Stage。

R7 对 H 的基本方向正确，但“每次 prepare 都封一个 segment”也没有必要。PRO-LONG 真正要求的是完整过去留在 context 外并可程序检索； SKILL.state 要求的是 bounded current state。 文件系统记忆的新研究反而提醒我们，过度维护组织结构本身并不会稳定提升答案质量，而且会随增长退化。

所以 R8 不再为了 fan-out 重命名日志。H 永远 append-only，只在大小阈值时自然 rotate。Job snapshot 只是：

{"segment":17,"offset":4839217}

同一次 prepare 的全部 sibling 得到同一个 cursor。读取规则就是旧 segment 全读 + 当前 segment 只读到 offset。没有 hash，没有数据库，没有 snapshot copy，没有摘要。

R7 的 diversity 章节发现了真正的问题，但解决过头了。它强制每个 sibling 都有 diversity_anchor，还要求计算“触及不同文件集合的比例”。 DivInit 的确证明普通 parallel sampling 会因相似第一步查询而产生高度冗余，matched compute 下 diverse initialization 可提高表现。 Interaction Tax 又证明在 fan-in 前互看完整 solution 会快速抹掉差异。 但“修改不同文件”绝不是正确的 diversity metric——五个正确 Agent 很可能都定位到同一个 bug 文件。

所以 R8 只有一条很弱但有效的机制：lens 是 optional Job hint。只有 Root 针对同一个不确定问题发多个 sibling 时，才用不同 hypothesis/evidence/localization/verification lens；天然独立的模块工作不需要人工造差异。没有 KPI，没有文件集合评分，没有必须填 anchor。

R7 对 MCP 的另一个问题是用 2024–2025 年 MCP Python SDK 1.x 的 AnyIO cancel-scope bugs，推出“p=20 时必然崩，所以每个连接必须手写 owner actor”。 那些 bug 是真实的，例如 v1.6.0 的 cancel-scope cleanup issue。 但当前稳定线已经是 MCP Python SDK 2.x，而且官方明确把 v2 描述成 client/server engine 的 major rework，用来修复长期架构问题；当前 2.1.1 于 2026-08-25 发布。 v2 已提供 ClientSessionGroup 等高层生命周期能力。 因此 R8 的原则是：先用当前 SDK 的正式高层生命周期，conformance test 实际并发与 teardown；只有上游实测不满足要求才加一层 actor，而不是提前重写 SDK。

工具发现方面，R7 把 mcp.help() 换成 progressive-disclosure file tree 是对的；Anthropic 当前 Tool Search 也显示，当工具达到几十乃至数百时，全部 schema 常驻 context 本身会严重污染工具选择。 但 R7 又要求生成 Pydantic typed Python stub 树，这再次创造了第二份 schema semantics。R8 更小：保存原始 MCP JSON schema + 极短 index。模型 Read/Grep 所需 schema，然后在一次 python_exec 中使用统一的：

await mcp.call(server, tool, **kwargs)

以后如果真实 benchmark 证明 typed wrappers显著提高正确率，再生成；v1 不做。

最后，R7 还引入了一整节 cwd allowlist、执行字符串攻击和安全测试。 这违背了我们前面已经确定的 ZLoop 本体：它不是第二个权限系统。R8 只留下一个事实声明：zloop-code 是与当前 ZCode 同宿主权限运行的 trusted local computation substrate；它不是 sandbox。 不做 cwd allowlist、network policy、write policy、risk class、permission gate。进程树清理属于资源正确性，而非权限控制。

还有一个值得指出的事实错误：R7 说 subagent“不读 project memory”，因此 packet 必须重复仓库规范。当前 ZCode 文档恰恰相反：自 v3.7.1 起，subagent 默认注入用户级与 workspace AGENTS.md；当 tools:"*" 时也继承 primary session 已连接的 MCP。 所以 packet 只携带动态 ZLoop facts；静态 repo rules 继续由 AGENTS.md 承担。

到这里，最终本体可以真正收敛为：

$$ \boxed{ \text{ZCode-Loop} = P+\Sigma+H+G+K+O } $$

其中持久领域实体仍然只有：

$$ \boxed{\text{Run}+\text{Job}} $$

而 clone seed、kernel threads、MCP sessions、schema cache、verification worktree 全部只是可随时销毁重建的 physical cache/runtime mechanism，绝不升级成实体。

下面这份就是我建议直接交给 Codex 的最终施工稿。

ZCODE-LOOP R8 FINAL
Native ZCode · Native Windows · Run + Job · P + Σ + H + G + K + O

你现在负责对真实 zcode-loop checkout 做一次 breaking rebuild。

这不是架构讨论任务。不要再提交候选方案后停止。

你必须先审真实 checkout，再删除旧系统、实现新系统、重写插件、重写测试、执行基准和真实可执行验证，最后报告实际机械结果。

Codex 只是本次施工 Agent。

最终 ZCode-Loop runtime 不得依赖 Codex SDK、Codex app-server、Claude SDK 或任何第二套模型/Agent runtime。

如果本文与旧 zcode-loop 文档冲突，以本文作为本次重建规格；但必须先读旧实现以理解迁移范围和用户已有工作，不得盲删未知数据。

0. 最终本体

ZCode-Loop 只有六个长期职责：

P
= 极短、稳定的 operating law。

Σ
= 当前时刻的 bounded mechanical present。

H
= 完整、无损、append-only、位于模型 context 之外、可程序化检索的过去。

G
= Git 所拥有的代码物理现实。

K
= Windows-native、stateful、可丢弃的 programmatic computation plane。

O
= tests / commands / runtime / API 等真实环境反馈。

写成：

ZCode-Loop = P + Σ + H + G + K + O

唯一持久业务实体：

Run
Job

其它任何对象必须先证明自己拥有不可从上述事实推导的独立语义，否则不得成为 durable entity。

1. 绝对不建立第二套认知或 Agent runtime

ZCode Root 是唯一 cognitive owner。

ZCode 原生 Agent tool 是唯一 Agent lifecycle。

ZLoop Python 不：

spawn native ZCode agents
poll native ZCode agents
resume native ZCode agents
cancel native ZCode agents
persist native agent PID
persist native agent thread/turn/session/liveness
implement worker heartbeat
implement Agent scheduler
implement Codex worker
implement Claude worker
implement NativeDispatch/NativeResult

Native Agent 的完成结果由 ZCode 自己返回 Root。

Root 收到结果以后，才显式使用 ZLoop 的 collect/integrate/oracle primitives。

2. 模型写入自由是不变量

不得增加：

allowed_files
write_scope
touch_hints
owned_files
restricted_files
path ownership
risk class
network policy
file permission gate
PreToolUse write denial

不得在 zloop-worker prompt 写：

only modify ...
do not edit outside ...
you may only write ...

一个 Job 得到一个完整 private repository。

Agent 可以修改它认为完成 Goal 所需的任何项目文件。

并行隔离来自：

one Job = one independent Git repository

不是来自人为切割文件权限。

Root 同样可以直接编辑当前 Run workspace。

3. 明确删除的旧架构

删除 active production 中承担下列职责的代码：

Project entity
Stage
Wave
Task
Attempt
Launch
Supervisor
Controller
ControllerEpoch
Scheduler
WorkerHost
Task DAG FSM
READY frontier
NativeDispatch
NativeResult
CodexBackend
backend abstraction for native model workers
PromotionIntent
ResourceLease
write_scope
touch_hints
risk/network policy
C2C
ResearchBroker
H1 semantic memory
blackboard
checkpoint capsule
durable Kernel registry/database

不使用 SQLite。

不要把 SQLite 换成 DuckDB、Postgres、LMDB、Redis 或另一个数据库。

旧代码若仅服务于这些概念，应删除，而不是留下 compatibility wrapper。

4. ZCode 原生边界

ZLoop 必须以 ZCode 当前公开能力为外围 runtime。

使用：

/goal
native Agent/subagent
Plugin command
Plugin subagent
Plugin hooks
Plugin MCP

不要复制 /goal 的 continuation。

数小时任务推荐：

/goal <objective>
/zloop <objective>

/goal 负责跨 round 的继续和自己的完成判断。

ZLoop 负责：

current state
exact history
parallel repository worlds
programmatic code mode
Git fan-in
mechanical oracle

Stop hook 不实现无限 continuation loop。

5. 先审真实 checkout

读取当前适用：

AGENTS.md
README*
pyproject.toml
lock files
package/build config
plugin manifest
commands
agents
hooks
MCP declarations
src/zloop/**
tests/**
active architecture docs

记录：

git --version
python --version
git rev-parse --show-toplevel
git rev-parse --git-common-dir
git rev-parse HEAD
git status --porcelain=v2 --branch
git worktree list --porcelain

运行当前仓库定义的：

tests
lint
type check
build
smoke

报告实际：

collected
passed
failed
skipped
duration

不要引用旧文档里的历史数字代替实际 baseline。

不要 reset、stash、clean 或覆盖与重建无关的用户工作。

6. 目标代码形状

优先收敛为：

src/zloop/
    __init__.py
    cli.py
    state.py
    gitops.py
    history.py
    oracle.py
    hook.py
    doctor.py

    code/
        server.py
        sessions.py
        output.py
        bridge.py
        schemas.py
        helpers.py

如果真实实现证明某两个小模块合并更简单，可以合并。

不要为了与本文目录一一对应制造空文件。

7. Plugin 形状

目标：

plugin/
    .zcode-plugin/
        plugin.json

    commands/
        zloop.md

    agents/
        zloop-worker.md

    hooks/
        hooks.json
        zloop_hook.py

    .mcp.json

Plugin 只新增一个 ZLoop MCP：

zloop-code

不要注册：

C2C
ResearchBroker
ipybox
Loommux
additional model provider

当前 ZCode subagent 默认可以注入 workspace/user AGENTS.md。

zloop-worker.md 显式设：

name: zloop-worker
description: ...
model: inherit
tools: "*"
injectAgentsMd: true

静态 repository rules 留在 AGENTS.md。

Job packet 只携带动态 ZLoop facts。

8. Durable state location

解析：

GIT_COMMON_DIR = git rev-parse --git-common-dir

存放：

<GIT_COMMON_DIR>/zloop/
    current

    runs/
        <run-id>/
            run.json

            jobs/
                open/
                archive/

            history/
                000001.jsonl
                000002.jsonl
                ...
                current.jsonl

            cursors/

            artifacts/
                jobs/
                verify/
                python/

    cache/
        clone-seed.git

    runtime/
        mcp-schema/

注意：

cache/
runtime/

不是领域实体。

它们可被删除并重新构造。

所有可变 JSON：

write temp
flush
close
os.replace

ZLoop host-side mutation 使用一个短生命周期 OS file lock。

这个 lock 不是状态实体。

9. Run

zloop start "<objective>"

v1 要求当前 primary repository 的非忽略项目修改为空。

若有已有用户项目修改：

返回 ZLOOP_REQUIRES_CLEAN_BASE

不要自动 stash。

不要 reset。

不要把未知用户修改偷偷吸收到 baseline。

记录：

canonical_ref
initial_oid
root_worktree
run_ref

创建：

refs/heads/zloop/run/<run-id>

并把用户当前 ZCode workspace 切到该 Run branch。

从此：

当前 ZCode workspace
=
Root workspace
=
当前 integrated Run reality

不要创建 hidden Stage worktree。

run.json 只保存不能由 Git 推出的事实：

version
run_id
objective
root_worktree
canonical_ref
initial_oid
run_ref
created_at

不要保存：

state enum
current stage
cached head
task count
worker count
kernel state

active Run 由：

<GIT_COMMON_DIR>/zloop/current

表示。

Run HEAD 始终从 Git 查询。

10. Root direct edits

Root 可以直接 Read/Edit/Write/Bash。

不要要求 Root 自己的每个修改都变成 Job。

但 prepare/integrate/verify/promote 必须从 immutable Git state 出发。

因此这些操作开始前调用：

sync_root()

如果 Root workspace 没有非忽略变化：

no-op

若有变化：

创建 temporary index：

GIT_INDEX_FILE=<tmp>
git read-tree <current-run-head>
git add -A -- .
git write-tree

得到 tree 后：

git commit-tree <tree> -p <old-run-head>

显式设置 ZLoop author/committer。

再：

git update-ref <run-ref> <new> <old>

使用三参数 CAS。

最后：

git reset --hard <new>

于是 Root 的自由修改在 fan-out 前被机械正规化为新的 G。

11. Σ：bounded present

zloop status --json

只返回当前机械事实：

run id
objective
root workspace
canonical ref + OID
run ref + OID
root dirty flag

current exact verification fact for current OID

open Jobs:
    id
    compact goal
    optional lens
    base_oid
    candidate_oid if present
    latest candidate-check fact if present

current history cursor
history root

不要：

扫描全部 archived Jobs
返回整个 H
返回老计划
返回 semantic summary
返回所有历史 oracle

目标：

|Σ_t| = O(1 + current_open_jobs)
12. H：精确历史

不建立：

semantic memory
embedding store
vector DB
RAG memory
H1 summary
model-written canonical state

H 是 segmented append-only JSONL。

当前 segment：

history/current.jsonl

达到配置的 size threshold（初始可取 32 MiB）才 rotate：

current.jsonl
    ->
0000NN.jsonl

然后建新的 current。

不要因为每次 fan-out 就 rotate。

不要重写 sealed segment。

不要整理、重排或总结 sealed history。

13. History snapshot cursor

Job fan-out 需要相同过去，但不需要复制完整 H。

在 prepare 开始时，在 history append lock 内取得：

segment number
current segment byte offset

保存：

{
  "segment": 17,
  "offset": 4839217
}

同一个 prepare 产生的所有 sibling Job：

same base_oid
same history_cursor

读取语义：

all sealed segments < cursor.segment
+
cursor.segment/current segment bytes [0:cursor.offset]

后续 sibling result 即使已经 append 到 H，也位于 cursor 之后。

因此不会成为该 fan-out 的 dispatch history。

提供普通 Python helper：

zloop_history.iter_records(root, cursor)
zloop_history.grep(root, cursor, pattern)

底层仍然只是文件。

不要创建 memory service。

不要 hash。

14. H 来源

H 保存 ZLoop 可观察到的事实。

至少：

A.
ZCode transcript records

B.
PostToolUse(Agent) native completion

C.
ZLoop physical events：
run.start
root.sync
jobs.prepare
job.collect
job.integrate
oracle.run
verify
promote
finish
abort

不要创建复杂 semantic event ontology。

尽量保存 source record，而不是提前解释。

15. Hooks

Production hooks 只需要：

SessionStart
PostToolUse matcher=Agent
Stop

不要：

PreToolUse governance
PermissionRequest governance
UserPromptSubmit governance
Stop continuation loop

SessionStart 在 startup/clear/compact 时，只注入一个很小的 pointer：

Active ZLoop Run: ...
Run `zloop status --json` for exact current state.
This workspace is the current integrated Run reality.
Exact prior evidence lives under HISTORY_ROOT and is retrievable programmatically.
Continue Root -> useful native fan-out -> Root fan-in -> oracle -> Root reasoning.

不要注入 H 本身。

16. transcript_path O(delta)

ZCode 的 transcript_path 只在当前 hook 运行期间存在。

所以 Stop hook 持久化新记录。

每 session cursor 保存：

byte_offset
small previous overlap bytes
source_segment

如果新 transcript：

length >= old offset
且 offset 前的小 overlap 与旧 overlap 一致

则：

只 append offset 之后的完整 JSONL records

如果 continuity 断裂：

记录新的 transcript source-segment marker
从新 transcript 起点开始 append

不要从头重复复制 cumulative transcript。

不要 cryptographic hash。

History capture error：

写 diagnostic
hook success/fail-soft
不得影响 Git/Job correctness

Hook stdout 必须很小。

诊断写 stderr。

17. Job

Job 是：

Root 从一个精确 code/history snapshot
发出的一个独立 code world。

Job JSON：

{
  "version": 1,
  "job_id": "...",
  "goal": "...",
  "lens": null,
  "base_oid": "...",
  "history_cursor": {
    "segment": 17,
    "offset": 4839217
  },
  "workspace": "...",
  "checks": [...],
  "created_at": "..."
}

lens optional。

不要：

depends_on
files
scopes
permissions
risk
revision
attempt
worker
kernel id
process id

Retry = 创建一个新 Job。

18. Parallelism 与 lens

p=10–30 是能力上限目标，不是 occupancy KPI。

只有真正可并行的工作才 fan-out。

对于天然独立 workstreams：

不需要人工 diversity lens。

对于同一个高度不确定问题，同时发多个 sibling 时：

Root 可以为不同 Job 给不同 lens，例如：

hypothesis
evidence source
localization angle
runtime-vs-static angle
verification approach

lens 是认知提示，不是权限。

不要：

强制每个 Job 都有 lens
计算不同文件集合比例
建立 diversity score
因为两个 Job 修改相同文件就判定并行失败

Sibling 在 Root fan-in 前不需要彼此完整 solution。

19. prepare

zloop prepare jobs.json --json

输入：

[
  {
    "goal": "...",
    "lens": "... optional ...",
    "checks": [...]
  }
]

schema 不接受：

files
write_scope
depends_on
risk

prepare：

acquire host mutation lock
sync_root()
capture:
base_oid = current Run HEAD
history_cursor = current H cursor
create/update clone seed
create all Job repositories from exact base
write open Job JSON
return Job packets

同一 prepare 的所有 siblings：

exact same base_oid
exact same history_cursor
20. Clone seed：仅作为物理加速缓存

不要直接把“live Root local clone + concurrent modification”当成安全假设。

建立：

<GIT_COMMON_DIR>/zloop/cache/clone-seed.git

第一次：

用 regular Git transport 从 Root repository 创建 bare seed

优先：

git clone --bare --no-local <root-repo> <seed-temp>

验证后原子替换为 seed。

后续 prepare：

在 host mutation lock 中
用普通 Git fetch 更新 seed 的 refs/zloop/base
到 exact base_oid

验证：

git -C <seed> cat-file -e <base_oid>^{commit}

一旦 seed 更新完成：

在当前 clone burst 期间不要再修改 seed。

然后每个 Job：

git clone --local --no-checkout <seed> <job-dir>
git -C <job-dir> checkout --detach <base_oid>
git -C <job-dir> remote remove origin

因为 source seed 在 burst 中 immutable，
local hardlink object optimization 可以保留。

不要：

git clone --shared

如果 seed 初始化/刷新/capability test 失败：

fallback：

git clone --no-local --no-checkout <root-repo> <job-dir>
git checkout --detach <base_oid>

正确性优先于 clone optimization。

clone seed 随时可以删除重建。

21. Job workspace

优先：

<repo>/.zloop-workspaces/<run-id>/<job-id>/

把：

.zloop-workspaces/

只加入 Root repository：

.git/info/exclude

不要修改 project .gitignore。

Job clone 是完整 repository。

Agent可以自由：

git branch
git commit
git reset
git rebase
git checkout
add/delete/rename files

不要依赖 Agent commit history作为 candidate。

22. Job packet

prepare 返回 Root 可直接交给 zloop-worker 的 packet：

JOB_ID
WORKSPACE
GOAL
LENS                 optional
BASE_OID
HISTORY_ROOT
HISTORY_CURSOR
CODE_SESSION
MCP_SCHEMA_ROOT
CHECKS

CODE_SESSION 派生为：

run:<run-id>:job:<job-id>

Root session：

run:<run-id>:root

不要持久化物理 kernel id。

23. zloop-worker prompt

保持短。

正文使用如下语义：

You are executing one ZLoop Job.

WORKSPACE is a complete private repository representing the code state at dispatch.
Work from that repository.

You have full autonomy over this repository snapshot.
Change any project files you judge necessary to achieve GOAL.

If LENS is present, treat it as an initial investigation angle, not as a restriction.
Abandon it if evidence disproves it.

HISTORY_ROOT contains exact prior Run evidence.
HISTORY_CURSOR is the dispatch boundary.
When historical evidence matters, inspect that snapshot programmatically rather than loading all history into context.

CODE_SESSION identifies your persistent Python computation cache.
Use zloop-code for repeated short API/MCP calls, deterministic chains, large intermediates, log/dataframe processing, history analysis, and reusable computation state.

MCP_SCHEMA_ROOT contains exact cached MCP tool schemas.
Read only the schemas relevant to the current computation, then call tools programmatically from Python.

Use direct native tools when the next action depends on semantic inspection of the immediately preceding result.

Run useful real checks.

Leave the repository in the best complete state you can.

Return a compact report:
conclusion;
material changes;
checks actually run;
unresolved uncertainty.

Git/files and executed feedback are physical evidence.
Your prose is cognitive evidence.

不要加 file-write restrictions。

24. K：zloop-code

K 是一等 computation plane。

它不是：

durable memory
second Agent
job scheduler
shell replacement for every action

它是：

persistent namespace
compound computation
short-call batching
MCP/API composition
local data reduction

生产 substrate：

jupyter_client
ipykernel
IPython
MCP Python SDK 2.x

不依赖：

ipybox
Loommux
repl-mcp
Posit mcp-repl
clikernel
Datalayer jupyter server

这些项目只作设计/benchmark参考。

25. Jupyter version policy

不要在 pyproject 中硬钉 ipykernel < 7。

允许范围：

jupyter_client >= 8.10, < 9
ipykernel >= 6.31, < 8

施工时必须测试至少：

current stable ipykernel 7.3.x
fallback ipykernel 6.31.x

在当前 Windows 环境运行 K conformance suite：

startup
persistent namespace
stdout/stderr/result
top-level await
background-thread output sanity
interrupt
timeout
reset
crash/restart
repeated execution
Unicode cwd

若 7.3.x 全通过：

当前 lock file pin 7.3.x。

只有存在可复现失败且 6.31.x通过时：

lock fallback 6.31.x
记录具体 failing conformance test

不要仅因 major version 就降级。

v1 不使用 ipykernel subshell。

26. Windows K runtime：不要全局 SelectorEventLoop

zloop-code parent 在 Windows 保持 Python 默认 asyncio event loop。

即：

ProactorEventLoop

不要：

asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())

原因：

parent 需要可靠支持 stdio MCP subprocess。

Jupyter Z
