# Fin Harness 服务器只读核对（2026-09-28）

本记录来自对 `ai-agent.kingdomai.com` 的只读 SSH 检查，以及本机固定模型响应的 SDK/MCP 回放。没有在服务器安装、提交、部署或重启服务，也没有读取或记录凭据。

## 服务器现状

- 运行中的 `fin-agent-web` 与 `fin-agent-finance-api` 进程，其 `FINANCE_DSH_SOURCE_ROOT`、`FINANCE_DSH_SDK_SOURCE` 均指向 `/home/che/cyris/deepseek-harness`；观察到的 DSH Node 子进程工作目录也在旧 checkout（实际路径 `/data/cyris/deepseek-harness`）。
- `/home/che/cyris/fin_agent` 位于 `9b70f7718a0eae26c331cf0ca44ea99c2dedd804`；旧 Harness 位于 `0433ae61319e1cdbe8adc8f1ca7192306bd2a6e4`。服务器还没有 `/home/che/cyris/fin_harness`。本机已推送的 Fin Agent `c1a2184` 及锁定的 Fin Harness `38ea60b` 尚未部署。
- 只核对了已跟踪文件：服务器 Fin Agent 的 `src/scenarios/custom_tool/dsh_intent_router.py`、`dsh_service.py` 有未提交修改；旧 Harness 的 `packages/llm/llm-deepseek/` 下两个源码文件、一个测试及三份 README 有未提交修改。未来迁移先审查、保存和归并这些现场差异，不能直接覆盖工作树。

## 空结果回放的判定

原 `scripts/verify_finance_dsh_empty_result_replay.py` 的追问夹具跳过了本轮目录读取，真实策略拒绝随后直接执行的 `finance_query`；夹具继续给出固定答案后仍有待完成动作，后续模型响应耗尽，表现为 HTTP 500。该现象在新旧源码上相同。

修正目录调用后发现第二个过时假设：此 API 会提供 Skill 目录。策略的 `skillGuidedAnswer` 在目录可用时保留最终模型综合，即使没有加载具体 Skill；因此 `emptyResultEarlyStop=true` 也不表示每个零行结果都会跳过模型。Host 生成的零行摘要用于最终响应，不会作为该摘要写回 DSH 会话历史；追问恢复的是上一轮真实模型消息。未修改业务策略或 Harness 核心。

修正夹具的上述假设后，在 Fin Harness `38ea60b` 上，baseline 与优化开关开启时的空结果、追问、未完成流程、非空结果和只返回数据共 10 个固定场景全部通过；未授权 MCP 请求仍返回 401，首两次模型请求字节一致，行数、摘要和样本相同。回放使用本地固定模型响应与只读金融查询，不能证明真实模型效果、Token 节省或生产稳定性。无 Skill 目录时的零行安全交接由 `tests/dsh_finance_loop_policy.test.mjs` 的定向测试覆盖。
