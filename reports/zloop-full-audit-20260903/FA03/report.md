# 控制库 (db.py) 全审计报告

## 1. 模式与初始化
- **WAL 检查 (I22)** (行 37-47): 使用 sqlite3 版本号 gate 决定 journal_mode. 生产环境要求 WAL，检测逻辑覆盖 3.51.3+ 或指定 backport 版本，否则强制 DELETE+EXTRA。
- **连接配置**: connect 强制 busy_timeout=30000, foreign_keys=ON, synchronous 根据 journal_profile 配置。
- **迁移 (D-8)**: _migrate 自动处理表结构演化 (ALTER TABLE ADD columns)，并对 schema version 进行加锁迁移(BEGIN IMMEDIATE), 确保多实例下迁移安全。

## 2. 事务与原子性
- **事务封装**: mutation contextmanager 通过 BEGIN IMMEDIATE 将所有写操作包装在高隔离性事务中，防止竞态冲突。
- **错误处理**: SError 用于 fail-closed 语义，CLI 接收到时退出（需外部映射 exit 3）。

## 3. 并发控制与恢复
- **OS 锁 (I43)**: RunLock 使用 msvcrt.locking (Win) 或 fcntl.flock (POSIX) 实现独占 Run 文件锁，acquire 采用 LK_NBLCK 实现非阻塞锁定。
- **Takeover 机制 (D-20)**: takeover_controller 强制实施死锁/死进程排除。在 CAS 更新 controller token 之前，必须执行 owner_alive (检查 PID, PID StartTime)。拒绝死者取替机制(FAIL-CLOSED)，严防 PID 重用导致的数据损坏。

## 4. 潜在影响点与总结
- **数据库导致全失败点**:
  1. 数据库被删除或路径错误: connect 抛出 SError (行 126)。
  2. 磁盘坏块/quick_check 失败: connect 中 quick_check 异常引起 fail-closed (行 134-138)。
  3. RunLock 竞态: 文件锁互斥被其他 Controller 持有 (行 181-183)。
- **证据**: src/zloop/db.py 源码直接分析。