# Filter 运算符边界修复与历史溯源

## 结论

这是自研金融DSL解析器的词法边界缺陷，不是DeepSeek Harness或CC框架内的业务限制。当前CC和DSH共用call_structure及report_provider，都会受影响。此次仅修复解析和执行协议缺口，不扩展新的业务状态、validator或个案规则。

## 历史证据

- call_structure.py的FILTER_ATOM_RE首次随2242990提交，英文操作符没有词边界；后续9a53e69上下文优化和4e20040 data-only修复没有改这段正则。
- 2242990父提交中的旧report_provider.FILTER_RE已有相同缺陷。仅提取其正则并运行，`rating_change contains 下调`产生`field=rat, op=in, value=g_change contains 下调`。旧_build_filter还会跳过无法识别的字段/操作符，因此历史没有报错不构成正确性证据。
- 已保存的2026-09-02 DSH Opt首跑RTE082“近期有哪些机构对兴森科技进行了评级调整？”使用`rating_change not null`，记录了相同field=rat错误；它不是9月6日新出现的问题。
- 同批CC RTE082只按name查研报，没有该谓词，故未触发解析错误；这不能证明CC对非法语法有不同保障。
- 当前与旧研报catalog合约/通用协议均缺少明确的统一filter运算符语法说明。未找到最近压缩删除既有完整filter语法的证据；属于已有协议表达缺口，不能把全部根因归给catalog压缩。

历史文件：outputs/financial_qa_prompt_policy_random20_20260902/cc_results.json及dsh_opt_first_pass_results.json。

## 为什么会误拆

原模式允许字段后零空白，英文in/like又没有词边界。遇到不支持的contains或not null时，正则回溯缩短字段，把rating_change内部的in拿来满足操作符部分：`rat | in | g_change contains 下调`。这不是有意编写的rating字段规则，而是通用词法错误。

## 实现

1. FILTER_ATOM_RE为英文操作符增加词边界。保留合法的符号比较、in列表/结果引用、like、布尔分组、null语法及not in的现有解析后拒绝行为。
2. 非法表达式指出原字段和出错位置，附带catalog权威filter语法；不再伪造不存在字段rat，不静默删除谓词或将非法contains自动放宽为别的业务语义。
3. api_view_catalog.json新增一个可选自然语言资产filter_syntax，作为唯一说明真源。模型执行包和错误反馈直接复用同一文本；不复制到各API规则、system prompt或新枚举里。
4. 精确dataview执行目录加载该语法；subject/路由摘要不加载。CC与DSH共用该目录投影。旧阶段context_builder也读取同一资产。
5. 不改变loop预算、完成声明、认证、会话续接、业务SQL或模型参数。

改动职责：词法解析属于HARD；catalog语法资产及其分阶段加载属于SOFT适配。

## 验证

- 210个Python测试通过，28个DSH loop测试通过；既有依赖告警保留。
- 覆盖多个包含in/like的字段与非法操作符，验证不能回溯拆字段；覆盖合法大小写/空白/符号/列表/空值兼容。
- CC SDK工具handler与DSH MCP bridge对相同非法谓词返回一致错误，且不进入Provider。没有用CC模型重跑替代这项协议证据。
- Provider级测试确认非法谓词不连接DB；合法like生成的SQL保留公司、起止时间和评级条件。
- 真实服务器隔离测试使用原Aliyun模型，DSH Opt standard；key鉴权保持，无key/错误key401。生产没有部署或重启，临时服务均已关闭。

| 题号/模式 | 秒数 | LLM次数 | 结果与检查 |
| --- | ---: | ---: | --- |
| RTE008 data | 5.58 | 2 | =下调，公司和三个月范围保留，0行，与此前SQL一致 |
| RTE008 both | 7.51 | 3 | in[下调,调低,下调评级]，公司和时间保留，0行、空结果摘要，无工具错误 |
| RTE003 data | 4.16 | 2 | 两家公司2026年净利润增速预测，43行，与此前17+26一致 |
| RTE082 data | 5.09 | 2 | 无非法操作符，返回4篇评级记录；未筛出“调整”，样例仍有维持，业务语义尚未收口 |

原生会话审计：4个会话实际加载的执行包均含与唯一catalog完全相同的filter_syntax，0个工具错误。本次回答模式通过MCP开启共享问答引擎，不是登录后的Chat端到端回归。

## 边界与遗留项

本次确定性解析bug及语法注入缺口已修复；不能据此宣称所有模型请求都会保留完整业务条件。RTE082仍存在初始查询语义覆盖不足，其他业务条件丢失与回答证据边界问题应分别验证，不能靠针对这道题的提示词掩盖。184题全量未恢复运行。

测试版本为5d775c6上的本轮工作区修改；原始结果与审计位于outputs/finance_filter_fix_20260906/。未修改旧首跑结果，未提交/推送本轮改动，也未部署生产。

## 保守化复核与三题冒烟（用户要求暂停全量）

本轮保留上述词边界修复，不替换整个解析器，不新增contains别名、不把非法表达式自动改成like、不增加评级关键词或完成判定validator。错误路径的字段/位置提取仅用于诊断，不参与SQL构造。

进一步收窄两处：执行目录只对精确声明`filter`参数的合约加载语法，不用参数名称子串猜测能力；语法明确空值筛选须由当前API支持，不暗示全部Provider能力一致。新增两项协议测试，覆盖filter与prefilter的区别。本轮定向Python测试187项通过（包含CC/DSH共享工具、catalog、provider及recovery）；git diff --check通过。没有再次调用CC模型。

真实MCP仅预选RTE008、RTE003、RTE007，各首跑一次，并发2，不带conversation_id；DSH Opt standard、low、deepseek-v4-flash-0731，response_mode=data。服务运行于隔离目录，沿用原服务端模型环境；无key/错误key均401，有效key200。所有请求summary=null，保留row-dict、schema及真实条数，仅展示前2行。临时监听已关闭，生产未修改。

| 题号 | 核验重点 | 各数据集条数 | 秒数 | LLM次数 | 上报总token（含缓存） |
| --- | --- | --- | ---: | ---: | ---: |
| RTE003 | 两家公司、2026、np_parent_growth、forecast均保留 | 17、26 | 5.81 | 2 | 9425 |
| RTE007 | 中信证券、300760.SZ、2027、np_parent、forecast均保留 | 0 | 4.73 | 2 | 9356 |
| RTE008 | 安井食品、2026-06-06至2026-09-06、rating_change like '下调'均保留 | 0 | 5.57 | 2 | 9307 |

逐项检查实际tool_calls的submitted_request与request，未删除筛选条件；RTE008仅将结果变量result规范为r1，谓词未改。所有执行attempt均为1，无工具错误或重试，总计6次模型调用、28088上报token。

本轮复用已有SQL证据交叉核验（未重新查询SQL）：RTE003在`outputs/financial_qa_mainland_full_report_20260904/db_sql_audit_184_enriched.json`为43行；RTE007/008在`outputs/finance_report_mcp_regression_20260906_4e20040/db_regression_audit.json`分别为中信精确/包含匹配均0、窗口内6篇均维持。新结果与这些证据一致；小样本通过不等于所有业务语义问题已解决，RTE082仍保留为未解决项。

本轮原始证据：`outputs/finance_filter_fix_20260906/conservative_smoke/`。按要求不启动184题全量，不为凑通过率重跑或替换失败样例。本轮未提交、推送或部署。
