# Stock AutoML：独立量化研究任务

这是一个可运行、有限预算的研究框架，入口是 `python -m src.quant_research.automl`（`scripts/run_stock_automl.py` 保留兼容入口）。读取 KingdomAI，生成可追踪的样本、实验、模型、预测、回测与评审报告。没有接入生产聊天路由、定时调度或实盘交易，也不修改数据库。

遵循仓库的 SOFT → HARD → SOFT：自然语言研究目标 → `ResearchSpec` 执行约束和实验结果 → 大模型解释。自然语言不拆成业务 JSON 树；程序必须实际消费的日期、模型、周期、阈值、成本和预算才成为字段。大模型只从已有实验候选中选择和解释，不能生成执行代码、SQL、修改数据源、预算或指标。

## 快速运行

在仓库根目录，使用 Python 3.10+：

```bash
python -m venv .venv-automl
.venv-automl/bin/python -m pip install -r requirements-automl.txt
.venv-automl/bin/python scripts/run_stock_automl.py --demo --max-trials 6
.venv-automl/bin/python scripts/run_stock_automl.py --inventory
.venv-automl/bin/python scripts/run_stock_automl.py --config docs/examples/stock_automl.json --llm
```

`.env` 只加载到进程，不打印凭据。数据库 URL 按 `KINGDOMAI_DB_URL`、`BI_DB_URL`、`BUSINESS_DB_URL` 顺序解析，schema 必须为 `kingdomai`；也可通过 `--db-env ENV_VARIABLE_NAME` 指定。数据库连接使用只读一致性快照事务、超时、参数绑定与行数上限，超过上限报错而非静默截断。连接账号仍应由运维配置为只读。

`--llm` 使用现有 `LLM_BASE_URL/LLM_ENDPOINT`、`LLM_API_KEY/LLM_KEY`、`LLM_DEFAULT_MODEL` 的 Chat Completions 兼容接口，单次调用有连接和读取超时。当前 env 是 DeepSeek。只发送候选及汇总指标，不发送原始行情、新闻正文、数据库地址或凭据。不启用 LLM 时依然可以自动实验，`objective` 仅记录，实际筛选依据 JSON 中的显式字段。

输出位于 `.gitignore` 已排除的 `outputs/stock_automl/<run_id>/`。不要把本地市场样本、模型或预测表提交到仓库。合成 demo 只能证明程序可运行。

## 数据与特征

| 数据 | 当前适配 | 时点处理与边界 |
|---|---|---|
| `kcrp_stock_price` | 动量、波动、振幅、开盘缺口、均线距离、量能、成交额、换手率；复权收益标签 | 当日收盘后 15:10 决策；所有滚动特征仅使用过去。供应商复权版本仍可能修订 |
| `kcrp_stock_pricevaluate` | 历史总市值、PE、PB | 保守设为次日可用；没有原始修订版本，不能声称严格历史财务信息集 |
| `kcrp_stock_industry` | SW2021 一级行业类别、行业分组评估 | 使用 begin/end 生效区间，不用今天成分回填；历史分类修订仍有局限 |
| `kcrp_news_info` | 已入库新闻数量的7日热度代理 | 日期型公告次日才可用，并取入库时间较晚者；无新闻日明确补零，不延续上次热度 |
| `aiia_stock_realtime_minute_snapshot` | 完整1分钟bar的日内波动及覆盖数量，`--minute` 开启 | 仅用已完成且不晚于15:00的bar，15:05后可用；缺表明确披露 |
| 任意其他表的历史特征 | `--events-csv` 或 Python `events` 输入 | 必须先生成 `symbol,available_at,<数值特征>`，按真实可得时间向后 as-of 合并；禁止覆盖价格或目标字段 |

`--inventory` 导出当前库完整表字段目录，不自动把所有表拼成训练宽表。财报、资金流、事件、股东等可沿统一事件入口增加；报告期不能冒充披露/可得时间。修订类特征必须在上游保留可得时点，不能用最新历史回填表假装 point-in-time。辅助数值特征自动进入 enriched 特征组。

2026-10-03 实际检查：env 中 `BI_DB_URL` 对应 KingdomAI 有172张表，上述日线、估值、行业、新闻表存在；该连接下没有分钟快照表。因此真实试跑只验证了日线及辅助数据，分钟适配只做本地回归，尚未做真实分钟库验收。分钟特征是收盘决策的补充，不是日内高频交易策略。

未指定股票时，从研究起点已上市、当时尚未退市的 A 股池按 seed 抽样，保留后来退市股票；不按期末收益挑样本。指定 `symbols` 则使用用户股票池，不能据此外推到全市场。交易日历目前取股票池日线日期的并集；单股票缺日补空，不跨停牌日错移标签。生产级研究应补充权威交易日历及退市清算数据。

## 模型、目标与自动探索

- `linear`：逻辑回归 / Ridge 回归，作为简洁基准。
- `elastic_net`：弹性网逻辑回归 / Elastic Net 回归，适合相关特征较多的比较实验。
- `tree`：决策树；`forest`：随机森林。
- `svm`：SVC / SVR；为控制内核模型复杂度，训练最多4000行。
- `hist_gradient_boosting`：直方图梯度提升分类 / 回归。关闭其随机内部早停，使用外部时间验证。

这些都是通用统计学习模型，不保证对股票有效。LightGBM/XGBoost、分位数回归、排序学习等可以后续作为模型注册项加入；首版不引入其额外依赖，也不做深度学习。

自动候选组合：持有期 × 任务 × 模型 × 样本机制 × 特征组 × 参数档位。样本机制包含全样本、动量、反转、放量流动性、低波动、新闻活跃。首轮覆盖不同角度，后续轮根据开发集表现兼顾邻近参数和随机探索；大模型可根据业务指导重新排序有限候选。`max_trials` 是整个任务预算，不是每轮预算；每个候选做 `folds` 次时间验证，分类内部还需独立校准。上限不代表预设全部实验都要运行。

显式约束支持 `industries`、`min_market_cap`、`max_market_cap`、`min_amount`、目标收益、预测概率阈值、收益预测阈值、持有期、模型列表、Top K、最低信号数、最低信号胜率和最大回撤。市值与成交额采用源字段原始单位，设置具体数字前需确认供应商单位。约束需要的特征缺失会报出，未知可选元信息可以兼容忽略。

自然语言是规划指导：如“偏向小盘反转”可影响候选选择，但任意语言目标不会自动变成未经验证的筛选规则。候选空间无法表达的要求应在规划理由中说明；需要精确约束时填写对应机器字段。没有配置的业务目标不暗自承诺已执行。

`horizon=h` 的统一标签是：t 日收盘后决策，t+1 开盘买入，t+h+1 开盘退出。分类预测这一收益是否超过 `target_return`，回归预测收益率。h=1符合次日买入、再下一日卖出的持有期；**不是当日收盘到明日收盘涨跌，也不是期间曾触及目标价的概率**。

## 验证与回测

1. 开始就按 seed 留出公司，并锁定最后20%的时间。留出公司所有历史样本均不参与模型选择、拟合或校准。
2. 开发期按日期滚动扩展训练窗；所有股票共享日期边界。训练标签结束时间必须严格早于下一验证起点，避免多日标签穿越边界。
3. 填缺、缩放、行业编码只在训练集拟合。分类再切出较晚时间块进行 sigmoid 概率校准，并再次清除跨界标签。SVC 不使用随机的内部概率校准。
4. 与训练期常数预测比较 Brier / MSE，输出 AUC、校准桶、RMSE、Rank IC、分月/行业统计。排序采用各折相对常数预测增益的均值减标准差；先比较是否达到指定开发集约束，不保证一定找到合格候选。
5. 在揭盲前保存 `selection.json`。只用一名开发集优胜候选分别评估“新时期老公司”“新时期新公司”，不会用盲测换赢家。若想专门研究某个周期或目标，限制 horizons/tasks 后单独运行。
6. 复用 `src/backtest`：收盘信号、次日开盘执行，按h天换仓，现金约束，逐日市值、费用、滑点、税费及执行问题。与同区间同池等权基准比较；不把重叠标签收益直接连乘成年化收益。

信号胜率是阈值以上的重叠样本统计，**不是成交交易胜率**。Wilson区间仅作描述，重叠收益不是独立样本；组合收益与回撤看回测账本。最低信号数 `min_signals` 约束 signal_count，不是实际成交笔数。没有信号就输出空胜率和零交易，不能把它表述为高胜率。

回测使用复权价格重新定基及1份单位，不完整模拟100股整手、最小佣金、全部历史税费、停复牌/开盘涨跌停队列、现金分红或退市清算。一字涨跌的买卖侧仅保守阻断；OHLC无法认证开盘能否成交。所有输出均是研究证据，尚非生产或实盘放行。

第一次盲测后，反复阅读同一窗口结果并修改规则会污染盲测。后续自适应研究只能使用开发集或新未揭盲窗口；脚本不会声称跨run自动维持保密盲测库。运行维、数据授权、生产放行仍须按根 AGENTS.md 单独提供证据。

## 产物与预测复用

每个run保存：配置、完整可用候选、数据来源与警告、代码/数据指纹、Git ref及dirty标记、库版本、时间及公司切分、源代码副本、合并特征快照、逐轮规划、开发集结果及失败原因、最终选择、模型、两组样本外预测、交易/净值账本、基准、Markdown报告和LLM评审。失败实验保留原因；LLM失败不改写数值结果，回退状态独立记录。

`model.joblib` 是整个预处理、模型和校准器；只加载自己生成且可信的本地模型文件。新行情可通过 Python 复用：

```python
from src.quant_research.automl.inference import predict_latest
signals = predict_latest(run_directory, daily_frame, events)
```

输入至少21个连续交易日（建议60日），列与数据适配器一致。仅对共同最新时点且符合样本约束的股票输出预测；字段包含symbol、date、horizon、target_return、prediction_kind、prediction。模型在旧开发集训练，不在盲测后自动偷用未来标签重拟合；新正式研究需明确新训练窗口。

## 验证命令与参考

```bash
python -m pytest -q tests/automl tests/test_backtest_core.py
```

覆盖正常协议、可选元信息兼容、严重输入拒绝、future特征不变性、缺日标签、标签purge、全部六类模型的分类/回归、只在训练期校准、模型权限边界、盲测不影响候选选择、交易成本、零信号和完整产物。

方法参考：[scikit-learn 时间序列交叉验证](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)、[数据泄漏与Pipeline](https://scikit-learn.org/stable/common_pitfalls.html)、[概率校准](https://scikit-learn.org/stable/modules/calibration.html)。本框架额外按标签结束时间清除交叉区间，并按公司留出；没有直接将股票面板按随机行切分。

模块依赖边界见 [模块README](../src/quant_research/automl/README.md)。本次可提交的真实试跑汇总见 [2026-10-03 运行证据](stock_automl_runs/20261003_smoke/README.md)。
