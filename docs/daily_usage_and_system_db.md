# 每日请求用量与系统库迁移

## 统计口径

- 北京时间，以完整请求的完成日归属；跨午夜请求归入完成当天。
- 对话次数：`aiia_runtime_turn` 有 `finished_at` 的请求记录数，不是会话数，不是内部 Agent turn 数。
- MCP 次数：经过鉴权与参数解析并进入 `finance_data_query` 的执行次数；目录、初始化、被拒绝的鉴权和参数校验不计。
- HTTP 查询/问答请求单列；MCP 复用执行器时仍只归 MCP，不重复计入 HTTP。
- 每日输出系统总 Token、对话次数/MCP次数、各渠道 Token 总和及请求均值。
- Token 为模型返回的输入+输出用量；DSH 缓存读入包含在处理总量中，推理输出若已包含在 output 内不重复累加。不是费用或供应商账单。
- 内部调用不单独落账。对话复用系统请求记录；外部请求以 UUID 唯一键在 `aiia_request_usage` 保存完整请求汇总。
- 无用量记录为 NULL，不视作0。只要有未知项，精确系统总量为 NULL，另列已记录总量；均值用已记录 Token / 有用量记录的请求数。真实上报的0可参与均值。
- 历史对话可读取已有用量；旧归一化逻辑曾将缺失写为全0，历史全0按未知处理。MCP/HTTP历史未落账，不能回补为0。
- 新表创建日属于部分采集日，在页面注明；后续完整日期可正常汇总。请求内部未上报、异常中断丢失的 Token 无法凭空还原。后台独立任务不归入对话/MCP请求。

## 页面和接口

`/status` 下方“系统每日用量”表。输入已有 API Key 后读取，不将 Key 写入 localStorage。
`GET /v1/usage/daily?days=30`，支持1–90天，采用已有 API Key 鉴权。
统计不公开对话内容、用户ID、Key或数据库连接信息；缓存60秒，SQL限制3秒。

接口字段：每一天的 `chat`、`mcp`、`http_api` 各有 `requests`、`total_tokens`、
`average_tokens`、`token_requests`、`unknown_usage_requests`；
每日 `total_tokens` 是完整总量，`known_total_tokens` 是已记录总量。

外部请求账目异常会写服务错误日志，不阻断业务结果；需关注
`Request usage persistence failed`。不是强一致计费系统。

## 系统库

2026-09-06核查：本地和服务器均连接 `47.112.132.214:3306/aiia_system`，
并非旧kingdomai。用户随后明确要求迁至 `47.94.1.2:3312/aiia_system`，旧库保留不动。
源库21张InnoDB表，无触发器、存储过程或数据库事件。

迁移脚本：`scripts/migrate_system_database.py --apply`。
仅从旧库执行只读一致性快照查询；目标库必须不存在，避免覆盖。
保留原始建表语句、主键、自增值和记录，分批复制；每表按主键排序计算 SHA256 并回读核对行数及摘要。
脚本不负责自动停服或切换连接，必须先暂停本地/服务器所有系统库写入。
迁移日志只包含表名、数量、内容摘要，不含业务记录与凭据。

新库迁移成功后，使用 `src.services.request_usage_service.DDL` 建立请求统计表。
配置仅更换 `SYSTEM_DB_URL`，证券查询仍走 kingdomai，热点仍走 stock_agent。
连接信息通过环境配置，不提交密钥。切换前保存权限600的环境配置备份。
Web以systemd管理，旧环境会留在运行进程中，修改.env后必须真正重启，不能仅做gunicorn HUP。

回退时恢复旧配置并重启。**一旦新库接收新请求，回退前需处理新增数据，不能直接切回旧库而丢失新记录。**
