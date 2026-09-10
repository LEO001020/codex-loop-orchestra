# VOL-13 — 工作区：每 launch 一目录，两档隔离

> **ZLoop Spec v1.0** · 卷 13/22 · 层级 L2 · 依赖：VOL-09

---

## 1. 布局与生命周期

```text
%ZLOOP_DATA%/projects/<pid>/workspaces/<stage>/<packet>/<launch_id>/
```
- 同 packet 的 retry 也开新目录（attempt+1 → 新 launch → 新 workspace）[I34]；旧目录转只读 quarantine 后延迟回收。
- 回收前置条件：VOL-10 §6 的 recoverable patch 已导出。
- S/H0 authority、`~/.zloop` 权威路径、`ZCODE_PLUGIN_DATA` 永不出现在 worker 可写根内 [I10]。

## 2. 两档 WorkspaceBackend

| 档 | 实现 | 语义 | 使用条件 |
|---|---|---|---|
| `worktree_fast` | `git worktree add`（独立 working tree/index，**共享 Git common objects/refs 管理**） | 快、省盘 | 仅当 sandbox probe 证明 worker 无法改 canonical/common refs/config，且 host 能完整验证。NORMAL 默认 |
| `clone_strong` | 独立 clone（可共享 read-only object cache） | worker 不持 canonical repo 凭据/refs | HIGH/CRITICAL 默认；`worktree_fast` capability 不足时自动升级 |

**不要误以为 `git worktree` 天然隔离 `.git` 管理状态**——common dir 是共享的；probe 必须包含"worker 尝试改 common ref/config 被拒"。

## 3. 不可变代码/数据/缓存宇宙

每 worker 固定：
```text
code_base_ref / stage_snapshot_hash     # 只读起点
dataset_ref = content-addressed          # sha256 校验后才可读
cache_ref   = immutable CAS 或 worker-private
env/lockfile snapshot                   # requirements/lock 哈希入 evidence
artifact_root = worker-private
experiment_id                           # quant 实验身份
```
- 禁止读其他 worker 的 mutable workspace [I24]。
- 共享 mutable 资源（DB/cache/registry）必须 explicit lease（VOL-09 §3）；live order/withdrawal 永远 manual-only。

## 4. Windows 特有拒绝/检查清单

- 路径规范化解 Unicode/大小写折叠后比较（casefold collision = 越界拒绝）；
- 拒绝：junction/reparse point 逃逸 write_scope、UNC/网络路径作为 workspace、symlink 指向 write_scope 外；
- `maxOutputBytes` 无关此处，但 AV/indexer 对 workspace 的文件锁纳入 chaos（P-WS1）。

## 5. Probe（P-WS1）

两档分别测：创建延迟、磁盘占用、common-ref 篡改被拒、credential 不可见、host delta 重建完整性、junction/symlink/casefold/untracked binary/mode-change 全量捕获。
