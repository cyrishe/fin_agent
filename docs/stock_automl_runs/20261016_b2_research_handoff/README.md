# B2研究交接：R25—R30（2026-10-16）

## 当前生产边界

线上仍是B2/R24/four_fit_delay：四K形态、原Top3按时序激活、锁定后延后一根合格K模拟买入、D2收盘卖出。此次没有新模型训练，没有变更数据库、定时服务或生产交易规则。R26均价入场和R29/R30卖法仅为研究对照。

## 本轮登记

|编号|内容|主要证据|
|---|---|---|
|口径纠正|用户引用的10月+12.51%属于旧R23B五K免手续费；当前B2同截点为−7.19%|[10月核对](../20261016_b2_backfill/OCTOBER_RECONCILIATION.md)|
|R25|09:36分钟标签开盘价实为09:35；区分固定窗口扫描与激活后延一K，旧四方案逐笔精确复现|[时钟对照](../20261016_b2_four_five_clock/README.md)|
|R26|锁定后的下一分钟开收均价，两种截止时钟分别列示；主end0941允许09:40开始最后一分钟|[均价入场](../20261016_b2_next_minute_mean/README.md)、[月度稳定性](../20261016_b2_next_minute_mean/MONTHLY_STABILITY.md)|
|F27/DIST28|用户最终需要的是两策略选股的规模与流动性分布，不是根据收益搜索因子；最终Excel为DIST28|归档内b2_monthly_distribution_20261016；F27保留作追溯，不将其分组当已验证规则|
|R29|D2相对买价+8%目标；用户明确高开超过目标也只记目标价，conservative.csv为这个口径；较高开盘卖价仅是已否定的敏感性对照|[目标止盈](../20261016_b2_d2_takeprofit8/README.md)|
|R30|固定目标止盈，否则14:50—15:00成交额/成交股数；161股日完整核对，42数据库+119本地原始分钟归档；无14:50后首次触发|[尾盘均价](../20261016_b2_d2_tail_vwap/README.md)|

全部账户回放2026-01-01至10-14、100万元现金轮换、各边0.1%手续费、D2退出；末笔未平仓估值保留。均价为市场代理价，不是实盘成交证明。用户重视月初亏损和月内回撤，不能以更高全期收益替代稳定性结论。

## 复现与资产

`B2_R25_R30_replay_assets_20261016.tar.gz`保存58个运行文件：所需小型输入快照、原始D2分钟数据、逐笔账本、逐月指标、Excel和构建器。包内路径相对仓库根目录，解压命令：

```sh
tar -xzf docs/stock_automl_runs/20261016_b2_research_handoff/B2_R25_R30_replay_assets_20261016.tar.gz -C .
.venv-automl/bin/python -m scripts.replay_b2_next_minute_mean_20261016
.venv-automl/bin/python -m scripts.replay_b2_d2_takeprofit8_20261016
.venv-automl/bin/python -m scripts.replay_b2_tail_vwap_20261016
```

这些脚本还引用原R18/R20/R24冻结资产（仓库既有归档），不重复训练。R25完整重跑需要原R20分钟日文件；包内inputs/first11.pkl保存本次用到的截取证据，source哈希可核。本轮不是从空目录安装全套历史数据库的工具。

三套回放再次运行后summary.csv和monthly.csv逐字节哈希一致，见reproduction.json。R25原方案逐笔复现、R26买价算术核对、R29日线成交核对、R30分钟完整性与量价核对分别随实验保存。包已逐文件解压回读核验，见asset_manifest.json。Excel经渲染检查与错误扫描。本次无新训练包；云盘仍待同步，Git推送不代表云盘同步。
