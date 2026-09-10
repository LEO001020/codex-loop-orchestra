# 计划Producer接线审计 (2026-08-12)

结论: **PASS** — skeleton_ready→EXPAND_K3 无生产 producer, 属 cold_start fail-silent 回退, 非 layered 缺失。

## 证据链
- 当前模式: config/orchestration_policy_v2.toml:59 `mode = "cold_start"`
- 安全回退: harness/plan_consumer.py:156-157 `if self.mode == "cold_start": return stats`
- 无 producer: harness/hooks/agents/config 无代码写 data/plans/inbox/*.json; 无 ControlPacket/DecisionSkeleton 生成器
- 运行时状态: data/plans 不存在 (审计首次调用后已还原 mkdir 副作用)
- 若启用 layered: plan_consumer.py:203 (skeleton_ready), :210 调 plan_pipeline.py, :230/:239 (expansion_valid/invalid); consumer 挂接 statemachine_v2.py:649-650 -> orchestration_epilogue.py:44 run_plan_once
- 状态机表: statemachine_v2.py:127-129 t27-t29 齐备; 生产入口 = statemachine step/reconcile CLI (:622-654; v1 镜像 statemachine.py:421-422)

## 可复现
`LOOP_ROOT=<repo> python harness/plan_consumer.py` 输出:
`{"mode":"cold_start","discovered":0,"claimed":0,"completed":0,"failed":0,"errors":[]}` rc=0

## 附注 (非阻塞)
triggers_v2.yaml:76-82 声明 emitted_by=plan_pipeline.py, 与实际发射器 plan_consumer.py 不符, 文书错位。
