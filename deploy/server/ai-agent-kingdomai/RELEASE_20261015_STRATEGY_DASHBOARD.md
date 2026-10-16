# 策略执行看板发布候选（2026-10-15）

## 范围

公网目标入口：`https://ai-agent.kingdomai.com/fin_agent/strategies`。看板显示最近 20 个 A 股交易日的策略选股、买卖价格、回填涨跌和未完成记录；顶部实时区只显示当天实际选出的股票，按 10 秒间隔刷新分钟 K 和行情。筛选支持策略；管理员还能按用户切换。策略执行数据来自 `kingdomai`，本功能只使用只读事务，不创建表、不写选股、不计算或回填卖价，也不修改定时任务。

后端文件：`src/services/strategy_execution_view_service.py`、`src/web/strategy_execution_routes.py`、`src/web/templates/strategy_executions.html`，在 `src/web/flask_app.py` 注册。前端工作台入口见 `frontend/src/components/Sidebar.tsx` 和 `frontend/src/styles.css`。服务器 Sidebar 与本地版本的“任务中心/定时任务”文字不同，发布时须在服务器基线合并这一个导航链接，不能直接覆盖整个 Sidebar 文件。

## 数据口径

优先读 `automl_strategy_execution` 和 `automl_strategy_run`；没有执行记录时读已有 `automl_strategy_selection`。同一用户、策略、信号日、名次只显示一次。旧选股表没有买卖回填，因此只显示信号价和“待回填”，不将信号价冒充买入成交价，不自行查行情填收益。无选股的已运行日仅在 run 表存在时标注为已运行无选股。`actionable=0` 显示轮空或观察，不计作待卖出。

接口 `/api/strategies/executions` 仅接受已登录会员或管理员；会员固定读取自己的 `user_id`，管理员可切换 owner。响应禁用缓存。页面 HTML 可直接打开，但未登录无法获取选股数据。

实时接口 `/api/strategies/today-market` 同样按登录身份和 owner 限定，服务端从当天的选股记录确定证券代码，不接受前端传入股票代码。分钟 K 从 `aiia_stock_realtime_minute_snapshot` 读取当天已完成的 1 分钟数据；应取根数按上海时间 09:31—11:30、13:01—15:00 计算，午休不累加，最多 240 根，不混入前一交易日。报价沿用 Stock Agent 的 Upchina 实时行情提供者；只展示返回交易日等于当天的报价，并显示行情源时间。行情源失败时 K 线仍可显示。后台标签页暂停轮询，恢复可见时立即刷新。

## 验证

- 本地定向测试 `tests/test_strategy_execution_view.py`：5 passed；覆盖会员隔离、旧表待回填、新表优先、午休和收盘分钟数、仅查询当天入选股票。
- 本地 Flask 路由导入、TypeScript 类型检查和 Vite 构建通过；浏览器预览检查了桌面布局、状态及筛选。
- 在服务器的只读数据库连接中运行同一展示服务，返回 20 个交易日、3 个策略、9 条选股记录、2 条可执行待回填。当前线上只有 definition/selection 两表，新 run/execution 表尚未部署。
- 在服务器以只读方式调用今日行情服务：10/15 今日股票 `002636` 返回 240 根分钟 K，首根 09:31、末根 15:00；行情时间为 15:30。行情时间必须展示，避免闭市后误称报价仍在跳动。
- 以上不等于生产入口已上线。服务器 Web 版本为 `5ba4ea0`，发布前需在该版本上合并和验证前端导航差异。

## 人工发布 Gate

仓库 `AGENTS.md` 要求生产部署、重启及数据库操作由人工 Gate。发布负责人确认后，在隔离发布目录核对 Web 代码和前端构建、现有服务配置及回滚快照，再激活 Web 服务并检查公网入口和权限隔离。这个看板不依赖新表迁移；未获数据库 Gate 时仍可用现有选股表上线。不得把旧记录的缺失收益补成 0%。
