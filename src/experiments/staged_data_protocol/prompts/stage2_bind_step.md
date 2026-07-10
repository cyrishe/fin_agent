# Task

Bind one coarse plan step to executable data or compute requests.

# Inputs

Original question:

{{question}}

Full coarse plan:

{{stage1_plan_json}}

Current step:

{{current_step_json}}

Dependency context:

{{dependency_context_json}}

API reference loaded for this step:

{{api_reference_json}}

# Output

Return one JSON object only:

```json
{
  "step_id": "S1",
  "status": "ready",
  "executable_request": {
    "type": "fetch",
    "api_calls": [
      {
        "api_id": "stock.quote",
        "operation": "query",
        "arguments": {},
        "filters": [],
        "outputs": [],
        "method_calls": [
          {
            "method_id": "stock.quote.pct.kd_pct_sum",
            "call_style": "method",
            "arguments": {"k": 5}
          }
        ]
      }
    ],
    "compute": null,
    "output": null
  },
  "result_schema": [],
  "dependency_refs": [],
  "issues": []
}
```

# Rules

1. Work on the current step only.
2. Use only the API references provided above.
3. Convert natural-language filters, ordering, limit, time window, and dependency references into the request when the API reference supports them.
4. If a dependency is needed, reference it as `Sx.result` or `Sx.result.field`.
5. For fixed methods, copy a `method_id` exactly from API reference `method_calls`.
6. For window methods such as `kd_pct_sum` and `kd_close_high`, put the trading-day window in integer argument `k`.
7. If the current step cannot be bound with the provided API reference, set `status` to `missing_api` or `needs_repair` and explain it in `issues`.
8. Do not invent API ids or method ids that are absent from the provided API reference.
