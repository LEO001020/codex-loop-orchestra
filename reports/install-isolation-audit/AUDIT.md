# 安装隔离审计报告（packet: install-isolation-audit）

审计对象：E:\codex-LOOP\github\codex-loop @ b806d6f1fb8762d888289a1b4b4028f7a2f6404d
范围：hooks/global_loop_mode.py、harness/global_desktop_mode.py、harness/install_user_config.py 的
CODEX_HOME / CODEX_LOOP_STATE_DIR 隔离契约；GitHub CI run 32661153557、Release run 32661155641。
方法：源码阅读（file:line 引用）+ GitHub Actions API/日志（gh api）+ 本地静默重跑隔离测试。
约束：只读；未修改仓库文件（见末尾并发写者观察）。

## 1. 一行结论
隔离契约本身一致且被测试覆盖（12/12 本地通过、四个 CI 矩阵作业中相关测试全部未失败），
但两个 CI run 均整体失败：根因是 Linux/Windows 托管 runner 没有 codex CLI（resolve_codex_binary
抛 "headless codex executable not found"），另有 1 个 Linux 独有失败（单元 loop fixture 缺
refill_policy.toml 触发 fail-closed）；与 CODEX_HOME/CODEX_LOOP_STATE_DIR 隔离契约无关。

## 2. 隔离契约（已验证，逐文件）

### hooks/global_loop_mode.py
- marker_path (L36-45)：CODEX_LOOP_MODE_MARKER > CODEX_LOOP_STATE_DIR/global-loop-mode.json >
  <root>/data/global-mode/global-loop-mode.json。b806d6f 提交正是新增了 CODEX_LOOP_STATE_DIR 分支。
- load_active_marker (L46-66)：schema=codex-loop-global-mode/v1 且 active=true 且
  control_root == resolved(root)，否则安全 no-op（含 control_root 不匹配）。
- 全文件不读 CODEX_HOME：钩子行为完全由 marker 位置 + control_root 绑定决定（已 grep 验证无 CODEX_HOME 引用）。

### harness/global_desktop_mode.py
- state_paths (L78-83)：CODEX_LOOP_STATE_DIR 覆盖 <root>/data/global-mode；marker 与 install 台账同位置。
- CLI (L441-443)：--codex-home 默认取 CODEX_HOME 环境变量，否则 ~/.codex。
- install (L290-312)：只写 CODEX_HOME 下 hooks.json / requirements.toml / AGENTS.md；
  state/backups 写在 state 目录（root 外可隔离）。
- ensure_backup (L227-249)：台账绑定 codex_home——"already bound to another CODEX_HOME" 拒绝换 home；
  并对 requirements.toml / AGENTS.md 做漂移检测（L233-245）。
- restore (L414-434)：再次校验注册 CODEX_HOME，backup 哈希校验后原子恢复，删除 marker+台账。
- runtime_canary (L124-158)：扫描 CODEX_HOME/sessions/rollout-*.jsonl（按 mtime 倒序取前 128，
  早于激活时间即 break），要求存在激活后、cwd 在控制根之外的会话且含三个标记 token
  （"Active Codex LOOP global mode" / LOOP_CONTROL_ROOT=<root> / "Mandatory LOOP model routing"）。
- status (L328-...)：effective_active = declared_active ∧ requirements_exact ∧ agents_active ∧
  context_verified ∧ spawn_gate_verified；activate 失败自动回滚（main L455-462）。

### harness/install_user_config.py
- CLI (L266-267)：--codex-home 默认 CODEX_HOME 或 ~/.codex。
- install (L125-...)：agents/*.toml -> CODEX_HOME/agents，config.toml.example 合并进
  CODEX_HOME/config.toml（保留用户键），先校验后写、失败全量回滚（L194-215），备份哈希入台账。
- 绑定检查 (L166-169)：台账 codex_home 不匹配即 RuntimeError；restore 同（L235）。
- restore 路径安全：target.is_relative_to(codex_home) 防台账逃逸（L238）。
- 契约不对称（已验证代码，无测试断言此行为）：install_state_path (L70-72) 固定
  <root>/data/global-mode/user-config-install.json，不读 CODEX_LOOP_STATE_DIR——
  与 global_desktop_mode.state_paths (L78-83) 不一致。当 CODEX_LOOP_STATE_DIR 指向隔离目录时，
  user-config 台账仍写入控制根内。install.sh 不调用本脚本（仅 INSTALL.md:121 / AGENT_INSTALL.md:31
  文档命令直接调用），影响面为用户按文档手动安装且设置了 CODEX_LOOP_STATE_DIR 的场景。

### 测试预期（Linux）
- tests/conftest.py _clean_env (L49-53)：所有测试强制 CODEX_HOME=/nonexistent-codex-home-for-tests，
  并清 LOOP_* 环境变量——测试永不触碰真实 ~/.codex。
- tests/orchestration_v2/test_install_v2.py：POSIX-only（pytestmark skipif os.name=="nt", L30-31）；
  _run() 自动把 CODEX_LOOP_STATE_DIR 设为 CODEX_HOME.parent/"loop-state"（L42-46）——状态目录
  随测试 CODEX_HOME 派生，隔离于仓库与真实 home。该套件即"Installer and managed-file boundary"
  静态门（ci.yml 在 ubuntu 上单独跑 test_install_v2.py + attention gate）。
- 未覆盖点：legacy _clean_env 不清 CODEX_LOOP_STATE_DIR / CODEX_LOOP_MODE_MARKER；
  test_global_desktop_mode.py 总是显式 monkeypatch CODEX_LOOP_MODE_MARKER，故无测试依赖默认路径。

## 3. CI/Release 运行证据（已验证，日志见同目录 *.log）
- run 32661153557（CI, push main, head=b806d6f, run#2）：FAILED。7 作业中 4 个 pytest 矩阵作业全红；
  static-gates（含 attention budget + installer boundary）、PowerShell syntax、Secret scan 绿。
- run 32661155641（Release, tag/branch v0.1.0, head=b806d6f, run#2）：FAILED，唯一 release 作业在
  "Test release tree" 步（python -m pytest tests -q）失败，未走到 Publish 步。
- 失败共性（4 个矩阵作业 + release 基本同集，~26-28 个失败/作业）：
  * 主因 OSError/WaveError "headless codex executable not found"——
    harness/dispatch.py:175 与 harness/headless_wave.py:310 的 resolve_codex_binary()，
    仅认 PATH 上 codex/codex.exe 或 CODEX_HEADLESS_BIN（仓库有 tests/mock_codex/bin/codex shim，
    但 ci.yml 未设置该变量，runner 无 codex CLI）。波及 test_dispatch_model_pin.py(10)、
    test_headless_wave.py(5)、test_dispatch_v2.py(5)、test_layered_e2e、test_refill_consumer_v2、
    test_release_review_route、tests/golden/test_g1_two_packet_parallel.py、test_dispatch.py 部分。
  * Linux 独有：tests/unit/test_dispatch.py::test_single_spawn_with_mock_codex_lands_report
    （POSIX-only）——PolicyError: refill policy missing <tmp loop>/config/refill_policy.toml
    （legacy loop fixture tests/conftest.py:362-365 只拷 retry_classes.yaml/triggers.yaml/
    orchestration_policy_v2.toml；v2 conftest CONFIG_FILES 有 refill_policy.toml）。
  * 隔离契约相关测试在任何作业的 FAILED 列表中均未出现：tests/test_global_desktop_mode.py(5)、
    tests/unit/test_install_user_config.py(7)、tests/orchestration_v2/test_install_v2.py(POSIX 套件)。
- 本地复验（只读、tmp 隔离）：python -m pytest tests/test_global_desktop_mode.py
  tests/unit/test_install_user_config.py -q -p no:cacheprovider（PYTHONDONTWRITEBYTECODE=1）
  -> 12 passed（Python 3.14.3, pytest 9.1.1）。

## 4. 推断与不确定
- "隔离测试在 CI 上通过" 为按 FAILED 清单全量枚举的推断：pytest 的最终汇总行未出现在下载日志中，
  但所有失败均已逐条列出（short test summary info 之后逐条 FAILED），两个隔离测试文件不在其中。
- 未复跑 test_install_v2.py（Windows 下被 skipif 跳过；跑它需要 WSL bash，属额外副作用面）。

## 5. 工作区并发写者观察（未由本审计造成）
审计开始时工作树干净（git status: nothing to commit）。审计进行中（03:31-03:32 本地时间，
与日志下载同时）tests/conftest.py 与 SHA256SUMS 被外部进程修改：改动为 _clean_env 增加
CODEX_HEADLESS_BIN=tests/mock_codex/bin/codex（tests/conftest.py:54）、loop fixture 增加
refill_policy.toml（:365）、SHA256SUMS 同步。该改动恰好对应本文档第 3 节的两类失败，疑为
并行的修复工作。本审计未回退、未触碰这些改动（避免与共享工作区其他写者冲突）。