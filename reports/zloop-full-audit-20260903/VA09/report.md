# 研究通道重审取证报告 - VA09

## 1. 结论
调用图分析显示，zloop 环境中的 kimi_server_up 检查（基于 KIMI_HEALTHZ_URL 环境配置）与 supervisor 的 wave 启动逻辑存在耦合。若 kimi web 未正确停止，supervisor 会触发 D-17/P-SEC1 安全阻断，导致 worker wave 启动失败。

## 2. 证据路径
- **调用图与耦合点**:
  - E:/zcode/zloop-gen8/src/zloop/supervisor.py:90: 定义了 kimi_server_up 检查逻辑。
  - E:/zcode/zloop-gen8/src/zloop/supervisor.py:239: un_wave 在 claim Controller 前调用 kimi_server_up 进行安全阻断。
- **环境配置**:
  - E:/zcode/zloop-gen8/src/zloop/supervisor.py:72: KIMI_HEALTHZ_URL 默认指向 http://127.0.0.1:58627/api/v1/healthz。
- **可达性**:
  - 静态分析表明该接口若在 kimi web 启动时可达，则会触发阻断，无法启动 worker wave。此处为明显的接口错配/环境依赖，需确保 WAVE 启动前完全封闭该端口。

## 3. 未知
- 暂未验证在特定并发竞态条件下，roker 的自动 fallback 是否会因端口占用情况导致对 kimi web 误触发。

