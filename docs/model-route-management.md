# LOOP 模型与 API 路由管理

模型路由分成三层，避免每次换 API 时重复修改配置或把密钥写进仓库：

1. `~/.opencodex/config.json`：只保存供应商、API 地址和凭据。一个供应商只配置一次。
2. `config/model_profiles.toml`：只保存 LOOP 各阶段使用的 `provider/model` 与推理强度，不保存密钥。
3. `config/model_catalog_allowlist.toml`：控制 Codex 模型选择器和 OpenCodex 自动发现范围。原生 GPT 条目（Sol / Terra / Luna，以及 Spark）必须保留；已失效供应商不得再写入。

统一入口：

```powershell
py -3 harness/model_route_manager.py status
py -3 harness/model_route_manager.py enable-grok
py -3 harness/model_route_manager.py restore
py -3 harness/model_route_manager.py catalog-sync
```

- `enable-grok`：记录当前 profile，临时将执行层切到 `snaillmou/grok-4.6`，同步 Windows、WSL、全局 Agent 定义和模型目录。
- `restore`：恢复临时切换前的 profile；当前恢复目标是 `v4f-v4p`。
- `catalog-sync`：重新应用供应商 `selectedModels`、关闭 `liveModels`，并按 allowlist 重建本地 Codex catalog。会从 `~/.codex/models_cache.json` 还原官方原生模型记录。
- 每次变更前会在 `~/.opencodex/backups/model-route-manager-*` 保存本地备份；备份可能含 API 凭据，不得提交到 Git。

Codex app-server 会缓存启动时的模型目录。路由对新派发可以立即生效；模型选择器若仍显示旧条目，正常退出并重启 Codex Desktop 后再看。不要在正在回答的根任务中使用 `ocx sync --restart-codex` 强制终止 app-server。
