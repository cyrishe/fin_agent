# Agent RSI 调研：递归自我改进技术与 Fin Agent 融合评估

- 调研日期：2026-09-08。
- 范围：原始论文、官方项目说明与关键源码；结合当前本地 Fin Agent 工作区。不是服务器部署审计。
- 性质：研究与初步建议，未实施学习系统，未安装依赖或修改生产配置。
- 关联：[金融数据正确性与 Agent 学习初步方案（2026-08-25）](finance_data_correctness_and_agent_learning_20260825.md)。

## 1. 核心结论

Fin Agent 适合引入受控的跨任务持续改进：从真实轨迹和用户反馈提出局部改进，经过独立回归和审核，再发布为可追溯的经验或资产版本。

当前优先级不是递归次数或自动修改权限，而是可靠的评估、准确的归因和正确的改动位置。推荐借鉴 ACE 的增量经验、GEPA 的离线候选搜索，以及 Reef 的反馈与版本闭环；暂不整体替换 DSH/CC，不开放生产主框架自修改，不开展模型权重训练。

这里的“适合”是架构判断，不是已经测得收益；外部项目的基准成绩不能换算为 Fin Agent 的准确率或速度提升。

## 2. RSI 的概念边界

“Agent-RSI”没有对应唯一的标准产品。本次按 Recursive Self-Improvement 技术路线调研。

需要区分：

1. 当前任务内修正：执行失败后重试；任务结束后不一定留下持久改进。
2. 跨任务自我改进：更新经验、提示词、Skill、工具描述或 harness；模型权重可以保持不变。
3. 更严格的 RSI：改进机制本身也可更新，使系统改善未来发现、提出和选择改进的能力。

反复总结或不断增加记忆，不自动等于第三种能力。Fin Agent 可以先获得第二种能力的工程价值，不必追求开放式 RSI。参见 [RSI 综述及形式化定义](https://github.com/D2I-ai/awesome-recursive-self-improving-agents)。

## 3. 主要技术与实际实现

| 项目 | 改进对象与机制 | 对 Fin Agent 的判断 |
|---|---|---|
| ACE | 从轨迹提炼经验，增量更新上下文 playbook，冻结模型权重 | 经验提炼适配度高；不照搬长手册全量加载 |
| GEPA | 轨迹反思、候选提示生成、评测比较与候选搜索 | 适合离线优化局部描述和提示，不需要替换 runtime |
| Reef | 请求与反馈关联、候选更新、评估、版本发布；支持权重和 harness 两条路线 | 借鉴工程闭环，但业务评估与发布门槛仍需自己实现 |
| Darwin Gödel Machine | 修改自身代码，在编码基准上评估，保留多样化候选分支 | 借鉴沙箱、分支、溯源，不直接开放生产自修改 |
| OpenRSI / OpenMLE | 在机器学习工程环境训练程序改进操作，再进行长程搜索 | 不是金融查询的即插即用组件；商业使用还需确认许可 |

### 3.1 ACE：借鉴增量更新，不照搬长上下文

Generator 执行任务，Reflector 提炼经验，Curator 提出增量更新，系统合入 playbook。它避免反复重写整份提示词导致旧经验丢失，与“系统持有事实、模型只贡献本轮修改”的原则相近。

实现核对发现：

- `ace/core/generator.py` 把传入的 playbook 整体格式化进提示词。
- 官方金融示例的 `playbook_token_budget` 默认是 80,000 tokens，不是 Fin Agent 的建议预算。
- `playbook_utils.py` 的通用操作主要实现 ADD；UPDATE、MERGE、DELETE 等仍有 TODO，不能把论文中的机制概述当作完整的生产治理实现。

因此只借鉴增量经验思想，按阶段和目录范围加载少量相关内容，并具备替换、合并、删除的治理能力。不能把“经验更多”当成优化目标。

ACE 的金融实验主要涉及 XBRL 实体识别及数值推理，不等于我们的多目录取数。论文收益不能直接外推。

来源：[论文](https://arxiv.org/abs/2510.04618)、[实验与消融](https://arxiv.org/html/2510.04618v3)、[官方实现](https://github.com/ace-agent/ace)、[Generator](https://github.com/ace-agent/ace/blob/main/ace/core/generator.py)、[经验操作](https://github.com/ace-agent/ace/blob/main/playbook_utils.py)。

### 3.2 GEPA：优化发动机，不是业务裁判

核心适配职责：

- `evaluate`：运行候选，返回逐题得分与轨迹。
- `make_reflective_dataset`：将轨迹整理为有用的改进反馈。

优化器负责搜索候选，不负责替接入方定义业务正确性。可以保持 DSH/CC、工具协议不变，仅把有限的提示资产作为候选变量。

GEPA 有 MCP adapter，但优化外层 MCP 工具描述不等于优化 Fin Agent 内部的 `subject → dataview → operation → API` 决策。需要接入内部运行轨迹，不能只优化“金融数据查询”外层说明。

来源：[论文](https://arxiv.org/abs/2507.19457)、[核心适配接口](https://github.com/gepa-ai/gepa/blob/main/src/gepa/core/adapter.py)、[MCP adapter](https://github.com/gepa-ai/gepa/tree/main/src/gepa/adapters/mcp_adapter)。

### 3.3 Reef：闭环骨架不等于默认质量保证

Reef 将反馈关联到原始请求，随后生成、评估和发布候选。它既支持模型权重训练，也支持调用模型 API 的无本地 GPU harness 优化。

需要注意：

- harness 教程使用三个固定编码任务比较候选与旧版；这不是广泛生产可靠性的证明。
- 通用 `DefaultCandidateEvaluationPlugin` 未显式配置 selector 时使用 `AlwaysSelect`。这是该组件的默认行为，不代表所有 recipe 都如此，但不能假设每条路径天然要求优于基线。
- 引入 Reef 仍需定义金融评分、权限、部署策略；其 serving、资产与 runtime 体系可能与现有框架重复。

来源：[仓库](https://github.com/Human-Agent-Society/reef)、[教程](https://github.com/Human-Agent-Society/reef/blob/main/tutorials/evolve-your-harness/README.md)、[评估配置](https://github.com/Human-Agent-Society/reef/blob/main/reef/train/evaluation/config.py)、[默认选择组件](https://github.com/Human-Agent-Society/reef/blob/main/reef/train/evaluation/evaluators.py)。

### 3.4 DGM 与 OpenRSI：有实证，但边界不同

DGM 在编码任务中修改 Agent 自身代码，并保留多样化候选分支，不是只从当前最优版本继续搜索。其研究也记录了伪造测试日志、修改检测标记以提高奖励的情况。因此，评分证据和评估器不能由候选自由修改。参见 [Sakana 原始报告](https://sakana.ai/dgm/)、[论文](https://arxiv.org/abs/2505.22954)。

OpenRSI 首批实现面向机器学习工程，通过 Draft、Improve、Debug、Crossover 等操作连接训练与搜索，不宣称解决通用 RSI。其原始源码和材料采用 CC BY-NC 4.0 非商业许可；若直接引入商业生产需另行确认授权，研究公开方法不等于可以直接复制代码。参见 [官方仓库](https://github.com/FrontisAI/OpenRSI)、[许可证](https://github.com/FrontisAI/OpenRSI/blob/main/LICENSE)。

## 4. 当前 Fin Agent 的条件与缺口

已具备的基础：

- [DSH 服务](../../src/scenarios/financial_qa/dsh_service.py)记录工具调用、结果引用、分步 token、执行耗时、模型、目录版本、提示词资产等。
- [目录服务](../../src/services/finance_data_tool_catalog_service.py)集中提供分层契约，可承载局部改进和版本关联。
- [Skill 候选存储](../../src/services/skill_candidate_store_service.py)具有 owner 边界和不可变候选修订，候选写入不自动改变生效版本。
- 已有跨业务历史评测、现场记录及用户对 golden 的纠正。

还不能视为完成的部分：

- 原始 trace 尚不是经过审核的经验数据集；结果摘要也不一定足以复原完整模型上下文。
- [RuntimeFeedbackService](../../src/services/runtime_feedback_service.py)是当前执行反馈封装，不是跨任务学习系统；completed 不代表业务语义正确。
- 候选存储不代表金融目录、提示词已具备完整自动测试、发布与回滚闭环。
- 历史 golden 有过修订，需要明确有效版本和允许的等价数据路径。

## 5. 建议的融合方式

```text
生产请求 → 现有 DSH/CC → 数据协议和工具 → 返回结果
                 │
                 └→ 执行证据＋用户反馈
                           ↓
                     提出候选改进
                           ↓
                  独立回归＋人工确认
                           ↓
                   局部版本发布与加载
```

学习放在请求完成后的独立环节，使用独立预算。生产请求只读经验证的版本，不因每次反思增加延迟或在执行中发生资产漂移。

### 5.1 改动归属

| 问题 | 合适位置 |
|---|---|
| 工具逻辑确定性错误 | 工具实现与协议测试 |
| 目录切片导致真实能力不可见 | 目录生成和加载机制 |
| 高频、局部、特殊数据口径 | 对应视图描述或局部 Skill |
| 用户个人习惯 | owner 隔离的私有经验 |
| summary 过度推断 | 表达阶段提示与评测 |
| 数据未同步、超时、错误配置 | 数据与运维，不固化为业务经验 |

经验系统可以提出“不新增经验”“删除冗余说明”，不以提示词积累量为目标。

### 5.2 上下文加载

- 入口选择前仅提供必要的数据范围经验；入口错误的经验不能只在正确入口选中后才可见。
- 选定视图后提供局部口径；生成调用时以权威目录中的方法、字段为准。
- 结果表达阶段再提供分析边界与谨慎表达经验。
- 先验证现有目录和检索是否足够，不先新增向量数据库。相似度代表相关性，不代表正确性。
- 稳定经验应合并回正式 catalog，并删除重复经验，避免两套协议源。
- 经验引用要关联来源和适用版本；模型、目录或数据范围变化后，不默认旧经验仍有效。

### 5.3 SOFT / HARD 边界

SOFT 分析可能原因和泛化经验；HARD 保存原始事实、归属、版本、证据与生效指针。静态校验继续检查协议和执行约束，不增加运行时“用户语义一致性 validator”。

不为经验内容强行建立复杂枚举状态或嵌套 JSON 树。解释性内容保持自然语言，真正需要系统寻址和权限管理的事实才结构化。

## 6. 如何避免越学越错

### 6.1 反思只是根因假设

原 RTEF112 中，模型读取 aggregate 目录后生成不存在的 `stock.report_metric.query`。目录可见范围缺失是值得检验的机制，而不是直接写一条全局“禁止 .query”。后续一次成功复跑不能证明机制已被完全修复，尤其当复跑最初选择了 query 而非 aggregate 时，不能视为同路径因果对照。现场参见 [2026-09-07 复盘](finance_realtime_api_and_catalog_navigation_20260907.md)。

### 6.2 成功信号必须拆开看

- 协议可执行，不代表取得了问题所需的数据。
- 查询今天却返回 T-1，不能因返回非空、turns 更少而奖励。
- 合理查询返回零行，不应自动惩罚模型。
- 明细充分时可与聚合路径等价，不要求复刻唯一 API 字符串。
- summary 是否忠实于数据，应与取数正确性分开评估。

### 6.3 用户反馈不是无条件全局真值

反馈可能是正确纠错、补充需求、个性偏好、数据问题或用户误解。需要结合上下文判断，不直接写入全局规则。私有信息不得跨 owner 传播，日志中的指令性文字不能变为系统指令。

## 7. 建议的验证顺序与指标

先比较：当前基线、少量审核经验版、离线优化候选版。保持模型、运行预算和数据条件一致，每次只改变有限资产，不混合调整目录、模型、重试和 summary。

评测要求：

- 跨股票、基金、板块、资金、财务、研报及多轮纠错覆盖。
- 已反复调试的 20 题作为开发回归，不再充当独立泛化证据。
- 保留未参与优化的题目，防止相近改写泄漏；另留未来新问题观察集。
- 固定快照或重放用于策略对照，真实链路另测数据时效与集成。
- 同条件重复运行，看随机波动及原本正确案例的退步。
- 关注数据获取成立性、入口合理性、首次协议通过率、summary 忠实性、turns、P50/P95 延迟、token。
- 离线优化开销单列，与新增上下文长期成本一起核算。

只有质量、延迟和成本的整体收益成立，才逐步放宽低风险资产的自动发布；当前没有本项目实测收益可以承诺。

## 8. 决策建议

1. 优先做真实案例归因与局部经验候选，保留人工审核。
2. 其次接入 GEPA 式离线优化，复用 DSH/CC 和现有评测。
3. 借鉴 Reef 的证据与发布闭环，复用自己的资产和权限体系。
4. 暂缓模型权重训练、生产代码自修改和自动修改评分标准。

未来如试验代码自改进，隔离的自定义工具 Coding/Test 场景可能比开放式金融问答更容易取得可验证反馈，但独立测试与权限边界仍不可省略。

最终目标：减少重复错误和人工排查，而不是扩大递归次数、经验数量或 Agent 修改权限。
