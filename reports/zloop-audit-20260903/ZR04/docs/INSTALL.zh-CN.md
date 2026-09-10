# ZCode-ZLoop 安装与环境配置指南 (Windows x64)

本文档详细说明如何在本地 Windows 环境下完成 ZCode-ZLoop Orchestra 的全套部署、Hooks 挂载与子代理配置。

## 1. 运行环境要求

- **操作系统**：Windows 10 / 11 64位 (建议开启 WSL2 支持)
- **Git**：2.40+ (已配置 Git Bash，支持 longpaths)
- **Python**：Python 3.14+ (需带有标准库 `sqlite3`)
- **ZCode 客户端**：v3.10.2 或更高版本

## 2. 安装步骤

### 步骤一：克隆代码与虚拟环境配置

```bash
git clone https://github.com/LEO001020/zcode-loop-orchestra.git
cd zcode-loop-orchestra

# 建立专属虚拟环境
python -m venv .venv

# 安装可编辑依赖
.venv/Scripts/python -m pip install -e ".[dev]"
```

### 步骤二：环境诊断 (Doctor)

运行 ZLoop 环境自检：

```bash
.venv/Scripts/python -m zloop.cli doctor
```

预期输出：
- `data root`: `C:\Users\<username>\.zloop`
- `journal profile`: `{"journal_mode": "DELETE", "synchronous": "EXTRA", "wal_ok": false...}`
- `hooks`: `{"hooks_enabled": true...}`

### 步骤三：工程纳管与 Hooks 安装

```bash
# 1. 将当前工程目录纳管入数据面
.venv/Scripts/python -m zloop.cli project attach

# 2. 安装 ZCode 进程级 Hooks
.venv/Scripts/python -m zloop.cli install
```

### 步骤四：部署精简子代理 Profiles

执行以下命令将 Profile 复制到 ZCode 的官方扫描根目录：

```bash
mkdir -p "$USERPROFILE/.zcode/agents"
cp plugin/agents/zloop-worker.md "$USERPROFILE/.zcode/agents/"
cp plugin/agents/zloop-auditor.md "$USERPROFILE/.zcode/agents/"
```

验证 `zloop-auditor.md` 中的 `model:` 声明为你的独立模型或网关全限定路径：
```yaml
model: af9697f5-a1f2-4616-8350-e14311d14fda/glm-5.3
```

### 步骤五：验证安装结果

运行全量测试套件确认无任何异常：

```bash
.venv/Scripts/python -m pytest tests -q
# 预期输出: 293 passed, 2 skipped
```
