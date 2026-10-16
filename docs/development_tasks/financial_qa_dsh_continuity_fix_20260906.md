# DSH 多轮目录复用修复与日K慢查询交接

## 不一致的取舍

| 现象 | 影响 | 处理 |
|---|---|---|
| 模型记得目录，Opt 新轮却拒绝同目录查询 | 多一次拒绝、多一次目录读取、重复上下文 | 修复：从 Harness 当前可见工具历史判断目录是否可复用 |
| 问题从行情切到融资余额 | 新视图需要不同字段和口径 | 保留按需读新目录，不跳过 |
| 行情日期 9月4日，融资余额日期 9月3日 | 数据源各自最新日期不同，不应伪装成同一天 | 保留原始日期，按实际结果说明 |
| 新轮仍保留旧用户语义、数据事实 | 指代和条件继承所必需 | 保留，不机械截掉历史 |
| 目录改版、上下文压缩或新会话后不能复用旧包 | 防止依赖失效或不可见协议 | 保留重新加载；不同会话不共享加载记录 |

## 实现边界

改动位于 `src/scenarios/financial_qa/dsh_loop_policy.mjs`，属于 DSH 场景适配层的 HARD 生命周期修正 + 对应 SOFT 阶段说明。未改 DSH 框架、CC、公共 API schema、SQL provider，也未增加阶段枚举。

1. 通过原生 `tools/post-execute` 为成功目录结果标注已有的权威 `finance_catalog_revision`。这是模型投影元信息，不是另一份目录，也不改变 MCP 原始 tracker / raw 数据结果。
2. 新轮通过原生 `agent.session.deriveMessages()` 读取当前模型可见历史。只认真实 catalog 工具调用对应的成功返回、相同目录版本及其中已有的函数定义。用户文字、失败包、旧版本、无版本旧包、移出上下文的包不能用于复用。冷 session 重建也不依赖额外进程缓存。
3. catalog 阶段可以选择读新目录，或直接调用已加载范围内的查询。检查只读取调用目标，对应已加载操作的 `api_name` / 目录方法模板；完整参数、字段、方法有效性仍交给原有 Python parser / validator，不复制 DSL 编译器。
4. 若一个 flow 包含未加载 API，仍先加载所需新 operation；不是只要读过 quote 就能跳到 margin、aggregate 或 window。
5. 顺带处理复用查询与新目录并行时的阶段衔接：不能因旧视图返回成功提前结束，也不能掩盖同一步查询失败。
6. 新轮可能直接输出完整查询，因此保持路由 reasoning 配置，但 maxTokens 至少覆盖 query 预算，避免继续用较小的目录预算截断查询。预算上限不代表实际 token 消耗。
7. fast 模式仍保留用户指定的“找 API → 调用 → 返回”约束；标准 Opt 才启用这次多轮捷径。

## 回归结果

原集 `tests/evals/finance_data_chat_v1.json` 的 2 个多轮 case，原问题逐轮送入同一真实 session，真实 Aliyun 模型与远程 DB；未人工补全问题。修复后两次回归共 8 turns，2 个独立场景的续问均正确。最终代码回归 4 turns 的原始 Harness trace 没有工具错误或阶段误拒绝。

基线：`outputs/financial_qa_dsh_continuity_20260906/current/`。
最终：`outputs/financial_qa_dsh_continuity_20260906/reuse-final/`。

| 追问 | LLM 次数（前→后） | 累计上下文 tokens（前→后） | 模型步骤耗时（前→后） | 语义与调用 |
|---|---:|---:|---:|---|
| 那五粮液呢？ | 4→2 | 37839→15467（-59.1%） | 11.969→3.203 s | 同样继承最近交易日 open，stock.quote mode=0/count=1 |
| 第二个公司的融资余额呢？ | 3→3 | 25300→24984 | 5.175→5.072 s | 正确指向比亚迪，加载 margin 后查询 financing_balance |

五粮液追问服务端总耗时 77.244→11.815 s，但其中 SQL API 耗时 65.213→8.565 s，变化受数据库状态、SQL 字段及负载影响，**不能全部归功于 Agent 修复**。模型步骤包括请求准备到返回；不是纯模型服务计算时间。

中间一遍模型选择了 mode=2 的收盘快照，该日是周日，所返回的 9月4日开盘价与日K一致；并未修改策略强制选 mode=2。不拿那遍 3 秒的总耗时作为日K优化证据。最终对比使用 mode=0 的结果。

24 个 Node policy tests + 23 个 Python DSH runtime tests 通过。新增覆盖同目录、新 operation、目录改版、失败与伪造包、冷 session、历史不可见、独立会话、方法模板、fast 边界、混合并行步骤以及 query 预算。长对话与完整 Chat 顶层路由尚不在本次模型测试覆盖内。

## 日K：下游数据工具 / 数据库侧交接，不在 Agent 层修复

复现调用：

```text
r1 = stock.quote(codes=["600519.SH"], mode=0, count=1) -> code,name,tradedate,open
```

入口：`src/experiments/staged_data_protocol/phase2/quote_provider.py:176`。
SQL 构造：同文件 `_build_per_entity_sql`（约 767 行），`count` 触发按证券 `ROW_NUMBER()` 分组排序，再外层取前 N。

只读脚本：`scripts/diagnose_finance_daily_quote.py`。完整 SQL、EXPLAIN、索引与返回值：`outputs/financial_qa_dsh_continuity_20260906/daily-quote-diagnostic.json`。

已验证：

- 连接 17.38 ms，不是几十秒的连接建立问题。
- `kcrp_stock_price` 只有主键 `(trade_date, stk_code)`；没有以 `stk_code` 为首列的索引。证券基础表连接走主键 `eq_ref`，不是无索引基础表 join。
- 原 SQL 执行计划预计从行情表扫描约 4,899,900 行，带 temporary/filesort。此数字是优化器估算，不是实测扫描行数。
- 原 SQL 在 8.006 s 达到本次诊断设置的 **session-local** 8 秒只读执行上限，被 MySQL 中止。历史真实工具调用成功但用时 52–65 s，最终复跑仍有 8.6–20.1 s 波动。
- 同一单证券、相同 `trade_date < '2026-09-06'` 条件的普通 `ORDER BY trade_date DESC LIMIT 1`（仍是日K，不是实时表）3.25 ms；只取行情表 2.85 ms。返回日期 2026-09-04、open=1295.88，与真实原工具结果相同。

判断：`count` 的窗口执行形态不能提前按 LIMIT 停止，又缺少匹配“代码→日期”访问模式的索引，导致单证券最近一条退化成大范围扫描排序。不是 1 行原始结果过大，也不是 DSH 重试造成；该慢调用记录只有一次 API attempt。

交给工具侧的修复方向：

1. 评估 `(stk_code, trade_date)` 索引，保留现有日期优先主键以支持跨股票截面读取；大型表变更需数据库侧确认方式与影响。
2. 优化日K provider 的 per-entity top-N 实现。单证券可使用可提前终止的索引倒序限量查询；多证券仍须保证**每只股票 N 行**，不能简单套全局 LIMIT。
3. 对正常交易、停牌、不同末次交易日、多证券、count>1、日期范围及复权字段做语义回归，并在冷/热缓存和并发条件测量。诊断 SQL 的单证券等价不代表可直接替换所有情况。

本轮没有改行情 provider、创建索引、调整全局 DB 参数或重启线上服务。仅改 Agent 适配代码，在服务器隔离测试目录验证。
