# 状态机发布闭环审计（只读）

结论：REDO —— 5 处缺陷（P0×2 / P1×3）。file:line 相对 E:\codex-LOOP\codex-loop-s-f2\。机械信号（table 导入校验 / exactly-once canary / CLI marshal / layered_gate 条件全集）未复验。以下均可复现于当前 HEAD。

P0-1 exactly-once 上存在两个可追溯的洞：

 seed 洞：harness/dispatch.py:723-729 `release_review_ledger_seed` 在 dry_run=False 路径（dispatch.py:765-766）每次都执行，行为是把 `rr-wave<N>` 重置成 DISPATCHABLE 并清空 history、attempts；这仅在 load_release_review_record 未见 `status=dispatched`（dispatch.py:663-674, 758-761）时才成立，但 marker 文件被删/损坏/并行调用使 existing 判定落空时就会静默重种子。与之叠加的 t26（harness/statemachine_v2.py:427-431）：`release_review`+RUNNING+exec_failed 直接进 SOL_ADJUDICATE。上一轮 reviewer 刚 MERGED，标记丢失即可让同一 wave 在同一 ledger 再次落入 SOL_ADJUDICATE，破坏"result always returns to SOL_ADJUDICATE"本意中的幂等约定。

 双派发洞：harness/l2_consumer.py:745-747 以 heartbeat freeze 判 stale、`os.replace(claim, reaped)`（l2_consumer.py:752）释放 idem_key；而 complete() 的顺序是先 validate→写 completion→emit verdict→**最后 unlink claim**（harness/l2_consumer.py:857-875）。若 verify 进程在"respawn → verdict emitted → 未 unlink"间崩溃，且 claim 已被 stale-reap，则下一轮 drain 看到无 claim+无 completion 就会再 dispatch 同一 idem_key。两次 verdict 中先到者写 completion、后到者永远 DUPLICATE，与"先到者"由心跳时机决定的非决定性事实相结合，违反 exactly-once 语义。

P0-2 排序门缺失：`<=3 candidates` 只是 t33 的注释（harness/statemachine_v2.py:133），从 L2_VERIFY 到 L2_RANK 没有任何候选数校验；生产者（harness/result_reducer.py:360）也不读 candidate 长度，t36 L2_RANK→L2_VERIFY（statemachine_v2.py:136）直接回到单源 verify。>3 候选时仍会静默走 ranking 路径，discrimination 前提（随机顺序、漏检 ≤3）不成立。

P1-1 TIMED_OUT 恢复路径依赖 synthetic spawn ts：harness/statemachine_v2.py:521-544 watchdog 在 spawn_times 缺失时用 ledger 里最近 transition-to-RUNNING 的 ts 作为 spawn_ts（_synthetic_spawn_ts），但 duty_officer 走 t38/t11/t12 把包从 DUTY_REVIEW 推回 RUNNING 后，spawn_times.json 不会被重新打点；新 RUNNING 期的 `elapsed` 由历史 ts 决定，第二个超时窗被"旧时钟"计算，重启/冷启动后 TIMED_OUT 的可观察恢复是非确定性的。

P1-2 Governor 对 K3/V4 是 fail-open 的白名单：harness/root_turn_governor.py:371-373 仅按 model 家族放 行 v4/k3，sol family 以外的表层不 gate；当 PreToolUse 缺 model 时 fail closed（line 374-378），但 break_glass 环境变量（harness/root_turn_governor.py:383-389）允许完全旁路 fail-closed，发布路径上的 relief 依赖一个无操作许可、无 attestation 的 OS env，audit 仅凭 `governor.break_glass` 事件可见。

P1-3 provider health/backoff 不是消费侧闸：harness/layered_gate.py:328-345/420 只在 layered enable 时读 data/provider_health/k3.json 并要求 `backoff_until <= clock`；实际 drain（harness/orchestration_epilogue.py:40-48）并不重取健康，l2_consumer.drain（harness/l2_consumer.py:683-723）也不查 backoff——dispatcher 失败/返回 False 只把 claim 留在原处，等 claim_stale_after_s=120s 后 stale-reap；K3 处于 backoff 期间新 verify 仍会被派发，错误只在 claim_reclaims 达 2 后才 verdict_escalate_l3（harness/l2_consumer.py:748-767），误伤窗口期间就是把故障 provider 当可用。
