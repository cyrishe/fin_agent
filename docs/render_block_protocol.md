# Render Block Protocol

前端展示类工具输出统一遵循 `render_block` 协议，不再让前端猜测历史字段格式。

## 顶层结构

单个 block：

```json
{
  "type": "kline|line|bar|pie|flow|table|metric_strip|structured_text",
  "title": "标题",
  "data": {},
  "meta": {
    "source": "",
    "url": "",
    "unit": ""
  }
}
```

多个 block：

```json
{
  "render_blocks": [
    {
      "type": "metric_strip",
      "title": "概览",
      "data": { "items": [] }
    }
  ]
}
```

说明：
- `type`：前端固定支持的展示类型。
- `title`：给用户看的标题。
- `data`：图形或结构化内容的标准数据体。
- `meta`：可选，主要用于溯源和单位说明。

## K线

```json
{
  "type": "kline",
  "title": "日线K线",
  "data": {
    "candles": [
      {
        "time": "2026-04-16",
        "open": 10.0,
        "close": 10.5,
        "low": 9.8,
        "high": 10.8,
        "volume": 10000,
        "pct": 1.2
      }
    ],
    "indicators": [
      {
        "name": "MA5",
        "points": [
          { "time": "2026-04-16", "value": 10.2 }
        ]
      }
    ]
  }
}
```

## 折线图

```json
{
  "type": "line",
  "title": "分时走势",
  "data": {
    "x_axis": ["09:30", "09:31", "09:32"],
    "series": [
      { "name": "价格", "data": [10.1, 10.2, 10.15] }
    ]
  }
}
```

## 柱状图

基础柱状图：

```json
{
  "type": "bar",
  "title": "大小单净额",
  "data": {
    "x_axis": ["大单", "中单", "小单"],
    "series": [
      { "name": "净额", "data": [12, 8, -3] }
    ]
  }
}
```

分组柱状图：

```json
{
  "type": "bar",
  "title": "行业主力买卖前5",
  "data": {
    "groups": [
      {
        "name": "主力买入前5",
        "x_axis": ["A", "B"],
        "data": [10, 8]
      },
      {
        "name": "主力卖出前5",
        "x_axis": ["C", "D"],
        "data": [-6, -9]
      }
    ]
  }
}
```

## 饼图

```json
{
  "type": "pie",
  "title": "实时成交分布",
  "data": {
    "items": [
      { "label": "大单流出", "value": 93596.2 },
      { "label": "中单流出", "value": 107085.1 }
    ]
  }
}
```

## 流程图

```json
{
  "type": "flow",
  "title": "分析流程",
  "data": {
    "nodes": [
      { "id": "n1", "label": "查询" },
      { "id": "n2", "label": "筛选" }
    ],
    "edges": [
      { "from": "n1", "to": "n2" }
    ]
  }
}
```

## 表格

```json
{
  "type": "table",
  "title": "历史资金表",
  "data": {
    "columns": ["date", "net_inflow_wan"],
    "rows": [
      { "date": "20260416", "net_inflow_wan": 123.4 }
    ]
  }
}
```

## 指标条

```json
{
  "type": "metric_strip",
  "title": "关键指标",
  "data": {
    "items": [
      { "label": "现价", "value": 1467.5 },
      { "label": "涨跌幅", "value": "1.42%" }
    ]
  }
}
```

## 约束

- 前端只稳定支持固定 `type` 集合，不再兼容无限扩展的历史格式。
- Tool 如果要展示，优先直接输出 `render_blocks`。
- `render_blocks` 用于展示，不回灌到 prompt。
- `meta.source` / `meta.url` 用于溯源；链接类内容优先进入参考资料区，不塞进正文。
