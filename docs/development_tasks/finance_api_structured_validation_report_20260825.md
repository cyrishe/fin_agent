# 金融 API 参数 JSON 化与静态检查测试报告（2026-08-25）

## 1. 结论与数字口径

本轮已按以下边界完成实现，并在第二轮复核中继续收紧为“Provider 真正消费的机械契约”：

```text
用户问题
  → LLM 继续生成原有 API string
  → 服务端将参数编译为内部 JSON
  → 对照 API Catalog 与 dataview 做机械静态检查
  → 静态通过后才调用 Provider
```

模型侧工具定义、API string 语法、提示词、Catalog 描述和 dataview 描述均未修改。静态层不检查请求是否符合用户自然语言语义，也不判断金融指标是否适合某种计算；`median` 的公式和“最新”应选哪个时间条件也没有在这里做补丁。

先澄清三个数字，它们不是同一个集合：

| 数字 | 含义 | 能否衡量本功能 |
|---:|---|---|
| 27 | 第一轮人工标注并真实执行的金融 API Case：25条历史调用 + 2条普通业务补充 | 可以，但负例只有3条、补充题只有2条，证据不足 |
| 60 | 本轮新增的普通金融业务 Case：40条结构合法 + 20条正常问题下的常见结构错误 | 可以，是扩展后的针对性评测 |
| 1,087 | 最新全仓 pytest 通过数，覆盖整个项目 | 不可以；它只是回归信号，不是金融静态检查评测集 |

因此，当前人工标注针对性集合是 **87条（27+60）**，不是1,037条。另有一个独立的历史产物扫描集：从2,802个JSON文件抽取到646条去重 API string，它没有逐题业务语义标签，只用于观察现存调用会被静态层发现多少结构问题。

人工标注集合汇总：

| 指标 | 结果 |
|---|---:|
| Case 总数 | 87 |
| 第一轮历史/普通业务 Case | 27（24合法、3非法） |
| 本轮新增普通业务 Case | 60（40合法、20非法） |
| 预期结构合法 | 64 |
| 预期结构错误 | 23 |
| 结构错误检出率 | 23/23，100% |
| 合法请求误拒绝率 | 0/64，0% |
| 静态判断与人工结构标签一致率 | 87/87，100% |
| 新增40条合法调用的最新 Provider 成功率 | 36/40，90%（每题60秒） |
| 新增40条合法调用中的明确评测超时 | 1/40 |
| 本轮新增20条非法调用 | 20条均在 Provider 前拦截 |

这次不能再说“静态检查都没问题”。准确结论是：

- 静态层对这87条人工标注的**机械结构标签**判断一致，确实拦截了23条，否则这些请求会继续进入 Provider；
- 15秒初跑只有30/40成功；把评测超时提高到60秒后，6条原失败调用恢复成功，最新结果为36/40；
- 剩余4条分别是：1条全市场聚合60秒超时、1条动态计算 Output Schema 不一致、1条研报数据库断连、1条新闻ES 502。它们没有被误写成静态业务规则；
- 人工扩展集是按当前协议定向设计的，100%不是生产分布准确率。646条历史API string复扫识别出的30条真实结构错配，是更接近自然分布的补充证据；
- 返回0行仍属于原条件成功执行，不能据此宣称业务答案正确或完整。

## 2. 实现范围

### 2.1 后置 JSON 化

新增统一编译器，将原 API string 中的参数转换为 JSON 安全结构。示例：

```text
r1 = stock.moneyflow(
  filter = "large_net > 0 and main_ratio > 5",
  order = "main_ratio desc",
  limit = 50,
  realtime = 1
) -> code, name, tradedate, large_net, main_ratio, main_net
```

内部转换为：

```json
{
  "api": "stock.moneyflow",
  "api_class": "basic_query",
  "subject": "stock",
  "dataview": "moneyflow",
  "arguments": {
    "limit": 50,
    "realtime": 1
  },
  "filter": {
    "and": [
      {"field": "large_net", "operator": ">", "value": 0},
      {"field": "main_ratio", "operator": ">", "value": 5}
    ]
  },
  "order_by": [
    {"field": "main_ratio", "direction": "desc"}
  ]
}
```

原字符串和原参数仍原样传给现有 Provider，JSON 结构当前用于检查与可观察证据，没有替换模型输出接口。

### 2.2 静态检查边界

当前检查：

- API 是否存在；
- API 参数是否属于目录声明或该 Provider 实际消费的兼容契约；
- 必填参数是否存在；
- filter 是否能完整解析；
- filter 运算符是否为该执行路径真实支持的运算符；当前会拒绝所有 Provider 均不支持的 `not in`，并按执行路径判断 `like`；
- filter 字段是否属于当前 dataview，或 K 日/实时模式实际生成的结果行；
- order 和 output 是否属于该 Provider 实际排序、投影的字段集合；
- 聚合结果是否包含全部 group_by 字段且只有一个聚合结果列；
- 普通查询是否错误输出函数表达式或不受支持的别名；
- `rN.column` 是否存在，依赖筛选是否使用 `field in rN.column`。
- filter 中是否混入 Provider 会当普通字符串处理的 SQL 子查询。

明确不检查：

- API string 是否回答了用户原始自然语言问题；
- 用户是否真的想筛选、排序或聚合该字段；
- 余额是否适合求和；
- “最新”应该转换成何种时间条件；
- median 等金融计算公式应该如何实现；
- 查询结果是否符合金融常识。

### 2.3 兼容保护

为避免静态检查降低既有成功率，继续接受运行时已有明确测试覆盖、且由对应 Provider 真正消费的兼容参数，如 `realtime`、`date`、`as_of` 和旧聚合形式中的 `metric`。这些参数不再作为全 API 全局白名单。板块行情中已有的 `plate_code → code`、`plate_name → name` 筛选别名只做确定性内部规范化。

历史调用使用的 `filter="1=1"`、`1 = 1`、`all` 等仍作为明确的全量查询写法接受；本轮同时让 basic_info Provider 把这些写法编译为真实 `1=1`，避免出现“静态通过、Provider 却报 invalid_filter”的分裂。

没有为自然语言近义词或业务意图增加 Provider 分支。

## 3. 第一轮27条逐 Case 结果

| Case | 场景 | 来源 | 静态检查 | 执行 | 行数 | 总耗时 |
|---|---|---|---|---|---:|---:|
| BUS001_CALL1 | 贵州茅台基础信息 | 历史100题 | 通过 | ok | 1 | 350ms |
| BUS003_CALL1 | 银行业股票筛选 | 历史100题 | 通过 | ok | 15 | 412ms |
| BUS007_CALL1 | 贵州茅台实时行情 | 历史100题 | 通过 | ok | 1 | 396ms |
| BUS007_CALL2_DATE | 指定日期的贵州茅台日行情 | 历史100题 | 通过 | ok | 0 | 630ms |
| BUS010_CALL1 | 宁德时代近10日行情 | 历史100题 | 通过 | ok | 10 | 385ms |
| BUS011_CALL1 | 全市场20日平均振幅排行 | 历史100题 | 通过 | ok | 20 | 862ms |
| BUS014_CALL1 | 贵州茅台资金流 | 历史100题 | 通过 | ok | 1 | 295ms |
| BUS015_CALL1 | 5日主力净流入合计排行 | 历史100题 | 通过 | ok | 10 | 686ms |
| BUS075_CALL1 | 沪深300基础信息 | 历史100题 | 通过 | ok | 1 | 377ms |
| BUS085_CALL1 | 20日跌幅最大指数 | 历史100题 | 通过 | ok | 10 | 2,138ms |
| BUS113_CALL1 | 半导体行业成分股 | 历史100题 | 通过 | ok | 74 | 335ms |
| BUS117_CALL1 | 三级行业成分股数量排行 | 历史100题 | 通过 | ok | 20 | 1,259ms |
| BUS116_BAD_REF | 用 `=` 绑定上一结果列 | 历史100题真实失败调用 | **失败** | 未执行 | — | 1ms |
| BUS116_FIXED_REF | 用 `in` 绑定上一结果列 | 历史100题真实修正调用 | 通过 | ok | 15 | 314ms |
| BUS127_CALL1 | 板块代码与名称 | 历史100题 | 通过 | ok | 500 | 406ms |
| BUS134_CALL1 | 实时板块涨幅排行 | 历史100题 | 通过 | ok | 20 | 354ms |
| BUS137_BAD_OUTPUT | K日板块指标输出 `plate_code/plate_name` | 历史100题真实失败调用 | **失败** | 未执行 | — | 1ms |
| BUS137_FIXED_OUTPUT | K日板块指标输出 `code/name` | 历史100题真实修正调用 | 通过 | ok | 0 | 308ms |
| BUS165_CALL1 | 沪深300ETF基础信息 | 历史100题 | 通过 | ok | 10 | 377ms |
| BUS186_CALL1 | 名称包含转债的债券 | 历史100题 | 通过 | ok | 20 | 432ms |
| BUS192_CALL1 | 实时债券涨幅排行 | 历史100题 | 通过 | ok | 20 | 401ms |
| REAL_ROBOT_MEMBER_QUOTE_BAD_FIELD | 用 `stock_code` 筛选 stock.quote | 历史真实 Agent 调用 | **失败** | 未执行 | — | 1ms |
| REAL_PRICEVALUE_COMPARE | 茅台与五粮液估值对比 | 历史完整运行 | 通过 | ok | 2 | 338ms |
| REAL_MARGIN_COMPARE | 宁德时代与比亚迪两融对比 | 历史完整运行 | 通过 | ok | 2 | 314ms |
| REAL_FINANCIAL_HISTORY | 茅台2024年以来财务数据 | 普通业务扩展 | 通过 | ok | 8 | 587ms |
| REAL_CORPORATE_ACTION | 宁德时代最近增发事件 | 历史完整运行 | 通过 | ok | 1 | 2,984ms |
| REAL_SHAREHOLDER | 茅台最近前十大股东 | 普通业务扩展 | 通过 | ok | 0 | 152,725ms |

每条 Case 的完整用户问题、原 API string、预期静态结果和依赖结果样本保存在 `tests/fixtures/finance_api_static_validation_cases_20260825.json`。执行器会生成包含完整 JSON 化参数、错误明细、Provider 状态、行数和耗时的结果文件。

## 4. 本轮新增60条逐 Case 结果

这60条不是异常词法游戏，而是基础资料、行情、分钟K、K日指标、聚合、动态计算、资金流、估值、两融、财务、研报、新闻、公司行动、业绩预告、主营分部、质押、行业、板块、指数、基金、债券和热点事件等正常金融问题。20条负例也都对应正常问题，只是在 API string 中放入了常见的字段/视图/引用错配。

| Case | 用户问题 | 静态检查 | Provider执行 | 行数 | 耗时 |
|---|---|---|---|---:|---:|
| EXP_VALID_001 | 查询宁德时代的股票代码、所属行业和上市日期 | 通过 | ok | 1 | 1,109ms |
| EXP_VALID_002 | 列出2020年以后上市的股票 | 通过 | ok | 20 | 511ms |
| EXP_VALID_003 | 比较茅台和五粮液今天最新行情 | 通过 | ok | 2 | 856ms |
| EXP_VALID_004 | 茅台最近30根5分钟K线 | 通过 | **超时/provider_error** | 0 | 15,003ms |
| EXP_VALID_005 | 宁德时代最近20个交易日日K | 通过 | **超时/provider_error** | 0 | 15,004ms |
| EXP_VALID_006 | 茅台近20日平均成交额 | 通过 | ok | 1 | 717ms |
| EXP_VALID_007 | 近10日最高收盘价排行 | 通过 | ok | 10 | 4,643ms |
| EXP_VALID_008 | 上涨股票平均涨幅 | 通过 | **超时/provider_exception** | — | 15,004ms |
| EXP_VALID_009 | 茅台与五粮液区间波动分析 | 通过 | **schema_validation_error** | 0 | 12,404ms |
| EXP_VALID_010 | 比亚迪最近交易日资金流 | 通过 | ok | 1 | 484ms |
| EXP_VALID_011 | 主力和大单同时净流入筛选 | 通过 | ok | 20 | 817ms |
| EXP_VALID_012 | 近10日主力净流入合计排行 | 通过 | ok | 20 | 4,690ms |
| EXP_VALID_013 | 茅台最新PE/PB/总市值 | 通过 | ok | 1 | 341ms |
| EXP_VALID_014 | 总市值前20只股票 | 通过 | ok | 20 | 374ms |
| EXP_VALID_015 | 茅台三年PE百分位 | 通过 | ok | 1 | 1,387ms |
| EXP_VALID_016 | 宁德时代最新两融数据 | 通过 | ok | 1 | 316ms |
| EXP_VALID_017 | 近5日融资净买入排行 | 通过 | ok | 10 | 4,029ms |
| EXP_VALID_018 | 茅台2025年以来财务数据 | 通过 | ok | 6 | 934ms |
| EXP_VALID_019 | 茅台2026年以来研报 | 通过 | **report DB连接失败** | 0 | 3,058ms |
| EXP_VALID_020 | 宁德时代2026年8月以来新闻 | 通过 | **ES 502** | 0 | 2,738ms |
| EXP_VALID_021 | 茅台最近一次分红 | 通过 | **超时/provider_error** | 0 | 15,008ms |
| EXP_VALID_022 | 宁德时代最近一期业绩预告 | 通过 | ok | 1 | 8,482ms |
| EXP_VALID_023 | 茅台主营业务分部 | 通过 | **超时/provider_error** | 0 | 15,005ms |
| EXP_VALID_024 | 宁德时代股权质押 | 通过 | **超时/provider_error** | 0 | 15,007ms |
| EXP_VALID_025 | 申万一级行业 | 通过 | ok | 31 | 426ms |
| EXP_VALID_026 | 银行行业全部成分股 | 通过 | ok | 40 | 503ms |
| EXP_VALID_027 | 各一级行业上涨成分股数量 | 通过 | ok | 0 | 356ms |
| EXP_VALID_028 | 名称包含人工智能的板块 | 通过 | ok | 1 | 391ms |
| EXP_VALID_029 | 今日板块涨幅前10 | 通过 | ok | 10 | 396ms |
| EXP_VALID_030 | 今日板块主力净流入前10 | 通过 | ok | 10 | 514ms |
| EXP_VALID_031 | 机器人板块全部成分股 | 通过 | ok | 0 | 799ms |
| EXP_VALID_032 | 沪深300最新行情 | 通过 | ok | 1 | 369ms |
| EXP_VALID_033 | 沪深300权重最高成分股 | 通过 | ok | 20 | 302ms |
| EXP_VALID_034 | 沪深300ETF最新行情 | 通过 | ok | 1 | 326ms |
| EXP_VALID_035 | 名称含转债的债券 | 通过 | ok | 20 | 1,394ms |
| EXP_VALID_036 | 今日成交额最高债券 | 通过 | ok | 20 | 1,997ms |
| EXP_VALID_037 | 当前活跃热点事件 | 通过 | ok | 20 | 429ms |
| EXP_VALID_038 | 热点事件最新状态 | 通过 | ok | 20 | 2,132ms |
| EXP_VALID_039 | 机器人热点相关股票 | 通过 | ok | 100 | 10,343ms |
| EXP_VALID_040 | 沪深300成分股平均涨跌幅 | 通过 | **provider_exception** | — | 4,504ms |
| EXP_INVALID_001 | 板块成分股今日涨跌幅 | **拦截：stock_code不属于stock.quote** | 未执行 | — | 2ms |
| EXP_INVALID_002 | 高市值股票最新涨跌幅 | **拦截：market_value不属于stock.quote** | 未执行 | — | 1ms |
| EXP_INVALID_003 | 按总市值排序最新行情 | **拦截：排序字段不属于stock.quote** | 未执行 | — | 1ms |
| EXP_INVALID_004 | 茅台行情与总市值 | **拦截：输出字段不属于stock.quote** | 未执行 | — | 1ms |
| EXP_INVALID_005 | 融资余额与主力净流入筛选 | **拦截：融资字段不属于moneyflow** | 未执行 | — | 1ms |
| EXP_INVALID_006 | 主力净流入股票的融资余额 | **拦截：资金流字段不属于margin** | 未执行 | — | 1ms |
| EXP_INVALID_007 | 上涨且低PE股票 | **拦截：行情字段不属于pricevalue** | 未执行 | — | 2ms |
| EXP_INVALID_008 | 年报口径财务数据 | **拦截：report_type不存在** | 未执行 | — | 1ms |
| EXP_INVALID_009 | 合同负债与收入 | **拦截：输出字段不存在** | 未执行 | — | 1ms |
| EXP_INVALID_010 | 银行业股票基础资料 | **拦截：industry_name应为industry** | 未执行 | — | 1ms |
| EXP_INVALID_011 | 板块涨幅排行 | **拦截：plate.quote输出身份字段错误** | 未执行 | — | 1ms |
| EXP_INVALID_012 | 上一步行业的成分股 | **拦截：结果引用错误使用等号** | 未执行 | — | 1ms |
| EXP_INVALID_013 | 上一步板块的成分股 | **拦截：引用列不存在** | 未执行 | — | 1ms |
| EXP_INVALID_014 | 各行业平均PE | **拦截：PE放在quote视图** | 未执行 | — | 1ms |
| EXP_INVALID_015 | 各板块平均涨幅 | **拦截：group_by字段不存在** | 未执行 | — | 1ms |
| EXP_INVALID_016 | 按rank排近20日涨幅 | **拦截：order字段不存在** | 未执行 | — | 1ms |
| EXP_INVALID_017 | 茅台最近10日行情 | **拦截：filters参数未声明** | 未执行 | — | 1ms |
| EXP_INVALID_018 | 主力和大单净流入筛选 | **拦截：filter缺少右操作数** | 未执行 | — | 1ms |
| EXP_INVALID_019 | 茅台最新估值 | **拦截：API未注册** | 未执行 | — | <1ms |
| EXP_INVALID_020 | 近20日平均总市值 | **拦截：K日字段/方法未注册** | 未执行 | — | 1ms |

完整题目、API string与人工标签在 `tests/fixtures/finance_api_static_validation_expanded_cases_20260825.json`；逐条结构化结果、完整错误和Provider返回在 `tmp/finance_api_static_validation_expanded_results_20260825.json`。

### 4.1 15秒初跑与60秒复跑的差异

上表保留的是初次15秒执行证据，当时40条静态合法调用中有10条未成功。使用修正后的评测器和每题60秒阈值复跑后：

| 最新执行事实 | 数量 | Case |
|---|---:|---|
| Provider成功 | 36 | 包括原失败的004、005、021、023、024、040 |
| 评测器明确超时 | 1 | 008，全市场行情聚合，60秒 |
| 动态执行契约失败 | 1 | 009，模型生成了Output Schema未声明的列 |
| 外部数据源失败 | 2 | 019研报数据库断连；020新闻ES 502 |

这说明初次“10条失败”里有6条只是15秒阈值或瞬时故障，不能反推为静态漏检。评测器现已分别记录 `execution_attempted`、`execution_ok` 和 `execution_timed_out`，并删除会把 `--no-execute` 静态通过误算成综合正确的 `guarded_correct`。

36/40只表示Provider状态成功，不等于金融答案正确。复核中，023主营分部和024股权质押虽然返回`ok`，部分核心业务列仍全空，属于多数据源路由/数据质量问题，不适合由静态层根据非空性拒绝。

仍应保持三层分工：静态层只挡确定性结构错；数据源健康、动态Schema和慢查询由执行层报告；是否重试、改写查询或请用户确认由Agent loop处理。

60秒逐Case结果保存在 `tmp/finance_api_static_validation_expanded_results_20260825_after.json`。

## 5. 第一轮三个真实静态失败

### 5.1 BUS116_BAD_REF

```text
industry_name = r1.industry
```

上一结果列必须作为结果集合绑定：

```text
industry_name in r1.industry
```

检查失败后没有调用数据库；历史修正请求通过并返回15行。

### 5.2 BUS137_BAD_OUTPUT

`plate.quote.kd_pct_sum` 的实际输出身份字段是 `code/name`，原调用输出了 `plate_code/plate_name`。静态检查在 Provider 前指出两个输出字段不属于该 API；历史修正请求使用 `code/name` 后执行成功。

### 5.3 REAL_ROBOT_MEMBER_QUOTE_BAD_FIELD

历史 Agent 请求：

```text
stock.quote(filter = "stock_code in r4.stock_code and tradedate = -1", ...)
```

`stock.quote` 的当前 dataview 字段是 `code`，不是 `stock_code`。旧 Provider 可能忽略无法识别的筛选字段；新检查在执行前阻止该请求，避免扩大股票集合。它只报告字段不属于 dataview，不猜测用户或模型本来想表达什么。

## 6. 从真实 Query 发现并修复的词法问题

此前多个历史 Case 使用：

```text
tradedate = 2026-08-04
```

旧日期解析正则会优先把该值识别成整数 `2026`，随后发生日期解析异常。本轮仅调整确定性的词法匹配顺序：完整 ISO 日期先于整数匹配。原历史 Query 现已静态通过并由 Provider 成功执行；没有增加日期业务判断，也没有要求模型改变写法。

第二轮又复现了 `code == 600519.SH` 的确定性词法问题：多个 Provider 的正则把较短的 `=` 放在 `==` 前面，导致值被解析成 `= 600519.SH`。现已统一改为最长运算符优先。真实执行回归返回贵州茅台1行，代码为 `600519.SH`。

另外，Catalog 中已有但此前被静态层误拒绝的两类写法已恢复：财务派生字段（如 `revenue_yoy`）以及新闻检索字段 `query/keyword`。Catalog 中123条调用示例现全部通过同一静态验证器。

### 6.1 第二轮按实际执行路径收紧的契约

第二轮没有把整个 dataview 字段并集机械套给所有 API，而是依据确定的 Provider 结果形状做了以下区分：

- `stock.quote` 的日K、分钟K、最新快照使用各自真实字段集合；例如 mode=2 不再允许实际不会返回的 `turn_ratio`；
- quote、moneyflow、margin、pricevalue 的 K 日 API 按各自结果行校验 filter/order/output，不再允许 Provider 会静默忽略的原始行情字段；
- 同分钟 K 日结果允许分钟字段以及 `value/current_value/change_pct/window_count`，但 `k/end_date` 只在真实可排序/输出时接受，不在不会生效的 filter 中接受；
- 聚合输出必须带齐 group_by 字段并且恰好有一个聚合结果，避免额外别名被Provider静默丢弃；
- API别名先规范化后再匹配 Catalog，避免 `basic_info/base_info` 别名绕过参数检查。

这些都是“该字段/运算符是否会被当前执行路径消费”的结构事实，不比较用户问题与API string的语义是否一致。

## 7. 历史产物全量静态扫描

扫描范围：

- `outputs/019fa65e-ee19-7b23-8f71-36050299b7d5`
- `outputs/financial_qa_cc`

扫描器读取2,802个有效JSON文件，抽取并去重得到646条模型生成的 API string。由于没有恢复每次历史会话的真实结果行，扫描时只为 `rN.column` 合成列存在性，不做业务语义判断，也不重跑Provider。

| 扫描指标 | 结果 |
|---|---:|
| JSON文件 | 2,802 |
| 去重API string | 646 |
| 静态通过 | 608 |
| 静态标记 | 38 |
| 人工复核为保存文本截断 | 8 |
| 人工复核为真实结构错配 | 30 |

最初扫描还把8条 `filter="1=1"` 当成解析失败。历史证据表明这是已有的全量查询写法，因此静态解析器将 `1=1`、`1 = 1` 与 `all` 一样作为兼容的空过滤条件；第二轮又同步修正 basic_info Provider，使静态和执行端都接受同一明确的 no-op。

38条标记的人工复核分类：

| 类别 | 数量 | 代表问题 |
|---|---:|---|
| 保存文本截断，不算模型结构错 | 8 | API string在 `total_l`、`total_equ`、`accounts_receiv` 等字段中间被历史产物截断 |
| 行情字段错配 | 3 | `pre_close/changePct/pe_ttm`、`tradetime`、`volume` 被写入当前 `stock.quote` 输出 |
| 财务字段错配 | 2 | `cashflow_operating_yoy`不在派生字段白名单；`report_type/contract_liabilities`不在当前财务视图 |
| 研报字段错配 | 2 | `report_period/summary`、`report_title/org_name` 不在当前研报视图 |
| 资金流字段错配 | 2 | `main_net_pct/retail_net`、`main_net_inflow`等字段未定义 |
| 两融字段错配 | 1 | `end_date/margin_buy/margin_netbuy`等不是当前字段 |
| 基础资料字段错配 | 2 | `listing_date/list_date/exchange`不在当前基础资料视图 |
| 业绩预告字段错配 | 1 | `end_date/notice_type/parent_net_profit`等放错视图 |
| 板块字段错配 | 1 | `plate.basic_info`使用`code/name/type/description`而非`plate_code/plate_name` |
| 跨结果/行情字段错配 | 1 | 用`stock_code in r4.stock_code`筛选`stock.quote` |
| 聚合排序字段错配 | 1 | 输出是`stock_count`却按不存在的`count`排序 |
| Provider不支持的LIKE | 8 | 日行情、财务、热点、行业基础资料会静默忽略或错误处理LIKE |
| 实时模式输出字段错配 | 4 | mode=2请求`turn_ratio`，分钟/快照Provider实际不返回该字段 |
| K日结果字段错配 | 1 | `plate.quote.kd_pct_sum`用原始`pct`过滤，Provider只消费K日结果字段 |
| SQL子查询伪装成IN值 | 1 | `code in (select ...)`被Provider当成普通字符串，而不是子查询 |

因此，历史自然分布上的直接结论不是“静态检查不存在”，而是：646条中有30条确定性结构错配可在Provider前暴露，约占4.64%；同时全量回放帮助发现并消除了8条 `1=1` 兼容性误拦。38条完整原文、来源文件和错误详情在 `tmp/finance_api_history_static_scan_20260825_after.json`。

## 8. 测试与限制

专项回归：

- 新 JSON 化、Provider字段契约、运算符、`1=1`、日期词法和评测器专项：73项通过；
- 相关金融运行时、结果引用、实时模式、Provider和 Phase 2 回归：194项通过、2项因专用实时凭据缺失跳过；
- Catalog中的123条API调用示例：全部静态通过；
- 第一轮27条静态复跑：24条通过、3条按标签拦截；
- 新增60条静态复跑：40条通过、20条按标签拦截；60秒真实执行中36/40成功、1条超时、3条非超时失败；
- 历史全量静态复扫：646条去重字符串，608条通过、38条标记；人工复核后30条为真实结构错配、8条为历史保存文本截断；
- `filter="1=1"` 和 `code == 600519.SH` 两条真实Provider回归均成功，后者返回贵州茅台1行。

最新全仓回归（仅作兼容性背景，不计入本功能准确率）：

- 1,087项通过；
- 27项跳过；
- 1项失败：`test_tool_plan_runtime_executes_code_step_and_downstream_binding`，属于未改动的通用 Code Runtime 节点状态测试，单独重跑仍失败，与金融 API 静态检查改动无调用关系。

仍未验证：

- LLM 自然语言到 API string 的语义正确率，因为模型侧未修改；
- 真实业务答案的人工金融正确率；
- 646条历史 Query 的全量Provider重跑；当前只完成全量静态扫描，并重新真实执行了扩展集中的40条合法调用；
- 返回0行是否符合用户预期；
- Provider 内部公式和金融口径是否正确。

其中 `REAL_SHAREHOLDER` 虽执行成功但耗时约153秒，是独立的 Provider/查询性能观察项，不属于静态检查错误。

还有两个明确的后续边界：

1. 当前JSON AST已经成为统一静态语法事实，但多数Provider仍重新用各自正则解析raw filter/order。长期应让Provider消费同一AST或共享compiler，否则括号布尔优先级等复杂表达仍可能在两套解析器之间漂移；本轮没有用更多关键词白名单掩盖这个架构问题。
2. Phase 2 Loop当前只根据静态验证决定是否继续，没有把`schema_validation_error/provider_error`等非ok执行结果自动回注给Agent重写调用。这属于第三层Agent loop优化，不应下沉成静态语义规则。
