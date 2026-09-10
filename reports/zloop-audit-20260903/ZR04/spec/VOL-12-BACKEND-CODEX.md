# VOL-12 — 执行后端：SDK-first 与 Windows 进程治理

> **ZLoop Spec v1.0** · 卷 12/22 · 层级 L2 · 依赖：VOL-02, VOL-04, VOL-09
> 含审计修正 P1-9（Job Object）。**核心立场：provider thread 状态是物理证据，永远不是 S 权威**（实证：openai/codex #37047 / #34220 / #37856）。

---

## 1. AgentBackend 抽象（唯一后端接口）

```python
class WorkerSpec(NamedTuple):
    launch_id: str; workspace: Path; prompt: dict   # VOL-09 §4 envelope
    network: str; max_turns: int; model: str | None

class AgentBackend(Protocol):
    def start(self, spec: WorkerSpec) -> LaunchHandle: ...
    def wait(self, h: LaunchHandle, timeout: float | None) -> str: ...   # 'terminal'|'timeout'|'unknown'
    def stream(self, h: LaunchHandle) -> Iterator[WorkerEvent]: ...
    def interrupt(self, h: LaunchHandle) -> bool: ...
    def collect(self, h: LaunchHandle) -> WorkerReport: ...             # VOL-04 §9
    def health(self) -> BackendHealth: ...
```
同一 contract 跑同一套 contract/chaos tests——这是 CodexSdkBackend / AppServerBackend / 未来 ZCodeBackend 可替换的唯一标准。

## 2. CodexSdkBackend（默认优先；签名按 2026-09-02 实测核实）

```python
from openai_codex import Codex, AsyncCodex, Sandbox      # PyPI: openai-codex（本机 0.147.0）

client = Codex(CodexConfig())        # 默认复用现有 Codex CLI 登录（已核实）
thread = client.thread_start(
    cwd=str(spec.workspace),
    sandbox=Sandbox.workspace_write,          # network_access 默认 false（仍须 P-CDX3 canary）
    approval_mode=ApprovalMode.never,         # 无人值守 worker 不走审批
    developer_instructions=ENVELOPE_HEADER,   # 机器身份头（launch/packet/revision）
)
handle = thread.turn(input=prompt_text, sandbox=Sandbox.workspace_write)  # per-turn 覆盖
for notification in handle.stream(): ...      # 终止判据 = turn/completed 通知
result = handle.run()                        # TurnResult{status, final_response|None, items, usage}
```

硬规则：
- **`final_response` 可为 None**（无 final-answer 的合法终止）——acceptance 绝不依赖最终一句文本（I27 精神同样适用）；完成判据 = `turn/completed` + status。
- worker turn 全部跑在**一个 supervisor 运行时**内（§3，D-12）；**每个 launch 依然拥有独立 workspace**——隔离靠 workspace 与 fencing，不靠进程数。
- resume/read/interrupt 全部 bounded timeout；stale-active（#37047）→ 按歧义处理，不无限等。

## 3. 运行时形态（D-12：单运行时，删除 worker-host/分片设计）

- 官方已核实：一个 `Codex/AsyncCodex` client 可并发消费多个活跃 turn ⇒ **v1 = 一个 wave supervisor 进程 + 一个 client + N 个并发 turn + N 个独立 workspace**。不出生 worker-host-per-launch、client shard pool、launch↔host 映射（本卷旧版 §2"一 launch 一 host"与旧 §3"一 host 背 4 launch"的矛盾系原文错误，按本节为准）。
- 只有 M9 压测证明单运行时的 RAM/吞吐/故障爆炸半径不可接受，才按 1→2→4 递增 shard，每步记 benchmark 证据。

## 4. 嵌套 agent 与网络（strict worker contract 的两道硬门）

| 门 | 方法 | 失败处置 |
|---|---|---|
| 嵌套禁用 | worker config `[agents].enabled=false`（+`features.multi_agent` 关）；启动后**枚举实际 tool catalog**，不得出现 `spawn_agent/send_input/resume_agent/wait_agent/close_agent` | catalog 不净 ⇒ 该 backend 不满足 strict contract ⇒ fail-visible 降级 |
| 物理断网 | workspace_write 默认 network_access=false；再跑双 canary：公网 canary + loopback/private canary，必须都被拒 | 不能 enforce ⇒ OS/container/firewall 边界，或该 backend 拒绝承载 offline coding worker |

**Prompt 写"不要联网"不算隔离**（I36）。

## 5. Resume/收集的歧义纪律（I44）

```text
crash 后对每个 dangling launch：
1. interrupt/kill 已知进程树（§6；核对 (pid, pid_start_time) 身份后才动手）
2. 旧 launch workspace → QUARANTINED/READ_ONLY_EVIDENCE；保存 partial diff/log/backend_handle
3. 只有 provider 明确 terminal ∧ exact launch_id 仍 active ⇒ 才 collect
4. 无法证明独占+terminal ⇒ 不在同目录"resume 继续写"
5. transient retry = 同 packet_revision、attempt+1、新 launch_id、新 workspace（I34）
```
已知实证背景：thread stale-active resume 挂死（#37047）、app-server 重启后 Completed 子代状态丢失致 wait timeout（#34220）、多窗口争同一 active thread（#37856）。

## 6. Windows 进程树回收（DEFER，D-12）

- v1 单运行时：入口 supervisor 一棵树套一个 Job Object 即可，不做每 launch 细分。
- 每 launch 的 Job Object 只有在 `interrupt()` 被实测不可靠（#37047 场景，P-CDX3）后才引入；**正确性不依赖进程击杀**——歧义 launch 走 quarantine（§5 / I34），workspace 隔离才是承重墙。

## 7. AppServerBackend（fallback）与 ZCodeBackend（未来）

- 触发：SDK 缺少所需 lifecycle/cwd/event contract 或实测不稳。
- 当天执行 `codex app-server generate-json-schema --out <DIR>`，sha256 入 manifest，只按当日 schema 实现；stdio 优先；experimental WS 不作 production transport。
- ZCodeBackend：ZCode 公开稳定 external session API（create/start、cwd 绑定、event stream、resume、cancel、result collection）后才实现；**禁止逆向 ZCode 私有 runtime**。通过同一 contract/chaos 测试后才可替换。

## 8. 测试锚点

stale-active 重建、resume bounded、crash 后 workspace quarantine、Job Object 树击杀（含孙进程）、nested catalog 探测、双 canary 断网、shard 故障半径、1/2/4/8/12/16/24/30 实测上限（M7/M9）。
