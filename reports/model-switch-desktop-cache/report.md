# Desktop 角色缓存审计报告

任务：Desktop multi-agent 角色固定 model 元数据从哪里生成/安装/缓存，修改 profile 后是否需要 cachebuster、重装、重启或同步用户目录。

## 结论

- 当前 active profile 为 `glm53f`，所有五个角色的固定 model 均为 `hax/glm-5.3-flash`（execution 和 review 同值）。**Desktop 缓存当前完全同步，无需 cachebuster、重装、重启或同步用户目录。**
- Desktop multi-agent 的固定 model 元数据存储在 `~/.codex/agents/*.toml`（Windows 下为 `C:\Users\hzq00\.codex\agents\`），不是插件缓存。不涉及 cachebuster 机制。
- 只有修改 model/effort 值时需要同步用户目录：运行 `python harness/model_profile.py set <profile> --root E:\codex-LOOP\codex-loop-s-f2 --codex-home C:\Users\hzq00\.codex --json`（或 `python harness/model_route_manager.py <command>`，内部调用同一 PROFILE_TOOL）。新线程即刻生效，无需重启；运行中的线程/子代理保持旧值（per-thread 固定）。UI 不会热更新已运行线程。
- 仅 worker.toml 存在 3 行注释文档漂移（2+/1-，不含 model 值），属文档不一致，非缓存失效，最小修改原则下不处理。

## 证据链

| 角色 | 仓库源 model | 用户目录 model | 状态 |
|------|-------------|---------------|------|
| worker | hax/glm-5.3-flash | hax/glm-5.3-flash | 一致 |
| verifier | hax/glm-5.3-flash | hax/glm-5.3-flash | 一致 |
| reviewer | hax/glm-5.3-flash | hax/glm-5.3-flash | 一致 |
| duty_officer | hax/glm-5.3-flash | hax/glm-5.3-flash | 一致 |
| plan_expander | hax/glm-5.3-flash | hax/glm-5.3-flash | 一致 |

所有角色的 `model_reasoning_effort = "max"`、`model_context_window = 1000000`、`model_auto_compact_token_limit = 800000` 均一致。

### 关键源文件与行号

| 文件 | 作用 |
|------|------|
| `config/model_profiles.toml` | profile 定义；`active_profile = "glm53f"`，`execution_model = "hax/glm-5.3-flash"` |
| `harness/model_profile.py` | 唯一同步器：原子读写 profile → 仓库 agents TOML → 用户目录 agents TOML → config.toml `[agents]` |
| `install.sh`（行 119-135） | 安装时逐文件比较并复制 `agents/*.toml` 到 `$CODEX_HOME/agents/`，替换前备份 |
| `harness/dispatch.py`（行 73-79） | 运行时查找顺序：`LOOP_ROOT/agents/` → `$CODEX_HOME/agents/` → 包目录 |
| `harness/dispatch.py`（行 90-114） | Desktop native spawn 直接加载角色 TOML（single source of truth）；headless exec 通过 CLI `-m`/`-c` 覆盖 |
| `config/config.toml` `[agents]` | `default_subagent_model = "hax/glm-5.3-flash"` 作为未指定角色时的回退层 |

### 差异证据

`worker.toml` 注释头漂移（3 行）：仓库源使用泛化的 "pinned from config/model_profiles.toml active profile at rotation time" 措辞，用户目录副本仍为旧的 "DeepSeek V4 Flash, ultra" 措辞。该差异由某次手动修改仓库源注释但未重跑同步引起，不影响运行时行为。

### cachebuster 适用范围

cachebuster（`update_plugin_cachebuster.py`）仅用于插件版本变更后强制重装插件缓存（`~/.codex/plugins/cache/`）。角色 TOML 不在插件缓存体系中，不适用。

## 更新命令（修改 profile 时才使用）

```bash
python harness/model_profile.py set <profile-name> \
  --root E:\codex-LOOP\codex-loop-s-f2 \
  --codex-home C:\Users\hzq00\.codex \
  --json
```

该命令原子更新：仓库 `agents/*.toml` → 用户目录 `agents/*.toml` → `config.toml [agents]` 默认值。执行后新线程立即使用新值。

## 验证命令（可机械复核）

```bash
# 对比两侧 model 行
rg -n "^model" --glob '*.toml' C:\Users\hzq00\.codex\agents
rg -n "^model" --glob '*.toml' E:\codex-LOOP\codex-loop-s-f2\agents

# 确认 config.toml 默认值
rg -n "default_subagent_model" C:\Users\hzq00\.codex\config.toml
```
