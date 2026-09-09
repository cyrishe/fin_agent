# 专业分析回答与参考数据体验

## 已完成的三项改动

1. **回答**：保留原始 Markdown 分析表格；结论引用块、分节和小型对比表有统一样式，窄屏表格可独立滚动。补充一条通用、正向表达引导，按信息需要选择形式，不增加模型必填 Schema，不让展示层编造指标或重做分析。
2. **参考数据**：有分析正文时，金融列表表格统一进入默认折叠的“参考数据”。显示每组名称、来源和记录数，点击后才挂载原有表格与分页。已有图表、指标卡和正文中的分析表格仍在主回答中。纯取数结果保持直接展示。
3. **Skill 可见性**：成功加载专业方法后显示真实方法名称；主回答与侧栏均可见。加载方法、读取专业参考分别记录进度。空选、失败不伪装成使用成功。历史回放使用已保存的 Skill 记录，新记录保留名称；旧记录缺名称时回退到 ID。

## 根因

- 原展示层只要存在结构化数据，就删除正文中的所有 Markdown 表格，无法区分原始明细和有分析价值的对比。这一清理逻辑已移除。
- DSH 进度映射没有 Skill 分支，未知工具结果进入“读取明细”兜底，因此 Skill 加载曾显示为“读取 0 条必要明细”。现在显式处理实际 Skill 加载和参考读取，明细完成提示只处理明细工具。
- 原 MessageItem 将所有结果块直接平铺；现在仅对符合条件的金融参考表格做展示分组。原始 Surface、数据句柄和分页接口保持原样。

## 设计参考

- [AI Elements Sources](https://ai-sdk.dev/elements/components/sources)：来源入口与回答分离，按需展开。
- [Anthropic Research](https://www.anthropic.com/news/research)：回答与可核验引用相配合。借鉴其证据可访问性，不把引用或加载等同于结论正确。

本项目已有自定义 Surface/Renderer，未引入 AI SDK、shadcn 或另一套状态机。利用既有 Skill ID、加载记录、进度状态和展示元数据衔接。`已加载`仅表示方法正文进入上下文，不表示每个方法步骤或最终结论已经通过核验。

## 验证与边界

- 前端全量 Vitest：117 项通过。包括正文/参考分组、纯取数与图表兼容、折叠时不挂载表格、真实加载证据、历史过程保留。
- 后端相关 Python 回归：111 项通过。包含 Skill 成功、空选、失败、参考加载以及不泄露方法正文。TypeScript 检查与 Vite 生产构建通过；构建保留现有大体积图表依赖的 chunk 警告。
- 本地浏览器：检查默认折叠、展开明细、第二页、Skill 加载视图、纯取数视图；390px 窄屏页面宽度与视口相等，无整页横向溢出。
- 浏览器验收使用 `frontend/tests/fixtures/answer-experience.html`，为开发期页面、虚构数据、不访问生产账户或数据。默认生产构建不会包含该入口。实际 Agent 回答可能采用不同的 Markdown，不保证每题都使用对比表。
- 不进行生产部署、重启或数据库修改。本次不是新一轮真实模型效果评测。

## 主要代码入口

- `src/scenarios/financial_qa/dsh_service.py`：真实 Skill 加载进度。
- `src/scenarios/financial_qa/presentation.py`：保留分析正文。
- `src/scenarios/financial_qa/service.py`：最终结果保存加载事实。
- `frontend/src/components/SkillActivity.tsx`：专业方法提示。
- `frontend/src/components/AnswerEvidence.tsx`：参考数据分组与按需展开。
- `frontend/src/components/MessageItem.tsx`、`BlockRenderer.tsx`、`MarkdownContent.tsx`：回答布局与渲染。

## 2026-09-09 补充：取数后等待阶段

- 根因：`applyStreamEvent` 将最新工具完成内容保存在 `run.summary`，主回答原样显示该内容，容易看成整轮完成；原提示还受 `artifacts.length === 0` 限制，出现任何结果后就隐藏。过程节点的完成状态本身没有错误。
- 修复：整轮 `run.status === running` 时，主回答持续显示动态标识、“正在处理… / 本轮尚未完成”和最新进展。过程折叠标题显示“进行中”，侧栏运行卡同样使用动态标识。只使用现有请求状态；不新增业务状态、推理节点、提示词或进度百分比。
- 收到 `done/run.finished` 或 `error/stream.error` 后移除运行提示；保留已完成步骤的真实结果。断流仍由现有 EventSource 错误处理结束运行。
- 当前本地工作树验证：前端 124 项测试、TypeScript 检查、Vite 构建通过（保留既有大 chunk 警告）。新增 7 项主回答回归，覆盖首事件前、取数完成后、已有部分回答和终态。
- 浏览器使用同一虚构数据验收页，核对等待、已有结果、折叠、完成和失败；390px 视口的文档宽度与滚动宽度均为 390px，未见错误覆盖层或控制台错误。CLI 浏览器在快照阶段无响应，改用应用内浏览器完成验收。
- 本次补充仅本地实现和验证，未提交、推送或更新服务器；无真实模型／生产数据评测。
