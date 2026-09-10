
# C2C 适配测试方案审计结论

## 1. 测试文件审查
- 已审查 E:\zcode\zloop-gen8\tests\test_c2c.py 和 E:\zcode\zloop-gen8\tests\test_c2c_gate.py。
- 确认现行测试逻辑依赖于 zloop.c2c 和 zloop.db 模块。
- 模拟测试环境 fixture 正确使用了临时文件系统。

## 2. 模拟边界与断言
- 建议将 mock 边界定义在 zloop.c2c.prepare_c2c 函数输入参数中。
- 断言应检查 events 表中是否存在 c2c_prepared 和 c2c_recorded 标记事件。

## 3. 人工 E2E 验收步骤
1. 设置测试项目。
2. 触发 wave start。
3. 验证 stage 中 promote 操作是否触发了 C2C 审计包准备。
4. 人工检查输出记录。

## 4. 回滚建议
- 每次测试后清理 ZLOOP_DATA 环境变量指向的临时目录。

