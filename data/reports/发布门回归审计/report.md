发布门回归审计报告（L2 verifier，只读）

范围：parent_manifest_importer.py、refill_consumer_v2.py、dispatch_v2.py、
dispatch.py 及新增测试（与 backup-20260813-012318 快照对比本轮改动）。

结论：pass（未发现 release-blocking 缺陷）。

核对要点：
1. parent_enabled 且 sandbox!=read-only 的包拒绝在出生边界之前
   （dispatch_v2.py:_spawn，抛出 DispatchBlocked，无副带出生）。只读强制
   同时作用于 --sandbox 与 dispatch_v1 pinned（dispatch_v2.py:534，
   dispatch.py:513）。
2. dispatch.py 在无 cwd 的 parent K3 角色上回退 detached_root=ROOT
   （dispatch.py:489-494），与入户口径一致；importer 侧 cwd 必经
   allowed_workspace_roots 校验。
3. dispatch_v2.build_exec_command 用 dataclasses.replace 替换 pin，不回传
   污染 role pin 原值；cli_overrides 不含 sandbox 键，无双源冲突。
4. refill_consumer_v2 保留 DISPATCHABLE-only、释放评审绕行、显式 role 强制、
   pool/role 冲突 fail-visible 分支，均被新测试覆盖。
5. parent_manifest_importer 全量校验先于首写；碰撞/单边崩溃恢复路径有测试；
   使用既有 progress_ledger.lock 锁名，事件 append 独立于 ledger 提交。

未测分支（非阻塞）：
- importer 的非 OSError/ValueError 异常（如 atomic_write_json 中途
  TypeError）会冒泡为未捕获异常而非结构化 blocked 输出；现有 except 仅
  捕获 (OSError, ValueError)。同样模式见于 refill_consumer_v2.run_once。
- dispatch_v2 对 parent_enabled 包未独立测试 sandbox!=read-only 的拒绝分支
  （test_dispatch_v2 覆盖 happy-path 只读强制）。

异常事务：未发现新的未回滚事务路径。预算 reservation 在 DispatchBlocked
路径上不会泄漏（注册发生在校验之后）。
