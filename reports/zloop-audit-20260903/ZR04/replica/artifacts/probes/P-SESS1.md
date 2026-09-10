# ZLoop 新会话物理验证报告（P-SESS1）

- 验证日期：2026-09-03
- 执行环境：Windows 10.0.26200 x64, Git Bash
- 根工作目录：`E:\zcode\zloop-gen8`
- 目的：只读物理验证与子代理异构路由穿透对账

---

## 检查 0：会话自识与 Session ID 捕获

### 1. 根会话模型信息
- **模型全限定标识**：`3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7/gemini-3.8-flash-high`
- **提供商 ID (Provider ID)**：`3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7`
- **模型 ID (Model ID)**：`gemini-3.8-flash-high`

### 2. Session ID 获取
- **执行命令**：
  ```bash
  python -c "import sqlite3; conn=sqlite3.connect(r'file:C:/Users/hzq00/.zcode/cli/db/db.sqlite?mode=ro', uri=True); print(conn.execute('SELECT session_id FROM model_usage ORDER BY started_at DESC LIMIT 1').fetchone()[0]); conn.close()"
  ```
- **控制台输出**：
  ```text
  sess_d649471d-01e0-4b1d-9d85-a4793bbb3f24
  ```
- **当前根会话 Session ID**：`sess_d649471d-01e0-4b1d-9d85-a4793bbb3f24`

---

## 检查 1：ZLoop 运行环境自检（Doctor）

### 1. 执行命令
```bash
cd "E:/zcode/zloop-gen8"
.venv/Scripts/python -m zloop.cli doctor
```

### 2. 输出全文
```text
data root: C:\Users\hzq00\.zloop
journal profile: {"journal_mode": "DELETE", "synchronous": "EXTRA", "wal_ok": false, "runtime_sqlite": "3.50.4", "reason": "sqlite (3, 50, 4) is inside the WAL-reset affected range (3.7.0-3.51.2, below the 3.50.7 backport); DELETE+EXTRA enforced by gate (I22)"}
registry: 0 project(s)
hooks: {"config_exists": true, "config_path": "C:\\Users\\hzq00\\.zcode\\cli\\config.json", "hooks_enabled": true, "event_count": 5, "zloop_managed": true, "command": "E:\\zcode\\zloop-gen8\\.venv\\Scripts\\python.exe"}
WARN: old LOOP machine-wide Codex hooks present (P-HYG1)
```

### 3. 判定结果
- **判定**：**PASS**
- **依据**：进程退出码为 0，无任何 `ERROR` 级别输出（仅有一条 `WARN: old LOOP machine-wide Codex hooks present (P-HYG1)` 警告）。

---

## 检查 2：Hook 首验（SessionStart 是否落证据）

### 1. 执行命令
```bash
cd "E:/zcode/zloop-gen8"
.venv/Scripts/python -c "from zloop import paths; import os; p=paths.zloop_data_root(); print('Data root:', p); os.system(f'dir \"{p}\"')"
```

### 2. 控制台输出
```text
 驱动器 C 中的卷是 系统
 卷的序列号是 714D-C81B

 C:\Users\hzq00\.zloop 的目录

2026/09/02  19:18    <DIR>          .
2026/09/03  00:12    <DIR>          ..
2026/09/02  19:18    <DIR>          hygiene-backup
               0 个文件              0 字节
               3 个目录 142,299,586,560 可用字节
Data root: C:\Users\hzq00\.zloop
```

### 3. 现场原因核查
通过对 `C:\Users\hzq00\.zloop` 递归检索与 `zloop/hook.py` 逻辑核查：
- 目录内仅存在 `hygiene-backup\requirements.toml.20260902-191845.bak`，未见今日生成的 journal 或 session 文件。
- 根因：`doctor` 明确显示 `registry: 0 project(s)`。根据 `hook.py` 中 `_capture` 实现逻辑，当 `_resolve_capture_project` 返回 `None`（即工作区尚未通过 `zloop` 注册项目）时，H0 证据链不会将未纳管工作区的事件落盘到数据根目录中。

### 4. 判定结果
- **判定**：**FAIL**
- **依据**：严格按照判定标准（“能观察到今日更新的 journal / session 证据条目 -> PASS；否则 -> FAIL”），因未观测到今日更新的 journal / session 证据条目，判定为 FAIL。

---

## 检查 3：子代理可见性 + P-HET1 异构多 Provider 路由真实验收（核心）

### 3a. 可用 subagent_type 盘点
当前系统提示词与 Agent 工具描述中可用的全部 `subagent_type` 如下（共 6 个）：
1. `general-purpose`
2. `Explore`
3. `zloop-auditor`
4. `zloop-worker`
5. `document-skills:judge`
6. `judge`

- **结果**：已完整包含预期的 `zloop-worker` 与 `zloop-auditor`。

### 3b. 最小子代理探针调用
- **派发参数**：
  - `subagent_type`: `"zloop-auditor"`
  - `description`: `"Probe subagent provider routing"`
  - `prompt`: `"Reply with the single word ACKNOWLEDGED and nothing else."`
- **子代理执行返回**：
  ```text
  ACKNOWLEDGED
  agentId: agent_8a11998b-924e-4924-b096-d2a1351800aa
  <usage>
  subagent_tokens: 3798
  tool_uses: 0
  duration_ms: 7778
  </usage>
  ```

### 3c. 数据库物理对账执行
- **执行命令**：
  ```bash
  python - <<'PYEOF'
  import sqlite3
  conn = sqlite3.connect(r"file:C:/Users/hzq00/.zcode/cli/db/db.sqlite?mode=ro", uri=True)
  conn.row_factory = sqlite3.Row
  sub = conn.execute("SELECT session_id, provider_id, model_id, status, error_message, started_at FROM model_usage WHERE session_id LIKE 'sess_subagent%' ORDER BY started_at DESC LIMIT 1").fetchone()
  root = conn.execute("SELECT provider_id, model_id FROM model_usage WHERE session_id NOT LIKE 'sess_subagent%' ORDER BY started_at DESC LIMIT 1").fetchone()
  print("=== P-HET1 VERIFICATION RESULT ===")
  print(f"Subagent Session:  {sub['session_id']}")
  print(f"Subagent Provider: {sub['provider_id']}")
  print(f"Subagent Model:    {sub['model_id']}")
  print(f"Subagent Status:   {sub['status']}")
  print(f"Subagent Error:    {sub['error_message']}")
  print(f"Root Provider:     {root['provider_id']}")
  print(f"Root Model:        {root['model_id']}")
  conn.close()
  PYEOF
  ```

- **对账输出结果**：
  ```text
  === P-HET1 VERIFICATION RESULT ===
  Subagent Session:  sess_subagent_agent_8a11998b-924e-4924-b096-d2a1351800aa
  Subagent Provider: af9697f5-a1f2-4616-8350-e14311d14fda
  Subagent Model:    glm-5.3
  Subagent Status:   completed
  Subagent Error:    None
  Root Provider:     3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7
  Root Model:        gemini-3.8-flash-high
  ```

### 3d. 判定结果
- **判定**：**P-HET1 PASS**
- **依据**：
  - `Subagent Provider` 为 `af9697f5-a1f2-4616-8350-e14311d14fda`（严格命中独立 GLM-5.3 网关）；
  - `Subagent Status` 为 `completed`；
  - `Subagent Model` 为 `glm-5.3`；
  - `Root Provider` 为 `3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7` (`gemini-3.8-flash-high`)；
  - 证明全限定模型声明成功穿透客户端，直连独立 GLM-5.3 网关，完全实现物理异构隔离，无同构回流。

---

## 检查 4：S0 Token 基线与 Enabled Skills 盘点

### 1. Token 基线查询
- **执行命令（指定脚本）**：
  ```bash
  python - <<'PYEOF'
  import sqlite3
  conn = sqlite3.connect(r"file:C:/Users/hzq00/.zcode/cli/db/db.sqlite?mode=ro", uri=True)
  conn.row_factory = sqlite3.Row
  rows = conn.execute("SELECT raw_usage_json FROM model_usage WHERE session_id NOT LIKE 'sess_subagent%' ORDER BY started_at ASC LIMIT 1").fetchall()
  for r in rows:
      print("Raw Usage:", r['raw_usage_json'])
  conn.close()
  PYEOF
  ```
- **输出**：
  ```text
  Raw Usage: None
  ```
  *(注：全库全局 `ORDER BY started_at ASC LIMIT 1` 查询的是历史上最早的一条旧记录)*

- **补充对账：当前会话初始轮次（S0）实际 Token 记录**：
  ```json
  {"inputTokens":26856,"outputTokens":129,"totalTokens":26985,"cacheReadTokens":0,"reasoningTokens":374}
  ```

### 2. Enabled Skills 数量盘点
系统提示词中列出的可用技能列表（共 14 个）：
1. `browser-use:control-browser`
2. `browser-use:web-gui-tester`
3. `computer-use:computer-use`
4. `document-skills:docx`
5. `document-skills:pdf`
6. `document-skills:pptx`
7. `document-skills:xlsx`
8. `skill-creator:skill-creator`
9. `zcode-guide:diagnosing-commands`
10. `zcode-guide:diagnosing-hooks`
11. `zcode-guide:diagnosing-mcp`
12. `zcode-guide:diagnosing-plugins`
13. `zcode-guide:diagnosing-skills`
14. `zcode-guide:zcode-configuration-guide`

- **统计结果**：当前启用技能总数为 **14**。

---

## 汇总综述

| 检查项 | 检查内容 | 结果 | 关键事实 / 物理对账记录 |
| :--- | :--- | :---: | :--- |
| **检查 0** | 会话自识与 Session ID 捕获 | **PASS** | Session ID: `sess_d649471d-01e0-4b1d-9d85-a4793bbb3f24`, Model: `3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7/gemini-3.8-flash-high` |
| **检查 1** | ZLoop 运行环境自检 (Doctor) | **PASS** | 退出码 0，无 ERROR；存在 1 条老 hooks 警告 (`P-HYG1`) |
| **检查 2** | Hook 首验 (SessionStart 证据) | **FAIL** | 数据根目录无今日 journal/session 文件（因 `registry: 0 project(s)` 导致作用域未命中未落盘） |
| **检查 3** | 子代理异构多 Provider 路由 (P-HET1) | **P-HET1 PASS** | 子代理 Provider `af9697f5-a1f2-4616-8350-e14311d14fda` (`glm-5.3`)，根 Provider `3f0e0bfa...`，物理隔离穿透成功 |
| **检查 4** | S0 Token 基线与 Skills 盘点 | **PASS** | S0 初始输入 Tokens 26856；启用 Skills 数量 14 个 |
