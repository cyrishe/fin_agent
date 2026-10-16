# 2026-09-09：Skill 分析预算与回答体验更新

## 更新结果

- 业务代码：`1c647fed39af7a4b2626da46fe6ae371b2b10cc5`，分支 `agent/financial-tool-design-protocol`；已推送 GitHub、Codeup。
- 正式目录：`/home/che/cyris/fin_agent`，从 `9b844e5` 快进同步。
- 前端在服务器 Node 22.19.0 环境构建后安装；先复制资源，最后替换 index，保留旧哈希资源供已有页面使用。
- 公网 `/fin_agent/` 返回 200，HTML 与本次构建一致，直接引用的 JS/CSS 均返回 200。
- index SHA256：`6c9cf25fdf98db71ebd7450fc43b7b7d11949d9b2a7a53e3e8b2fbd6d8bb0cf9`。
- `.env`、数据库、nginx、运行数据和依赖版本均未修改。

## 仍需管理员执行

部署账号没有免密 sudo；未通过杀进程或其他方式绕过权限。两个现有服务保持 active，但后端仍是更新前进程（Web PID 588080、API PID 588068）。**静态页面已更新，Skill 加载事件和新预算需重启后才完整生效。**

```bash
sudo systemctl restart fin-agent-web.service fin-agent-finance-api.service
systemctl is-active fin-agent-web.service fin-agent-finance-api.service
curl -fsS http://127.0.0.1:22054/health
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:22056/
```

然后刷新浏览器。无需修改环境变量；已只读核对新代码与当前配置合并后的 Skill 上限为目录 12、查询 8、明细 6、单次输出 8192 token。服务重启有短暂中断，选择无进行中请求时操作。

## 验证与边界

测试代码均为上述业务 commit，服务器隔离目录：
`/home/che/cyris/fin_agent_deploy/skill-ui-20260909-CgA3hm/source`。

- 前端全量 Vitest **117 passed**，TypeScript 检查与生产构建通过。构建仍有图表依赖的大 chunk 提示。
- DSH Node 协议 **52 passed**。
- 服务器 Python 3.10.12 相关六文件回归 **110 passed、1 failed**。失败是 `tests/test_finance_business_skills.py::test_crawler_falls_back_to_managed_driver_when_path_driver_is_broken`：现有 SeleniumManager 缺少 `binary_paths`。在更新前的 `9b844e5` 上单独复测同样失败；相关爬虫、测试、依赖文件本次无改动，未升级依赖或隐去失败。
- 本地相同六文件 Python 3.12 回归为 **111 passed**。
- Python workload：`test_financial_qa_presentation.py`、`test_financial_qa_dsh_runtime.py`、`test_finance_dsh_skill_first.py`、`test_finance_business_skills.py`、`test_llm_stream_block_service.py`、`test_finance_skill_first_integration.py`。
- 未运行真实生产对话、效果评测、压测或恢复演练；不得将接口 200 或专项测试解释为整体生产质量门通过。

## 备份

更新前源码归档与前端完整备份位于：
`/home/che/cyris/fin_agent_deploy/skill-ui-20260909-CgA3hm/`。

- `source-before.tar.gz`：旧 Git 源码，不含服务器 `.env` 或运行数据。
- `frontend-before/`：旧前端产物。
- `source/`：本次固定 commit 的构建及验证目录。

未执行回滚。若需恢复，先由管理员确认范围并停止相关服务，再使用备份；不要覆盖 `.env`、数据库或用户运行数据。
