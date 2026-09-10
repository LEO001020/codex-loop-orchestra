# 安全安装审查报告：README（codex-loop 开源仓库）

审查日期：2026-08-24。范围：E:\codex-LOOP\github\codex-loop（只读审查）。
审查维度：权限、凭据、可还原、非 binary fork、单机边界。

## 结论

README（README.md 与 README.zh-CN.md）在五个维度上的安全声明均清晰、准确，且与仓库实现一致；未发现 P0；1 个 P1（还原命令语义歧义）；2 个 P2（文案精度）。

## 五维度核验结果

| 维度 | 结论 | 直接证据 |
|---|---|---|
| 权限 | 清楚、准确 | README：“Hooks run with the current user's permissions. Gates deny operations; they do not grant privileges.”、“L0/L1/L2 may block…none may publish”、Observer 只读。SECURITY.md 同文：hooks 不提权、sol_tool_gate 为 deny-gate、diffvalidator 强制 authorized_paths。AGENT_INSTALL.md 要求 dry-run→审批→确定性安装→机械验证。 |
| 凭据 | 清楚、准确 | README quickstart 明令“Never read, print, or change my API credentials”；“Credentials remain in the user's authenticated Codex or gateway environment and are excluded from release archives”；“LOOP never edits provider credentials or catalogs”。scripts/verify_release.py 排除 sessions/logs/backups/credentials/secrets；.gitignore 排除 .env、secrets/、credentials/、*.pem/*.key、.codex/；CI 含 gitleaks Secret scan job；install_user_config.py / global_desktop_mode.py / install.sh 无凭据写入路径。 |
| 可还原 | 基本清楚，1 处 P1 语义歧义 | install.sh 在写入前建不可变 restore ledger（L148），config.toml 时间戳 .bak 备份（L267-269）；install_v2.sh 逐文件备份 + 回滚 journal + ERR/INT/TERM/HUP trap；Set-Codex-LOOP-Mode.ps1 ValidateSet 含 Activate/Deactivate/Status/Restore；uninstall.sh 从激活备份还原 AGENTS.md/hooks.json/requirements.toml 并有 legacy 回退。 |
| 非 binary fork | 清楚、准确 | README 多处明示“not a Codex fork / does not patch Codex binaries / builds on Codex's documented extension points”。git ls-files = 239 个跟踪文件，无 dist/、无 .exe/.dll/.whl/.bin 等二进制；LICENSE 为 MIT 自有项目。基准事实对齐：README 明确“Codex already has subagents”，LOOP 定位为控制/运行/观察层增强，无越权宣称。 |
| 单机边界 | 清楚 | README：“The current release is a single-machine control plane, not a distributed scheduler.”；80-agent 行为“Single-machine operating envelope, not an official Codex limit”。实现侧：Observer 绑定 127.0.0.1 且 Windows 下 SO_EXCLUSIVEADDRUSE（loop_monitor_server.py L1070-1127，Start-Codex-LOOP-Monitor.ps1 L58）。 |

## P0

无。

## P1（1 个）

**P1-1 可还原命令语义歧义（README.md Windows/Linux 安装块）**

- Windows 注释“# Pause or restore the verified pre-install backup”未区分 Deactivate（暂停，保留安装与备份）与 Restore（从已验证备份回滚到安装前状态）；读者可能把 Restore 当暂停用，或反向理解“restore the backup”。
- Linux 行“# Restore managed files / ./uninstall.sh”只写“还原”，未提示 uninstall.sh 还会删除未改动的 LOOP agent TOML（INSTALL.md 有准确表述）。
- 影响：可还原维度的用户可理解性缺陷；实际风险低（Restore 有备份哈希校验拒绝机制，INSTALL.md 明确“Restore refuses: do not bypass a backup hash mismatch”），故为 P1 而非 P0。

可直接替换的最小文案（与 INSTALL.md 措辞对齐）：

README.md Windows 块替换为：

```powershell
# Pause: stop injecting into new tasks; keep installation and backups
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Deactivate

# Roll back: restore pre-install managed files from the verified backup
./launchers/Set-Codex-LOOP-Mode.ps1 -Mode Restore
```

README.md Linux 块替换为：

```bash
# Roll back: restore pre-install managed files and remove unchanged LOOP agent TOMLs
./uninstall.sh
```

README.zh-CN.md 同步替换对应段落。

## P2（2 个，文案精度）

- **P2-1** README 未在正文列明安装器写入范围（config.toml 缺失键合并、AGENTS.md/hooks.json/requirements.toml、全局模式标记；详见 INSTALL.md）。Quickstart 已含 dry-run + 人工审批，风险低；建议加一句“installer writes only to CODEX_HOME config files and the repository-local state”提升自证性。
- **P2-2** README 称“CI checks … archive boundaries”，实际归档边界检查在 release.yml（scripts/verify_release.py），ci.yml 只跑 pytest、attention budget、installer/managed-file boundary、PowerShell 语法、gitleaks。若按“GitHub Actions 整体”理解则成立，但字面不精确；建议改为“CI and release workflows check … archive boundaries”。

## 证据分类

- 直接证据：README.md / README.zh-CN.md / INSTALL.md / AGENT_INSTALL.md / SECURITY.md / .gitignore / .github/workflows/ci.yml / release.yml / scripts/verify_release.py / install.sh / install_v2.sh / uninstall.sh / launchers/Set-Codex-LOOP-Mode.ps1 / launchers/loop_monitor_server.py / harness/install_user_config.py / harness/global_desktop_mode.py 全文或关键段读取；git ls-files（239 个跟踪文件，无二进制）；README 两份文件字节级 UTF-8 校验（终端曾显示乱码，实为控制台 GBK 显示问题，文件本身无缺陷）。
- 推论：凭据维度“安装器从不编辑 provider 凭据”依据关键词扫描（auth.json/credentials/catalog/api_key/token 在安装代码中无写入路径）+ SECURITY.md 设计说明；属于代码审阅型推论，非运行时动态验证。
- 未知：gitleaks 扫描的实际检出效果未在本机复跑（仅验证 CI 配置存在）；无其他影响结论的未知项。
