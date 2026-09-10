PASS — node_repl未逃逸Job；headless三链路已统一禁用，Desktop保留，Windows边界无竞态。

## 1. node_repl未逃逸（进程证据）
当前进程树：node_repl.exe共9个，全部挂PPID=50708，即Desktop app-server
（codex.exe -c features.code_mode_host=true app-server），属Desktop会话MCP子进程。
无headless supervisor/exec后代持有node_repl（活跃headless链为独立codex.exe exec
进程，命令行均含 -c mcp_servers.node_repl.enabled=false）。所谓逃逸Job不会发生：
node_repl是headless supervisor后代时，AssignProcessToJobObject在ResumeThread之前
完成（lifecycle_supervisor.py:397-400），子进程自ExitBoot阶段已在Job内，其后代默认
继承Job归属，KILL_ON_JOB_CLOSE保证整树回收。

## 2. headless三链路覆盖一致（源码证据）
harness/headless_wave.py:278、harness/dispatch.py:505、harness/dispatch_v2.py:438
均无条件注入 "-c mcp_servers.node_repl.enabled=false"（紧随ipybox覆盖，无法被后续
参数反转；无任务级opt-in）。Desktop面（tool/agents_spawn）未触碰，保留node_repl。

## 3. Windows CREATE_SUSPENDED→Job→Resume边界
lifecycle_supervisor.py:348-416：CreateJobObjectW+KILL_ON_JOB_CLOSE设置在
CreateProcessW之前（:360-372）；CreateProcessW带CREATE_SUSPENDED（:383）；
Assign→Resume按序（:397-400）；失败路径TerminateProcess强句柄回退+
组合报错（:401-413），杜绝永久挂起孤儿。测试覆盖三失败路径
(test_windows_assign_failure_terminates_suspended_child_before_close等)。

## 4. 摩擦与兼容性
低摩擦：单一条-c覆盖，三处复制，无新政策文件；不破坏LOOP：ipybox/apps/remote_plugin
政策独立保留；Desktop节点node_repl会话不受影响；supervisor的Job/Stdout/事件路径无改动。

## 唯一残余（非阻塞）
headless下并非node_repl物理不可能创建——禁用依赖MCP自动启动被诚实关闭。
当前无进程证据显示泄漏；运维上Job边界仍是兜底。
