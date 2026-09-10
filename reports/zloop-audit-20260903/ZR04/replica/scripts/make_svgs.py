"""Generate full set of architecture SVGs for zcode-loop-orchestra."""
import re
from pathlib import Path

DEST = Path("E:/zcode/zloop-gen8/docs/assets")
DEST.mkdir(parents=True, exist_ok=True)

SIMP_ZH = """<svg width="1600" height="1220" id="architecture" class="theme-light" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 1220" role="img" aria-labelledby="title desc">
  <title id="title">ZCode LOOP Orchestra 极简系统架构</title>
  <desc id="desc">采用发布会视觉风格的 ZCode-ZLoop 原生多智能体控制回路，展示规划前置、物理并发执行、物化回滚与异构审计。</desc>
  <defs>
    <linearGradient id="pageGlow" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="var(--glow-a)"/>
      <stop offset="1" stop-color="var(--glow-b)"/>
    </linearGradient>
    <filter id="shadowSoft" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="8" stdDeviation="14" flood-color="var(--shadow)" flood-opacity="0.10"/>
    </filter>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--line-strong)"/>
    </marker>
    <marker id="arrowBlue" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--blue)"/>
    </marker>
    <marker id="arrowGreen" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--green)"/>
    </marker>
    <marker id="arrowRed" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--red)"/>
    </marker>
  </defs>
  <style>
    #architecture{
      --bg:#f7f7f5;--surface:#ffffff;--surface-2:#f0f1ef;--ink:#151619;--muted:#666a73;--faint:#989da6;
      --line:#d9dce1;--line-strong:#8f98a5;--shadow:#7a8492;
      --blue:#3e7df2;--blue-soft:#dfeafe;--blue-stroke:#b6c9ea;
      --green:#3f8a67;--green-soft:#d7e8de;--green-stroke:#b7cfbf;
      --violet:#8d6cb1;--violet-soft:#e6ddf1;--violet-stroke:#cab8de;
      --amber:#9b7346;--amber-soft:#ecdfd1;--amber-stroke:#d7bda1;
      --red:#c24b4b;--red-soft:#fbebeb;--red-stroke:#e8b5b5;
      --entry-fill:#dce8fb;--entry-stroke:#b8cae7;
      --control-fill:#ecdfd1;--control-stroke:#d6bca3;
      --execute-fill:#d9e9df;--execute-stroke:#b7cfbf;
      --verify-fill:#e7def0;--verify-stroke:#cbb8df;
      --glow-a:#ffffff;--glow-b:#eef1f5;
      font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text",Inter,"Microsoft YaHei","Noto Sans CJK SC","Segoe UI",Roboto,Arial,sans-serif;
    }
    @media (prefers-color-scheme: dark) {
      #architecture{
        --bg:#08090b;--surface:#111317;--surface-2:#171a1f;--ink:#f6f7f8;--muted:#a8aeb8;--faint:#737a86;
        --line:#2a2f37;--line-strong:#737e8e;--shadow:#000000;
        --blue:#86b1ea;--blue-soft:#1a2434;--blue-stroke:#425a7f;
        --green:#7db995;--green-soft:#1b2b22;--green-stroke:#3b644b;
        --violet:#b89ad6;--violet-soft:#2a2130;--violet-stroke:#664f79;
        --amber:#d0ae82;--amber-soft:#2d241b;--amber-stroke:#6a543e;
        --red:#e27a7a;--red-soft:#331d1d;--red-stroke:#844141;
        --entry-fill:#1e2939;--entry-stroke:#445c82;
        --control-fill:#31261d;--control-stroke:#73583d;
        --execute-fill:#1d2b23;--execute-stroke:#426a52;
        --verify-fill:#2b2231;--verify-stroke:#6a537a;
        --glow-a:#0d1118;--glow-b:#090a0c;
      }
    }
    .page{fill:var(--bg)} .decor{fill:url(#pageGlow)}
    .title{fill:var(--ink);font-size:38px;font-weight:710;letter-spacing:-1.3px}
    .subtitle{fill:var(--muted);font-size:16px;font-weight:430;letter-spacing:-0.15px}
    .eyebrow{fill:var(--faint);font-size:12px;font-weight:700;letter-spacing:1.8px}
    .stage{fill:var(--faint);font-size:13.5px;font-weight:760;letter-spacing:1.5px}
    .node-title{fill:var(--ink);font-size:17.5px;font-weight:690;letter-spacing:-0.3px}
    .node-sub{fill:var(--muted);font-size:12px;font-weight:460;line-height:1.4}
    .node-badge{font-size:11px;font-weight:650}
    .box{fill:var(--surface);stroke:var(--line);stroke-width:1}
    .blue-card{fill:var(--entry-fill);stroke:var(--entry-stroke);stroke-width:1.2}
    .amber-card{fill:var(--control-fill);stroke:var(--control-stroke);stroke-width:1.2}
    .green-card{fill:var(--execute-fill);stroke:var(--execute-stroke);stroke-width:1.2}
    .violet-card{fill:var(--verify-fill);stroke:var(--violet-stroke);stroke-width:1.2}
    .red-card{fill:var(--red-soft);stroke:var(--red-stroke);stroke-width:1.2}
    .connector{fill:none;stroke:var(--line-strong);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arrow)}
    .connector-blue{fill:none;stroke:var(--blue);stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arrowBlue)}
    .connector-green{fill:none;stroke:var(--green);stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arrowGreen)}
    .connector-red{fill:none;stroke:var(--red);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:5 5;marker-end:url(#arrowRed)}
    .raised{filter:url(#shadowSoft)}
    .foot{fill:var(--faint);font-size:11px;font-weight:500}
  </style>

  <rect class="page" width="1600" height="1220"/>
  <circle class="decor" cx="1480" cy="20" r="280" opacity=".65"/>
  <circle class="decor" cx="110" cy="1160" r="240" opacity=".5"/>

  <!-- Header -->
  <text class="eyebrow" x="72" y="52">系统架构 · 极简拓扑</text>
  <text class="title" x="72" y="94">ZCode-ZLoop 原生三重审计与并发架构</text>
  <text class="subtitle" x="72" y="124">规划前置 ChatGPT 反驳 · 8–15 异步工作树物理并发 · Staging 失败原子回滚 · 异构 GLM-5.3 独立门禁</text>

  <!-- Metrics Bar -->
  <g class="raised">
    <rect class="box" x="72" y="152" rx="16" ry="16" width="330" height="76"/>
    <text fill="var(--blue)" font-size="28" font-weight="750" x="96" y="195">8–15</text>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="175" y="184">物理并发 Workers</text>
    <text fill="var(--muted)" font-size="11.5" x="175" y="206">JIT 线程池 + 非阻塞 poll()</text>
    <rect x="72" y="152" width="4" height="76" rx="2" fill="var(--blue)"/>
  </g>
  <g class="raised">
    <rect class="box" x="424" y="152" rx="16" ry="16" width="330" height="76"/>
    <text fill="var(--green)" font-size="28" font-weight="750" x="448" y="195">-53.4%</text>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="556" y="184">首轮输入 Token 削减</text>
    <text fill="var(--muted)" font-size="11.5" x="556" y="206">物理剥离 14 个 MCP/Skills</text>
    <rect x="424" y="152" width="4" height="76" rx="2" fill="var(--green)"/>
  </g>
  <g class="raised">
    <rect class="box" x="776" y="152" rx="16" ry="16" width="330" height="76"/>
    <text fill="var(--violet)" font-size="28" font-weight="750" x="800" y="195">100%</text>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="888" y="184">异构模型隔离直通</text>
    <text fill="var(--muted)" font-size="11.5" x="888" y="206">Gemini 执行 × GLM-5.3 审计</text>
    <rect x="776" y="152" width="4" height="76" rx="2" fill="var(--violet)"/>
  </g>
  <g class="raised">
    <rect class="box" x="1128" y="152" rx="16" ry="16" width="400" height="76"/>
    <text fill="var(--amber)" font-size="28" font-weight="750" x="1152" y="195">293</text>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="1215" y="184">自动化测试全绿</text>
    <text fill="var(--muted)" font-size="11.5" x="1215" y="206">I1–I44 架构不变量物理闭环</text>
    <rect x="1128" y="152" width="4" height="76" rx="2" fill="var(--amber)"/>
  </g>

  <!-- Flow Col 1: 任务入口与根协调 -->
  <g class="raised">
    <rect class="box" x="72" y="260" rx="20" ry="20" width="310" height="880"/>
    <text class="stage" x="96" y="295">STAGE 1 · 规划前置</text>

    <!-- User / Prompt -->
    <rect class="blue-card" x="96" y="320" rx="14" ry="14" width="262" height="110"/>
    <text class="node-title" x="116" y="352">用户需求输入</text>
    <text class="node-sub" x="116" y="376">下发工程任务 / 宏观重构目标</text>
    <text class="node-sub" x="116" y="396">切片写入 packets.json</text>

    <!-- Root Agent -->
    <rect class="box" x="96" y="460" rx="14" ry="14" width="262" height="150"/>
    <text class="node-title" x="116" y="492">根会话 (Root Agent)</text>
    <text fill="var(--blue)" class="node-badge" x="116" y="514">Gemini 3.8 Flash (Provider 3f0e0bfa)</text>
    <text class="node-sub" x="116" y="540">唯一认知与编排权威</text>
    <text class="node-sub" x="116" y="562">轻量 S1 瘦身运行态 (12.5k tok)</text>
    <text class="node-sub" x="116" y="584">执行 zloop c2c prepare --role plan</text>

    <!-- Gate 1: C2C-P -->
    <rect class="violet-card" x="96" y="640" rx="14" ry="14" width="262" height="160"/>
    <text class="node-title" x="116" y="672">审级 1 · 计划审计 (C2C-P)</text>
    <text fill="var(--violet)" class="node-badge" x="116" y="694">ChatGPT 网页端 / 跨家族新线程</text>
    <text class="node-sub" x="116" y="720">信息量最小，决策价值最高</text>
    <text class="node-sub" x="116" y="742">反驳架构漏洞、补齐 Web 事实</text>
    <text class="node-sub" x="116" y="764">通过 -> record c2c_recorded (plan)</text>

    <!-- Wave Start Gate -->
    <rect class="amber-card" x="96" y="830" rx="14" ry="14" width="262" height="130"/>
    <text class="node-title" x="116" y="862">计划门禁 (Wave Gate)</text>
    <text class="node-sub" x="116" y="888">HIGH/CRITICAL 强制要求</text>
    <text class="node-sub" x="116" y="910">无 plan 记录 -> 阻断在 PLANNING</text>
    <text class="node-sub" x="116" y="932">放行 -> 允许发射并发 Workers</text>

    <rect class="box" x="96" y="990" rx="14" ry="14" width="262" height="110"/>
    <text class="node-title" x="116" y="1022">控制底座 (Control S)</text>
    <text class="node-sub" x="116" y="1046">SQLite DELETE+EXTRA 崩溃一致</text>
    <text class="node-sub" x="116" y="1066">busy_timeout=30s · CAS Token 守卫</text>
  </g>

  <!-- Flow Col 2: 执行层 (8-15 Workers) -->
  <g class="raised">
    <rect class="box" x="424" y="260" rx="20" ry="20" width="360" height="880"/>
    <text class="stage" x="448" y="295">STAGE 2 · 物理隔离并发执行</text>

    <!-- Supervisor -->
    <rect class="amber-card" x="448" y="320" rx="14" ry="14" width="312" height="110"/>
    <text class="node-title" x="468" y="352">冷调度器 (Supervisor)</text>
    <text class="node-sub" x="468" y="376">单主进程驱动 · 零推理纯物理逻辑</text>
    <text class="node-sub" x="468" y="396">50ms 微弱错峰发射，平滑 API 429</text>

    <!-- Worker Pool -->
    <rect class="box" x="448" y="460" rx="14" ry="14" width="312" height="520"/>
    <text class="node-title" x="468" y="492">8–15 独立工作树 (Worktrees)</text>
    <text class="node-sub" x="468" y="516">JIT 异步线程池派发 · 非阻塞 poll() 轮询</text>

    <!-- Worker Cards -->
    <rect class="green-card" x="468" y="540" rx="10" ry="10" width="272" height="65"/>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="484" y="567">Worker 1 (worktree_s01_p01)</text>
    <text class="node-sub" x="484" y="587">write_scope 严格隔离 · Git 锁重试自愈</text>

    <rect class="green-card" x="468" y="620" rx="10" ry="10" width="272" height="65"/>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="484" y="647">Worker 2 (worktree_s01_p02)</text>
    <text class="node-sub" x="484" y="667">独立临时环境 · 429 指数抖动退避</text>

    <text fill="var(--muted)" font-size="18" font-weight="700" x="590" y="718">···</text>

    <rect class="green-card" x="468" y="740" rx="10" ry="10" width="272" height="65"/>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="484" y="767">Worker 8 (worktree_s01_p08)</text>
    <text class="node-sub" x="484" y="787">产出文件 Delta，提交候选哈希</text>

    <rect class="green-card" x="468" y="820" rx="10" ry="10" width="272" height="65"/>
    <text fill="var(--ink)" font-size="14.5" font-weight="650" x="484" y="847">Worker 15 (worktree_s01_p15)</text>
    <text class="node-sub" x="484" y="867">全量 15 路并发承载 · 绝无单线程卡死</text>

    <rect class="blue-card" x="468" y="900" rx="10" ry="10" width="272" height="60"/>
    <text fill="var(--ink)" font-size="14" font-weight="650" x="484" y="925">子代理 Profile: zloop-worker</text>
    <text class="node-sub" x="484" y="945">纯开发 9 工具 · 零 MCP Schema 负担</text>

    <!-- Output Delta -->
    <rect class="box" x="448" y="1000" rx="14" ry="14" width="312" height="100"/>
    <text class="node-title" x="468" y="1032">Delta 机械提取</text>
    <text class="node-sub" x="468" y="1056">git status --porcelain=v2 -z 严密重构</text>
    <text class="node-sub" x="468" y="1076">空父目录递归向上清理，无幽灵残留</text>
  </g>

  <!-- Flow Col 3: 物化与原子回滚防线 -->
  <g class="raised">
    <rect class="box" x="824" y="260" rx="20" ry="20" width="340" height="880"/>
    <text class="stage" x="848" y="295">STAGE 3 · 物化与防毒化回滚</text>

    <!-- Staging Tree -->
    <rect class="box" x="848" y="320" rx="14" ry="14" width="292" height="120"/>
    <text class="node-title" x="868" y="352">临时暂存区分支 (Staging)</text>
    <text class="node-sub" x="868" y="376">单 Stage 私有 Git 空间 (staging_ws)</text>
    <text class="node-sub" x="868" y="396">物化前记录干净 parent_sha 基线</text>
    <text class="node-sub" x="868" y="416">顺序应用 Workers 产出的代码变更</text>

    <!-- Gate 2: Mechanical Acceptance -->
    <rect class="violet-card" x="848" y="470" rx="14" ry="14" width="292" height="170"/>
    <text class="node-title" x="868" y="502">审级 2 · 机械自动化验收</text>
    <text fill="var(--violet)" class="node-badge" x="868" y="524">pytest / 编译 / 语法与静态检查</text>
    <text class="node-sub" x="868" y="550">机械现实 > 模型自夸 (I38)</text>
    <text class="node-sub" x="868" y="572">严禁模型自己宣称“测试已跑通”</text>
    <text class="node-sub" x="868" y="594">通过 -> 暂存候选 Commit，允许晋升</text>
    <text class="node-sub" x="868" y="616">失败 -> 立即触发硬原子回滚！</text>

    <!-- P0-2 Defense: Atomic Rollback -->
    <rect class="red-card" x="848" y="670" rx="14" ry="14" width="292" height="170"/>
    <text fill="var(--red)" font-size="16.5" font-weight="700" x="868" y="702">防毒化硬重置 (Atomic Rollback)</text>
    <text class="node-sub" x="868" y="728">git reset --hard parent_sha</text>
    <text class="node-sub" x="868" y="750">git clean -fdx 物理彻底擦除</text>
    <text class="node-sub" x="868" y="772">绝不让失败代码驻留在 Staging HEAD</text>
    <text class="node-sub" x="868" y="794">彻底根除多任务“级联雪崩误杀”</text>
    <text class="node-sub" x="868" y="816">S 库事件记录 candidate sha 取证保全</text>

    <rect class="box" x="848" y="870" rx="14" ry="14" width="292" height="110"/>
    <text class="node-title" x="868" y="902">状态物化 (Materialized)</text>
    <text class="node-sub" x="868" y="926">Packet 状态跃迁为 MATERIALIZED</text>
    <text class="node-sub" x="868" y="946">更新 Stage current_snapshot 指针</text>

    <rect class="box" x="848" y="1000" rx="14" ry="14" width="292" height="100"/>
    <text class="node-title" x="868" y="1032">证据归档 (H0/H1)</text>
    <text class="node-sub" x="868" y="1056">不可篡改 NDJSON 日志持续追加</text>
    <text class="node-sub" x="868" y="1076">>4KB 载荷自动溢出至 CAS Blob</text>
  </g>

  <!-- Flow Col 4: 结果审计与晋升合入 -->
  <g class="raised">
    <rect class="box" x="1204" y="260" rx="20" ry="20" width="324" height="880"/>
    <text class="stage" x="1228" y="295">STAGE 4 · 异构审计与主干晋升</text>

    <!-- Gate 3: C2C-A Result Audit -->
    <rect class="violet-card" x="1228" y="320" rx="14" ry="14" width="276" height="240"/>
    <text class="node-title" x="1248" y="352">审级 3 · 结果审计 (C2C-A)</text>
    <text fill="var(--violet)" class="node-badge" x="1248" y="374">异构独立直通 · P-HET1 闭环</text>
    <text class="node-sub" x="1248" y="400">Auditor: GLM-5.3 (Provider af9697f5)</text>
    <text class="node-sub" x="1248" y="422">全限定模型路由，100% 物理隔离</text>
    <text class="node-sub" x="1248" y="444">只读契约：坚决不写文件</text>
    <text class="node-sub" x="1248" y="466">审查介质：只吃 Git Diff 与单测日志</text>
    <text class="node-sub" x="1248" y="488">输出契约：严格结构化 JSON 判决</text>
    <text class="node-sub" x="1248" y="510">或调用 ChatGPT 跨家族终验</text>
    <text class="node-sub" x="1248" y="532">通过 -> record c2c_recorded (result)</text>

    <!-- Stage Promote Gate -->
    <rect class="amber-card" x="1228" y="590" rx="14" ry="14" width="276" height="150"/>
    <text class="node-title" x="1248" y="622">晋升门禁 (Promote Gate)</text>
    <text class="node-sub" x="1248" y="648">角色感知防护 (Role-Aware)</text>
    <text class="node-sub" x="1248" y="670">强制校验 role=result 记录</text>
    <text class="node-sub" x="1248" y="692">plan 角色混充 -> 坚决拦截拒绝！</text>
    <text class="node-sub" x="1248" y="714">result 审查通过 -> 放行晋升</text>

    <!-- Canonical Promotion -->
    <rect class="green-card" x="1228" y="770" rx="14" ry="14" width="276" height="150"/>
    <text class="node-title" x="1248" y="802">主干原子晋升 (CAS ff-only)</text>
    <text class="node-sub" x="1248" y="828">Git physical oracle 验证干净 HEAD</text>
    <text class="node-sub" x="1248" y="850">git merge --ff-only 安全快进</text>
    <text class="node-sub" x="1248" y="872">主干分支推进至全新 Staged SHA</text>
    <text class="node-sub" x="1248" y="894">Stage 状态正式跃迁为 PROMOTED</text>

    <!-- Success Outcome -->
    <rect class="box" x="1228" y="950" rx="14" ry="14" width="276" height="150"/>
    <text class="node-title" x="1248" y="982">高质量代码产出交付</text>
    <text class="node-sub" x="1248" y="1006">零毒化破坏 · 零未授权覆盖</text>
    <text class="node-sub" x="1248" y="1026">经受住大模型反驳、单测、异构双审</text>
    <text class="node-sub" x="1248" y="1046">全生命周期可复现、可审计</text>
    <text fill="var(--green)" font-weight="700" font-size="13" x="1248" y="1076">PRODUCTION GRADE VERIFIED</text>
  </g>

  <!-- Connectors -->
  <path class="connector-blue" d="M 227 430 L 227 460"/>
  <path class="connector-blue" d="M 227 610 L 227 640"/>
  <path class="connector-blue" d="M 227 800 L 227 830"/>
  <path class="connector-green" d="M 358 895 L 448 375"/>

  <path class="connector" d="M 604 430 L 604 460"/>
  <path class="connector" d="M 604 980 L 604 1000"/>
  <path class="connector-green" d="M 760 1050 L 848 380"/>

  <path class="connector" d="M 994 440 L 994 470"/>
  <path class="connector-red" d="M 994 640 L 994 670"/>
  <path class="connector-green" d="M 994 640 L 994 870"/>

  <path class="connector-green" d="M 1140 925 L 1228 440"/>
  <path class="connector" d="M 1366 560 L 1366 590"/>
  <path class="connector-green" d="M 1366 740 L 1366 770"/>
  <path class="connector-green" d="M 1366 920 L 1366 950"/>

  <!-- Footer -->
  <text class="foot" x="72" y="1175">ZCode-ZLoop Gen8 · Native Multi-Agent Orchestration Harness · Dual Heterogeneous AI Verification</text>
  <text class="foot" x="1170" y="1175">Confidential &amp; Verified Architecture · Invariants I1–I44 Enforced</text>
</svg>"""

(DEST / "architecture-simplified.zh-CN.svg").write_text(SIMP_ZH, encoding="utf-8")
print("Saved architecture-simplified.zh-CN.svg")

# English translation mapping
replacements = [
    ("ZCode LOOP Orchestra 极简系统架构", "ZCode LOOP Orchestra Simplified Architecture"),
    ("采用发布会视觉风格的 ZCode-ZLoop 原生多智能体控制回路，展示规划前置、物理并发执行、物化回滚与异构审计。", "Keynote-style architecture of ZCode-ZLoop native multi-agent loop: pre-planning audit, physical concurrency, atomic rollback, and heterogeneous verification."),
    ("系统架构 · 极简拓扑", "SYSTEM ARCHITECTURE · SIMPLIFIED TOPOLOGY"),
    ("ZCode-ZLoop 原生三重审计与并发架构", "ZCode-ZLoop Native Triple-Audit & Concurrency Loop"),
    ("规划前置 ChatGPT 反驳 · 8–15 异步工作树物理并发 · Staging 失败原子回滚 · 异构 GLM-5.3 独立门禁", "Pre-Planning Counter-Plan · 8–15 Async Worktree Concurrency · Atomic Staging Rollback · Independent GLM-5.3 Gate"),
    ("物理并发 Workers", "Physical Concurrent Workers"),
    ("JIT 线程池 + 非阻塞 poll()", "JIT Thread Pool + Non-Blocking poll()"),
    ("首轮输入 Token 削减", "Initial Input Token Reduction"),
    ("物理剥离 14 个 MCP/Skills", "Pruned 14 Unneeded MCP/Skill Schemas"),
    ("异构模型隔离直通", "Isolated Cross-Model Routing"),
    ("Gemini 执行 × GLM-5.3 审计", "Gemini Execution × GLM-5.3 Audit"),
    ("自动化测试全绿", "Automated Tests All Green"),
    ("I1–I44 架构不变量物理闭环", "Invariants I1–I44 Fully Closed"),
    ("STAGE 1 · 规划前置", "STAGE 1 · PLANNING & DISPATCH"),
    ("用户需求输入", "User Requirement Input"),
    ("下发工程任务 / 宏观重构目标", "Dispatch engineering / refactoring goal"),
    ("切片写入 packets.json", "Slice into bounded packets.json"),
    ("根会话 (Root Agent)", "Root Session (Sole Authority)"),
    ("唯一认知与编排权威", "Sole cognitive and planning authority"),
    ("轻量 S1 瘦身运行态 (12.5k tok)", "Lean S1 pruned runtime (12.5k tok)"),
    ("执行 zloop c2c prepare --role plan", "Executes zloop c2c prepare --role plan"),
    ("审级 1 · 计划审计 (C2C-P)", "Audit Gate 1 · Planning (C2C-P)"),
    ("ChatGPT 网页端 / 跨家族新线程", "ChatGPT Web / Disjoint Fresh Thread"),
    ("信息量最小，决策价值最高", "Lowest token cost, highest reasoning leverage"),
    ("反驳架构漏洞、补齐 Web 事实", "Challenges assumptions, complements web facts"),
    ("通过 -> record c2c_recorded (plan)", "Pass -> record c2c_recorded (role: plan)"),
    ("计划门禁 (Wave Gate)", "Wave Dispatch Gate"),
    ("HIGH/CRITICAL 强制要求", "Mandatory for HIGH/CRITICAL risk"),
    ("无 plan 记录 -> 阻断在 PLANNING", "Missing plan record -> Blocked in PLANNING"),
    ("放行 -> 允许发射并发 Workers", "Approved -> Launch concurrent workers"),
    ("控制底座 (Control S)", "Control Store (S Engine)"),
    ("SQLite DELETE+EXTRA 崩溃一致", "SQLite DELETE+EXTRA crash consistent"),
    ("busy_timeout=30s · CAS Token 守卫", "busy_timeout=30s · CAS Token ownership"),
    ("STAGE 2 · 物理隔离并发执行", "STAGE 2 · ISOLATED CONCURRENCY"),
    ("冷调度器 (Supervisor)", "Cold Wave Supervisor"),
    ("单主进程驱动 · 零推理纯物理逻辑", "Deterministic process · Zero inference overhead"),
    ("50ms 微弱错峰发射，平滑 API 429", "50ms staggered launch smoothing burst QPS"),
    ("8–15 独立工作树 (Worktrees)", "8–15 Isolated Worktrees"),
    ("JIT 异步线程池派发 · 非阻塞 poll() 轮询", "JIT thread pool dispatch · Non-blocking poll()"),
    ("write_scope 严格隔离 · Git 锁重试自愈", "write_scope fencing · index.lock backoff"),
    ("独立临时环境 · 429 指数抖动退避", "Isolated workspace · 429 jittered retry"),
    ("产出文件 Delta，提交候选哈希", "Emits filesystem delta & candidate SHA"),
    ("全量 15 路并发承载 · 绝无单线程卡死", "Full 15-way concurrency · Zero hang"),
    ("子代理 Profile: zloop-worker", "Worker Profile: zloop-worker"),
    ("纯开发 9 工具 · 零 MCP Schema 负担", "Lean 9-tool catalog · Zero MCP clutter"),
    ("Delta 机械提取", "Deterministic Delta Extraction"),
    ("git status --porcelain=v2 -z 严密重构", "Parsed from git status --porcelain=v2 -z"),
    ("空父目录递归向上清理，无幽灵残留", "Empty parent directories pruned upwards"),
    ("STAGE 3 · 物化与防毒化回滚", "STAGE 3 · MATERIALIZATION & DEFENSE"),
    ("临时暂存区分支 (Staging)", "Staging Workspace (staging_ws)"),
    ("单 Stage 私有 Git 空间 (staging_ws)", "Private stage branch worktree"),
    ("物化前记录干净 parent_sha 基线", "Records clean parent_sha baseline before apply"),
    ("顺序应用 Workers 产出的代码变更", "Sequentially applies validated worker deltas"),
    ("审级 2 · 机械自动化验收", "Audit Gate 2 · Mechanical Acceptance"),
    ("pytest / 编译 / 语法与静态检查", "pytest / cargo test / linters / build"),
    ("机械现实 > 模型自夸 (I38)", "Mechanical reality > Model self-claims (I38)"),
    ("严禁模型自己宣称“测试已跑通”", "Never trust model self-reports of passing"),
    ("通过 -> 暂存候选 Commit，允许晋升", "Pass -> Retain candidate commit, proceed"),
    ("失败 -> 立即触发硬原子回滚！", "Fail -> Trigger immediate hard rollback!"),
    ("防毒化硬重置 (Atomic Rollback)", "Poison-Proof Atomic Rollback"),
    ("git clean -fdx 物理彻底擦除", "git clean -fdx scrubs untracked poison"),
    ("绝不让失败代码驻留在 Staging HEAD", "Never leaves defective commit on Staging HEAD"),
    ("彻底根除多任务“级联雪崩误杀”", "Completely eliminates cascading poison avalanche"),
    ("S 库事件记录 candidate sha 取证保全", "S-event records candidate SHA for evidence"),
    ("状态物化 (Materialized)", "Packet State: MATERIALIZED"),
    ("Packet 状态跃迁为 MATERIALIZED", "Atomic transition in SQLite S transaction"),
    ("更新 Stage current_snapshot 指针", "Stage current_snapshot pointer advances"),
    ("证据归档 (H0/H1)", "Observable Evidence (H0/H1)"),
    ("不可篡改 NDJSON 日志持续追加", "Immutable NDJSON journal append-only"),
    (">4KB 载荷自动溢出至 CAS Blob", ">4KB payloads spill to CAS blob store"),
    ("STAGE 4 · 异构审计与主干晋升", "STAGE 4 · AUDIT & CANONICAL PROMOTION"),
    ("审级 3 · 结果审计 (C2C-A)", "Audit Gate 3 · Result Audit (C2C-A)"),
    ("异构独立直通 · P-HET1 闭环", "Heterogeneous routing · P-HET1 verified"),
    ("全限定模型路由，100% 物理隔离", "Fully-qualified provider routing (af9697f5)"),
    ("只读契约：坚决不写文件", "Read-only contract: never modifies files"),
    ("审查介质：只吃 Git Diff 与单测日志", "Evidence-only: reads diffs and test logs"),
    ("输出契约：严格结构化 JSON 判决", "Strict JSON verdict: PASS or REJECT"),
    ("或调用 ChatGPT 跨家族终验", "Or invoke ChatGPT fresh thread review"),
    ("通过 -> record c2c_recorded (result)", "Pass -> record c2c_recorded (role: result)"),
    ("晋升门禁 (Promote Gate)", "Canonical Promotion Gate"),
    ("角色感知防护 (Role-Aware)", "Role-Aware Protection (D-25)"),
    ("强制校验 role=result 记录", "Requires recorded role=result C2C event"),
    ("plan 角色混充 -> 坚决拦截拒绝！", "plan-role bypass attempt -> Rejected!"),
    ("result 审查通过 -> 放行晋升", "result review verified -> Allow promotion"),
    ("主干原子晋升 (CAS ff-only)", "Canonical Fast-Forward Promotion"),
    ("Git physical oracle 验证干净 HEAD", "Git physical oracle asserts clean HEAD"),
    ("git merge --ff-only 安全快进", "Safe git merge --ff-only to staged SHA"),
    ("主干分支推进至全新 Staged SHA", "Canonical branch advances to staged commit"),
    ("Stage 状态正式跃迁为 PROMOTED", "Stage transitions to PROMOTED in 1 S tx"),
    ("高质量代码产出交付", "Production-Verified Deliverable"),
    ("零毒化破坏 · 零未授权覆盖", "Zero poison leakage · Zero unauthorized write"),
    ("经受住大模型反驳、单测、异构双审", "Counter-plan, mechanical, & heterogeneous verified"),
    ("全生命周期可复现、可审计", "Fully reproducible & observable history"),
]

simp_en = SIMP_ZH
for src, dst in replacements:
    simp_en = simp_en.replace(src, dst)

(DEST / "architecture-simplified.en.svg").write_text(simp_en, encoding="utf-8")
print("Saved architecture-simplified.en.svg")
