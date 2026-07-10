# Stock Deep Dive Alpha

这是单股票分析 skill 的 alpha 入口。

目标：

- 验证单股票场景下，tool 组合是否稳定
- 验证投顾式结构化输出是否更容易收敛

## 默认 tool 策略

- `mode = strict`
- tools:
  - `stock_quote`
  - `stock_funds`
  - `stock_reports`
  - `company_news`

## 运行方式

```bash
PYTHONPATH=/Volumes/ext/stock_agent python src/skill_runtime/run_skill_alpha.py \
  --skill stock_deep_dive \
  --input src/skills/stock_deep_dive/examples/alpha_input.json \
  --max-steps 5 \
  --output data/results/stock_deep_dive_alpha.json
```

## 重点检查

1. 是否优先使用 `stock_quote` / `stock_funds`
2. 结论是否比 `hotspot_trace` 更收敛
3. `render_payload.sections` 是否完整
4. 是否体现投顾式专业表达
