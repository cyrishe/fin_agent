# Quant Factor Screening Skill

## 目标

把自然语言量化条件、消息面条件或组合策略转成可执行的候选筛选流程。

必须遵守：

- 先形成 factor plan，再执行数据准备和因子计算。
- 数据准备只通过 `quant_data_provider` 或文件 intake artifact，不能让 code runtime 直连数据库。
- 数据缺失、字段缺失、股票池为空时返回 coverage/failure reason，不能编造候选股。
- 输出只做数据分析和候选排序，不承诺收益，不给交易指令。

## 推荐流程

1. 识别股票池、交易日窗口、报告期窗口、所需数据源、因子和排序方式。
2. 调用 `quant_factor_screening`，优先传入显式 `factor_plan`；若需求简单，可传 `user_text`、`universe`、`date_range`、`required_data` 和 `top_n`。
3. 根据返回的 `data_coverage` 和 `risk_warnings` 判断结果是否可解释。
4. 最终回答应包含候选股表、因子定义、覆盖率、缺口、风险提示和 audit 摘要。

## 输出要求

核心字段：

- `selected_stocks`
- `factor_table`
- `factor_definitions`
- `data_coverage`
- `risk_warnings`
- `render_blocks`
- `audit`

表达要求：

- 说明排序依据和数据覆盖，不夸大结论。
- 对缺失数据单独列出，不把缺失当作负面因子。
- 如果没有 verified provider 数据，明确说明无法完成有效筛选。
