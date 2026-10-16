# Tool / Skill 输入编排

## 目标

用户可以通过以下方式明确调用一个已经存在的 Tool 或可执行 Skill：

- `$stock_realtime_quote 贵州茅台 五粮液`
- `$stock_realtime_quote 请处理附件中的公司`
- 前端传入 `selected_asset`，同时附带自然语言和附件

Tool 与 Skill 只声明自己的业务输入 Schema，不负责理解 Excel、TXT、DOCX、会话附件或批量调度。

## 主线

```text
用户文字 / 附件（SOFT）
  -> InvocationInputResolverService 解析附件并建立受控输入索引
  -> AssetInvocationService 读取目标资产合同和附件摘要
  -> LLM 识别字段、对象集合或表格列（SOFT）
  -> 系统按 Schema 展开、去重、标准化并生成 calls（HARD）
  -> Tool work items / Skill jobs
  -> 执行预览和结果表达（SOFT）
```

显式 `$asset`、`selected_asset`，或句首“请用/调用 `<asset_name>` 工具（Skill）”已经固定目标资产，因此这一层不重新做顶层意图分类，也不替换目标 Tool / Skill。

没有精确资产名、只按业务能力描述的自然语言仍走 normal_qa 的柔性理解，由 CC 决定是否选择资产。

## 输入来源

附件先由 `AttachmentService` 保存为受控 attachment，再由 `FileIoToolService` 解析。

- CSV / TSV / XLSX：向模型提供表头、前 20 行样本和总行数。
- TXT / DOCX 正文：向模型提供受限文本预览。
- DOCX 表格：保留受限表格预览。

附件内容始终是用户数据，不是系统指令。

表格中的完整对象列不由模型抄写。模型只返回来源选择：

```json
{
  "mode": "map",
  "item_argument": "stock",
  "items": [],
  "source": {
    "attachment_id": "att_xxx",
    "table_index": 0,
    "column": "公司"
  }
}
```

系统根据该选择从原始解析结果读取整列、删除空值、稳定去重，并保留原顺序。

## 单实体与原生批量

调用形态由目标资产的 JSON Schema 决定：

- `stock: string` + 多个公司：生成多个独立 calls，Tool / Skill 仍只处理一个对象。
- `stock_codes: array` + 多个公司：生成一个 call，列表一次传入目标资产。
- `map.items` 是参数对象：每个对象生成一个 call。

LLM 不得因为目标资产一次只支持一个对象而只取第一项。用户明确列出的对象必须完整保留，除非用户明确要求抽样或限制数量。

股票名称由 `StockIdentityResolverService` 在执行前转换为标准证券代码。无法确认的股票不会由模型猜测。

## 当前边界

- XLSX 当前读取第一张工作表，系统最多物化前 5000 行；模型只看到前 20 行样本。
- 表格来源当前支持单列对象集合；多列对象数组（例如股票代码和持仓权重）需要后续增加列映射。
- TXT / DOCX 普通段落中的实体仍由 SOFT 语义提取，不具备表格列的无损寻址。
- Tool 多 calls 已有受限并发执行；Skill 多 calls 当前提交为多个异步 job，最终批次聚合仍需独立完善。
- 只有带明确 `input_schema` 的可执行 Skill 才能获得稳定的批量编排；finance-business 方法型 Skills 继续由 CC 按 working set 使用。
- attachment 的 owner 读取校验需要继续收紧，不能仅依赖不可猜测的 attachment ID。

## 参考原则

- MCP 将文件等上下文建模为 Resource，将可执行能力建模为带 JSON Schema 的 Tool；两者不应混为一个业务工具输入。
- OpenAI Agents SDK 的 structured tool input 使用独立参数 Schema，运行时负责解析和校验。
- LangGraph 的 `Send`/map-reduce 模式把同一节点应用到动态对象集合，节点本身不需要理解集合来自文件还是对话。

Fin Agent 不直接引入这些框架；只复用其稳定边界：资源、结构化参数和批量执行分离。
