# 策略执行表：中文策略名与独立模型版本

2026-10-16 设计。SQL：`deploy/sql/strategy_execution.sql`；写入契约：`scripts/strategy_execution_store.py`。本次发布获用户批准，迁移和任务切换的实际结果见 `deploy/server/ai-agent-kingdomai/RELEASE_20261016_STRATEGY.md`。

## 一眼看懂一条选股

```text
策略名      模型版本  版本说明                 信号日       排名  股票          可执行  信号价  买价  卖价  毛收益
收盘开盘    v1.0      20日滚动七因子·14:40选股  2026-10-15   1    002636 金安国纪  0       71.09   —     —      —
涨停冷静    v1.0      四K形态后延后一根K模拟买入 2026-10-15   1    …             0       …       —     —      —
```

`strategy_name` 就用“收盘开盘”“涨停冷静”这样的中文业务名称，是模型序列的身份；`model_version` 是该序列中经确认值得固定的一版规则，例 `v1.0`、`v1.1`。每天重训、模型文件哈希变化、运行机器或 sklearn 小版本号，都不自动升策略版本。若规则和选择效果经验证值得保留，再显式新增版本。`version_desc` 用一句短话说清本版核心变化，长公式、因子、概率、运行证据进入 JSON 或训练档案。版本号不代表已经证明有收益；当前收盘开盘的实时脚本仍未证明复现冻结 B1，登记时应在说明和证据中明确。

## 表的职责

| 表 | 一行代表 | 常用标准列 |
|---|---|---|
| `strategy_daily_run` | 一个用户、中文策略、模型版本在某个信号日的一次成功运行，包括零选股 | `strategy_name`、`model_version`、`version_desc`、`signal_date`、`signal_at`、`candidate_count`、`selection_count`、`model_sha256`、`environment_json` |
| `strategy_execution` | 当日第 N 名股票的选股决定及后续买卖价格回填 | 同样的中文身份与版本、`selection_rank`、`stock_code`、`stock_name`、`actionable`、信号/买入/卖出时间与价格、价格来源、毛/净收益、`details_json` |

`actionable` 缺省为 1；轮空或观察位明确写 0。未买、未卖、未回填的价格与收益都是 `NULL`，不填 0，也不让看板临时推算。`buy_price_basis`、`sell_price_basis` 说明是市场行情代理价还是券商成交回报；没有券商记录时不能把模拟价称作真实成交。`details_json` 是 MySQL JSON 列，PyMySQL 读出时可当 JSON 字符串处理，不为单个策略扩几十个专属列。

`details_json` 可保存模型类别概率、筛选门槛、四K形态、退出规则、原始实验 ID 与证据路径。例如：

```json
{"predicted_class":"ge3","turnover_gate_pass":false,"legacy_strategy_id":"rolling20_7f_hgb_sklearn171_dbapi_amount_v2"}
```

旧 ID 只是溯源属性，不再出现在新表的主键或日常筛选入口。策略运行环境放在 `strategy_daily_run.environment_json`，避免每只股票重复一份机器和依赖信息。

## 迁移与发布边界

旧 `automl_strategy_selection` 有四分类专属字段，B2 已改写旧 `automl_strategy_execution`；两套旧表都先保留。上线时先确认中文名、版本号和短描述，再只迁入**正在运行且口径已核对**的序列：当前 1.7.1 收盘开盘与已冻结 B2。`rolling20_..._v1`、X6 等历史实验不因 ID 不同就自动占用新模型版本，继续从旧表追溯。迁移按用户、策略名、版本、信号日、名次核对行数、股票、`actionable`、价格和收益；每个定时任务切换到新表后再让看板以新表为主，避免旧任务继续写入造成两边分叉。

10 月 16 日生产库只读盘点建议的首批映射如下，版本号和短描述供发布前审阅：

| 旧表中的来源 | 新策略名 | 模型版本 | 核心改动短描述 | 当前行数 |
|---|---|---|---|---:|
| `automl_strategy_selection` 的现行 sklearn 1.7.1 序列 | 收盘开盘 | `v1.0` | 20日滚动·七因子四分类 | 3 |
| `automl_strategy_execution` 的 B2 冻结序列 | 涨停冷静 | `v1.0` | 四K形态后延后一根K模拟买入 | 3 |

两条旧收盘开盘实验序列合计 6 条 selection 暂留旧表，不给它们虚构 `v1.1` 或 `v1.2`。迁移来源 ID 放 `details_json.legacy_strategy_id`；B2 当前 `decision_json` 和收盘开盘原有概率/因子细节也转入 `details_json`，不丢可追溯信息。这里的 `v1.0` 是新执行表的首版登记号，不代表收盘开盘线上效果已经通过 B1 复现核验。

2026-10-16 用户明确批准按本设计更新数据库、线上服务分支与服务。迁移脚本 `scripts/migrate_strategy_execution.py` 默认只读，`--execute` 才创建和迁入。收盘开盘使用 `readable_strategy_execution.py` 回填次交易日09:40；涨停冷静在自身 scan/settle 任务写新表，D2收盘结算并单列净收益。旧表保留。看板以新表为权威，旧实验留在旧表，不占正式版本。
