# Task

Convert the user's financial data question into a coarse staged plan.

# Context

Allowed subjects and dataviews:

{{subject_dataview_context}}

# Output

Return one JSON object only:

```json
{
  "steps": [
    {
      "id": "S1",
      "action": "fetch",
      "subject": "stock",
      "dataviews": ["quote"],
      "requirement": "Natural-language requirement for this step.",
      "depends_on": [],
      "expected_result": "Short natural-language result description."
    }
  ]
}
```

# Rules

1. Use English keys and English enum values only.
2. `action` must be one of: `fetch`, `compute`, `output`.
3. For `fetch`, `subject` and `dataviews` must come from the allowed list.
4. Do not output deep field paths such as `pricevalue.market_value` or `quote.close`.
5. Keep filters, ranking, time windows, joins, grouping, and statistics in `requirement` as natural language.
6. Split the task into small steps. Later steps may depend on earlier step ids.
7. Use `compute` only for cross-row statistics, grouping, projection, sorting, filtering, or derived result shaping.
8. Use `output` only for final user-visible results.

# User Question

{{question}}

