# 核心文件盘点 — 审计报告

任务：识别保持 LOOP 编排、门禁、生命周期、observer 所需的最小顶层文件/目录，供生成可发朋友的源码 ZIP。
模式：只读审计。未修改任何源文件，未运行测试/安装，未联网，未创建子代理，未输出密钥。唯一写入是本目录下的报告产物。
源：`E:\codex-LOOP\codex-loop-s-f2`（LOOP_CONTROL_ROOT）。

## 结论（一行）
建议 ZIP 包含 7 个源目录（harness/hooks/config/agents/schemas/metering/tests）+ 6 个根文件（AGENTS.md、README.md、install.sh、install_v2.sh、VERSIONS.lock、SHA256SUMS），共 212 个文件 ≈2.1 MB；排除全部运行时/个人/机器特定内容（data、reports、backup-*、.git、__pycache__、.pytest_cache、嵌套副本 codex-loop-s-f2/、空目录 C/、.codex/、三个根日志/证书文件）；observer(8765) 实现在源目录之外，需另行追加。

## 建议 include（212 项，精确清单见 include_list.txt）
| 路径 | 文件数 | 作用 | 证据 |
|---|---|---|---|
| harness/ | 54 | 编排/生命周期/状态机/补位/告警/重试/ipybox | dispatch_v2.py、statemachine_v2.py、lifecycle_supervisor_v2.py、headless_wave.py、refill_controller_v2.py、refill_consumer_v2.py、parent_manifest_importer.py、retry.py 等；README 5 分钟路径直接调用 dispatch.py/statemachine.py/dag_assert.py/diffvalidator.py/worktree_pool.sh/smoke_gate.sh/acceptance_replay.sh |
| hooks/ | 8 | 门禁（Sol 工具门、路由门、生命周期钩子、spawn 门） | sol_tool_gate.py/v2/router、root/leaf_agent_spawn_gate.py、global_loop_mode.py、subagent_lifecycle.py、subagent_start_meter.sh、hooks.json.example（install.sh 据此生成 .codex/hooks.json，见 install.sh:104,270） |
| config/ | 19 | 全部策略与模板 | global_working_agreement.md、model_profiles.toml、orchestration_policy_v2.toml、retry_classes.yaml、escalation_ladder.yaml、managed_files_v2.txt（install_v2.sh 的唯一安装清单）、config.toml.example 等；retry.py/duty_*/signals_collect.py 等运行时引用已核实 |
| agents/ | 5 | 子代理角色 TOML | worker/verifier/reviewer/plan_expander/duty_officer.toml |
| schemas/ | 3 | 结果/计划/决策 JSON schema | short_result_validator.py 等消费 |
| metering/ | 5 | 令牌份额计量 | model_token_share.py/v2/bridge、usage_reconcile.py、e0_annotate.py、oscillation_report.py |
| tests/ | 118 | 全量回归（含 mock_codex、golden、orchestration_v2、unit、statemachine_paths） | 朋友侧自验；README 交付校验级 B 级 |
| 根文件 6 | 6 | 入口/文档/版本锁/校验和 | install.sh（部署入口，5 步安装）、install_v2.sh（manifest 部署）、VERSIONS.lock（install 校验引用）、SHA256SUMS（README: `sha256sum -c`） |

## 建议 exclude（理由+实测体量）
- `data/`：4,629 文件 ≈465 MB — 事件流/会话/包状态/设备数据（其中 data/reports 与 data/lifecycle 均为运行痕迹），必排除。
- `reports/`：83 文件 ≈3.4 MB — 历史运行报告。
- `backup-20260811-215706/215927/20260813-012318/`：共 24 文件 ≈457 KB — 安装备份。
- `.git/`（104 文件）、`.pytest_cache/`（5 文件）、`**/__pycache__/` — 元数据/缓存。
- `codex-loop-s-f2/`（嵌套目录）：82 文件 ≈1.9 MB — 8/13–8/14 的陈旧副本，与顶层内容重复（内含旧 config/harness/hooks/tests）。
- `C/`：空目录，疑似 "C:\" 路径笔误产物。
- `.codex/`：7 文件 54 KB — hooks.json 硬编码 `E:\codex-LOOP\...` 绝对路径（机器特定）；install.sh 会从 hooks/hooks.json.example 重新生成目标仓库的 .codex/hooks.json，故不入包。
- 根文件：`search_log.md`（30 KB 研究日志）、`work_commencement_certificate.md`（个人证书）、`test_evidence_pytest_output.txt`（pytest 输出）。

## 密钥核查（证据）
- 正则扫描 `sk-[a-z0-9]{20,}`、`api[_-]?key\s*[:=]`、`bearer\s+[a-z0-9]`、`-----BEGIN` 覆盖 harness/hooks/config/agents/schemas/metering/tests/.codex：0 命中。
- 文件名扫描 `.env/.key/.pem/.p12/auth.json/credential/secret/api-key`：0 命中。
- config/model_profiles.toml 仅含模型路由标识（如 weiwu/deepseek-v4-flash），非凭据；.codex/config.toml 注释明确认证在用户 Windows 原生 CODEX_HOME。ZIP 内不含任何密钥，凭证应由朋友在各自环境注入。

## Observer（8765）— 关键边界
- 8765 面板实现**不在源目录内**：`E:\codex-LOOP\launchers\loop_monitor_server.py`（63,725 B，纯 stdlib HTTP 服务 + 可选 psutil；`--port` 默认 8765，硬编码 `DEFAULT_WINDOWS_ROOT=E:\codex-LOOP\codex-loop-s-f2` 与 WSL 路径）。
- 源目录内仅有其回归测试 `tests/unit/test_loop_monitor_server.py`（按 `parents[3]/launchers/...` 定位，缺失时 pytest skip）。
- 若朋友 ZIP 需"保持 observer 工作"，须另行手动追加 launchers/loop_monitor_server.py（及可选 Start-Codex-LOOP-Monitor.ps1、Register-Codex-LOOP-Monitor.ps1），并把根路径常量改为朋友本机路径。此文件超出本包源目录授权范围，本报告只标记、未读取其运行细节之外内容、未修改。

## 使用方式（供朋友侧执行）
```
cd codex-loop-s-f2
7z a codex-loop-s-f2-src.zip @reports/core-file-inventory/include_list.txt
# 或 tar：tar -czf codex-loop-s-f2-src.tgz -T reports/core-file-inventory/include_list.txt
```
解包后 `./install.sh --repo <目标git仓库>` 部署；`harness/smoke_gate.sh` 自检。

## 不确定项
1. observer 是否随包取决于朋友是否需要 8765 面板；本包默认"源目录自洽最小集"，observer 单独追加。
2. SHA256SUMS 仅覆盖 managed_files_v2.txt 清单文件；ZIP 新增测试等文件不影响其既有校验，但请勿改动被哈希文件内容。
3. VERSIONS.lock 中模型名（gpt-5.6-sol 等）为历史路由标识，随模型档案轮换可能过时，不影响打包。
