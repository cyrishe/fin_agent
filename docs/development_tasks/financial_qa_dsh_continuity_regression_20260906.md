# DSH Opt 多轮语义连续性回归

后续修复、最终回归及日K根因见 [修复与交接记录](financial_qa_dsh_continuity_fix_20260906.md)。以下保留初次调查证据。

## 范围与方法

- 发现旧清单：`outputs/context_multiturn_test_inventory_20260824/`。当时统计上下文相关 60 cases，不等于金融查询 60 cases。
- `tests/evals/finance_data_chat_v1.json`：10 cases，其中真实连续金融问答 2 cases、4 turns。本次直接复用原问题。
- `tests/evals/conversation_intent_v1.json`：26 cases，包含语义补全及顶层路由；不是金融 DSH session 测试。
- `tests/evals/natural_tool_conversations_v1.json`：25 cases，属于工具开发对话，不混入金融查询通过率。
- 旧 CC 证据：`outputs/019fa65e-ee19-7b23-8f71-36050299b7d5/financial_qa_final_latest_full.json`，两组均通过；其中数据是 fixture，不能直接作真实数据库耗时基线。
- 新脚本 `scripts/eval_financial_qa_dsh_continuity.py`：同组固定 thread/owner，原始追问，不人工补全，`isolated_request=False`，standard DSH Opt、保留 summary；不同组隔离。直接进入金融服务，**不宣称覆盖 Chat HTTP / 顶层路由**。
- 在服务端隔离快照运行，读取服务器现有 env，不修改线上服务、不创建线上用户。首轮使用 9a53e69 金融实现；复跑同步当前金融三个观测增强文件（dsh_service/service/tools）。

## 首次回归证据

| case / turn | 实际查询 | 时间 | LLM 次数 | 累计上下文 tokens |
|---|---|---:|---:|---:|
| entity_followup / 1 | 茅台，最近交易日 open | 41.379 s | 3 | 16338 |
| entity_followup / 2 | 五粮液，继承最近交易日 open | 45.921 s | 4 | 36128 |
| ordinal_followup / 1 | 宁德时代、比亚迪，close | 5.315 s | 3 | 16289 |
| ordinal_followup / 2 | 比亚迪，financing_balance | 5.256 s | 3 | 24722 |

两组的第二轮均 `resumed=true`。五粮液为 `000858.SZ`，2026-09-04，open=70.68；比亚迪为 `002594.SZ`，2026-09-03，financing_balance=12824602690 元。后一组没有将上一轮行情日期 2026-09-04 强加到融资数据。语义承接 2/2 通过，不代表长对话全面可靠。

内部原始结果和 trace：`outputs/financial_qa_dsh_continuity_20260906/`。

## 已确认的根因：语义保留与阶段重置不一致

entity_followup 第 2 轮模型直接提出正确的五粮液查询，但被 Opt pre-tool 拒绝：`金融查询策略拒绝当前阶段调用 ...finance_query`。随后重新读取相同 quote/query 目录，再执行相同查询，最终成功。

不是模型忘了上一轮，而是模型历史保留了可用目录；我们 `dsh_loop_policy.mjs` 的 `resetTurn` 却无条件回到 catalog，`allowedTool` 只允许读目录。由此产生额外 LLM 调用、目录重复注入。被策略拒绝的调用发生在 MCP tracker 之前，因此只看 `record.tool_calls` 会漏计，必须对照 harness `tool/call`、`tool/result`。

后续改进应围绕目录授权/有效期与会话生命周期一致性设计，使用已有 catalog revision 和 harness hooks；不能简单放开所有旧目录，也不应针对“五粮液”等具体问句打提示词补丁。本轮只诊断，未改生产策略。

历史原文、目录和 steering message 继续进入后续轮次，解释了上下文增长。应区分必要历史语义与重复执行资料，而不是机械删除上一轮事实。

## 当前代码复跑

第二遍共 4 turns 均正常结束，两组追问语义再次正确（累计 4 次追问成功，只有 2 个独立场景，不当作 4 场景）。`current/` 保存复跑证据。

| case / turn | 时间 | LLM 次数 | 累计上下文 tokens |
|---|---:|---:|---:|
| entity_followup / 1 | 62.425 s | 3 | 17172 |
| entity_followup / 2 | 77.244 s | 4 | 37839 |
| ordinal_followup / 1 | 6.308 s | 3 | 16385 |
| ordinal_followup / 2 | 5.273 s | 3 | 25300 |

同目录追问被阶段策略拒绝的现象再次出现。两次日K查询耗时分别 52.254 s、65.213 s，均只有一次 API execution attempt；本轮没有进一步定位到 SQL/网络/数据服务内部，不能断言数据库根因。模型步骤不是这部分长耗时的主要来源。不要用 fixture CC 耗时比较推出 DSH 更慢。

## 测试限制

本地 worker/session 复用、10 个独立会话并发隔离两项单测通过（mock harness，不冒充真实模型测试）。尚未回归全链路顶层话题切换、长对话、跨进程恢复及工具开发对话。本次不得据此宣称全部 60 项通过。
