# DSH 无损优化第一批：确定性零行回复与内部重复处理

日期：2026-09-08。范围为本地金融查询 DSH；未修改或升级 DeepSeek Harness，未提交、推送或部署服务器。

## 1. 实施边界

本次只去除已证明不参与最终业务结果的操作。目录、tool schema、阶段提示词、推理强度、token 上限、结果投影、SQL、重试预算均保持原样；不增加候选检索或针对评测问题的规则。

已完成：

- 对已完成且全零的查询，直接返回原先系统就会使用的固定回复，省去随后会被覆盖的模型生成。
- 没有过程订阅者时，不再构造和解析过程展示内容。SDK 的原始事件、工具记录与 token 统计照常收集。
- 内部 trace/运行索引采用无损紧凑 JSON 编码；不减少审计字段，不延迟持久化，不改成容易丢失尾部记录的异步写入。

尚未实施：历史目录/结果去重、紧凑表格、查询内部并行、分页索引和长期 worker 会话回收。它们需要各自的等价性验证或性能证据，不与本批合并上线。

## 2. 架构与唯一真源

原先 `service.py` 的 `_all_zero_result_summary` 原样抽取至 `src/scenarios/financial_qa/empty_result.py`。原导入路径仍保留别名。CC 和 DSH 对外仍使用这一个函数，JS 不维护第二份回复模板。

链路为：

1. 正常模型规划、catalog 加载、DSL 执行、结果注册全部照旧。
2. Python MCP bridge 在内部 trace 中提供由完整本轮结果生成的 `empty_result_context`，类型采用 DSH 已有 plugin user message。这个字段不进入工具输出或模型 schema。
3. DSH `agent/pre-step` 在已有 final 阶段决定是否采用该回复。必须满足：非 data-only；没有必需动作；全部已发调用有成功结果；全部查询均有明确零行结果及引用；最后一批查询显式声明 `data_request_complete=true`；没有待处理输入；trace 的 turn revision 和 catalog revision 一致。
4. 条件满足时，将 Python 提供的原始 plugin message 追加至 native session 日志和上下文，并使用原生 pre-step rejection 停止下一轮模型调用。
5. SDK 原生结束原因仍是 `blocked`。宿主只有在本次事件中找到与当前结果生成的固定回复完全一致的 plugin message 时，才识别为正常提前返回。真实失败、取消及缺失该证据的 blocked 不得伪装成成功。

固定回复不是模型输出，不能伪造 `assistant/message` 或生成 token usage。下一次显式续聊可读到系统实际返回的回复；独立请求不会继承这条消息。运行记录中的 `empty_result_early_stop` 是观测信息，不是新的业务状态机。

缺少完成标记的兼容输入继续走旧流程；遇到零行中间依赖、部分成功、错误或过期 trace 也不启用提前返回。保守放弃提速，不改变查询语义。

运行开关：现有 `FINANCE_DSH_LOOP_POLICY_CONFIG` JSON 中新增可选 `emptyResultEarlyStop`，默认 true。需要停用时将其设为 false，保留原配置的其他字段，无需回退代码。

## 3. 自动回归

- Python 聚焦回归：146 passed，2 skipped；覆盖 DSH、共用 CC 场景、MCP/API 鉴权、临时 access token、评测客户端、catalog 和执行工具。
- Node 循环策略：32 passed；新增一项参数化测试覆盖 15 种终止/回退组合，包括显式完成、多空集、fast、旧参数、未完成、混合非零、失败、过期 trace、待处理输入及 data-only。
- Python 新增宿主判定回归覆盖：正常 handoff、缺失 handoff、非零、错误来源、错误文本、查询失败、取消、停用开关。
- 无过程订阅者的回归确认：展示解析函数未调用，但工具结果计数和落盘记录仍然保留。
- `git diff --check`、新增及修改 Python 脚本编译通过。

命令：

```sh
.venv/bin/python -m pytest tests/test_financial_qa_dsh_runtime.py tests/test_financial_qa_cc_scenario.py tests/test_finance_api_app.py tests/test_finance_access_tokens.py tests/test_finance_mcp_client_eval.py tests/test_finance_data_tool_catalog_snapshot.py tests/test_finance_data_tool_services.py -q --disable-warnings --maxfail=2
node --test tests/dsh_finance_loop_policy.test.mjs
```

## 4. 固定决策的真实 MCP/DSH 回放

脚本：`scripts/verify_finance_dsh_empty_result_replay.py`。

模型请求由本地 SSE fixture 响应，不消耗模型服务 token；MCP 鉴权、真实 Harness SDK/插件、内部 MCP 子进程、catalog、远端数据库只读查询、结果存储、公开数据返回均走真实实现。这是协议等价性验证，不是模型准确率或耗时评测。

| 场景 | 基线模型请求数 | 优化后 | 结果 |
|---|---:|---:|---|
| 已完成、全零 | 3 | 2 | 数据和固定 summary 相同 |
| 显式 conversation_id 续聊，再次查询零行 | 2 | 1 | 复用目录，保留前次系统回复 |
| 首次零行但未完成，后续再查询 | 4 | 3 | 首个零行未触发终止；两次查询都执行 |
| 非零，5 日行情 | 3 | 3 | 数据样例和 summary 相同 |
| data-only、零行 | 2 | 2 | 不增加自然语言回复 |

两个版本的首个零行案例，前两轮 HTTP 模型请求体逐字节相同，覆盖 system、完整工具说明、operation catalog 和阶段提示。全部场景返回的行数、样例值和 summary 一致。显式续聊能在后续模型请求中看到固定回复；后续无 ID 的独立请求中不存在该回复。未授权 MCP 请求为 401。

最终证据：`outputs/finance_empty_result_replay_20260908_v3/report.json` 及各场景的 `native_events` / `model_requests`。

回放脚本第一次运行的末尾输入等价断言失败：两个版本错误地复用了相同的显式 conversation_id，因此后版依法加载了前版的 working_set。这是评测隔离错误，不是 MCP 默认串上下文。脚本改为每次运行、每个版本独立的 continuation ID，保留版本内部续聊；没有修改运行时的历史恢复功能。失败证据保留在无 v2/v3 后缀的首次输出目录。

执行方式：

```sh
.venv/bin/python scripts/verify_finance_dsh_empty_result_replay.py --env-file .env --output outputs/finance_empty_result_replay_new
```

## 5. 真实模型同题对照

本地当前配置：阿里云、`deepseek-v4-flash-0731`；使用现有凭据进行测试，未修改 `.env`。3 个问题各跑基线/优化一次，共 6 次请求。基线只是关闭上述提前返回开关，其他参数相同。

证据：`outputs/finance_empty_result_ab_20260908/{baseline,optimized}/`。

| 问题 | 模型轮次 | 总耗时，秒 | 累计输入上下文（含缓存） | 结果与归因 |
|---|---|---|---|---|
| 已实施定增公司、发行价格及募资金额（BUS053） | 3 → 2 | 17.683 → 11.434 | 16,049 → 9,584 | 都走 corporate_action；相同 source/进展筛选、倒序和 limit=20，均零行，触发优化 |
| 贵州茅台最近五个交易日收盘价及涨跌幅 | 4 → 3 | 10.690 → 8.213 | 28,828 → 17,722 | 最终同一 API、相同查询参数和 5 行数值/schema；没有触发优化，基线多读一次 window 目录 |
| 上汽集团未来三年核心增长逻辑（RTEF070） | 4 → 4 | 19.684 → 15.250 | 39,117 → 33,409 | 均 report，但自选研报起始日期不同，14 → 6 行；不能作为效果相同或提速成立的配对证据 |

BUS053 累计上下文减少 40.28%，输入加输出（含缓存）16,581 → 9,943。最明确的消除项是基线最后一轮：2.049 秒、6,465 输入上下文 tokens、101 输出 tokens。总耗时差还包含启动、缓存及模型输出波动，不能把 6.249 秒都归因于这项改动；也不能把缓存 token 与非缓存 token 按同一价格计算费用。

非零路径没有任何新终止行为；其 live 波动不应宣称为优化收益，更不通过针对问题的提示词补丁拉齐结果。此次可靠性结论来自被修改分支的真实回放等价性及边界测试，不是对所有金融问题做出的零幻觉或全量准确率承诺。

现有同题脚本新增 `--cases-file` 和 `--[no-]empty-result-early-stop`，可复用原始问题做开关对照，不改写问题。适用完整 canonical LLM env；本地若只配置公司现有的 DASHSCOPE_API_KEY，应在评测进程中明确映射到 LLM_API_KEY，不改持久化环境，也不把密钥写入结果文件。

## 6. 下一批的准入条件

上下文去重必须保留完整语义，不重写另一份 catalog 摘要。先用固定工具结果比较每轮实际模型输入，再评测入口、所有筛选条件、单位、时间窗口、结果覆盖、必要修复及续聊；同时比较缓存/非缓存输入和耗时。无法证明不退化则不启用。分页索引、会话回收和内部并行先测各自瓶颈，不以增加配置或重写框架替代性能证据。
