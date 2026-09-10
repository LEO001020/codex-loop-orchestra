# 模型标识解析：gemini3.8flash

范围：只读源码/配置核查，未联网、未修改任务范围外文件、未读取密钥值。

## 结论

- 用户指定网关为 OpenCodex，模型 ID 必须原样保留 `gemini3.8flash`。
- LOOP 自身的 model 字符串格式是 `provider/model`，因此在本 LOOP 配置内
  落地形态只能是 `<provider>/gemini3.8flash`，其原样模型 ID 部分是
  `gemini3.8flash`。
- 当前 OpenCodex 配置没有 Gemini provider，也没有任何 `gemini3.8flash`
  模型条目。`<provider>` 不能由现有文件推导为 `google`；它必须是操作者
  决策并已在 `~/.opencodex/config.json` 注册的 provider key。

## 直接证据

- OpenCodex 现有 provider keys 仅是 `openai`、`weiwu`、`weiwu-k3`、
  `snaillmou`、`kimi`、`hax`；现有 `selectedModels` 与 `customModels` 中均无
  `gemini3.8flash`。检查对象：`C:\Users\hzq00\.opencodex\config.json`
  （只读；未读取任何密钥字段）。
- `C:\Users\hzq00\.codex\opencodex-catalog.json` 与
  `C:\Users\hzq00\.codex\models_cache.json` 各有 21 条模型记录，均无
  Gemini / `gemini3.8flash` 条目。
- LOOP 模型 profile 只接受带 provider 前缀的字符串：
  `harness/model_profile.py:102-113` 要求 `execution_model` 含 `/`。
- 子代理路由 gate 同样要求显式 `provider/model`：
  `hooks/root_agent_spawn_gate.py:45-52` 读取 profile，`root_agent_spawn_gate.py:88-97`
  校验模型串；`model_profiles.toml:2` 当前 active profile 是 `glm53f`，
  其模型为 `hax/glm-5.3-flash`（`config/model_profiles.toml:69-73`）。
- `~/.codex/config.toml:61` 的全局子代理默认是
  `hax/glm-5.3-flash`，但这是运行时默认，不是 Gemini 落点。

## 最小同步点

以下都不是“改模型名”，而是把原样 `gemini3.8flash` 挂到已选 provider 的
最小登记面；`<provider>` 需由操作者明确选择：

1. `~/.opencodex/config.json`：provider 必须已配置；`model_profile.py`
   在缺 provider 时失败：`harness/model_profile.py:189-195`。若新增模型，
   会写入该 provider 的 `models`、`selectedModels`、`subagentModels` 与
   `customModels`，`customModels.modelId` 保持原样
   （`harness/model_profile.py:189-208`）。
2. `config/model_catalog_allowlist.toml`：`catalog-sync` 只把 allowlist
   中的 provider/model 重建进 catalog，并把 active execution model
   要求为 allowlist 成员：`harness/model_route_manager.py:150-167`、
   `harness/model_route_manager.py:134-146`、`harness/model_route_manager.py:229-267`。
   因此需要新增 `<provider> = ["gemini3.8flash"]` 或在既有 provider 数组
   追加原样字符串。
3. `config/model_profiles.toml`：新增/切换 profile 时使用
   `<provider>/gemini3.8flash`；这是 LOOP 的落地 model 字符串。
4. 切换 profile 会同步以下引用：
   `config/orchestration_policy_v2.toml`、`config/roles.yaml`、项目与全局
   `agents/{worker,duty_officer,reviewer,verifier,plan_expander}.toml`、
   `.codex/config.toml`、`config/config.toml.example`、
   `~/.codex/config.toml`、WSL 对应副本，以及 catalog/OpenCodex 配置
   （`harness/model_profile.py:117-167`、`harness/model_profile.py:212-252`、
   `harness/model_profile.py:320-330`）。

## 可选但有条件的同步

- `config/orchestration_policy_v2.toml:53` 的 `execution_aliases`：
  仅当该模型作为普通执行 profile 且需要避免窗口内流量计量进入 `unknown`
  时，把 `<provider>/gemini3.8flash` 追加进去。meter 通过该数组归入
  `v4` 执行桶：`metering/model_token_share_v2.py:132-134`。不是功能启用
  的必要条件。
- catalog 上下文窗口：`catalog_context()` 先查 provider 的
  `modelContextWindows`，再查 `customModels.contextWindow`，最后 provider
  cap，都没有时 fallback 990000（`harness/model_route_manager.py:197-227`）。
  若需要精确能力元数据，应补充其中一处；若可接受 fallback，可最小省略。

## 仍无法确认的事项

- 正确的 provider key：OpenCodex 现配置没有 Gemini provider，仓库/配置中
  无 `gemini3.8flash` 先例，不能从现有数据推导为 `google` 或其他名。
- 该 gateway 是否能直接服务原样 `gemini3.8flash`：需要 provider 端点或
  gateway 模型清单确认；本任务禁止联网，未核实。
- 精确上下文窗口与 compaction 元数据：本地无该模型记录，当前机制会
  fallback；不应虚构数值。
- Gemini 专属能力字段：现有 catalog 模板未表达或未覆盖的 Gemini 专属
  能力，本地无法确认。

## 结论

在“用户明确 OpenCodex、模型 ID 原样 `gemini3.8flash`、最小改动”约束下：
LOOP 侧落地 model 字符串为 `<provider>/gemini3.8flash`，provider key 必须由
操作者显式确定并先注册到 OpenCodex；模型 ID 不改名。需同步 allowlist 与
profile；catalog/OpenCodex 模型注册由 `model_profile.py` 或
`model_route_manager.py catalog-sync` 补齐，精确上下文能力元数据为可选。
