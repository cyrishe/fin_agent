# 2026-09-09：DSH 完成控制、结果筛选与等待提示

## 已完成

- 业务提交 `9d6655627c9aff0300f85f968a4e1fc554365a72`，分支 `agent/financial-tool-design-protocol`；已推送 GitHub、Codeup。
- 正式目录 `/home/che/cyris/fin_agent` 从 `9dab007` 快进更新。
- 服务器 Node 22.19.0 环境隔离构建前端；先安装新哈希资源，最后替换index，保留旧资源供已有页面使用。
- index SHA256：`ae724d9bef1dd48085a8333188fd9ea60a802fd9eadd4a1dd16c94a0a14e8d9b`。
- 公网 `/fin_agent/` 返回200，HTML哈希与安装产物一致；直接引用的JS/CSS均返回200。
- `.env`、数据库、nginx、依赖版本及运行数据均未修改。
- 同步后两个服务active，API本机health为ok，Web本机返回200。旧进程PID仍为Web `650742`、API `650739`，不是新后端已加载的证明。

## 需要管理员重启

选择没有进行中请求的时段，在服务器执行：

```bash
sudo systemctl restart fin-agent-web.service fin-agent-finance-api.service
systemctl is-active fin-agent-web.service fin-agent-finance-api.service
curl -fsS http://127.0.0.1:22054/health
```

然后刷新浏览器。无需修改环境变量。生产重启按人工Gate交由管理员执行，本轮未杀进程或绕过权限。

## 验证与已知边界

- 固定业务提交的服务器隔离Python专项：420通过、1排除；DSH Node协议56通过，前端类型检查及构建通过。
- 本地同组Python420通过、1排除；前端全量124通过，类型检查通过。
- 排除的旧测试仍期待12个Skill，而当前基线已存在15个。此前实际失败已记录，不能将专项通过说成全仓质量门通过。
- Python workload：`test_finance_result_view.py`、`test_financial_qa_cc_scenario.py`、`test_financial_qa_dsh_runtime.py`、`test_finance_topn_query_plans.py`、`test_finance_call_structure.py`、`test_finance_python_filter.py`、`test_finance_cross_runtime_disclosure.py`。
- 真实模型小样本及未解决的回答计数/归因错误见 `docs/development_tasks/finance_harness_completion_implementation_20260909.md`；本次部署未重新执行生产模型问答、压测或恢复演练。

## 备份

`/home/che/cyris/fin_agent_deploy/harness-completion-20260909-8zDWUU/`

- `source-before.tar.gz`：更新前Git源码归档，不含凭据和运行数据。
- `frontend-before/`：完整旧前端资源。
- `source/`：本次业务提交的隔离验证及构建目录。

如需回滚，管理员先确认并停止服务，再恢复备份中的源码及前端；保留正式目录的.env、用户数据和未跟踪文件。本轮未执行回滚。
