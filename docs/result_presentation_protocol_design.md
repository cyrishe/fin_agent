# 结果组合与展示协议设计

## 目标

在现有 `tool -> final_output -> render_payload -> 前端渲染` 链路上，补一层更稳定的“结果组合与展示协议”。

目标不是把结果层完全交给提示词，也不是继续让前端猜结构，而是建立：

1. 固定的展示协议
2. 明确的声明层
3. 可选的转换层
4. 轻量的组合编排层

这层要满足：

- 工具输出尽量直接进入统一协议
- skill 和 planner 可以声明展示偏好
- 自动推断存在，但不是唯一主路径
- 大模型只做弱决策，不做底层解析
- 支持回退，不强制每次都走完整链路

---

## 当前仓库基础

当前仓库已经有三块可直接复用的基础，不是从零开始。

### 1. 已有 `render_block` 协议

文件：
- [render_block_protocol.md](/Volumes/ext/stock_agent/docs/render_block_protocol.md)

当前已经固定支持：

- `line`
- `pie`
- `bar`
- `kline`
- `flow`
- `table`
- `metric_strip`
- `structured_text`

这意味着“图形协议”本身已经有雏形，后续重点不是重新发明图表格式，而是把这套协议上升成全链路主协议。

### 2. 已有工具侧声明能力

当前仓库已有两种声明方式：

1. tool 直接输出 `render_blocks`
2. tool spec 对字段声明 `render_type`

相关位置：
- [tool_plan_runtime_service.py](/Volumes/ext/stock_agent/src/services/tool_plan_runtime_service.py)
- [stock_history_kline.spec.json](/Volumes/ext/stock_agent/src/tools/specs/stock_history_kline.spec.json)
- [stock_intraday_kline.spec.json](/Volumes/ext/stock_agent/src/tools/specs/stock_intraday_kline.spec.json)
- [stock_realtime_quote.spec.json](/Volumes/ext/stock_agent/src/tools/specs/stock_realtime_quote.spec.json)

这说明“先由 tool 提供展示意图”这条主路径已经成立。

### 3. 已有展示 contract 编译器雏形

文件：
- [display_contract_compiler.py](/Volumes/ext/stock_agent/src/services/display_contract_compiler.py)

当前它已经做了几件对本设计非常有价值的事：

- 将工具映射到 section kind
- 为不同 section 提供 block type priority
- 维护 `passthrough_bindings`
- 输出较稳定的 `render_payload` schema

这意味着新的结果展示层不应绕开它，而应以它为基座继续发展。

---

## 参考仓库调研

这部分不追求“照搬”，而是抽象出可借鉴的设计思想。

### A. `hermes-agent`

关键文件：
- [AGENTS.md](/Volumes/ext/ref_repos/hermes-agent/AGENTS.md)
- [agent/display.py](/Volumes/ext/ref_repos/hermes-agent/agent/display.py)

可借鉴点：

1. **展示层和执行层明确分离**
   - `display.py` 只负责展示，不负责业务逻辑
   - tool preview、spinner、diff preview 都是“展示职能”

2. **过程态与结果态分层**
   - thinking / waiting / tool preview / final response 各自独立
   - 不是把所有运行对象混成一个终态文本

3. **轻量预览优先**
   - `build_tool_preview()` 的设计说明：先给简明预览，而不是先 dump 全量结果

对我们的启发：

- `思考过程 / 执行进展 / 正式结果` 的分层方向是对的
- 结果区也应进一步分层，而不是只放“最终结果 + 结构化展示”
- 展示层应有独立 contract，不应直接绑死在 tool 原始输出上

### B. `openclaw`

关键文件：
- [extensions/diffs/src/tool.ts](/Volumes/ext/ref_repos/openclaw/extensions/diffs/src/tool.ts)
- [extensions/diffs/src/types.ts](/Volumes/ext/ref_repos/openclaw/extensions/diffs/src/types.ts)
- [extensions/diffs/src/render.ts](/Volumes/ext/ref_repos/openclaw/extensions/diffs/src/render.ts)

可借鉴点：

1. **presentation defaults 是显式类型**
   - `DiffPresentationDefaults`
   - `DiffRenderOptions`
   - `DiffViewerPayload`

2. **tool 输出的是 artifact/viewer contract，不是页面 HTML**
   - tool 负责提供可展示工件
   - renderer/viewer 负责按 contract 展示

3. **渲染分 viewer / image / file 多模式**
   - 同一份核心数据，支持不同展示载体

对我们的启发：

- 我们也应有显式的 presentation contract
- tool/adapter 输出应是“规范化展示工件”，不是拼接后的最终页面
- 未来可以支持同一 `render_block` 同时服务于：
  - 会话页
  - 独立 viewer
  - 导出报告

### C. `open-webui`

关键文件：
- [middleware.py](/Volumes/ext/ref_repos/open-webui/backend/open_webui/utils/middleware.py)

可借鉴点：

1. **事件流中间层负责整理输出项**
   - message
   - function_call
   - function_call_output
   - reasoning
   - code_interpreter

2. **按 item type 渲染，而不是把所有内容拼成一坨**
   - 工具执行和结果用 `<details>`
   - reasoning 也是独立层

对我们的启发：

- 我们应继续坚持 item/section 分层，不把 tool result 当普通文本
- 结果组合层应基于 typed item / typed block，而不是只基于字符串摘要

### D. `openai-agents-python`

关键文件：
- [items.py](/Volumes/ext/ref_repos/openai-agents-python/src/agents/items.py)
- [result.py](/Volumes/ext/ref_repos/openai-agents-python/src/agents/result.py)

可借鉴点：

1. **run item 类型化**
   - `MessageOutputItem`
   - `ToolSearchCallItem`
   - `ToolSearchOutputItem`
   - 以及 stream event / result state 的明确分离

2. **streaming result / final result 分离**
   - event queue
   - run state
   - final result

对我们的启发：

- 我们的结果区最好也分为：
  - 过程项
  - 工具结果项
  - 组合后的展示项
- 不应只保留 `final_output` 一个大对象承担所有职责

---

## 我们自己的特点与优势

我们不应该把上述实现原样搬进来。当前仓库有几个自己的优势，应该保留并强化。

### 1. 工具输出已经逐步标准化

我们已经把多类工具拆成：

- `stock_realtime_quote`
- `stock_history_kline`
- `stock_intraday_kline`
- `stock_realtime_funds_flow`
- `stock_history_funds_flow`
- `stock_industry_funds_flow`
- `market_realtime_breadth`
- `market_history_amount`
- `market_minute_amount_series`

这些工具已经比老的综合工具更“单一、垂直、精简”。这非常适合直接接展示协议。

### 2. `render_blocks` 已经进入真实 tool 输出

这意味着我们的展示协议可以走“tool-first”路线，而不是必须全靠后处理层猜。

### 3. 前端已经具备多种 block renderer

当前会话页已经能稳定渲染：

- line
- pie
- bar
- flow
- kline
- table
- metric_strip

所以我们的主要工作，不是从零造 viewer，而是把输入 contract 变得稳定。

### 4. 我们有 `display_contract_compiler`

这一点比很多参考实现更适合走“先组合、后渲染”的路线。它可以承担：

- section 分类
- preferred block types
- passthrough bindings

未来只需要再加上：

- adapter
- composer

就能形成完整链路。

---

## 建议的总体架构

建议把结果层收成 4 层。

### 第 1 层：协议层

固定前端支持的 block 类型：

- `line`
- `pie`
- `bar`
- `kline`
- `flow`
- `table`
- `metric_strip`
- `structured_text`

顶层统一结构：

```json
{
  "render_blocks": [
    {
      "type": "line|pie|bar|kline|flow|table|metric_strip|structured_text",
      "title": "标题",
      "data": {},
      "meta": {
        "source": "",
        "url": "",
        "unit": ""
      }
    }
  ]
}
```

这是稳定边界，不应再由前端自行扩展历史兼容格式。

### 第 2 层：声明层

声明优先级建议如下：

1. tool 输出里的 `render_blocks`
2. tool spec 里的字段级 `render_type`
3. skill 的展示偏好
4. planner/prompt 中的展示 hint
5. auto 规则归类

建议 skill 后续新增：

```json
{
  "presentation": {
    "preferred_blocks": ["metric_strip", "line", "table"]
  }
}
```

建议 planner/prompt 最终落为：

```json
{
  "presentation_hints": {
    "preferred_render_types": ["pie", "flow"]
  }
}
```

### 第 3 层：转换层

不是所有 tool 都必须自己输出 `render_blocks`。因此需要一层隐藏 adapter。

建议只做 3 类：

1. `render_block_adapter`
   - 任意结构化数据 -> 标准 `render_block`

2. `table_to_chart_adapter`
   - table/list -> `line / bar / pie`

3. `presentation_layout_adapter`
   - 多个 `render_blocks` + `final_output` -> section contract

这些 adapter 应该是隐藏工具或隐藏 transform，不参与普通 tool 候选召回。

### 第 4 层：组合编排层

这层负责结果的 section 排序与布局，不负责图表数据转换。

输入：

- `final_output`
- `render_blocks`
- `reference_materials`
- `display_contract`
- `presentation_hints`

输出一个更小的 contract，例如：

```json
{
  "hero": {
    "title": "贵州茅台今日行情与资金概览",
    "summary": "股价震荡偏弱，资金净流出为主。"
  },
  "sections": [
    {
      "section_type": "key_metrics",
      "title": "核心指标",
      "sources": ["stock_realtime_quote", "stock_realtime_funds_flow"]
    },
    {
      "section_type": "chart_group",
      "title": "走势变化",
      "sources": ["stock_intraday_kline", "stock_history_kline"]
    },
    {
      "section_type": "analysis",
      "title": "分析结论",
      "use_final_output": true
    }
  ],
  "references_policy": "separate"
}
```

这层才适合引入轻量 LLM 做：

- section 命名
- section 顺序
- 哪些内容进正文
- 哪些内容进参考资料

但不让 LLM 决定底层字段结构。

---

## 自动策略

`auto` 不应等于“直接问大模型”。推荐按如下优先级：

1. 已声明 `render_blocks`
2. 已声明 `render_type`
3. 规则归类：
   - 值类型
   - schema
   - list/dict 大小
   - 抽样前几项
4. 必要时才用 LLM 做组合与排序

也就是说：

- 图表类型判定尽量确定化
- LLM 只做弱决策

---

## 与当前代码的建议结合点

### A. 基于 `tool_plan_runtime_service.py`

建议新增：

- `_collect_render_candidates(...)`
- `_adapt_to_render_blocks(...)`
- `_compose_presentation_contract(...)`
- `_refine_presentation_contract_with_llm(...)` 可选

### B. 延续 `display_contract_compiler.py`

它应继续承担：

- section kind 归类
- preferred block types
- passthrough bindings

不要绕开它重做一套。

### C. 前端只按 contract 渲染

位置：
- [conversation_workbench.html](/Volumes/ext/stock_agent/src/web/templates/conversation_workbench.html)

目标不是继续“最终结果 + 结构化展示”并排，而是改成：

1. Hero
2. Key Metrics
3. Charts
4. Tables
5. Analysis
6. References

---

## 推荐落地顺序

### 第一步：正式确立展示协议

- 明确 `render_block` 为主协议
- 清理 tool/skill/planner 的展示意图入口
- 文档化各 block 的标准格式

### 第二步：补转换层

- 先实现 `render_block_adapter`
- 不启用 LLM
- 优先让更多 tool 能稳定落到统一协议

### 第三步：补组合层

- 先规则组合
- 生成 `presentation_contract`
- 前端按 contract 渲染 section

### 第四步：引入轻量 LLM

- 只负责 section 命名与排序
- 通过 flag 控制
- 关闭时仍能回退到规则组合

---

## 风险与回退

这层必须具备回退能力。

建议提供至少两个 flag：

- `RESULT_BLOCK_ADAPTER_ENABLED`
- `RESULT_COMPOSER_ENABLED`

关闭时退回：

- `final_output`
- `render_payload`
- 现有 deterministic renderer

这样可以确保：

- 新协议可以渐进接入
- 不会因为结果组合层出问题影响整体可用性

---

## 结论

本设计不是单纯增加提示词，而是建立一套更稳定的结果展示架构：

1. 固定展示协议
2. tool/skill/planner 声明展示意图
3. 隐藏 adapter 做协议转换
4. composer 做 section 编排
5. 前端只负责 contract 渲染

参考仓库给我们的启发是：

- `hermes-agent` 提醒我们要分清过程态和结果态
- `openclaw` 提醒我们要把 presentation contract 做成显式类型
- `open-webui` 提醒我们要基于 item type 分层渲染
- `openai-agents-python` 提醒我们要把 run item / stream / result 分开建模

而我们自己的优势在于：

- tool 已开始垂直化
- `render_blocks` 已经进入真实输出
- 前端已具备多 block renderer
- `display_contract_compiler` 已经有 section/block 优先级框架

所以更合理的路径不是重写一套，而是把这些已有能力收敛成统一的结果展示协议体系。
