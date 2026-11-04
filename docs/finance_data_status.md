# 金融数据状态页

入口 `/status`，公开快照接口 `/status/data`。部署在 `/finance/` 下时，访问
`https://ai-agent.kingdomai.com/finance/status`。页面不调用大模型。
页面读缓存，不能通过刷新触发数据库扫描；不返回连接信息、原始业务记录和数据库异常。

覆盖股票、基金、债券、板块、指数、行业、财务三表、公司披露、研报和热点共46个监控单元。
数据源尽量复用查询 provider 的注册表，新增来源应同步纳入监控。

## 时间与数据口径

- 北京时间。启动及跨日观察一次，15:30后每900秒重扫。
- 分钟K线单独每60秒检查，不受15:30限制；只统计源1m，避免重复计算派生周期。
  默认容忍300秒延迟；午休冻结在11:30、收盘冻结在15:00，开盘前不报缺失。
  数量与前五交易日同一时点累计量比较；实时扫描不会反复重扫其他45个数据源。
  每行 `checked_at` 保留实际检查时间，实时刷新不会掩盖日更快照过期。
- 默认 T-1：交易数据目标为前一交易日；研报目标为前一自然日。
  例如周一检查行情时看上周五，研报看周日。
- 当天到齐要求可通过逐数据集 lag=0 配置；每行明确展示目标日期。
- 基线固定为目标日期之前五个可比日期，缺数据的日期计0，不挑有数据的日期。
- 非交易日免“未更新”和低量告警，继续展示观测数据；表不存在、扫描失败、
  交易日历缺失仍提示。假日必须以 `aiia_trade_calendar/CN_A` 为准，不以周末猜测。
- 行情等每日源：目标日0条显示未更新；有数据表示已到达。
  少于五日均值80%时标注低量，供运营核对，不阻断查询 API。
- 三张表按公告日期统计，业务分部、基础信息和公司行动按变更日期统计。
  非每日必有记录的数据源，零新增不判同步失败。
- 研报指标按关联研报发布日期统计事实行数，不按预测年份统计。
- `arrived_at` 是目标日数据切片中的最后修改/入库时间。
  修改时间并非上游同步任务成功时间，也不是最早到达时间。

## 配置

```dotenv
FINANCE_STATUS_ENABLED=1
FINANCE_STATUS_CHECK_TIME=15:30
FINANCE_STATUS_INTERVAL_SECONDS=900
FINANCE_STATUS_REALTIME_INTERVAL_SECONDS=60
FINANCE_STATUS_REALTIME_TOLERANCE_SECONDS=300
FINANCE_STATUS_SNAPSHOT_PATH=data/finance_status/latest.json
# 当行情要求当日收盘数据到齐时：
# FINANCE_STATUS_LAGS_JSON='{"stock.quote":0,"fund.quote":0,"plate.quote":0}'
```

监控复用查询 provider 的数据库配置：证券数据来自 kingdomai，热点来自 stock_agent，
只执行只读统计；逐表串行，
SQL最长3秒，连接5秒超时、读取8秒超时。快照原子落盘，重启保留上次结果。
请使用单进程 Finance API 服务运行扫描，多实例部署只在一个实例启用扫描，
在网关只路由状态页到扫描实例。

当前只能确认库内数据存在、规模与时效，不能证明上游全量同步完成或指标语义正确。
完整的同步成功监控需要同步程序提供批次完成时间、预期行数和实际行数；
这些字段目前不虚构。此版本不修改数据库表或添加索引；超过10万行估算规模且日期
字段不是索引首列的表跳过全扫描，显示需要优化索引。其余慢查询会显示检查失败。

2026-09-06 再核查：旧 `47.113.122.220:3306/kingdomai` 和新
`47.94.1.2:3312/kingdomai` 均无 `aiia_trade_calendar`。旧服务器可见的
`wind.asharecalendar` 也没有记录；现有 platform/system 连接中无 calendar 表，
report 连接未成功，因此没有可直接搬迁的源表。

随后经用户授权，已在新库创建 `aiia_trade_calendar`，按上交所2026年度公告
写入365个自然日（242个交易日），市场标识为现有代码使用的 `CN_A`。
字段为 `market_code`、`calendar_date`、`is_trade_day`，附 `source_url`、`created_at`；
主键 `(market_code,calendar_date)`，交易日查找索引
`(market_code,is_trade_day,calendar_date)`。监控及现有查询无需改接口。

官方来源：[年度休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/)、
[上证公告〔2025〕45号](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)。
周末与公告中的节假日区间均为休市，行政调休上班的周末也不交易。
初始化脚本 `scripts/init_trade_calendar_2026.py` 默认只预览，`--apply` 才写入；
只允许新库目标、只补缺失日期，遇到已有日期冲突会回滚而不是覆盖。
写入后逐日回读校验。MySQL建表隐式提交，数据插入独立事务。

**覆盖边界：仅2026-01-01至2026-12-31。** 不据此声称历史回测日历齐全；
2025及更早的回测/跨年窗口仍需补入相应年度官方日历。2027年也尚未填入。
后续年度应核对官方安排后补充，不能仅按周一至周五生成未来交易日。

执行 `.venv/bin/python scripts/finance_status_preflight.py` 可输出当前缺失的表、字段
及索引建议 SQL。此脚本仅检查并打印，不执行 DDL；由数据库运维选择维护窗口应用。
