# MCP评测：临时Key、自动路由与完整报告

**给普通用户：优先使用根目录 `run_eval.sh`，编辑顶部带注释的配置后，只运行 `bash run_eval.sh`。**
说明见 [客户端使用说明](mcp_eval_client.md)。管理员执行 `python scripts/package_finance_eval_client.py`
即可生成可交付的客户端ZIP，仅包含白名单中的客户端文件、依赖清单和样本，不含Token或服务端配置。
将单独签发的临时Token交给用户，用户放为包内的`token.json`即可。macOS/Linux支持Bash，Windows使用Git Bash/WSL。

统一入口：`scripts/eval_finance_mcp.py`。只调用现有MCP，不启动服务器、不修改生产配置。
默认 `finance_task`、`runtime=dsh`、`research_mode=auto`、`execution_mode=standard`、
`response_mode=both`、`detail=true`、并发2、HTTP超时360秒。每题独立，不传conversation_id，不自动重试。

评测器固定传`is_test=true`：正常写入用量账本，显示在独立的测试栏，且包含在系统总量内。
普通REST/MCP调用省略此字段仍计入原渠道。测试标记不改变权限、模型提示或执行过程。
测试与MCP/HTTP是互斥的记账分类，同一请求只累计一次。隔离研报评测器也启用测试记账，
不再关闭用量记录；纯替身单元测试不连接生产账本。

旧版服务需加载新代码并重启后支持此字段；评测器会先检查工具Schema。历史记录可由管理员凭原始证据补记：

```bash
python scripts/backfill_finance_test_usage.py <保存的运行目录> --env-file .env
# 上面先预览数量、Token和完成日期；确认原始证据后执行：
python scripts/backfill_finance_test_usage.py <保存的运行目录> --env-file .env --apply
```

补记保留原始请求ID和完成时间，按ID幂等；若已存在记录与证据冲突，则整批回滚。
不会从历史请求数量猜测哪些是测试，也不会无证据重新归类旧的MCP记录。

## 一条命令签发1小时凭证并运行

在有权读取服务端API父Key的环境运行，例如服务器项目目录：

```bash
cd /home/che/cyris/fin_agent
.venv/bin/python scripts/eval_finance_mcp.py \
  --url https://ai-agent.kingdomai.com/fin_agent/mcp \
  --issue-token --env-file .env --ttl-hours 1 \
  --cases-file tests/evals/report_mcp_skill_smoke_v1.json \
  --output-dir outputs/report_mcp_run
```

这条命令复用现有临时Token签名机制，签发后直接调用，凭证只在进程内存中使用，不打印、不写入结果文件。
多Key配置须加 `--principal 已有principal_ID`。真实环境变量优先于env文件。
`--ttl-hours`支持小数，如0.5；默认1小时。报告仅记录principal、签发/到期时间等非秘密元数据。

这里不是匿名申请接口：签发需要已有父Key权限，不引入新的远程发Key服务。
临时Token继承父Key的权限和归属，过期后新请求401；过期前已接收的请求可完成。
认证失败后停止未开始的题，已开始的请求收尾，不自动续期或重跑。
父Key轮换并加载后，其临时Token同时失效；不支持单枚提前撤销。

服务必须已加载支持临时Token及`finance_task`的代码。脚本先认证、initialize、tools/list，
并通过list_skills核对显式方法的权限。旧服务或无权限会在业务请求之前失败，不悄悄改走其他入口。
本脚本不会自动重启生产服务。

## 普通客户端使用已领取的Key

客户端依赖：`python -m pip install httpx openpyxl`。
自动签发模式另需项目已有的 `python-dotenv` 依赖；不需要模型Key或数据库访问。

```bash
python scripts/eval_finance_mcp.py \
  --url https://ai-agent.kingdomai.com/fin_agent/mcp \
  --token-file /tmp/finance-eval-token.json \
  --query '机构看好山东黄金的主要理由有哪些？' \
  --skill equity-report-analysis
```

也可将已有Key/临时Token放在`FINANCE_ACCESS_TOKEN`环境变量，然后省略token-file。
`--token-env NAME`改用其他变量。凭证不接受命令行明文参数。
若要单独签发供别人使用，保留原工具：

```bash
.venv/bin/python scripts/create_finance_access_token.py \
  --env-file .env --ttl-hours 1 --output /tmp/finance-eval-token.json
```

该工具默认仍是历史的4小时，指定1小时即可；文件权限0600，只能新建。通过安全渠道交付，不复制整份.env。

## 选择Skill、工具或自动路由

在上述命令后组合这些参数：

| 目的 | 参数 |
|---|---|
| 自动路由 | 不指定Skill/工具；默认`finance_task` |
| 将题集里的显式选择清空 | `--auto` |
| 指定研报Skill | `--skill equity-report-analysis` |
| 多Skill，保留顺序并综合回答 | `--skill earnings-analysis --skill valuation-analysis` |
| 指定金融数据查询入口 | `--tool finance_data_query` |
| 对含Skill的题集改跑数据入口 | `--tool finance_data_query --auto` |
| 回答和参考数据 | `--response-mode both`（默认） |
| 只返回数据 / 只返回回答 | `--response-mode data` / `--response-mode summary` |
| 详细执行证据 | `--detail`（默认）；关闭用`--no-detail` |
| 深度、快速答复 | `--research-mode deep` / `--research-mode fast` |
| 调用并发 / 超时 / 样本行数 | `--concurrency 2 --timeout 360 --max-rows 100` |
| 题集筛选 | `--limit 3` 或 `--case-ids RTE016-explicit RTE020-auto` |
| 已知服务器版本 | `--revision <server-commit>`，仅记录调用者声明，不用本地commit冒充服务器版本 |

`--tool`是MCP暴露的入口名，目前查询入口是`finance_task`、`finance_data_query`。
`stock.report.query`等底层金融目录方法不是独立MCP工具，不能冒充MCP工具名传入。
数据入口根据问题选择底层金融数据方法；本脚本不通过改写问题假装硬性锁定某个底层方法。
工具工坊自定义工具不在本次范围。

CLI的Skill/工具参数覆盖题集相应字段。`--auto`清空Skill选择，并默认使用finance_task；
同时指定`--tool`时以显式工具为准。Skills仅用于finance_task；在数据入口传非空Skills会给出配置错误。
Skill列表保留首次出现顺序，空列表或省略表示自动选择。

题集支持字符串数组、`cases`/`pilot`数组，以及`question`/`query`、`case_id`/`id`兼容输入：

```json
{"cases":[
  {"case_id":"report","question":"总结贵州茅台近期机构共识","skill_ids":["equity-report-analysis"]},
  {"case_id":"quote","question":"贵州茅台最新收盘价","tool":"finance_data_query"},
  {"case_id":"auto","question":"分析阳光电源的竞争优势"}
]}
```

额外题集字段不直接透传到API；不能覆盖认证、runtime或会话归属。
不要向已有运行目录追加新调用，脚本会拒绝，避免混合配置和覆盖首跑。

## detail与思考信息

默认保存完整JSON响应与这些接口已有字段：

- 模型响应次数`detail.turns`，不是用户对话次数，也不等于工具调用次数。
- 每次模型响应的耗时、输入/缓存/输出/推理Token、内部turn/step编号。
- 客户端耗时、服务端请求耗时、核心执行耗时、排队时间。
- 工具事件、调用参数、成功/失败、校验耗时、供应商API耗时、执行尝试。
- 实际加载的Skill、内容hash、目录版本、参考数据、返回行数和截断标识。

当前API不返回模型内部思考原文或独立纯思考耗时。报告呈现已公开的步骤、查询目标、工具证据和推理Token，
不会把工具调用日志称为内部思考，也不会为获取思考原文改变服务端协议。
增加客户端timeout不改变服务端turn预算或推理强度。

时间可能重叠，不能相加为总耗时。缓存Token、推理Token依API定义统计，避免重复相加；未返回指标留空。
结果总行数可能包含重复查询和聚合，不等于独立研报篇数。

## Excel与完整阅读

每次运行生成 `MCP评测结果.xlsx`、`完整阅读.html`、`results.json`、`results/*.json`、`manifest.json`。
Excel有五个页签：

1. **评测结果**：一题一行，完整问题和完整回答各一个单元格；关键耗时、Token、轮次、所选Skill、接口状态、人工复核。
2. **运行指标**：一题一行，完整的服务耗时、Token口径、模型/推理强度、行数和请求配置。
3. **模型步骤**：一次模型响应一行，不再把一句话拆成一行。
4. **工具调用**：一次事件/业务调用一行；两类来源明确区分，保留失败和尝试证据。
5. **参考数据**：一个结果集一行，包含实际返回的数据、Schema和来源。

Excel行高有限，长回答在单元格内保存全文，主表保持适当高度；点击“完整阅读”进入对应题目的离线阅读页，
原始回答连续展示，诊断数据默认折叠。分享时请将HTML与Excel放在同一目录。
只有超出Excel单元格32,767字符容量的极长文本才在Excel中明确节选，HTML/JSON仍保存全文。
原始回答不改写；不会基于HTTP200、工具命中或Token数量自动推断业务正确率。

人工评审可通过 `--review-file reviews.json` 导入：

```json
{"RTE003-auto":{"verdict":"存在数值比较错误","evidence":"指出具体错误和来源，不改写模型原文。"}}
```

离线重排旧结果，不调用MCP、模型或签发Token：

```bash
python scripts/eval_finance_mcp.py \
  --export-only outputs/server_report_skill_20260910/isolated \
  --output-dir outputs/report_mcp_readable
```

支持通用脚本的results.json，以及9月10日服务器隔离测试的manifest＋单题JSON。
省略output-dir时原地重建报告。报告导出失败也保留已保存的JSON，不自动重跑业务请求。

开发环境优先使用Artifact Tool，可设置 `FINANCE_EVAL_NODE` 与 `FINANCE_EVAL_ARTIFACT_MODULES`。
普通服务器未安装Artifact Tool时使用openpyxl，保持相同列和值，不需要安装Codex。
问题、回答和数据按文本写入，HTML全部转义，不执行模型输出中的公式或脚本。

退出码：0＝所有请求与必要响应结构完成（不代表语义全对）；1＝存在失败/未执行题，但已生成报告；
2＝配置、预检或导出失败。HTTP超时不代表服务端已经取消任务，因此不会自动重发。

## 验证入口

```bash
.venv/bin/python -m pytest tests/test_finance_mcp_client_eval.py tests/test_finance_access_tokens.py -q
```

测试覆盖真实MCP认证中间件与替身业务网关、临时Key寿命/归属、自动和显式路由、详细响应、并发、
失败停止、秘密脱敏和报告原文完整性。替身测试不代表生产模型效果评测；历史隔离结果不代表公网新版已生效。
