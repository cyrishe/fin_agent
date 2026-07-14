# Coding final contract

`final` 是模型到系统的实现提案，不是测试或激活证明。

```json
{
  "source": "model",
  "type": "final",
  "status": "code_ready",
  "message": "实现草稿已生成，等待系统测试。",
  "implementation": {
    "summary": "实现连续交易日上涨判断。",
    "entry_module": "main",
    "modules": [
      {
        "module_id": "main",
        "role": "输入校验、数据查询和规则判断入口",
        "language": "python",
        "entrypoint": "run",
        "functions": [
          {"name": "validate_inputs", "responsibility": "校验公开输入。"},
          {"name": "evaluate_signal", "responsibility": "执行设计中的确定性规则。"},
          {"name": "run", "responsibility": "组织调用并返回稳定业务结果。"}
        ],
        "source_code": "def run(inputs: dict) -> dict:\n    ..."
      }
    ]
  },
  "tests": [
    {
      "test_id": "three_days_up",
      "category": "happy_path",
      "status": "proposed",
      "input_json": "{\"stock_code\":\"600000.SH\"}",
      "expected_json": "{\"matched\":true}",
      "purpose": "验证连续三日上涨。"
    }
  ],
  "sample_input_json": "{\"stock_code\":\"600000.SH\"}",
  "implementation_notes": [],
  "design_issues": [],
  "risks": []
}
```

约束：

- `code_ready` 必须包含 `entry_module` 指向的可执行模块。
- 当前运行时使用一个动态入口模块；内部职责可由同一模块中的函数承载。
- `functions` 说明实际内部函数职责，并包含入口 `run`；不复制源码。
- `need_design_fix` 时 `implementation.modules` 和 `tests` 可以为空，但 `design_issues` 不得为空。
- `tests[].expected_json` 只写可稳定断言的业务字段，不断言时间、内部路径或诊断文本。
