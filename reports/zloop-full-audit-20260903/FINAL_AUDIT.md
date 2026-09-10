# ZCode-Loop 全景独立审计与接管报告

日期：2026-09-03—2026-09-04  
目标：`E:\zcode\zloop-gen8`  
控制框架：`E:\codex-LOOP\codex-loop-s-f2`  
参考 E2E：`C:\Users\hzq00\codex-with-chatgpt`（按用户要求未更新）

## 总体判定

ZCode-Loop 的查询壳可运行，但生产执行主链没有接通。`--help`、
`--version`、`doctor`、`project list`、`run list`、`binding status` 均机械
退出 0，全部 Python 模块也可 import；然而真实 Codex backend 在 CLI 中被
无条件阻断，唯一可达 wave 路径硬编码 Mock backend。因此“所有生产功能无法
运行”成立，不能被基础查询命令成功所掩盖。

## P0：真实执行 backend 不可达

- `E:/zcode/zloop-gen8/src/zloop/cli.py:1172` 对
  `args.backend == "codex"` 直接抛出 `CODEX_AUTH_BROKEN`。
- `E:/zcode/zloop-gen8/src/zloop/cli.py:1228-1229` 最终总是向 Supervisor
  传入 `MockWorkspaceBackend(repo)`。
- `CodexSdkBackend` 的 ThreadPoolExecutor(16) 确实存在，但 CLI 从未构造它，
  所以交接稿把不可达代码当成了生产并发能力。
- 即使直接接线也会失败：Supervisor 传给 `backend.start` 的是 `dict`，而
  `backend/codex_sdk.py:147-160` 要求 `WorkerSpec` 并访问属性。

## P0：C2C 原回归

原未提交实现每次 prepare 都执行 `cmd /c start msedge https://chatgpt.com`，
不复用标签或 prepared 包，不发送包、不等待回复、不回写结果，却立即返回
PREPARED；CLI 又把 PREPARED 当作 PASS。结果同时是无限弹窗和高风险门禁
错误放行。返回 packet 路径还与实际写入目录不一致。

本轮已改为复用 `XiaoDuoYa/codex-with-chatgpt`：

- 删除全部 Edge/OS URL 启动路径。
- outstanding `(run, stage, role)` 复用同一 C2C ID。
- 通过上游 CLI `doctor --no-fix --json` 检查 bridge，不复制 bridge、OAuth、
  tunnel、connector、session 或 browser 实现。
- ZCode 根 Agent 使用 workspace-local Skill，网页只走 ZCode 官方 IAB。
- Codex-LOOP 直接使用已安装 canonical Skill，并增加只读状态 adapter。
- PLAN/DONE/PASS 才满足对应 gate；BLOCKED/REJECT 拒绝；无结构文本 UNKNOWN；
  PREPARED/PENDING/UNKNOWN 均阻断。
- 门禁错误包含 c2c_id、真实 packet 路径与 canonical Skill 下一步。

机械结果：ZCode C2C 相关矩阵 20 passed，新增 Edge/cmd 进程数 0；
Codex-LOOP C2C/Gemini/dispatch/lifecycle 矩阵 99 passed, 1 skipped。

## P1：当前 working tree 不能发布

目标仓库 HEAD 为 `80c13ef`，相对 origin ahead 1；CLI、C2C、research、
Supervisor、测试及 egg-info 有未提交修改，Kimi CLI/port discovery/测试等为
未跟踪文件。D-25—D-29 无法从 HEAD 重建。Stage/Supervisor 的 dirty-base
fail-closed 会拒绝多条写执行路径。禁止在未分拣前 reset/clean。

## P1：控制状态污染

控制库同时存在 R001、R002 两个 ACTIVE run，controller PID 均为空；两个
ZCode session 分别绑定不同 run。CLI `_require_active_run` 静默选择最新 ACTIVE
而不是拒绝歧义，导致 session 以为操作 R001、CLI 实际落到 R002 的风险。

## P1：SQLite 并发降级

doctor 发现运行时 SQLite 3.50.4 位于项目认定的 WAL-reset 风险区间，实际使用
`journal_mode=DELETE`、`synchronous=EXTRA`、`wal_ok=false`。只读命令仍可用，
但 8—15 并发写目标与该串行化模式冲突，容易产生 busy timeout。

## P1：Hook 双轨与静默失败

当前 ZCode hooks 已启用，5 个事件，解释器路径存在；Hook 并非完全没装。
但用户配置硬编码目标 venv 的绝对路径，插件模式又使用相对路径，doctor 同时
警告旧机器级 Codex LOOP hooks 存在。Hook 的 fail-soft/退出 0 会把路径或版本
错配表现成“什么都没发生”。应只保留一个权威安装面并验证哈希/版本。

本轮为 canonical E2E 启用了 ZCode 官方 Browser Use、Skills 与 feature.skill；
保留了原 hooks 和 suppressedBuiltins。需要新 ZCode 会话/重载后生效。

## P1：Metrics 是未集成孤岛

`src/zloop/metrics` 只有 token、latency、concurrency、c2c_stats 函数。会话要求
的 collector.py、exporter.py、metrics/cli.py、`zloop metrics export` 和控制库
生产采集均不存在。完成声明不成立。

## P1：研究通道与执行 gate 冲突

Supervisor 在 wave 前探测默认 Kimi health URL；端口存活即以
`KIMI_SERVER_UP` 拒绝执行。研究模块同时增加 58627—58635 发现和 Kimi CLI
回退。测试通过把 URL 指向 59999 来规避，而非证明生产共存。需定义按 run 隔离
或显式生命周期，不能让研究服务存活等价于整个执行面停机。

## P1：测试证据不可信

历史日志存在 `6 failed, 294 passed, 2 skipped` 和 `270 passed, 2 skipped`，
没有当前工作树 301+ passed 的可信机械证据。旧 C2C 测试会真实打开 Edge；
另有代理仅输出点号却自报全绿。本轮只接受带退出码的定向结果，尚未宣称全套
PASS。即使全 mock 测试绿，也不能证明被 CLI 禁用的真实 backend。

## P1/P2：物化、Promotion 与路径边界

Acceptance 命令是宿主执行能力，其安全边界是 packet 授权来源；不能把任意来源
acceptance 当可信。rollback 使用 `reset --hard` + `clean -fdx`，只有严格保证
目标是独立 staging 时才安全。Promotion 跨 Git 与 SQLite，Git 已前进而 DB 写
失败会产生 dangling 窗口，必须用故障注入验证每个切点。worktree_fast 共享
canonical Git 管理区，高风险包应使用 strong clone 或更硬隔离。

## Codex-LOOP 自身治理

一次 headless canary 对 Gemini 返回 403（该 plane 的 token 无模型权限），而
Desktop native Gemini 可运行，说明 provider 权限是 plane-specific；403 出生
不得计入有效并发。GLM verifier 本轮也出现 504/长时间无终态，其不完整结果未
被用于结论。

Gemini 专属“长制品 + 短回执”已实施：只匹配
`antigravity/gemini3.8flash`，要求至少 20,000 非空白有效字符、8 个实质章节、
12 个文件行号证据、反例/失败模式/未知项；重复段落超过 20%、代码日志超过
50%、占位内容均失败。其他模型直接跳过，不受限制。

## 修复顺序

1. 接通真实 Codex backend，统一 Supervisor—Backend WorkerSpec，并做真实 auth
   canary。
2. 分拆并保护 dirty working tree，建立 D-23—D-29 的提交与验收边界。
3. 恢复单项目单 ACTIVE run 或强制显式 run/session 一致性。
4. 完成两个 workspace 的 canonical C2C 首次 setup 与单标签网页 E2E。
5. 解决 SQLite WAL 版本/依赖或降低并发承诺。
6. 统一 Hook 安装权威面并清除旧机器级冲突。
7. 解决 Kimi research 与 Supervisor gate 的生命周期冲突。
8. 实现 Metrics collector/exporter/CLI。
9. 建立无真实外部副作用的全套测试和真实 backend 最小 canary。
10. 对 Promotion/rollback/Windows 路径做故障注入。
+
## 当前边界

已完成：全面审计、C2C 弹窗止血与 canonical E2E 宿主适配、Codex-LOOP
canonical C2C adapter、Gemini 专属长制品治理、定向机械测试。

未完成：真实 Codex worker backend、首次 connector/setup、全量安全 pytest、
双 ACTIVE run 清理、Hook 去重、Metrics 实现。以上不能标记 PASS。
