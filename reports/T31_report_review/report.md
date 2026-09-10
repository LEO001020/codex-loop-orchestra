# T31 Stage3 最终报告对抗审计 — report.md

- 审计对象：`outputs/HONOR_VER_AN10_STAGE3_FINAL_REPORT_20260823.md`（2026-08-23 23:36:29 终版，178 行）
- 审计方式：只读文件核验；未使用 ADB；未派生子代理；未修改任何被审文件
- 证据源：E03_execute journal.jsonl / ledger.tsv、p1_preboot、p1_postboot、waveA_pre、waveA_postboot、p2_pre_wave_a、p5_p6、p7_p8、reapply_reduced、T01/T03/T04/T05/T07/T10/T14/T16/T17/T19/T21/T22/T24b/T26/T27/T28/T29/T30/T32/T33、GPT 包（02 matrix / 03 drift / scripts/reconcile_after_ota.py）、Stage2 FULL_AUDIT_REPORT

## 总体结论

报告核心执行陈述全部获得 journal/ledger 及 T 任务证据支持。四个目标缺陷类别中：
**过度声称 1 条实质命中（F1）+ 1 条未证实措辞（F2）；未完成写成完成 0 条；GPT 包原样执行 0 条；Stage3 侧错误 whitelist 检测 0 条**（Stage2 BROKEN rollback 的 9 条 whitelist + 缺陷被报告正确识别、已修复并归档，白名单检测口径 `user,{pkg},` 精确匹配在 T33 中得到执行验证）。

## PASS 项

1. Device/build 170→175 OTA（journal 23:10:34 reboot→23:12:20 boot_complete 175、ota_detected fingerprint；p1_preboot props 170 fingerprint）— 报告表格准确。
2. P1 重启前状态：10 HONOR enabled=3 + 29 Reduced ignore + 核心包 enabled=0（p1_preboot package_enabled/appops_reduced 逐项相符）。
3. OTA 后：10 HONOR + honor.browser 仍 disabled-user；29 Reduced 全部回到 allow；xhs/bilibili user WL 行被系统移除、alipay 仍在（p1_postboot summary/appops_reduced/deviceidle_whitelist 前后 diff 相符）。
4. 电话/IMS：卡1 CS/PS IN_SERVICE、NR n78、IMS PDN CONNECTED+P-CSCF、飞行模式 0、UEInfoCheck 保持 disabled（T05 03_registry/09_ims/08_airplane + p1_postboot）。
5. 第一次回放 29/29 allow→ignore（journal reapply_reduced_done ok=29、ledger 29 行 before=allow after=ignore，含 360）。
6. PRE Perfetto：/data/local/tmp errno 13 permission denied → /data/misc/perfetto-configs 成功；轨迹 43,653,269 bytes（perfetto_run1.txt 原文 + pull2.txt + 本地文件大小一致）。
7. PRE gfxinfo 数字逐项一致：launcher 381/jank 12/3.15%/p50 7/p90 18/p99 81/missed 12；SystemUI 166/15/9.04%/8/23/61/15（gfxinfo framestats 文件原文）。
8. Wave A 独立审计成立：GPT 要禁 4 包只执行 1 包。hiview SKIP（T17/T21：*PERS* UID1000、persistent、oom -800、被 system/bt/nfc/powergenie 绑定；flags 无 PERSISTENT 属 ROM 侧授予）；hiviewpush SKIP（同 uid.system、无独立阻断）；magazine SKIP（175 进程 8616 + LockScreenCarouselProvider；170 静态 ImageWallpaper=T16）；phoneservice DISABLE（无进程/无服务/非前台，T21+T24b），reboot 后仍 3（journal waveA_postboot_ok + waveB_persist）。
9. Wave B：18 候选 KEEP 14 / DISABLE 4 与 T22 final_matrix 一致；ledger 4 行 pm_disable_user before=0 after=3 rc=0；FloatTasks 共享 android.uid.system（wave_flags）+ 失败单包恢复逻辑（waveA_post_and_waveB.py）属实；二次 reboot 后 4 包仍 3、核心包仍 0（waveB_persist.json）。
10. 二次 reboot 把 28 个 Stage2 Reduced 冲回 allow（reapply_results.json 28 行 before=allow），二次回放 28/28 ignore、360 No UID 预期 FAIL（journal reapply_fail + ledger rc=255）— 报告"更强发现"与 GPT 03_drift "正常替换更新不丢 AppOps" 的反驳成立。
11. NMB3：chatgpt/aweme/taptap 无 FGS 不在 WL（T04）；B站=com.bilibili.app.in 非 tv.danmaku.bili、T14 前台+PAUSED 会话、T26 Dozing 无前台无会话不在 WL（T14/T26）；5/5 ignore 写入（ledger 23:30:51-53）；T33 三趟复核五包 ignore 且无 user,{pkg}, 命中；alipay/health/parentcontrol 仍在 WL 未动。
12. 360 卸载：Success/gone/user128 未装（qihoo_uninstall.json + T07）；非 DeviceAdmin/a11y/NLS（T07）；installer=com.hihonor.appmarket（Stage2 FULL_AUDIT L375）；get-installer 子命令本机不存在（journal "Unknown command: get-installer"）。
13. POST 轨迹 16,980,752 bytes；首次 gfxinfo 全 0 帧作废；唤醒重跑 launcher 473/2.54%/9/12/42/missed12、SystemUI 99/3.03%/12/16/69/missed3（p7_p8 framestats_awake 原文）；因果表述已限定"不是实验室级 A/B"，无过度归因。
14. ART：未 compile；launcher/systemui/微信 speed-profile（boot-after-ota/prebuilt/ab-ota）、HonorSuite verify 无 profile（art.json + pm_art_dump 原文）。
15. freeze 文件：16 disabled / 33 reduced（28+5，360 已排除）/ baseline_packages 469 / 175 fingerprint（HONOR_DESIRED_STATE.stage3.20260823.json 实测计数）。
16. reconcile dry-run：第一次 freeze 后报 28 条 appops_ignore（reconcile_stdout.txt 恰 28 条），二次回放后 []（reconcile_after_reapply stdout []）— 报告解释（reboot 冲掉 ignore 的现场证据）成立。
17. 回滚脚本：CORRECTED 零 whitelist +（29 appops default + 6 pm default-state）；BROKEN 恰 9 条 whitelist +；Stage3 rollback 零 whitelist +（本次直接复核现 1421B 版：5 pm enable + 5 NMB3 appops default）— 与 T01/T30 一致。
18. 白名单检测口径：报告 L39 "必须匹配 user,{pkg},，不能把任意 user, 行当命中" 与 T01（in_user_wl() 缺陷定位）、T33 判定口径一致 — Stage2 错误已修复，Stage3 未复现。
19. 保护集：T29 账本审计（34 行写操作无 YOYO/PC/powergenie/iaware/phone/systemui/launcher、全文 0 whitelist）+ T28 终版 81/81 protected 包 enabled=0；ledger 全量 action 仅 appops_set_ignore/pm_disable_user/pm_uninstall_user0，无 whitelist 写。
20. 诚实性：报告对 NMB3/360/POST/ART/freeze 先标未完成（ADB 离线版），设备 23:30 恢复后全部落地并更新为已完成 — 与 journal 时间线（waveB_reboot_issued 23:22:57 → waveB_persist 23:30:51 → 冻结 23:31:46）吻合，无"未完成写成完成"。

## FAIL 项

- **F1（实质过度声称）**：报告 L144 "脚本契约：versionCode 变化 fail-closed" 不准确。reconcile_after_ota.py L132-144 实际 gate 为 `build_changed AND changed AND not force_changed_system`；T19 §6.2/§6.4 已证三条 fail-open 路径（未 freeze 时种子 baseline_version_code 全 null、fingerprint 不变而 versionCode 变、versionCode 解析失败均直接提议 disable-user）。stage3 freeze 已补基线（null_vc=0），但"单包更新而 fingerprint 不变"路径仍 fail-open。应改为条件表述："已 freeze 且 build fingerprint 变化时，versionCode 变化 fail-closed（可 --force-changed-system 覆盖）；其余路径仍直接重放"。
- **F2（轻微，未证实措辞）**：报告 L94 "二次 reboot 后、非前台时执行" — p5_p6.py NMB3 段无任何前台/焦点检查即写 appops；最近旁证为 T26（23:14-23:16 Dozing/AOD、目标包无前台）与 T24b（恢复后前台为 honorsuite/AOD），执行时刻 23:30:52 未采前台快照。结论方向可信，但该限定词无执行时刻证据，建议改为引用 T26/T24b 或删除该限定。

## 观察项（非 FAIL）

- "设备把挂起的 175 OTA 吃掉"是机制推断：170→175 过渡可观测（journal），"挂起 OTA"本身无直接证据，宜标为推断。
- T30 核验的是 866B 版 stage3_rollback.ps1；现行 1421B 版（+5 条 NMB3 appops default）本次直接复核仍 0 whitelist +，报告结论成立但归属快照不同。
- 报告 L88 的 T28 表述（45 YOYO + 12 PC 已扫、24 PC 掉线未扫）是 23:36:29 写入时的中间态；T28 终版 23:37:26 已补全 81/81 OK，与报告无矛盾。

## 审计员声明

本包全程只读：仅读取被审文件与证据文件；未执行任何 ADB/设备命令；未修改被审文件；未派生子代理。自报 PASS 不代替机械复验。
