# stock_funds

## input_sample
```python
{
  "code": "sample_text"  # 证券代码或公司名称。工具内部会自动解析并使用默认资金窗口与新闻策略。 | 引用 `code` | string
}
```

## output_sample
```python
{
  "data": {
    "snapshot": {
    },
    "capital_flow_news_items": [
      {
        "title": "sample_text",  #  | 引用 `$result.data.capital_flow_news_items[].title` | string
        "url": "sample_text",  #  | 引用 `$result.data.capital_flow_news_items[].url` | string
        "site": "sample_text",  #  | 引用 `$result.data.capital_flow_news_items[].site` | string
        "publish_time": "sample_text",  #  | 引用 `$result.data.capital_flow_news_items[].publish_time` | string
        "snippet": "sample_text",  #  | 引用 `$result.data.capital_flow_news_items[].snippet` | string
        "matched_keywords": [
          "sample_text"
        ],
        "stock_code": "sample_text",  #  | 引用 `$result.data.capital_flow_news_items[].stock_code` | string
        "stock_name": "sample_text"  #  | 引用 `$result.data.capital_flow_news_items[].stock_name` | string
      }
    ]
  }
}
```
