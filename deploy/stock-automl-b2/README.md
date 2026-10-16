# B2：D0封板前三、D1四K形态、延后一K模拟盘

策略ID为 `B2`。完整业务说明由 `scripts/automl_b2.py::definition()` 单一来源生成，写入
`kingdomai.automl_strategy_definition` 的 summary、training_method、selection_formula、execution_formula 和 formula_json。
旧表没有 desc 列，summary 即完整中文描述；不为同义字段另改生产结构。

## 每日运行

1. D0盘后 `close --write-db`：使用D0所属月份冻结的R18完整分段51因子随机森林，给所有原口径封板候选评分，原Top3全入库，actionable=0。
2. D1从09:30运行 `scan --watch --write-db`，每3秒直接读取三只候选的原始1分钟接口。只消费已经结束的K，忽略尚未完结或别的日期的K。四根K完成后应用当月四K规则。
3. 激活检查结束标签09:35—09:40：分钟有量且open低于涨停价0.005元。最早分钟优先，同分钟D0排名优先。锁定一只后不换股，实体≤−8%则轮空。
4. 锁定后的下一根及以后合格K开盘价为模拟买价，最迟结束标签09:40；唯一最终买入者actionable=1。该价发生在K开始，确认最早在该K结束；buy_at与details_json.actionable_confirmed_at分别保存，两者通常至少差一分钟。
5. D2收盘 `settle --write-db`：写入日线收盘模拟卖价、毛收益及独立列net_return_pct（买卖各0.1%）。卖价缺失、无成交或价格断裂保留待核对。

服务只记录模拟盘，没有券商接口。100万元两份资金轮换是历史基准的账户回放口径；线上此服务保存候选与逐笔模拟价格/收益，不假称真实账户持仓或订单成交。

所有候选保留首个出现板下价的分钟结束标签 first_open_bar_end，表示这一分钟内打开过；不是秒级开板时刻。首次完整形态、锁定及最终模拟买入的确认时间各自保留。首次开板不等于actionable。
若某个候选的必要分钟缺失，程序等待，不能擅自忽略它而选择另一只。接口失败写日志并重试，始终保留已确认身份和价格；数据修订导致结果改变时拒绝覆盖并报错。

## 模型与规则

固定RF240棵、depth7、leaf50、max_features0.8、seed20261013，中位数填补和缺失指示，平衡权重，sklearn1.7.1。
六个月训练、两个月验证、下一月测试/推理。训练只用历史D1开盘激活且三日标签齐的样本，D0推理不预知D1；第一阶段保留原S/Y排除口径，执行端按D0/D1历史N/R过滤，不补Top4。
形态保持R24的79条至多两条件规则和原筛选流程：因果折外Top1训练、两月验证、三段稳定性、样本保留、命中与收益约束及Wilson评分，迁移Top3。−8%后置过滤不替补。
已有月份模型/规则只加载和校验。进入新月后先完成该月训练和选择，保存实际训练样本、权重、验证与折外预测、形态样本、审计和哈希，确认可回读才启用。缺必要分钟数据时失败并等待补齐，不能静默沿用旧月份。

## 数据与表

生产模型定义仍在 `automl_strategy_definition`，定义ID为 `B2`。执行身份为
`strategy_name=涨停冷静`、`model_version=v1.0`，由 `readable_strategy_execution.py` 统一映射。
`strategy_daily_run` 保存每天运行和模型哈希，`strategy_execution` 保存原Top3及模拟买卖。
`environment_json` 保存模型月份、训练/验证窗口、规则哈希及来源；`details_json` 保存评分、因子、形态和筛选原因。
旧 `automl_*` 执行表仅供追溯，当前任务不再往旧表双写。

```sql
SELECT signal_date,selection_rank,stock_code,stock_name,actionable,
 JSON_EXTRACT(details_json,'$.score') AS score,
 JSON_UNQUOTE(JSON_EXTRACT(details_json,'$.first_open_bar_end')) AS first_open_bar_end,
 JSON_UNQUOTE(JSON_EXTRACT(details_json,'$.actionable_confirmed_at')) AS confirmed_at,
 buy_at,buy_price,sell_at,sell_price,gross_return_pct,net_return_pct
FROM strategy_execution
WHERE user_id='imac' AND strategy_name='涨停冷静' AND model_version='v1.0'
ORDER BY signal_date DESC,selection_rank;
```

`./b2.sh report --day YYYY-MM-DD` 读取该D0信号日的最新数据库事实。默认输出中文紧凑摘要：
排名、评分、四K形态、actionable、模拟买卖、实际确认时间和筛选原因；`--full-output` 可读完整明细。
盘后重复运行也从数据库输出当前买卖事实，避免把已买入股票重新显示为未激活。

## 历史回填

独立脚本 `scripts.backfill_automl_b2` 先 `prepare` 再 `write`，两个命令均需
`--assets assets --output outputs/backfill_TAG --env-file /path/to/.env`，日期用 `--start`、`--end` 指定D0。
prepare只读数据库，使用对应月份的冻结模型和规则，逐条复现原评分/排名/交易，再生成带哈希的待写材料。
write整批事务写入，重跑不会重复插入；已存在的实时日保留。首次write自动保存本用户现存记录到db_before.json，再执行写入。
最后运行同参的 `verify`，独立回读身份、评分、明细、价格及收益，并核对既有记录未改变。

历史行明确记录 `environment_json.source=historical_replay` 与 `details_json.historical_replay=true`。
买卖都是回放模拟价，实际实时确认时间留空。未到D2不提前结算；原研究缺分钟的候选保留名单并注明审计排除。
历史交易日取已完成的全市场日线快照，未来D1/D2取服务器交易日历，避免仅有当年日历时漏回填往年。
本脚本不重新训练，也不改B2选股与买卖规则。

日线缓存从经核验的研究快照增量追加；源种子daily.joblib不改，追加到live_daily.joblib。
新月模型有独立manifest。SEED_MANIFEST只描述最初交付资产，不把后来追加的缓存冒充原资产。
源表历史ST按begin_date≤当日<end_date查；名称用于显示，可为当前名称，不把名字充当历史ST证据。

## 部署和恢复

打包 `python -m scripts.package_automl_b2`。包不含.env、密钥、虚拟环境。
在独立release目录解压，核对MANIFEST.json和资产清单，复制已有Linux1.7.1虚拟环境到B2独立.venv，补装固定zstandard0.25.0；原服务依赖不改。
先执行verify_automl_b2复现历史，再register建表注册定义，close生成当日模拟候选，回读数据库后安装B2自己的cron块。
原收开盘服务目录及cron保留。停止B2只需删除B2的cron块；模型/记录保留可审计，不删除生产表。
部署回执和实际cron以本次docs中的服务器记录为准，本文件本身不表示已部署。
