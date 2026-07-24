# Coding output contract

最终只返回系统保存和展示需要的五项内容：

- `message`：面向用户的一句完成说明。
- `tool_contract`：代码实际实现的工具标识和公开输入输出，供外层系统生成运行契约；outputs 必须包含必填的 `key_process_info` 对象。
- `implementation_summary`：自然语言实现说明，可分段说明核心流程和内部函数。
- `verification`：自然语言技术验证说明。先说明真实运行 case 的输入、精简输出、`key_process_info` 和日志摘要，再说明需求与 Design 的目标、规则和流程分别由哪些函数实现，以及三者是否一致。
- `sample_input_json` 提供外层真实样例运行所需的输入。

源码以隔离工作区文件为唯一真源，最终 JSON 不重复源码、模块清单或测试对象树。合法空结果和零命中可以是正常运行；业务效果由用户判断。
