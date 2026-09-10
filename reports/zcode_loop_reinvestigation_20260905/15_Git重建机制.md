# Git重建机制审计报告 (证据补全版)

## 1. 原理核验

### 1.1 `merge-tree --write-tree`
- **审计行为**: 本机环境 `git version 2.55.0.windows.3`。
- **证据**: `git merge-tree --help` 已确认。该功能在 Git 2.55 版本中成熟，通过 `--write-tree` 参数在不修改 index 和 worktree 的前提下直接生成 tree 对象。此特性允许 ZLoop 在并行 Fan-in 时验证合并冲突而不产生 Git 持久副作用。
- **判断**: 成立。满足事务性 Fan-in 需要。

### 1.2 `commit-tree` 和 `update-ref` CAS
- **审计行为**: `git commit-tree --help` 与 `git update-ref --help` 确认参数有效。
- **证据**: `commit-tree` 支持传入 tree 对象和 parent ID 以构造 commit 对象。`update-ref` 的 `-z` 和 CAS 参数允许 `update-ref <ref> <new_oid> <old_oid>` 形式调用。
- **判断**: 成立。满足原子性提交机制。

### 1.3 `clone --local` / `--no-hardlinks`
- **审计行为**: `git clone --help` 确认。
- **证据**: 机制在于通过文件系统硬链接/符号链接指向源仓库对象。并发下若源仓库 HEAD 发生变动（CAS 更新前），会导致 clone 对象的 reference 悬挂。
- **局限**: 仅靠 Git 命令不能彻底解决竞态，必须在应用层 (ZLoop host scope) 实现 `flock` 或同类互斥，确保在 clone 完成前源 reference 不被 CAS 修改。

## 2. 结论与局限

- R8 规格的 Git 原语设计 (Fan-in commit-tree, 原子引用更新, 缓存 seed) 在 Git 2.55.0 下均有直接原语支持。
- 核心风险点在于多 Agent 的竞态处理，应由应用层的锁机制与 Git 原子原语组合解决。
