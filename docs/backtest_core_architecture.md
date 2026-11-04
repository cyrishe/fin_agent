# Fin Agent 独立回测核心：架构与边界

## 1. 当前定位

本模块是一个**独立、确定性、日频、组合级**的回测核心。它先解决最稳定的共同问题：

> 对需要模拟资金与持仓的策略，上游最终统一为“某个收盘时点希望持有什么、各占多少”，再由回测核心转换为订单、成交、持仓、资金和指标。只观察信号后续表现的 Event Study 不进入这个交易账本，也不强行转换成目标组合。

当前核心位于 `src/backtest/`，不依赖 Flask、LLM、数据库、现有 Tool/Skill 路由或前端，可以用内存行情独立运行和测试。外围已经有受限的真实 A 股 Buy & Hold 数据适配器，以及 selection Tool / Custom Tool 到账本的桥接纵切面；它们仍缺完整数据快照、用户运行治理和产品入口，不能被描述为生产可用的 A 股回测系统。

### 1.1 目标

- 用一个稳定抽象覆盖固定股票组、固定权重组合、定期调仓和动态选股。
- 严格区分“策略想要什么”和“市场最终成交了什么”。
- 采用明确的时点规则防止未来数据泄漏。
- 形成可追踪的 `决策 → 订单 → 成交 → 账本 → 指标` 证据链。
- 对手续费、税费、滑点、整手、停牌/禁止买卖和现金不足做确定性处理。
- 保持核心业务中立，让未来的 Tool、Skill、自然语言和文件输入通过适配器接入。
- 在相同数据、配置和确定性策略输入下得到可复现的结果与指纹。

### 1.2 当前非目标

- 不在回测核心里理解用户自然语言、附件或 Tool/Skill 名称。
- 不让策略直接改现金、持仓或伪造成交。
- 不把回测实现成 Normal QA、金融数据查询 Tool 或某个业务 Skill 的内部特例。
- 不在第一版支持分钟/Tick、做空、融资融券、期权、期货、杠杆和复杂订单。
- 不在第一版实现分布式参数搜索、实盘交易、定时任务或前端报告编排。
- 不用简化规则冒充真实 A 股撮合、公司行动或历史成分股能力。

## 2. 对成熟框架的取舍

本设计借鉴成熟框架的稳定思想，但不直接嵌入一个大型框架：

| 框架 | 借鉴内容 | 当前没有照搬的内容 |
| --- | --- | --- |
| [QuantConnect LEAN Algorithm Framework](https://www.quantconnect.com/docs/v1/algorithm-framework/overview) | 把组合目标与执行分离；目标组合是策略和交易之间的稳定交接物 | Universe、Alpha、Risk、Execution 的完整对象体系和实盘基础设施 |
| [Qlib Strategy/Executor](https://qlib.readthedocs.io/en/latest/component/strategy.html) | 策略产生交易意图，由独立执行和交易账户落实；适合承接排序、Top-N 等研究结果 | 完整研究平台、嵌套执行器和数据基础设施 |
| [Backtrader Order Creation & Execution](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/) | 当前 Bar 收盘已经发生，收盘后产生的市价单只能在下一根 Bar 的开盘执行 | Broker、Indicator、Observer 等完整运行时 |
| [Zipline Data Bundles](https://zipline.ml4trading.io/bundles.html) | 交易日历、数据快照与可复现性必须是一等输入 | Bundle 摄取、资产数据库和整套生态 |
| [vectorbt Portfolio](https://vectorbt.dev/api/portfolio/base/) | 未来可作为大量参数组合、独立样本的向量化加速器 | 不用向量数组替代当前逐日账本和成交证据链 |

核心取舍是：**以逐日、事件顺序明确的组合账本作为正确性主线；把向量化扫描留作可替换的加速层。**

## 3. SOFT → HARD → SOFT 边界

回测不是一个先天属于 Tool 或 Skill 的业务，它更适合作为多个入口共用的金融运行时。

SOFT 层先判断用户要“看信号表现”还是“模拟持有”。只有后者进入下面的组合账本；这个选择由用户目标和策略输出能否表达完整持仓共同决定，不能只根据 Tool family 自动分类。

```text
用户问题 / 附件 / Tool 输出 / Skill 输出
                 │
                 ▼
       SOFT：理解目标和业务语义
       - 股票范围、组合、策略含义
       - 回测区间、调仓意图
       - 用户指定的费用或展示要求
                 │
                 ▼
       HARD：稳定、可执行的回测协议
       - MarketData / BacktestConfig
       - Strategy / TargetPortfolio
       - ExecutionModel
       - Decision / Order / Trade / Ledger
                 │
                 ▼
       SOFT：金融化解释与场景化呈现
       - 先讲结论，再讲收益风险
       - 展示净值、回撤、调仓和异常
       - 根据结果规模分页、摘要或下钻
```

系统层只固化会影响执行正确性的事实，例如日期、标的、权重、订单数量、成交价格和费用。`reason`、`evidence` 允许策略携带自然语言理由和证据，但不会参与账本运算，也不会因为表达方式不同阻断主流程。

## 4. 统一抽象：`TargetPortfolio`

策略唯一需要回答的问题是：

> 在当前可见数据截止时点，下一次执行后希望完整组合处于什么目标权重？

```python
TargetPortfolio(
    weights={"600519.SH": Decimal("0.6"), "000858.SZ": Decimal("0.3")},
    reason="按策略评分选出前两名",
    evidence={"score_date": "2026-01-05"},
)
```

它的语义是：

- `weights` 是**完整目标快照**，不是增量买卖指令。
- 当前持有但未出现在 `weights` 中的股票，目标权重为 `0`，会产生卖出计划。
- 权重必须非负，总和不得超过 `1`；未分配部分保留为现金。
- `TargetPortfolio(weights={})` 表示目标为空仓，即清仓。
- 策略返回 `None` 表示本日不调仓，继续持有当前组合。这与“清仓”严格不同。
- 策略不能直接指定虚构成交价、修改现金或跳过执行模型。

因此，不同的**组合模拟**输入可以在上游自然映射到同一协议：

| 用户或上游能力 | 到回测核心的映射 |
| --- | --- |
| “回测这三只股票，等权买入并持有” | `FixedWeightStrategy` |
| 已知股票组合和权重 | `FixedWeightStrategy` |
| 每月按固定权重再平衡 | 带 `rebalance_every` 的固定权重策略 |
| 外部 Tool/Skill 已算出每期组合 | `ScheduledTargetStrategy` 或自定义 Strategy 适配器 |
| 每期从股票池按评分选 Top-N | 评分结果 → `TargetPortfolio` |
| 动态调节股票比例 | 自定义 Strategy 每个调仓日输出新目标快照 |

这个抽象保护了上游 Tool/Skill 的干净：它们只负责选择与配置组合，不需要了解手续费、现金争抢、持仓成本或账本细节。

## 5. 精确的日频时序

第一版只采用一种不含糊的时序：

```text
D 日开盘         D 日盘中              D 日收盘后               D+1 日开盘
   │                 │                     │                         │
   │                 │             只读取截止 D 收盘的数据           │
   │                 │             Strategy 输出目标权重              │
   │                 │             按 D 收盘价和 D 日权益冻结股数      │
   │                 │                     ├──── DAY 订单 ────────────►│
   │                 │                                               执行卖单
   │                 │                                               再公平分配买单现金
   │                 │                                               按开盘价及成本模型成交
```

具体规则如下：

1. D 日先完成已有订单的开盘成交，再以 D 日收盘价生成当日组合快照。
2. `MarketView` 只允许策略访问不晚于 D 日的数据；访问未来日期会产生 `future_data_access` 错误。
3. 策略在 D 日收盘后输出 `TargetPortfolio`。
4. 引擎用 **D 日收盘权益和 D 日收盘价**计算目标股数，并按标的整手向下取整。
5. 买卖数量在 D 日即被冻结，绝不读取 D+1 开盘价反推“本来应该买多少”。
6. 订单在 D+1 开盘用实际开盘价、滑点和费用模型尝试一次。
7. D+1 跳空可能使冻结的买单超出可用现金，此时只能缩减成交数量，不能回头改写 D 日决策。
8. 订单为 DAY：缺 Bar、不可交易或买卖受限时，当日过期并记录 issue，不做隐式重试。
9. 完整目标中任一正权重标的缺少 D 日参考 Bar 时，整次目标不生成订单；不能只卖旧仓却跳过缺数据的新仓。
10. 最后一个交易日不会再产生无法执行的下一日决策。

当前 `BacktestConfig.start_date` 是首个估值与决策会话，不表示“在该日开盘前已经持仓”。因此固定组合会在首日收盘产生目标、下一交易日开盘建仓；若报告必须从已持仓状态开始，后续适配器应显式加入前一交易日决策和独立的报告截取范围，不能偷偷假设期初已成交。

这一时序避免了常见的“用 D 日收盘信号，却假设自己能以 D 日收盘价成交”以及“先看到 D+1 开盘价，再决定 D 日订单股数”的前视偏差。

## 6. 模块与稳定协议

```text
MarketData
    │
    ▼
MarketView ──► Strategy ──► TargetPortfolio
                              │
                              ▼
                    目标股数与 Order 规划
                              │
                              ▼
ExecutionModel ─────────► Trade / Issue
                              │
                              ▼
                   Ledger / PortfolioSnapshot
                              │
                              ▼
                    Metrics / BacktestResult
```

### 6.1 `contracts.py`

定义跨模块稳定传递的事实：

- 输入：`Instrument`、`Bar`、`BacktestConfig`、`TargetPortfolio`
- 执行证据：`DecisionRecord`、`Order`、`Trade`、`ExecutionIssue`
- 状态结果：`PositionSnapshot`、`PortfolioSnapshot`
- 汇总：`BacktestMetrics`、`BacktestResult`
- 稳定错误：`BacktestError(code, message, details)`

`BacktestResult.to_dict()` 负责把日期和 `Decimal` 转成稳定、可序列化的数据。结果同时保存引擎版本、数据指纹、运行指纹、策略说明、执行模型和显式假设。

### 6.2 `data.py`

- `MarketData` 是行情数据端口，只约定交易日历、标的、Bar、Instrument 和数据指纹。
- 交易日历必须是严格递增且不重复的 `date` 序列；核心会拒绝乱序适配器，不能排序后掩盖“信号去过去成交”的错误。
- `MarketView` 是策略可见的时点切片，从接口上阻止读取未来数据。
- `InMemoryMarketData` 是当前独立实现与测试使用的确定性数据源，不是生产数据库适配器。

### 6.3 `strategy.py`

- `Strategy` 只需实现 `reset()`、`on_close()` 和 `describe()`。
- `FixedWeightStrategy` 覆盖买入持有或定期固定权重再平衡。
- `ScheduledTargetStrategy` 承接外部 Tool/Skill 在指定日期生成的目标组合。
- `TopNMomentumStrategy` 是验证动态选股和时点数据边界的参考实现，不应被视为投资建议或内置推荐策略。

### 6.4 `execution.py`

`ExecutionModel` 把订单和执行日 Bar 转为成交报价。当前 `BpsExecutionModel` 显式支持：

- 双边佣金率与最低佣金；
- 卖出税率；
- 开盘价基础上的滑点；
- 只以执行时已知的开盘价计算成交报价，不读取开盘后的当日最高价或最低价。

费用参数是每次运行的明确输入，核心不会声称某组默认值就是某个历史时期的真实费率。引擎还会校验可替换模型返回的成交金额、费用、滑点和现金变化是否满足会计恒等式，避免错误插件凭空创造现金或资产。

### 6.5 `engine.py`

`BacktestEngine` 串联时序、订单规划、执行和账本。它按日期串行处理同一个组合，因为同日卖出所得现金会影响买入，而买入又共同受一个现金账户约束。

### 6.6 `metrics.py`

当前从真实账本快照与成交计算：

- 总收益率、年化收益率；
- 年化波动率、夏普比率；
- 最大回撤；
- 成交金额口径的换手率；
- 交易次数、佣金、税费和滑点成本。

指标是结果的确定性汇总，不替代决策、订单和成交明细。

## 7. 账本与共享现金的公平性

### 7.1 唯一权威账本

现金、持仓数量、平均成本和已实现盈亏只由成交驱动：

- 买入：现金减少，持仓增加，佣金进入持仓成本。
- 卖出：现金增加，持仓减少，卖出费用和税费进入已实现盈亏。
- 不允许负现金、卖出超过持仓或无成交直接修改持仓。
- 每日收盘用当日收盘价估值；持仓缺少当日 Bar 时使用最后已知价格，并显式记录 `stale_mark`，不会静默伪造数据。

### 7.2 同日顺序

同一交易日严格采用：

1. 卖单按标的稳定排序执行；
2. 卖出所得现金进入共享账户；
3. 收集全部可执行买单；
4. 判断全部冻结买单在真实开盘价、费用和滑点下是否可支付；
5. 若现金不足，对所有买单应用相同缩放比例，再分别按整手向下取整；
6. 用最大余数法按稳定规则分配仍可支付的少量剩余整手；
7. 记录每张被缩减买单的 `cash_constrained` 证据。

因此，现金不足时不是逐只贪心买入，而是先形成对整批订单都一致的比例基线，再处理不可分割的整手余数。余数相同时才用标的稳定顺序打破平局，输入列表顺序不会改变结果。最终仍可能因整手和最低佣金留下现金，这是可解释的执行结果。

独立回测之间、不同参数组合之间可以并行；同一组合、同一交易日内部不能并行修改账本。

## 8. 真实数据接入要求与偏差控制

生产级结果的上限由数据质量决定。真实适配器至少需要提供：

1. **权威交易日历**：不能用自然日或某张偶然缺数的行情表猜交易日。
2. **时点正确的 OHLCV**：执行价与估值价的口径必须明确。
3. **复权与公司行动**：拆并股、分红、配股等需要统一处理；不能把复权收益和未复权成交数量随意混用。
4. **历史证券状态**：上市、退市、停牌、ST、代码变更。
5. **历史交易限制**：涨跌停、是否可买/可卖、整手和特殊卖出规则。
6. **历史费用制度**：佣金、最低佣金、印花税等应随时期和市场配置。
7. **时点股票池**：指数成分、行业分类和筛选范围必须使用当时可知版本，避免幸存者偏差。
8. **时点基本面/研报数据**：只能在真实披露或可用时间之后进入策略，不能按报告期日期提前使用。
9. **基准与无风险利率**：用于后续超额收益、信息比率等比较指标。
10. **数据版本标识**：数据源、快照时间、复权方式和指纹应随结果保存。

需要重点防止的偏差包括：

- 未来函数和收盘价同 Bar 成交；
- 幸存者偏差；
- 复权、分红与持仓数量口径不一致；
- 停牌或涨跌停仍假设全部成交；
- 用今天的行业/指数成分回测历史；
- 缺失数据静默填充；
- 忽略交易成本和容量；
- 反复试参后只展示最佳结果。

当前内存数据源适合手工可核验的单元测试和协议验证，不足以给出生产投资结论。

## 9. 当前明确限制

- 只支持日频、开盘执行的市场单近似。
- 固定 bps 滑点只依赖开盘价；当前没有开盘集合竞价价格带或盘口数据，因此不会用执行日全天 high/low 做事后截断。
- 只支持多头、无杠杆、单一共享现金账户。
- 未实现 A 股持仓 T+1 卖出限制。
- `can_buy`、`can_sell` 和 `tradable` 必须是执行日开盘时点可知的布尔事实，由数据适配器提供；核心不会自行推断涨跌停或停牌，也不会接受 `"false"` 之类含糊字符串。
- 不模拟成交量容量、冲击成本、盘口、部分成交和订单跨日生命周期。
- 不处理分红、拆并股、配股、退市结算等公司行动。
- 不支持多币种、汇率、融资利息、借券和衍生品。
- 固定股票组合的产品适配层已支持默认沪深300、用户指定主指数和 CC 补充相关指数，并计算同区间超额收益、跟踪误差和信息比率；这些比较保持在账本核心之外。行业/风格暴露、收益归因以及任意策略运行的统一比较仍未实现。
- 暂无持久化运行、断点恢复、参数搜索和分布式任务调度。
- `src/backtest/` 核心本身不直连 `aiia_trade_calendar`、行情数据 API 或研报数据库；交易日与点时股票范围适配位于外围 `Strategy Run Wrapper`，避免数据库能力反向污染账本协议。
- 已有选股 Tool 到回测核心的隔离桥接，以及固定 revision Custom Tool 经真实 Runtime 协议、受控历史 finance bridge 到组合账本的纵切面。当前仅支持固定 targets 和 `stock.quote.dynamic_cal` 的交易日绑定原始行情列；当前名称/ST 标签、分钟字段和后复权列均拒绝。任意用户策略的产品级历史回放仍未开放，在用户运行 ownership、API/列级 point-in-time 声明和资源治理完成前，不接入 Normal QA 的正式策略回测入口。
- 当前 `point_in_time` 保证特指 Host 所代理金融数据的可见性截止，不等于任意 Tool 代码完全确定性；策略若自行使用系统时钟或随机数，结果指纹可以揭示差异，但运行时尚未冻结这些输入。
- 历史 Host 不沿用资产的 `local_dev`，而把 formal sandbox 选择作为服务端执行事实；没有可用安全后端时 fail closed。本地纵切面使用的是显式受控测试运行时，不构成生产隔离证明。
- 线上 `dynamic_cal` 仍按请求生成计算代码；虽然数据列已受历史边界控制，生成逻辑尚未随策略 revision 冻结。正式回放需固定并指纹化它，或改用确定性原始行情接口。

这些限制应作为结果假设展示，不能用补丁式默认值掩盖。

## 10. 后续接入点

建议按以下顺序演进，保持核心不耦合：

### 10.1 从受限真实适配器到可重放 `MarketData` Snapshot

Buy & Hold 外围已经能读取 `aiia_trade_calendar` 和真实未复权日线，并对重复 symbol/date fail-fast；策略回放仍需要 Fin Agent 数据层补齐：

- 用 `aiia_trade_calendar` 提供明确的交易会话序列；
- 查询历史 OHLCV、证券状态和交易限制；
- 明确复权、公司行动和数据截止口径；
- 生成稳定数据指纹。

核心只消费 `MarketData` 协议，不知道数据库连接、API Catalog 或 SQL。

### 10.2 Tool/Skill 目标组合适配器

当前 ranked selection → Top-N → equal weight 桥已经存在，并拒绝按多次调用顺序伪造排名。其他 Tool 或 Skill 输出仍应先保留其业务语义，再由小型、明确的适配器转换：

```text
评分 / 入选股票 / 建议权重 / 调仓日期
                  │
                  ▼
      ScheduledTargetStrategy 或自定义 Strategy
                  │
                  ▼
             TargetPortfolio
```

逐股独立输出只有在存在明确的聚合/排名政策时才能形成一个目标组合；不能把调用顺序当排名，也不能因为可以并行就自动解释为组合策略。单股票 Tool/Skill 的公开输入协议仍不需要承载回测循环。

### 10.3 Agent 编排

普通自然语言请求继续走 `investment_analyst + normal_qa → FinancialQaCcService`，不新增顶层 `backtest` mode；CC/Agent 负责判断用户是在：

- 回测固定股票组；
- 回测已有组合；
- 用某个 Tool/Skill 周期性选股；
- 比较少量策略或参数；
- 继续追问某次回测的调仓和异常。

完成 SOFT 理解后，编排层构造稳定回测输入并启动专用运行服务。显式 `$Tool` 的当前 ToolPlan 路径只执行 active revision，不能直接拿来做历史回放。执行期间可把“数据准备、策略决策、订单执行、指标汇总”等阶段事件发送到思考区域，但这些展示事件不进入核心账本协议。

### 10.4 金融 Renderer

Renderer 从 `BacktestResult` 按信息层次呈现：

1. 结论摘要和适用假设；
2. 收益、回撤、波动、换手和成本；
3. 净值/回撤图；
4. 关键调仓与贡献；
5. 未成交、现金受限、陈旧估值等问题；
6. 可下钻的决策、订单、成交和每日持仓。

结果过多时先摘要和异常，再分页或提供明细文件，不把全部流水直接塞入对话。

### 10.5 研究与性能层

- 现有 `quant_research` 可以产出因子分数或目标权重，但不直接改写账本。
- 大量彼此独立的策略/参数组合可并行运行。
- 若规模需要，可增加 vectorbt 风格的向量化预筛；候选结果仍应由同一账本核心复核，避免性能实现改变成交语义。

## 11. 最小独立示例

下面示例不访问数据库。策略在 2026-01-05 收盘后产生 50%/50% 的目标组合，订单数量按当日收盘价冻结，在下一交易日开盘成交。

```python
from decimal import Decimal

from src.backtest import (
    BacktestConfig,
    BacktestEngine,
    Bar,
    BpsExecutionModel,
    FixedWeightStrategy,
    InMemoryMarketData,
    Instrument,
)


def bar(date, symbol, open_, close):
    high = max(Decimal(open_), Decimal(close))
    low = min(Decimal(open_), Decimal(close))
    return Bar(
        date=date,
        symbol=symbol,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume="1000000",
    )


data = InMemoryMarketData(
    bars=[
        bar("2026-01-05", "AAA", "10.0", "10.0"),
        bar("2026-01-05", "BBB", "20.0", "20.0"),
        bar("2026-01-06", "AAA", "10.2", "10.5"),
        bar("2026-01-06", "BBB", "19.8", "20.2"),
        bar("2026-01-07", "AAA", "10.6", "10.8"),
        bar("2026-01-07", "BBB", "20.1", "20.0"),
    ],
    instruments=[
        Instrument("AAA", lot_size=100),
        Instrument("BBB", lot_size=100),
    ],
    calendar=["2026-01-05", "2026-01-06", "2026-01-07"],
    source_name="minimal_example",
)

strategy = FixedWeightStrategy({"AAA": "0.5", "BBB": "0.5"})
result = BacktestEngine().run(
    data=data,
    strategy=strategy,
    config=BacktestConfig(
        universe=["AAA", "BBB"],
        initial_cash="100000",
    ),
    execution_model=BpsExecutionModel(
        commission_rate="0.0003",
        minimum_commission="5",
        sell_tax_rate="0.0005",
        slippage_rate="0.0002",
    ),
)

print(result.metrics)
print(result.to_dict()["trades"])
print(result.run_fingerprint)
```

这个例子刻意保留完整过程：固定组合仍先生成 `TargetPortfolio`，再形成订单并进入同一个执行和账本主线，不为“简单买入持有”另开一套捷径。

## 12. 一句话原则

**模拟持有时，策略负责“想持有什么”，执行负责“市场允许成交什么”，账本负责“实际拥有什么”；只看信号表现时不伪造账本；Renderer 负责把两类结果讲清楚。**
