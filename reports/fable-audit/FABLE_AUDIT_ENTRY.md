# Fable 审计入口：Codex LOOP F2 + ipybox 当前覆盖层

审计日期：2026-08-10（Asia/Shanghai）

本包是当前 F2/旧 LOOP 环境之上的增量覆盖层快照，不是一个可以直接
覆盖现有环境的旧安装包。不要运行包内旧 `install.sh` 去覆盖当前机器。

## 先给结论

当前 ipybox headless 生命周期修复、48 路目标配置和 8765 观察面已经形成
可审计实现；但整个系统尚不能判定为最终发布通过，至少有六个明确缺口：

1. 最近五小时 all-in Sol effective share 为 45.93%，没有达到 20–25%；
   排除当前异常长的安装/审计根任务后是 22.82%，只能证明执行面接近目标，
   不能把根任务从最终 KPI 中删除。
2. K3 effective share 仅 0.46%。这是路由拓扑缺陷：K3 主要只做晚期条件
   verifier/reviewer，而 `passthrough_enabled=false` 会把非 `direct_l3` 动作
   升级回 Sol，仓库又缺少完整的 `send_l2 -> K3` 自动消费链。
3. Windows 的 OpenCodex catalog 在 23:03 被重新生成，V4/K3 已从
   1,000,000/800,000 漂移回 272,000/244,800；WSL/headless 因 CLI 显式
   override 仍按 1M/800k 工作。现有 Windows→WSL 整文件同步脚本存在把
   WSL 一起降级的风险。
4. `SHA256SUMS` 是旧基线，当前树有 12 条不匹配且没有覆盖新增 ipybox
   文件。交付包以顶层 `FILELIST.sha256` 为唯一当前完整性清单；旧清单只
   作为漂移证据保留，不能用于验收本包。
5. 单池 48/48 压力波尚未实际跑过。已验证的是 24/24 单池和用户观察到的
   跨任务全局 48 路流畅；不得把两者写成“单池 48 已验证”。
6. 2026-08-10 23:55:34，OpenCodex 从旧 PID 23124 更替为 PID 53300；
   Desktop app-server PID 21756 未变、WSL 未重启、orphan gateway=0。旧服务
   wrapper 随后每约 5 秒尝试再启动一次并得到“Proxy already running”，暴露
   出网关单实例所有权/接管缺陷。该事件不能归因于 ipybox。

## 建议阅读顺序

1. `README_FIRST.md`（本文件在包根的副本）。
2. `audit/environment-facts.json`：构建时机器事实与冻结的五小时口径。
3. `source/codex-loop-s-f2/reports/fable-audit/INTEGRATED_AUDIT_FINDINGS.md`：
   15 份独立报告的 P0/P1 合并结论与证据冲突。
4. `source/codex-loop-s-f2/reports/fable-audit/GATEWAY_INCIDENT_20260811.md`：
   打包末期 OpenCodex 健康阻塞、PID 更替与重复 wrapper 循环的最新证据。
5. `source/codex-loop-s-f2/reports/fable-audit/ORCHESTRATION_ARCHITECTURE.md`：
   Sol→K3→V4→K3→Sol 的目标编排层、状态机和接受门。
6. `source/codex-loop-s-f2/reports/fable-audit/SOL_TOKEN_GOVERNANCE_PROPOSAL.md`：
   turn-scoped meter、ControlPacket、headless 路由和低摩擦控制器。
7. `source/codex-loop-s-f2/reports/fable-audit/CONTROL_PACKET.md`：主 agent 对
   不继承根长上下文的 headless 子 agent 的控制通道。
8. `source/codex-loop-s-f2/reports/ipybox-headless-stability-20260810.md`：
   ipybox 懒启动、沙箱、父死亡、孤儿回收、并发和崩溃证据。
9. `audit/headless-agent-reports/`：15 份独立审计最终报告；03 号计量复核
   两次超时，无最终报告，状态文件中保持 fail-visible。
10. `FILELIST.sha256`：包内文件的当前 SHA-256 清单。

## 冻结指标

最近五小时，Windows Desktop 与 WSL/headless 合并，maintenance 按 user turn
分类而不是整条 rollout 分类：

| 口径 | Sol | V4 | K3 |
|---|---:|---:|---:|
| raw total share | 71.10% | 28.80% | 0.11% |
| effective share (`total - cached input`) | 45.93% | 53.61% | 0.46% |
| output share | 21.15% | 78.62% | 0.23% |

有效 token：Sol 5,248,803；V4 6,126,150；K3 52,906。

当前安装/审计根任务单独为 91.57% Sol effective，并贡献约 67% 的五小时
Sol effective。排除它后其余生产任务为 22.82%，但最终控制器必须使用
all-in 口径。

## 崩溃归因边界

23:13:11–23:13:37 创建 16 个 Desktop-native 950k 子会话；23:15:06
`codex.exe` app-server 重启。Electron/ChatGPT 外壳、OpenCodex PID 23124、
WSL 均未重启；WSL 内存充足，orphan gateway 为 0。该次证据支持
“Desktop app-server/对话控制层被大量 950k 原生子会话并发初始化推垮”，
不支持“ipybox 是唯一首因”。同样 16 路审计改走 headless 后，app-server
保持稳定；首轮 8 完成、8 超时，超时项通过原 session 的短 resume 收口，
其中 7 份成功返回，03 号再次超时；全过程没有创建新的 Desktop 子会话。

## Fable 必须审计的编排问题

1. Sol 是否只保留 DecisionSkeleton、歧义/冲突和最终裁决，同时仍对全部
   根决策拥有权威？
2. K3 `plan_expander` 能否在不更改 Sol/user 决策的前提下生成 DAG、验收、
   风险、依赖和 ControlPacket overlay？
3. `send_l2` 是否有 exactly-once 的 K3 headless 消费器，而不是静默 pass
   或升级回 Sol？
4. 现有 cold-start `passthrough_enabled=false` 应如何迁移为 layered routing，
   同时保留 explicit high-risk `direct_l3` 与 human release gate？
5. 子 agent 完整报告是否只落盘，根 agent 是否只接收最多 8 条/500 token
   的 schema-valid short result？
6. ControlPacket 的 decision-ledger、revision、cancel/relaunch、resume 与 stale
   revision 拒绝机制是否足以形成主→子控制通道？
7. K3 总占比 10–25%、其中 verifier/reviewer 2–8% 是否是合理观察带；如何
   防止为了配额烧 token 或让 V4 空等？
8. OpenCodex catalog 的模型上下文值应在哪里建立持久单一真相，避免服务
   重启再次覆盖 Windows 1M 设置？

## 接受门

Fable 只能在以下证据齐全后接受生产启用：

- meter 有 turn-scoped maintenance、replay 去重和 1h/5h/24h/7d fixture；
- 一条非平凡 fixture 完整走 Sol→K3 plan→V4→条件 K3→Sol；
- `send_l2` exactly-once 到 K3，high-risk `direct_l3` 不变；
- 三个分母均至少 2M effective-token 的连续五小时样本中，all-in Sol
  均不超过 25%，且 K3 明显高于 0.46%；
- 单池 48/48：0 failed、OpenCodex PID 不变、Desktop app-server 不重启、
  orphan gateway=0、ipybox 可用；
- Windows/WSL catalog 在服务同步和 Desktop 重启后仍为 V4/K3 1M/800k，
  新 Desktop 子 agent 实测 effective context 950k；
- child 大报告不能进入 root return channel；
- `FILELIST.sha256` 全部通过，秘密扫描无高置信凭证。

## 拒绝门

出现以下任一做法应拒绝：把根 token 隐藏成 maintenance、禁用 ipybox、
降低 48 总并发作为主修复、强制 K3 审查每个普通 packet、为了占比硬烧 K3、
复制完整根 transcript 给每个子 agent、或在没有 `send_l2` 消费器时只打开
passthrough。

## 官方边界

OpenAI 官方文档支持“用子 agent 把探索/测试/日志噪声移出主线程，并向主
线程返回摘要”的一般原则，也明确每个子 agent 会独立消耗模型与工具 token。
本包的 48 路、V4/K3 分工、ControlPacket、headless lifecycle 和 token 目标
都是本地 LOOP 设计，不是 OpenAI 产品保证：

https://learn.chatgpt.com/docs/agent-configuration/subagents
