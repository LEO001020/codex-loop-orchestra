# 脏基准全局影响复核报告

## 概要
对 E:/zcode/zloop-gen8/ 进行的 git status 检查显示该工作树目前处于 dirty 状态（存在修改的文件）。

## 组件阻断映射
- CLI (src/zloop/cli.py):
    - _git_dirty 函数（行 174）使用 git status --porcelain=v2 -z。
    - 检查 dirty 状态的逻辑在 cli.py:569。
    - 当检测到 dirty 时，抛出 BLOCKED_DIRTY_BASE 或在 CLI 交互中进行阻断（如 cli.py:1473）。
- Stage (src/zloop/stage.py):
    - check_stage_base 函数（行 112）强制要求 dirty_digest 为空字符串（即清空状态）。若不为空，则返回 (False, "BLOCKED_DIRTY_BASE")（行 120）。
- Supervisor (src/zloop/supervisor.py):
    - 在 run_wave 初始化阶段调用 check_stage_base（行 292）。
    - 检测到 dirty 时，阻断整个 wave 启动，并记录 BLOCKED_DIRTY_BASE（行 295）。

## 结论
当前的 git status 状态必然导致上述 gate 触发，从而在各阶段（CLI启动、监督器启动）产生 BLOCKED_DIRTY_BASE 错误。

