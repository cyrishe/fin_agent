# 14:40 七因子 Top1/Top2 本机运行

入口：`scripts/automl_1440.sh`。从仓库根目录运行，不下单。

## 首次使用

```bash
./scripts/automl_1440.sh init-db
./scripts/automl_1440.sh prepare --date 2026-10-09
./scripts/automl_1440.sh run --date 2026-10-09
```

`init-db` 在 `kingdomai` 建立 `automl_strategy_definition` 和
`automl_strategy_selection`。默认 `run` 只生成本地 JSON 和全候选概率 CSV；
确认结果后附加 `--write-db --user-id <用户ID>` 才把 Top1/Top2 写库。
同一用户、策略、日期、排名的重跑在事务内替换原记录。
可用 `.venv/bin/python -m scripts.verify_automl_1440_schema` 做事务内协议核验；
测试数据最终回滚。

日常交易日建议先于 14:40 准备模型，再在 14:40 精确采集：

```bash
./scripts/automl_1440.sh prepare
./scripts/automl_1440.sh run --write-db --user-id imac
```

不写 `--date` 即上海当天；14:40 前运行 `run` 只提示未到，不查询行情或写库。
14:40:00–14:40:59 之间使用全市场批量行情接口；接口不带交易所时间戳，
整批请求必须在这一分钟内完成。窗口结束后仅接受数据库中精确的
14:40 一分钟 K 和当日日行情昨收。当天如果既错过接口窗口，数据库又尚未
入库，脚本会报错，不把稍后的价格冒充 14:40 信号。

## 数据和口径

- 前 20 个连续交易日的 14:40 涨幅 3%–6% 候选，七因子，用次晨
  09:31–09:40 十根一分钟 K 的第二高价打四分类标签。
- 使用 `small_boosted_tree` 每日重训；先排预测 ≥3% 类，后排预测
  1%–3% 类，最多输出 Top1/Top2。只排除信号时已在涨停价的 ST。
- Top1 当日截至 14:40 的个股成交额低于自身此前 20 个交易日同口径
  中位数，或这 20 个交易日数据不全，则轮空；Top2 不递补。
- 日行情、估值、历史及回填分钟 K 来自 `kingdomai`；当天 14:40
  的实时信号来自行情接口。数据库当日早盘分钟 K 尚未入库时，训练标签
  可由一分钟 K 接口补齐。`--archive-dir` 可使用已解压的未复权 MK 日文件；
  `--archive-tar /Users/imac/Downloads/mk.tar.xz` 是 2026 年 5–7 月的备用路径。
- 当日推理只保存 14:40 信号价。前次资金回放使用的买入 14:40–14:41、
  卖出次晨 09:39–09:40 高低均价，在 14:40 尚不可知，不参与本次选股。

本地输出在 `outputs/stock_automl/live_1440/`，含模型缓存、
逐日训练样本快照及 SHA-256、`<日期>_decision.json` 与
`<日期>_all_predictions.csv.gz`。历史接口补回的次晨 K 线可能使训练行数
有别于旧报告；按每日快照与模型哈希核对本次结果。
结果表明确存储用户、策略 ID、股票、排名、四类概率、七因子、当日成交额、
20 日中位数、有效天数与是否可交易；策略表保存训练、排序和执行公式，
策略变更须使用新的策略 ID。`user_id` 是本机脚本输入标识，不提供
应用层身份认证；将来接入多用户服务时需要由服务端注入可信用户 ID。

### 10 月 9 日重放校验

新流水线在相同的 471 只候选中，用数据库及分钟接口补得 6,261 条训练
标签，得到 Top1 `603757`、Top2 `603669`。旧[独立 20 日报告](../20261011_aug_oct_independent20/README.md)
记录 6,240 条训练标签，Top1 `600699`、Top2 `002907`。额外的 21 条
来自历史缺行后的接口补录；即使直接用旧报告冻结的 6,240 条样本与当前代码
重训，本机也得到 Top1 `603236`、Top2 `600699`。因此新流水线使用独立的
策略 ID，旧报告收益不能直接作为它的预期收益；在确认重训差异之前只记录
推理结果，不自动下单。

查看某用户的选股和对应策略简述时，按用户和策略 ID 关联两表：

```sql
SELECT s.signal_date, s.selection_rank, s.stock_code, s.stock_name,
       s.p_ge3, s.p_1to3, s.amount_1440_cny,
       s.amount_prior20_median_cny, s.actionable,
       d.strategy_id, d.summary, d.training_method, d.selection_formula
FROM kingdomai.automl_strategy_selection AS s
JOIN kingdomai.automl_strategy_definition AS d
  ON d.user_id = s.user_id AND d.strategy_id = s.strategy_id
WHERE s.user_id = 'imac'
ORDER BY s.signal_date DESC, s.selection_rank;
```

## 可选定时

```bash
.venv/bin/python scripts/install_automl_1440_schedule.py --user-id imac --install
```

macOS LaunchAgent 每个交易日 13:50 运行 `prepare`、14:40 运行
`run --write-db`；非交易日自动跳过。日志位于
`outputs/stock_automl/live_1440/logs/`。不加 `--install` 仅生成
`~/Library/LaunchAgents/` 中的两个 plist 文件，便于先检查配置。
13:50 训练只用截至前一交易日的各日 14:40 特征，并在今日 09:40
标签形成后纳入最后一个训练日；今日 14:40 的行情只在 `run` 阶段
用于构造待预测候选和七因子。

## 2026-10-12 实际运行

**复现核对（2026-10-12）：以下两只股票仅是本次实时脚本保存的输出，不能标为已验证最佳回测模型的同口径信号。**实时脚本的 10 月 9 日历史演练用 6,261 条训练样本，选出大元泵业；冻结的 [8–10 月独立 20 日基准](../20261011_aug_oct_independent20/README.md)同日记录 6,240 条训练样本、选出均胜电子。对照输入发现，实时训练补入了旧基准在 9 月 16 日缺标签的 21 条样本；共同样本的部分数值因子也有差异。进一步从冻结候选文件按现有训练代码重训 10 月 9 日的 6,240 条，Top1 却是移远通信，仍不能复现冻结结果。这项差异尚未定位。10 月 12 日实时模型又用同一路径训练 6,473 条。其输出可以从保存的模型与 117 条候选逐条复算，但**尚未通过与冻结基准的选股复现检查**，不可据此声称延续了上午资金回放的收益表现。

模型使用 2026-09-04 至 2026-10-09 的 20 个连续交易日，共 6,473 条
有标签训练行。14:40:25 全市场行情接口采集完成，七因子候选 117 只；预测
类别分布为 `<0` 76、`0–1` 35、`1–3` 6、`≥3` 0。按类别顺延选中：

| 排名 | 股票 | 14:40 价格 | `P(≥3%)` | `P(≥1%)` | 当日成交额 | 前20日中位数 | 门槛 |
|---|---|---:|---:|---:|---:|---:|---|
| Top1 | 603757 大元泵业 | 75.08 | 14.85% | 54.91% | 9.509亿元 | 5.078亿元 | 通过 |
| Top2 | 300131 英唐智控 | 13.47 | 13.20% | 49.10% | 7.766亿元 | 7.650亿元 | 观察 |

定时任务退出码为 0，`automl_strategy_selection` 中用户 `imac`、策略
`rolling20_7f_hgb_dbapi_amount_v1`、该日排名 1/2 的两条记录与本地
`2026-10-12_decision.json` 相符。仅保存推理，未下单；次晨标签和实际卖出
收益此时尚未发生。
