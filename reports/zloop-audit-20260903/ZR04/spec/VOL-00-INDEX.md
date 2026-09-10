# VOL-00 — 索引与装载协议（执行 Agent 的导航中枢）

> **ZLoop Spec v1.0** · 2026-09-02 · 卷 00/22 · 层级 L0（常驻）
> **取代关系**：本规范库取代 `ZLOOP_GEN8_FINAL_EXECUTION_PROMPT_2026-09-02.md`（该文件保留为历史参考，不再直接执行）。
> **上游依据**：Gen-8 母稿 + 2026-09-02 独立审计（GLM-5.3 @ ZCode 3.10.2 真机，4 项 P0 / 7 项 P1 / 6 项 P2 修正已全部并入本库）。

---

## 0. 这套文档解决什么问题

执行 Agent 在实现过程中会被大量细节淹没：厂商契约、schema、竞态、测试、混沌注入、benchmark。
单一 2322 行母稿无法在执行期安全装载。因此本库的设计约束是：

1. **每卷自足**：一卷 + 其声明的依赖卷 ≤ 2 卷，即可支撑一个独立任务的全部决策；
2. **状态外置**：执行进度、已证实/证伪的事实、决策记录放在 `PROGRESS.md` / `DECISIONS.md` / VOL-02，不依赖模型记忆；
3. **装载协议**：任何时刻模型工作集 = 本卷 + VOL-01 + 当前任务卷（+ 声明依赖），用完即卸；
4. **现实优先**：真机观察与本库冲突时，现实获胜，先改库再继续（见 VOL-22 §3）。

## 1. 文档层级

| 层 | 内容 | 装载策略 |
|---|---|---|
| L0 | VOL-00（本卷）、VOL-01 宪章 | 常驻上下文 |
| L1 | VOL-02 平台契约、VOL-03 架构总览 | 开工首日 / 里程碑开始 / 事实存疑时 |
| L2 | VOL-04…VOL-19 子系统规范 | 按任务装载对应卷，用完卸载 |
| L3 | VOL-20 M0 探针、VOL-21 里程碑、VOL-22 执行手册 | 按阶段装载；迷失时必读 VOL-22 |

## 2. 卷注册表

| 卷 | 文件 | 装载时机 | 依赖 |
|---|---|---|---|
| 00 | VOL-00-INDEX.md | 常驻 | — |
| 01 | VOL-01-CONSTITUTION.md | 常驻 | — |
| 02 | VOL-02-PLATFORM-CONTRACTS.md | 里程碑开始 / 任何厂商事实存疑 / M0 后刷新 | 00 |
| 03 | VOL-03-ARCHITECTURE.md | 首次开工 / 跨子系统设计决策 | 01,02 |
| 04 | VOL-04-DATA-MODEL.md | 实现任何 schema / ID / 文件格式 | 03 |
| 05 | VOL-05-HOOK-BINDING.md | M2；任何 hook/绑定改动 | 02,04,07 |
| 06 | VOL-06-EVIDENCE-PLANE.md | M2–M3（H0/H1/H2） | 04,05 |
| 07 | VOL-07-CONTROL-DB.md | M3；任何 S/SQLite 改动 | 02,04 |
| 08 | VOL-08-STAGE-PIPELINE.md | M3+；Stage FSM / 风险 / dirty base | 04,07 |
| 09 | VOL-09-WAVE-PACKET.md | M6；wave/packet/launch 设计 | 04,07,08 |
| 10 | VOL-10-MATERIALIZATION.md | M6；REPORTED→MATERIALIZED 链 | 08,09,13 |
| 11 | VOL-11-PROMOTION.md | M6；canonical 晋升 | 08,10,16 |
| 12 | VOL-12-BACKEND-CODEX.md | M7；Codex SDK 后端 | 02,04,09 |
| 13 | VOL-13-WORKSPACES.md | M6–M7；worker 工作区 | 09 |
| 14 | VOL-14-SCHEDULER.md | M9；并发宽度 | 09,19 |
| 15 | VOL-15-RESEARCH-PLANE.md | M4；Luna/Kimi/Broker | 02,04 |
| 16 | VOL-16-C2C-AUDITOR.md | M5；C2C 审计 | 02,04,09 |
| 17 | VOL-17-SECURITY.md | 贯穿；任何信任边界改动 | 04 |
| 18 | VOL-18-TESTING-CHAOS.md | 贯穿；每里程碑收尾 | 全部 |
| 19 | VOL-19-BENCHMARK-EVAL.md | M10；因果评测 | 18 |
| 20 | VOL-20-M0-PROBES.md | Phase -1 / M0 全程 | 02 |
| 21 | VOL-21-MILESTONES-GATES.md | 里程碑切换 / release gate | 20 |
| 22 | VOL-22-EXECUTION-PLAYBOOK.md | 每阶段开始；迷失时立即重读 | 00 |

活文档（非规范，执行期持续更新）：
- `PROGRESS.md` — 当前阶段、已完成探针、阻塞项、下一步（**每次会话第一步读它、收尾前更新它**）；
- `DECISIONS.md` — 编号决策日志（格式见 VOL-22 §5）。

## 3. 装载协议（上下文纪律，硬规则）

1. **工作集上限**：并发装载 ≤ 本卷 + VOL-01 + 2 个任务卷。需要更多说明你把任务切得太大了——回 VOL-22 §2 切小。
2. **按表装载**：开始任务前查 §2 注册表与 §4 里程碑映射，只装载声明需要的卷。
3. **不再引用**（澄清：这不是物理卸载——token 一旦读入就不会离开 context；progressive disclosure 的真义是"一开始别读"，由 §2/§4 的按表装载实现）：任务完成后不得凭记忆引用该卷细节做决策，依赖前必须重读。
4. **迷失协议**（30 分钟找不到下一步动作、或发现自己即兴发明架构时）：
   ① 重读本卷 §4 找当前阶段 → ② 读 `PROGRESS.md` → ③ 读 VOL-22 §4 决策树 → ④ 执行最小下一步。
5. **事实更新协议**：任何 probe/实测与本库冲突 → 更新对应卷 + VOL-02 该条目的状态 → 在 DECISIONS.md 记一条 → 继续工作。禁止让库与现实漂移。
6. **禁止凭记忆写契约**：所有 schema/命令/字段名以 VOL-04…VOL-16 为准；不确定时重读，不要回忆。

## 4. 里程碑 → 卷映射

| 里程碑（见 VOL-21） | 必装卷 | 任务 |
|---|---|---|
| Phase -1 / M0 | 20, 02, 22 | 环境/旧系统考古、全部厂商探针、capability manifest |
| M1 | 05, 21 | no-op 插件 + 安装/卸装 |
| M2 | 05, 06, 18 | H0 + Session Binding |
| M3 | 07, 06, 08, 18 | SQLite S + H1/H2 + Stage FSM |
| M4 | 15, 18 | Research Broker |
| M5 | 16, 18 | C2C Stage Auditor |
| M6 | 08, 09, 10, 11, 13, 18 | Supervisor + Workspace + 物化 + 晋升 |
| M7 | 12, 13, 18 | CodexSdkBackend |
| M8 | 09, 22 | G↔L 循环（真实 /goal） |
| M9 | 14, 18 | 10–30 扩展 |
| M10 | 19, 21 | benchmark + freeze + 审计 |

## 5. 执行 Agent 启动指令（给未来会话粘贴）

```text
你在执行 ZLoop 项目。规范库位于 E:\zcode\zloop-spec\。
第一步：读 VOL-00-INDEX.md 与 VOL-01-CONSTITUTION.md；
第二步：读 PROGRESS.md 确定当前位置；若不存在，从 VOL-20 的 Phase -1 开始；
第三步：按 VOL-00 §4 装载当前里程碑的卷；
第四步：按 VOL-22 执行手册工作。规则：现实优先于文档；每完成一个
可验证单元就更新 PROGRESS.md；禁止跳过探针直接实现；
只有登录/2FA/不可逆变更/live-trading/网络策略修改才停下来等用户。
不要输出新的架构计划——架构已冻结在规范库中，你的工作是探针、实现、测试、记录。
```

## 6. 全局约定

- **数据根** `%ZLOOP_DATA%` = `~/.zloop`（Windows：`C:\Users\<user>\.zloop`）。
  布局：`%ZLOOP_DATA%/registry.json`；`%ZLOOP_DATA%/projects/<project_id>/{control.sqlite3, history/, blobs/, workspaces/, runs/, research/, c2c/}`。
- **ID**：run=`R###`；stage=`S##`（配 `stage_revision` 整数）；packet=`P##`（配 `packet_revision`）；wave=`W#`；launch=`L`+uuid 前 12 位；research=`RS###`；c2c=`C2C###`；epoch/attempt 为整数。
- **时间**：ISO-8601 UTC（`2026-09-02T08:15:30Z`）。
- **能力状态**（沿用 Gen-8 证据纪律）：`DOCUMENTED / OBSERVED / EXPERIMENTAL / UNKNOWN / UNAVAILABLE / OBSERVED_DIFFERENT`。
- **风险级**：`LOW / NORMAL / HIGH / CRITICAL`；`risk_effective = max(root请求, host确定性floor)`。
- **平台**：Windows 优先（本机 win32 10.0.26200，Git Bash 2.55 语义），路径全部按 Windows 处理；PowerShell/Python 内部不假设 POSIX。
- **审计修正编号**：P0-1…P0-4 / P1-5…P1-11 / P2-12…P2-17（见 VOL-01 §6 登记表）。
- **语言栈**：Python ≥3.11（本机 3.14.3）；v1 依赖仅 `openai-codex`（后端）+ 标准库；CLI 框架 argparse。

## 7. 紧凑术语表

| 术语 | 定义 |
|---|---|
| root | ZCode 主 Agent（GLM-5.3），唯一认知权威 |
| supervisor | cold host 控制器进程（非 daemon；见 VOL-03 §5） |
| first-class worker | ZLoop wave 创建的 Codex 执行单元，物理并行 |
| native subagent | ZCode 自带 Agent 工具的子代理，认知并行，不入 roster |
| H0/H1/H2 | 精确可观察历史 / 有界恢复检查点 / 可编程精确回查 |
| S | SQLite control DB，correctness-critical 生命周期权威（fail-closed） |
| G-COG | root 认知闭环形态分类 A/B/C/D（见 VOL-03 §3） |
| Stage / Packet / Wave / Launch | 语义小节 / 工作包 / 并发批次 / 物理执行实例（每 launch 独立 workspace） |
| materialization | host 把已验收 delta 合入当前 private stage snapshot |
| promotion | private staged_head 晋升 canonical（ff-only、受控） |
| C2C | 异种 Web Chat（ChatGPT Web）计划/结果审计服务 |
| bind-token | 一次性高熵令牌，PostToolUse hook 原子认领，绑定 session↔run |
| physical-effect oracle | Git/后端/文件系统等真实副作用来源，用于 crash 后对账 |
