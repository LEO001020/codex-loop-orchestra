# AGENTS.md — ZLoop 执行地图（短）

**规范库（权威设计）**：`E:\zcode\zloop-spec\`。本文件只是地图，不是规范的复制；一切以规范库为准。

## 装载协议（摘要，全文见 VOL-00 §3）

1. 先读 `VOL-00-INDEX.md` + `VOL-01-CONSTITUTION.md`，再读 `PROGRESS.md` 确定当前位置。
2. 按当前里程碑装载对应卷（VOL-00 §4 映射表）；工作集 ≤ 当前卷 + VOL-01 + 2 个任务卷，用完即卸。
3. 迷失协议（30 分钟无下一步，或发现自己在即兴发明架构）：VOL-00 §4 → `PROGRESS.md` → VOL-22 §4 → 执行最小下一步。
4. 禁止凭记忆写契约：所有 schema/命令/字段名以 VOL-04…VOL-16 为准；不确定就重读，不要回忆。

## 现实优先（reality wins）

真机观察与规范库/本仓库冲突 ⇒ **现实获胜**：先更新对应卷 + VOL-02 条目状态 + `DECISIONS.md` 记一条，再继续。禁止让库与现实漂移；探针失败按 fallback 降级，不许为了让设计成立而重跑出不同结论。

## 测试

```bash
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest
```

## 禁止行为（违反任一条 = 项目失败，即使测试全绿）

- **禁止伪造成功**：未经 probe 证明的能力不得出现在文档、代码注释或对外报告中（VOL-01 §3.3）。
- **永不**从 `~/.codex` 的任何 auth 备份（现存 5 个 `auth.json` 变体）挖 token/key。
- **未经用户确认绝不删除** `C:\ProgramData\OpenAI\Codex\requirements.toml` 等 ProgramData 文件（存活中的旧 LOOP 机器级 hook 注册；已于 2026-09-02 备份到 `~/.zloop/hygiene-backup/`）。
- **测试绝不触碰真实 `~/.zloop`**：一律使用临时目录/隔离数据根。
- 其余硬禁令见 VOL-01 §3（不逆向 `zcode.cjs`/asar 内部接口；不用 Stop hook 做无限 continuation——平台上限 3 次；不自动 stash/reset/clean/commit 用户 canonical 未提交修改；live/不可逆动作永远显式人类授权）。


## Live vendor-test budget (D-15, 2026-09-02)

User quota is a real cost. Hard rules:
- Probe agents touching a live vendor (Kimi/Codex/etc.): **max 2 real turns per probe**, one-word prompts, no reruns without recording why.
- Implementation agents: stubs/fakes only; **live verification is a separate, explicitly budgeted step** (also max 2 turns).
- A 403/429 means STOP, never retry-loop.
- Every live turn must be countable in the vendor's local log afterward (audit obligation).
