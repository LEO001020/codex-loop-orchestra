# 宣传边界审查 — L2 核对结论

## 最强公开声明（允许说）
仓库将“单父对话 20 并发、跨对话跨平面全局 80”写入 config/refill_policy.toml
作为唯一并发权威（policy_version 2026-08-13.dialogue-20-global-80），补位控制器
实时按 running/initializing 缺口补位，并经 OpenCodex 路由在多模型档案间显式切换
执行/审核组合（当前 active_profile=v4f-v4p）。

## 三条禁用绝对化说法
1. “已持续/稳定运行在 20/80 并发”——当前 refill_state.json 观察值 active total=1、
   deficit=0；20/80 是策略目标，非已验证的持续稳态。
2. “支持任意模型”——仅配置档案内模型可用；weiwu-k3/kimi-k3 被全局协议禁止
   （global_working_agreement.md:22），且 OpenCodex 路由校验 fail-closed。
3. “补位全自动、永不失败”——OpenCodex 健康门禁失败会阻断出生并回退 30s
   （dispatch.py:222），补位相关 roster-stability 事件曾进死信。

## 证据
- config/refill_policy.toml:7,13-16
- config/model_profiles.toml:2,48-52
- harness/refill_controller_v2.py:439,475
- harness/refill_consumer_v2.py:434；harness/lifecycle_supervisor.py:804
- harness/model_profile.py:185-190；harness/dispatch.py:222
- config/global_working_agreement.md:22
- data/refill/refill_state.json（active total=1，deficit=0）
