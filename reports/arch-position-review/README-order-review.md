# 架构位置复核（只读）

仓库：E:\codex-LOOP\github\codex-loop
目标：评估首页 README.md 中 架构图 / IPybox 段 / Quickstart 三者的顺序，给最简顺序与删减意见。
复核时间戳：README.md 与 README.zh-CN.md 均于 2026-08-24 05:14:03 被并发修改（git status 显示两者为 M，未提交）；本复核以该时刻后读取的当前版本为准。

## 直接证据（文件与行号，当前状态）

- 现章节顺序：Quickstart(README.md:25) → What you get(:46) → How LOOP works(:57，架构图在 :59) → IPybox 段(:63-67) → NOTE 声明(:69-70) → Installation details(:72) → Configuration authority(:132) → Control loop(:167) → Repository layout(:179) → Verification(:195) → Security(:206) → History(:217)。
- 架构图：README.md:59，位于 How LOOP works 小节首行；中文版对应 README.zh-CN.md:57。
- IPybox 段标题已由 "Why LOOP includes a persistent Python plane" 改为 "A persistent Python workbench for the harness"；当前文本不含 Prime Agent / RHAE / ARC-AGI 引用（rg 全仓 README 无匹配）。
- 重复块 A：Windows clone+activate 命令在 README.md:38-42 与 README.md:96-100 逐字重复。
- 重复块 B：agent 安装提示词在 README.md:29-34 与 README.md:76-82 各一份（措辞略异）。
- 重复块 C：IPybox 卖点在 What-you-get 表 README.md:53 已有一句，README.md:63-67 再用两段展开（机制细节：lazy server、sandbox policy、routing rules、dataframe 持久化）。
- 重复块 D："Restart Codex Desktop and create a new task" 见于 README.md:44 与 :102。
- 顶栏锚点 README.md:13 现为 #quickstart / #how-loop-works，与标题匹配（首次读取时为 #start-in-30-seconds，已被并发修改修复）。
- 中英文两版章节顺序一致（README.zh-CN.md:24 快速开始 → :55 LOOP 如何工作 → :61 持久 Python 工作台）。

## 评估结论（相对顺序）

- Quickstart 最前（:25）：符合高星仓库惯例——可执行入口在 hero/截图之后尽早出现。无需移动。
- 架构图次之（:59，位于 How-it-works 内）：符合惯例。无需移动。
- IPybox 段最后（:63，架构图之后、Installation 之前）：相对位置正确（概念图先于机制细节），但它是三者中唯一可大幅删减的部分，且夹在架构图与安装说明之间拉长了“读图→装机”路径。
- 三者的最小顺序即现行顺序，无需重排：Quickstart → 架构图 → IPybox 段。

## 删减意见（最小化）

1. 只保留一处 agent 安装提示词：删除 README.md:76-82（Installation details 内），Quickstart 保留 :29-34 的短版即可；或反向保留一处。
2. 只保留一处 Windows clone+activate 命令：删除 README.md:38-42（Quickstart 内）或 :96-100，二者逐字重复，保留 Installation details 内的一份、Quickstart 用锚点链接。
3. IPybox 段压缩为 1 句（呼应 README.md:53 特性表）：删除机制细节（:65 的 lazy server/sandbox/routing 与 :67 的 dataframe 持久化描述）；若需保留设计来源致谢，仅留 1 行带链接（当前版本已无 Prime Agent 引用，如需致谢属决策项）。
4. 可选：NOTE 声明（:69-70）从 How-it-works 移至 Security and limitations（:206）或 History（:217），避免打断 How-it-works → Installation 的阅读流。
5. 可选：删除 README.md:44 或 :102 中重复的 "Restart Codex Desktop" 提示，只留一处。

## 证据分类

- 直接证据：上述所有行号引用、重复块、中英文一致性、锚点现状（当次会话读取）。
- 推论：高星仓库首页惯例（hero/截图→Quickstart→特性→How-it-works/架构→安装详情→配置→安全/许可）为通用 OSS 实践归纳，非本仓库书面规范；“IPybox 段拉长装机路径”为阅读流推断。
- 未知：5:14 的并发编辑是否已完成；维护者是否希望保留 Prime Agent 设计来源致谢（当前已被删除，docs/ 无 .md 文件可承载）。
