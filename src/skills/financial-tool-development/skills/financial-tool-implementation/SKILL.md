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

开始实现时，根据 Design 划分必要的逻辑模块组；如果 Design 没有明确拆分，就按实现需要自行选择。用简短自然语言说明准备先完成什么，随后逐组推进：完成一个逻辑组后先写入动态模块、编译并做聚焦功能测试，再继续下一个逻辑组。

每个逻辑组完成时，用一句自然语言报告真实的业务模块或指标名称、正在处理或已完成的能力、涉及的数据接口或测试动作。例如“正在实现 MACD 金叉判断”“金叉窗口计算已完成，正在接入行情数据”。不要使用“核心模块”“实现阶段”“聚焦验证”等无法说明当前工具内容的泛化话术。

中间不要输出 `tool_contract`、`implementation_summary`、源码、文件名或服务器路径，也不要求用户逐段确认。只有最终回答使用 Output Schema。

功能测试至少保留一个代表性 case。无论业务结果是否命中，都把该 case 的真实输入和真实工具输出写入 `scratch/test_evidence.json`：

```json
{"cases": [{"input": {}, "actual": {}}]}
```

可以保存一个或多个代表性 case。`actual` 是工具本次运行的原始返回并包含 `key_process_info`，不要重新组织。该文件只用于系统回收、保存和展示测试证据；缺失或无法解析时不改变 Coding 结果。

## Coding 阶段输出

- `code`：系统从模块文件回收的实际源码。
- `implementation_summary`：面向用户说明工具功能、实际完成的业务模块、功能测试，以及需求、Design、实现的一致性。对齐说明必须指出具体需求如何落实到实现或测试证据，不能只写“保持一致”或重复工具介绍；不得出现源码文件名或服务器路径。

系统注册工具还需要内部 `tool_contract`，只描述工具名称和公开输入输出。

新实现可以沿用并在必要时校准 Design 的轻量 `finance_tool_profile`。它使用 `finance_tool_profile.v1`，记录主要工具族、执行形态、输出语义和一句可选摘要，是系统检索、展示及选择运行伴随契约所需的信息，不是公开输入；旧实现省略该字段仍然兼容。

- 以最终输出职责选择 `information`、`analytics` 或 `strategy`。查询事实是 `information`；指标、评分、诊断和解释是 `analytics`；只有直接输出入选结果、完整排名、买卖/持有信号或目标权重才是 `strategy`。阈值、股票输入或评分计算本身不能把工具升级为策略。
- `execution_shape` 只使用 `aggregate_context`、`entity_local`、`cross_sectional`、`portfolio_stateful`；`output_semantic` 只使用 `facts`、`metric`、`series`、`assessment`、`ranked_selection`、`signal`、`portfolio_target`、`action_receipt`。这些是一次业务计算的形态和主输出语义，不是公开的运行模式。
- `action` 当前只能停留在 Design，不进入可执行 Coding。若误进入本阶段，不得生成、测试或声称执行下单、撤单、通知等外部动作；应明确返回当前不支持可执行外部动作，不得用普通工具绕过该边界。

只有 `finance_tool_profile.family=strategy` 的真正策略工具才输出系统运行伴随契约；它与代码修订一起保存，但不是公开输入：

- `strategy_runtime_profile` 使用 `strategy_runtime_profile.v1`。`binding.field` 指向证券标识输入；逐股独立策略使用 `string`，必须横向比较或共享组合状态的策略优先使用 `array` 且 `items_type=string`，由 Wrapper 一次注入点时 universe。不要为此新增 `single/list/market`、市场展开、通用循环、公开 `as_of` 或调度参数。
- `required_history_sessions` 是计算所需的预热交易日，`default_run_sessions` 是默认评价区间；交易日解析、非交易日回退、市场成分和有界并行由 Wrapper 完成。
- 只有无状态、使用完整点时 universe、一次返回明确有序证券列表且适配当前日频回测的选股策略，才输出 `selection_output_profile`。逐股 `string` 策略、分钟级盘中策略或需要常驻状态的策略不得输出该字段，也不得宣称已可组合回测。
- `selection_output_profile` 映射运行 Host 的标准结果信封，因此候选与日期路径通常写成 `data.<公开输出字段>`；`data` 只表示 Host 信封，不要为了适配回测再让业务输出额外套一层同名 `data`。日期字段表示本次结果实际使用的数据截止日。
- `information` 和 `analytics` 不输出这两个策略字段。策略修改若未改变执行形态，保持已有伴随契约；不要为了展示重复发明运行状态。`family=strategy` 时必须同时输出 `strategy_runtime_profile`，而非策略工具不得附带策略伴随契约。

动态工具仍是一次性 `run(inputs)`。持续监控、定时运行、通知和批量/市场调度由外围系统负责；如果当前运行环境不能强制历史截止日，必须在 `implementation_summary` 如实说明尚不能安全历史回放，不能在代码中用当前数据伪装回测。

实现过程中不要反复重写 `tool_contract`。中间只报告必要的进度；完成实现、编译和聚焦测试后，在最终结果中输出一次最新版 `tool_contract` 和 `implementation_summary`。

真实运行的输入、输出和过程信息由运行器直接保存和展示，不要在最终回复中重新编码、整理或判断。
