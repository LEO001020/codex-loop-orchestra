"""Generate overview architecture SVGs (1600x1320) for zcode-loop-orchestra."""
from pathlib import Path

DEST = Path("E:/zcode/zloop-gen8/docs/assets")

OVERVIEW_ZH = """<svg width="1600" height="1320" id="architecture" class="theme-light" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 1320" role="img" aria-labelledby="title desc">
  <title id="title">ZCode-ZLoop 原生多智能体全景架构图</title>
  <desc id="desc">ZCode-ZLoop 原生架构全景：根智能体编排、规划前置 ChatGPT 跨家族审计、物理工作树并发执行、Staging 原子硬回滚与异构 GLM-5.3 晋升门禁。</desc>
  <defs>
    <linearGradient id="pageGlow" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="var(--glow-a)"/>
      <stop offset="1" stop-color="var(--glow-b)"/>
    </linearGradient>
    <linearGradient id="blueWash" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="var(--blue-wash-a)"/>
      <stop offset="1" stop-color="var(--blue-wash-b)"/>
    </linearGradient>
    <linearGradient id="violetWash" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="var(--violet-wash-a)"/>
      <stop offset="1" stop-color="var(--violet-wash-b)"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="160%">
      <feDropShadow dx="0" dy="14" stdDeviation="22" flood-color="var(--shadow)" flood-opacity="0.16"/>
    </filter>
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
      --blue:#3e7df2;--blue-soft:#dfeafe;--blue-stroke:#b6c9ea;--blue-wash-a:#dfeafe;--blue-wash-b:#dfeafe;
      --green:#3f8a67;--green-soft:#d7e8de;--green-stroke:#b7cfbf;
      --violet:#8d6cb1;--violet-soft:#e6ddf1;--violet-stroke:#cab8de;--violet-wash-a:#e6ddf1;--violet-wash-b:#e6ddf1;
      --amber:#9b7346;--amber-soft:#ecdfd1;--amber-stroke:#d7bda1;
      --red:#c24b4b;--red-soft:#fbebeb;--red-stroke:#e8b5b5;
      --glow-a:#ffffff;--glow-b:#eef1f5;
      font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text",Inter,"Microsoft YaHei","Noto Sans CJK SC","Segoe UI",Roboto,Arial,sans-serif;
    }
    @media (prefers-color-scheme: dark) {
      #architecture{
        --bg:#08090b;--surface:#111317;--surface-2:#171a1f;--ink:#f6f7f8;--muted:#a8aeb8;--faint:#737a86;
        --line:#2a2f37;--line-strong:#737e8e;--shadow:#000000;
        --blue:#86b1ea;--blue-soft:#1a2434;--blue-stroke:#425a7f;--blue-wash-a:#1a2434;--blue-wash-b:#1a2434;
        --green:#7db995;--green-soft:#1b2b22;--green-stroke:#3b644b;
        --violet:#b89ad6;--violet-soft:#2a2130;--violet-stroke:#664f79;--violet-wash-a:#2a2130;--violet-wash-b:#2a2130;
        --amber:#d0ae82;--amber-soft:#2d241b;--amber-stroke:#6a543e;
        --red:#e27a7a;--red-soft:#331d1d;--red-stroke:#844141;
        --glow-a:#0d1118;--glow-b:#090a0c;
      }
    }
    .page{fill:var(--bg)} .decor{fill:url(#pageGlow)}
    .title{fill:var(--ink);font-size:40px;font-weight:710;letter-spacing:-1.3px}
    .subtitle{fill:var(--muted);font-size:16px;font-weight:430;letter-spacing:-0.15px}
    .eyebrow{fill:var(--faint);font-size:12px;font-weight:700;letter-spacing:1.8px}
    .stage{fill:var(--faint);font-size:13.5px;font-weight:760;letter-spacing:1.5px}
    .node-title{fill:var(--ink);font-size:18px;font-weight:690;letter-spacing:-0.3px}
    .node-sub{fill:var(--muted);font-size:12px;font-weight:460;line-height:1.4}
    .chip-text{font-size:11.5px;font-weight:650}
    .small{fill:var(--muted);font-size:11px;font-weight:500}
    .foot{fill:var(--faint);font-size:11px;font-weight:500}
    .box{fill:var(--surface);stroke:var(--line);stroke-width:1}
    .box-soft{fill:var(--surface-2);stroke:var(--line);stroke-width:1}
    .blue-card{fill:var(--surface);stroke:var(--blue-stroke);stroke-width:1.2}
    .green-card{fill:var(--surface);stroke:var(--green-stroke);stroke-width:1.2}
    .violet-card{fill:var(--surface);stroke:var(--violet-stroke);stroke-width:1.2}
    .amber-card{fill:var(--surface);stroke:var(--amber-stroke);stroke-width:1.2}
    .red-card{fill:var(--surface);stroke:var(--red-stroke);stroke-width:1.2}
    .connector{fill:none;stroke:var(--line-strong);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arrow)}
    .connector-soft{fill:none;stroke:var(--line);stroke-width:1.3;stroke-linecap:round;stroke-linejoin:round}
    .connector-blue{fill:none;stroke:var(--blue);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arrowBlue)}
    .connector-green{fill:none;stroke:var(--green);stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arrowGreen)}
    .connector-red{fill:none;stroke:var(--red);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:6 6;marker-end:url(#arrowRed)}
    .hairline{stroke:var(--line);stroke-width:1}
    .metric,.raised{filter:url(#shadowSoft)} .hero{filter:url(#shadow)}
    .blue-text{fill:var(--blue)} .green-text{fill:var(--green)} .violet-text{fill:var(--violet)} .amber-text{fill:var(--amber)} .red-text{fill:var(--red)}
  </style>

  <rect class="page" width="1600" height="1320"/>
  <circle class="decor" cx="1492" cy="8" r="260" opacity=".65"/>
  <circle class="decor" cx="94" cy="1260" r="220" opacity=".5"/>

  <!-- Header -->
  <text class="eyebrow" x="72" y="52">系统架构 / 控制回路全景</text>
  <text class="title" x="72" y="94">ZCode-ZLoop Orchestra (Gen8)</text>
  <text class="subtitle" x="72" y="125">原生 ZCode 根模型认知唯一定权 · 前置 ChatGPT 规划审计 · 8–15 物理异步并发 · 原子硬回滚 · 异构 GLM-5.3 独立门禁</text>

  <!-- Metrics Bar -->
  <g class="metric raised">
    <rect class="box" x="72" y="156" rx="20" ry="20" width="330" height="84"/>
    <text class="blue-text" font-size="34" font-weight="750" x="96" y="202">8–15</text>
    <text fill="var(--ink)" font-size="15" font-weight="650" x="190" y="193">稳定物理并发</text>
    <text class="small" x="190" y="217">独立 Worktree + Git 锁退避自愈</text>
    <rect x="72" y="156" width="4" height="84" rx="2" fill="var(--blue)"/>
  </g>
  <g class="metric raised">
    <rect class="box" x="418" y="156" rx="20" ry="20" width="330" height="84"/>
    <text class="green-text" font-size="34" font-weight="750" x="442" y="202">-53.4%</text>
    <text fill="var(--ink)" font-size="15" font-weight="650" x="575" y="193">首轮 Token 净削减</text>
    <text class="small" x="575" y="217">剥离 14 个 MCP/Skills 注入</text>
    <rect x="418" y="156" width="4" height="84" rx="2" fill="var(--green)"/>
  </g>
  <g class="metric raised">
    <rect class="box" x="764" y="156" rx="20" ry="20" width="330" height="84"/>
    <text class="violet-text" font-size="34" font-weight="750" x="788" y="202">100%</text>
    <text fill="var(--ink)" font-size="15" font-weight="650" x="898" y="193">异构隔离穿透</text>
    <text class="small" x="898" y="217">全限定 af9697f5/glm-5.3 独立直通</text>
    <rect x="764" y="156" width="4" height="84" rx="2" fill="var(--violet)"/>
  </g>
  <g class="metric raised">
    <rect class="box" x="1110" y="156" rx="20" ry="20" width="418" height="84"/>
    <text class="amber-text" font-size="34" font-weight="750" x="1134" y="202">293</text>
    <text fill="var(--ink)" font-size="15" font-weight="650" x="1205" y="193">全量自动化测试全绿</text>
    <text class="small" x="1205" y="217">I1–I44 生产级不变量物理闭环</text>
    <rect x="1110" y="156" width="4" height="84" rx="2" fill="var(--amber)"/>
  </g>

  <!-- Stage Rail -->
  <line class="hairline" x1="160" y1="280" x2="160" y2="1250" opacity=".9"/>
  <circle cx="160" cy="296" r="4" fill="var(--line-strong)"/>
  <text class="stage" x="72" y="301">任务入口</text>
  <text class="stage" x="72" y="396">根会话协调</text>
  <text class="stage" x="72" y="496">规划前置审查</text>
  <text class="stage" x="72" y="605">生命周期控制</text>
  <text class="stage" x="72" y="722">物理并发执行</text>
  <text class="stage" x="72" y="836">物化与机械测试</text>
  <text class="stage" x="72" y="944">异构结果审计</text>
  <text class="stage" x="72" y="1037">主干晋升合入</text>
  <text class="stage" x="72" y="1120">运维可观测</text>

  <!-- Stage 0: 人类任务 -->
  <g class="raised">
    <rect class="box" x="220" y="266" rx="20" ry="20" width="1160" height="66"/>
    <text class="node-title" x="800" y="295" text-anchor="middle">用户工程意图输入 (Developer Prompt)</text>
    <text class="node-sub" x="800" y="318" text-anchor="middle">切入宏观工程目标；最终合并与发布始终由人触发或机械守卫。</text>
  </g>
  <path class="connector" d="M800 332 L800 354"/>

  <!-- Stage 1: Root Session -->
  <g class="hero">
    <rect class="blue-card" x="220" y="354" rx="22" ry="22" width="1160" height="74"/>
    <rect x="242" y="369" rx="10" ry="10" width="146" height="23" fill="var(--blue-soft)" stroke="var(--blue-stroke)"/>
    <text class="chip-text blue-text" x="315" y="384.5" text-anchor="middle">认知与编排手</text>
    <text class="node-title" x="800" y="382" text-anchor="middle">ZCode 根会话 (Gemini 3.8 Flash / Provider 3f0e0bfa)</text>
    <text class="node-sub" x="800" y="407" text-anchor="middle">唯一认知与编排权威 · S1 瘦身运行态 (12.5k Tokens，轻装上阵) · 负责任务切片与 Packets 构造</text>
  </g>
  <path class="connector" d="M800 428 L800 452"/>

  <!-- Stage 2: Planning Gate (C2C-P) -->
  <g class="raised">
    <rect class="violet-card" x="220" y="452" rx="22" ry="22" width="1160" height="102"/>
    <rect x="242" y="468" rx="10" ry="10" width="168" height="23" fill="var(--violet-soft)" stroke="var(--violet-stroke)"/>
    <text class="chip-text violet-text" x="326" y="483.5" text-anchor="middle">审级 1 · 规划前置审查</text>
    <text class="node-title" x="800" y="482" text-anchor="middle">ChatGPT 网页端 / 跨家族独立新线程 (C2C-P)</text>
    <text class="node-sub" x="800" y="506" text-anchor="middle">信息量最少、决策价值最高阶段 · 识别隐藏假设与盲区 · 补齐 Web 检索事实 · 产出 counter-plan 反驳</text>
    <g>
      <rect x="247" y="520" width="265" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="379.5" y="535" text-anchor="middle">zloop c2c prepare --role plan</text>
      <rect x="522" y="520" width="255" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="649.5" y="535" text-anchor="middle">≤8,000 字符脱敏限制</text>
      <rect x="787" y="520" width="285" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="929.5" y="535" text-anchor="middle">zloop c2c record --c2c &lt;id&gt;</text>
      <rect x="1082" y="520" width="270" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="1217" y="535" text-anchor="middle">c2c_plan_gate_required 物理门禁</text>
    </g>
  </g>
  <path class="connector-green" d="M800 554 L800 576"/>

  <!-- Stage 3: Control & Scheduling -->
  <g class="raised">
    <rect class="amber-card" x="220" y="576" rx="22" ry="22" width="1160" height="98"/>
    <rect x="242" y="591" rx="10" ry="10" width="146" height="23" fill="var(--amber-soft)" stroke="var(--amber-stroke)"/>
    <text class="chip-text amber-text" x="315" y="606.5" text-anchor="middle">确定性控制面</text>
    <text class="node-title" x="800" y="603" text-anchor="middle">冷宿主调度器 (Supervisor) &amp; 控制底座 S</text>
    <text class="node-sub" x="800" y="626" text-anchor="middle">程序负责状态迁移、计数与等待；模型只负责判断 · 50ms 启动微弱错峰平滑 API 429 · SQLite busy_timeout=30s 守卫</text>
    <g>
      <rect x="247" y="640" width="255" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="374.5" y="655" text-anchor="middle">DELETE+EXTRA 崩溃一致</text>
      <rect x="512" y="640" width="265" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="644.5" y="655" text-anchor="middle">CAS Token 进程死亡证明 (D-20)</text>
      <rect x="787" y="640" width="270" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="922" y="655" text-anchor="middle">JIT 线程池 + 非阻塞 poll()</text>
      <rect x="1067" y="640" width="285" height="23" rx="11.5" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="1209.5" y="655" text-anchor="middle">429 指数抖动退避重试 (≤3次)</text>
    </g>
  </g>
  <path class="connector-green" d="M800 674 L800 696"/>

  <!-- Stage 4: 8-15 Physical Execution -->
  <g class="hero">
    <rect class="green-card" x="220" y="696" rx="22" ry="22" width="1160" height="106"/>
    <rect x="242" y="711" rx="10" ry="10" width="156" height="23" fill="var(--green-soft)" stroke="var(--green-stroke)"/>
    <text class="chip-text green-text" x="320" y="726.5" text-anchor="middle">物理隔离执行层</text>
    <text class="node-title" x="800" y="724" text-anchor="middle">8–15 独立 Git 工作树 (Worktrees)</text>
    <text class="node-sub" x="800" y="747" text-anchor="middle">每个 Packet 独立 Worktree · write_scope 严格物理隔离 · Git index.lock 4 次指数退避自愈 · zloop-worker 精简 Profile (~513 tok)</text>
    <g>
      <rect x="247" y="764" width="180" height="26" rx="8" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="337" y="781" text-anchor="middle">Worker 1: worktree_p01</text>
      <rect x="437" y="764" width="180" height="26" rx="8" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="527" y="781" text-anchor="middle">Worker 2: worktree_p02</text>
      <rect x="627" y="764" width="180" height="26" rx="8" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="717" y="781" text-anchor="middle">Worker 3: worktree_p03</text>
      <text fill="var(--muted)" font-size="16" font-weight="700" x="822" y="781">···</text>
      <rect x="857" y="764" width="180" height="26" rx="8" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="947" y="781" text-anchor="middle">Worker 8: worktree_p08</text>
      <rect x="1047" y="764" width="180" height="26" rx="8" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="1137" y="781" text-anchor="middle">Worker 15: worktree_p15</text>
    </g>
  </g>
  <path class="connector" d="M800 802 L800 824"/>

  <!-- Stage 5: Materialization & Rollback -->
  <g class="raised">
    <rect class="box" x="220" y="824" rx="22" ry="22" width="1160" height="96"/>
    <rect x="242" y="839" rx="10" ry="10" width="168" height="23" fill="var(--violet-soft)" stroke="var(--violet-stroke)"/>
    <text class="chip-text violet-text" x="326" y="854.5" text-anchor="middle">审级 2 · 机械物化验收</text>
    <text class="node-title" x="800" y="852" text-anchor="middle">Staging 暂存区物化 &amp; 原子硬回滚防线 (P0-2 Fix)</text>
    <text class="node-sub" x="800" y="876" text-anchor="middle">物理应用 Delta -> 执行宿主自动化单测 (pytest/build) -> 失败立即 git reset --hard parent_sha + git clean -fdx 拔除毒株</text>
    <g>
      <rect x="247" y="888" width="265" height="22" rx="11" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="379.5" y="903" text-anchor="middle">空父目录向上递归修剪 (P0-4)</text>
      <rect x="522" y="888" width="275" height="22" rx="11" fill="var(--red-soft)" stroke="var(--red-stroke)"/>
      <text class="small red-text" x="659.5" y="903" text-anchor="middle">失败零残留：拒绝级联毒化雪崩</text>
      <rect x="807" y="888" width="270" height="22" rx="11" fill="var(--surface-2)" stroke="var(--line)"/>
      <text class="small" x="942" y="903" text-anchor="middle">S 库记录 materialization_failed</text>
      <rect x="1087" y="888" width="265" height="22" rx="11" fill="var(--green-soft)" stroke="var(--green-stroke)"/>
      <text class="small green-text" x="1219.5" y="903" text-anchor="middle">通过：状态跃迁 MATERIALIZED</text>
    </g>
  </g>
  <path class="connector-green" d="M800 920 L800 942"/>

  <!-- Stage 6: Heterogeneous C2C-A Audit -->
  <g class="hero">
    <rect class="violet-card" x="220" y="942" rx="22" ry="22" width="1160" height="88"/>
    <rect x="242" y="957" rx="10" ry="10" width="176" height="23" fill="var(--violet-soft)" stroke="var(--violet-stroke)"/>
    <text class="chip-text violet-text" x="330" y="972.5" text-anchor="middle">审级 3 · 异构代码审计</text>
    <text class="node-title" x="800" y="970" text-anchor="middle">GLM-5.3 专职审计官 (P-HET1 闭环) / ChatGPT 跨家族终验</text>
    <text class="node-sub" x="800" y="995" text-anchor="middle">全限定 af9697f5/glm-5.3 独立直通 · 只读契约（坚决不写文件） · 仅吃 Git Diff 与单测日志 · 严格 JSON 判决 (PASS / REJECT)</text>
  </g>
  <path class="connector-green" d="M800 1030 L800 1052"/>

  <!-- Stage 7: Canonical Promotion -->
  <g class="raised">
    <rect class="green-card" x="220" y="1052" rx="22" ry="22" width="1160" height="84"/>
    <rect x="242" y="1067" rx="10" ry="10" width="136" height="23" fill="var(--green-soft)" stroke="var(--green-stroke)"/>
    <text class="chip-text green-text" x="310" y="1082.5" text-anchor="middle">主干安全晋升</text>
    <text class="node-title" x="800" y="1079" text-anchor="middle">角色感知晋升门禁 &amp; CAS ff-only 快速合并</text>
    <text class="node-sub" x="800" y="1104" text-anchor="middle">强制校验 role=result 审计记录（plan 角色混充坚决拦截） · Git physical oracle 确认干净 HEAD · 安全快进推进主干</text>
  </g>
  <path class="connector" d="M800 1136 L800 1158"/>

  <!-- Stage 8: Observability & Evidence -->
  <g class="raised">
    <rect class="box" x="220" y="1158" rx="20" ry="20" width="1160" height="76"/>
    <text class="node-title" x="800" y="1186" text-anchor="middle">不可篡改证据面 (H0/H1) &amp; 自动化运维指标 (Metrics)</text>
    <text class="node-sub" x="800" y="1210" text-anchor="middle">H0 NDJSON 事件流水 · CAS Blob 大载荷溢出 · zloop.metrics 吞吐率 / 时延分布 / Token 节约与并发重叠率自动化核算</text>
  </g>

  <!-- Footer -->
  <text class="foot" x="72" y="1285">ZCode-ZLoop Gen8 · Native Multi-Agent Orchestration &amp; Triple-Audit Architecture · Invariants I1–I44 Enforced</text>
  <text class="foot" x="1190" y="1285">Fully Verified by Automated Chaos &amp; Regression Suites (293 Tests Passed)</text>
</svg>"""

(DEST / "architecture-overview.zh-CN.svg").write_text(OVERVIEW_ZH, encoding="utf-8")
print("Saved architecture-overview.zh-CN.svg")

# English version for overview
replacements_overview = [
    ("ZCode-ZLoop 原生多智能体全景架构图", "ZCode-ZLoop Native Multi-Agent Architecture Overview"),
    ("ZCode-ZLoop 原生架构全景：根智能体编排、规划前置 ChatGPT 跨家族审计、物理工作树并发执行、Staging 原子硬回滚与异构 GLM-5.3 晋升门禁。", "ZCode-ZLoop native architecture overview: sole root cognition, pre-planning ChatGPT audit, physical worktree concurrency, atomic staging rollback, and heterogeneous GLM-5.3 promotion gate."),
    ("系统架构 / 控制回路全景", "SYSTEM ARCHITECTURE / FULL CONTROL LOOP"),
    ("原生 ZCode 根模型认知唯一定权 · 前置 ChatGPT 规划审计 · 8–15 物理异步并发 · 原子硬回滚 · 异构 GLM-5.3 独立门禁", "Native ZCode Root Cognition Authority · Pre-Planning ChatGPT Audit · 8–15 Physical Concurrency · Atomic Rollback · Independent GLM-5.3 Gate"),
    ("稳定物理并发", "Physical Concurrency"),
    ("独立 Worktree + Git 锁退避自愈", "Isolated Worktrees + index.lock Backoff"),
    ("首轮 Token 净削减", "Net Input Token Reduction"),
    ("剥离 14 个 MCP/Skills 注入", "Pruned 14 MCP/Skill Schemas"),
    ("异构隔离穿透", "Heterogeneous Isolation"),
    ("全限定 af9697f5/glm-5.3 独立直通", "Fully-qualified af9697f5/glm-5.3 direct"),
    ("全量自动化测试全绿", "Automated Tests All Green"),
    ("I1–I44 生产级不变量物理闭环", "Invariants I1–I44 Enforced"),
    ("任务入口", "ENTRY"),
    ("根会话协调", "ROOT COGNITION"),
    ("规划前置审查", "PLAN AUDIT"),
    ("生命周期控制", "CONTROL PLANE"),
    ("物理并发执行", "EXECUTION"),
    ("物化与机械测试", "MATERIALIZE"),
    ("异构结果审计", "RESULT AUDIT"),
    ("主干晋升合入", "PROMOTION"),
    ("运维可观测", "OBSERVABILITY"),
    ("用户工程意图输入 (Developer Prompt)", "Developer Engineering Intent (Task Entry)"),
    ("切入宏观工程目标；最终合并与发布始终由人触发或机械守卫。", "High-level goal slice; final merge and promotion always guarded mechanically or by human."),
    ("认知与编排手", "Cognition & Planning"),
    ("ZCode 根会话 (Gemini 3.8 Flash / Provider 3f0e0bfa)", "ZCode Root Session (Gemini 3.8 Flash / Provider 3f0e0bfa)"),
    ("唯一认知与编排权威 · S1 瘦身运行态 (12.5k Tokens，轻装上阵) · 负责任务切片与 Packets 构造", "Sole cognitive authority · Lean S1 pruned runtime (12.5k tokens) · Task decomposition into Packets"),
    ("审级 1 · 规划前置审查", "Audit Gate 1 · Pre-Planning Review"),
    ("ChatGPT 网页端 / 跨家族独立新线程 (C2C-P)", "ChatGPT Web / Cross-Family Fresh Thread (C2C-P)"),
    ("信息量最少、决策价值最高阶段 · 识别隐藏假设与盲区 · 补齐 Web 检索事实 · 产出 counter-plan 反驳", "Lowest token cost, highest reasoning value · Surfaces hidden assumptions · Complements web facts · Emits counter-plan"),
    ("≤8,000 字符脱敏限制", "≤8,000 chars bounded & redacted"),
    ("c2c_plan_gate_required 物理门禁", "c2c_plan_gate_required gate"),
    ("确定性控制面", "Control Plane"),
    ("冷宿主调度器 (Supervisor) & 控制底座 S", "Cold Supervisor & Control Store (S)"),
    ("程序负责状态迁移、计数与等待；模型只负责判断 · 50ms 启动微弱错峰平滑 API 429 · SQLite busy_timeout=30s 守卫", "Code manages state transitions and waiting; models make judgments · 50ms staggered launch · busy_timeout=30s"),
    ("DELETE+EXTRA 崩溃一致", "DELETE+EXTRA crash consistent"),
    ("CAS Token 进程死亡证明 (D-20)", "CAS Token process death proof (D-20)"),
    ("JIT 线程池 + 非阻塞 poll()", "JIT thread pool + Non-blocking poll()"),
    ("429 指数抖动退避重试 (≤3次)", "429 jittered exponential backoff (≤3)"),
    ("物理隔离执行层", "Physical Execution"),
    ("8–15 独立 Git 工作树 (Worktrees)", "8–15 Isolated Git Worktrees"),
    ("每个 Packet 独立 Worktree · write_scope 严格物理隔离 · Git index.lock 4 次指数退避自愈 · zloop-worker 精简 Profile (~513 tok)", "One Worktree per Packet · write_scope isolation · 4-attempt index.lock backoff · zloop-worker profile (~513 tok)"),
    ("审级 2 · 机械物化验收", "Audit Gate 2 · Mechanical Acceptance"),
    ("Staging 暂存区物化 & 原子硬回滚防线 (P0-2 Fix)", "Staging Worktree Materialization & Atomic Rollback (P0-2)"),
    ("物理应用 Delta -> 执行宿主自动化单测 (pytest/build) -> 失败立即 git reset --hard parent_sha + git clean -fdx 拔除毒株", "Applies delta -> Runs mechanical tests -> On failure immediately git reset --hard + clean -fdx"),
    ("空父目录向上递归修剪 (P0-4)", "Empty parent directories pruned (P0-4)"),
    ("失败零残留：拒绝级联毒化雪崩", "Zero poison residue: prevents cascading avalanche"),
    ("S 库记录 materialization_failed", "S-event records candidate SHA"),
    ("通过：状态跃迁 MATERIALIZED", "Passed: atomic transition to MATERIALIZED"),
    ("审级 3 · 异构代码审计", "Audit Gate 3 · Result Code Audit"),
    ("GLM-5.3 专职审计官 (P-HET1 闭环) / ChatGPT 跨家族终验", "GLM-5.3 Dedicated Auditor (P-HET1 Closed) / ChatGPT Final Gate"),
    ("全限定 af9697f5/glm-5.3 独立直通 · 只读契约（坚决不写文件） · 仅吃 Git Diff 与单测日志 · 严格 JSON 判决 (PASS / REJECT)", "Fully-qualified provider af9697f5 direct · Read-only contract · Reads diffs and logs · Strict JSON (PASS/REJECT)"),
    ("主干安全晋升", "Canonical Promotion"),
    ("角色感知晋升门禁 & CAS ff-only 快速合并", "Role-Aware Promotion Gate & CAS ff-only Merge"),
    ("强制校验 role=result 审计记录（plan 角色混充坚决拦截） · Git physical oracle 确认干净 HEAD · 安全快进推进主干", "Requires role=result C2C record (plan-role rejected) · Git physical oracle asserts clean HEAD · Fast-forward"),
    ("不可篡改证据面 (H0/H1) & 自动化运维指标 (Metrics)", "Observable Evidence (H0/H1) & Automated Metrics"),
    ("H0 NDJSON 事件流水 · CAS Blob 大载荷溢出 · zloop.metrics 吞吐率 / 时延分布 / Token 节约与并发重叠率自动化核算", "H0 NDJSON stream · CAS blob overflow · zloop.metrics throughput / latency / token reduction & concurrency overlap"),
]

overview_en = OVERVIEW_ZH
for src, dst in replacements_overview:
    overview_en = overview_en.replace(src, dst)

(DEST / "architecture-overview.en.svg").write_text(overview_en, encoding="utf-8")
print("Saved architecture-overview.en.svg")
