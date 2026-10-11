# Skill Web Search 发布记录（2026-09-14）

## 已完成

- 功能提交 `e7bb931`，分支 `agent/financial-tool-design-protocol`，已推送 GitHub、Codeup。
- 服务器 `/home/che/cyris/fin_agent` 从 `372f1c0` 快进同步；未提交的其他工具开发改动没有发布。
- 本地 Python 定向测试 156 passed，Node DSH 流程测试 68 passed。
- 服务器 Python 3.10 定向测试 155 passed、1 failed；Node 20.20.2 测试 68 passed；TypeScript 与 Vite production build 通过，保留大 chunk 告警。
- 服务器直接读取三环集团新浪资讯目录得到 5 条候选，读取首条正文得到 1354 字符 Markdown。该验证不调用 Brave、不调用模型，不代表线上 Agent 全链路或业务答案质量通过。

## 待处理事项

- `sudo -n systemctl restart fin-agent-web.service` 返回需要密码；Web PID 仍为 `1005`，启动时间为 `2026-09-13 11:21:43 CST`。**源码和构建已同步，新版未完成重启激活**。公网 Studio HTTP 200 仅证明服务可用。
- 服务器没有配置 Brave key，provider 为默认 Elasticsearch；股票目录与正文读取不依赖 key，Brave 全网搜索尚不可用。本轮未修改 `.env`、nginx 或数据库。
- 唯一失败测试为 `test_crawler_falls_back_to_managed_driver_when_path_driver_is_broken`：服务器 Selenium 4.10.0 缺少 `SeleniumManager.binary_paths`。已确认此调用与测试在基线 `372f1c0` 就存在；本地 Selenium 4.47.0 可通过。浏览器爬虫驱动回退路径待修复，不能宣称全部回归通过。本轮没有升级生产依赖。

## 构建与恢复资产

目录 `/home/che/cyris/fin_agent_deploy/web-search-20260914-e7bb931/`：

- `source-before.tar`：更新前受 Git 管理的源码，不包含环境密钥和运行数据。
- `frontend-before/`：原前端完整备份。
- `frontend-next/`：当前提交构建，采用 `/fin_agent/` 前缀；尚未替换正式入口。
- `node-tests.log`：服务器 Node 测试记录。

管理员确认上述限制后，可在服务器交互执行（sudo 密码不写入命令或仓库）：

```bash
bash /home/che/cyris/fin_agent/deploy/server/ai-agent-kingdomai/activate-web-search-20260914.sh
```

脚本校验构建对应源码、预置哈希资源、重启 Web、验证路由，再原子切换静态入口并比对公网 HTML。需要单独补充线上 Agent 冒烟；本记录不构成全量测试或生产质量门通过。静态入口可从 `frontend-before/index.html` 恢复；源码回退按正常 Git 回退及管理员重启流程执行。
