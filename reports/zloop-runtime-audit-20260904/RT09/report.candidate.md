# ZLoop Materialization 运行性审计报告 (RT09)

## 1. 概述
本审计报告针对 zloop.materialize 模块进行只读运行性审计。本模块作为 ZLoop 系统的核心组件之一，负责将 worker 最终产生的 filesystem 状态通过增量方式重建并应用到 staging 分支，随后进行主机侧的验收运行，并最终提交触发数据库闭环过渡。设计目标在于通过完全受控的主机路径，确保 worker 并非污染 staging 环境。

## 2. 增量重组机制
增量数据应用在 materialize.py/materialize.py:125 定义的 _apply_delta 函数中实现。该函数通过遍历 enumerate_delta 输出的增量集合，对 staged 树进行同步。对于被标记为删除的路径，函数通过 dst.unlink(missing_ok=True) 实现了文件的物理移除（行 139），确保了变更的原子性。

增量应用的关键在于 _apply_delta 仅仅是物理覆盖，并不涉及合并操作，这符合 
worker
最终
FS 定义。对于 rename 操作，通过读取 orig_path 进行物理移除（行 143-146），体现了正确的增量清理语义。

## 3. 边界与 Scope 定义
为了防止增量应用导致路径逃逸，模块在 _apply_delta 前进行了严格的 Scope 校验（行 186）。通过 paths_within_scope 函数，所有增量路径必须落入预定义的 write_scope 中。如果路径校验失败，该函数将直接回退，避免导致 staging 分支状态不一致。

同时，函数使用 pathlib.PurePosixPath 对输入 delta 内部的路径进行归一化校验，在 _safe_join 函数中防范了路径遍历攻击（行 25-29），确保了系统级增量的鲁棒性。

## 4. 提交机制与元数据
在 Delta 应用完成后，staging 树会准备进行一次 materialization commit。代码通过 _trailers 函数（行 151-159）注入了极其关键的 ZLoop 追踪元数据，包括 ZLoop-Run、ZLoop-Stage、ZLoop-Packet。这些 Trailers 是原子化追溯 materialization 源头的基础。

提交过程使用 git commit -q -m（行 206）进行。在提交前，通过 git diff --cached --quiet（行 204）确保有实际的物理变更，如果 staging 没有任何变动，即使逻辑上 worker 请求了物化，materialization 也会无声跳过且不产生冗余 SHA。

## 5. 主机验收运行
主机侧验收是本次 AUDIT 的重点。函数 run_host_acceptance（行 59）在 candidate commit 生成之后运行。与 worker 环境不同，该运行在 staging worktree 执行，以确保测试环境在全新的 baseline 之上。

该函数在 Python subprocess.run 层级包裹了 Timeout 和 shell 模式运行 shim（行 68）。对于每个 accepted command，其 rc、标准输出和 stderr 均被截断到 500 字符纳入结果记录（行 75-76，行 81-87）。即使 acceptance 运行超时（行 78），也会通过 rc/timeout 字段优雅处理，避免 crash 整个物化进程。

## 6. 指向性回滚机制
故障时的回滚是维护阶段安全性的核心。当 rollback_on_failure=True 配置时，若 acceptance 失败（行 219），则会调用 rollback_staging（行 105）。

rollback_staging 函数直接执行 git reset --hard <parent_sha>（行 106）并跟随 git clean -fdx（行 107），将 staging 环境重置到物化前的 commit。这种设计有效防止了污染性 commit 在 staging 树中遗留，体现了 FAIL-CLOSED 的可靠性原则。

## 7. 数据库闭环过渡
物化闭环在 materialize_packet 函数尾部的上下文管理器中（行 228）完成。该闭环操作直接作用于 packets 表和 stages 表，通过同一个 SQL 事务确保物理环境与系统记录的对应性。

闭环更新包括 state='MATERIALIZED' 的标记更新（行 229-231）以及 stage 锚点 current_snapshot 的重定向（行 232-234）。最后触发的 store._event(packet_materialized, ...)（行 236）记录了该 SHA 关联的事件信息，为后续可能发生的 rollback 或对比分析提供了物理审计证据，保证了闭环操作的完整性。

## 8. 闭环分析与评估
基于上述运行性审计，zloop.materialize 模块在实现逻辑及数据原子性层面形成了物理闭环。通过重采样 delta 归一化增量，配合 git-staging 的 hard-reset 回滚语义，能够有效避免不合格物化结果对环境的持久化污染。

审计建议：后续应对 run_host_acceptance 的环境变量注入策略进行进一步验证，确保存档环境在隔离语义上满足 strict-isolated 运行标准，而非仅仅基于 filesystem 的物理隔离。
