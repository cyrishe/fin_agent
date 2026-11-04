# Skill 优先、统一发现与研报方法接入

日期：2026-09-08。依据：用户确认的 Skill 优先策略，以及 system/public/private、统一目录与 `$` 调用要求。

## 已实现的主线

默认金融问答先按问题语义匹配业务方法。有匹配时先加载 Skill，由方法指导取证与回答；简单研报字段问题也可以使用研报默认方法。没有匹配或只覆盖部分问题时，继续使用调用者已获授权的数据工具和模型通用能力，保留已有有用指导。方法选择为 SOFT，不增加关键词路由、覆盖率枚举或强制“必须命中”的业务门禁。

用户显式选择 Tool 时仍遵循其明确选择；`$` 业务 Skill 则经服务端授权后加载到同一个 Finance Agent 会话，不进入历史 compiled SkillRunner。自动选择与显式选择使用同一授权版本包。

系统级硬边界包括身份、active revision、可见性、Reference路径、快照一致性和实际工具授权。Skill 不能增加用户权限，也不再因为缺少 `allowed-tools` 声明而收回调用者原本可用的工具。

## 本轮改动

| 部分 | 实现 |
| --- | --- |
| 正式研报方法 | [equity-report-analysis](../../src/skills/finance-business/skills/equity-report-analysis/SKILL.md) 注册进系统业务catalog；默认方法、7个按需References和中文显示信息 |
| 统一授权目录 | [SkillHubCatalogService](../../src/services/skill_hub_catalog_service.py) 将system seed、当前用户private active及public active合并为同一不可变业务目录；Hub、`$`和Web金融会话共用 |
| 用户版本 | [候选存储](../../src/services/skill_candidate_store_service.py) 复用owner、source_manifest.visibility、current_revision_no；启用和公开分别操作，owner校验和双revision CAS；Reference进入不可变revision |
| 三类展示 | system为平台业务方法；public/private为个人资产分享范围；不增加重复三值权限状态，不与内部system_skill执行类型混用 |
| 默认动态发现 | 默认investment_analyst取消固定旧11项列表；缺失/null表示不额外限制，显式空列表禁用，非空列表限定；各Agent/Application投影保留此区别 |
| CC方法加载 | 保留原生Skill，并提供统一只读`read_finance_skill`；`$`预加载已授权正文；方法/Reference记录进入现有Trace |
| DSH方法加载 | 原三项数据工具加`read_finance_skill`与`read_finance_skill_reference`；本轮冻结快照在可信内部context传递，模型只看到摘要和实际选中内容 |
| DSH持续分析 | 普通问答允许取数后按需读取方法、参考和补查，未命中也可继续；方法读取不占数据查询次数。fast和仅数据模式保留原有预算/交付边界 |
| 产品入口 | `$`补全、手写引用、Studio带入对话、候选启用和独立公开/私有切换；业务身份来自认证用户，不复用旧Tool的线程owner别名 |

已读正文和References在当前turn固定版本；下一turn重新计算授权目录。中途撤回公开不回收已经合法读取的内容，后续轮次不再允许读取。数据库目录暂不可用时，普通金融会话记录独立诊断并使用系统方法/现有工具；不能授权确认的个人显式请求会失败，不猜测权限或使用旧私有缓存。

## 接口

- `GET /api/skill-hub`、详情与Reference：按认证身份读取授权目录。
- `GET /api/assets/invocable`：统一`$`候选，业务Skill优先展示，工具仍保留。
- `POST /api/skill-hub/candidates/<skill_id>/activate`：显式启用候选，携带预期candidate和active revision。
- `PATCH /api/skill-hub/candidates/<skill_id>/visibility`：所有者修改private/public，携带预期active revision。

实际字段和HTTP约束以[路由实现](../../src/web/flask_app.py)及[接口测试](../../tests/test_business_skill_explicit_invocation.py)为准。公开不等于经过业务质量认证；本轮未实现自动评测发布门禁。

## 验证证据

使用工作区已有 `.venv/bin/python`（Python 3.12.14、pytest 7.4.0）与应用附带Node。测试未连接真实数据库、未调用真实金融查询或付费模型，也未运行部署。

- 核心Python回归：225 passed，覆盖注册包、权限、CAS、前后版本/Reference、显式调用、真实默认应用配置、CC/DSH运行适配、故障降级、工具授权与结果保留。结果见[JUnit记录](../../outputs/skill_first_20260908/pytest.xml)。
- 扩大Agent路由/上下文/Planner回归：41 passed。与上述核心组为不同测试文件，合计266项Python测试通过；未运行全仓库测试。
- DSH loop policy：39 passed，含有/无Skill的补充取证、参考加载、预算、仅数据和复用隔离。
- 前端调用资产映射：5 passed；`tsc -b`通过。
- 正式研报包 `quick_validate.py` 通过。使用现有项目虚拟环境，未安装新依赖。
- 修改文件的 `git diff --check` 通过。

新增关键回归：[运行整合](../../tests/test_finance_skill_first_integration.py)、[权限与版本](../../tests/test_skill_registry_visibility.py)、[显式入口](../../tests/test_business_skill_explicit_invocation.py)、[动态默认策略](../../tests/test_agent_skill_registry_policy.py)、[DSH方法读取](../../tests/test_finance_dsh_skill_first.py)。

## 范围与尚未验证项

- 代码实现与本地协议回归已完成；未重启当前服务、未向生产库写入Skill或执行发布部署。运行新版Web服务后才会使用本次接入。
- 真实模型是否稳定选择匹配方法、研报分析质量增益及单体/渐进加载成本差异，仍按[实验方案](../../experiments/skills/equity-report-analysis-evaluation.md)做对照；225个协议测试不代表效果评测分数。
- 当前数据主线仍以研报字段/抽取指标为主，没有因本次接入新增研报全文读取能力。
- DSH本轮继续限定为三项金融数据工具及两项方法读取工具，没有扩展个人执行Tool或回测适配；Skill不能突破这一运行边界。
- Web会话使用认证用户目录；独立Finance API的principal与Web用户身份尚无映射，本轮未将API token视为某个Web用户的私有Skill授权，也未新增REST文本`$`解析。
- 默认无身份启动只读system seed；认证用户可使用public和本人private。匿名市场浏览不在本轮。
- Runtime仅保留精简binding元数据而非长期持有所有方法正文；binding和磁盘快照清理仍需结合实际运行生命周期验证，未宣称高并发/长期运行资源有界。
- 工作区在开始前已有较多未提交改动，当前HEAD为`5d775c6`。本轮未提交Git，不能仅以HEAD代表测试源码。生产运行效果与稳定性仍为`NOT_READY`。
