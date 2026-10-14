# 2026-10-11 AutoML 研究进展与迁移清单

10 月 12 日的复现问题与模型训练资产管理见[后续交接](HANDOFF_20261012.md)；本页只记录 10 月 11 日当时的结论。

本页汇总当天提交在 `agent/financial-tool-design-protocol` 分支上的代码、实验和数据资产。历史回测每天用此前连续 20 个交易日的样本重新拟合，是**滚动验证**；将来在 14:40 选股或次晨判断卖点时，应加载事先训练好的模型做推理。是否定期重训是另一项任务，不应被每日推理隐含触发。

## 今日完成的工作

1. **基准口径与买入可行性。** 将 14:40 七因子、涨幅 3%—6%、只剔除当时已经涨停的 ST（其余 ST 保留）、缺失当时特征即剔除、T+1 缺分钟数据不反向删除 T 日样本，落实到 [2026 年 1—7 月 20 日滚动回测](20261011_mk_1m_tradable_st_rolling20/README.md)。修正候选数与模型判涨数混用的问题，逐日展示达标、可接受、中性、严重错误和缺验证数据。ST 涨停信号另用日 K 抽检，并保留其他 ST 处理方案作为历史对照。
2. **独立时段核验。** 重新冻结 [2026 年 8—10 月独立候选样本和连续 20 日训练窗口](20261011_aug_oct_independent20/README.md)，不把缺失交易日跳过补位。8 月没有足够的完整窗口；9 月 2—30 日有效 Top1 共 20 笔，固定 09:40 价格收益代理平均 +0.984%/笔。将 [2025 年 3—8 月](20261011_mk_2025_mar_aug_backtest/README.md)和[9—10 月](20261011_mk_2025_sep_oct_backtest/README.md)也按同一滚动流程回放。2025 年 3—8 月 Top1 为 120 笔，固定 09:40 平均 -0.236%/笔，跨年差异不能掩盖。
3. **分钟数据和实验口径。** 2026 年 1—7 月的 139 个未复权交易日、2025 年 1—11 月的 220 个未复权交易日均整理成逐日无损 `.csv.zst`，有 [2026 年哈希清单](20261011_mk_1m_compact/compressed_manifest.json)和 [2025 年哈希清单](20261011_mk_2025_compact/compressed_manifest.json)。按本次迁移要求，**只有 2026 年 139 个压缩分钟 K 文件通过 Git LFS 上传**；2025 年原始逐分钟数据留在本机，报告和哈希清单仍在 Git。分别试验了[主板 3%—6%/创业科创 4%—8%](20261011_main3_wide4_may_jul/README.md)和[仅非主板 4%—8%](20261011_wide_only_4to8_ge3_may_jul/README.md)，都保持原基准不被覆盖。
4. **成交额和卖出时点。** 将大盘和 Top1 个股截至 T 日 14:40 的成交额、前 5/20 个交易日同一时点的比较值附到[逐日表](20261011_market_state_asof/README.md)，明确条件只是事后发现的探索标签，未擅自改变基准交易。分别做了[2025 年 5—7 月](20261011_stock_amount_gate_2025_may_jul/README.md)和[2026 年逐月](20261011_stock_amount_gate_2026_monthly/README.md)的条件对照；与 [09:45 对 09:40 卖出比较](20261011_0945_exit_comparison/README.md)一样，尚不足以替换原规则。
5. **次晨形态识别。** 对原策略已经选出的 294 笔 Top1，按前六根一分钟 OHLCVA 尝试四类十分钟走势识别、提前卖出回归、六根 K 的 1D CNN，并分别加入前一天个股和次晨大盘信息。[形态识别审计](20261011_opening_shape_prototype/shape_recognition_review.md)与 [CNN 对照](20261011_opening_shape_prototype/sequence_model_review.md)分开评估“识别准不准”和“卖出能否增益”。目前跨期结果没有稳定胜过原展平逻辑回归，卖出动作继续以 09:40 为兜底。四份小型复现实验快照已纳入 [data/](20261011_opening_shape_prototype/data/)，新机器不用重查原始分钟数据也能复核该 294 笔实验。

上述收益均是选股时点买价和次晨分钟价格的**价格路径代理**，不等于真实成交净收益；滑点、费用和 09:36 判断后能否以 09:37 开盘价成交仍需独立验证。成交额门槛是看过样本后提出的规则，相关提升属于探索期观察，不可当成独立样本验证。

## 新机器接手

- 克隆该分支后先安装 Git LFS，并运行 `git lfs install`、`git lfs pull`。2026 年分钟 K 直接位于 `outputs/stock_automl/mk_1m_compact/YYYY-MM-DD.csv.zst`；2025 年分钟 K **未上传**。2026 年 9 月的早盘实验还依赖当时本地数据库或本次已提交的 294 笔实验快照；`.env` 和数据库凭据未提交。
- 检查 `git lfs ls-files` 应有 139 个 2026 年日文件。压缩文件大小和 SHA-256 应与[2026 年清单](20261011_mk_1m_compact/compressed_manifest.json)一致。新机器可直接用 pandas `pd.read_csv` 读取 `.csv.zst`。
- 核心实验入口：`python -m scripts.evaluate_automl_opening_exit` 和 `python -m scripts.experiment_automl_opening_sequence`；相关摘要、逐笔预测及模型对照都在 [次晨形态实验目录](20261011_opening_shape_prototype/README.md)。运行后若本机没有 `outputs/stock_automl/` 中对应缓存，脚本会从已提交的四份快照读取。
- 继续研究时保持 2026 年 1—7 月与 9 月独立验证窗口、训练样本口径和选股/卖出目标分开；不要把 2025 年原始分钟 K 已随 Git 迁移或次晨 CNN 已可用于自动卖出当成事实。
