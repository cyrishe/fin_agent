# 2026-09-10：MCP Skill 与主框架优化发布记录

## 代码与发布准备

- 功能提交：`e05ad18`；干净构建所需测试样例修正：`da641bd`。
- 分支：`agent/financial-tool-design-protocol`，已推送 GitHub、Codeup。
- 服务器正式目录：`/home/che/cyris/fin_agent`，由 `923a94a` 快进至 `da641bd`。
- 部署备份和隔离验证目录：`/home/che/cyris/fin_agent_deploy/mcp-skills-20260910-e05ad18/`。
- `source-before.tar` 保存旧 Git 源码，`frontend-before/` 保存旧前端；不包含服务器凭据和运行数据。
- 隔离源码的 1291 个 Git blob 已逐个与固定提交校验一致。
- 服务器 `.env`、nginx、依赖版本未更改；未执行数据库迁移或权限绑定修改。

本轮包含前序多 Skill 选择、预算收尾、参考数据折叠和追问推荐，以及新的 MCP `finance_task`、`list_skills`。
`finance_data_query` 保持兼容；`finance_task` 可自动选择或显式指定有序 `skill_ids`。

## 服务器检查

- Python 3.10.12 专项 10 文件：89 passed。
- Node 22.19.0 DSH 协议：59 passed。
- 前端 Vitest：127 passed。
- TypeScript 和生产构建：通过；保留既有大 chunk 提示。
- 首次干净构建发现前端测试样例缺少 `createdAt`；已在 `da641bd` 修复后重建通过，未将失败构建安装到线上。
- 新前端 index SHA256：`26c5f610a56c237869c0b88ac226babb60f522e37a1d38ee5aac7f19a1928141`。

本地全量回归仍有 7 项已在旧基线复现的失败，具体见 `docs/development_tasks/mcp_skill_invocation_20260910.md`；不宣称默认全量质量门通过。

## 激活状态

源码已同步，新哈希静态资源已预置；旧 index 保留，避免新前端与旧后端混用。
部署账号尝试 `sudo -n systemctl restart fin-agent-web.service fin-agent-finance-api.service`，
服务器拒绝：`sudo: a password is required`。没有通过杀进程或替代守护进程绕过权限。

管理员需要在服务器执行：

```bash
sudo systemctl restart fin-agent-web.service fin-agent-finance-api.service
```

重启前确认的进程：Web PID `882794`、API PID `882790`，启动时间均为 `2026-09-09 19:37:51 CST`。
重启后须核对新 PID、健康状态及公网 tools/list 包含 finance_task/list_skills，再安装已构建 index，检查公网 HTML 与 JS/CSS。

## 研报验证

验证脚本：部署目录 `verify_report_skill.py`；本地副本 `outputs/server_report_skill_20260910/run.py`。
覆盖山东黄金看多理由、科大讯飞 AI 催化、阳光电源竞争格局、鹏鼎/沪电盈利预测比较、茅台共识分歧、安琪糖蜜成本敏感性。
显式选用 `equity-report-analysis` 与不传 skill_ids 的自动选择分开记录。

等待重启期间先在服务器隔离 HTTP/MCP 实例执行固定业务版本。隔离实例使用生产金融只读 Provider 和既有 SkillHub 读取，独立会话及 worker 目录，不写生产用量计数。
隔离结果不能替代正式服务重启后的公网验证。正式 API 请求正常产生的用量记录属于本次测试范围。

### 服务器隔离实测结果

6 条请求均 `ok=true`、返回完整回答、实际加载 `equity-report-analysis`。显式 4 条、自动 2 条。
单次耗时 36.32–94.52 秒；观察到 5–9 次模型响应。没有把接口完成率当成业务准确率。

| 样本 | 方法选择 | 耗时 | 人工阅读结果 |
|---|---|---:|---|
| 山东黄金看多理由 | 显式 | 50.39 秒 | 两份研报的机构、日期、理由与风险可追溯，说明目标价缺失和样本边界 |
| 科大讯飞 AI 催化 | 显式 | 51.69 秒 | 完整回答；有重复取数；“公司披露事实”与“研报转述”层级仍有表达不一致 |
| 阳光电源竞争格局 | 自动 | 36.32 秒 | 自主加载研报 Skill，读取专题参考，给出优势、竞争压力与机构口径限制 |
| 鹏鼎/沪电预测比较 | 自动 | 40.05 秒 | 正确查询预测指标并区分时点，但正文把更小的差距称为“更大”，有数值引用不一致和未取证业务归因 |
| 贵州茅台共识分歧 | 显式 | 94.52 秒 | 13 篇研报，结合评级聚合、预测和行情；回答未截断，但机构/评级计数表达不一致，存在超出历史估值证据的结论 |
| 安琪糖蜜敏感性 | 显式 | 52.92 秒 | 读取敏感性参考并补财务数据；列出缺失输入，但随后无依据假设历史降价幅度反推 7%–8% 净利影响，税前成本与归母净利口径也未充分衔接 |

部分答案仍带“数据请求已完成”等过程措辞。上述业务问题保留原始首次结果，不通过重跑覆盖，也不以单题关键词或硬校验补丁修饰通过率。

证据：服务器部署目录 `report-isolated/`；本地 `outputs/server_report_skill_20260910/isolated/`。
每题记录请求、完整回答、参考数据样本和 detail；manifest 记录版本、配置、源码 hash、方法与用量。
返回行数可能包含多次查询的重复记录或聚合结果，不等于独立研报篇数。

**当前未完成：正式服务重启、新前端 index 激活、公网新版 MCP 实测。** 用户已授权部署，但账号缺少免密重启权限，等待管理员完成上面的重启命令。
