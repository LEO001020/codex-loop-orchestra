命令执行摘要:`n1. `zloop --help` - 退出码: 0`n2. `zloop --version` - 退出码: 0; 版本: 0.1.0`n3. `zloop doctor` - 退出码: 0`n4. `zloop project list` - 退出码: 0`n5. `zloop run list` - 退出码: 0`n6. `zloop binding status` - 退出码: 0

## 安全命令输出补证

### `zloop doctor`

退出码：`0`

stdout：

```text
data root: C:\Users\hzq00\.zloop
journal profile: {"journal_mode": "DELETE", "synchronous": "EXTRA", "wal_ok": false, "runtime_sqlite": "3.50.4", "reason": "sqlite (3, 50, 4) is inside the WAL-reset affected range (3.7.0-3.51.2, below the 3.50.7 backport); DELETE+EXTRA enforced by gate (I22)"}
registry: 1 project(s)
project 5f07712b-1822-4a56-bf77-68b3a5cda7d1 (zloop-gen8): quick_check=ok journal=DELETE
hooks: {"config_exists": true, "config_path": "C:\\Users\\hzq00\\.zcode\\cli\\config.json", "hooks_enabled": true, "event_count": 5, "zloop_managed": true, "command": "E:\\zcode\\zloop-gen8\\.venv\\Scripts\\python.exe"}
WARN: old LOOP machine-wide Codex hooks present (P-HYG1)
```

stderr：空。

### `zloop project list`

退出码：`0`

stdout：

```text
[{"project_id": "5f07712b-1822-4a56-bf77-68b3a5cda7d1", "git_root": "E:\\zcode\\zloop-gen8", "git_common_dir": "E:\\zcode\\zloop-gen8\\.git", "display_name": "zloop-gen8", "created_at": "2026-09-02T21:49:04Z"}]
```

stderr：空。

### `zloop run list`

退出码：`0`

stdout：

```text
[{"run_id": "R001", "project_id": "5f07712b-1822-4a56-bf77-68b3a5cda7d1", "objective": "build zloop metrics subsystem", "state": "ACTIVE", "created_at": "2026-09-02T21:54:10Z", "closed_at": null, "controller_nonce": null, "controller_pid": null, "controller_pid_start": null, "cancel_requested": 0}, {"run_id": "R002", "project_id": "5f07712b-1822-4a56-bf77-68b3a5cda7d1", "objective": "Log Exporter Development", "state": "ACTIVE", "created_at": "2026-09-03T09:46:44Z", "closed_at": null, "controller_nonce": null, "controller_pid": null, "controller_pid_start": null, "cancel_requested": 0}]
```

stderr：空。

### `zloop binding status`

退出码：`0`

stdout：

```text
{"project_id": "5f07712b-1822-4a56-bf77-68b3a5cda7d1", "bindings": [{"zcode_session_id": "sess_01162d8d-ce5d-4f16-94ea-a435156ecb6c", "project_id": "5f07712b-1822-4a56-bf77-68b3a5cda7d1", "run_id": "R001", "stage_id": null, "binding_epoch": 1, "resume_after_clear": 0, "updated_at": "2026-09-02T21:54:10Z"}, {"zcode_session_id": "sess_9ed9383f-4bd7-45ea-bd28-4f944ffa670b", "project_id": "5f07712b-1822-4a56-bf77-68b3a5cda7d1", "run_id": "R002", "stage_id": null, "binding_epoch": 1, "resume_after_clear": 0, "updated_at": "2026-09-03T09:46:44Z"}], "pending_claims": []}
```

stderr：空。
