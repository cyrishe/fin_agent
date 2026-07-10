# theme_leaders

## input_sample
```python
{
  "query": "sample_text"  # 板块/热点名称，用于检索并返回龙头股列表 | 引用 `query` | string
}
```

## output_sample
```python
{
  "data": [
    {
      "realtime_pct": "sample_text",  # 实时/当天涨幅 | 引用 `$result.data[].realtime_pct` | string
      "stock_code": "sample_text",  # 龙头股代码 | 引用 `$result.data[].stock_code` | string
      "stock_name": "sample_text",  # 龙头股名称 | 引用 `$result.data[].stock_name` | string
      "theme_name": "sample_text",  # 所属板块或者概念 | 引用 `$result.data[].theme_name` | string
      "week_pct": "sample_text"  # 一周涨幅 | 引用 `$result.data[].week_pct` | string
    }
  ]
}
```
