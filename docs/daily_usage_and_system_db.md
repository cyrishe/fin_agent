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
仅从旧库执行只读一致性快照查询；目标库必须不存在或完全为空，拒绝覆盖已有表。
保留原始建表语句、主键、自增值和记录，分批复制；每表按主键排序计算 SHA256 并回读核对行数及摘要。
脚本不负责自动停服或切换连接，必须先暂停本地/服务器所有系统库写入。
迁移日志只包含表名、数量、内容摘要，不含业务记录与凭据。

新库迁移成功后，脚本使用 `src.services.request_usage_service.DDL` 建立请求统计表，
并执行已有定时任务 SQL，补齐 `aiia_scheduled_task`、`aiia_scheduled_task_run`。
配置仅更换 `SYSTEM_DB_URL`，证券查询仍走 kingdomai，热点仍走 stock_agent。
连接信息通过环境配置，不提交密钥。切换前保存权限600的环境配置备份。
Web以systemd管理，旧环境会留在运行进程中，修改.env后必须真正重启，不能仅做gunicorn HUP。

回退时恢复旧配置并重启。**一旦新库接收新请求，回退前需处理新增数据，不能直接切回旧库而丢失新记录。**

### 独立系统库准备结果（2026-09-06）

当前 Fin Agent 的本地、服务器配置及服务器进程仍指向旧 `aiia_system`。
新 MySQL 使用 `cubeyz` 账号访问 `kingdomai` / `stock_agent`；系统库也复用此账号，
但库名必须单独使用 `aiia_system`。连接器现在拒绝把两个业务库作为系统库。

已实际尝试 `--prepare`，MySQL 返回 **1044 Access denied for user cubeyz@% to database aiia_system**。
因此新库尚未创建，数据未复制，`.env` 和运行连接未切换。先授权再停服，避免无谓停机。

隔离连接、空目标库保护、用量、工具存储、定时任务与环境示例相关测试共 40 项通过。

DBA 在 **47.94.1.2:3312** 执行：`docs/sql/create_aiia_system_database.sql`。
该 SQL 仅创建独立 schema，并给既有账号授予该 schema 的读写/建表/索引权限；不改密码，不授权其他库。

随后按顺序操作：

1. 在项目根目录运行 `.venv/bin/python scripts/migrate_system_database.py --prepare`，验证账号能访问空库。此步骤不切连接、不复制数据，可在运行中执行。
2. 暂停所有旧系统库写入端，包括服务器 Web/Financial API、本地 Web/API、可能的定时 worker 和测试进程。Web 使用 `sudo systemctl stop fin-agent-web.service`；其余按原启动方式正常停止。
3. 运行 `.venv/bin/python scripts/migrate_system_database.py --apply`，确认每表行数和 SHA256 回读一致。以当前源库为准，预计复制 21 张表，再补 3 张现有代码需要的新表。
4. 安全备份两端 `.env`，仅将 `SYSTEM_DB_URL` 改为新 MySQL 地址、同一 `cubeyz` 账号密码和 `/aiia_system`。密码须 URL 编码，不打印到终端、不提交 Git。
5. 在启动前验证 `SELECT DATABASE()` 为 `aiia_system`，核对用户、会话、工具资产、修订与调度表以及用量接口；完整重启所有写入端，不能用 HUP 代替环境重载。

迁移失败会保留目标现场，脚本拒绝覆盖非空库；不要自行删除旧库或直接反复执行复制。

### 不按 aiia_ 前缀混搬

- `kingdomai.aiia_stock_realtime_minute_snapshot*` 和 `aiia_trade_calendar` 是金融业务数据，留在 `kingdomai`。
- `stock_agent.aiia_simple_bi_*` 属于另一套 BI 系统，本仓库无引用；是否一起迁移需单独确认并检查该项目配置，不随 Fin Agent 自动搬动。
- 旧 `aiia_system` 中的历史表（包括 `leader_risk_state`）随源库保留复制；不与 `stock_agent` 的同名表合并或覆盖。
