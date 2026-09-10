# A06R 实际脚本发布门禁核验报告（L2 Verifier，独立复核 A04R 生成物）

- 结论：READY（pass）
- 角色：L2 跨源核验；未执行任何脚本，未修改设备/系统/被审文件状态；仅新增本报告。
- 日期：2026-08-23（Asia/Shanghai）

## 对照 A02R 批准清单

A02R/APPROVED_ACTIONS.tsv 共 5 项：EV-001 设备门（只读断言）、EV-002 user0 全量只读普查、
EV-003 gfxinfo framestats pre/post、EV-005 波前/波后复读+logcat 风暴筛查、PR-001 Windows 执行工作区。

## 验收条件逐项核验

1) 默认 DryRun：execute_stage2_safe.ps1:285 `if (-not $Execute) { Show-Plan; exit 0 }`，
   位于任何 New-Item/ADB/文件写之前；Show-Plan 仅 Write-Host（:230-277），Resolve-Adb 仅
   Test-Path/Get-Command（:92-104）。rollback.ps1:82-86 默认仅打印计划。
2) -Execute 恰好且仅 5 项、零手机/Windows 状态写：设备命令表全部只读
   （普查 :46-65、候选包 dumpsys package :311-317、快照 :82-86、gfx :322-329、
   门 :167-189、风暴 :206-228）；Windows 写仅限 outputs/HONOR_STAGE2_<ts>/ 工作区
   （:288-289、:383-391），属 PR-001 批准范围。
3) fail-closed：门要求恰好一台 state=device 的 authorized 设备，0/多台即 throw→exit 2
   （:170-176）；brand=HONOR/model=VER-AN10/current user=0 任一不匹配即 throw（:185-187）；
   门先于一切后续手机命令（:293-299），EV-005 前再次在线断言（:334-339）。
4) 无阻断动作、无间接调用：18 类禁用模式扫描 0 命中（唯一 Remove-Item 为
   rollback.ps1:77 工作区清理，经绝对路径前缀校验 :60-64 与台账 WRITE 行守卫→exit 3 :65-71）；
   仅有的外部进程调用为 `& $script:Adb @(固定数组)`（execute:116），无 Invoke-Expression/iex/
   Start-Process/点源/字符串拼命令；logcat 仅 `-d -t`（:209），无 `-c`；无 settings put/pm grant/
   force-stop/reboot/perfetto/compile/netpolicy add|remove/appops set。
5) actions.jsonl：5 行全部 TEMPLATE、result=PENDING、时间/serial 为占位符（无预写成功）；
   运行时 journal 独立写入工作区。rollback.ps1 默认 DryRun，-Execute 仅删除工作区生成物。
6) 无越权路径：额外的 `pm list users`/`pm list packages -d --user 0` 为 EV-002“全量只读普查”
   范围内的纯只读查询，不改变任何状态。

## 机械复核（本人独立重放，未执行脚本）

- PowerShell AST：execute 0 语法错误/3671 tokens；rollback 0/448；与 A04R 自报一致。
- 禁用词/写命令扫描：两脚本 0 命中（详见上 4)）。
- actions.jsonl：5/5 可被 ConvertFrom-Json 解析，全部 TEMPLATE/PENDING。
- A04R 目录仅含 .keep + 4 个交付物，无其他可执行工件。

## 非阻塞偏差（A04R 已披露，不构成 BLOCK）

- Bash rollback 未交付：PR-001“任何手机写命令前生成 PowerShell+Bash rollback”前提依赖写命令，
  本包零写命令，前提恒真；已交付 PS1 且台账守卫未来 WRITE 行（rollback.ps1:65-71）。
- 台账在只读采集完成后写入：因无任何写命令，时序前置恒真；enabled numeric 内联、appops/
  whitelist/netpolicy 值以快照文件+SEE_EVIDENCE 指针捕获。
- EV-003 未实现 reset 分支：reset 属设备状态写，取“记录时间点”分支，保守且安全。
- 观察项（不影响门禁）：同秒重跑共享时间戳会复用工作区目录；rollback 不带 -WorkspaceName
  时默认选最新 HONOR_STAGE2_* 工作区。