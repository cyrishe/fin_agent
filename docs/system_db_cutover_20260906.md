# 系统库迁移结果（2026-09-06）

## 结果

- 源：`47.112.132.214:3306/aiia_system`，保留不动。
- 目标：`47.94.1.2:3312/stock_agent`，复用原有 cubeyz 账号，不改密码。
- 21 张表、17,386 条记录复制完成。按主键顺序逐表比较回读 SHA256、行数；再次读取旧库新快照，核对摘要未变。建表结构亦已比较，忽略 MySQL 版本补显的等价字符集声明。
- 新增 `aiia_request_usage`、`aiia_scheduled_task`、`aiia_scheduled_task_run` 三张已有应用定义的表。
- 本地与服务器仅更换 `SYSTEM_DB_URL`，其他环境变量保持不变。两端环境文件已备份，权限 600。
- stock_agent 原有 BI、热点、leader 等业务表保持原样；金融行情和研报仍由 kingdomai 提供。

## 迁移中发现并解决的问题

1. `leader_risk_state` 不只是表名冲突，CHECK 约束名也冲突。归档表使用 `aiia_legacy_leader_risk_state`，约束增加 `aiia_legacy_` 前缀。迁移脚本预检已补 CHECK 约束名检查及测试。第一次失败仅创建 20 张空表；全部核实为空且结构一致后清理重建，没有删除记录。
2. MySQL 8.0.28 → 8.0.37 导入一条历史对话 JSON 时，同一个测试结果数值的三处副本出现浮点末位变化。校验拦截后，通过显式 DOUBLE 转换在新库恢复源值，整条 JSON 文本逐字一致，原 `updated_at` 保留。没有放宽摘要标准、四舍五入、改业务规则或改旧库。已复制的表重新逐表核验，只有尚未复制且为空的 5 张表继续导入。

## 验证

- 本轮相关测试：44 项通过；此前完整后端回归 1231 项通过、27 项跳过。本轮未重跑完整 Agent 模型链路。
- 用户 199、用户会话 371、线程 3602、对话请求记录 1578（切换时数量，后续运行会增加）。
- 工具存储服务从新库成功读取 81 个工具资产；用户凭据外键无孤儿记录。
- 请求用量表完成事务写入、回读、回滚测试，不向统计表留下虚构请求。
- 服务器 API `/health`、`/status`、`/v1/usage/daily?days=7`、`/v1/finance/catalog` 返回 200；用量接口未鉴权返回 401。
- 公网 `/fin_agent/v1/usage/daily?days=7` 返回 200；MCP `tools/list` 返回 200，并能发现 `finance_data_query`。目录请求不计 MCP 业务请求次数，本轮未额外运行模型查询。
- 用量采集表在 2026-09-06 建立，该日属于部分采集日；历史未采集 MCP/API 和模型缺失 usage 仍标未知，不当成零或完整总量。

## 服务状态与操作

服务器金融 API 已恢复，监听 `127.0.0.1:22054`，仍是原来的手工托管方式。
本地 Web 已恢复（22053）。本地独立 API 原进程使用临时访问 Key，根 `.env` 未持久化该 Key；当前未恢复，不影响服务器 API 或本地 Web。需要使用本地独立 API 时，先配置本地 `FINANCE_API_KEY` 或 `FINANCE_API_KEYS_JSON` 再启动，不自动换 Key。

服务器 Web 由 systemd 管理，迁移后尚待用户执行：

```bash
sudo systemctl start fin-agent-web.service
sudo systemctl status fin-agent-web.service --no-pager
```

完整启动会加载新系统库连接和服务器配置的 Astra low。不要重复启动占用 22054 的另一套 API。
公网状态页还需 nginx location 生效，步骤见 [服务器操作说明](../deploy/server/ai-agent-kingdomai/RELEASE_20260906.md)。

## 证据与回退

服务器目录：`/home/che/cyris/fin_agent_deploy/system-cutover-L4zcS5Tv/`。

- `migration.jsonl` / `migration-retry.jsonl`：前两次校验拦截日志。
- `migration-completed.jsonl`：恢复后 21 张表的行数、SHA256 及完成回执。
- `final-verification.jsonl`：切换前再次比对源库、目标结构与行数，最终通过。
- `env.before` / `env.before-switch`：服务器环境备份，含凭据，不提交 Git、不对外分享。
- 本地环境备份：`.codex_tmp/system-cutover-backup-OJ795P5B/env.before-switch`，同样不提交 Git。

旧库是保留副本，不是双写目标。新库开始接收请求后，不能直接切回旧库，否则会遗漏新记录；回退需要先停写并处理增量。
