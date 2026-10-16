# 行情连续性与标签质量核对

本轮只读取得 2025-07-01 至 2026-10-15 的日行情快照，沪深证券筛选后为 1,622,478 行、5,259 股、313 个源交易日。键 `stk_code, trade_date` 无重复。目标及标签区间前收参考价无空值。

供应商 `adjopen/adjhigh/adjlow/adjclose` 并非可直接跨日使用的完整一致序列。2026-04-01 至 10-15 中，有 76 个股票日（74 股）满足：

`abs(100 × (adjclose / previous_adjclose − close / preclose)) > 0.1 个百分点`

其中 23 股各一日差异超过 0.5 个百分点；部分股票在 9 月 9–10 日的复权价格尺度约扩大十倍。例如 `600621.SH` 在 9 月 9 日原始收盘 13.36、复权收盘 293.5759；9 月 10 日原始收盘 13.62、前收 13.36，但复权收盘 2992.8925。原始涨幅约 +1.95%，直接跨日复权相除却约 +919.46%。

原先基于供应商复权字段的扫描在汇总前中止，部分中间产物保存在 `outputs/stock_automl/kline_survey_kp1_20261016/aborted_provider_adj_parts`，不进入任何正式表格、排名或研究包。

正式扫描统一从原始 OHLC 和前收参考价建立因果链式价格指数，见 [统计口径](METHOD.md)。这是整条价格序列的构造方式，不是按股票代码或收益高低打补丁。原始快照未修改，数据库未写入。仅有约 0.1 个百分点的差别可能包含除权参考价舍入等原因，不能把所有 76 条都断言为同一种错误。

实际原始价格的大跌另行保留；没有把极端收益截尾为普通涨跌。停牌或无效 K 线按缺失处理，标签要求其后的每个市场交易日都可观测，不用下一条有成交数据替代。

证据：

- `outputs/stock_automl/kline_survey_kp1_20261016/source_quality_audit.json`
- `outputs/stock_automl/kline_survey_kp1_20261016/adjustment_discrepancies.csv`
- `outputs/stock_automl/kline_survey_kp1_20261016/adjustment_audit_summary.json`
- `outputs/stock_automl/kline_survey_kp1_20261016/input_manifest.json`
- [自动核验回执](verification.json)：独立原始价格逐日相乘核对收益标签、真实数据截断前缀核对形态因果性、64 条规则汇总数回读。

主表另有排除这 23 股的敏感性结果，避免是否依赖这些数据影响对形态的判断。前收参考价和复权资料均为当前供应商快照，未独立重建逐笔公司行动或历史数据修订版本。
