# Catalog 叶子契约与提前查询：历史现场和机制核查

日期：2026-09-08。范围：只做诊断、历史回放和证据整理，本次未修改运行机制或部署服务器。

## 结论

不是系统把 `stock` 概览误判成了执行包。历史记录证明：模型只读概览就尝试查询，框架当时正确拦截；随后加载 `report` 执行包，框架进入全局 query 阶段，未加载契约的 `basic_info` 也能通过阶段检查，最后被已有协议静态检查拒绝。

有三个已证实的机制事实：

1. 模型面对的是扁平的通用 MCP 工具，目录层级在工具返回内容中，Harness 不会自动把“目录”解释为必须遍历的工具依赖。
2. Skill 接入用通用业务指引替换了数据目录阶段的具体指引。这是提示词组装的职责混合，不是缺少更多业务案例提示。
3. 当前 readiness 是阶段级，不是本次请求对应 API 的契约级；“读过某个执行包”和“当前调用有对应执行包”没有对齐。

模型是否“觉得信息足够”属于内部判断，日志不能直接证明。能证明的是它在尚未读取对应执行包时生成了调用，以及系统为什么允许生成、在哪里拦截、又在哪里放行。

## 1. 证据来源

- 历史题目 RTE020：**阳光电源在光伏行业的竞争格局中处于什么位置？**
- 服务器版本：`4fdda49c0a04891ab9d74c19bef24abefda31369`。
- request_id：`fq_e0ef7d7d1ebf4d27b13598cf374c489d`。
- session_id：`financial-qa-f89ec19e23624e6395d37d8c5eb7d0fd`。
- 策略文件 SHA256：`4e69b698a291a78df9ff452597e4fa10306e4b360c5b8a96b53158954f8fb8ba`。
- 历史 native 日志 SHA256：`9d056a22a3ed04516efeba5f6bee40cf3427ccc55a28282e9c20a151bb77f542`。
- [逐轮输入、工具参数及完整返回](evidence/report_protocol_failures_20260908/RTE020_context.md)。保留可观察上下文，不包含模型内部推理文本；不是供应商 HTTP 抓包。
- [实际提示词汇总](evidence/report_protocol_failures_20260908/RTE020_actual_prompts.md)。

本次离线回放读取上述 Git 版本的策略源码，并核对其 SHA256 与历史记录一致，未用当前工作区修订后的预算判断代替历史代码。

## 2. 真实发生顺序

| 轮次 / native seq | 已读取的目录与实际动作 | 框架结果 |
|---|---|---|
| 第 1 轮，175–180 | 读取 `stock` 概览及两个业务 Skill，未读取数据执行包 | 概览返回 `mode=subject`，仍处于 catalog 阶段 |
| 第 2 轮，183 | 阶段原因明确为 `catalog_needs_narrowing` | 实际注入的是 Skill 通用指引，见下文 |
| 第 2 轮，407–408 | 提交 `stock.basic_info.query(...)`、`stock.business_segment.query(...)` | 阶段 guard 拒绝，未执行查询工具主体 |
| 第 2 轮，409–410 | 读取 `stock / report / query` 执行包 | 包返回 `mode=dataview`，本轮结束后进入 query |
| 第 3 轮，530–531 | 提交包含 `stock.basic_info.query(...)` 与 `stock.report(...)` 的 flow；仍未读 basic_info 包 | 阶段检查放行；静态检查拒绝第一步：`unsupported api=stock.basic_info.query` |
| 第 4 轮，555–556 | 读取 `stock / basic_info / query` 执行包 | 得到精确入口 `stock.basic_info` 及其签名、字段 |
| 第 5 轮，659–660 | 已去掉 `.query`，但把 `data_request_complete` 放进 steps 内且值为字符串 | 外层 MCP 参数 Schema 拒绝 |
| 第 6 轮，758–759 | 修正外层 JSON 后再次提交 | 已进入 `query_repair_limit`，阶段 guard 拒绝 |

同一轮内生成的多个工具调用，不能假定后一个调用已参考了前一个工具的返回。这里第 2 轮对 report 包的读取，不能解释成第一次错误调用之前模型已经知道 report 包。

本题后续停止是修复预算耗尽；不能把其他题目的 query 次数边界 bug 当成本题 `.query` 的根因。

## 3. 为什么“目录工具”没有自动强制走到叶子

历史请求头注册给模型的只有以下五个原生工具：

- `mcp__finance__read_finance_catalog`
- `mcp__finance__finance_query`
- `mcp__finance__load_finance_result`
- `mcp__finance__read_finance_skill`
- `mcp__finance__read_finance_skill_reference`

`stock.basic_info` 不是 Harness 注册的独立工具，而是 `finance_query.steps[].request` 字符串里的业务 API。`subject → dataview → operation` 是我们在 catalog 返回中表达的层级，并非 Harness 内建的目录树和调用依赖。

代码位置：`src/scenarios/financial_qa/tools.py:648`。仅 subject 返回 `mode=subject`；subject + dataview 返回 `mode=dataview` 并包含方法契约。

历史 stock 概览列出了 `basic_info`、描述和 `operations: ["query"]`，没有该方法的 `api_name`、签名和字段。首次错误调用与把这些导航名称拼为 `stock.basic_info.query` 相符，但这只是对可观察输入和输出的解释，不能据此声称已观察到模型内部拼接过程。

“模型能够翻阅目录”是能力；“当前执行必须依赖已加载的契约”是运行时约束。前者不会自动提供后者。

## 4. 新发现：Skill 指引覆盖了数据目录指引

原本 catalog 阶段的指引：

> 当前任务是目录定位。根据路由摘要与方法定义，一次提交明确的 subject + dataview + operation；需要的独立目录可并行读取。定位有歧义时读取对应概览。

本题实际收到的指引（seq=183）：

> 依据授权 Skill 目录选择适用方法，读取尚未加载的正文，再定位所需数据目录。其余数据目标通过金融目录取数，并结合通用分析完成。

这是 `promptFor()` 中 `base = ...` 的替换，不是原有阶段职责加上 Skill 的分析说明：`src/scenarios/financial_qa/dsh_loop_policy.mjs:538`。

触发条件为授权 Skill 目录存在且不是仅数据模式，**不要求已经选中或读取具体 Skill**：同文件 `hasSkillCatalog()`、`skillGuidedAnswer()`，226–233 行。query 阶段的“按已加载契约构造 finance_query”也被通用 Skill 综合分析指引替换。

`git blame` 确认该覆盖分支由 `4fdda49c`（2026-09-08 15:59:38）引入，正是本次评测的部署版本。回放该版本的提示词组装：有 Skill 目录时逐字匹配历史 seq=183；无 Skill 目录时返回原目录阶段指引。

边界：全局 system 提示中仍有“读取对应执行包，按精确 API 入口构造请求”。因此不是模型完全没有被告知规则，而是紧邻调用的阶段指引被弱化、职责混合。本次没有模型 A/B 实验，不能把 `.query` 的全部概率归因于这一项。

## 5. 查询工具始终可见：有意的缓存选择

`preserveRequestPrefix` 默认为 true，注释说明是为了保持工具 Schema 稳定，复用 DeepSeek KV-cache：`dsh_loop_policy.mjs:47`。

`applyRestriction()` 在该配置为 true 时直接返回，不调用原生 `agent.ctx.tools.restrict()`：同文件 852 行。历史每轮请求头确实一直包含五个工具。

因此模型即使仍在 catalog 阶段也能生成 `finance_query` 调用；后续执行 guard 会拒绝它。**可见不等于允许执行。**这不是 stock 概览被误判为 ready，而是可见性与执行权限分离的性能取舍。

## 6. 真正的放行缺口：readiness 没有对应到目标 API

`catalogIsReady()` 仅判断 `payload.mode === "dataview"`：同文件 408 行。

`updateAfterStep()` 在本轮目录调用均返回 dataview 后进入 query：同文件 618 行。`stageAllows()` 在 query 阶段允许通用查询，不检查请求中每个目标 API 是否已有对应契约：同文件 583 行。

所以本题读到 report 包就进入 query；随后 basic_info 尽管没有加载，也可以进入查询工具，直到已有协议校验发现 `.query` 不存在。

已有 `reusableCatalogApis()` / `canReuseCatalog()` 会从模型仍可见且版本有效的历史目录中提取 API，并核对目标，但用于 turn 初始化与 catalog 阶段的复用放行，不是本轮进入 query 后的逐目标检查。不能说系统完全没有契约复用能力，准确说法是这项能力没有覆盖本次缺口。

## 7. 离线验证结果

探针脚本：`outputs/server_report_eval_20260908/probe_catalog_mechanism.mjs`。

原始结果：`outputs/server_report_eval_20260908/catalog_mechanism_probe.json`。

分别回放到原生 seq=407 和 seq=530 之前，测试原有 guard。每个输入独立恢复现场，不执行模型、MCP 工具主体或数据库查询。

| 回放现场 | `stock.report(...)` | `stock.basic_info(...)` | `stock.basic_info.query(...)` |
|---|---|---|---|
| 仅加载 stock 概览 | 阶段拒绝 | 阶段拒绝 | 阶段拒绝 |
| 已加载 report 执行包 | 阶段放行 | 阶段放行 | 阶段放行，之后仍会被静态检查拒绝 |

切换 `preserveRequestPrefix=true/false` 后，上表执行 guard 结果不变，共 12 个断言通过。false 时，在前一个现场会隐藏 finance_query；但后一个现场仍放开通用查询，不能独自解决“读了 report，调用未加载的 basic_info”。

这里的“放行”只指阶段 guard，并非 DSL 合法或 SQL 已执行。

## 8. 能否用现有框架能力约束

可以。Harness 已有：

- `tools.restrict({allow, deny})`：按阶段控制原生工具可见性。
- `tools.guard(...)`：在执行前检查必要条件。

实现在 `/Volumes/ext/deepseek-harness/packages/core/tools/src/index.ts:1063` 和 `:1099`。我们已接入这些接口，不需要再造目录框架。

正确的约束目标应是：**本次调用依据的 API 契约已经可用**，而不是“机械地按三层逐个翻页”。

`get_model_dataview()` 在不指定 operation 时会返回该视图的全部完整方法；直接读取对应执行包也合法，仍可见且版本一致的旧包可以复用。它们均不应为了满足层数而增加工具调用。

按现有职责边界，后续最小改动方向是：

1. 数据目录与查询的阶段职责保持稳定，Skill 负责取证范围和分析方法，不替换执行契约的加载职责。
2. 将已有目录契约与本次目标 API 对应起来，利用现有执行前检查承载，不新增按题目关键词设计的 validator，也不修改 `.query` 字符串来兜底。
3. 是否启用阶段工具隐藏，单独评估少一次无效调用与前缀缓存损失，不把它当成契约检查的替代品。

这些可排除已证实的设计缺口，不能保证模型读包后永远不犯语法错误；普通生成错误仍由原有静态检查和正常 loop 修复。本次只提出方向，尚未实施。
