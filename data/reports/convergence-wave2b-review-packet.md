# 第二轮 B Release ReviewPacket

本包针对 untracked v2 工作树无法由普通 `git diff` 完整呈现的问题，给出精确源码句柄与 SHA256。Reviewer 可只读打开下列文件并复算。

## 核心源码 SHA256

- `hooks/subagent_lifecycle.py` — `a2f4d45a5208e0ef9b89c41abfd4884caf450bfdcebc188088c70be86a41ded7`
- `hooks/sol_tool_gate_router.py` — `e9f77827db1e213527a4ad38a71aa2839b68936584a2a56298059461f19fea44`
- `harness/refill_consumer_v2.py` — `cce09c6c6caa74ca84f1096f2de976ad0c54870e3d1ee9f06210994b74488184`
- `harness/refill_controller_v2.py` — `32951732aba95552e299c83ddcdb9728226b4a5092be0a968b2105925185be5b`
- `harness/headless_wave.py` — `0466d15eee6ecf6fae1c5463ab3df472903ed05760c87baa4f1db2717acb9f26`
- `harness/dispatch_v2.py` — `4a1021a5daab12f639ee1dd013179b0d4207d8c6f572d33f0a00a5a2994a658b`
- `harness/dispatch.py` — `f51bc607d8bea4dd1997a4cb2e7f16955131cac0459721c66e71cd317bee6fb7`
- `harness/budget_controller.py` — `d18051d00b753a4e5bdc7257d614573b9d36a91cc80cf07998c10d53ac1e3ced`
- `harness/lifecycle_supervisor.py` — `29d94c5418237cd0376b7918e0608fb78edfac3382806bd2dbc0c806ce38ca7c`
- `harness/orchestration_epilogue.py` — `b3c3308e9c14f844100d1c1f7a992143deee555552c27493f52118b49f98537a`
- `metering/model_token_share_v2.py` — `f5eb4490e13c815b9146d4532fa9399c0181d1e12f485e85948b86f255d84f30`
- `config/orchestration_policy_v2.toml` — `8b87eedbc23c6fbd12b5871e125cb34aea9511bea3a6b20b161e6fb7efc8dd31`

## 精确审查句柄

- hook v2 refill + meter：`hooks/subagent_lifecycle.py:51`、`:80`。
- scoped root + child env：`hooks/sol_tool_gate_router.py:28`、`:62`。
- actuator selection + transaction lock：`harness/refill_consumer_v2.py:70`、`:88`、`:127`、`:133`。
- demand-backed target refill：`harness/refill_controller_v2.py:244`。
- PID/create-token recovery + stable running：`harness/headless_wave.py:105`、`:150`、`:179`。
- previous-attempt/prompt + post-spawn accounting：`harness/dispatch_v2.py:525`、`:549`、`:563`、`:583`。
- v1 post-spawn boundary：`harness/dispatch.py:550`；dedicated release review：`:635`–`:775`。
- budget idempotent reserve/reclaim：`harness/budget_controller.py:223`、`:313`。
- epilogue唯一 actuator 接线：`harness/orchestration_epilogue.py:35`。
- temporary profile attribution：`metering/model_token_share_v2.py:129`、`config/orchestration_policy_v2.toml:44`。

## 机械证据

- orchestration-v2：`423 passed`。
- lifecycle/hook/headless/8765/dispatch/meter 定向：`102 passed, 1 skipped`。
- real headless V4 wave：8/8 stable running，随后全终态。
- real headless K3 verifier wave：8/8 stable running；K3 provider 当前 completion 慢，仍由 supervisor 保持 heartbeat。
- current meter：刷新 OK，5h Sol effective share 0.354326。
- current refill：schema v3，target 48，preferred 36/12；真实 ledger 空时 pending=deficit=0。

Review gate：只报告本轮 B 的可复现 P0/P1。缺少后续安装/WSL ipybox MCP smoke 不属于本轮 B 代码失败。
