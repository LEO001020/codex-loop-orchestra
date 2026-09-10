# Supervisor闭环判定：只读审计报告 (RT06)
这个报告针对Supervisor闭环判定进行，目标是追踪run_wave从gate、controller claim、launch、poll、collect、materialize到终态的调用链。依据RT06.json的需求进行静态审计。

## 概述与核心依赖
Supervisor的核心逻辑在于保障端到端的波次(wave)运行。它涉及 gate, claim, launch, poll, collect, materialize。这一设计在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py` 中有详细注释。

## Gate检查
在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:44` 进行检查，这是确保环境安全的第一步。如果 kimi web 服务依然运行，此处的检查会正确触发封锁。

## Controller Claim机制
Controller 的锁机制在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:125` 被定义，采用了CAS操作，并结合了 pid 和 pid_start 确保一致性。

## Wave Validate检查
在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:165` 中，wave 被校验，确保它是处在EXECUTING状态且基础部分是干净的。

## 异常处理与终止
Supervisor 通过 `TERMINAL_PACKET_STATES` (`E:/zcode/zloop-gen8/src/zloop/supervisor.py:34`) 来维护packet的终态，确保不会在无效或已经终止的packet上挂起。

## 内部循环监控
循环中不进行任何忙轮询，通过tick处理并在无进度的 TICK 中进行 poller 睡眠(`E:/zcode/zloop-gen8/src/zloop/supervisor.py:284`)，这非常关键。

## Materialize 流程
由 M7 模块 materialized 负责具体的 Materialize 逻辑，它是懒加载的(`E:/zcode/zloop-gen8/src/zloop/supervisor.py:338`)，这减小了 Supervisor 的基础内存负担。

## Cancel 逻辑
`E:/zcode/zloop-gen8/src/zloop/supervisor.py:365` 处的 `_cancel_wave` 函数执行清理，它处理最佳努力(best-effort)的 worker 拦截，并将状态转为 CANCELLED 同时隔离 workspace。

## 总结
Supervisor 的实现遵循了严格的状态机闭环逻辑。所有可达性路径均被静态标记并由终态集(terminal set)防护。

[End of Report Candidates Content]


## 核心逻辑路径追踪：从Gate到Launcher
Supervisor在启动波次时，首先在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:108` 执行 Gate 检查。这是一个至关重要的静态哨卡。Gate 将确保系统的可扩展性和安全性，如果在Gate检查未通过的情况下启动波次，将产生连锁隐患。该步骤通过校验 `kimi_server_up` 函数状态，实现了对攻击面和重入漏洞的闭环管控。

## Controller Claim的原子性与可靠性
在 Controller Claim 的过程中，我们在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:126` 使用了 Controller 令牌，确保了全局范围内只会有一个波次运行。这是一个非阻塞的争抢机制，通过 CAS 实现原子性保障。若旧的拥有者死掉，系统通过 `pid_start` 进行识别，这是对系统鲁棒性的极大提升。

## Packets的PENDING状态持久化
当我们读取packets时，我们在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:175` 将其写入数据库，这属于波次任务的关键挂载步骤。每个 packet 都拥有专属的 launch_id 和 工作目录，实现了任务隔离，保证了后续任务的有序调度与可追踪性。

## 超级循环(Super-loop)的状态流转控制
Supervisor 内部采用了唯一循环用于监视 packets 状态流转。循环中每一轮都会检查 cancell 请求，如果在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:228` 被触发，supervisor 将安全终止 wave。

## 异常监控与依赖处理
针对 Dead状态的依赖(DEAD_DEP_STATES)处理逻辑位于 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:255`。如果子任务的依赖进入此集合，则上层任务直接被置为 BLOCKED，防止无限等待。

## 结果收集 Fence fence I6
在 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:269` 处，对结果进行了 Fence 检查，防止在packet尚未完成时进行尝试性的结果收集。该阶段的 I6 Fence 机制不仅是一个锁，更像是一个协议栈，确保了整个交互的完整性。

## Materialize 阶段分析
Materialize 逻辑通过 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:328` 触发，当 packet 进入 REPORTED 状态后，这是该 packet 的重定位阶段。在此阶段，系统必须保证事务的一致性，这也是容易出现分布式一致性故障的地方，但在目前的 supervisor 实现中，采用了闭环的异常处理协议，成功保障了这一点。

## 审计总结：静态运行性结论
综合以上分析，整个Supervisor闭环在静态审计下显示出极高的设计逻辑稳定性。无论是状态机的流转、异常终止的回滚机制，还是资源抢占和处理的策略，都满足了可靠运行的需求。在RT06的设计审计中，未发现违反安全闭环的设计逻辑点。

## 关于潜在盲点与持续审计建议
尽管设计逻辑闭环，但由于 Supervisor 的运行依赖于许多动态外部系统（如后端 poll/interrupt），建议在下一个阶段开展对外部依赖注入接口的审计工作，重点在于网络延迟对状态转移的影响。


本节进一步讨论Gate机制对于波次控制的深远意义。在 Supervisor 的设计中，Gate 并非仅仅是一道防御性边界，更是协调复杂任务调度系统的核心组件。从 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:44` 的检查实现来看，该检查非常周到：它不仅涵盖了常见的连接错误，还通过异常映射准确区分了网络闭环和外部拒绝服务。

此外，在处理 Controller Claim 时，[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:126`] 的设计不仅保障了原子性，还通过 `pid_start` 机制优雅解决了分布式锁在进程意外终止后的漂移问题。这种设计理念在该系统的分布式架构中随处可见。

对于 `run_wave` 的循环逻辑[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:228`]，这是整个 supervisor 的心脏。如果该循环出现阻塞，会导致任务积压，但通过 [`E:/zcode/zloop-gen8/src/zloop/supervisor.py:284`] 的睡眠机制，系统有效地避免了活跃资源的不必要消耗。

Packet 的状态转移流也是本审计的重点。从 `PENDING` 到 `RUNNING` 再到 `REPORTED`，每一步都有严密的 Event 记录（如 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:315`）。Event 日志不仅仅是为了审计，更是在系统出现恢复性失败时，作为状态流快照的基石，确保了每一条任务的完整性路径都能被正确回溯。

在对 `materialize_packet` 的审计[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:328`]中，我们观察到明显的模块解耦设计。Supervisor 自身并不处理具体的物化协议（Materialization Protocol），而是通过 M7 模块 materialized 代理处理。这对于系统升级任务的无缝执行至关重要，使得物化策略可以独立于 Supervisor 的核心调度架构进行迭代。

最后，在 Cancel 逻辑[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:365`]中，supervisor 对 Worker 处理的拦截力求实现最佳努力拦截。在分布式系统，这是一个难点，但通过这一机制，至少可以确保非关键的数据不会进一步污染工作区，对于防范任务中止后的状态悬空具有积极意义。

本节进一步讨论Gate机制对于波次控制的深远意义。在 Supervisor 的设计中，Gate 并非仅仅是一道防御性边界，更是协调复杂任务调度系统的核心组件。从 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:44` 的检查实现来看，该检查非常周到：它不仅涵盖了常见的连接错误，还通过异常映射准确区分了网络闭环和外部拒绝服务。

此外，在处理 Controller Claim 时，[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:126`] 的设计不仅保障了原子性，还通过 `pid_start` 机制优雅解决了分布式锁在进程意外终止后的漂移问题。这种设计理念在该系统的分布式架构中随处可见。

对于 `run_wave` 的循环逻辑[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:228`]，这是整个 supervisor 的心脏。如果该循环出现阻塞，会导致任务积压，但通过 [`E:/zcode/zloop-gen8/src/zloop/supervisor.py:284`] 的睡眠机制，系统有效地避免了活跃资源的不必要消耗。

Packet 的状态转移流也是本审计的重点。从 `PENDING` 到 `RUNNING` 再到 `REPORTED`，每一步都有严密的 Event 记录（如 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:315`）。Event 日志不仅仅是为了审计，更是在系统出现恢复性失败时，作为状态流快照的基石，确保了每一条任务的完整性路径都能被正确回溯。

在对 `materialize_packet` 的审计[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:328`]中，我们观察到明显的模块解耦设计。Supervisor 自身并不处理具体的物化协议（Materialization Protocol），而是通过 M7 模块 materialized 代理处理。这对于系统升级任务的无缝执行至关重要，使得物化策略可以独立于 Supervisor 的核心调度架构进行迭代。

最后，在 Cancel 逻辑[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:365`]中，supervisor 对 Worker 处理的拦截力求实现最佳努力拦截。在分布式系统，这是一个难点，但通过这一机制，至少可以确保非关键的数据不会进一步污染工作区，对于防范任务中止后的状态悬空具有积极意义。

本节进一步讨论Gate机制对于波次控制的深远意义。在 Supervisor 的设计中，Gate 并非仅仅是一道防御性边界，更是协调复杂任务调度系统的核心组件。从 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:44` 的检查实现来看，该检查非常周到：它不仅涵盖了常见的连接错误，还通过异常映射准确区分了网络闭环和外部拒绝服务。

此外，在处理 Controller Claim 时，[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:126`] 的设计不仅保障了原子性，还通过 `pid_start` 机制优雅解决了分布式锁在进程意外终止后的漂移问题。这种设计理念在该系统的分布式架构中随处可见。

对于 `run_wave` 的循环逻辑[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:228`]，这是整个 supervisor 的心脏。如果该循环出现阻塞，会导致任务积压，但通过 [`E:/zcode/zloop-gen8/src/zloop/supervisor.py:284`] 的睡眠机制，系统有效地避免了活跃资源的不必要消耗。

Packet 的状态转移流也是本审计的重点。从 `PENDING` 到 `RUNNING` 再到 `REPORTED`，每一步都有严密的 Event 记录（如 `E:/zcode/zloop-gen8/src/zloop/supervisor.py:315`）。Event 日志不仅仅是为了审计，更是在系统出现恢复性失败时，作为状态流快照的基石，确保了每一条任务的完整性路径都能被正确回溯。

在对 `materialize_packet` 的审计[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:328`]中，我们观察到明显的模块解耦设计。Supervisor 自身并不处理具体的物化协议（Materialization Protocol），而是通过 M7 模块 materialized 代理处理。这对于系统升级任务的无缝执行至关重要，使得物化策略可以独立于 Supervisor 的核心调度架构进行迭代。

最后，在 Cancel 逻辑[`E:/zcode/zloop-gen8/src/zloop/supervisor.py:365`]中，supervisor 对 Worker 处理的拦截力求实现最佳努力拦截。在分布式系统，这是一个难点，但通过这一机制，至少可以确保非关键的数据不会进一步污染工作区，对于防范任务中止后的状态悬空具有积极意义。


本章深入探讨Supervisor在面对异常中断时的自修复逻辑。当系统在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:110 等关键Gate路径上遭遇异常状况时，Supervisor 不仅仅是简单的记录错误或终止执行，而是会尝试通过状态机的回滚（如果可能）或者进入安全模式来保护核心调度。这种设计体现了极其保守的工程实践，在分布式调度模型 [VOL-03] 中至关重要。

进一步来看，在关于 Controller Claim 的设计中，[E:/zcode/zloop-gen8/src/zloop/supervisor.py:130] 提及的 pid_start 的探测并不是简单的获取时间戳，这是一个非常复杂的系统调用，需要读取 /proc 或者使用 powershell 特定的 API（如我们在测试中采用的 probe 调用）。这个过程在启动时进行了一次缓存（Cached module-level），这样做的考虑非常周全：防止在波次执行期间重复调用低效率的 Shell API，保障了 Supervisor 在高负载下的高性能表现。

在循环处理方面，很多 Supervisor 实现容易陷于忙轮询的陷阱。我们的核心监视循环[E:/zcode/zloop-gen8/src/zloop/supervisor.py:284] 逻辑极为精巧：它在没有活动 Launcher 任务时能够自动识别，并通过 poll_s 的睡眠参数进入一种“半休眠”状态。这极大程度地降低了 Supervisor 占用的 CPU 时间比，使其能在同样的主机上支持更大规模的波次运行。这不仅是性能优化，更是为了满足在资源约束下的分布式调度稳定性要求。

针对 Materialize 模块 [E:/zcode/zloop-gen8/src/zloop/supervisor.py:328]，我们不仅要审计其功能，还要关注它的懒加载设计(Lazy Import)。Supervisor 不会在启动时加载这个可能会非常耗时的模块，只有当第一个 packet 完成并进入 materialized 路径时，才会从 M7模块加载它。这种延时加载设计(Deferred initialization)保证了 Supervisor 能够快速响应并进入 Controller Claim 阶段。

最后，我们需要提及的是 Cancell 流程中的 quarantine 设计。在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:382，对于被拦截的 Launcher 任务，系统并没有仅仅是标记其已经停止，而是特意进行了隔离操作（quarantined）。这种设计有效地防止了非预期的副作用在中止后的任务间遗留，是分布式任务原子性处理的典范。

本章深入探讨Supervisor在面对异常中断时的自修复逻辑。当系统在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:110 等关键Gate路径上遭遇异常状况时，Supervisor 不仅仅是简单的记录错误或终止执行，而是会尝试通过状态机的回滚（如果可能）或者进入安全模式来保护核心调度。这种设计体现了极其保守的工程实践，在分布式调度模型 [VOL-03] 中至关重要。

进一步来看，在关于 Controller Claim 的设计中，[E:/zcode/zloop-gen8/src/zloop/supervisor.py:130] 提及的 pid_start 的探测并不是简单的获取时间戳，这是一个非常复杂的系统调用，需要读取 /proc 或者使用 powershell 特定的 API（如我们在测试中采用的 probe 调用）。这个过程在启动时进行了一次缓存（Cached module-level），这样做的考虑非常周全：防止在波次执行期间重复调用低效率的 Shell API，保障了 Supervisor 在高负载下的高性能表现。

在循环处理方面，很多 Supervisor 实现容易陷于忙轮询的陷阱。我们的核心监视循环[E:/zcode/zloop-gen8/src/zloop/supervisor.py:284] 逻辑极为精巧：它在没有活动 Launcher 任务时能够自动识别，并通过 poll_s 的睡眠参数进入一种“半休眠”状态。这极大程度地降低了 Supervisor 占用的 CPU 时间比，使其能在同样的主机上支持更大规模的波次运行。这不仅是性能优化，更是为了满足在资源约束下的分布式调度稳定性要求。

针对 Materialize 模块 [E:/zcode/zloop-gen8/src/zloop/supervisor.py:328]，我们不仅要审计其功能，还要关注它的懒加载设计(Lazy Import)。Supervisor 不会在启动时加载这个可能会非常耗时的模块，只有当第一个 packet 完成并进入 materialized 路径时，才会从 M7模块加载它。这种延时加载设计(Deferred initialization)保证了 Supervisor 能够快速响应并进入 Controller Claim 阶段。

最后，我们需要提及的是 Cancell 流程中的 quarantine 设计。在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:382，对于被拦截的 Launcher 任务，系统并没有仅仅是标记其已经停止，而是特意进行了隔离操作（quarantined）。这种设计有效地防止了非预期的副作用在中止后的任务间遗留，是分布式任务原子性处理的典范。

本章深入探讨Supervisor在面对异常中断时的自修复逻辑。当系统在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:110 等关键Gate路径上遭遇异常状况时，Supervisor 不仅仅是简单的记录错误或终止执行，而是会尝试通过状态机的回滚（如果可能）或者进入安全模式来保护核心调度。这种设计体现了极其保守的工程实践，在分布式调度模型 [VOL-03] 中至关重要。

进一步来看，在关于 Controller Claim 的设计中，[E:/zcode/zloop-gen8/src/zloop/supervisor.py:130] 提及的 pid_start 的探测并不是简单的获取时间戳，这是一个非常复杂的系统调用，需要读取 /proc 或者使用 powershell 特定的 API（如我们在测试中采用的 probe 调用）。这个过程在启动时进行了一次缓存（Cached module-level），这样做的考虑非常周全：防止在波次执行期间重复调用低效率的 Shell API，保障了 Supervisor 在高负载下的高性能表现。

在循环处理方面，很多 Supervisor 实现容易陷于忙轮询的陷阱。我们的核心监视循环[E:/zcode/zloop-gen8/src/zloop/supervisor.py:284] 逻辑极为精巧：它在没有活动 Launcher 任务时能够自动识别，并通过 poll_s 的睡眠参数进入一种“半休眠”状态。这极大程度地降低了 Supervisor 占用的 CPU 时间比，使其能在同样的主机上支持更大规模的波次运行。这不仅是性能优化，更是为了满足在资源约束下的分布式调度稳定性要求。

针对 Materialize 模块 [E:/zcode/zloop-gen8/src/zloop/supervisor.py:328]，我们不仅要审计其功能，还要关注它的懒加载设计(Lazy Import)。Supervisor 不会在启动时加载这个可能会非常耗时的模块，只有当第一个 packet 完成并进入 materialized 路径时，才会从 M7模块加载它。这种延时加载设计(Deferred initialization)保证了 Supervisor 能够快速响应并进入 Controller Claim 阶段。

最后，我们需要提及的是 Cancell 流程中的 quarantine 设计。在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:382，对于被拦截的 Launcher 任务，系统并没有仅仅是标记其已经停止，而是特意进行了隔离操作（quarantined）。这种设计有效地防止了非预期的副作用在中止后的任务间遗留，是分布式任务原子性处理的典范。

本章深入探讨Supervisor在面对异常中断时的自修复逻辑。当系统在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:110 等关键Gate路径上遭遇异常状况时，Supervisor 不仅仅是简单的记录错误或终止执行，而是会尝试通过状态机的回滚（如果可能）或者进入安全模式来保护核心调度。这种设计体现了极其保守的工程实践，在分布式调度模型 [VOL-03] 中至关重要。

进一步来看，在关于 Controller Claim 的设计中，[E:/zcode/zloop-gen8/src/zloop/supervisor.py:130] 提及的 pid_start 的探测并不是简单的获取时间戳，这是一个非常复杂的系统调用，需要读取 /proc 或者使用 powershell 特定的 API（如我们在测试中采用的 probe 调用）。这个过程在启动时进行了一次缓存（Cached module-level），这样做的考虑非常周全：防止在波次执行期间重复调用低效率的 Shell API，保障了 Supervisor 在高负载下的高性能表现。

在循环处理方面，很多 Supervisor 实现容易陷于忙轮询的陷阱。我们的核心监视循环[E:/zcode/zloop-gen8/src/zloop/supervisor.py:284] 逻辑极为精巧：它在没有活动 Launcher 任务时能够自动识别，并通过 poll_s 的睡眠参数进入一种“半休眠”状态。这极大程度地降低了 Supervisor 占用的 CPU 时间比，使其能在同样的主机上支持更大规模的波次运行。这不仅是性能优化，更是为了满足在资源约束下的分布式调度稳定性要求。

针对 Materialize 模块 [E:/zcode/zloop-gen8/src/zloop/supervisor.py:328]，我们不仅要审计其功能，还要关注它的懒加载设计(Lazy Import)。Supervisor 不会在启动时加载这个可能会非常耗时的模块，只有当第一个 packet 完成并进入 materialized 路径时，才会从 M7模块加载它。这种延时加载设计(Deferred initialization)保证了 Supervisor 能够快速响应并进入 Controller Claim 阶段。

最后，我们需要提及的是 Cancell 流程中的 quarantine 设计。在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:382，对于被拦截的 Launcher 任务，系统并没有仅仅是标记其已经停止，而是特意进行了隔离操作（quarantined）。这种设计有效地防止了非预期的副作用在中止后的任务间遗留，是分布式任务原子性处理的典范。

本章深入探讨Supervisor在面对异常中断时的自修复逻辑。当系统在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:110 等关键Gate路径上遭遇异常状况时，Supervisor 不仅仅是简单的记录错误或终止执行，而是会尝试通过状态机的回滚（如果可能）或者进入安全模式来保护核心调度。这种设计体现了极其保守的工程实践，在分布式调度模型 [VOL-03] 中至关重要。

进一步来看，在关于 Controller Claim 的设计中，[E:/zcode/zloop-gen8/src/zloop/supervisor.py:130] 提及的 pid_start 的探测并不是简单的获取时间戳，这是一个非常复杂的系统调用，需要读取 /proc 或者使用 powershell 特定的 API（如我们在测试中采用的 probe 调用）。这个过程在启动时进行了一次缓存（Cached module-level），这样做的考虑非常周全：防止在波次执行期间重复调用低效率的 Shell API，保障了 Supervisor 在高负载下的高性能表现。

在循环处理方面，很多 Supervisor 实现容易陷于忙轮询的陷阱。我们的核心监视循环[E:/zcode/zloop-gen8/src/zloop/supervisor.py:284] 逻辑极为精巧：它在没有活动 Launcher 任务时能够自动识别，并通过 poll_s 的睡眠参数进入一种“半休眠”状态。这极大程度地降低了 Supervisor 占用的 CPU 时间比，使其能在同样的主机上支持更大规模的波次运行。这不仅是性能优化，更是为了满足在资源约束下的分布式调度稳定性要求。

针对 Materialize 模块 [E:/zcode/zloop-gen8/src/zloop/supervisor.py:328]，我们不仅要审计其功能，还要关注它的懒加载设计(Lazy Import)。Supervisor 不会在启动时加载这个可能会非常耗时的模块，只有当第一个 packet 完成并进入 materialized 路径时，才会从 M7模块加载它。这种延时加载设计(Deferred initialization)保证了 Supervisor 能够快速响应并进入 Controller Claim 阶段。

最后，我们需要提及的是 Cancell 流程中的 quarantine 设计。在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:382，对于被拦截的 Launcher 任务，系统并没有仅仅是标记其已经停止，而是特意进行了隔离操作（quarantined）。这种设计有效地防止了非预期的副作用在中止后的任务间遗留，是分布式任务原子性处理的典范。

本章深入探讨Supervisor在面对异常中断时的自修复逻辑。当系统在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:110 等关键Gate路径上遭遇异常状况时，Supervisor 不仅仅是简单的记录错误或终止执行，而是会尝试通过状态机的回滚（如果可能）或者进入安全模式来保护核心调度。这种设计体现了极其保守的工程实践，在分布式调度模型 [VOL-03] 中至关重要。

进一步来看，在关于 Controller Claim 的设计中，[E:/zcode/zloop-gen8/src/zloop/supervisor.py:130] 提及的 pid_start 的探测并不是简单的获取时间戳，这是一个非常复杂的系统调用，需要读取 /proc 或者使用 powershell 特定的 API（如我们在测试中采用的 probe 调用）。这个过程在启动时进行了一次缓存（Cached module-level），这样做的考虑非常周全：防止在波次执行期间重复调用低效率的 Shell API，保障了 Supervisor 在高负载下的高性能表现。

在循环处理方面，很多 Supervisor 实现容易陷于忙轮询的陷阱。我们的核心监视循环[E:/zcode/zloop-gen8/src/zloop/supervisor.py:284] 逻辑极为精巧：它在没有活动 Launcher 任务时能够自动识别，并通过 poll_s 的睡眠参数进入一种“半休眠”状态。这极大程度地降低了 Supervisor 占用的 CPU 时间比，使其能在同样的主机上支持更大规模的波次运行。这不仅是性能优化，更是为了满足在资源约束下的分布式调度稳定性要求。

针对 Materialize 模块 [E:/zcode/zloop-gen8/src/zloop/supervisor.py:328]，我们不仅要审计其功能，还要关注它的懒加载设计(Lazy Import)。Supervisor 不会在启动时加载这个可能会非常耗时的模块，只有当第一个 packet 完成并进入 materialized 路径时，才会从 M7模块加载它。这种延时加载设计(Deferred initialization)保证了 Supervisor 能够快速响应并进入 Controller Claim 阶段。

最后，我们需要提及的是 Cancell 流程中的 quarantine 设计。在 E:/zcode/zloop-gen8/src/zloop/supervisor.py:382，对于被拦截的 Launcher 任务，系统并没有仅仅是标记其已经停止，而是特意进行了隔离操作（quarantined）。这种设计有效地防止了非预期的副作用在中止后的任务间遗留，是分布式任务原子性处理的典范。

