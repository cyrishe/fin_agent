# B2 多模型逐月选股实验：停电前检查点

记录于 2026-10-19 22:07 CST。用户要求暂停逐年计算，明天继续。本实验只研究 B2 日线模型的月度选择；正式 `B2/v1.0` 及生产数据库未改。

## 已完成

- R43：2022-09—2026-09 共 49 个可拟合月份，每月随机森林、LightGBM、XGBoost 共 12 组配置，每组两个 80% 分层抽样种子，保存 588 个组合模型；另保留原固定 RF51 作为参考。按六个月训练、两个月验证，为 2023-05—2026-09 的 41 个测试月冻结选模结果。测试月结果未参与当月选模。
- R43：分钟资料准备、四 K 月规则选择、D1 买入与 D2 卖出信号回放已完成；503 条最终交易信号，41 个月分类分段结果已落盘。模型重载评分最大差 0，503 条信号与 `automl_b2.evaluate_morning` 一致，逐日无重复交易。日线分钟覆盖：2,907 个股票日中 2,892 个通过；余下 7 个价格冲突、6 个分钟缺失、2 个 D1 日线缺失。
- R44：以 D1 开盘至 D2 收盘的费后收益代理为选模目标，41 个月的模型选择已冻结；所需 989 个分钟日期处理完毕，`minute_facts.pkl`、`enriched.pkl` 和 `coverage.json` 已落盘。四 K 规则选择、交易回放与审计尚未运行。
- R45：验证期表现暂停交易的规则已预先写入 `specification.json`；尚未运行。

## 目前可以确认的实验读数

R43 全期 2023-05—2026-09，三日再板标签：形态前 Top3 为 1,558/2,468 = 63.13%；形态通过 Top3 为 1,305/1,794 = 72.74%；实际买入为 274/503 = 54.47%。与同区间原固定 RF51 的 287/543 = 52.85% 相比，实际买入的三日再板率仅高 1.62 个百分点，同时买入笔数减少 40。

用已保存的买价和 D2 收盘价，按买卖各 0.1% 计算逐笔收益（不是账户资金回放）：原固定 RF51 平均每笔 −0.663%，R43 平均每笔 −1.138%；盈利笔占比 43.46% / 43.14%，亏超 5% 占比 38.12% / 39.36%。这说明 R43 的再板命中率提高未转化为 D2 卖出收益改善。历史区间已参与研究，以上也不是盲测。

R43 按已落盘三日标签的最终买入命中率：2023 年 65/131 = 49.62%；2024 年 62/122 = 50.82%；2025 年 87/135 = 64.44%；2026 年 1—9 月 60/115 = 52.17%。这些只是分类读数，不是逐年账户收益。用户要求的逐年账户计算仍暂停。

## 文件与接续

- 代码：`scripts/experiment_b2_daily_search_r43.py`、`scripts/experiment_b2_validation_pause_r45.py`、`scripts/summarize_b2_daily_search_r43_r45.py`、`scripts/build_b2_search_workbook_r43_r45.mjs`。
- R43 结果：`outputs/stock_automl/b2_daily_search_r43_20261019/`。关键文件为 `specification.json`、`selected/*/choice.json`、`rules_frozen.json`、`signals.pkl`、`shape_test_metrics.json`、`verification.json`、`audit.log`。`signal_manifest.json` 中信号 SHA-256 为 `cb43d96774a43d912d6110b79031773791c712cb0c4e590743892a10b75d3158`。
- R44 结果：`outputs/stock_automl/b2_daily_risk_choice_r44_20261019/`。当前停在分钟准备完成之后；可从 `select_rules --risk` 继续，不必重新训练或提取分钟日期。
- R45 预声明：`outputs/stock_automl/b2_validation_pause_r45_20261019/specification.json`。
- 明天依次运行 R44 的 `select_rules`、`evaluate`、`audit`，再计算两方案资金账户及逐年逐月、运行 R45，对照 2023—24 年失效期，最后导出工作簿。不得拿 R43 的三日命中改善直接宣称策略收益改善。

运行环境需保留或重建 `/tmp/b2-r41-sklearn`、`/tmp/b2-r43-libs`、`/tmp/b2-r43-omp/lib`；安装包另存于 R43 目录 `runtime_wheels/`。命令前缀：

```sh
DYLD_LIBRARY_PATH=/tmp/b2-r43-omp/lib PYTHONPATH=/tmp/b2-r41-sklearn:/tmp/b2-r43-libs:. .venv-automl/bin/python -m scripts.experiment_b2_daily_search_r43 select_rules --risk
```

研究资产当前只在本机；R43/R44/R45 还未登记 KingdomAI 训练归档、未云端回读核验，不得称作已入库或已同步。当前没有实验进程在运行。
