---
name: financial-tool-coding-v1
description: 将已确认的金融工具设计契约转换为可测试、可版本化、可由系统动态加载的数据库代码模块。用于自定义金融工具进入 Coding、根据测试反馈修订实现，或发现设计不可实现并退回 Design 的场景；不用于重新发散需求或生成用户可见文件。
---

# 金融工具动态实现

## 目标

把已确认的 Design 契约实现为数据库代码模块。保持输入、输出、规则和异常语义不变；实现、测试和激活由外层系统分别完成。

## 强制边界

- 不重新定义工具目标，不新增设计未授权的功能。
- 不向用户表达文件、目录、保存路径或下载代码。
- 只输出一个入口模块，除非拆分是执行环境的硬性要求。
- 模块入口固定为 `run(inputs: dict) -> dict`，返回 JSON 可序列化对象。
- 数据访问只使用系统注入的 `custom_tool_sdk`；不得直接连接数据库、读取密钥、访问网络或执行 shell。
- 使用 `custom_tool_sdk.info` 和 `custom_tool_sdk.debug` 记录少量核心计算证据。`info` 记录阶段汇总，`debug` 记录决定最终结果的公式输入和中间指标；不要记录原始全量行情、Provider 信封或敏感信息。
- Coding 只依据已确认 Design、API Catalog 和 SDK 契约生成实现，不连接数据库、不验证数据库凭据，也不要求用户提供数据库配置。API 背后的数据库与 Provider 只在后续 Test/Run 阶段由可信宿主负责。
- `finance_query` 返回系统规范化信封；先检查 `ok`，再只从顶层 `data` 读取行数组，不解析底层 Provider 的 `result.data.rows` 等内部结构。
- 生成结果只是 draft。不得自行激活、注册或发布。
- 只有核心业务语义无法确定，或 API Catalog 明确缺少实现必需的数据能力时，才返回 `need_design_fix` 并指出最小阻塞点。

## 工作流程

1. 只读取系统标记为已确认的权威 Design 快照、输入输出契约、可用数据能力和上一版测试反馈；不要读取或重放原始用户反馈历史。
2. 判断设计是否可实现；低风险实现选择由 Coding 自行完成，只有无法正确实现核心功能时才退回 Design。
3. 按 Design 的 `modules[].functions[]` 落实精简实现计划；可以增加必要的私有辅助函数，但不得改变模块职责。
4. 生成数据库代码模块，不生成文件清单。
5. 在关键计算完成处写入结构化 `info/debug`，使样例运行可以解释最终结果；日志不改变公开返回结构。
6. 提供当前实现真正需要的测试用例；`tests[].status` 一律填 `proposed`，实际结果由外层沙箱回填。
7. 自检权限、序列化、异常处理和设计一致性。
8. 输出符合 `schema.json` 的唯一 final 对象。

## 输出纪律

- 流式过程可以输出 `codex/agent_update`、`model/analysis`、`model/code_plan`，但要按“读取设计、规划实现、生成模块、准备测试、检查边界”组织，避免原始碎片。
- 最后一条必须是 `source=model,type=final`。
- `implementation.modules` 描述数据库中的逻辑模块，不是文件。
- `implementation.modules[].functions` 声明实际实现的函数与职责，用于系统核对 Design；它不是源码切片或用户可见文件结构。
- `tests` 是拟执行测试；不得把未运行的测试写成 passed。输入与预期分别写入 `input_json`、`expected_json`，值必须是合法 JSON 对象字符串。
- `sample_input_json` 必须是可由外层解析的合法 JSON 对象字符串；不要在严格 Schema 中生成任意字段对象。
- 涉及股票、指数或基金时，样例使用真实且常见的证券代码，不使用 `TEST001` 等虚构标的；用户未指定时可自行选择一个或两个代表性标的。
- 不主动生成 `render_blocks`；展示由系统协议根据 final 确定性编译。

## 最小实现要求

- 校验 `inputs` 必须是对象，并校验必填字段和关键类型。
- 正常路径和失败路径都返回稳定结构。
- 对外部数据缺失、空结果和字段缺失给出可解释错误，不猜测结果。
- 不将样例输入硬编码为样例输出。
- 将可调整阈值集中在实现顶部或局部配置对象中，不散落魔法数字。
- 所有测试在系统真正执行前都只能是 `proposed`；Coding 输出不能作为测试通过或可启用的依据。
- 可见性固定由系统设为个人私有；Coding 不得建议或执行公开发布。

需要核对完整字段时读取 [output-contract.md](references/output-contract.md)。

## 完成条件

- `status=code_ready`：包含可执行入口模块和一个有效样例，且没有阻止核心功能正确实现的设计缺口。
- `status=need_design_fix`：不输出半成品模块；在 `design_issues` 中给出最少且可回答的问题。只有 API Catalog 缺少设计必需的 API/字段等设计能力缺口才能据此退回；数据库连接或凭据故障属于运行环境问题，不能退回 Design。
