# Coding output contract

Coding 阶段稳定保存和展示：

- `code`：外层系统从隔离工作区回收的实际源码。
- `implementation_summary`：面向用户说明工具功能、已完成的业务模块和功能测试，并用具体实现或测试证据说明需求、Design、实现如何对应。不要只写“保持一致”，不要重复工具介绍，也不要出现源码文件名或服务器路径。

`tool_contract` 只供系统注册工具。合法空结果和零命中可以是正常运行，业务效果由用户判断。

`tool_contract` 只在最终结果中输出一次；中间进度不要重复生成或改写它。

新实现可在最终结果中沿用或校准 Design 的轻量 `finance_tool_profile`：协议为 `finance_tool_profile.v1`；`family` 以主要输出职责区分 `information`、`analytics` 和 `strategy`；`execution_shape` 为 `aggregate_context`、`entity_local`、`cross_sectional` 或 `portfolio_stateful`；`output_semantic` 为 `facts`、`metric`、`series`、`assessment`、`ranked_selection`、`signal`、`portfolio_target` 或 `action_receipt`；可选 `summary` 只简述用途。旧 payload 省略该字段仍然兼容。指标、评分、诊断或解释默认是 `analytics`，只有直接输出入选、完整排名、买卖/持有信号或目标权重才是 `strategy`。当前不生成可执行 `action` 工具。

只有 `family=strategy` 的工具才额外输出 `strategy_runtime_profile`，由系统校验并作为修订伴随契约保存；`information` 和 `analytics` 必须省略。它不属于公开输入，不得加入 `single/list/market`、市场展开、通用循环、日期归一化或调度参数。逐股独立策略绑定 `string` 证券字段；横截面或组合策略绑定 `array` 且声明 `items_type=string`，由 Wrapper 注入点时 universe。

只有一次返回完整有序证券列表、且能够安全按历史截止日执行的日频策略，才额外输出 `selection_output_profile`。该字段只是当前组合回测桥的结果映射，不代表绕过权限、固定修订号或 point-in-time 预检。路径映射 Host 的标准结果信封，通常是 `data.<公开输出字段>`；不要为此让业务输出重复嵌套 `data`。逐股、分钟级或常驻状态策略必须省略。

真实运行结果属于运行器事实，直接持久化并交给界面展示，不经过主会话重新组织或判断。

Coding 按 Design 中必要的逻辑模块组推进；Design 未明确拆分时按实现需要选择。每组完成后写入动态模块并编译、做聚焦功能测试，再用一句自然语言报告真实的业务模块或指标名称、完成能力和测试动作；不要用“核心模块”“实现阶段”“聚焦验证”等泛化话术。中间不输出最终协议、源码、文件名或服务器路径，也不等待用户逐段确认。只有最终回答使用 Output Schema。

至少保留一个代表性功能测试，并将实际证据写入 `scratch/test_evidence.json`：

```json
{"cases": [{"input": {}, "actual": {}}]}
```

`actual` 直接使用工具原始返回并包含 `key_process_info`。该证据由系统直接回收和展示，不由外层模型重写；缺失时不阻断 Coding。
