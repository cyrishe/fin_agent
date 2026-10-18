# 涨停冷静 B2 模型与回测登记入口

稳定策略身份为 `kingdomai.automl_strategy_definition` 中的 `imac / B2`，生产展示身份为 `kingdomai.strategy_daily_run` 和 `kingdomai.strategy_execution` 中的 `涨停冷静 / v1.0`。定义 SHA-256：`157b9d77bb9951214b0978365cec407320b1d5663636b80fe60939ae94172c0c`。实际拟合模型按月变化；策略版本、月模型、形态规则、逐日选股、交易和账户回测须分别溯源。

| 管理对象 | 权威入口 | 本次核对状态 |
| --- | --- | --- |
| 策略定义与买卖公式 | `automl_strategy_definition`、`scripts/automl_b2.py::definition()` | B2/v1.0 保持冻结 |
| 月模型和训练包 | `automl_training_archive`、R18/R24 模型及样本清单 | 既有 `B2_20261015`、`B2_20261015_R2`；云端独立回读均待核验 |
| 逐日候选与模拟买卖 | `strategy_daily_run`、`strategy_execution` | 已存 2025-10-16 起；与复跑重叠 240 日、720 候选、146 买入逐项一致 |
| 账户资金回测与候选规则审计 | [2026-10-18 同口径独立复跑](20261018_b2_strict_recheck/README.md) | 18 个月、226 买入、+160.85%、40.20% 回撤；研究期非盲测 |
| 尚未入库的历史逐日记录 | 同次复跑及[只读 DB 回执](20261018_b2_strict_recheck/db_registry_readback.json) | 2025-05-06—10-15，110 日、330 候选、80 买入；待生产写入 Gate |

以后报告“B2 最优/已登记/可回测月份”时，先核对本页、策略定义哈希、月模型与月规则清单、数据库逐日范围和对应账户账本。`automl_training_archive` 是训练资产索引，`strategy_execution` 是逐股模拟结果；当前 KingdomAI 没有独立账户级汇总回测表，故历史账户指标由固定回执与完整账本管理，不能挪作逐笔收益或线上实盘绩效。
