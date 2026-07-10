# financial_news_search

## input_sample
```python
{
  "query": "sample_text"  # 公司名、股票代码、概念、主题或事件关键词 | 引用 `query` | string
}
```

## output_sample
```python
{
  "data": [
    {
      "title": "sample_text",  #  | 引用 `$result.data[].title` | string
      "url": "sample_text",  #  | 引用 `$result.data[].url` | string
      "site": "sample_text",  #  | 引用 `$result.data[].site` | string
      "publish_time": "sample_text",  #  | 引用 `$result.data[].publish_time` | string
      "snippet": "sample_text",  #  | 引用 `$result.data[].snippet` | string
      "source": "sample_text"  #  | 引用 `$result.data[].source` | string
    }
  ]
}
```
