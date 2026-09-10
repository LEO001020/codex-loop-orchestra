# 基线恢复终审报告

审计时间：2026-08-14（Asia/Shanghai）
审计类型：只读对比，无写操作，无子 agent
活动树：E:\codex-LOOP\codex-loop-s-f2

## 一、四状态树身份确认

A - codex-loop-s-f2-20260810.tar.gz（原始 ipybox）85条目，干净基线
B - codex-loop-s-f2-fable-audit-20260810.tar.gz（Fable本地环境包）176条目，三层包裹，缺SHA256SUMS
C - codex-loop-s-f2-orchestration-v2-20260810.tar.gz（Fable最终+1）49条目，v2模块交付包
D - archives/final-f2-success-20260810T082053Z 101条目，验证通过，entries=100 bad=0
backup - backup-20260813-012318/ 16文件，内部快照

## 二、当前树 vs backup 差异清单（9个文件变化）

DIFF harness/dispatch_v2.py: +34L | bak=20260812-213013 | cur=20260813-051452 | SUMS-MATCH
DIFF harness/refill_controller_v2.py: +273L | bak=20260813-010806 | cur=20260814-134620 | SUMS-MATCH
DIFF harness/statemachine_v2.py: +86L | bak=20260812-220836 | cur=20260813-052002 | SUMS-MATCH
DIFF harness/layered_gate.py: +5L | bak=20260812-175254 | cur=20260813-145730 | SUMS-MATCH
DIFF harness/orchestration_common.py: +3L | bak=20260812-174646 | cur=20260813-155501 | SUMS-MATCH
DIFF config/loop_config_v2.toml: +1L | bak=20260813-000128 | cur=20260813-155445 | SUMS-MATCH
DIFF config/refill_policy.toml: +2L | bak=20260813-000128 | cur=20260813-155445 | SUMS-MATCH
DIFF metering/model_token_share_v2.py: +22L | bak=20260812-184647 | cur=20260813-145730 | SUMS-MATCH
DIFF config/orchestration_policy_v2.toml: 0L delta | bak=20260812-184647 | cur=20260814-165626 | ** SUMS-MISMATCH **

## 三、P0缺陷：SHA256SUMS 失锁

路径：E:\codex-LOOP\codex-loop-s-f2\config\orchestration_policy_v2.toml
- SHA256SUMS记录（行 config/orchestration_policy_v2.toml）：49b1dfc8dde67bee0ea996f0aabef71d34b3e3bc9ee363aed375725eb33c3e1a
- 实际哈希：7e181701a768b755fb51a4e34f629d13f7078c55f9804e85da44fd3aea829abb
- SHA256SUMS mtime：20260814-134715；文件 mtime：20260814-165626 → 文件在SUMS之后被写入

变更内容（仅2行）：
  - k3_model = "weiwu-k3/kimi-k3"   →  "weiwu/aws.claude-sonnet-4.6"
  - k3_reasoning = "max"             →  "ultra"

等级：P0（policy控制面，与AGENTS.md强制 weiwu-k3/kimi-k3 max 冲突，哈希失锁）

## 四、P1观察：refill_controller_v2.py +273行

新增 _process_start_token(pid) 与 _process_generation_alive(pid, expected)
（Windows ctypes + Linux /proc/pid/stat PID世代验证）
已纳入SHA256SUMS，SUMS-MATCH，正向演进，不触发恢复阈值。等级：P1观察

## 五、可恢复前像 / 中间态 / 禁止整包覆盖边界

可恢复前像：
- config/orchestration_policy_v2.toml：恢复为backup版（weiwu-k3/kimi-k3 / max）
  backup源：E:\codex-LOOP\codex-loop-s-f2\backup-20260813-012318\config\orchestration_policy_v2.toml
  同步更新SHA256SUMS对应行为：49b1dfc8dde67bee0ea996f0aabef71d34b3e3bc9ee363aed375725eb33c3e1a

中间态（不应回滚的正向演进）：
- harness/dispatch_v2.py、refill_controller_v2.py、statemachine_v2.py
- harness/layered_gate.py、orchestration_common.py
- config/loop_config_v2.toml、refill_policy.toml
- metering/model_token_share_v2.py

禁止整包覆盖：
- 禁止用D（final-f2-success 20260810T082053Z）覆盖：落后4天，会回滚所有正向演进
- 禁止用B（Fable本地环境包）覆盖：三层包裹，缺SHA256SUMS，含audit数据
- 禁止用C（Fable最终+1）覆盖：仅49条目交付包，非完整树

## 六、最小修复边界

必须执行（P0）：
1. config/orchestration_policy_v2.toml 第31-32行改回 weiwu-k3/kimi-k3 / max
   或明确授权当前值并同步 SHA256SUMS
2. 同步更新 SHA256SUMS 中 config/orchestration_policy_v2.toml 对应行

无需执行：
- refill_controller_v2.py PID世代保护保留，不回滚

## 七、终审裁定

REDO — P0

config/orchestration_policy_v2.toml:31-32（k3_model/k3_reasoning）
在20260814-165626被写入新值（weiwu/aws.claude-sonnet-4.6 ultra），
但SHA256SUMS（mtime 20260814-134715）未同步，完整性校验失锁；
同时与AGENTS.md强制 weiwu-k3/kimi-k3 max verifier路由规则冲突。
最小修复：仅修改该文件2行+同步SHA256SUMS 1行，禁止整包覆盖。
