# VOL-05 — Hook 子系统与 Session 绑定（P0 子系统）

> **ZLoop Spec v1.0** · 卷 05/22 · 层级 L2 · 依赖：VOL-02, VOL-04, VOL-07
> 含审计修正 P1-5/6/7、P2-13/14。**本子系统建立在一个已实测的前提上**：Terminal 子进程无 session-id 环境变量，而 PostToolUse 是唯一同时持有 `session_id` 与 structured `tool_response` 的位置。

---

## 1. 部署与注册

- **位置**：用户级 `~/.zcode/cli/config.json`（workspace 级 hooks 被整体忽略 [P1-6]）或未来 plugin `hooks/hooks.json`。v1 由 `zloop install` 写入/`zloop uninstall` 移除；`zloop doctor` 校验。
- **形态**：全部 `type: "process"`（argv 无 shell、Windows 最稳）；单入口 `zloop-hook handle`，从 stdin JSON 的 `hook_event_name` 分派（不信任 args 传事件）。
- **配置**（写入 `~/.zcode/cli/config.json`）：

```json
{ "hooks": {
    "enabled": true,
    "timeoutMs": 8000,
    "maxOutputBytes": 32768,
    "events": {
      "SessionStart":       [{ "hooks": [{ "type": "process", "command": "<ABS>\\zloop-hook.exe", "args": ["handle"] }] }],
      "UserPromptSubmit":   [{ "hooks": [{ "type": "process", "command": "<ABS>\\zloop-hook.exe", "args": ["handle"] }] }],
      "PostToolUse":        [{ "hooks": [{ "type": "process", "command": "<ABS>\\zloop-hook.exe", "args": ["handle"] }] }],
      "PostToolUseFailure": [{ "hooks": [{ "type": "process", "command": "<ABS>\\zloop-hook.exe", "args": ["handle"] }] }],
      "Stop":               [{ "hooks": [{ "type": "process", "command": "<ABS>\\zloop-hook.exe", "args": ["handle"] }] }]
    } } }
```

> **D-9（2026-09-02 剃刀审计采纳）**：生产只注册上述 **5 个后置事件**。平台支持 7 个事件是事实（VOL-02 §1.1），但 `PreToolUse`/`PermissionRequest` 在**每次工具调用前**同步拉起进程（热路径税），而 `PostToolUse(+Failure)` 已携带工具名/输入/call id 与完整结构化结果——被拒意图的 forensic 价值不抵该成本。hook 代码保留 7 事件分派（手动注册仍可用）；`zloop install` 只注册 5 个（代码已改，测试已过）。

> **D-16（2026-09-02 第三次审计采纳）**：**plugin scope 是生产部署目标**（`plugin/` 包：`.zcode-plugin/plugin.json` + `hooks/hooks.json` + `zloop-hook.cmd`，enable 注册到当前 workspace、disable 一并移除）；user-config 安装降级为**显式兼容回退**——它对无关 workspace 付进程税且 disable(ZLoop)≠native。同时 hook 增加 **cwd 严格项目过滤**：会话 cwd 不属于任何注册项目的 git_root ⇒ 一律不落盘、不 claim（修复跨 workspace 隐私泄漏）。

- 不设 matcher（捕获全部；SessionStart 全 source）。配置按 session 快照——**改动 hook 配置后需新 session 生效**。
- 三个两源不一致项（官网 vs 本机插件文档）：输出 schema 宽严、`async` 语义、`${CLAUDE_SESSION_ID}` 是否存在——**P-HK2 裁决前，实现一律按保守分支**（只发已文档字段；不依赖模板变量；不做 async）。

## 2. 事件职责矩阵

| 事件 | capture（H0） | 其他职责 |
|---|---|---|
| SessionStart | `session_start`（含 source） | recovery 分支（§5） |
| UserPromptSubmit | `prompt`（脱敏后） | — |
| PostToolUse | `tool_result` | bind-token claim 扫描（§4） |
| PostToolUseFailure | `tool_failure`（error/is_interrupt） | — |
| Stop | `stop`（`last_assistant_message` 进 blob，只留 ref+摘要 hash） | — |

（`PreToolUse`/`PermissionRequest` 按 D-9 不注册；若未来有实测证据需要"被拒意图"取证，再单独立项评审，不默认恢复。）

除 §4 一次性确认与 §5 恢复注入外，**一律空 stdout + exit 0**。

## 3. capture 分支（算法）

```text
stdin 读一行 → json.loads（失败→丢弃，exit 0）
→ 按 hook_event_name 分类 → redact.py 递归脱敏（VOL-17 §2 模式表）
→ 组装 H0 envelope（VOL-04 §5；coverage 默认 root_surface_full；
   子代理来源事件标 native_child_*——P-HK3 定实际可见性）
→ payload ≤4KB inline；否则写 blob（写 blob 失败→降级 inline 摘要+degraded 标记）
→ 取 per-session lockfile（<history>/<session>.lock；msvcrt.locking / fcntl；
   等待 ≤2s，超时→写 degraded 诊断后放弃本行，exit 0）
→ append NDJSON → 释放锁 → exit 0
总预算 ≤3s；任何异常 → 尽量写 history_degraded → 静默 exit 0
```

- **并行 tool call ⇒ 并发 PostToolUse** [P2-14]：锁是正确性机制，不是优化；P-HK1 验证。
- `transcript_path` 是临时文件、hook 后目录即被清理：**需要的内容当场读、当场持久化**；长期 H0 一律在 `~/.zloop`，repo 内只允许 derived pointer。

## 4. bind-token 协议（I32 完整时序）

```text
1. root 前台运行: zloop run start "<objective>"   （或 zloop attach R012）
2. CLI 在 S 事务 INSERT pending_binding_claims
   {nonce: 64hex(32B 熵), purpose, project_id, run_id, expires_at: now+120s}
3. CLI stdout 第一行:  ZLOOP_BIND_TOKEN=<nonce>      ← marker 在最前=截断安全；≤80 字符
4. （可选）--wait-claim <s>：轮询 S ≤s 秒至 claimed，打印确认；超时→exit 4 + WARNING
5. ZCode PostToolUse(Bash) → zloop-hook：
   a. event=PostToolUse ∧ tool_name=Bash
   b. 在 tool_response 的文本表示中扫描  ^ZLOOP_BIND_TOKEN=([0-9a-f]{64})$
   c. 命中 → S 事务(BEGIN IMMEDIATE):
        UPDATE pending_binding_claims SET claimed_at=?, claimed_by_session=?
         WHERE nonce=? AND claimed_at IS NULL AND expires_at>?
      rowcount==1 → UPSERT session_bindings(binding_epoch+1) + events{binding_claimed}
        → stdout: {"hookSpecificOutput":{"hookEventName":"PostToolUse",
            "additionalContext":"[zloop] bound: run R012 · project <name>"}}
      rowcount==0（过期/已用/伪造/跨项目）→ 静默（安全结果=NOT BOUND，绝不猜）
   d. 未命中 → 正常 capture
6. root 随时: zloop binding status（机械确认 pending/claimed/expired）
```

**安全性质（I21 测试集）**：高熵单次；TTL 120s；project/run 绑定；两并发 session 各自 claim 唯一 token；replay/cross-claim/过期 fail-safe；输出中的伪造文本与网页诱导不能越过 nonce 校验；H0 读历史不触发 claim parser（递归 guard）。

**前台约束 [P2-13]**：后台 Bash 的 tool_response 只含 task id/log 路径 ⇒ token 不会出现。处置：CLI 打印 marker 时附提示行"foreground required to bind"；`--wait-claim` 超时给出 exit 4 明确警告；`binding status` 显示过期原因。**Playbook 规定 `run start/attach` 一律前台**。

## 5. recovery 分支（仅 SessionStart）

| source | 行为 |
|---|---|
| `compact` | 对 **exact bound session** 注入 bounded 恢复块（VOL-06 §4 模板） |
| `startup` / `resume` | 仅当该 exact session 已绑定→注入；**绝不选"最近 active run"** |
| `clear` | 默认无输出；`resume_after_clear=true` 或用户显式 attach 例外（I28） |

注入内容 = 重建的 H1.machine 有界摘要 + H1.semantic 有界摘要 + H2 提示行。禁止：网络、模型、repo-wide 扫描、全量历史。DB busy/corrupt/binding 不明 → 无输出 exit 0。
**capture 与 recovery 故障隔离**（双向）：任一失败不拖死另一分支，hook 永不 non-zero。

## 6. 延迟预算与语言决策 [P1-7]

hook inline ⇒ 每次 tool call 直接付成本。工程目标（非硬 gate）：
- SessionStart（含 recovery 读）p95 ≤ 600ms；其余事件 no-op p95 ≤ 300ms、写盘 p95 ≤ 500ms。
**硬 gate = Z0.5 vs Z0 A/B**（空插件 vs 纯 native 的 turn latency 无可感回归，VOL-19 §3）。
语言：v1 Python 单语言；P-HK4 实测超标才替换为 tiny native helper（仅 capture 热路径）。
hook 内禁止：网络、模型调用、repo/git 扫描、history 搜索、summary 生成。

## 7. 失败语义总表

| 故障 | 行为 |
|---|---|
| 磁盘满/ACL/锁超时 | `history_degraded` 诊断行（可行时）；exit 0 |
| stdin 非法 JSON | 丢弃；exit 0 |
| S busy（claim/恢复） | 放弃该次 claim/注入；exit 0 |
| ZCode 侧 timeout kill | 无残留（OS 文件锁随进程消亡释放） |
| marker 丢失（后台调用） | NOT BOUND；root 按 §4 警告前台重试 |

## 8. 对应探针/测试

P-HK1（并发 hook）、P-HK2（输入/输出 schema 实测裁决三处两源不一致）、P-HK3（子代理工具调用是否触发 hook 及 session_id 语义）、P-BIND1（前台/后台 token 路径）、I18/I21/I22/I23 fixture（VOL-18）。
