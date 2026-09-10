# L2 验证报告：首屏传播终审

## 验证范围
- 目标仓库：E:\codex-LOOP\github\codex-loop
- 审查对象：README.md（189 行）、README.zh-CN.md（190 行），只读
- 判据：普通用户 20 秒内理解 ①产品 ②持续高并发差异 ③控制循环 ④安装入口

## 直接证据（逐项）
1. 产品一句话定位
   - README.md:6 "Codex already has subagents. LOOP makes them work as one controlled, continuously running engineering team."
   - README.md:17 段落开头 "In 20 seconds:"，与判据直接对应
   - README.zh-CN.md:6 / :17 "20 秒看懂："，同构
2. 持续高并发差异
   - README.md:25 "## High concurrency that stays high"；:27 明确对比 launch concurrency vs sustained concurrency + 补位机制
   - README.zh-CN.md:25/:27 同构（"启动并发" vs "持续并发"）
3. 控制循环
   - README.md:31 "## The control loop is the product"；:44 一句话总结 "LLMs handle judgment; code handles state; independent models handle review; humans handle release."
   - README.zh-CN.md:31/:44-46 同构
4. 安装入口
   - README.md:19 加粗 "Install:"，推荐 AGENT_INSTALL.md 或跳转手动命令；:52-63 Quickstart 含可直接粘贴的提示词；:102-138 手动命令
   - README.zh-CN.md:19/:54-64/:103-138 同构

## 机械核验（全部通过，直接证据）
- 全部链接目标存在：AGENT_INSTALL.md、INSTALL.md、INSTALL.zh-CN.md、LICENSE、CONTRIBUTING.md、THIRD_PARTY_NOTICES.md、SECURITY.md、VERSIONS.lock、install.sh/uninstall.sh、launchers/*.ps1、harness/model_profile.py
- 全部图片资产存在：docs/assets/dashboard.en.png、dashboard.png、architecture-overview.en.svg、architecture-overview.zh-CN.svg
- 锚点 slug 与标题一致（GitHub 规则：去标点、空格转连字符、CJK 保留）：
  #quickstart/#install-pause-and-restore/#from-codex-primitives-to-a-controlled-system、
  #快速开始/#安装暂停与还原/#从-codex-原生能力到受控工程系统
- 数字口径与治理文件一致：20/80/50/≤25% 在 README.md:90-94、README.zh-CN.md:91-95 与全局工作协议表述一致（推论，依据为本次注入上下文逐字对照）
- zh-CN 首页指向的 AGENT_INSTALL.md 为英中双语文件（首屏实测），中文用户安装入口无语言断层

## 结论
P0：无。P1：无。判定 PASS。

## 证据分级
- 直接证据：上述行号内容、文件存在性、锚点对照均来自本次会话实测
- 推论：数字口径一致性基于注入上下文对照，未逐文件重算
- 未知：GitHub 远程渲染效果（徽章、remote repo 名）无法离线验证，但不影响四项目标判据
