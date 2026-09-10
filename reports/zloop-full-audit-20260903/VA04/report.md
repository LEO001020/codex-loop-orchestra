# VA04 任务复核：多ACTIVE运行复核

## 1. 结论
系统允许在同一项目中同时存在多个 ACTIVE 运行（R001 和 R002）。CLI 命令（如 `zloop stage begin` 或 `zloop wave start`）依赖于对最新 ACTIVE 运行（`_require_active_run`）的获取。由于 R002 的 `created_at`（2026-09-03T09:46:44Z）晚于 R001（2026-09-02T21:54:10Z），CLI 总是绑定到 R002。

## 2. 证据路径
- **多 ACTIVE 运行** (`E:/codex-LOOP/codex-loop-s-f2/reports/zloop-full-audit-20260903/FR01/report.md`): `zloop run list` 证实 R001 和 R002 均处于 ACTIVE 状态。
- **CLI 锁定机制** (`E:/zcode/zloop-gen8/src/zloop/cli.py:59`): `_sorted_runs(store)` 按 `created_at` 升序排序，`_require_active_run` 取 `active[-1]`。
- **绑定归属测试** (`E:/codex-LOOP/codex-loop-s-f2/reports/zloop-full-audit-20260903/FR01/report.md`): `zloop binding status` 显示两个不同的 session 分别绑定至 R001 和 R002。

## 3. 风险评估与建议
尽管 CLI 总是操作最新 ACTIVE 运行，但多个独立的 session 绑定到不同的 run (R001, R002) 意味着可能存在多个并行的逻辑控制器。这在没有统一控制器锁定的情况下（如 FR02 所示，controller_pid 为 NULL）可能导致不可预料的交叉影响，特别是在阶段推进和波次执行中。

建议项目强制闭合旧 ACTIVE 运行，或在 CLI 命令中增加 `--run-id` 参数以显式指定目标运行，而不是隐式绑定到最新的一个。
