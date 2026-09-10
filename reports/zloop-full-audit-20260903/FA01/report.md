# 安装入口全审计报告 (FA01)

## 1. 概述
对 ZLoop 工程 (zcode-loop-orchestra) 的 pyproject.toml、README.md 及 README.zh-CN.md 进行静态审计，评估其在全新环境下的启动与安装阻断点。

## 2. 命令入口矩阵
| 命令 | 描述 | 入口 |
| :--- | :--- | :--- |
| zloop doctor | 环境自检 | zloop.cli:main |
| zloop project attach | 纳管当前项目 | zloop.cli:main |
| zloop install | 安装用户级钩子 | zloop.cli:main |
| zloop run start | 启动业务任务 | zloop.cli:main |
| zloop stage begin | 开启阶段 | zloop.cli:main |
| zloop wave propose | 提议并发任务包 | zloop.cli:main |
| zloop c2c prepare | 准备审计包 | zloop.cli:main |
| zloop c2c record | 录入审计证据 | zloop.cli:main |
| zloop wave start | 开始并发 wave | zloop.cli:main |
| zloop stage promote | 快速晋升 | zloop.cli:main |

## 3. 依赖状态
来自 pyproject.toml:
- 核心依赖: dependencies = [] (Line 10) - **极高风险**
- 可选依赖: codex (openai-codex), dev (pytest) - (Line 13-14)

## 4. 关键阻断与冲突 (Blocking Issues)
1. **依赖缺失 (严重/Critical)**: pyproject.toml (Line 10) 中 project.dependencies 为空。若核心逻辑（如后端调度）需要第三方库，安装将无法启动任务，导致运行时 ImportError。
2. **Python 版本门禁冲突**:
   - README.md (Line 14) 要求 Python 3.14+。
   - pyproject.toml (Line 8) 要求 requires-python = ">=3.11"。
   此处 README 的硬性门禁高于工程配置，若用户使用 3.11-3.13，可能遭遇 README 门禁拦截或兼容性 Crash。
3. **平台路径硬编码 (潜在风险)**: README.md (Line 29) 使用 Windows 路径格式进行安装命令 (pip install -e ".[dev]")。在非 Windows 环境或复杂 Shell 环境下，此安装路径可能解析失败。

## 5. 结论
工程在安装入口处存在 dependencies 列表缺失的致命空洞，以及 Python 版本管理的配置与说明冲突。需在合并安装流前修正 pyproject.toml 中的核心依赖及版本对齐。
