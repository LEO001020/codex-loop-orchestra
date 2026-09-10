# ZLoop 新会话物理验证报告（P-SESS2）

**验证时间**：2026-09-03
**工作区**：`E:\zcode\zloop-gen8`
**执行模式**：物理验证（Read-only，无现场修复）

---

## 检查汇总表

| 检查项 | 验证内容 | 关键物理证据 / 状态 | 判定 |
| :--- | :--- | :--- | :--- |
| **检查 0** | 会话自识与工作区纳管修复 | Root Session ID: `sess_4aa91bae-03c0-4310-a2ed-013b2ed53916`<br>Provider: `3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7` (Gemini)<br>Project Attach 输出 UUID: `5f07712b-1822-4a56-bf77-68b3a5cda7d1` | **PASS** |
| **检查 1** | ZLoop 运行环境自检（Doctor） | Registry 变为 1 project(s)，退出码 0，无 ERROR 级条目 | **PASS** |
| **检查 2** | Hook 首验（证据日志捕获） | 项目历史日志文件 `history/sessions/sess_4aa91bae-03c0-4310-a2ed-013b2ed53916.ndjson` 成功创建并持续落盘（已捕获 11+ 条工具执行事件） | **PASS** |
| **检查 3** | Skills 关闭状态与 S1 Token 瘦身基线对账 | 系统提示词 Skills 声明数: 0<br>S0 基线: 26,856 Tokens<br>S1 首轮输入: 12,520 Tokens<br>削减幅度: **-14,336 Tokens (-53.38%)** | **PASS** |
| **检查 4** | P-HET1 异构多 Provider 路由二次核验 | Subagent Provider: `af9697f5-a1f2-4616-8350-e14311d14fda`<br>Subagent Model: `glm-5.3`<br>Status: `completed`<br>Root Provider: `3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7` | **PASS** |

---

## 详细执行记录与证据

### 检查 0：会话自识与工作区纳管修复

1. **根会话模型信息**：
   - Session ID: `sess_4aa91bae-03c0-4310-a2ed-013b2ed53916`
   - Provider ID: `3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7`
   - Model ID: `gemini-3.8-flash-high`

2. **工作区纳管执行**：
   - 执行命令：
     ```bash
     cd "E:/zcode/zloop-gen8" && .venv/Scripts/python -m zloop.cli project attach
     ```
   - 物理输出：
     ```json
     {"project_id": "5f07712b-1822-4a56-bf77-68b3a5cda7d1", "created": true, "git_root": "E:\\zcode\\zloop-gen8", "git_common_dir": "E:\\zcode\\zloop-gen8\\.git", "display_name": "zloop-gen8"}
     ```
   - 判定：**PASS**

---

### 检查 1：ZLoop 运行环境自检（Doctor）

- 执行命令：
  ```bash
  cd "E:/zcode/zloop-gen8" && .venv/Scripts/python -m zloop.cli doctor
  ```
- 物理输出：
  ```text
  data root: C:\Users\hzq00\.zloop
  journal profile: {"journal_mode": "DELETE", "synchronous": "EXTRA", "wal_ok": false, "runtime_sqlite": "3.50.4", "reason": "sqlite (3, 50, 4) is inside the WAL-reset affected range (3.7.0-3.51.2, below the 3.50.7 backport); DELETE+EXTRA enforced by gate (I22)"}
  registry: 1 project(s)
  project 5f07712b-1822-4a56-bf77-68b3a5cda7d1 (zloop-gen8): no control DB yet (no runs)
  hooks: {"config_exists": true, "config_path": "C:\\Users\\hzq00\\.zcode\\cli\\config.json", "hooks_enabled": true, "event_count": 5, "zloop_managed": true, "command": "E:\\zcode\\zloop-gen8\\.venv\\Scripts\\python.exe"}
  WARN: old LOOP machine-wide Codex hooks present (P-HYG1)
  ```
- 退出码：`0`
- Registry 状态：`1 project(s)`，无任何 ERROR 级条目。
- 判定：**PASS**

---

### 检查 2：Hook 首验（证据日志捕获）

- 现象与分析：
  1. 提示词预置脚本查询的 slug 路径 `proj_e-zcode-zloop-gen8` 不存在，因 Gen8 架构的 project_id 格式采用随机 UUID（本工程为 `5f07712b-1822-4a56-bf77-68b3a5cda7d1`）。
  2. 在未触发 live wave 前，`control.sqlite3` 按设计尚未生成（Doctor 明确提示 `no control DB yet (no runs)`）。
  3. 会话历史证据文件（Session Journal）位于：
     `C:\Users\hzq00\.zloop\projects\5f07712b-1822-4a56-bf77-68b3a5cda7d1\history\sessions\sess_4aa91bae-03c0-4310-a2ed-013b2ed53916.ndjson`
  4. 查验证据内容：
     自工作区执行 `project attach` 纳管后，本会话的所有 Hook 事件均被正常捕获并哈希链式记录在 ndjson 日志中（包含 seq 1 至 seq 11+ 等连续 `tool_result / PostToolUse` 事件记录）。
- 判定：**PASS**（物理 Journal 证据完整落盘）。

---

### 检查 3：Skills 关闭状态与 S1 Token 瘦身基线对账

1. **Skills 数量盘点**：
   - `C:\Users\hzq00\.zcode\cli\config.json` 配置：
     - `"skills": {"enabled": False}`
     - `"mcpServers": None`
   - 当前会话提示词与注入工具中：**0 个 Skills**（未注入任何 browser-use, computer-use 等技能元数据）。

2. **S1 输入 Token 对账**：
   - 物理查询结果：
     - `sess_4aa91bae-03c0-4310-a2ed-013b2ed53916` 首轮输入：
       ```json
       {"inputTokens": 12520, "outputTokens": 129, "totalTokens": 12649, "cacheReadTokens": 0, "reasoningTokens": 266}
       ```
     - S0 基线 Token 数：`26,856`
     - S1 当前 Token 数：`12,520`
     - 削减量：`26,856 - 12,520 = 14,336` Tokens
     - 削减比例：**-53.38%**
- 判定：**PASS**（Skills 全部关闭，S1 输入 Token 发生显著断崖式削减）。

---

### 检查 4：P-HET1 异构多 Provider 路由二次核验

1. **子代理派发**：
   - subagent_type: `zloop-auditor`
   - prompt: `Reply with the single word ACKNOWLEDGED and nothing else.`
   - 返回内容：`ACKNOWLEDGED`（消耗 subagent_tokens: 2,329）

2. **底表物理对账结果**：
   ```text
   === P-HET1 ROUTING RESULT ===
   Subagent Provider: af9697f5-a1f2-4616-8350-e14311d14fda
   Subagent Model:    glm-5.3
   Subagent Status:   completed
   Root Provider:     3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7
   ```
3. **判定依据**：
   - Subagent Provider 严格等于 `af9697f5-a1f2-4616-8350-e14311d14fda`（GLM 专有 Provider）；
   - Subagent Model 严格等于 `glm-5.3`；
   - Subagent Status 严格等于 `completed`；
   - 根会话 Provider（`3f0e0bfa-2ab5-45ff-ba40-51fd19811bf7`，Gemini）与子代理 Provider 物理相异且完全隔离直通。
- 判定：**PASS**

---

## 结论
所有 5 项检查（检查 0 至检查 4）全部通过（**ALL PASS**）。ZLoop Gen8 在关闭内置 Skills/MCP 后瘦身显著，工作区纳管成功，Hook 证据捕获及异构模型路由均已通过物理对账验证。
