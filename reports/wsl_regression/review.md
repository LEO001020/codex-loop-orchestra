# WSL 回归维度 - 审查记录（只读）

仓库：E:\codex-LOOP\github\codex-loop（HEAD b806d6f，工作树含 tests/conftest.py 隔离修复）
结论：干净 Ubuntu runner 可执行 mock shim；conftest 修复（CODEX_HEADLESS_BIN 固定 + legacy fixture 补拷 refill_policy.toml）方向正确、无阻塞问题。

## 证据

1. 可执行位：git 索引 100755（git ls-files --stage tests/mock_codex），5 个脚本全部带 x 位；actions/checkout 在 Linux 上按 tree mode 落盘。
2. 行尾与 shebang：5 个脚本均 #!/usr/bin/env bash + LF；git ls-files --eol 显示 i/lf w/lf（.gitattributes 为 * text=auto eol=lf、*.sh text eol=lf）；字节扫描 NUL=0、CR=0；bin/codex 与 mock_codex_exec.sh 首 96 字节十六进制确认 23 21 2F 75 73 72 2F 62 69 6E 2F 65 6E 76 20 62 61 73 68 0A。
3. WSL（Ubuntu）真实 bash 冒烟 11/11 PASS（smoke.sh）：--version 输出 codex-cli 0.0.0-mock rc=0；exec --json -m/-c/-o 回显 model/effort 并写 last-message 文件；scenario=fail rc=1；report 落地（cwd=worktree 时按 prompt 中 data/reports/<pid>/report.json 落盘且内容正确）；scenario_control.sh set/reset 正常。
4. 调用链：dispatch.py resolve_codex_binary() 优先 CODEX_HEADLESS_BIN（isfile 校验），cmd 为列表 argv（无 shell），launch_supervisor 以 Popen 启动；supervisor PosixBoundary 以 cwd=worktree 启动 worker；mock 在 worktree 落地 report，lifecycle_supervisor.publish_report 拷回根 data/reports/<pid>/report.json，与 B-level 测试轮询路径一致。
5. 平台分工：真实 spawn 测试 test_single_spawn_with_mock_codex_lands_report 带 skipif(os.name=="nt")；Windows 侧其余测试仅构造/检查命令边界。本机 Windows pytest：dispatch 相关 55 passed/1 skipped，legacy loop 套件 32 passed，均在 CODEX_HEADLESS_BIN 固定后通过。
6. refill_policy.toml：config/refill_policy.toml 存在（1012 B，合法 TOML）；RefillPolicy.load 为 fail-closed 单源（orchestration_common.py:489-501），dispatch 出生门（observed_birth_counts 至 RefillControllerV2）在 hermetric root 需要它；legacy loop fixture 现拷贝它（随后并发改动泛化为 *CONFIG_FILES，仍覆盖）。
7. 环境变化提示：审查期间另一进程对同一工作树做了 LOOP Orchestra 更名与 observer 路径归一化批量修改（统一 mtime 03:38:04，12 个文件含 SHA256SUMS），与本次审查对象正交；mock_codex 文件未被其改动，上述冒烟基于当前树复跑成立。

## 未覆盖

- 未在 WSL 跑全量 pytest（需 pip 安装 requirements-dev，超出只读范围）；Ubuntu 上的机械验收由 CI 的 pytest tests（ubuntu-latest, py3.11/3.14）承担。
