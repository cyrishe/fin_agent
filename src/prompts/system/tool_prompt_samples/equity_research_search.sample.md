# equity_research_search

## input_sample
```python
{
  "company": "sample_text"  # 公司名称或证券代码。工具内部会自动解析为标准证券代码。 | 引用 `company` | string
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
      "institution": "sample_text",  #  | 引用 `$result.data[].institution` | string
      "analyst": "sample_text",  #  | 引用 `$result.data[].analyst` | string
      "report_type": "sample_text",  #  | 引用 `$result.data[].report_type` | string
      "snippet": "sample_text",  #  | 引用 `$result.data[].snippet` | string
      "stock_code": "sample_text",  #  | 引用 `$result.data[].stock_code` | string
      "stock_name": "sample_text"  #  | 引用 `$result.data[].stock_name` | string
    }
  ]
}
```
