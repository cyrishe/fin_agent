# DSH Opt 上下文与执行效率复核（2026-09-06）

## 范围与证据

这是研究记录，不包含运行时代码修改或部署。依据：

后续实施与真实 A/B 的独立记录见 [实施与同题验证](financial_qa_dsh_context_implementation_20260906.md)；下文保留调研时的证据和推断，不将预期收益冒充实测。

- 既有 `financial_qa_dsh_policy_productization_20260902.md`、`financial_qa_dsh_fast_mode_and_framework_review_20260904.md`、`financial_qa_public_trace_and_dsh_opt_20260904.md`。
- 当前金融 DSH 代码，以及本地 DeepSeek Harness `cd5ef8148158c3a752a658978873241fdf8e2bbc`；同时查阅其官方 GitHub 对应版本资料。没有升级框架。
- 服务器 9 月 4 日保存的运行索引与完整 `session.jsonl`，选择结果投影上线后的 8 次运行、5 个不同问题，共 27 次 LLM 请求、13 次目录读取。样本包括上线过程中的不同策略修订，不能当作当前同一版本的随机准确率评测。
- 当前代码离线生成目录的大小复核，以及直接调用当前 DSH 插件的离线探针。没有重新发出模型请求，也没有重新查询数据库。

量化结果：`outputs/financial_qa_dsh_context_audit_20260906/analysis.json`。
只读审计脚本：`tmp/dsh_context_audit_20260906/analyze.py`；插件探针：同目录 `probe.mjs`。
原始会话副本保留在同目录 `workers/`；完整日志只供内部审计，不进入用户上下文或公开报告。

本文的“字符”是解析日志外层 JSON 后，模型内容中的实际 Unicode 字符数，不是日志文件大小，也不是 token 估计。各部分合计不包含所有协议分隔符，不能替代 provider usage。历史指标使用实际运行记录；优化幅度的推断单独标注。

## 结论与实施顺序

| 顺序 | 方向 | 证据/预期 | 范围 |
|---|---|---|---|
| P0 | MCP 目录无损序列化 | 完整目录字符数可减少约 60%–64%；没有删除字段或例子 | DSH 内部 MCP 桥 |
| P0 | 结果投影后的完整性与分页信息一致 | 真实结果中出现 complete 相互矛盾、page 与可见行数不同 | DSH 结果投影 |
| P0 | 修正 fast 结束条件和重试边界 | 当前 hook 不区分 data-only 与 summary；底层仍重试 | DSH 策略 + 共用执行器的现有模式 |
| P1 | 小型结果按实际体积带齐 | 10 日行情全量仅约 1,100 字符，却花费额外 LLM 一轮取明细 | DSH 首次结果预算 |
| P1 | 清除冲突、错场景和重复指令 | query 工具同时禁止和鼓励补充查询；研究模式要求不存在的 Skill | 共用工具说明 + DSH 场景提示 |
| P1 | 全部零行的确定结束提前到工具边界 | 已生成的答案被统一摘要覆盖；样本白花约 3 秒、7,742 输入上下文 tokens | DSH 结束策略 |
| P2 | 已结束阶段的内容退出模型历史 | final 仍带完整路由、目录、旧阶段提示；原生 surface 支持可追溯替换 | DSH 插件 |
| P2 | 小型数值表的可逆紧凑表达 | 降低每行重复键名；公开结果继续 row-dict | 模型可见投影，可复用至 CC |
| P2 | 同阶段独立查询并发 | 框架可并发，当前 MCP 工具默认 exclusive，flow 内也串行 | 业务执行器/DSH 适配层 |

不建议继续全局压低 maxTokens、盲目裁剪目录字段、增加固定的检查模型、扩大关键词路由规则，也不建议引入候选检索。

## 1. 最大的确定性“水分”：MCP 把完整目录重新转义了

### 已复现的链路

1. `tools.py::_tool_result` 原本用 `ensure_ascii=False` 返回 JSON 文本。
2. `dsh_mcp_server.py::_payload_from_sdk_result` 把文本还原为 dict。
3. `create_server` 的 `call_tool` 把 dict 交给 Python MCP SDK。
4. 当前 MCP SDK `server/lowlevel/server.py` 对 dict 自动使用 `json.dumps(results, indent=2)`，默认 `ensure_ascii=True`。
5. DSH 使用该 text content，中文成为实际的 `\u7814\u62a5` 字符序列；这不是查看日志时外层编码造成的错觉。
6. DSH post-execute 已对 query/details 重新 JSON.stringify，所以它们恢复了紧凑中文；catalog 被 kind 判断跳过，仍是膨胀格式。

| 当前完整 execution pack | 实际目录文本字符 | 同一对象紧凑 UTF-8 JSON 字符 | 无损减少 |
|---|---:|---:|---:|
| 股票·研报明细 / query | 7,619 | 3,049 | 60.0% |
| 股票·行情 / query | 13,556 | 4,940 | 63.6% |
| 股票·财务三表与财务指标 / query | 17,464 | 6,726 | 61.5% |

13 个真实目录结果逐个完成 JSON 解析、重序列化、再次解析的相等校验；字段、规则、方法、例子、顺序保持不变。当前 catalog 生成结果也复现同样体积。

由于目录在后续请求中重复携带，仅此无损变化在 8 条运行中可累计减少 214,418 个内容字符；各条运行的累计组件字符减少 15.5%–38.7%。这是离线字符比较，不能直接说 token 或时间同比减少。

建议在内部 MCP 返回时显式提供 `CallToolResult(content=[TextContent(紧凑中文JSON)], structuredContent=原对象)`。SDK 原生支持此类型，不需修改 MCP SDK 或 DSH 源码。另一个较窄方案是让现有 post-execute 也对 catalog 做无损重序列化；两个方案选一个，不新增两层重复逻辑。

CC 当前直接消费共用 `_tool_result`，不经过这个 DSH MCP dict 包装环节，因此不能把上述 60% 直接作为 CC 收益。

## 2. 已经压过的结果有信息不一致，必须先修

### 2.1 完整性相互矛盾

兴森科技出货量样本（session `5be6b488…`）返回 5 条研报。首次结果的长文本被缩短后：

```text
sample_complete = false
result_projection.complete = false
step_evidence.sample_complete = true
step_evidence.guidance = “…sample_complete=true，禁止再分页加载。”
```

根因：`projectQueryPayload` 只更改顶层 sample_complete，未同步模型可见的 evidence 和 guidance。`dsh_system.md` 又要求“只遵循 step_evidence.guidance”，模型实际收到冲突指令。这不能归咎于 low 模型不听话。

当前插件离线探针用 1 行、600 字文本也复现：原对象保持 600 字，模型副本只剩 481 字，顶层 false、evidence true。

修正方向：只在模型副本上，从同一投影结果派生一致的完整性和下一步提示；服务端完整事实保持原样。优先收敛已有重复表达，不再增加一组状态枚举或业务 validator。

### 2.2 分页事实与实际可见内容不同

- 今世缘：服务端 page 为 `offset=0, returned=20, total=20, has_more=false`，但模型只看到前 5 + 后 5，共 10 行。
- 中际旭创：page 返回 11 行，模型只看到 10 行。遗漏的中间行没有明确行位置标记。

原始数据库结果正确；问题在模型投影把“服务端已取完”与“模型已看完”混在一起。模型可能据此认为已覆盖全体机构/所有报告期。顺序只保留首尾也不等于满足机构覆盖或评级比例计算。

建议：在预算容纳时保留完整连续小页；超预算时明确给出可见页范围与未读范围，不以不可恢复的中间省略冒充完整页。必要的位置属于可精确读取的机器事实，不应让模型猜。长文本按完整字段或可寻址文本页读取；现有接口只有行 offset/limit/columns，无法读取同一长字段被裁掉的后半段。

### 2.3 当前字符预算不是完整内容总预算

`projectRows` 只扣减字符串值长度，不计算键名、JSON 结构、数值、schema；query 多个 step 还会各自重新领取一份预算。`queryTotalMaxChars=6000` 因而不等于整个模型工具结果最多 6000 字符。

字段按遍历顺序消耗预算，前面的长文本可能挤掉后面的日期、机构、单位。8 个样本没有出现全字段只剩省略号，但代码路径确实允许，属于实现边界而非已发生频率。

建议从整个模型副本的序列化体积计量，优先保留行身份、日期、单位及数值等已选字段的完整内容；长字段按独立可寻址粒度处理。不要降低总预算后继续按字符串前缀盲截。

## 3. 五类实际问题的逐轮分析

下表是历史真实运行，不是本次新跑，也不是相同版本的统计 A/B。

| 问题/运行 | 逐轮真实输入上下文 tokens | 路径 | DSH 耗时 |
|---|---|---|---:|
| 贵州茅台 5 日行情 | 3,614 → 10,407 → 10,763 | 目录 → 查询 → 回答 | 48.22s |
| 宁德时代 10 日行情 | 3,669 → 10,095 → 10,678 → 11,356 | 目录 → 查询 → 明细 → 回答 | 22.72s |
| 今世缘估值方法 | 3,613 → 6,985 → 8,786 → 16,522 | 目录 → 查询 → 明细 → 回答 | 16.77s |
| 中际旭创研报比例 + 净利走势 | 3,675 → 14,579 → 15,915 → 16,811 | 同轮两目录 → 一次双步骤 flow → 明细 → 回答 | 21.16s |
| 兴森科技出货量，零行 | 3,668 → 7,174 → 7,742 | 目录 → 查询零行 → 生成随后被替换的回答 | 11.74s |

### 3.1 宁德时代：固定行数制造了本可省掉的一轮

10 行完整明细、5 列的整个工具返回仅 1,109 字符。首次查询却只给 3 行，因为“带齐”的条件是 `row_count <= 5`；模型随后必须调用明细工具。

明细工具自身约 15ms，但选择明细的模型阶段约 2.33s，同时再消费 10,678 个输入上下文 tokens（含缓存）。理论路径可以回到 3 次模型请求：第一次结果就携带全部小表。实际节省时间仍需重跑，不能机械减去所有旧阶段 tokens，因为新 final 输入也会改变。

建议把 5 行阈值改成“完整结果在有限行数与真实序列化预算内则带齐”。31 行×少量数值列完全可能比 3 篇研报正文小，不能用统一行数判定上下文风险。多行上限保留作异常边界。

### 3.2 今世缘：证据比早期长尾紧凑，但仍有历史协议负担

最终请求组件字符约 36,267，其中：

- 工具 schema/说明 8,229；
- 目录 7,619（无损编码后 3,049）；
- 首次查询样例/证据 3,649；
- 明细 12,225；
- 累积阶段提示 1,044；
- 全局 system 1,262；其余为用户文本、先前工具调用/模型消息。

目录中的请求方法和未用字段在最终写答案时大多不再需要，但已选字段的财务口径、单位、报告日期语义等仍需保留。不要把“删除整个目录”当默认方案。

另外纠正此前性能归因：旧 RTE005 读取 report + report_metric 两个目录、查询 25 + 60 行；新样本只读取 report、查询 20 行。65.69s → 16.77s、110,707 → 35,906 的现象成立，但不能因为都是 4 次模型调用，就认定全部差异由投影造成。查询路径、结果量、服务路由和运行条件也不同。下一轮必须同时做固定工具结果重放和端到端 A/B。

### 3.3 中际旭创：多目标能力存在，额外目录和默认口径仍有代价

一次正常 flow 里确实放了研报与财务两个目标。另一运行同时加载 report 的 query、aggregate 和 financial_3_table 的 query，最后只使用 report.agg 与 financial_3_table。report.query 的 7,619 字符未被执行，却进入两次后续请求；report 的公共字段和规则也被两个 operation 重复加载。

优化方向：首次选择只包含真正需要的 operation；同一 dataview 的多个 operation 若均必要，可从同一 catalog 生成一次公共字段和多个精确 contract，避免重复，不做检索、不复制第二份目录说明。

另两条运行给“多期净利润走势”生成无时间范围、仅 `order/limit` 的请求，实际只有最新一期。减少模型轮次不能以这样的证据缺口为成功指标。需要维持 catalog 的默认范围/财务累计口径，并清理与它竞争的指令，再判断是否有必要给 query 阶段增加推理预算；不为该公司单独加规则。

### 3.4 兴森科技：零行最终回答是实际可省的生成

最后一个零行样本仍有第 3 次模型调用：输入上下文 7,742 tokens、输出 166 tokens、约 2.97s。共用服务在模型运行完成后，才调用 `_all_zero_result_summary` 覆盖这段生成结果。

建议把已经存在的“可直接结束且全部零行、无运行错误”的确定条件提前到 DSH 工具边界，使用原生结束机制，返回同一个业务摘要。保留多目标部分成功、待执行依赖、尚需修复等原有语义；不能简单把任意一个零行当整轮完成。无需新模型、关键词分类或第二份业务总结。

### 3.5 贵州茅台：不是所有慢都来自 LLM

48.22s 中，真实查询工具耗时 36.40s；3 次模型阶段合计约 11.77s。目录仅约 19ms。即使模型耗时归零，仍有约 36 秒的数据路径开销。

另外宁德时代 Chat 端到端 29.82s，DSH 内部 22.72s，差约 7.10s，可能包含入口规划、排队、网络及结果处理。这段差值没有充分 span 分解，不能全部归因于 DSH。

因此应分别记录入口/队列/模型/工具/返回处理；本轮没有查询调用内部的完整慢 SQL 证据，不能直接归因为某个数据库或提供商。

## 4. 提示词“水分”更多是冲突与错场景，不只是字数

### 已确认的问题

1. `finance_query` 的 2,437 字符工具说明前段要求“不添加无关解释性查询”，后段却继续要求成功后进入解释目标，并列举趋势、期间比较、构成和同行比较。还残留不完整句 `confirmation, not context; (2)`。这是真实拼接残留，应整体整理该段，不再末尾叠加反向提示。
2. `dsh_system.md`、工具 description、stage prompt、step_evidence.guidance 四处都在定义成功后下一步。部分规则重复，部分冲突。应明确一份场景总原则、一份工具契约、一份当前阶段动作与一份实际结果事实。
3. `STAGE_PROMPTS.catalog` 硬编码 report/report_metric/financial_3_table 的业务范围，复制了 catalog 的选择边界，违背之前的唯一真源目标。业务边界应保存在 catalog 并同源渲染，阶段提示只决定当前动作。
4. DSH 的 `_prompt` 注入了共用 `research_mode_prompt`。auto/deep 会要求匹配或加载个股研究 Skill，但当前 DSH sdk-minimal 只接入三个金融工具，无加载 Skill 的能力。fast 也带着“二至三个证据目标”的研究模板；今世缘 final 真的出现“二至三个会改变结论的证据目标”标题。该表达泄漏说明模板没有按纯查数场景适配。
5. 固定前缀模式下，stage prompt 通过 pre-step 追加为 user/message；前期 catalog/query 指令不会自动删除。今世缘四轮累积提示 408 → 629 → 843 → 1,044 字符。体积相对目录不大，但旧指令会继续竞争模型注意力。

### 建议的资产分工

| 资产 | 应保留的内容 | 不宜承担 |
|---|---|---|
| 全局 system | 金融数据真实性、用户目标、输出语言与证据原则 | 每一阶段完整操作手册 |
| 三个工具 schema/description | 工具输入输出、引用方式、何时适用 | 研究框架、补充分析模板、多轮修复策略重复描述 |
| catalog execution pack | 完整选中粒度的功能、字段、规则、方法、例子 | 其他未选 operation 的重复内容 |
| 当前阶段 hint | 当前动作、现有失败/完成事实、允许的恢复范围 | 再复制 catalog 业务范围 |
| 结果 evidence | 实际筛选、记录数、字段覆盖、当前可见完整性 | 重复长篇通用指令 |
| 回答要求 | 此场景的回答长度/组织偏好 | 要求 DSH 加载其不存在的 Skill |

这不要求大规模扩展 Output Schema。共用工具说明整理可同时惠及 CC；DSH 阶段和研究提示的调整属于 DSH 专有适配。

## 5. DSH 原生接口能做到什么，不能误用什么

### 可追溯的历史内容替换

DSH 的 Session surface 原生允许追加 replacement 事件，引用被替换事件的 `sourceEventSeqs`，后续模型看到新的内容，而原始事件日志保留。官方 `compaction-tool-result-pruner` 已使用这条路径，不需要调用总结模型，也不需要 fork 框架。

可在 catalog/query 生命周期结束后试验：

- 旧阶段指令替换为简短完成事实或当前阶段快照；
- 已执行 catalog 的方法、例子退出 final 上下文；完整保留所需口径规则、单位、字段语义和实际 selection；
- 首次样例被更完整明细覆盖时，避免再次携带重复长文本；保留实际查询条件与 result_ref。

按完整章节和结果页处理，不截断 catalog 句子，不让另一轮 LLM重新编写语义摘要。若要从现有混合 `rules` 中剥离“调用规则”和“解释口径”，必须先证明可完整保真；第一阶段可以保留全部 rules，只移除已经不需要的调用方法/例子。

重要限制：`agent/request` 只接受 provider/model/reasoningEffort/temperature/maxTokens/stop，并不接受 messages。`llm/stream` 的 loop 请求是 deep-frozen，框架明确要求监听器只读，不暗改内容。必须用原生 session surface，而非在发 HTTP 前偷偷删字段，否则回放和实际请求不一致。

`systemPrompt.context()` 支持动态上下文快照，但其更新本身不能保证把所有旧 stage message 移除；要结合 surface 生命周期设计，不能只把 API 换个名字就宣称实现历史回收。

### 缓存需要实验，不是“保留越多越省”

移除旧内容会使首个变化位置后的前缀缓存失效；工具动态隐藏也可能改变 header。当前 `preserveRequestPrefix=false` 同时切换工具可见性和 system 组装位置，不是只切一个工具的干净实验开关。

建议分开实验：仅 catalog 无损编码、仅说明整理、仅历史替换、仅 final 工具隐藏。对 3 次调用的短请求，前缀重写可能不如稳定 schema；对多轮明细，持续背负大目录可能更贵。最终用实测未缓存输入、缓存读取、输出与时长判断。

不要删除 DeepSeek 历史 reasoning_content 来凑 token 降幅：当前官方 adapter 明确保留它以符合 tool-call 回传和网关兼容。需要进一步减少推理消耗时，应试验当前阶段的 effort/业务提示，不剪掉已有协议要求的历史字段。

### 真正并发还差哪些实现

DSH 原生工具调度支持同一步并发；只有工具明确 `isConcurrencySafe=true` 才允许重叠。当前 MCP bridge 没有传递这个声明，因此默认 exclusive。除此之外，`finance_query` 内部 `for step` 逐个 await，即使一个模型回合规划了两件事，数据库执行也没有同时开始。

业务执行器中，独立的只读请求可以先并发执行，再按原有 step 顺序统一保存 rN、lineage 与 tracker；`stepN.column` 依赖必须等待上一层完成。不能给共享结果登记器简单标一个“线程安全”就全部并发。超时还需传递到实际数据调用；`asyncio.to_thread` 外层取消不等于线程和远端查询立刻停止。

目录调用仅十几毫秒，给目录加并发不是重点；多个独立慢查询才可能有明显收益。当前样本没有独立慢查询并发对照，不能宣称可降低多少耗时。

## 6. 快速模式审计发现的边界偏差

本轮对当前插件做了无模型、无数据库的执行钩子探针：

```text
executionMode=fast, dataOnly=false → concludeTurnCalled=true
executionMode=fast, dataOnly=true  → concludeTurnCalled=true
```

原因：`tools/execute` 仅检查 fast + query 就调用 `exec.concludeTurn()`。原生 agent-loop 收到成功 terminal result 会结束；fast 的 turn-stopping 又直接 return，因此 summary/both 存在漏掉最终回答的确定代码路径。现有测试验证了 data-only terminal，却没验证 summary/both 必须保留回答轮。记录的是当前 hook 复现及源码路径，本轮未对公网 fast/both 再跑模型。

此外共用 `tools.py` 会在 execute_request 抛异常时重试一次，也会根据 provider_retry_allowed 重试，没有检查 `_finance_execution_mode`。所以“fast 无检查重试”当前仅在 agent loop 层成立，不能笼统说整个请求无重试。

建议让结束条件读取已有输出模式，并明确模型 repair 与数据请求 retry 两层的区别；按用户原有要求让 fast 行为一致。无需新状态枚举。将 data-only、summary、both、provider error 四条主线作为后续实施验收。

## 7. Token 口径与下一轮验证

当前项目 `_usage` 的 `total_tokens` 是未缓存输入 + 输出，不包含 `cache_read_tokens`；DSH session 里的 usage.totalTokens 则包含缓存读取。不能把二者都称为提供商原始总消耗。

例如新今世缘：未缓存输入 18,498，缓存读取 17,408，输出 1,276；累计输入上下文 35,906。项目 total_tokens 为 19,774，三类合计为 37,182。reasoning 已包含在输出中，不再叠加。缓存读取也不是免费，真实费用需要提供商相应计价。

下一轮建议先固定这 5 个问题，再加 31 行数值表、长字段尾部含关键结论、多目标部分失败三个边界。每项改动单独开关，做两层验证：

1. 固定 DSL 和原始结果的重放：比较模型可见内容，验证中文重序列化对象相等、选中 catalog 粒度完整、rows/page/完整性一致、出处和引用可达。
2. 同模型、同 endpoint、同 catalog/prompt hash、相同并发的端到端交错 A/B，多次运行：比较 API/筛选/时间/字段/结果覆盖、回答证据覆盖；同时记录模型调用、各阶段时间、缓存输入与输出、max-token/错误率。

衡量目标不是一味追求更短，而是“不重复传已经无用的协议，不为小表再问一轮，不花模型资源生成随后被覆盖的答案，同时完整保留当前阶段所需证据”。

## 源码索引

- Fin Agent：`src/scenarios/financial_qa/dsh_mcp_server.py`、`dsh_loop_policy.mjs`、`tools.py`、`result_registry.py`、`research_mode.py`、`dsh_service.py`、`service.py`。
- Catalog：`src/services/finance_data_tool_catalog_service.py::get_model_dataview`；唯一真源 `src/tools/finance_data/catalog/api_view_catalog.json`。
- DSH：`packages/core/agent-loop/src/agent.ts`、`tool-calls.ts`；`packages/core/session/src/index.ts`；`packages/llm/llm/src/call-config.ts`、`index.ts`；`packages/llm/llm-deepseek/src/serialize.ts`；`packages/mcp/mcp-client/src/tools.ts`。
- 官方版本固定参考：[无模型调用的结果替换与缓存说明](https://github.com/deepseek-ai/deepseek-harness/blob/cd5ef8148158c3a752a658978873241fdf8e2bbc/packages/compaction/compaction-tool-result-pruner/README.md)、[原生工具并发调度](https://github.com/deepseek-ai/deepseek-harness/blob/cd5ef8148158c3a752a658978873241fdf8e2bbc/packages/core/agent-loop/src/tool-calls.ts)。
