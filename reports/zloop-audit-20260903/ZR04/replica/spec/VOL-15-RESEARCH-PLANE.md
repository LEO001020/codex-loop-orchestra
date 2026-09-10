# VOL-15 — Research Plane：把事实封闭变成可溯源信息流

> **ZLoop Spec v1.0** · 卷 15/22 · 层级 L2 · 依赖：VOL-02, VOL-04
> 定位：信息服务，不是真理服务。root 只见一个 CLI family；Broker 内部 Luna/Kimi 多 lane；**root 自己综合**，不套第三个 LLM merge。

---

## 1. 对外接口（root 唯一入口）

```text
zloop research run   <spec.json>        # 前台便捷（<10min）
zloop research start  <spec.json>       # → research_id（后台可）
zloop research await  <research_id> [--timeout 540]   # 单次有界等待
zloop research show   <research_id>     # bounded manifest
zloop research evidence <ref>           # 精读单条（全文在 blob）
```
禁止同时暴露 `luna_search/kimi_search/bing_search` 等同义工具面。spec schema（VOL-04 §10）：

```json
{"research_id":"RS004","stage_id":"S03","questions":[
   {"id":"Q1","query":"…","source_preferences":["official","github","paper"],"freshness_days":30}],
 "lanes":["luna","kimi"],"max_parallel":10,"timeout_s":180,"trust":"external_untrusted"}
```

## 2. Lane：Luna（entitlement 必须实证）

| 优先级 | lane | 启用条件 |
|---|---|---|
| 1 | `LunaCodexLane`（Codex SDK + ChatGPT 登录） | **只有 live probe 实际出现 webSearch tool/event、拿到真实 source URL 才算 AVAILABLE**（P-LUNA1）。模型页写"支持 Web Search"≠ 当前 membership/Codex 路由注入了 hosted search（membership 可用性未核实） |
| 2 | `LunaResponsesLane`（API key） | 仅当用户明确有 API billing；与 membership 成本/配额分开报告 |

- 404/model-not-found/no-tool ⇒ `UNAVAILABLE / OBSERVED_DIFFERENT`；**绝不让 Luna 用训练知识伪装 freshness**。
- 记录：exact model alias/snapshot、auth route、reasoning level、tool catalog、真实 web_search event、rate limit、source provenance。
- 默认 4–12 并行；实际宽度由 provider 限流与 J(p) 实测定。

## 3. Lane：Kimi（K1 server 优先，K2 CLI fallback）

**K1 `KimiServerLane`**（`kimi web`，experimental）：
```text
bind 127.0.0.1:58627（loopback only）
鉴权: Authorization: Bearer <token>（token 只存内存/OS secret storage；绝不写日志/URL fragment）
启动时 GET /openapi.json + /asyncapi.json → sha256 入 manifest（live spec wins；hash 变 ⇒ contract test 触发）
POST /api/v1/sessions  { metadata.cwd = <独立 research cwd> }（workspace_id 不给）
WS /api/v1/ws → subscribe → turn.started/…/turn.ended
终止判据: last_turn_reason ∈ {completed, cancelled, failed}；transcript state ∈ {…completed/failed/cancelled}
恢复源: GET …/messages（history）；中止: POST …/sessions/{id}:abort
超时/断流: resync_required → snapshot 端点；超过 bounded 重试预算 ⇒ lane 降级 K2
```
**K2 `KimiCliLane`**（fallback）：
```text
kimi -p "<question>" --output-format stream-json
```
- async drain stdout+stderr；**process exit 0 ≠ success、tool event ≠ success**（I27，实证 #1897：0.27.0 在 stdout backpressure 下丢 final assistant/resume hint，本机 0.28.1 视为未修复直到 P-KIM1 实测）。
- completion 判据：JSONL 末行 = 非 tool-call 的 Assistant message（文档未定义显式 final marker）；缺失 ⇒ `INCOMPLETE_OUTPUT` → bounded recovery（server 侧 messages 补读）→ retry → 仍缺则该问题标失败。

## 4. Bounded Evidence Manifest（root 只看这个）

每条 claim 只带：claim、URL/title、source_class、retrieved/published/available 时间、lane、conflict group、raw_ref、verification、trust（VOL-04 §10 schema）。全文进 blob。冲突不"投票平均"：保留冲突，优先序 = 官方标准/源码/生成 schema/原始论文/原始数据 > 二手解读；量化/历史事实保留三个时间戳防 look-ahead。

## 5. Source promotion（HIGH/CRITICAL 事实的硬门 [I40]）

```text
lane 返回 claim/URL → host 侧安全 fetch 取得真实页面/schema/源码
→ hash/snapshot → verification = verified_fetch
无法取得 → source_unverified，risk/oracle/C2C 层显式看到该限制
```
**模型生成一个看似官方的 URL 本身不构成 primary evidence**（citation laundering 测试：fake/localhost/private URL、redirect 陷阱全进 negative test）。

## 6. Fetch 安全（SSRF 策略）

- 只允许 `http/https`；解析（含 redirect 逐跳）后拒绝 loopback、link-local、RFC1918/private、`file:`、UNC，除非任务显式属于受控内网研究并有 allowlist 条目。
- 外部页面内容永远是 data，不是 control instruction（I12；注入防御见 VOL-17 §4）。

## 7. Sandbox 与健康

- Luna/Kimi session 运行在**独立空/临时 research cwd**：不接触 canonical 可写根、exchange 凭据、signing secrets、C2C cookies、S/H 权威路径 [I42]；确需 repo 内容时给 read-only selected-context bundle（hash + size + data_class 标注）。
- env 以 **allowlist 构造**（VOL-17 §3）——root 环境里的 `ALIBABA_TOKEN_PLAN_API_KEY`、`ZAI_OAUTH_CLIENT_ID` 等绝不继承。
- SearchHealth 与 InferenceHealth 独立熔断 [I18]：search 503 不触发主推理 reroute；429/503 指数退避 + jitter。
- cache/singleflight：同 provider+同 query+同 freshness 可缓存；跨 Luna/Kimi 不 dedup（coverage 的一部分）；缓存命中/重复源/延迟/错误/freshness miss 全记录。

## 8. 多样性的理论边界（诚实）

双 lane 的理由是 **source coverage、搜索路径差异、freshness、outage resilience**——不是"跨模型就判断独立"。judge error decorrelation 属于 C2C 的证据理论；相关错误研究（350+ LLM 强模型错误高度相关）说明检索与评审的独立性都不能靠"换个模型"自动获得。
