# stock_quote

## input_sample
```python
{
  "code": "sample_text"  # 证券代码或公司名称。工具内部会自动解析并使用默认历史窗口和分钟线策略。 | 引用 `code` | string
}
```

## output_sample
```python
{
  "data": {
    "code": "sample_text",  #  | 引用 `$result.data.code` | string
    "daily_kline": "sample_text",  #  | 引用 `$result.data.daily_kline` | string
    "intraday_kline": "sample_text",  #  | 引用 `$result.data.intraday_kline` | string
    "name": "sample_text",  #  | 引用 `$result.data.name` | string
    "realtime_quote": "sample_text",  #  | 引用 `$result.data.realtime_quote` | string
    "source": "sample_text",  #  | 引用 `$result.data.source` | string
    "stk_code": "sample_text"  #  | 引用 `$result.data.stk_code` | string
  }
}
```
