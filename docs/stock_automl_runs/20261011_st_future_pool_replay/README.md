# ST 与次晨数据完整性：20 日滚动模型复跑

2026-10-11 使用仓库中保存的 2026-08-05 至 09-30 行情资产，在相同环境下重新拟合两组模型。训练窗口为每个测试日前最近 20 个信号日，测试为 09-02 至 09-30 的 20 个交易日；14:40 涨幅 3%–6%、七因子、四分类小型提升树和 Top1 排序规则相同。标签是次日 09:31–09:40 一分钟 K 的第二高价相对 14:40 价的收益分类；下表收益则按次日 **09:40 分钟收盘价**卖出计算，未经费用、滑点和成交性验证。

| 口径 | 40 日候选股票日 | 20 日 Top1 笔数 | 次高价 ≥3% | 次高价 ≥1% | 09:40 单笔平均收益 |
|---|---:|---:|---:|---:|---:|
| 旧：名称含 ST 和次晨分钟数据缺失的股票事后不入池 | 14,292 | 20 | 8 | 15 | +1.269% |
| 修正：保留 ST，T 日全部当时可见候选都评分；补回能取得的历史标签 | 14,783 | 20 | 8 | 13 | +0.761% |

同日期成对相减，修正口径少 **0.508 个百分点/笔**。20 日中 8 日换了 Top1，修正口径收益高于旧口径 2 日、低于旧口径 6 日，其余 12 日相同。旧、修正 Top1 分别有 14、13 笔正收益；两组 Top1 均未直接选中名称含 ST 的股票。保留 ST 后，历史训练集合增加了 ST 及其他原先漏掉的有标签股票，这也会改变非 ST 股票的排序。因此两组的差异不能单独归因于“次晨缺数据”这一项。

[20 个测试日的两组 Top1、实际类别和卖出收益](daily_top1_comparison.csv)。

修正池的 14,783 条中，原分钟表有 14,724 条次晨标签；另有 53 条可从分钟行情 API 补回，剩余 6 条次日停牌，没有可定义的次晨价格。**6 条在各自 T 日仍进入推理候选池**；等它们成为历史样本时，因为没有监督标签才不进入模型拟合，绝不事前从候选池剔除或伪造标签。若不补回 53 条、只训练原有标签，同环境修正池的 Top1 均值为 +0.513%；补回后为 +0.761%。

## 与 10 月 10 日存档的区别

旧报告保存的对应数字为 **+1.311%**，修正报告为 **+0.669%**。本次重新拟合同一保存输入后得到上表的 **+1.269%** 和 **+0.761%**。当前复跑与存档的候选量、训练日期、输入文件哈希一致，但部分预测概率和 Top1 名单不一致；将当前环境 `scikit-learn` 1.7.1 换成机器上另有的 1.6.1 后，旧池 Top1 仍为 +1.269%，因此**仅这两个版本的差异不能解释存档不一致**。存档当时的完整执行环境尚未锁定，不能声称精确复现存档数字。两个独立成对比较都显示修正后收益下降；本页以同环境新复跑作为可核验对照，不将两套数字交叉比较。

## 复跑输入与输出

- 旧池键来自 [`exact_1440_to_close9_candidates.csv.gz`](../20261009_close9_target/exact_1440_to_close9_candidates.csv.gz)，SHA-256 `99263a8722907209d1cc45ffd6e3e73a1386fc36050f67e5d55f78b0c3e8942a`；七因子和结果在 [`prepared_candidates.csv.gz`](../20261010_four_class_similar_days/prepared_candidates.csv.gz)，SHA-256 `c0a4d1ea86c0ea7bbcb21d98cb8efe133efeaf8bd981ca0a86557e00bff00c08`。旧键对应的七因子、14:40 价格及结果，与原旧文件及次高价资产逐项一致。
- 历史标签补全记录为 [`minute_api_label_audit.csv`](../20261010_four_class_baseline_recent20/minute_api_label_audit.csv)，SHA-256 `d054e741e368093ef7b45a3b32da213ac1c0fb4ef944064bcdb3e67b2a2bb5f2`。
- 两组训练均调用 [`run_automl_four_class_pipeline.py`](../../../scripts/run_automl_four_class_pipeline.py) 的样本、特征、标签、模型、推理和排序函数。修正组通过 [`experiment_automl_recovered_morning_labels.py`](../../../scripts/experiment_automl_recovered_morning_labels.py) 运行。旧组将旧池键与 `prepared_candidates.csv.gz` 内连接，再调用 `run(rows, states, output)`，只读取结果中的 `recent20`。输出保存在本机忽略目录 `outputs/stock_automl/reproduction_20261011/old_pool_direct/` 与 `outputs/stock_automl/reproduction_20261011/corrected/`，含全部概率、逐日 Top2、训练折和摘要。

修正组在仓库根目录的复跑命令：

```bash
.venv-automl/bin/python -m scripts.experiment_automl_recovered_morning_labels \
  --prepared docs/stock_automl_runs/20261010_four_class_similar_days/prepared_candidates.csv.gz \
  --api docs/stock_automl_runs/20261010_four_class_baseline_recent20/minute_api_label_audit.csv \
  --output outputs/stock_automl/reproduction_20261011/corrected
```

这 20 天此前已反复用于模型研究，属于复现与口径审计，不是新的盲测，也不支持据此宣称实盘收益。
