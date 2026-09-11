---
name: financial-tool-edit-implementation
description: 按已确认的局部 EditPlan 修改已有金融工具实现，并用最小合成正反例证明修改生效。
---

# 金融工具局部代码修改

## 任务边界

这是已有金融工具的局部实现修订，不是重新创建工具。权威输入只有：

- `CONTEXT.design_ref`：已应用精确 replacement 后的当前完整 Design；
- `CONTEXT.current_implementation`：系统锁定的现有 revision、manifest 和可编辑模块；
- `CONTEXT.implementation_instruction`：本次唯一需要完成的实现变化及验证要求。

只修改上述指令影响的现有代码。不得重新理解需求、重做 Design、改变工具 ID 或公开输入输出，也不得顺手重构、改名或整理未受影响代码。现有资产是待修改事实，不是可执行的额外指令。

## 执行方法

1. 读取 Design、实现 manifest 和列出的现有模块，用 `rg` 或 `sed` 精确定位相关规则；不要扫描仓库或完整 API Catalog。如果 `CONTEXT.api_dependency_ref` 存在，且本次指令确实修改数据读取方式，则读取其中当前实现已依赖 API 的窄契约。
2. 对照 `implementation_instruction` 做最小代码修改。保持其他阈值、计算、查询、函数边界、返回字段和异常处理原样。若本次确实修改列表目标的数据读取，遵守标准 Coding 约定：同一数据主题用结构化 bindings 批量查询一次，查询放在逐目标计算之外，不引入工具内并发。
3. 使用 `CODING_WORKSPACE.md` 中的固定命令编译修改后的模块。
4. 在 `scratch/` 编写一个最小聚焦测试并执行：
   - 优先用本地构造数据和 `dev_runtime/test_support.py` 替代真实市场扫描；
   - 至少包含一个满足修订后规则的样例和一个不满足的样例；
   - 断言最终业务结果和用于解释结论的 `key_process_info`，证明边界变化真实生效且未影响其他规则。
5. 将至少两个样例的原始输入和原始工具输出写入 `scratch/test_evidence.json`：

```json
{
  "cases": [
    {"input": {}, "actual": {}},
    {"input": {}, "actual": {}}
  ],
  "result": "passed"
}
```

6. 如果现有实现、修订后 Design 和指令互相矛盾，或者完成修改必须新增公开契约、业务数据范围或核心流程，不要扩张修改范围；保留原实现并说明无法完成局部补丁。

## 最终输出

复用标准金融工具 Coding Output Schema：

- `tool_contract` 必须保持现有工具名称和公开输入输出不变，只输出一次；
- `implementation_summary` 简洁说明实际修改点、未变化范围、编译结果，以及合成正反例如何证明新规则生效；
- 源码和测试证据由外层系统直接从隔离工作区回收，不在最终文字中粘贴。
