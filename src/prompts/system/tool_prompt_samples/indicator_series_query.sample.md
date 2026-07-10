# indicator_series_query

## input_sample
```python
{
  "indicator_ids": [
    "sample_text"
  ],
  "subject_codes": [
    "sample_text"
  ],
  "range_days": 1  # 返回最近多少个交易日的数据点，默认 30。 | 引用 `range_days` | integer
}
```

## output_sample
```python
{
  "data": [
    {
      "trade_date": "sample_text",  #  | 引用 `$result.data[].trade_date` | string
      "indicator_name": "sample_text",  #  | 引用 `$result.data[].indicator_name` | string
      "indicator_value": "",  #  | 引用 `$result.data[].indicator_value` | number|null
      "subject_type": "sample_text",  #  | 引用 `$result.data[].subject_type` | string
      "subject_code": "sample_text",  #  | 引用 `$result.data[].subject_code` | string
      "subject_name": "sample_text",  #  | 引用 `$result.data[].subject_name` | string
      "measure_name": "sample_text",  #  | 引用 `$result.data[].measure_name` | string
      "window_size": 1,  #  | 引用 `$result.data[].window_size` | integer
      "unit": "sample_text",  #  | 引用 `$result.data[].unit` | string
      "snapshot_time": "sample_text",  #  | 引用 `$result.data[].snapshot_time` | string
      "minute_index": "",  #  | 引用 `$result.data[].minute_index` | integer|null
      "is_finalized": true,  #  | 引用 `$result.data[].is_finalized` | boolean
      "calc_mode": "sample_text",  #  | 引用 `$result.data[].calc_mode` | string
      "source_summary": "sample_text"  #  | 引用 `$result.data[].source_summary` | string
    }
  ]
}
```
