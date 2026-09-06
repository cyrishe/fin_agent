# DSH 与工具体系发布复核（2026-09-06）

## 范围与结论

复核金融查询 DSH 上下文压缩/阶段预算（`9a53e69`）、自定义工具 DSH 编排与 Codex 交接、候选验证与启用、前端子路径部署，以及模型配置兼容性。修正下述问题后，自动回归通过，可以同步代码。

系统数据库迁移不属于本次发布：保留现有连接和旧库数据，待停止写入后另行进行一致性迁移。此前日用量功能的外部请求表尚待数据库迁移时创建，不能将“代码已部署”视作统计已经启用。

## 复核与修正

| 项目 | 处理 |
|---|---|
| DSH 查询上下文 | 小表完整返回；大明细按连续行分页；截断后同步 `sample_complete` 和分页元数据；原始数据仍保存在结果注册表。 |
| fast 返回边界 | 仅 data-only 原生结束；需要 summary 时保留最终模型步骤。 |
| 自定义工具职责 | DSH 保存 Requirement/Design/Flow；父进程调用现有 Codex 实现与测试，不在 MCP 子进程复制 Coding 生命周期。 |
| 多轮 bridge | 最新代码更新 context revision；补充第二轮回归，验证工具枚举不会清空本轮资产及 trace。 |
| 路由开关与附件 | 禁用 intent router 时走原预处理；带附件输入保留原多模态入口，不被仅支持文本的 router 截断。 |
| 用量汇总 | 非工具意图的路由调用仍计入 planning usage；同步 Chat 汇总不再重复加已经包含的 preprocess usage。不是对全链路 usage 完整性的声明。 |
| 配置兼容 | 恢复显式 `LLM_ENDPOINT` 的兼容读取；API 文档示例统一为 Astra low。 |
| 服务器 MaaS endpoint | 真实服务器使用工作空间专属 `*.maas.aliyuncs.com`；修正只认可 `dashscope` 主机名的检查，两种阿里云端点共用正确 Key 来源。新增兼容与域名边界测试。 |
| 干净克隆可启动 | 将 DSH 启动脚本的可执行位纳入 Git，避免只在原机器可执行。 |
| 测试与当前协议 | Profile 测试先记录技术验证再启用；Skill 文案断言同步当前术语。 |
| 单元测试隔离 | 代码步骤/绑定测试不再调用真实总结模型。原失败由模型把两行排序样例判断为“覆盖不足”引起；未针对该样例修改业务判断。 |

候选启用仍检查技术验证，用户查看/交互与执行状态分开。未改动金融 API 字符串协议，也未加入用户语义一致性静态检查。

## 自动回归

- Python：`pytest tests --ignore=tests/manual --ignore=tests/integration -q --disable-warnings --maxfail=5`：**1212 passed，27 skipped**，约 132 秒。
- Node：自定义工具与金融查询两套 loop policy：**27 passed**。
- 前端：Vitest **111 passed**；TypeScript 与 Vite production build 通过。构建仍有大 chunk 提示，不影响本次构建成功。
- `git diff --check` 通过。

服务器首次兼容回归发现上述 MaaS 域名问题，已在刷新服务前修复。配置初始化测试改为隔离的模拟 `.env`，不再依赖开发者本机私钥配置。

跳过的测试及 `manual/integration` 未被上述数字覆盖。前作者的真实 DSH 样本停在 Codex 交接前，不能算完整 Chat/SSE→Codex→候选→验证端到端通过；完整链路与 P50/P95 效果评测仍应继续。

## 服务器发布边界

服务器现有工作区包含已部署但未提交的补丁。发布先留存补丁、受影响文件和前端构建，再以旧基线做三方核对，逐项处理重叠改动；不覆盖 `.env`、运行数据或已有日志。更新后检查 Python 3.10 兼容性、DSH policy、服务入口和真实路由。数据库迁移与 nginx 特权配置不夹带执行。
