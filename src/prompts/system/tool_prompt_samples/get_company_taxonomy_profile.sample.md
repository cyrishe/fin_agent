# get_company_taxonomy_profile

## input_sample
```python
{
  "query": "sample_text"  # 公司名称或股票代码 | 引用 `query` | string
}
```

## output_sample
```python
{
  "data": {
    "company": "sample_text",  #  | 引用 `$result.data.company` | string
    "query_date": "sample_text",  #  | 引用 `$result.data.query_date` | string
    "industries": [
      "sample_text"
    ],
    "sectors": [
      "sample_text"
    ],
    "concepts": [
      "sample_text"
    ],
    "events": [
      "sample_text"
    ]
  }
}
```
