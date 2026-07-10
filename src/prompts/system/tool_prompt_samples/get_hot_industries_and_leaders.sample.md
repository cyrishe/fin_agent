# get_hot_industries_and_leaders

## input_sample
```python
{
}
```

## output_sample
```python
{
  "data": {
    "query_date": "sample_text",  #  | 引用 `$result.data.query_date` | string
    "items": [
      {
        "theme_type": "sample_text",  #  | 引用 `$result.data.items[].theme_type` | string
        "theme_name": "sample_text",  #  | 引用 `$result.data.items[].theme_name` | string
        "hotness": 1,  #  | 引用 `$result.data.items[].hotness` | number
        "leaders": [
          {
            "name": "sample_text",  #  | 引用 `$result.data.items[].leaders[].name` | string
            "code": "sample_text",  #  | 引用 `$result.data.items[].leaders[].code` | string
            "role": "sample_text",  #  | 引用 `$result.data.items[].leaders[].role` | string
            "performance": "sample_text"  #  | 引用 `$result.data.items[].leaders[].performance` | string
          }
        ],
        "event_summary": "sample_text",  #  | 引用 `$result.data.items[].event_summary` | string
        "performance_summary": "sample_text",  #  | 引用 `$result.data.items[].performance_summary` | string
        "query_date": "sample_text"  #  | 引用 `$result.data.items[].query_date` | string
      }
    ]
  }
}
```
