# Report Page Protocol

## 1. 目标

定义一套由后端直接输出、前端按顺序渲染的页面协议。

适用场景：

- 热点/行情总览
- 板块与个股联动分析
- 研报预测与交易线索汇总
- 单股票深度分析
- AI 生成的结构化页面

核心原则：

- 后端负责内容组织、区块顺序、字段填充
- 前端负责布局、样式、交互、降级兜底
- 未识别字段忽略，未识别块类型走通用兜底渲染
- 页面不依赖固定模板文件，优先依赖统一的 section/block/card 协议
- 多样化来自 block 组合和顺序，而不是为每种分析场景单独造模板

---

## 2. 页面根结构

```json
{
  "version": "1.0",
  "page_id": "hotspot-report-20260312-ai-compute",
  "page_type": "hotspot_report",
  "title": "AI 算力热点跟踪",
  "subtitle": "后端按协议输出，前端顺序渲染",
  "as_of": "2026-03-12 14:35:00",
  "theme": {
    "accent": "#e44c3a",
    "tone": "warm"
  },
  "summary": {
    "market_phase": "risk_on",
    "tags": ["算力", "液冷", "光模块"]
  },
  "sections": []
}
```

字段说明：

- `version`: 协议版本，便于前后端兼容
- `page_type`: 页面类型，例如 `hotspot_report`、`daily_review`
- `page_type`: 页面类型，例如 `hotspot_report`、`stock_deep_dive`
- `summary`: 页面级摘要，可用于顶部导航、分享卡片、SEO
- `sections`: 页面主体，前端按数组顺序渲染

---

## 3. 预定义大区块

建议后端统一使用以下 `section_kind`，便于前端建立稳定视觉和导航映射：

- `market_overview`: 行情区
- `market_kline`: K线技术区
- `research_prediction`: 研报预测区
- `sector_heat`: 板块热度区
- `stock_focus`: 个股重点区
- `capital_flow`: 资金区
- `news_catalyst`: 新闻催化区
- `catalyst_chain`: 事件催化链区
- `risk_watch`: 风险提示区
- `timeline_review`: 时间线区

前端不依赖这些枚举才能渲染，但会基于这些枚举给出更合适的标题、配色、锚点和图标。

---

## 4. Section 结构

```json
{
  "section_id": "market-overview",
  "section_kind": "market_overview",
  "title": "行情区",
  "description": "指数、情绪、核心方向概览",
  "layout": {
    "desktop_columns": 12,
    "mobile_columns": 1,
    "dense": false
  },
  "blocks": []
}
```

字段说明：

- `section_id`: 稳定唯一 ID
- `section_kind`: 预定义业务区块类型
- `layout.desktop_columns`: PC 端栅格列数，建议固定 12
- `layout.mobile_columns`: 移动端列数，通常为 1
- `blocks`: 区块内的内容块

---

## 5. Block 通用结构

```json
{
  "block_id": "market-score",
  "type": "metric_strip",
  "title": "市场温度",
  "span": {
    "desktop": 4,
    "tablet": 6,
    "mobile": 1
  },
  "height": "compact",
  "props": {},
  "data": {}
}
```

通用字段：

- `block_id`: 稳定唯一 ID
- `type`: 渲染器类型
- `span`: 不同端上的栅格宽度
- `height`: `compact`、`normal`、`tall`
- `props`: 样式或行为参数
- `data`: 业务数据

兼容规则：

- 前端忽略未知字段
- `span` 缺失时默认 `desktop=12, tablet=12, mobile=1`
- 遇到未知 `type` 时展示 JSON 预览，不中断整个页面

---

## 6. 推荐块类型

### 6.1 `structured_text`

用于结构化文本、摘要、观点、结论。

```json
{
  "type": "structured_text",
  "data": {
    "lead": "今天的主线仍是 AI 算力。",
    "paragraphs": [
      "核心成交集中在服务器、光模块和液冷。",
      "低位补涨开始扩散，但持续性待观察。"
    ],
    "bullets": [
      {"label": "主线", "text": "算力链强于应用链"},
      {"label": "风险", "text": "高位换手抬升"}
    ],
    "callout": {
      "tone": "warning",
      "text": "如果午后指数回落且成交放大，需要防高位分歧。"
    }
  }
}
```

### 6.2 `metric_strip`

用于指数、情绪、强弱、胜率等摘要指标。

```json
{
  "type": "metric_strip",
  "data": {
    "items": [
      {"label": "上证", "value": "3382.41", "change": "+0.68%"},
      {"label": "创业板", "value": "2198.12", "change": "+1.42%"},
      {"label": "涨停数", "value": "86", "change": "+12"},
      {"label": "炸板率", "value": "18%", "change": "-4%"}
    ]
  }
}
```

### 6.3 `table`

用于明细列表、研报预测、重点个股等。

```json
{
  "type": "table",
  "props": {
    "sticky_header": true
  },
  "data": {
    "columns": [
      {"key": "name", "label": "标的"},
      {"key": "sector", "label": "方向"},
      {"key": "rating", "label": "评级"},
      {"key": "target", "label": "目标价"},
      {"key": "reason", "label": "逻辑"}
    ],
    "rows": []
  }
}
```

### 6.4 `kline`

用于日线/分时/叠加指标图。

```json
{
  "type": "kline",
  "props": {
    "main_series": "candles",
    "overlays": ["ma5", "ma10", "ma20"]
  },
  "data": {
    "symbol": "SZ300308",
    "name": "中际旭创",
    "candles": [
      ["2026-03-06", 145.2, 149.6, 144.3, 151.1, 1832456]
    ],
    "lines": {
      "ma5": [["2026-03-06", 146.3]]
    }
  }
}
```

约定：

- `candles` 行格式：`[time, open, close, low, high, volume]`
- 前端允许只传 `candles`，不传指标

### 6.5 `flowchart`

用于事件链、催化路径、推演流程。

```json
{
  "type": "flowchart",
  "data": {
    "engine": "mermaid",
    "source": "flowchart LR\nA[政策催化] --> B[板块放量]\nB --> C[龙头加速]"
  }
}
```

### 6.6 `heatmap`

用于板块-个股热力布局图。

```json
{
  "type": "heatmap",
  "data": {
    "groups": [
      {
        "name": "算力",
        "items": [
          {"label": "中际旭创", "value": 98, "change": 8.24, "weight": 28},
          {"label": "天孚通信", "value": 82, "change": 6.11, "weight": 18}
        ]
      }
    ]
  }
}
```

约定：

- `value`: 热度/综合分
- `change`: 涨跌幅，前端用颜色映射
- `weight`: 面积权重，没有则按 `value`

### 6.7 `timeline`

用于事件时间线、新闻推进过程。

```json
{
  "type": "timeline",
  "data": {
    "items": [
      {"time": "09:31", "title": "服务器板块异动", "text": "龙头率先上板"},
      {"time": "10:12", "title": "液冷扩散", "text": "板块联动增强"}
    ]
  }
}
```

### 6.8 `insight_cards`

用于把多个关键观察点组织成卡片组，适合深度分析页的“结论摘要”“支撑逻辑”“风险提示”。

```json
{
  "type": "insight_cards",
  "data": {
    "items": [
      {"title": "阶段判断", "text": "估值修复与验证并行", "tone": "accent"},
      {"title": "核心支撑", "text": "主力回流与机构一致预期同步强化", "tone": "neutral"}
    ]
  }
}
```

### 6.9 `tabs_panel`

用于同一主题下的多维度信息切换，适合研报、资金、风险拆页式展示。

```json
{
  "type": "tabs_panel",
  "data": {
    "tabs": [
      {
        "label": "机构观点",
        "paragraph": "机构普遍认为渠道改革成效开始体现。",
        "bullets": []
      },
      {
        "label": "风险因素",
        "paragraph": "批价和淡季动销仍需继续观察。",
        "bullets": []
      }
    ]
  }
}
```

---

## 7. 前后端职责边界

后端负责：

- 决定页面有哪些 section
- 决定 section 内 block 顺序
- 输出业务文案、表格列、K 线数据、流程图源码、热力图节点
- 尽量用稳定字段，不让前端猜业务语义

前端负责：

- 根据 `type` 选择渲染器
- 根据 `span` 响应式布局
- 对未知 block 做降级
- 控制移动端折叠、横向滚动、图表高度
- 对统一 card 类 block 建立稳定视觉语言，但不把页面写死成固定模板

---

## 8. 推荐兼容策略

1. 后端新增 block 类型时，同时给出 `fallback_text`
2. 前端维护 `rendererRegistry`
3. 每个 block 渲染失败时只影响当前块，不影响整页
4. 表格、K 线、热力图都允许空数据，前端显示空态
5. 移动端优先顺序与桌面端一致，不重新洗牌

---

## 9. 本轮落地页面

本次实现对应页面：

- 页面模板：`src/web/templates/report_protocol_demo.html`
- 页面路由：`GET /report_protocol_demo`
- Demo 数据：`GET /api/report_protocol_demo`

这页用于验证：

- 协议是否足够覆盖主要内容类型
- PC/移动端布局是否顺畅
- 后续把真实热点/研报/行情数据接进来时，前端是否无需重写页面骨架

建议新增一个单股票 deep-dive demo 页面，验证：

- `stock_deep_dive` 是否也能走同一套协议
- K线、资金、研报、新闻、风险卡片是否能统一渲染
