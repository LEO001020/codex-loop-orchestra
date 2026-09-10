# 并发路由最终真值审计报告 (L2, 只读)
Verdict: PASS

## 断言-证据对照
1. 16 wave / 48 target — refill_policy.toml:12-14 target_total=48, v4=36, k3=12, 唯一并发权威(头注释 P0-8.3), orchestration_common.py:502-509 fail-closed 直读; refill_controller_v2.py:244-251 按 target 补债. controller 不硬编 16 (v1 缺陷已修: PolicyError).
2. 8 native 仅偏好 — 无文件以 native=8 限制有效并发; "8" 仅出现在 spawn_throttle max_initializing=8 与 health_gate_every=8 (节流/健康门, 非 cap); native/exec 双 roster 全量汇总 (refill_controller_v2.py:172-205). Desktop 8 偏好执行在 Sol prompt 层, 本次范围内代码无 8-cap.
3. 36/12 借调 — refill_controller_v2.py:225-231: reservations_borrowable 且单一 active pool 时该池得 target_total=48, 多池恢复 36/12 (watermark reclaim); refill_policy.toml:19 reservations_borrowable=true.
4. 空 demand 零出生 — refill_controller_v2.py:238,244: queue_empty 时全池 required=false, deficit=0, reason=queue_empty(:257); raw_debt 以 min(pending,...) 封顶(pending=0=>0); consumer build_manifest 无任务返回 None => idle (refill_consumer_v2.py:115-117, 文档串 :19-20 canary).
5. stable-running 销债 — 仅 roster state=="running" 计为有效并发 (headless_wave.py:173-177 observed; 250-252 debt=min(pending, target-running), initializing 仅 reserve 不销债); wait_observed 要求持续 0.25s (headless_wave.py:179-189); 未确认出生明示不销外部债 (:426-429).
6. 实际模型而非 role 标签 — native/pending/exec 三处 roster 汇总: role 缺失时回退 classify_pool(model) 按实际模型串归池 (refill_controller_v2.py:182,190,202); resolve_role_pin 拒绝 TOML/policy 模型分歧 (dispatch_v2.py:164-165,189).
7. 无 roleless Sol — 出生路径强制显式 role: ROLE_SET 白名单 (headless_wave.py:81), dispatch role_for_entry 缺显式 role 直接拒绝 "refusing inherited/default model" (refill_consumer_v2.py:48-56); dispatch.py:415-417 supervisor 无 model 直接 raise; ROLE_FAMILY 无映射即 ModelPinError (dispatch_v2.py:171); Sol 为 turn 非 spawn, 且受 sol_budget_block_v2 治理.

## 复核命令
Get-Content harness\{headless_wave,refill_controller_v2,refill_consumer_v2,dispatch_v2,agent_router}.py; Get-Content config\refill_policy.toml, config\orchestration_policy_v2.toml; Select-String refill_controller_v2.py -Pattern 'required = |queue_empty|classify_pool'
