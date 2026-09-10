# Goal续接机制调查报告

## 1. /goal 指令现状
* **现象**: 指令 /goal 在整个工作区及相关Codex配置中不存在。
* **证据**: 执行 g  /goal E:/codex-LOOP/codex-loop-s-f2 C:/Users/hzq00/.codex 结果仅指向本次任务附件及历史运行日志，无CLI或插件实现。
* **结论**: /goal 为过时描述或方案幻觉。

## 2. 真实续接机制
* **机制**: Stop hook。
* **限制**: 官方规范明确上限为 3 次 continuation。
* **替代方案**: 采用会话内多轮对话以维持上下文。

## 3. 核验方法
* 全域搜索: 使用 g 工具对全空间搜索 /goal 定义。
* 规范文档: 查阅 E:\zcode\zcode-loop-orchestra\spec\VOL-05-HOOK-BINDING.md 中关于 hook 的定义。

