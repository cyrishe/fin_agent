# Custom tool storage protocol

自定义工具没有用户可见文件。系统把它作为动态运行时资产保存：

- `aiia_runtime_artifact`：稳定工具身份、所有者、生命周期和当前修订号。
- `aiia_runtime_artifact_revision`：每次 Coding 产生的不可变实现快照；`spec_json` 保存数据库模块、确认的 Design、Design 版本指纹、系统反馈证据、拟执行测试和样例输入。
- `aiia_custom_tool_test_run`：每次实际测试的输入、输出和三道门禁证据。

动态模块可通过 `custom_tool_sdk.info/debug` 写入少量结构化执行证据。日志由沙箱输出通道单独收集，不属于工具公开输出；debug 测试界面展示核心中间指标，普通工具调用仍以业务结果为主。

生命周期只保留存储事实 `draft -> active`。Coding 创建 draft；真实技术执行和输出契约通过后，用户可以确认启用。业务逻辑是否符合需求由测试结果交给用户判断，不由系统设置业务门禁。

可见性默认 `personal`，所有者存于 `owner`，可见性与发布证据存于 `source_manifest_json`。普通“启用”不改变可见性。`public` 必须由独立动作设置，且调用者必须具有 `custom_tool:publish` 权限；模型输出和普通确认按钮都不能发布。

Design 反馈原文由系统按回合保存，并随实现修订写入 `design_feedback_evidence`；模型不负责复述或保存历史。
