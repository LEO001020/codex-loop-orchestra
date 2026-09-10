# 公开发布审计报告（packet: public-release-audit）

审计时间：2026-08-24（Asia/Shanghai）。目标：`E:\codex-LOOP\github\codex-loop`，
远程 `github.com/LEO001020/codex-loop-orchestra` @ `b806d6f1fb8762d888289a1b4b4028f7a2f6404d`。
全程只读；未修改被审仓库、未安装、未发布、未推送。

## 结论

完成门未达：CI run 32661153557 与 Release run 32661155641 均失败（测试矩阵 4/4 腿红，
发布流程 Build/Verify/Publish 从未执行过），v0.1.0 release 与 3 个资产存在且内部
一致但系工作流外发布；秘密排除风险未发现；完成门所缺 = 修复 CI 中 codex 可执行文件
解析缺陷（含 orchestration_v2 conftest 遮蔽、loop fixture 缺 refill_policy.toml），
提交后重跑 CI + Release 至全绿。

## 已验证（直接证据）

### 本地 git 状态
- 审计开始时工作树干净：`git -C E:\codex-LOOP\github\codex-loop status --porcelain=v1 -b`
  输出仅 `## main...origin/main`。
- `git rev-parse HEAD` = b806d6f…；`git ls-remote origin`：`refs/heads/main` =
  b806d6f…，`refs/tags/v0.1.0` 注解标签对象 1ec1fa6e → peeled b806d6f…。
- 仓库仅 2 个提交：318cbc5 "Initial open-source release"、b806d6f "Fix isolated CI activation"。

### 远程元数据（gh api，认证账号 LEO001020）
- 仓库：`private=false`、`archived=false`、`default_branch=main`、创建 2026-08-23T19:22:09Z。
- Release v0.1.0：`draft=false`、`prerelease=false`、target_commitish=main，
  published_at 2026-08-23T19:22:39Z，3 个资产（tar.gz 871242 B、zip 1009663 B、
  SHA256SUMS 199 B），下载量均 0。
- 分支保护：`GET /branches/main/protection` → 404（未受保护）。

### 工作流运行（全部 4 个 run，无一次成功）
- CI #1 32661017448（318cbc5，push）failure；CI #2 32661153557（b806d6f，push）failure。
- Release #1 32661034178（318cbc5，tag push）failure；Release #2 32661155641
  （b806d6f，tag push）failure。
- CI #2 job 结论：`Configuration and release gates`=success、`PowerShell syntax`=success、
  `Secret scan`(gitleaks)=success；测试矩阵 4 腿全 failure（ubuntu 3.11/3.14 各 27 个失败
  测试，windows 3.11/3.14 各 28 个，去重 30 个唯一测试名，见下文）。
- Release #1/#2 job：`Resolve and validate release tag`=success（tag v0.1.0 ==
  VERSIONS.lock [release] version 0.1.0）、`Generate source integrity manifest`=success、
  `Test release tree`=failure、`Build reproducible archives`/`Verify archive boundaries`/
  `Publish GitHub Release`=skipped。

### 失败根因（run 日志行）
- `OSError: headless codex executable not found`，raise 于 `harness/dispatch.py:175`
  （`resolve_codex_binary`，仅认 `CODEX_HEADLESS_BIN` 或 PATH 上的 codex）；
  触发测试如 `tests/orchestration_v2/test_dispatch_v2.py:232`
  `test_explicit_k3_dry_run_remains_observable_during_backoff`、
  `test_dry_run_spawn_records_route_metadata`（dispatch_v2.py:471→512→435）、
  `tests/unit/test_dispatch_model_pin.py:46`（dispatch.py:557）与
  `tests/golden/test_g1_two_packet_parallel.py:33`（dispatch.py:935→928→557）。
- `PolicyError: refill policy missing: …/loop/config/refill_policy.toml — refusing
  silent defaults (P0-8.3)`，触发 `tests/unit/test_dispatch.py:125`
  `test_single_spawn_with_mock_codex_lands_report`。
- 提交态缺陷（git show b806d6f:tests/conftest.py 与 diff 佐证）：
  a) `tests/conftest.py` 的 autouse `_clean_env` 未设置 `CODEX_HEADLESS_BIN`
     （mock 位于 `tests/mock_codex/bin/codex`，已跟踪且模式 100755）；
  b) `tests/orchestration_v2/conftest.py` 定义同名 autouse `_clean_env`
     遮蔽根 conftest 且同样不含该变量 → v2 全部命令构造测试在全新 CI 环境失败；
  c) `loop` fixture 复制到测试根的配置不含 `refill_policy.toml` → PolicyError。
- 本地单测复现（Windows，pytest 单测，缓存禁用、不写仓库）：当前（已含外部修复的）
  工作树通过 `test_cmd_contains_model_flag_matching_worker_toml`；提交态无此修复，
  CI 日志即为提交态失败证据。

### 资产完整性（已验证）
- 远端 3 资产下载至 %TEMP% 后 SHA256：
  tar.gz `0862ff4e…a61c4`、zip `3ef515c7…dae35`、SHA256SUMS `53326f47…6d07a`；
  与本地 `dist/` 同名文件逐字节一致；资产内 SHA256SUMS 内容与两个归档哈希吻合。
- 归档边界：zip 与 tar.gz 各 236 项，根前缀 `codex-loop-orchestra-0.1.0/`，
  无 data/ reports/ dist/ .git/ logs/ secrets/ credentials/ .env/.codex 等项。

### 秘密排除（已验证）
- CI `Secret scan`（gitleaks，fetch-depth 0）job success。
- 本地扫描：`git log --all -p` 与全部已跟踪文件正文对
  ghp_/gho_/ghs_/github_pat_/AKIA/sk-/BEGIN PRIVATE KEY/xox* 等模式 0 命中。
- `git ls-files` 无 data/ reports/ logs/ sessions/ secrets/ credentials/ dist/、
  无 .env、*.key、*.pem；`.gitignore` 覆盖上述类别。

### 清单一致性（已验证）
- `test_install_v2.py::test_release_checksums_cover_exact_managed_boundary`（CI
  static-gates 通过）对每个托管文件校验真实 SHA256 → 提交态 b806d6f 的 SHA256SUMS
  与自身树一致（另用 `git show HEAD:<file>` 复算 10 个文件抽样，全部匹配）。
- FILELIST.txt == config/managed_files_v2.txt（236 项）。
- README/README.zh-CN 含 CI 徽章；badge.svg 当前显示 "CI - failing"。

## 推断（非直接观察）
- Release v0.1.0 于 19:22:39 发布、资产于 19:24:56 上传，均发生在失败工作流的
  Publish 步骤从未执行的前提下（两次 Release run 的该步骤均 skipped，且资产
  时间戳距 run 创建仅约 1 秒）→ 推断由工作流外脚本/人工 `gh release create/upload`
  完成，源为本地 dist 构建（哈希逐字节一致支持此推断）。
- tag v0.1.0 曾在 19:22:35（run1 触发，指向 318cbc5 的轻量 tag）后被替换为指向
  b806d6f 的注解 tag（tagger 时间 2026-08-24 03:24:50 +08:00），与 run2 触发一致。
- main 无 GitHub 分支保护，CONTRIBUTING 的 "release gates human-triggered" 属流程
  性约定（推断未在 GitHub 侧强制）。
- 仓库元数据 `size=0` 为 GitHub API 返回值，疑为展示口径问题，不影响门判定。

## 审计期间观察到的外部写入（重要）
审计开始（工作树干净）后、期间，工作树陆续出现未提交改动；截至最后一次检查共
12 个文件（git status 显示 ` M`）：AGENTS.md、harness/orchestration_common.py、
harness/refill_controller_v2.py、hooks/sol_tool_gate_router.py、tests/conftest.py、
tests/orchestration_v2/conftest.py、tests/orchestration_v2/test_refill_controller_v2.py、
tests/orchestration_v2/test_windows_wsl_paths.py、tests/test_global_desktop_mode.py、
tests/unit/test_loop_monitor_server.py、tests/unit/test_model_profile.py、SHA256SUMS。
改动内容与上述 CI 失败根因一一对应（conftest 增加 CODEX_HEADLESS_BIN、loop fixture
增加 refill_policy.toml 等，diff +54/-26），判定为另一进程/代理正在修复本仓库；
这些改动不属于本次审计产生，已原样保留、未回退（SHA256SUMS 的 mtime 早于最新文件
改动，尚未随最新编辑重新生成）。审计方本地唯一执行过 pytest 单测（tmp_path 隔离、
-p no:cacheprovider、PYTHONDONTWRITEBYTECODE=1），经核 dispatch.py 写入面仅限
DATA/events/ledger，未向仓库写入任何字节。

## 完成门缺口清单（按优先级）
1. CI 全绿：修复上述 codex 解析缺陷后提交，重跑 `CI` 至 4 腿测试矩阵 + static-gates
   + PowerShell + Secret scan 全绿（当前 30 个唯一失败测试）。
2. Release 全绿：重跑 `Release` 工作流使 `Build reproducible archives`、
   `Verify archive boundaries`、`Publish GitHub Release` 真实执行并产出/覆盖资产。
3. 清单随修复同步：提交前用 `python scripts/gen_filelist.py .` 与
   `python scripts/gen_sha256sums.py . --output SHA256SUMS` 重新生成（当前工作树
   SHA256SUMS 落后于最新编辑；提交态 CI 的 test_install_v2 会拦哈希不一致）。
4. 重发布后复核：资产哈希/归档边界（现资产本身已通过全部完整性检查，仅需确认
   由工作流产出而非外部上传）。

## 复现命令
```
git -C E:\codex-LOOP\github\codex-loop status --porcelain=v1 -b
git -C E:\codex-LOOP\github\codex-loop ls-remote origin
gh run view 32661153557 -R LEO001020/codex-loop-orchestra --log-failed
gh run view 32661155641 -R LEO001020/codex-loop-orchestra --log-failed
gh api repos/LEO001020/codex-loop-orchestra/actions/runs/32661153557/jobs
gh release view v0.1.0 -R LEO001020/codex-loop-orchestra --json assets,body
gh release download v0.1.0 -R LEO001020/codex-loop-orchestra -D <tempdir> --clobber
Get-FileHash <tempdir>\* -Algorithm SHA256   # 与本地 dist\ 对比
```
