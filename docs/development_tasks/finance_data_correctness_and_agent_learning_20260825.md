# 金融数据正确性与 Agent 学习闭环任务报告（2026-08-25）

状态：分析完成，待按优先级实施。

负责范围：Fin Agent 主框架、金融数据 Tool/Catalog、Finance CC Agent Loop、经验学习与发布边界。

## 结论摘要

金融数据正确性不应在“复杂工具、细碎提示词、更多 Agent Loop”之间三选一。推荐采用三层运行结构，并增加一条与生产执行隔离的学习平面：

```text
数据语义层：负责算对、返回依据
  → Catalog / 按需知识：负责让 Agent 选对、理解对
  → 有界 Agent Loop：负责发现没有对并定向修复

真实运行轨迹
  → 经验候选
  → 回放与评测
  → 不可变快照发布
  → 下一次按需加载
```

规则归属不以“解释起来是否复杂”为唯一标准，而优先判断：

1. 是否确定、可测试、跨任务复用；
2. 是否依赖市场、日期、证券身份或数据源；
3. 一旦错误，是否会直接使数据或核心结论错误。

满足上述条件的规则，应下沉到数据接口或统一语义计算层。普通、稳定的金融常识优先使用基模原生能力；高频、特异、本地化或 Provider 特有的知识按需加载；执行后的目标—证据偏差交给有界 Loop。Agent 可以自动总结经验候选，但不得在线直接改写全局提示词、生产 Catalog 或市场规则。

当前最优先的工作不是先建设“自进化”，而是先消除模型可见契约、Validator 契约和 Provider 实际语义之间的漂移。

## 一、当前主线与已有基础

当前生产路径可以概括为：

```text
用户问题
  → Finance CC
  → read_finance_catalog（目标化读取）
  → finance_query（多步查询 DSL）
  → parse / bind / validate
  → api_runner / provider
  → result_ref / schema / sample / working_set / step_evidence
  → Finance CC 组织回答
```

已具备的正确基础包括：

- [`src/scenarios/financial_qa/tools.py`](../../src/scenarios/financial_qa/tools.py) 已提供目标化 Catalog 读取和金融查询入口，不需要把完整 API 说明长期塞入 system prompt。
- [`src/scenarios/financial_qa/result_registry.py`](../../src/scenarios/financial_qa/result_registry.py) 保存实际 API、selection、字段覆盖、行数、依赖、`result_ref` 和 step evidence，具备目标—结果核验的事实基础。
- [`src/services/session_variable_store_service.py`](../../src/services/session_variable_store_service.py) 将完整结果与模型可见的小样本分离，并按 session/owner 隔离。
- [`src/scenarios/financial_qa/query_recovery.py`](../../src/scenarios/financial_qa/query_recovery.py) 已区分 Provider 原请求重试、validation 定向修复、歧义候选和空结果，避免无边界盲目重试。
- [`src/scenarios/financial_qa/business_skills.py`](../../src/scenarios/financial_qa/business_skills.py) 已采用内容寻址、不可变快照和显式 reload，可作为经验候选发布机制的现有模式。
- [`src/services/finance_claude_session_service.py`](../../src/services/finance_claude_session_service.py) 已关闭自动 memory，并记录调用、耗时、重试、Skill、usage 和结果，具备安全的学习起点。

因此，本任务不建议新造一个独立的“自学习 Agent Runtime”，而应补强现有 Catalog、evidence、recovery、Skill snapshot 和 eval 链路。

## 二、代码审计中的确定性问题

### FD-P0-01 模型 Catalog 与执行 Catalog 双源漂移

模型侧读取：

- [`src/tools/finance_data/catalog/api_view_catalog.json`](../../src/tools/finance_data/catalog/api_view_catalog.json)
- [`src/services/finance_data_tool_catalog_service.py`](../../src/services/finance_data_tool_catalog_service.py)

Validator 和 Provider 路由侧读取：

- [`src/experiments/staged_data_protocol/phase2/catalog.py`](../../src/experiments/staged_data_protocol/phase2/catalog.py)
- [`src/experiments/staged_data_protocol/phase2/call_validator.py`](../../src/experiments/staged_data_protocol/phase2/call_validator.py)
- [`src/experiments/staged_data_protocol/phase2/api_runner.py`](../../src/experiments/staged_data_protocol/phase2/api_runner.py)

只读比较发现：两边均覆盖 7 个 subject、约 31 个 dataview，名称大体一致，但至少有 5 个 dataview 字段集合不同、7 个 dataview 的 KD 能力结构不同；Validator 还存在模型 Catalog 未表达的 fallback。当前未发现共享生成源或契约 parity/drift 测试。

影响：模型可能按照自己看到的说明正确调用，却被运行时按另一份契约校验或执行；后续如果只自动更新 JSON Catalog，甚至可能扩大漂移。

目标：建立一个版本化的可执行语义源，由它生成或约束模型视图、Validator 契约和 Provider 路由。CI 至少保证：

```text
model-visible capability
  == validator-accepted capability
  == provider-executable capability
```

### FD-P0-02 median 能力声明与实现不一致

以下 Provider 中，`median` 当前实际映射为 `AVG`：

- [`src/experiments/staged_data_protocol/phase2/quote_provider.py`](../../src/experiments/staged_data_protocol/phase2/quote_provider.py)
- [`src/experiments/staged_data_protocol/phase2/intraday_quote_provider.py`](../../src/experiments/staged_data_protocol/phase2/intraday_quote_provider.py)

Catalog 却声明支持 median。这是确定性的工具语义错误，不能依靠提示词或 Loop 修复。应实现真正中位数，或者撤销该能力声明，并用明显偏态的数据构造协议级测试，确保 AVG 与 median 可以被区分。

### FD-P0-03 涨跌停规则已经下沉，但存在重复和口径分叉

较新的实现位于：

- [`src/tools/realtime_market_ranking_tool.py`](../../src/tools/realtime_market_ranking_tool.py)
- [`src/tools/limit_board_list_tool.py`](../../src/tools/limit_board_list_tool.py)

其中已经考虑 ST 5%、创业板/科创板 20%、北交所 30%、其他通常 10%。但相同逻辑在 [`src/tools/market_snapshot_tools.py`](../../src/tools/market_snapshot_tools.py) 重复，旧的 [`src/market_info/market_info.py`](../../src/market_info/market_info.py) 在 ST 和北交所口径上存在差异，且未发现覆盖完整规则、生效日期和边界价格的直接单元测试。

这说明“业务复杂性下沉”方向正确，但实现方式应是统一规则内核，而不是继续增加互不一致的 `is_xxx` 微工具。建议所有行情、涨停池、连板和市场宽度能力复用同一个带生效日期的市场政策组件，并返回：

- 计算出的涨跌停价格；
- `is_limit_up` / `is_limit_down`；
- 参考价格和证券身份依据；
- `rule_version` 与 `as_of`；
- 必要的计算 trace。

“是否涨停”属于确定性数据语义；“为何涨停、封板质量和持续性如何”仍属于 Skill/模型分析。

### FD-P1-01 金融查询约束存在多处重复

相近规则目前分散在：

- [`src/scenarios/financial_qa/system.md`](../../src/scenarios/financial_qa/system.md)
- [`src/scenarios/financial_qa/data_query.md`](../../src/scenarios/financial_qa/data_query.md)
- [`src/scenarios/financial_qa/finance_api_protocol.md`](../../src/scenarios/financial_qa/finance_api_protocol.md)
- [`src/scenarios/financial_qa/tools.py`](../../src/scenarios/financial_qa/tools.py)

会话组装时又会拼接基础提示、协议、数据查询说明和 Skill 路由摘要。重复内容容易造成版本不一致，也增加常驻上下文。后续应明确基础不变量、API 局部知识、业务方法和恢复护栏的归属，按需加载而非长期平铺。

### FD-P1-02 轨迹已有，但缺少“纠正—成功”闭环

当前日志保存了运行时长、工具调用、重试、Skill、usage、最终答案或错误，但尚未形成完整的：

```text
用户问题 / 本轮目标
  → 执行轨迹与版本
  → 最终答案
  → 后续用户纠正
  → 修复后的可验证结果
```

用户纠正主要停留在对话语义中，无法稳定用于离线聚类、回放和经验候选生成。现有日志中正式答案的观测副本还会截取到有限长度，因此不能直接充当学习数据权威。

## 三、目标架构：三层运行结构加独立学习平面

| 层级 | 应承担 | 不应承担 |
|---|---|---|
| 数据接口/语义层 | 交易日、证券身份、复权、单位、报告期、涨跌停、确定性派生指标、Provider 差异 | 主观投资判断、用户表达、长篇分析方法 |
| Catalog/按需知识 | 字段、参数、默认值、时间和单位口径、Provider 限制、高频本地使用说明 | 复制底层计算规则、全部金融常识、长期超长提示 |
| Agent Loop | 目标与结果匹配、证据覆盖、定向修复、停止或询问 | 重新实现市场规则、凭模型猜测确定性数据 |
| 学习平面 | 轨迹、候选、聚类、回放、发布、回滚 | 在线直接改写全局 prompt/Catalog/Provider |

### 3.1 数据语义层：把确定性复杂性下沉，但保持体系化

适合下沉的内容应同时具备高确定性、可测试、跨任务复用和结果敏感性，例如：

- 市场交易日与时区；
- 证券代码、市场和板块身份；
- 前复权/后复权及基准价格；
- 财报报告期、公告期、单季/累计口径；
- 单位、币种、比例缩放；
- 涨跌停和其他带生效日期的交易制度；
- Provider 的字段映射、空值和精度语义。

不要为每个业务谓词建立独立碎片化工具。优先在相关 dataview 中提供标准派生字段或语义算子；只有当某能力具有独立输入、成本或生命周期时，才新增独立 dataview。

数据层返回的不只是结果，还应带最小充分的 provenance，使 Loop 可以核验而不必重算业务规则。

### 3.2 Catalog 与按需知识：只补基模真正缺失的局部知识

基础提示词只保留跨任务不变量：

- 解析对象、指标、时间、粒度和关系；
- Catalog 是当前可执行能力的权威入口；
- 不虚构数据、字段或工具结果；
- 关键结论必须有 result/evidence 支持；
- 证据充分后停止调用。

普通、稳定的金融概念先依赖基模。只有经评测证明高频出错的本地规则、Provider 特性、用户偏好和查询配方才进入额外知识。

推荐加载流程：

```text
market / asset / provider / subject / dataview
  / catalog_revision / error_signature / owner
                    ↓ 确定性过滤
                语义相关性排序
                    ↓
              只加载 Top 1～3 条
```

知识卡必须绑定适用范围、版本、来源、owner 和可选失效时间。不能只靠 embedding 命中，也不能把一次用户纠正直接复制进 system prompt，以防误归纳、隐私泄露和提示注入。

### 3.3 Agent Loop：基于证据的有界检查

Loop 应复用现有自然语言 `goal` 和实际 evidence，不需要先扩张成大型 QueryIntent Schema。每次查询后的低成本检查包括：

1. 实际数据对象、字段、时间范围、粒度和单位是否覆盖目标；
2. `selection_applied` 是否发生了模型未意识到的默认选择、截断或排序；
3. 输出列、非空覆盖和结果完整性是否足以回答；
4. 关键事实是否可以回指 `result_ref`；
5. 是否重复了相同的规范化查询而没有产生新证据。

独立语义 critique 不必在每个普通成功查询后运行，仅在以下情况触发：

- validation/provider 错误；
- 高风险或多步结论；
- 用户明确纠正；
- 调用或修复次数异常；
- 相同错误或查询指纹重复；
- 目标与结果存在可观察缺口。

Loop 决策保持简单：直接回答；携带明确 mismatch 做一次定向修复；或者询问/停止。Provider 原请求只重试一次，validation 只做有限语义修复；零行、空值本身属于 evidence，不能作为随意换口径的理由。

现有全局最大轮数和单次查询步数只是粗粒度保护，后续更需要 per-goal、重复指纹和 no-progress 预算，而不是继续增大全局轮数。

## 四、学习闭环设计

### 4.1 Experience Trace：系统保存事实

学习平面的 HARD 部分只固化后续必须寻址和验证的事实，不要求模型重复生成系统已有内容。建议最小覆盖：

- trace、owner、thread、turn 标识；
- 原始问题和本轮完整自然语言目标；
- 模型、Provider、Catalog、Skill、市场规则和 checker revision；
- 调用、validation/provider 失败、step evidence 与 result refs；
- 最终答案引用、耗时、token、调用次数；
- 后续用户纠正与修复 trace 的关联。

具体业务解释仍保留为自然语言资产，不把每一种金融意图拆成多层枚举和 JSON 树。

### 4.2 候选触发：只使用强信号

可以自动生成候选的信号包括：

- 用户明确指出错误和正确口径；
- 失败后修改查询并取得可验证成功；
- 同一目标多次改变 API/参数后才成功；
- 相同错误特征跨 Case 重复；
- 确定性 checker 发现目标—证据偏差。

没有用户投诉、模型自称成功、工具返回 HTTP 200，都不能单独作为成功标签。

### 4.3 经验候选：模型只输出本轮贡献

Agent 可以总结：问题模式、适用范围、正确行为、evidence refs、建议归属。它输出的是候选，不是已经生效的规则。

自动路由建议：

| 观察到的问题 | 候选去向 |
|---|---|
| 查询正确但工具结果错误 | Provider/API 缺陷或语义字段 |
| dataview、字段、参数选择错误 | Catalog 局部说明或 failure guard |
| 可复用的多步查询顺序 | 查询 Workflow/Recipe |
| 数据正确但最终结论无证据或过度推断 | Loop checker/repair 指导 |
| 明确个人偏好 | owner-scoped memory |
| 单次、歧义、证据不足 | 仅保留 trace，不发布 |

### 4.4 Promotion Gate：验证后才进入运行时

候选需要去重、聚类、绑定版本，并回放：

- 原始失败 Case；
- 相似 Case；
- 反例；
- 当前回归测试集。

比较正确率、证据支持率、用户纠正率、调用/轮数、耗时、token 和错误干预率。通过后才形成内容寻址的不可变快照，支持 shadow/canary、回滚和版本追踪。

可以复用现有 Skill candidate/active snapshot 的设计原则，避免创建另一套不兼容的发布模型。现有 [`src/services/skill_candidate_store_service.py`](../../src/services/skill_candidate_store_service.py) 可作为候选与正式版本分离的参考，但金融接口和全局提示仍需独立权限与评测门。

### 4.5 三种学习速度

1. **本轮学习**：自动、立即，根据当前 evidence 进行一次有界修复。
2. **用户级学习**：用户明确纠正后，可生成 owner-only、可撤销、可过期的经验候选或临时绑定。
3. **全局学习**：自动收集与总结候选，但必须经过回放、评测和发布流程；生产 API 与基础提示词不得 live self-modify。

对应项目原则：

```text
用户反馈语义（SOFT）
  → trace/ref/revision/owner（HARD）
  → lesson candidate（SOFT）
  → published snapshot binding（HARD）
  → 下一轮定向 guidance（SOFT）
```

## 五、评测与观测

现有 [`tests/evals/finance_data_chat_v1.json`](../../tests/evals/finance_data_chat_v1.json) 只有少量代表性 Case，可以作为种子，但不足以承担自动发布门。

建议分四类指标：

| 维度 | 指标示例 |
|---|---|
| 正确性 | 目标覆盖率、证据支持率、字段/时间/单位正确率、用户纠正率 |
| 效率 | Catalog 读取、查询调用、重试、总轮数、延迟、token |
| 稳定性 | 错误修复率、无效 critique、回归率、回滚率 |
| 学习质量 | 候选准确率、重复候选压缩率、发布胜率、跨 Case 泛化率 |

每个待发布候选至少需要“原始 Case＋相似 Case＋反例＋现有回归”四组 evidence。基模升级时还应运行最小提示基线：若新模型在不加规则的情况下已经稳定解决某类问题，就不应继续永久注入该规则。

## 六、外部 Agent 经验的适用边界

- [Reflexion](https://arxiv.org/abs/2303.11366) 使用自然语言反思和 episodic memory，适合本轮或短期修复；反思本身不能直接成为生产事实。
- [CRITIC](https://arxiv.org/abs/2305.11738) 通过外部工具反馈进行批判与修正，支持本方案以真实执行 evidence 而非模型自我感觉作为 Loop 基础。
- [ExpeL](https://ojs.aaai.org/index.php/AAAI/article/view/29936) 从成功和失败轨迹中提取经验并在推理时检索，最接近“经验候选＋按需加载”。
- [Voyager](https://arxiv.org/abs/2305.16291) 只将经过环境验证的能力加入 Skill Library，对应本方案的“先回放验证，再发布快照”。
- [Agent Workflow Memory](https://arxiv.org/abs/2409.07429) 归纳和检索可复用工作流，适合金融多步查询配方，而不是全局提示词堆叠。
- [GEPA](https://arxiv.org/abs/2507.19457) 从系统轨迹中产生、评估并选择提示候选，适合作为离线 prompt 优化方法，不适合在线不断追加 system prompt。

这些实现的共同价值不是允许 Agent 随时改写自己，而是保存经验、验证经验、选择性检索并版本化发布。

## 七、建议任务顺序与验收重点

### P0：先恢复接口语义真实性

1. **FD-P0-01 单一 Catalog 权威源**
   - 模型视图、Validator 与 Provider 从同一 versioned source 生成或校验。
   - 增加 subject/dataview/field/KD/default/provider route parity tests。
   - 任一侧新增或删除能力而另一侧未同步时 CI 失败。

2. **FD-P0-02 修复 median**
   - 真正实现 median，或删除错误能力声明。
   - 使用偏态数据验证 median 与 AVG 不相等。

3. **FD-P0-03 集中市场规则**
   - 涨跌停规则只有一个生产实现。
   - 支持证券身份、生效日期、ST、不同板块和精度边界测试。
   - 所有调用者共享 rule version 和 provenance。

### P1：先观测，再调整运行行为

4. **FD-P1-04 Consolidated Experience Trace**
   - 关联问题、目标、版本、调用、结果、用户纠正和修复结果。
   - owner 隔离；正式答案和 evidence 使用引用，不依赖截断日志。
   - 暂不自动改变运行时行为。

5. **FD-P1-05 提示分层与按需加载**
   - 去除 system/protocol/tool description 中的重复规则。
   - API 局部语义随 dataview 精确加载一次。
   - 高频本地知识卡绑定 scope/revision/owner/expiry，默认只加载 Top 1～3。

6. **FD-P1-06 有界目标—证据检查**
   - 增加 query fingerprint、no-progress 和 per-goal budget。
   - 普通成功查询不额外 critique；触发式 Case 才进入检查。
   - 错误修复必须指出具体 mismatch，并有停止条件。

### P2：建立受控学习闭环

7. **FD-P2-07 Candidate Miner 与路由**
   - 只接受强信号，自动生成自然语言候选和 evidence refs。
   - 能区分 Provider 缺陷、Catalog 说明、查询配方、Loop 规则和用户偏好。

8. **FD-P2-08 Replay / Promotion / Rollback**
   - 候选去重、聚类、回放、反例测试和回归测试。
   - 支持 shadow/canary、不可变发布和回滚。
   - 初期全局候选必须人工确认；只有低风险且有重复证据的知识卡，后续才考虑自动晋级。

9. **FD-P2-09 离线 Prompt 优化**
   - 在经验数据量和 Eval Gate 足够后，评估 GEPA/DSPy 类候选优化。
   - 只发布经过当前模型、Catalog 和回归集验证的版本。

推荐执行顺序：

```text
Catalog 单源与 Provider 真实性
  → 市场规则集中
  → Experience Trace
  → 提示分层和按需知识
  → 有界目标—证据 Loop
  → 候选生成与路由
  → 回放、灰度发布和回滚
  → 离线 Prompt 优化
```

## 八、明确边界

- 本报告是代码审计与初步架构方案，不宣称上述 P0～P2 已完成。
- 不为学习闭环新增庞大业务意图 Schema 或多套并行状态机。
- 不把一次用户纠正自动升级为全局金融事实。
- 不用提示词掩盖 Provider 的确定性错误，也不用 Loop 重算底层市场规则。
- 不把可观察性字段混入正式业务回答；trace 和 evidence 继续走独立通道。
- 所有 HARD 扩展都必须证明是跨模块衔接不可替代的事实，并补最小协议测试。
