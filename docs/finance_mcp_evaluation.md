# 研报问答评测

本地自动化回归：`python -m pytest -q` 和 `node --test tests/dsh_finance_loop_policy.test.mjs`。测试覆盖研报明细、年度预测、聚合、空结果、认证、MCP 与不可用数据入口，以及 DSH 目录版本和循环策略。

真实环境评测需在授权环境中准备对应表结构、只读数据库账号、模型和 DSH，再通过 REST/MCP 测试评级分布、预测数值、机构观点、无匹配记录和连续追问，逐项核对查询范围与来源。`scripts/eval_report_skill_smoke.py` 和 `scripts/eval_finance_report_mcp.py` 是历史评测入口，使用前应检查输入样本、输出目录和环境；不要将客户数据复制或上传到未经授权的位置。

结果须记录当前代码版本、配置、样本、运行时间和判分依据；单次运行不证明生产稳定性或完整效果。
