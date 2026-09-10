# ZLoop 运维手册（Operations）

> 适用对象：ZLoop 的安装、卸载、诊断、回滚，以及本机遗留（OLD-LOOP）卫生项处置。
> 契约依据：`E:\zcode\zloop-spec\VOL-05-HOOK-BINDING.md`（安装面）、VOL-20 P-HYG1、VOL-21 M1 gate。
> **现状（2026-09-02，round 3 诚实台账）**：`zloop install / uninstall / doctor` 已实现并有测试（M1 交付物；hooks 已在本机用户级 config 安装，5 事件，见 §2 与 D-9；D-16 plugin 打包与项目级 scoping 本轮接线中）。`zloop rollback` 是 M10 交付物，尚未实现。`zloop-hook` capture / bind-token / recovery 已实现（M2）。Codex 路由已实测可用（D-14，opencodex/cliproxy），不再有"重登解锁"项；当前最高优先风险是 P-SEC1 隔离失败（D-17，见 §7 行动 (0)/(3)）。§8 是 v0.2 命令面参考——其中标注 "wiring in progress" 的命令是本轮并行 agent 正在接线、**尚未在代码中出现**的，在 `zloop --help` 确认之前不要假定可执行。

---

## 1. 数据根与路径

- 数据根 `%ZLOOP_DATA%` = `~/.zloop`（Windows：`C:\Users\<user>\.zloop`）。
- 布局：`%ZLOOP_DATA%/registry.json`；`%ZLOOP_DATA%/projects/<project_id>/{control.sqlite3, history/, blobs/, workspaces/, runs/, research/, c2c/}`；备份目录 `%ZLOOP_DATA%/hygiene-backup/`。
- 测试与演练**绝不**使用真实 `~/.zloop`——一律临时目录/隔离数据根。

## 2. 安装 hook（`zloop install`）

1. `zloop install` 将 zloop hook 以 **process 型**注册写入**用户级** `~/.zcode/cli/config.json` 的 `hooks` 块（argv、无 shell、同步执行）。
   - 只走用户级 config（或 plugin `hooks/hooks.json`）；**workspace 级 `.zcode/config.json` 的 hooks 会被 ZCode 整体忽略**。
   - 注册前若 config 中已有 hooks，先做备份到 `~/.zloop/hygiene-backup/`。
2. `hooks` 块必须带 `enabled:true`（否则默认禁用）。默认 `timeoutMs=60000`、`maxOutputBytes=32768`（hook 自身 stdout 上限）。
3. 注册 **5 个 post-execution 事件**（D-9：SessionStart / UserPromptSubmit / PostToolUse / PostToolUseFailure / Stop；PreToolUse / PermissionRequest 因热路径进程税被删除）。hook 代码本身保留 7 事件分派——若配置里手工注册了全部 7 个，仍能正常工作。
4. **关键限制**：hook 配置按 ZCode session **快照**——安装/改动只对**新开的 session** 生效（见 §7 验证清单）。

## 3. 卸载（`zloop uninstall`）

1. `zloop uninstall` **只移除 zloop 管理的 hooks 块**；用户可能存在的其他 hook 注册原样保留。
2. 移除前把当前 config 状态备份到 `~/.zloop/hygiene-backup/`。
3. 卸载后需**新 session** 才能观察到 native 语义恢复（I1：无注入、无 hook 进程、turn 延迟与 Z0 无可感差）。
4. **数据不在卸载范围内**：`~/.zloop`（registry、各项目的 control.sqlite3、history/、blobs/ 等）在卸载后全部保留；删除数据根是单独的、显式的手动动作。

## 4. 诊断（`zloop doctor`）

`zloop doctor` 检查并报告：

- 用户级 `~/.zcode/cli/config.json` 中 zloop hooks 块是否存在、`enabled` 是否为 true；
- 数据根 `~/.zloop` 可写性、journal/DB 路径布局；
- SQLite 运行时版本 vs D-1 版本闸门（≥3.50.7 backport / ≥3.51.3 才允许 WAL，本机 3.50.4 ⇒ DELETE+EXTRA）；
- 探针注册表（`artifacts/capabilities/manifest.json`）中 BLOCKED/PARTIAL 项摘要。

## 5. 回滚（rollback）

- `zloop rollback`（演练命令 `zloop rollback --run`，M10 release gate 必测）：停用 hooks 与 supervisor，恢复纯 ZCode native 语义，同时保全用户 repo 状态与 `~/.zloop` 数据。
- 原则：**回滚/卸载永不损失数据**——`~/.zloop` 数据、用户 repo 的未提交修改都不在删除范围（硬禁令 7）。

## 6. OLD-LOOP 卫生项（P-HYG1，M0 硬前置）

**事实**：`C:\ProgramData\OpenAI\Codex\requirements.toml` 是**存活中的旧 LOOP 机器级 Codex hook 注册**（`global_loop_mode.py` 等，含硬编码 `E:\codex-LOOP` 路径）。ZLoop 的 first-class worker 是 Codex 进程 ⇒ 会被旧钩子命中。

**当前状态（2026-09-02，round 3）**：

- 已清点并**备份**到 `~/.zloop/hygiene-backup/`（4571 字节，sha256 `6c0f4f7b461ccc2bdf57725d0e65478062dfdc8a0755dc59c7d8815f63af8de4`）。
- **D-14 更正：新路由下停用并非必需**——Codex workers 已携带 `CODEX_LOOP_REQUIREMENTS_TOML` 空 override（D-5），旧钩子不会再命中 ZLoop worker。但存活的旧注册是一个**混淆变量**（live 探针归因困难），故仍建议安全停用。

**安全停用程序（rename-first，可逆，须用户确认）**：

1. 确认备份存在且 sha256 与上记一致。
2. **重命名**而非删除：`requirements.toml` → `requirements.toml.disabled-YYYYMMDD`（保留在原目录，可随时改回；若权限要求移动，则移入 `~/.zloop/hygiene-backup/`）。绝不直接删除 ProgramData 文件。
3. 验证无旧 LOOP 拦截：在**临时 cwd** 复跑一次干净的 Codex headless turn，确认输出中无任何 `global_loop_mode` / `E:\codex-LOOP` 痕迹。
4. 如有异常：改回原名即恢复原状（rename-first 的意义正在于此）。

**worker 进程缓解（若选择不停用 / 停用前）**：

- 为 Codex worker 设置环境变量 `CODEX_LOOP_REQUIREMENTS_TOML`，指向一个**空的 requirements 文件**，绕过旧版 managed hooks。
- 替代 fallback（VOL-20 P-HYG1）：为 first-class worker 使用隔离的 `CODEX_HOME`（全新 codex home + 显式登录），并在 manifest 记录原因。

**其他遗留**：`~/.codex` 下 5 个 `auth.json` 变体与 config 备份一律禁止作为凭据来源（割接时轮换/清理，见 VOL-17 §6）；旧树 `E:\codex-LOOP` 只读归档，不再修改。

## 7. 用户行动清单（round 3，第三方审计批准的顺序）

> D-14 更正：Codex 路由已实测可用（opencodex/cliproxy，P-CDX1 PASS），**不再有"codex login 重登"解锁项**。按下述顺序执行；(0) 无需用户操作。

**（0）GLM 本轮自修（进行中，无需用户操作）**：hook 项目级 scoping + plugin 打包（D-16）；D-20 接管死亡证明；research 三轴语义（D-18）+ searcher-only 工具面（D-19）；`zloop stage promote` CLI；redact 精化（D-21）。落地验证以套件 owner 报告 + `zloop --help` 实测为准。

**（1）安全停用旧 ProgramData requirements.toml（§6 rename-first 程序）**：备份已在（sha256 `6c0f4f7b…`）；重命名→临时 cwd 验证无旧 LOOP 拦截。**新路由下并非必需**（workers 携带 `CODEX_LOOP_REQUIREMENTS_TOML` override），目的是移除混淆变量。

**（2）新开一个 ZCode session**（hook 配置按 session 快照，旧 session 永远看不到新 hooks），并在其中依次：
   - **P-HK 组 + P-BIND1**：跑任意 Bash（如 `echo zloop-check`）触发 PostToolUse → 确认 `~/.zloop/projects/<project_id>/history/sessions/` 出现该 session 的 H0 journal；`zloop run start` 前台输出 bind token 并被 claim。若 journal 未出现：确认 session 确为新开（非恢复/复用）、检查 `~/.zcode/cli/config.json` hooks 块 `enabled:true`、跑 `zloop doctor`。
   - **G-COG（最高优先探针）**：以 **MOCK waves** 驱动 `/goal` 与后台 task-notification 的交互——**三次 串行→并行→串行 循环**；全程**无需任何登录**。在 P-GC1 出结果前不得实现上层自动 Stage 编排（基线预期 gcog_mode=B，D-2）。
   - **C2C E2E**：P-C2C1——prepare → root 原生 Browser 交互 → record 哈希核对（Browser 仅 root/main agent 可用，Windows 无 Chrome 登录导入）。

**（3）Codex 路由后续（P-CDX 组）——门控于 D-17 缓解纪律**：路由已 live（D-14），公共出口封禁已验；但 P-SEC1 FAIL（读不设限 + loopback 可达）⇒ 在 OS 边界落地前，真实负载只允许 trusted-content 工作负载，且**波次期间绝不运行 kimi web**（supervisor 对 Kimi loopback server 存活直接拒绝 wave start）。

**（4）无需用户动作**：Kimi 配额自动重置（P-KIM1 已锁 K1 契约）；Luna 探针**重定义待定**（cliproxy 路由暴露的是 provider 侧 web_search，membership 路由未测）。

**（5）M8 → M9 → M10**（依 spec VOL-19）：M8 真实 G-L 循环 → M9 原生-vs-外部 worker 对比臂 → M10 context-quality 臂 + `zloop rollback --run`（release gate 必测）。

---

## 8. Command reference (v0.2)

> 快速参考：`docs/COMMANDS.md`（一页版，含退出码）。本节记录 v0.2 新命令面的**运维语义**。
> 诚实边界（round 3 快照）：`stage begin/status/close`、`wave propose/start/cancel`、`research run` 已在 `src/zloop/cli.py` 的 parser 注册；`zloop stage promote SID [--skip-c2c]` 是本轮接线中的命令——以 `zloop --help` 实测为准（no-fake-success，VOL-01 §3.3）。

### 8.1 退出码（全命令统一契约）

| code | 含义 |
|---|---|
| 0 | ok |
| 2 | usage（argparse 参数错误） |
| 3 | S_DEGRADED — S 损坏/不可写，fail-closed（I4） |
| 4 | bind-token wait 超时（`--wait-claim` 到期未被 hook claim；通常是跑在了后台，P2-13） |
| 5 | blocked — 前置条件不满足（未注册项目 / 未知 run / 状态不允许） |
| 130 | Ctrl-C 中断 |

### 8.2 v0.2 新命令面（wiring in progress this round）

| 命令 | 语义（契约） | 状态 |
|---|---|---|
| `zloop stage begin <objective-slice> [--risk REQUESTED]` | 在当前 run 创建 stage（PLANNING→EXECUTING）：确定性 risk floor（VOL-08 §2）、clean-base 门槛（I37）、锁定 base ref/tree | 已在 parser 注册（round 3 快照；以 `zloop --help` 实测为准） |
| `zloop stage status [RID]` / `zloop stage close` | stage 行 / FSM 终态 | 已注册（同上） |
| `zloop stage promote SID [--skip-c2c]` | CAS + ff-only 晋升（I30/I39）：intent-first、worktree 干净且 digest 未变、HEAD 未漂移、staged 须为 HEAD 后代；HIGH/CRITICAL stage 默认要求 C2C 记录在案（`--skip-c2c` 显式降级并留痕）。exit 5 原因：`DIRTY_OR_DRIFT` / `HEAD_DRIFT` / `NOT_DESCENDANT` / `c2c_gate_required` / `nothing_materialized` / `staging_missing` | **wiring in progress（本轮）**（promote.py 库层已实现+测试；CLI 子命令接线中） |
| `zloop wave propose` | 提交 wave proposal → host-side final ruling（`validate_wave`：DAG 无环、write_scope 两两不相交或显式 depends_on 序列化、risk ≥ floor、network_policy 形状） | 已注册（同上） |
| `zloop wave start` | 启动 wave：每 packet 全新 launch_id + 全新 workspace（I34）；结果过 I6 四重 fence；**D-17：Kimi loopback server 存活期间直接拒绝** | 已注册（同上） |
| `zloop wave cancel` | 写 `cancel_requested`（D-8 语义，见 8.4） | 已注册（同上） |
| `zloop research run <query>` | Research Broker 单次检索（Kimi K1 单路，D-10；D-18 三轴输出字段、D-19 searcher-only 会话） | 已注册（同上） |
| `zloop verify-run [RID]`（升级版） | 目标完成判据：run 存在且 CLOSED +（升级方向）promoted stage 全部落地。当前已实现版本：run 存在且 CLOSED 即 ok，否则 exit 5 | 已实现（基础版）；升级项 **wiring in progress** |

已稳定实现的命令（doctor / project / run / attach / detach / binding / history / checkpoint / install / uninstall / verify-run）见 `docs/COMMANDS.md`。

### 8.3 FOREGROUND 规则（run start / attach / wave start）

- `zloop run start` / `zloop attach` /（落地后的）`zloop wave start` 是**前台命令**：
  1. bind-token marker（`ZLOOP_BIND_TOKEN=<nonce>`）必须出现在**前台** Bash tool_response 的 stdout 里，PostToolUse hook 才能扫到并 claim（I32）。后台运行的 tool_response 不含 stdout ⇒ token 永远无法被 claim（P2-13）。
  2. 平台硬上限：单次前台 Bash 调用 ≤ **600,000 ms（600s）**。因此 `--wait-claim N` 的 N 必须远小于 600；超时未 claim ⇒ exit 4 + stderr 警告"re-run in foreground"。
  3. 超过 10 分钟的 wave **不得**在前台死等：用 `wave start`（后台化）+ 结束 turn，等 task-notification 重新唤醒 root（D-2：await 参考实现 = background + notification；`await --timeout` 上限 540s）。

### 8.4 Controller-token cancel 语义（D-8）

- run 的所有权 = S 内 **CAS token**（`runs.controller_nonce/controller_pid/controller_pid_start`），不是长持 OS 锁——旧设计（wave 长进程持 run.lock + cancel 需要 S 事务）会自我锁死，已废弃。
- `cancel` 是**命令输入，不是生命周期迁移**：`request_cancel` 只写 `cancel_requested=1`（无需 controller token）；owner 在自己的下一个 tick 观察到它，由 **owner** 执行状态迁移（ACTIVE→CANCELLED 等）。
- 接管（takeover）需要机械死亡证明：新 controller 只有带着旧 `(pid, pid_start_time)` 的死亡证据才能 CAS 换主（`claim_controller(expected_old=…)`）；TTL 永不单独决定所有权（I43）。
- OS `RunLock` 只包住单次 mutation，绝不跨 wave 生命周期持有。

### 8.5 5-hook 注册（D-9）

- `zloop install` 只注册 **5 个 post-execution 事件**：SessionStart / UserPromptSubmit / PostToolUse / PostToolUseFailure / Stop（`process` 型、无 matcher、`timeoutMs=8000`、`maxOutputBytes=32768`、用户级 config）。
- 理由：PreToolUse / PermissionRequest 会在**每次工具调用前**同步拉起进程（热路径税）；PostToolUse(+Failure) 已携带完整结构化结果。第二次独立审计（2026-09-02）裁定。
- hook 进程本身仍分派全部 7 个已文档事件（防御性兼容：手工注册 7 个也能工作）。
