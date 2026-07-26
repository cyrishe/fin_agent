---
name: financial-tool-implementation
description: 根据需求、设计和真实反馈，实现或修改可动态执行的金融工具。
---

# 金融工具代码实现

## 专业背景

以熟悉金融数据和 Python 动态执行的高级开发工程师视角完成实现。

## 平台差异

- 这是系统保存和动态加载的代码模块，不是交付给用户的代码文件。源码写入 `CONTEXT.current_implementation.module_files`，由外层系统回收和保存。
- 固定入口为 `run(inputs: dict) -> dict`。
- 金融数据只通过 `custom_tool_sdk.finance_query` 获取；具体能力按 `references/coding.md` 定位 API Catalog。
- 工具输出遵循 Design，并包含 `key_process_info`，保存解释本次结果所需的核心中间结构、指标名称和值。

## 成功指标

只有两项：

1. 按照需求完成代码并真实跑通。空结果、零命中或数据不足可以是正常结果；不判断策略是否有效。
2. 静态检查需求、Design 和代码的输入、输出、核心逻辑与数据范围一致，并给出核心证据和结论。

任一项未满足，就在当前 Coding 会话和同一工作区中根据新增错误或反馈继续修复、复测。两项都满足后，再一次性输出最新版结果。

## Coding 阶段输出

- `code`：系统从模块文件回收的实际源码。
- `implementation_summary`：简要说明实现内容、核心函数，以及需求、Design、代码一致性的证据和结论。
- `execution_examples`：少量真实运行样例，每项以 JSON 文本保存工具 input 和完整 output；output 包含业务结果和 `key_process_info`。

系统注册工具还需要内部 `tool_contract`，只描述工具名称和公开输入输出。
