# VOL-04 — 数据模型：ID、布局、S DDL 与全部 JSON Schema

> **ZLoop Spec v1.0** · 卷 04/22 · 层级 L2 · 依赖：VOL-03
> 本卷是唯一的 schema 权威。实现任何存储/接口前以此为准；改动须先改本卷并在 DECISIONS.md 记录。

---

## 1. ID 与命名

| 实体 | 格式 | 示例 |
|---|---|---|
| project | uuid4 | `7f3a…` |
| run | `R` + 3 位零填充，project 内递增 | `R012` |
| stage | `S` + 2 位 | `S03`（配 `stage_revision` 整数，从 1 起） |
| packet | `P` + 2 位 | `P07`（配 `packet_revision` 整数） |
| wave | `W` + 整数 | `W2` |
| launch | `L` + uuid4 前 12 hex | `L9c1f0e2a4b7d` |
| attempt | 整数（同 packet_revision 内递增） | `2` |
| research | `RS` + 3 位 | `RS004` |
| c2c | `C2C` + 3 位 | `C2C011` |
| evidence ref | `ev:<type>:<locator>` | `ev:blob:sha256:ab…`、`ev:s:<seq>`、`ev:git:<sha>` |
| 时间 | ISO-8601 UTC | `2026-09-02T08:15:30Z` |

## 2. 数据根布局

```text
~/.zloop/
  registry.json                          # {project_id: {git_root, git_common_dir, display_name, created_at}}
  projects/<project_id>/
    control.sqlite3                       # S（VOL-07）
    backups/control-<ts>.sqlite3          # 低频在线备份
    history/sessions/<zcode_session_id>.ndjson   # H0，每 session 单 writer
    blobs/sha256/<前2位>/<hash>            # CAS，文件名=内容 hash
    workspaces/<stage>/<packet>/<launch_id>/      # 每 launch 独立（VOL-13）
    runs/<run_id>/waves/<wave_id>/        # wave 日志、worker 报告、delta 包
    research/<research_id>/               # lane 原始输出（进 CAS 前）
    c2c/<c2c_id>/                         # prepare/record 产物
    stage-snapshots/<snap_id>             # private snapshot refs（指向 Git 对象）
```

## 3. S DDL（SQLite；启用 `foreign_keys=ON`）

```sql
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE projects (
  project_id TEXT PRIMARY KEY, git_root TEXT NOT NULL, git_common_dir TEXT NOT NULL,
  display_name TEXT, created_at TEXT NOT NULL);

-- 一次性绑定令牌 [I32]
CREATE TABLE pending_binding_claims (
  nonce TEXT PRIMARY KEY,                 -- 64 hex（32B）
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  run_id TEXT, stage_id TEXT,             -- claim 时可空
  purpose TEXT NOT NULL CHECK(purpose IN ('run_start','attach')),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,   -- TTL 120s
  claimed_at TEXT, claimed_by_session TEXT);

CREATE TABLE session_bindings (
  zcode_session_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  run_id TEXT REFERENCES runs(run_id), stage_id TEXT,
  binding_epoch INTEGER NOT NULL DEFAULT 1,
  resume_after_clear INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(project_id),
  objective TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('ACTIVE','CLOSED','CANCELLED')),
  created_at TEXT NOT NULL, closed_at TEXT);

CREATE TABLE controller_epochs (
  epoch INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id),
  started_at TEXT NOT NULL, ended_at TEXT, host TEXT NOT NULL, pid INTEGER NOT NULL);

CREATE TABLE stages (
  run_id TEXT NOT NULL REFERENCES runs(run_id), stage_id TEXT NOT NULL,
  stage_revision INTEGER NOT NULL DEFAULT 1,
  objective_slice TEXT NOT NULL,
  risk_requested TEXT NOT NULL, risk_floor TEXT NOT NULL, risk_effective TEXT NOT NULL,
  expected_canonical_head TEXT NOT NULL, canonical_dirty_digest TEXT NOT NULL,
  stage_base_ref TEXT NOT NULL, stage_base_tree TEXT NOT NULL,
  current_snapshot TEXT,                 -- 当前 private snapshot id
  state TEXT NOT NULL CHECK(state IN ('PLANNING','EXECUTING','STAGED','PROMOTING','PROMOTED','CLOSED','BLOCKED','CANCELLED')),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, stage_id));

CREATE TABLE packets (
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, stage_revision INTEGER NOT NULL,
  packet_id TEXT NOT NULL, packet_revision INTEGER NOT NULL DEFAULT 1,
  goal TEXT NOT NULL, write_scope_json TEXT NOT NULL,   -- ["src/foo/**"]
  acceptance_json TEXT NOT NULL, constraints_json TEXT, deps_json TEXT,
  resource_scope_json TEXT, evidence_refs_json TEXT,
  risk_class TEXT NOT NULL, network_policy TEXT NOT NULL DEFAULT 'none',
  max_turns INTEGER,
  state TEXT NOT NULL CHECK(state IN ('PENDING','RUNNING','REPORTED','ACCEPTED','MATERIALIZED','FAILED','BLOCKED','CANCELLED','SUPERSEDED')),
  active_launch_id TEXT,
  PRIMARY KEY (run_id, stage_id, packet_id));

CREATE TABLE attempts ( -- D-12: attempt 序数可由 launches 派生；v1.1 计划删除本表
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, packet_id TEXT NOT NULL,
  packet_revision INTEGER NOT NULL, attempt INTEGER NOT NULL,
  created_at TEXT NOT NULL, note TEXT,
  PRIMARY KEY (run_id, stage_id, packet_id, packet_revision, attempt));

CREATE TABLE launches (
  launch_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, stage_revision INTEGER NOT NULL,
  packet_id TEXT NOT NULL, packet_revision INTEGER NOT NULL, attempt INTEGER NOT NULL,
  workspace_id TEXT NOT NULL, backend TEXT NOT NULL,
  backend_handle TEXT,                    -- provider thread id（仅物理证据 [I44]）
  pid INTEGER, pid_start_time TEXT,      -- worker-host 身份核验
  intent_state TEXT NOT NULL CHECK(intent_state IN ('INTENDED','BOUND','RUNNING','TERMINAL','AMBIGUOUS','QUARANTINED')),
  terminal_state TEXT, terminal_at TEXT, created_at TEXT NOT NULL);

CREATE TABLE resource_leases (
  lease_id TEXT PRIMARY KEY, resource_key TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('EXCLUSIVE','SHARED')),
  holder TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT);

CREATE TABLE promotion_intents (
  intent_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL, stage_id TEXT NOT NULL, stage_revision INTEGER NOT NULL,
  expected_canonical_head TEXT NOT NULL, expected_dirty_digest TEXT NOT NULL,
  staged_head TEXT NOT NULL, final_audit_ref TEXT,
  state TEXT NOT NULL CHECK(state IN ('INTENDED','APPLIED','RECOVERED','ROLLED_BACK','BLOCKED')),
  created_at TEXT NOT NULL, resolved_at TEXT);

CREATE TABLE events (                    -- append-only 逻辑审计行（与状态同事务写入）
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  run_id TEXT, stage_id TEXT, kind TEXT NOT NULL, detail_json TEXT NOT NULL);

CREATE INDEX idx_packets_stage ON packets(run_id, stage_id, state);
CREATE INDEX idx_launches_packet ON launches(run_id, stage_id, packet_id, packet_revision, attempt);
CREATE INDEX idx_events_run ON events(run_id, seq);
CREATE INDEX idx_claims_pending ON pending_binding_claims(expires_at) WHERE claimed_at IS NULL;
```

## 4. 事务配方（所有 lifecycle mutation 的唯一形态）

```text
BEGIN IMMEDIATE;
  1. 读当前行，校验 version/state 前置条件（fencing 检查在此）
  2. INSERT INTO events(...)            -- 逻辑审计
  3. UPDATE/INSERT 状态行
COMMIT;
```
失败（任何一步）⇒ ROLLBACK ⇒ I4 语义（停止 mutation，不 launch、不 promote）。
单一写者由 run lock 保证；`BEGIN IMMEDIATE` + `busy_timeout` 处理读连接竞争。

**CAS 更新模式**（晋升等）：
```sql
UPDATE promotion_intents SET state='APPLIED', resolved_at=? 
 WHERE intent_id=? AND state='INTENDED';   -- rowcount==1 才算数
```

## 5. H0 事件信封（NDJSON，每行一条）

```json
{"seq": 1042, "ts": "2026-09-02T08:15:30Z", "session_id": "sess_…",
 "run_id": "R012", "stage_id": "S03",
 "kind": "tool_result",
 "event": "PostToolUse", "tool": "Bash",
 "coverage": "root_surface_full",
 "payload_ref": "blob:sha256:ab91…", "payload_inline": null,
 "hash": "sha256:…", "prev_line_hash": "sha256:…"}
```
- `coverage` ∈ `root_surface_full | native_child_result_only | native_child_surface_observed | external_worker_sdk_events | external_worker_final_only | hook_capture_failed`；
- payload ≤4KB 内联，否则 blob + `payload_ref`；
- `hash = sha256(安全负载)`，`prev_line_hash` 供顺序校验（**注意**：这是行级链接用于损坏检测，不是防篡改权威——权威是 S 事务，见 VOL-07 §1）；
- kind 枚举：`session_start, prompt, tool_call, tool_result, tool_failure, stop, wave_event, materialize, promote, research, c2c, checkpoint, binding, degraded`。

## 6. Blob CAS

- 内容寻址：`sha256(已脱敏字节)`；写路径 `blobs/sha256/<ab>/<hash>`；写后即不可变（只新增）。
- 任何大 payload（网页正文、研究报告、worker 报告、审计响应）先 blob、后引用。
- blob 可全量重建索引；删除 blob 不损坏 S（H0 是 fail-soft 面）。

## 7. H1 检查点

### 7.1 H1.machine（代码从当前现实重建，模型不可写 [I14 之上层]）

```json
{"run_id":"R012","stage_id":"S03","stage_revision":3,
 "canonical_head":"git:sha…","stage_snapshot_head":"snap_…",
 "stage_state":"EXECUTING",
 "packet_states":{"P01":"MATERIALIZED","P02":"BLOCKED"},
 "active_launch_ids":["L9c1…"],
 "oracle_refs":["ev:s:4512"],"research_refs":["ev:s:4102"],
 "audit_refs":["ev:s:4390"],"dataset_refs":["sha256:…"],
 "generated_at":"…"}
```

### 7.2 H1.semantic（root 在 durable handoff piggyback 写 [I33]）

```json
{"objective_slice":"…",
 "established_facts":[{"claim":"…","evidence_refs":["ev:…"]}],
 "decisions":[{"decision":"…","why":"…","evidence_refs":["ev:…"]}],
 "rejected_hypotheses":[{"hypothesis":"…","why_rejected":"…","evidence_refs":["ev:…"]}],
 "unresolved_questions":["…"],"next_frontier":["…"],"risk_notes":["…"],
 "semantic_state_hash":"sha256:…"}
```
规则：目标 2–8KB、硬上限 16KB；每字段 evidence_refs 必须可解析（否则整条降级 `unverified_notes` [I15]）；`semantic_state_hash` 去重（连续相同不落盘）；**绝不**包含 machine 字段；绝不为此单独发起模型调用。

## 8. Wave/Packet 提案 JSON（root → host）

```json
{"wave_id":"W1","stage_id":"S03","stage_revision":3,"risk_class":"NORMAL",
 "packets":[{
   "packet_id":"P07","goal":"…",
   "write_scope":["src/foo/**"],
   "acceptance":["pytest tests/foo -q","ruff check src/foo"],
   "constraints":["不改公共 API"],
   "depends_on":["P01"],
   "resource_scope":["dataset:sha256:…"],
   "evidence_refs":["ev:s:4102"],
   "risk_class":"NORMAL","network_policy":"none","max_turns":20}]}
```

## 9. WorkerReport（host 收集后生成；worker 自报只是 evidence）

```json
{"launch_id":"L9c1…","run_id":"R012","stage_id":"S03","stage_revision":3,
 "packet_id":"P07","packet_revision":4,"attempt":2,
 "status":"completed|incomplete|failed",
 "final_summary_ref":"blob:sha256:…",
 "delta_manifest_ref":"blob:sha256:…",      // host 重建的 filesystem delta 清单
 "local_evidence_refs":["ev:…"],
 "backend_events_digest":"sha256:…",
 "terminal_marker_seen":true}
```
**注意**：`status=completed` 仅为 REPORTED 输入；是否 ACCEPTED 由 host 在当前 snapshot 上重验收决定（VOL-10）。

## 10. Research Evidence 记录 / C2C 包

```json
// evidence（Broker 返回的最小单元；root 只看 manifest，全文在 blob）
{"ref":"web:…","research_id":"RS004","question_id":"Q1","lane":"luna|kimi|native",
 "query":"…","claim":"…","url":"…","title":"…",
 "source_class":"primary|secondary|community",
 "observed_at":"…","published_at":"…","retrieved_at":"…",
 "raw_ref":"blob:sha256:…","content_hash":"sha256:…",
 "verification":"lane_reported|verified_fetch|source_unverified|conflicted",
 "trust":"external_untrusted"}

// C2C packet（host prepare 产物；数据分类见 VOL-16 §4）
{"c2c_id":"C2C011","role":"plan|result","stage_id":"S03","stage_revision":3,
 "risk_effective":"HIGH","packet_sha256":"…",
 "data_class":"project_internal",
 "content_ref":"blob:sha256:…","allowlist_refs":["ev:s:4102"]}
```

## 11. 枚举汇总（单一事实源）

- 风险：`LOW/NORMAL/HIGH/CRITICAL`；网络策略：`none|allowlist:<id>`；信任：`external_untrusted|internal`；
- 数据分类：`public|project_internal|sensitive|secret`；
- verification：`lane_reported|verified_fetch|source_unverified|conflicted`；
- H0 coverage：见 §5；FSM 状态：见各引擎卷（08/09/10/11）；
- 能力状态：`DOCUMENTED/OBSERVED/EXPERIMENTAL/UNKNOWN/UNAVAILABLE/OBSERVED_DIFFERENT`。
