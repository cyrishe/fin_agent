# Skill Runtime

`skill_runtime` 负责执行基于 `SKILL.md + schema.json` 的业务 skill。

第一版目标：

- 读取 skill 定义
- 注入可用 tools 及其 schema
- 运行一个有限步数的单 agent loop
- 支持模型在每一步选择：
  - 调用一个 tool
  - 直接提交最终结果
- 对最终结果做 schema 校验

当前不做的事情：

- 多 agent
- 图式编排
- checkpoint 持久化
- 人工介入节点
- 框架绑定

核心模块：

- `models.py`: runtime 数据结构
- `tool_adapter.py`: tools 注入与执行
- `tool_selector.py`: tools 选择策略（strict / auto / free）
- `schema_validator.py`: 最小 schema 校验器
- `prompt_builder.py`: prompt 拼装
- `agent_loop.py`: 单 agent loop
- `runner.py`: 对外入口
