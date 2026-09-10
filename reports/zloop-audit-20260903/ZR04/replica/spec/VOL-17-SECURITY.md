# VOL-17 — 安全：信任边界、数据分类与环境构造

> **ZLoop Spec v1.0** · 卷 17/22 · 层级 L2 · 依赖：VOL-04
> 含审计修正 P1-8（allowlist 环境）、P0-3（旧系统清场）。

---

## 1. 信任类

- 所有 Web/C2C/research 输出：`trust = external_untrusted`。
- 外部内容**永远不能**：改 wave lifecycle；扩 write_scope/resource_scope；改 permission/network policy；直接 acquire lease；integrate/promote；读 canonical 凭据；触发 live order/withdrawal/deploy（I12）。
- 防注入靠**构造性权限**，不靠"让另一个模型判断是否安全"：Research/C2C 数据面只读；host policy/state 不由网页内容解析成指令；任何外部建议必须走 root → packet proposal → host validation。

## 2. 数据分类与 redaction

| 类 | 例 | 出机规则 |
|---|---|---|
| `public` | 公开文档 | 自由 |
| `project_internal` | 源码、内部设计 | C2C packet 可含（policy 允许） |
| `sensitive` | 内部指标、客户数据 | 需 project policy 显式允许 |
| `secret` | API key、PEM、wallet、cookie | **绝不**出机；不进 research lane、不进 C2C packet |

redaction 模式表（`redact.py`，H0 与 artifact export 共用）：`.env*`、PEM/SSH 私钥块、wallet/keystore、`token/key/password/secret/authorization` 赋值模式、bearer 串、已知凭据文件名。**redaction-before-hash 是硬不变量** [I13]。artifact 导出（报告/交付）前二次 secret 扫描。

## 3. 子进程环境 = allowlist 构造 [P1-8]

root 环境实测含敏感名：`ALIBABA_TOKEN_PLAN_API_KEY`、`ZAI_OAUTH_CLIENT_ID`、`ZAI_BUSINESS_BASE_URL`（2026-09-02 本机实测）。**枚举式 denylist 覆盖不住**，一律 allowlist：

```python
WORKER_ENV_ALLOWLIST = {"PATH", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP",
                        "LANG", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8",
                        "HOME", "USERPROFILE"}   # + packet 显式声明的最小变量
subprocess.run(..., env={k: os.environ[k] for k in WORKER_ENV_ALLOWLIST if k in os.environ})
```
- worker env 永不含：`ZCODE_PLUGIN_DATA`、C2C 凭据、exchange secrets、`~/.zloop` 任何路径值。
- research lane 同构 allowlist + 独立 cwd（VOL-15 §7）。
- 测试：worker/research 进程内 `os.environ` 快照 diff —— 只允许 allowlist 内键存在。

## 4. 注入防御矩阵

| 载体 | 防御 |
|---|---|
| 网页内容→worker 指令 | coding worker 默认断网（I29）；prompt 只含 packet envelope |
| 网页内容→root 指令 | external_untrusted 标记 + root 按 Playbook 不把网页当指令（文档层）+ host 校验一切 proposal（机制层） |
| 网页内容→control plane | host 不解析外部内容为指令；SSRF 策略（VOL-15 §6） |
| H0 自读递归 | 读历史不产生新行（VOL-06 §1.2） |
| worker 伪造 acceptance | host 在私有区重验收（VOL-10） |

## 5. 秘密卫生

- 不迁旧 gateway keys；不从 `~/.codex` 任何 auth.json 备份挖凭据（宪章禁令 5）。
- provider token 不放 packet/env 给不需要的 worker；只给完成 packet 所需最小网络/凭据。
- H0 目录 user-only ACL；`zloop` 日志与错误信息中 secret 模式一律打码。

## 6. 旧系统清场（M0 硬前置）[P0-3]

1. **`C:\ProgramData\OpenAI\Codex\requirements.toml`**：清点其中旧 LOOP 机器级 hook 注册 → 得到用户确认后移除/禁用（旧钩子会命中 ZLoop 的每个 Codex worker，含硬编码 `E:\codex-LOOP` 路径的 `global_loop_mode.py`）。移除前备份到 `~/.zloop/hygiene-backup/`。
2. `~/.codex`：5 个 auth.json 变体 + `config.toml.bak-glm53f` → 归档建议 + **轮换建议**（由用户执行，ZLoop 不代删）。
3. 旧 repo `E:\codex-LOOP` → 只读归档；确认无自动启动/计划任务仍指向旧树（`schtasks /query` 过滤 + 启动文件夹检查，只读清点）。
4. 新 ZLoop 与旧 hook/scheduler **绝不双写同一工作区**（安装时 `zloop doctor` 检测冲突并警告）。

## 7. first-class worker 的网络与隔离

`network_policy=none` 必须是执行器实证（VOL-12 §4 双 canary）；`worktree_fast` 的 Git-admin 隔离 probe（VOL-13 §2）；HIGH/CRITICAL 用 `clone_strong`。任何"prompt 里写了不联网"的实现一律判 fail（I36）。


## 8. 读隔离与 loopback（P-SEC1 实测，D-17，2026-09-02）

第一手哨兵证据：`Sandbox.workspace_write` **不限制读**（C 盘根与用户 Profile 哨兵均被逐字读回）；**公网被拒但 loopback 可达**（127.0.0.1 canary 被取回）。因此：
- 跨 plane 升级路径在本机为真：untrusted worker → 读 `~/.kimi-code/server.token` → 控制 loopback Kimi server（session/fs/shell）。env allowlist **防不住文件系统读取**。
- 即时缓解：supervisor 在 Kimi server 存活（healthz 可达）时**拒绝开 wave**；`kimi web` 与 worker wave 永不同时运行。
- **M7 真实负载 gate**：worker 必须运行在专用低权限 OS 身份/隔离边界内（只可见 worker clone 与必要 runtime）——这是消灭 confidentiality P0 的新增实体，符合奥卡姆（它关闭的是已实证的失效类，非美观）。在此之前：first-class Codex worker 仅限**可信内容**负载。
- 卫生：用户 Profile 内不放 worker 可读的明文机密；Kimi token 文件位置由 Kimi 固有布局决定，需纳入威胁模型而非假设不可达。
