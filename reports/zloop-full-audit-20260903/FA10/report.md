# Promotion 全审计报告 (Packet FA10)

## 1. 摘要与发现
审计结论：Promotion 系统在逻辑上稳健，但在原子性、安全性及 Windows 下 Git 的特殊行为上存在潜在缺陷，可能导致状态不一致。

## 2. 详细缺陷与风险
1. **脏状态检查 (dirty_state, L38)**: 使用 git status --porcelain=v2 -z 并对输出进行 NUL 分隔符连接排序。
   - *风险*: 依赖 subprocess.run(capture_output=True) 解码，rrors=\'replace\' (L36) 会丢失信息并可能产生误导性的 digest。
2. **ff-only 促销 (promote, L141)**: git merge --ff-only 是 Checked-out 安全的，但操作前并未锁定 canonical worktree，中间发生的第三方文件变更可能被促销覆盖。
3. **reconcile_dangling 逻辑 (L215-285)**: 灾难恢复逻辑依赖 _trailers_match (L183) 检查 Git commit trailer 的字段。
   - *缺陷*: 未验证 Trailer 的完整来源，易受通过构造同格式 Commit 伪造状态的攻击。
   - *风险*: 若状态被标记为手动介入，此时用户若不清楚后果暴力移除锁，可能引发冲突。
4. **Windows 行为与 Git**:
   - 该模块严重依赖 Git 的确定性行为。在 Windows/WSL 混合环境或文件系统锁定的情况下，subprocess 调用 Git 可能因为文件锁失败，由于 RuntimeError 处理粒度不够，可能导致 BLOCKED。

## 3. 失败分支分析
若在 promote 的第 (6) 步 (store 交互) 前故障：
- Git 分支可能已前进（FF applied），但数据库未更新。系统进入 dangling 状态。
- 
econcile_dangling 能够检测到 HEAD 前进，但可能因为 trailer 未写（逻辑在 mutation 内部）而被错误识别或需要手工干预。

## 4. 建议测试
1. *原子性测试*: 强制在 _git 函数中注入延迟/故障，验证 
econcile_dangling 在不同阶段的分类准确性。
2. *伪造 trailer 测试*: 手动提交带非法 ZLoop trailer 的 commit，验证 
econcile_dangling 是否会将其错误标记为 RECOVERED。
3. *并发执行测试*: 在大量 I/O 或 Git 被锁定的测试环境下观察 dirty_state 的确定性。
