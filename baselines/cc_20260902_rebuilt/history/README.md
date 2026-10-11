# 2026-09-02 CC 历史评测证据（只读封存）

这里是已有结果的脱敏封存，不是一次新评测，也不是已证明逐字复原的旧代码版本。生成过程不访问模型、数据库或网络。

## 文件与完整性

- `cases.json`：20 个原题，顺序和文本不变；保留当时逐题 source_case 元数据及推荐入口。标签是历史判断，未重新标注为正确答案。
- `results.json`：完整原始 cc_results.json JSON 树；只脱敏敏感凭据、headers/cookies，以及字符串中的明确凭据形态。问题、最终答案、选择、数据、usage、时间、SSE 与 trace 均保留；原始值仅在审计列出的路径发生变化。
- `audit.json`：原文件/产物 SHA256、脱敏路径及独立历史证据。只记录路径与原因，不记录被移除的值。

原始来源：`outputs/financial_qa_prompt_policy_random20_20260902/cc_results.json`，原字节数 13,168,859。

```text
original SHA256: 24175f5dc181ed04cf7417a60a72d71f3fcf8a9dce9bcb8e5f39791546b7083c
results.json SHA256: 8d7b2c3b868924457a67b79b112c3e5aab83eec213aee84006adeb5938a4ace6
cases.json SHA256: 14aad1a3a3edee40734257f9bb4ddb6d57256f80f05991732e5989937af76ad9
```

本次发现并处理的敏感位置记录数：0。零记录表示规则未命中，不声称自动扫描能识别所有未知形式的秘密。审计测试另外逐题核对问题、答案、工具选择、usage 和计时未丢失。

## 运行设置的证据等级

| 项目 | 历史事实及边界 |
|---|---|
| 实际模型 | deepseek-v4-flash；来自20题响应，另有native消息佐证 |
| 回答模式 | 20题 research_mode=auto，均有最终自然语言答案；完整问答，不是9月8日data-only/fast实验 |
| 推理 | comparison_analysis.json 明确 CC effort=low、ClaudeAgentOptions 未显式设置 max_tokens；不是逐请求 native effort 记录 |
| 最大轮次 | 同期配置模板为12，但此次结果未保存有效max_turns；不能伪称运行时已核实 |
| Provider/endpoint | 同期代码/模板为deepseek；该次provider_transport为空，实际endpoint缺少运行绑定；不可默认等同当前Bailian路由 |
| SDK | native Claude CLI：2.1.215；Python claude-agent-sdk包版本未随该次保存 |
| 评测方式 | scripts/eval_finance_query_api_batch.py，真实Chat/SSE；HTTP http://127.0.0.1:22120；并发3、每题超时600秒 |
| CC内部超时 | 选中会话旁路记录为[180, 900]秒，普通/长题各自预算；与HTTP超时不是同一指标 |
| 会话隔离 | 同一guest身份，每题新HTTP会话和thread；本批20题没有多轮场景 |
| 目录revision | aa5713c6946a5aa1a802b0be62c6d77dec0f94bd6c33cf0b7b5b3006a39335a3 |
| 源码绑定 | 没有本次全部src/config内容哈希；重建版本不能被描述为完整精确历史checkout |

原始结果本身记录的模型、研究模式和日期等保存在results.json；补充证据的来源路径、SHA及可观察值在audit.json。

## 不能从“20完成”推导“20正确”

20/20请求正常结束并有自然语言最终回答，不等于20/20取得了题目全部所需数据，也不等于答案正确。原标签仍有已知局限：RTE053没有结构化取数；RTE033护城河走基础资料/财务表；RTE082查研报但未严格限制评级调整。完整保留这些现场，不挑选或改写成功样本。

母集为67道大陆可支持题，排除3道含news需求后，从64题以种子20260902选20；不是行情/基金/债券的全场景基准。source_metadata部分旧分类统计仍合计100，已原样保留而未悄悄改写；实际题数与入口应从逐题source_case读取。历史推荐入口不是强制唯一API，明细/聚合等价需要按题义和数据完整性复核。

## 复现封存（无模型调用）

```sh
.venv/bin/python scripts/freeze_cc_reference_history.py --scan-only
.venv/bin/python scripts/freeze_cc_reference_history.py --output-dir /absolute/new/history
.venv/bin/python -m pytest tests/test_cc_reference_history.py -q
```

生成器拒绝覆盖已有非空目录。不要复制旧个人env、cookies或Claude配置；未来运行必须由外部安全注入凭据，并与这些静态历史证据分开存放。
