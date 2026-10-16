# Agent Surface Protocol v1 思想实验

状态：协议压力测试，不构成协议修改。

目标：先模拟真实金融 Agent 对话、运行和异常路径，判断 `agent_surface.v1` 是否能自然表达，以及基于它能否形成清晰的界面状态。只有核心场景表达稳定、主要缺口解决、灰盒原型效果基本满意后，才进入正式视觉设计和运行时代码改造。

## 1. 实验方法

每个场景依次模拟五层：

```text
用户输入与上下文
→ 模型可能输出的 model.surface_draft
→ 运行时补齐后的 canonical blocks
→ 流式事件和状态迁移
→ 用户实际看到的关键界面状态
```

实验刻意覆盖：

- 不需要工具的轻量回答；
- 一次工具调用和多工具并行；
- 正常完成、部分完成、失败、重试和改计划；
- 澄清、审批、认证、取消与恢复；
- 复杂 Artifact 的设计、修改、验证和发布；
- 金融数据时点、口径、来源、估算和模拟边界；
- 断线、重放、上下文插队和海量数据。

当前协议在实验期间冻结。表达困难时先记录，禁止临时通过增加 block kind 解决。

## 2. 评分标准

每项 0-2 分，总分 14 分：

| 维度 | 0 分 | 1 分 | 2 分 |
|---|---|---|---|
| 语义自然度 | 无法表达 | 需要绕用其他类型 | 直接、清晰表达 |
| 模型负担 | 需要模型生成运行时/UI 字段 | 需要较多结构约束 | 模型合同简洁 |
| 金融可追溯 | 缺失关键口径 | 依赖约定或 annotations | 来源、时点、口径完整 |
| 流式与恢复 | 无法连续更新 | 能更新但恢复不完整 | 可增量、重放和恢复 |
| 交互与权限 | action 不安全或无法表达 | 需要外部约定 | 意图、授权和恢复明确 |
| 渲染与降级 | 绑定单一控件 | 需要定制分支 | 可选择 renderer 且有降级 |
| 非技术用户体验 | 暴露内部实现 | 需要较多折叠 | 默认只呈现业务含义 |

结论阈值：

- 12-14：通过；
- 9-11：有条件通过，进入原型前需要补齐协议或配套合同；
- 0-8：不通过，说明核心流程存在结构缺口。

## 3. E01 轻量金融知识问答

### 用户输入

> 市盈率 TTM 和静态市盈率有什么区别？不用查数据。

### 模型草稿

```json
{
  "object_type": "model.surface_draft",
  "protocol_version": "agent_surface.v1",
  "sections": [
    {
      "role": "primary",
      "blocks": [
        {
          "kind": "narrative",
          "semantic": "finance.concept_explanation",
          "payload": {
            "format": "markdown",
            "text": "**静态市盈率**使用最近一个完整会计年度的归母净利润；**TTM 市盈率**使用最近连续十二个月的归母净利润。公司利润变化较快时，TTM 通常更接近当前盈利状态，但两者都可能受一次性损益影响。"
          }
        }
      ]
    }
  ]
}
```

### 关键界面状态

1. 用户消息正常显示。
2. 助手文字逐字流出。
3. 不显示计划、任务条、Artifact 或“正在调用 Agent”。
4. Run 生命周期存在于后台，但完成后界面仍然是一条普通回答。

### 结论

- 七类 block 不会强迫简单问答复杂化。
- `narrative` 足够，Section 只需要 `primary`。
- 具体实现必须允许前端隐藏单步 Run 的 process UI。

评分：**14/14，通过**。

## 4. E02 单次行情数据查询

### 用户输入

> 看一下沪深 300 截至 2026-07-10 最近 60 个交易日的走势和最大回撤。

### 运行条件

- 使用历史指数行情工具；
- 数据截至 2026-07-10 收盘；
- 指数点位不存在股票复权问题；
- 工具返回 60 条日线和最大回撤区间。

### 可能的 canonical blocks

```text
primary
  narrative: “近 60 个交易日整体震荡上行，期间最大回撤为样例值 X%。”
evidence
  data/timeseries/finance.index_ohlc
  data/record/finance.drawdown_summary
  resource: 历史行情工具结果
```

`data` block 的横切上下文：

```json
{
  "namespace": "finance",
  "as_of": "2026-07-10T15:00:00+08:00",
  "timezone": "Asia/Shanghai",
  "markets": ["CN"],
  "instruments": [{"id": "000300.SH", "asset_type": "index", "market": "SSE"}],
  "currency": "CNY",
  "unit": "index_point",
  "frequency": "1d",
  "data_mode": "historical",
  "calendar": "SSE"
}
```

### 流式过程

1. `run.started`。
2. 创建一个简短 workflow item：“读取历史行情”。
3. 数据到达后创建 timeseries block；Renderer 可以先显示骨架，再显示 K 线或折线。
4. drawdown record 完成后更新结论 narrative。
5. `run.finished`。

### 关键界面状态

- 默认先显示结论和走势图；
- 数据口径以紧凑标签或详情展示，不塞进正文；
- 客户端不支持图表时降级成 60 行时间序列表格；
- 工具原始 JSON 不进入主界面。

### 结论

`data.shape + content_type + finance context` 足以表达，不需要 `kline` 顶层 block。

评分：**14/14，通过**。

## 5. E03 多工具股票比较，包含部分数据缺失

### 用户输入

> 比较宁德时代和比亚迪的估值、增长和近一年股价表现。只做差异分析，不要给买卖建议。

### 运行条件

- 估值、财务和历史行情三个工具并行；
- 两只股票行情完整；
- 其中一只股票最新季度增长字段缺失；
- 估值数据为延迟数据，财务数据为已披露定期报告。

### 模拟工作流

```text
读取估值       succeeded
读取财务指标   partial
读取一年行情   succeeded
统一口径       succeeded
形成差异结论   succeeded
```

### 模拟最终输出

```text
primary/narrative
  “两家公司当前估值、已披露增长和一年价格表现存在以下差异……本回答不构成买卖建议。”

evidence/data records
  两家公司估值与增长对比表

evidence/data timeseries
  归一化价格曲线

evidence/assessment
  overall=warn
  issue=finance.latest_quarter_field_missing
  issue=finance.valuation_data_delayed
```

### 分支验证

- 缺字段不应让整个 Run 变成 `failed`；Run 可以 `partial` 或成功并携带 assessment warning。
- `workflow` 负责说明哪一步不完整；`assessment` 负责解释这对结论意味着什么。
- “不要给建议”属于接受的约束，应该影响 Narrative 内容，但不需要新增 block。

### 发现的轻微问题

Block 级 `evidence_refs` 可以支撑整个结论，但如果一段话包含多个来源不同的数字，引用粒度略粗。此问题在 E09 集中评估。

评分：**13/14，通过**。

## 6. E04 金融工具设计、实现、失败修复和评审

### 用户输入

> 帮我设计一个市场状态判断工具，普通研究用户能看懂。它要综合趋势、波动和市场宽度。

### 完整状态路径

```text
understanding
  → clarification
  → specification
  → design_validation
  → waiting_user
  → implementation
  → test_failed
  → revision_loop
  → verification
  → waiting_publish_approval
  → completed
```

### 阶段一：需求理解

模型输出一个简短 narrative 和 interaction：

```text
已理解：输出 risk_on / neutral / risk_off，展示主要驱动和风险边界。
待确认：基准指数、观察周期、市场宽度范围、价格复权口径。
```

用户通过结构化表单选择：

```json
{
  "benchmark": "000300.SH",
  "lookback": 60,
  "breadth_universe": "all_a_share",
  "adjustment": "forward"
}
```

### 阶段二：规格设计

主产物不是代码，而是 `artifact/finance.tool_spec`：

```json
{
  "display_name": "市场状态判断",
  "purpose": "结合趋势、波动和市场宽度判断市场环境",
  "inputs": ["benchmark", "lookback", "breadth_universe", "as_of"],
  "outputs": ["regime", "confidence", "drivers", "risk_notes"],
  "regimes": ["risk_on", "neutral", "risk_off"],
  "logic": [
    "计算基准趋势",
    "计算实现波动率",
    "计算上涨和创新高比例",
    "使用可解释规则合成状态"
  ]
}
```

`assessment` 发现：

- 市场宽度必须以 `as_of` 截断，防止前视偏差；
- 停牌和上市不足观察周期的证券需要明确处理；
- 输出 `confidence` 不能伪装成统计概率。

### 阶段三：实现与失败

普通用户默认只看到：

```text
正在实现数据读取和判断逻辑
验证 4/6 通过
2 项需要修正：停牌样本处理、空交易日边界
```

详细代码、命令输出和文件变更进入 diagnostic/artifact details。

Workflow 更新：

```text
生成实现       succeeded
基础样例       succeeded
历史时点检查   failed
修正前视问题   running
重新验证       pending
```

修正完成后，Artifact 从 `draft → reviewable → approved`，并关联 assessment validation refs。

### 关键界面状态

1. 对话为主，需求澄清内联出现。
2. 规格形成后出现可持续编辑的 Artifact 工作区。
3. 实现阶段显示业务进度，而不是 IDE。
4. 失败后在原 workflow 内进入修复循环，不另起一段毫无关联的回答。
5. 最终发布是新的审批点，不等同于“设计确认”。

### 结论

核心 Block/Section 能表达完整流程。真正缺少的是 `finance.tool_spec.v1`、验证结果和 Artifact diff 等业务子 Schema，而不是新的顶层 block。

评分：**12/14，通过，但工具设计原型前必须定义 Artifact 子协议注册方式**。

完整基础事件样例见 `agent_surface_v1_financial_tool_design.json`。

## 7. E05 工具失败、限流和自动改计划

### 用户输入

> 统计过去五年每次降准公告后 5、20 个交易日沪深 300 的表现。

### 运行条件

- 首选事件检索工具遇到限流；
- 运行时等待退避后仍失败；
- Planner 改用央行公告资源检索；
- 行情工具正常；
- 个别公告日为非交易日，需要映射到下一个交易日。

### 事件逻辑

```text
workflow item event_search attempt=1 failed, retryable=true
workflow item event_search_official attempt=2 running
resource blocks 增量加入公告来源
workflow item trading_day_alignment succeeded
assessment issue 提醒“公告日映射为下一交易日”
run.finished succeeded
```

### 用户体验要求

- 主界面显示“事件来源切换为官方公告”，不显示堆栈信息；
- 自动重试不需要弹窗；
- 只有当所有可接受来源都失败时才请求用户决定；
- diagnostic 中可以查看 tool call、attempt、error code 和时间。

### 结论

现有 Workflow 的 `attempt/error/result_refs` 和 Resource/Assessment 可以自然表达。退避、超时和幂等属于 Runtime policy，不应变成 UI block。

评分：**13/14，通过**。

## 8. E06 认证与外部影响审批

该实验包含两个不同性质的暂停点，不能混为一个“确认按钮”。

### E06-A 查询用户券商持仓

用户输入：

> 读取我的券商持仓，分析行业和风格暴露。

运行时发现尚未连接券商，Run 进入：

```text
status=waiting_user
wait_reason=authentication
```

理想 Interaction 需要表达：

- 请求方和身份提供方；
- 完整可检查的授权域名；
- 所需 scope；
- 授权不会向模型暴露密码或 token；
- 外部授权完成通知；
- 手动重试和取消。

当前协议只能把授权 URL 勉强放入一个 `resource`，再用 `interaction/authenticate` 引用其文字说明。Resource 的 `relation` 没有 authorization，Interaction 也没有正式的 `external_flow_ref`。使用 annotations 可以绕过，但不够安全、明确。

结论：**存在核心交互缺口**。

### E06-B 发布工具给团队

用户输入：

> 把刚才验证通过的工具发布给研究团队。

当前 `interaction/approve + decision_context` 可以表达：

```text
effect_summary=发布版本 1.0.0，团队成员可调用
external_effect=true
allowed_scopes=[once]
```

后台签发 action，用户提交 `action_id + resume_token + idempotency_key`。模型无法自行提供“以后都允许”。这一路径没有结构问题。

综合评分：**10/14，有条件通过；发布审批通过，认证外部流程不通过**。

## 9. E07 用户插入临时问题后恢复原任务

### 对话过程

```text
Run A：正在设计市场状态工具
用户：先帮我查一下创业板今天涨幅，然后继续刚才的设计
Run B：完成临时行情查询
Run A：恢复到设计评审步骤
```

项目现有 `InteractionFrameService` 已经保存：

- `active_focus_type/id`
- `suspended_task_id`
- `suspended_task_stack`
- `resume_hint`

但 Surface 协议当前只有共同 `context_id`、各自 `run_id/task_id`，缺少：

- Run A 的 `paused/suspended` 状态；
- Run B 与 Run A 的关系；
- 恢复事件或 `resumes_run_id`；
- 前端应将哪个 Surface 设为 active focus 的稳定投影。

强行使用 `blocked` 会误导用户，使用 `waiting_user/external` 也不准确；放入 annotations 则无法形成通用前端行为。

### 关键界面要求

- Run A 不能显示失败或一直转圈；
- 临时查询应轻量完成，不覆盖工具设计 Artifact；
- 查询完成后，输入框和焦点明确回到 Run A；
- 用户可以选择不恢复或从任务列表手动恢复。

结论：当前协议**不能自然表达多 Run 暂停、插队和恢复关系**。

评分：**8/14，不通过**。

## 10. E08 长任务断线重连

### 运行过程

```text
回测任务执行到 seq=42
浏览器断开
后台继续运行到 seq=87
用户重新打开页面
客户端请求从 seq=42 恢复
服务端发现 replay window 仍在，返回 43..87
或 replay window 已清理，返回新的 surface.snapshot
```

当前服务端事件已经具备：

- `event_id`
- 单调 `seq`
- entity `revision`
- `surface.snapshot`
- `stream.heartbeat`
- ClientCapabilities 的 `supports_resume`

但没有定义客户端恢复请求：

```text
run_id
after_seq
known_surface_revision
accepted_protocol_version
```

也没有明确服务端响应“replayed / snapshot_required / run_gone”的协议。SSE 或 WebSocket 可以自行实现，但不同前端会产生不一致行为。

结论：业务事件层基本正确，缺少一个很小但必要的 **Stream Resume 配套合同**。

评分：**9/14，有条件通过**。

## 11. E09 高风险结论与多来源证据

### 用户输入

> 这家公司大跌后是否值得抄底？请把事实、推断和不确定性分开。

### 理想输出结构

```text
primary/narrative
  先说明无法替代个体化投资建议
  给出条件化判断，而不是单一买卖指令

evidence/data
  价格、估值、盈利预期变化

evidence/resource
  公告、财报、行情来源

evidence/assessment
  facts: 已验证事实
  inference: 基于事实的推断
  uncertainty: 数据缺失、未来假设和反例
```

当前协议能在 Assessment dimension/issue 上挂 `evidence_refs`，也能让整个 Narrative block 引用多个证据。但下面这段话会出现粒度问题：

```text
估值已回到三年分位低位 [来源 A]，
但盈利预测仍在下修 [来源 B/C]，
因此低估值未必代表风险已释放 [推断]。
```

如果整个段落只引用 A/B/C，前端无法稳定知道哪个来源支持哪个 claim。要求模型为每句话拆一个 block 又会显著增加模型负担和界面碎片。

结论：不应增加 `claim` 顶层 block；应在 Narrative/Assessment 的金融扩展中提供可选 `claim_id + text range/anchor + evidence_refs + epistemic_type`。

评分：**11/14，有条件通过，属于金融证据子协议问题**。

## 12. E10 海量数据结果与渐进预览

### 用户输入

> 计算全部 A 股过去 250 个交易日的因子矩阵，并导出完整结果；页面上先给我看前 50 行和覆盖情况。

### 数据规模

- 约 5000 个标的；
- 多列因子和质量标记；
- 完整数据远超 inline payload 上限。

### 理想输出

```text
process/workflow
  universe_resolve → data_fetch → factor_compute → quality_check → export

evidence/data records
  只包含前 50 行预览

evidence/assessment
  覆盖率、缺失率、异常值和停牌处理

artifact/resource
  Parquet/CSV 完整结果、Schema、大小、生成时间和访问控制
```

### 流式过程

- workflow progress 可以按批次更新；
- 预览 block 使用同 ID patch 更新；
- 完整文件只通过 Resource 引用；
- `max_inline_bytes` 控制前端和服务端内联上限；
- 导出失败不会丢失已经完成的质量 Assessment。

### 结论

Data 与 Resource 分离正确，不需要“下载表格”专用 block。需要在 Resource 服务中实现签名 URL、权限和有效期，但这不是 Surface 核心类型问题。

评分：**13/14，通过**。

## 13. 汇总结果

| 场景 | 分数 | 结论 | 主要发现 |
|---|---:|---|---|
| E01 轻量知识问答 | 14 | 通过 | 复杂协议可以不干扰普通对话 |
| E02 单次行情查询 | 14 | 通过 | data shape 与 renderer 解耦成立 |
| E03 多工具比较 | 13 | 通过 | partial workflow + assessment 合理 |
| E04 工具设计闭环 | 12 | 通过 | 缺业务 Artifact 子协议，不缺顶层 block |
| E05 失败与改计划 | 13 | 通过 | 重试属于 Workflow 与 Runtime policy |
| E06 认证和发布审批 | 10 | 有条件 | 发布可表达，安全外部认证流不足 |
| E07 插队与恢复 | 8 | 不通过 | 缺多 Run 暂停、关系和 active focus 投影 |
| E08 断线续传 | 9 | 有条件 | 缺客户端 resume 请求/响应合同 |
| E09 高风险多来源结论 | 11 | 有条件 | 需要可选 claim-evidence 金融扩展 |
| E10 海量数据 | 13 | 通过 | preview + resource 架构成立 |

## 14. 当前判断

### 已验证正确，建议坚持

1. 七类 Block 的抽象层级合适，本轮没有出现必须新增顶层 kind 的场景。
2. Section 按信息作用组织，而不是按页面位置组织，能够覆盖轻量回答和复杂工作区。
3. Workflow 统一 plan、todo、工具调用、验证和修复循环是正确方向。
4. Artifact 必须独立于消息；工具规格、实现和结果可以共享生命周期。
5. Data 的 shape/semantic 与具体图表分离有效，K 线、表格和流程图应继续留在 Renderer 层。
6. Assessment 是金融场景需要的独立业务块，能避免把缺失、质量和风险全部塞进一段提示文字。
7. 模型合同与运行时合同拆分必要，尤其 action、ID、权限、来源和 revision 不能交给模型。

### 原型设计前必须解决

**G1：Run 协调投影**

需要定义暂停/恢复、parent/interrupt/resume 关系和 active focus 如何从现有 InteractionFrame 投影到前端。候选方向是增加 Run relation，而不是增加新 block。

**G2：Stream Resume 配套合同**

需要定义客户端 `after_seq` 请求、服务端 replay/snapshot/run_gone 响应和 replay window 行为。

**G3：安全外部交互**

需要给认证/OAuth/支付等外部流程定义受限 descriptor，包括 provider、完整 URL、resource ref、scope、completion signal 和安全展示规则。

### 工具设计灰盒原型前必须定义

**G4：Artifact 子协议注册方式**

至少需要 `finance.tool_spec.v1`、`finance.validation_report.v1` 和版本/diff 的最小合同。它们是业务子协议，不修改七类核心块。

### 可以作为金融扩展继续验证

**G5：Claim-Evidence Anchor**

给决策相关 Narrative/Assessment 提供可选的 claim 级证据锚点；不要求所有回答逐句结构化。

## 15. 不应采取的修补方式

- 不增加 `auth`、`citation`、`download`、`retry`、`paused_task` 等新顶层 block；
- 不把 InteractionFrame 原样发送到前端；
- 不把 A2UI 组件树作为模型主要输出；
- 不要求模型生成 seq、revision、action_id、resume_token 或完整来源元数据；
- 不为解决 claim 引用而把每句话拆成一个 block；
- 不因为断线恢复问题修改 Narrative/Data 等内容协议。

## 16. 进入正式设计的门槛

只有同时满足以下条件才开始正式视觉设计和工程落地：

1. G1-G3 有经过样例验证的合同；
2. E01-E10 的代表性消息全部通过 Schema 校验；
3. 所有增量场景可从 snapshot 确定性重放；
4. 工具设计场景有最小 Artifact 子协议；
5. 使用真实协议数据制作灰盒关键帧，至少覆盖：空闲、运行、部分失败、等待用户、Artifact 评审、完成；
6. 灰盒评审确认普通问答足够轻，复杂任务足够清晰，技术细节默认不会压过业务内容；
7. 协议修改冻结后再进入视觉系统和生产组件实现。

下一步先编码代表性协议消息并做正向、负向和重放验证，再对 G1-G5 给出最小修正方案。此时仍不制作正式视觉稿。

## 17. 缺口修复的第二轮思想实验

本节只比较候选方案，不修改正式 Schema。

### 17.1 G1：多 Run 暂停、插队和恢复

**方案 A：继续使用 `blocked/waiting_user`**

- 优点：无协议修改。
- 问题：语义错误；用户没有阻塞任务，也没有尚未回答的问题。
- 结论：拒绝。

**方案 B：新增 `paused` 状态，并在 RunState 中增加可选 coordination**

```json
{
  "status": "paused",
  "stage": "review",
  "coordination": {
    "active_focus": false,
    "reason": "user_interrupt",
    "related_run_id": "run_temporary_query",
    "relation": "interrupted_by",
    "resume_mode": "auto_after_related_run"
  }
}
```

临时 Run 完成后，原 Run 更新为：

```json
{
  "status": "running",
  "stage": "review",
  "coordination": {
    "active_focus": true,
    "related_run_id": "run_temporary_query",
    "relation": "resumes_after"
  }
}
```

- 优点：保持 `run.status` 事件，不增加 Block；可以从现有 InteractionFrame 映射；前端行为明确。
- 风险：`paused` 会进入通用 WorkStatus，需要规定它对 block/work item 是否也合法。
- 建议：RunStatus 支持 `paused`；WorkItem 是否支持另行评估，暂不自动扩张。

**方案 C：新增完整 Context/Run DAG 协议**

- 优点：可以表达复杂分支、fork、handoff 和并发。
- 问题：对当前产品过重，模型和前端都不需要理解完整 DAG。
- 结论：暂不采用。只有真正出现多 Agent 分支工作区时再升级。

推荐：**方案 B**。预计 E07 从 8 分提升到 13 分。

### 17.2 G2：Stream Resume

**方案 A：每个前端自行约定 query parameter**

- 优点：实现快。
- 问题：WebSocket、SSE、移动端和测试环境行为会分裂；无法统一表达 replay window 已过期。
- 结论：拒绝。

**方案 B：增加两个非模型、非 Surface Event 的传输对象**

```json
{
  "object_type": "stream.resume_request",
  "protocol_version": "agent_surface.v1",
  "context_id": "ctx_1",
  "run_id": "run_1",
  "after_seq": 42,
  "known_surface_revision": 12
}
```

```json
{
  "object_type": "stream.resume_response",
  "protocol_version": "agent_surface.v1",
  "run_id": "run_1",
  "mode": "replay",
  "first_seq": 43,
  "last_seq": 87
}
```

`mode` 仅允许：

```text
replay | snapshot_required | run_finished | run_not_found
```

- 优点：不改变七类 Block 和 Event；各传输实现共享语义；容易做集成测试。
- 风险：需要明确认证、保留窗口和 `run_not_found` 是否泄漏资源存在性。

推荐：**方案 B**。预计 E08 从 9 分提升到 13 分。

### 17.3 G3：认证等安全外部流程

**方案 A：把 URL 放进 Narrative 或普通 Resource**

- 问题：无法区分证据链接和需要用户完成的安全流程；前端可能自动预取或错误打开。
- 结论：拒绝。

**方案 B：在 canonical Interaction 中增加运行时签发的 ExternalFlowDescriptor**

```json
{
  "external_flow": {
    "flow_id": "flow_broker_auth_01",
    "kind": "authentication",
    "provider": "example_broker",
    "display_url": "https://broker.example.com/oauth/authorize",
    "launch_url": "https://broker.example.com/oauth/authorize?request=opaque",
    "requested_scopes": ["positions.read", "account.summary.read"],
    "completion_mode": "server_notification",
    "expires_at": "2026-07-11T11:00:00+08:00"
  }
}
```

约束：

- 只允许 Runtime 写入，ModelSurfaceDraft 仍只能提出 `intent=authenticate`；
- 前端展示完整域名，用户明确同意前不预取、不打开；
- `launch_url` 不能包含凭据和个人敏感信息；
- 外部完成通知只恢复原 Run，不创建伪造的新任务；
- form mode 不采集密码、API key 或券商 token。

**方案 C：通用 iframe/webview block**

- 问题：权限边界、样式、钓鱼和敏感输入风险过高。
- 结论：拒绝。

推荐：**方案 B**。预计 E06 从 10 分提升到 13 分。

### 17.4 G4：Artifact 子协议

**方案 A：为每种金融工具新增顶层 block**

- 问题：`factor_design/tool_design/query_design` 会无限增长，模型需要学习大量 UI 类型。
- 结论：拒绝。

**方案 B：保持 Artifact 通用，通过注册表解析内容**

注册项至少包含：

```text
artifact_type
content_schema_version
schema_uri
default_renderer
fallback_renderer
editable
validator_ids
```

第一批子协议：

```text
finance.tool_spec.v1
finance.factor_spec.v1
finance.validation_report.v1
finance.dataset_manifest.v1
```

模型只生成业务 content；Registry/Compiler 校验版本并选择受信 Renderer。

推荐：**方案 B**。这不会改变 E01-E03，也不会让普通问答承担额外字段。

### 17.5 G5：Claim 与证据

**方案 A：每句话一个 Narrative block**

- 问题：流式体验碎片化，模型输出不稳定，前端产生大量小卡片。
- 结论：拒绝。

**方案 B：使用字符 offset 锚定**

- 优点：可以精确高亮。
- 问题：Markdown、流式 append、语言替换和国际化都会使 offset 失效。
- 结论：不适合作为主要合同。

**方案 C：高风险回答可选 ClaimEvidence 列表**

```json
{
  "claims": [
    {
      "claim_id": "claim_valuation_low",
      "statement": "估值处于三年低分位",
      "epistemic_type": "fact",
      "evidence_refs": ["valuation_e09"]
    },
    {
      "claim_id": "claim_risk_not_released",
      "statement": "低估值未必意味着风险已经释放",
      "epistemic_type": "inference",
      "evidence_refs": ["valuation_e09", "forecast_e09"]
    }
  ]
}
```

Renderer 可以在段落末尾显示 claim citation，也可以把 claims 放入“事实与推断”详情；不要求字符级定位。只有决策相关回答启用，普通 Narrative 不增加负担。

推荐：**方案 C，作为金融扩展而非核心 Block**。预计 E09 从 11 分提升到 13 分。

## 18. 第二轮预期结果

| 缺口 | 推荐最小方案 | 是否增加 Block | 是否增加模型负担 | 预期场景分数 |
|---|---|---:|---:|---:|
| G1 多 Run 协调 | Run `paused` + coordination | 否 | 否 | E07: 13 |
| G2 断线续传 | resume request/response | 否 | 否 | E08: 13 |
| G3 外部安全流程 | Runtime ExternalFlowDescriptor | 否 | 否 | E06: 13 |
| G4 Artifact 子协议 | Registry + versioned schema | 否 | 仅复杂 Artifact | E04: 13 |
| G5 Claim 证据 | 可选 finance ClaimEvidence | 否 | 仅高风险回答 | E09: 13 |

这些候选方案共同满足一个原则：解决运行、权限和金融证据问题，但不扩大七类 Block，也不把更多运行时字段交给大模型。

## 19. 机器验证结果

代表性消息文件：`docs/protocol_examples/agent_surface_v1_thought_experiment_cases.json`。

验证结果：

- E01-E10 共 18 条代表性消息全部通过 `agent_surface_protocol_v1.schema.json`；
- 事件按 Run 分组后形成 11 条有序事件流；
- E04、E05 的两个 RFC 6902 patch 均通过 target、base revision、路径和重放检查；
- 现有复杂工具设计样例的 13 条消息、10 个事件和 7 类 Block 继续通过校验；
- 负向验证确认当前 Schema 会拒绝 `paused`、`resource.relation=authorization` 和 `stream.resume_request`，与 G1-G3 的缺口判断一致。

因此当前结论不是“Schema 不能工作”，而是：**内容表达核心已经稳定，但跨 Run 协调、安全外部流程和传输恢复合同还不完整。** 在这些合同完成第二轮样例验证前，不应开始正式 UI 设计。
