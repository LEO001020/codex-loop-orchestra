# 现有架构 zloop-gen8 深度审计报告

## 结论
zloop-gen8 (ID: 53e0b85) 架构已向 Git-native 转型。它完全摆脱了 Codex SDK 的后端依赖，并通过 src/zloop/backend/ 的本地化代码实现，将架构边界收敛至 Git 仓库的物理状态。它不是一个被动的调度器，而是一个基于 Git 工作树和 Job-Packet 模型的自主计算单元。

报告路径: [11_现有架构zloop-gen8.md](/abs/E:/codex-LOOP/codex-loop-s-f2/reports/zcode_loop_reinvestigation_20260905/11_现有架构zloop-gen8.md)

## 事实证据
1. 仓库状态: git status (OID: 53e0b85) 显示了广泛的重构痕迹，包括 src/zloop/backend/codex_sdk.py 的直接删除，以及 supervisor.py 和 wave.py 的频繁修改。
2. 核心代码结构:
    - 调度逻辑: src/zloop/supervisor.py 和 src/zloop/wave.py 担负着任务序列化与并行控制。
    - 存储: db.py 明确引用 control.sqlite3，但代码设计上趋于将 sqlite 用作短暂的状态交换空间。
    - 后端: src/zloop/backend/ 目录下已无 Codex SDK，改为 zcode_native.py，直接对接 ZCode 的原生执行面。
3. 关键证据路径:
    - 代码组件位置: E:\zcode\zloop-gen8\src\zloop\
    - 后端定义: E:\zcode\zloop-gen8\src\zloop\backend\zcode_native.py
    - 分支状态: main 分支，距离 origin/main 有 13 次提交。

## 推断与分析
* 架构迁移: 删除 codex_sdk.py 标志着该架构已经彻底放弃了对中间 SDK 的依赖，转向了更轻量、更直接的 ZCode 原生交互方式。
* 计算可靠性: 将状态检查与数据库迁移逻辑 (db.py) 保持在轻量级，侧重于 Git 元数据校验，这显著提升了在 Windows 复杂 FS 之上的并发可靠性。

## 局限与未知
* Orchestration: packets.json 的完整运行时调度语义依赖于 supervisor.py 内部的状态机逻辑，非显式声明。
* 数据库依赖: 虽然 sqlite 用于控制状态，但该本地 SQLite 文件 (control.sqlite3) 的物理锁争用处理在多 Job 并行下的稳健性仍需通过 tests/test_concurrency_fixes.py 进行压力验证。

