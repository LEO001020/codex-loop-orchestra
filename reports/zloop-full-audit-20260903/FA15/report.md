# 安全脱敏审计报告 (Packet: FA15)

## 概述
本审计针对 redact.py、paths.py 和 worker_env.py 进行静态分析，重点关注秘密识别能力、路径边界防护和 subprocess 环境构建的一致性。

## 问题汇总

### redact.py (秘密识别与脱敏)
1. **模式识别边界缺陷 (Line 21-25):**
   - 描述：kv (键值对) 脱敏 regex 正则中对值的长度限制为 {6,}。
   - 漏洞点：短位秘密（<6 字符）无法被 kv 模式覆盖。
   - 前提：攻击者利用弱凭证或短 token，且其名称未触发现有 Segments 集。

2. **Segements 集局限性 (Line 45):**
   - 描述：_SECRET_KEY_SEGMENTS 和 adjacent pairs 完全依赖硬编码列表。
   - 缺陷：未覆盖所有可能的敏感词边界，新增敏感策略未同步更新该硬编码集。

### paths.py (数据路径边界)
1. **路径遍历风险 (Line 23):**
   - 描述：project_dir(project_id) 直接通过 os.path.join 路径连接。
   - 漏洞点：若 project_id 输入包含 ../ 且未经规范化处理，可能允许访问 zloop_data_root 之上的受限目录。
   - 前提：project_id 来自用户可控的输入。

2. **环境变量可控依赖 (Line 14):**
   - 描述：zloop_data_root 高度依赖 ZLOOP_DATA 环境变量。
   - 缺陷：若环境初始化前 ZLOOP_DATA 被篡改，所有登记的 registry.json 可能被重定向。

### worker_env.py ( subprocess 环境构建)
1. **安全依赖倒置 (Line 38):**
   - 描述：build_worker_env 依赖 redact.key_is_secret 进行 credential 拒绝。
   - 缺陷：安全控制逻辑耦合紧密，若 redact 的模式识别未能覆盖特定格式的秘密，该校验逻辑失效。

## 结论
现有方案整体采用基于 Allowlist 和递归正则表达式脱敏的主动安全防御。主要风险点在于路径的规范化和脱敏规则的覆盖范围。

报告日期：2026-09-03
