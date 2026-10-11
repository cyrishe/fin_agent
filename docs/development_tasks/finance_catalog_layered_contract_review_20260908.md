# 金融数据协议分层复核：回应批注与统一说明方案

日期：2026-09-08。范围：本地当前工作区；基线 HEAD 为 `5d775c6`，存在多人未提交改动。

## 一、先给结论

你的核心方向是原有架构本来就应该坚持的：**subject 定对象，dataview 定数据，少数方法类型定操作；具体场景按需组合协议，金融计算由工具实现。**

当前不是“完全没有体系，全靠模型读文字”。已有分层目录、共享调用模板、按 operation 加载和业务算法封装。但也不是“只是我没展示好，系统没有问题”：

- 上一份清单只抽反向句，省掉了调用签名、参数和模板引用，破坏了整体可读性。这是报告的错误。
- 真实说明仍将参数含义、默认值、适用模式和内部实现分散到多个文字位置，局部重复明显。
- 五类方法总纲存在，但不同运行分支没有一致使用；部分参数约束仍分别由文字和代码维护。
- 本轮先确认事实、梳理体系与改动边界；保留你的原批注，未修改运行代码、提示词或执行协议。

## 二、逐条回应你的前四项批注

| 你的批注 | 核实结果 | 确认与处理方向 |
|---|---|---|
| 1. “字段只能用定义的”，是强制约束，可以保留 | 正确。字段与参数的存在性属于执行契约，静态检查已有相应约束 | 保留；前份报告混列，未充分区分执行契约与具体错例的语义纠偏 |
| 2. 最初没有这么多文字，应该结构化 | 基本属实。最早可追溯版本的类规则更短；参数结构从早期就有 | 恢复参数、模式、输出口径的固定组织形式，把追加长句拆回其所属定义 |
| 3. stock_quote_query 是什么？rules 是否包含整个 OP 提示？ | 它是内部调用模板键，真实 API 仍是 stock.quote；不是新 OP。模型目前确实能看到该键。rules 只是说明来源之一 | 展示时以“股票→行情→明细→stock.quote”为主线。内部模板复用可以保留，模型看到的是组装后的当前方法契约 |
| 4. 为什么一来就是日期比较？没有完整方法定义？ | 已有 desc/call_pattern/args/output_rule，我的清单没展示；但 args 是字符串列表，默认值和适用条件确实散在 rules、示例、字段别名等处 | 并非重新发明 API，而是把已有契约按统一格式完整呈现；共用 filter 语法与具体日期字段含义分开 |

证据：[行情模板完整定义](/Volumes/ext/fin_agent/src/tools/finance_data/catalog/api_view_catalog.json:23)、[结构检查](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/call_structure.py:173)、[模型目录组装](/Volumes/ext/fin_agent/src/services/finance_data_tool_catalog_service.py:441)。

“源表”“kline_type”“不重采样”“不差分”这一段确实混入了内部实现说明。模型需要的业务事实是：返回指定周期的 K 线，每根的量额是该区间值；是否完成由 is_finalized 表示。物理表及如何生成 K 线留给 Provider 和开发文档。

### 最初版本的实际变化

下面只比较 stock.quote 引用的**类规则字符串**，不是整轮提示或 token：

| 可追溯版本 | 引用模板 | rules 条数 / 字符数 |
|---|---|---:|
| 019e580 · 2026-07-10 | basic_query | 3 / 166 |
| a255a92 · 2026-08-16 | stock_quote_query | 14 / 912 |
| 50d4b63 · 2026-09-06 | stock_quote_query | 14 / 1006 |
| 本次工作区 | stock_quote_query | 14 / 987 |

8 月版本的首条原句只有“mode=0 查询截至上一交易日的日K；count 表示每只股票返回的最近日K根数。”9 月 6 日把记录数与市场交易日、停牌和显式日期范围追加到同一条。源表与重采样段落则在 8 月引入专用模板时已存在。

所以不能把所有冗长说明都归因于最近几天，也不能声称最早个人原稿已被完整找回；以上是仓库可追溯历史。

## 三、真正的体系：五类业务方法、四种装载 operation、内部契约模板

你记得“五类”是对的。[现有五类通用 API 总纲](/Volumes/ext/fin_agent/src/prompts/codex/finance_api_call.system.md:11)首次进入 Git 是 2026-07-26 的 `39f52f1`。

| 五类业务方法 | 面向数据的用途 | 当前 operation | API 形态 |
|---|---|---|---|
| 基础查询 | 返回匹配的数据明细；筛选、排序、限量 | query | subject.dataview(...) |
| K 日指标 | 按对象返回指定窗口的已定义指标 | window | subject.dataview.kd_字段_方法(...) |
| 成分关系 | 返回主体与其成分证券的对应关系 | query | subject.constitution(...) |
| 聚合查询 | 按分组归并数据；包括成分证券指标聚合 | aggregate | subject.dataview.agg(...) |
| 动态行情计算 | 执行自然语言描述的自定义行情计算 | compute | stock.quote.dynamic_cal(...) |

说明：

- 成分关系在业务上单列，技术装载上属于 query。因此 5 和 4 没有冲突，当前无需新增第五个 operation 枚举。
- 方法类型是复用的操作形态；“最新”“比较”“排名”是用户目标，由模型结合数据粒度选择和组合方法。
- 当前 15 个 api_class_patterns 是参数/输出契约的实现模板，不是 15 种业务操作；其中一个同分钟模板当前没有 API 行引用。
- stock_quote_query 专用模板是为 codes/names/mode/period/count 等差异而拆出的。拆模板有合理原因，模板里堆长句不是必然结果。
- 五类总纲目前由 [Coding 分支装载](/Volumes/ext/fin_agent/src/services/codex_exec_skill_harness.py:510)，并不等于 DSH 查询也拿到了同样清晰的总纲；该旧文档中的具体参数还需按当前 catalog 对齐。

### 一次查询应如何沿体系组织

```text
subject：对象，如 stock / plate / fund
  └─ dataview：数据，如 quote / moneyflow / report_metric
       ├─ 数据定义：一行代表什么、字段含义、单位、时间口径
       └─ 方法：从五类业务方法中选择
            ├─ operation：目录装载分类
            ├─ api_name：真正调用的入口
            └─ 本次契约：共享方法定义 + 当前视图字段 + 方法特有参数/口径

Provider：执行固定查询模板、金融算法、数据源适配
  └─ 结果：数据、实际时间/单位、执行证据及可用引用
```

这不是要求模型再输出一棵对象树，而是目录的组织与按需装载方式。

## 四、当前已经做到什么，哪里确实没有做好

| 环节 | 当前已有实现 | 实际缺口 |
|---|---|---|
| 初始路由 | 工具说明提供 subject/dataview 的功能与范围摘要 | 不同分支还另写业务路由词表，容易重复 |
| 按操作加载 | 选定 subject/dataview/operation 后，只保留对应函数与引用模板 | 仍带整个视图字段集合；同一个 query 内 mode 的参数和字段作用域未清晰组织 |
| 模板复用 | 函数引用 api_class，模板有签名、参数、输出和规则 | 模型要在 function、api_class、view、note 等处拼接同一参数含义 |
| 参数定义 | args 列出必填/可选参数，部分取值写在括号中 | 类型、默认值、适用模式并非完整的统一定义；校验代码仍另有逻辑 |
| 字段与输出 | 有 fields、aliases、desc、output_rule、value_domains | 单位/模式说明常塞进 aliases；输入字段、原始输出和窗口结果字段角色不够直观 |
| 金融特性 | Provider 有固定 SQL 模板及特殊计算分发 | 部分内部机制又以提示文字泄漏出来；跨 Provider 的时间口径也未完全一致 |
| 执行后上下文 | 返回结果引用、样例、执行和恢复信息 | 结果 guidance、阶段提示和常驻提示仍重复带策略 |

源码定位：[初始路由摘要](/Volumes/ext/fin_agent/src/scenarios/financial_qa/tools.py:1885)、[按 operation 裁剪](/Volumes/ext/fin_agent/src/services/finance_data_tool_catalog_service.py:250)、[guidance 与 note 合并](/Volumes/ext/fin_agent/src/services/finance_data_tool_catalog_service.py:489)、[从 args 提取参数名](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/call_structure.py:634)。

### 还原模型实际收到的一个目录包

本次直接调用当前目录投影，未调用模型或数据库：

| 选中目录包 | 函数 / 引用模板 | 视图字段数 | 规则来源 | 紧凑 JSON 字符数 |
|---|---|---:|---|---:|
| stock.quote / query | stock.quote / stock_quote_query | 33 | 主体1 + 视图3 + 模板14 + 示例guidance4 | 5,326 |
| stock.report_metric / aggregate | stock.report_metric.agg / report_metric_aggregate | 24 | 主体1 + 视图5 + 模板5 + guidance1 | 3,885 |
| plate.constitution / aggregate | plate.constitution.agg / constituent_aggregate | 5 | 主体3 + 模板6 + guidance1 | 2,599 |

字符数包含字段与示例，不是 token，也不含完整系统/工具/历史上下文。它证明“整份 439 条清单并未一次塞给模型”，同时也证明“选对局部包后仍有说明分散与重复”。

尤其 stock.quote：目录包列 33 个字段，但当前静态契约按 mode 得到的可用字段分别为 21、24、28 个。[字段适用模式已有代码约束](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/call_structure.py:101)；模型端却主要依靠若干字段别名里的文字识别差异。这是具体的契约呈现缺口，而非只换措辞就解决。

[本次实际目录包快照](/Volumes/ext/fin_agent/docs/development_tasks/evidence/catalog_contract_review_20260908/selected_execution_packs.json)。

## 五、金融特性下沉：哪些是真实实现，哪些不能夸大

| 能力 | 工具已经负责 | 模型只需知道 |
|---|---|---|
| 行情日线/分钟/实时分流 | 按 mode 分派到日 K、分钟 K、实时 API | 三种模式的业务范围、参数与结果粒度 |
| 日 K 窗口 | 内部取市场交易日历，按固定日期集合计算 | k 的窗口单位及结果的实际日期/覆盖 |
| 区间涨幅、区间振幅 | 专门 SQL 计算 | 方法名对应的业务指标与必要公式口径 |
| median | 排序后取中间项/中间两项均值 | 可用的方法和目标字段 |
| 融资变化/变化率 | change/pct_change 专用模板 | 变化和求和是不同方法 |
| 实时涨跌停 | 根据上游涨跌停价与现价生成状态 | is_limit_price 的状态含义 |
| 财务取数 | 默认报告期、报表类型、关联表与派生值在 Provider 处理 | 实际默认期、累计/单季等会改变解读的口径 |

证据：[行情分流](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/api_runner.py:159)、[特殊算法与通用模板分发](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/quote_provider.py:929)、[交易日窗口](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/quote_provider.py:1094)、[融资方法分发](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/margin_provider.py:432)、[实时涨跌停](/Volumes/ext/fin_agent/src/experiments/staged_data_protocol/phase2/realtime_quote_provider.py:100)。

三个限定需如实保留：

1. **融资余额 sum 当前仍是求和**，变化是 change/pct_change；不能把“金融特性下沉”理解成系统擅自改写用户选择的操作。
2. **各窗口日期来源尚不完全统一**：日 K 用市场交易日历；融资用源数据最近 K 个不同日期；同分钟比较用最近 K 个有分钟记录的日期。统一 window 抽象可以复用，但应如实声明窗口口径，不能先写成完全一致再要求模型接受。
3. 当前 kd_pct_sum 使用窗口内首末收盘价，K=1 时为零。它是实际实现，不等于已验证所有“近 K 日涨幅”问法都期望同一口径；本轮不借机改公式。

## 六、如何整理：固定格式、局部组装，先不变调用协议

### 6.1 每个方法按同一顺序展示

| 部分 | 内容 | 维护位置 |
|---|---|---|
| 定位与用途 | subject、dataview、方法类别、api_name、一句用途 | 视图与函数 |
| 调用 | 精确签名、公共 filter/order/输出表达式 | 共享方法契约，当前函数展开 |
| 参数 | 名称、类型、是否必填、默认值、适用模式、含义 | 当前方法的有效参数定义 |
| 数据 | 允许输入/输出的字段、含义、单位、记录粒度 | 视图字段及当前方法的结果字段 |
| 特殊口径 | 真正改变取数或数值含义的少量约定 | 本方法；通用算法由 Provider 实现 |
| 调用示例 | 一个覆盖核心调用形态的例子；有真实独立形态时再补 | 方法示例 |

主体/视图摘要负责发现能力，执行包负责怎么调用。内部模板可以继续复用；投影层将它们组装成上述连续契约，模型直接读本次方法即可。动态字段筛选应保留该操作合法条件与输出能力，不按猜测用户意图删字段。

参数与方法是程序确实要读取的执行契约，适合规范化；用途、解释、业务表达继续使用简洁自然语言。这里不建议把每句说明拆成新状态、必填字段或层层 JSON。

### 6.2 以 stock.quote 示范应有的说明形态

以下是**建议呈现形式**，不是已经上线的新 schema；保留现有入口及参数名称。

**用途：**按股票返回日 K、指定周期分钟 K 或最新行情快照。

**调用：**

```text
rN = stock.quote(codes, names, filter, order, limit, mode, period, count) -> fields
```

上面是签名，使用时按需传关键字参数。

**通用参数：**

| 参数 | 类型 | 含义 |
|---|---|---|
| codes / names | 字符串列表 | 股票范围 |
| filter | 条件表达式字符串 | 使用当前字段和统一 Python 风格筛选协议 |
| order | 排序表达式字符串 | 排序字段与方向 |
| limit | 正整数或 -1 | 总返回行数；-1 表示显式全量，仍受系统安全上限 |
| mode | 0 / 1 / 2，默认0 | 选择下表的数据形态 |
| period | 分钟整数 | 分钟 K 周期，取值见下表 |
| count | 正整数 | 每证券选取的最近 K 线根数，取值见下表 |

**数据模式：**

| mode | 数据 / 一行粒度 | period | count | 时间与量额 |
|---|---|---|---|---|
| 0 | 股票 × 日 K | — | 可选，1–5000 | 已完成历史日 K；日期范围内取数。默认截面查询选最新可用日，普通宽查询默认总 limit=100 |
| 1 | 股票 × 分钟 K | 1/3/5/10/15/30/60，默认1 | 1–1000，默认240 | 每根 K 线区间量额；is_finalized 表示该根是否完成 |
| 2 | 股票 × 最新快照 | — | — | snapshot_time 是行情时间；量额为当日累计 |

**输出定义：**字段按当前模式列出“名称 / 含义 / 单位”，区分价格、量额、时间、状态。比如 mode=1 的 close 是该根 K 收盘价，mode=2 的 close 是快照最新价。

样板还需对齐两项实际执行差异：日 K 在筛选后按证券取 count；分钟 K 先按证券/日期取最近 count 根，再应用其他指标过滤。另外，明确股票并按日期排序、limit>1 的日 K 请求存在时间序列路径。这些应归入参数执行顺序与时间选择契约，先确认统一期望再整理实现，不能简单用一句默认规则覆盖所有路径。

共同 filter 语法在共享位置给一次；tradedate 的实际日期含义在字段定义给出。源表、索引、SQL、重采样与差分实现进入开发说明，模型读取当前调用契约即可。

### 6.3 组装与验证顺序

1. 对齐五类方法总纲与四种 operation 的映射；复用已有分类，保持当前 API 名称。
2. 先整理 stock.quote 一个完整样板：参数表、模式表、字段作用域和默认行为，迁回散落定义；以实际代码为准。
3. 将样板投影方式应用到其他方法模板，特殊算法只保留准确的业务口径；代码已有差异时先确认差异，而非用提示词抹平。
4. 共用参数/字段定义尽量同时服务文档与现有静态检查，保留兼容输入；先验证请求可接受集合及结果语义未变。
5. 再清理重复常驻、阶段、note 和结果 guidance。按当前阶段装载所需信息，执行事实与业务策略各归其位。
6. 用历史相同 query 比较入口、API 串、静态失败/重试、数据等价性、token 和时间。文本变短只是结果之一，不替代效果回归。

## 七、本轮验证与边界

- 实际运行当前目录投影，保存四个代表执行包（含行情 window）。
- 目录投影测试：20 passed。
- 金融封装的所选离线/mock/SQL形状与数值测试：67 passed。验证了三模式路由、分钟周期、固定日历窗口、median、涨跌停及融资 sum 等局部行为。
- 未连接生产数据库、未调用模型、未修改运行提示/协议/Provider、未提交或部署。离线检查不代表模型准确率或线上数据完整性。

复现所选金融封装测试：

```sh
.venv/bin/python -m pytest -q \
  tests/test_stock_quote_realtime_modes.py \
  tests/test_realtime_quote_provider.py \
  tests/test_finance_recent_scan.py::test_calendar_window_filters_fixed_dates_instead_of_backfilling_per_security \
  tests/test_finance_recent_scan.py::test_explicit_daily_range_is_unchanged_by_probe_switch \
  tests/test_finance_python_filter.py::test_aggregate_median_uses_middle_rows_not_mean \
  tests/test_finance_python_filter.py::test_grouped_median_ranks_each_group_after_filter \
  tests/test_phase2_engine_loop_feedback.py::test_margin_allows_requested_method_for_any_metric_field
```

**结论：保留原来的五类方法与金融封装；应重整的是有效契约的组织和组装，而不是继续按一个错例补一条话，也不是重新造一套查询 API。**
