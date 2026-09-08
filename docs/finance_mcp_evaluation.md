# MCP 查询评测与临时 Access Token

## 已有实现与本次边界

- 正式工具：`finance_data_query`，Streamable HTTP、无状态，服务内路径`/mcp`，当前nginx外部路径`/fin_agent/mcp`。
- REST/MCP共用`FinanceApiGateway → FinancialQaCcService → DSH`。类名含Cc不代表使用CC；本脚本明确传`runtime=dsh`。
- DSH加载现有`dsh_loop_policy.mjs`，默认启用优化策略；实际开关/模型/推理强度由服务器配置决定。脚本不修改这些参数；`execution_mode=standard`不是未经优化的DSH，`fast`则是已有的无检查/重试快速路径。
- 旧`scripts/eval_finance_report_mcp.py`是隔离启动服务的184题研报回归；`scripts/eval_mcp_both_sample.py`是固定地址、固定模式的历史20题脚本。均保留。
- 新`scripts/eval_finance_mcp.py`只访问现有MCP地址，不创建服务、不访问DB、不持有模型key、不绕过认证。默认并发2、仅数据、每题独立，不传conversation_id，也不移除服务原有显式会话续接功能。
- 新`scripts/create_finance_access_token.py`只给持有服务端父key的操作人员签发临时凭证。到期校验属于认证协议，不修改业务提示词、catalog、SQL或loop。

## 先部署一次认证改动

长期`FINANCE_API_KEY / FINANCE_API_KEYS_JSON`继续有效。新增的临时token需要服务器加载本次`src/finance_api/auth.py`与`access_tokens.py`后才能识别；**旧服务会返回401，不能只生成token却不更新服务**。

部署代码并按现有方式重启金融API服务一次。此后签发临时token不改.env、不增加数据库记录、也不需要再次重启。本文与脚本本身不会自动部署或重启生产。

## 1. 在服务器签发临时token（默认4小时）

用有权读取该服务.env的用户运行，不要把整份.env拷到评测电脑。

```bash
cd /home/che/cyris/fin_agent
.venv/bin/python scripts/create_finance_access_token.py \
  --env-file .env \
  --output /tmp/finance-eval-token.json
```

多key配置必须用`--principal 已有key的ID`选择归属；只有一个key时可省略。
要改为2小时，加`--ttl-hours 2`，也允许小数（如0.5小时）。

输出文件包含access_token、归属及到期时间，文件权限0600，只能新建、不会覆盖。终端仅打印文件位置与到期时间，不打印token。通过SSH/SCP安全传到评测电脑并维持0600权限，不要放进Git、聊天、截图或报告。

```bash
scp che@39.106.248.18:/tmp/finance-eval-token.json /tmp/finance-eval-token.json
chmod 600 /tmp/finance-eval-token.json
```

临时token以HMAC签名，归属继承签发它的父key；每次请求由服务器检查签名和到期时间。这里的“一次性”指一轮评测期间可重复使用，并非只能调用一次。到期前已被接收的请求允许完成；过期后新请求401。轮换/删除父key并让服务重新加载后，该父key签发的全部token失效。

权限范围与父key相同（当前为金融数据REST/MCP访问），不是新增权限或管理员登录凭证。该设计没有单枚token的提前撤销列表；需要立即撤销时轮换父key。客户自助创建/撤销长期凭证的产品功能不在本次范围，脚本不会给客户服务器签名权限。

## 2. 单题或多题查询，直接获得Excel

评测端只需Python依赖，不需要DSH、模型配置或数据库连接：

```bash
python -m pip install httpx openpyxl

python scripts/eval_finance_mcp.py \
  --url https://ai-agent.kingdomai.com/fin_agent/mcp \
  --token-file /tmp/finance-eval-token.json \
  --query '鹏鼎控股与沪电股份2026年净利润预测增速有何差异？' \
  --query '中信证券对迈瑞医疗2027年归母净利润预测是多少？' \
  --concurrency 2 \
  --response-mode data \
  --output-dir outputs/mcp_my_eval
```

生成：`MCP评测结果.xlsx`、每题原始JSON、`results.json`及不含token的运行配置。

- `--response-mode data`：原始数据，不生成summary。
- `--response-mode both`：数据＋summary。其余参数保持一致，便于公平比较。
- `--concurrency 10`：最多同时10个请求。实际吞吐受服务器worker数、DB连接池与模型限流影响，客户端不会自动调服务器。
- `--timeout 180`：单请求HTTP超时；脚本不自动重试。客户端超时不等于服务端任务必然取消，报告记录为超时，不立即重发。
- `--max-rows 100`：每个数据集接口最多返回100条（现有MCP上限）；Excel始终只预览前2条，原始JSON保存接口实际回传内容。真实总条数与返回条数分别保留，不声称JSON是数据库全量。
- `--execution-mode fast`：使用已有DSH快速策略；默认standard与近期DSH Opt评测一致。
- 默认每题独立。不允许题集中的额外字段覆盖认证、runtime或conversation_id。

也可把已有长期key或临时token放进`FINANCE_ACCESS_TOKEN`环境变量，再省略`--token-file`；`--token-env NAME`可更改变量名。不要把token当命令行参数，以免进入shell历史或进程列表。

脚本先做带认证的initialize与tools/list，再发送查询；认证不通过不发业务请求。途中401/403会停止尚未开始的题，已发出请求正常收尾，避免继续浪费配额。HTTP重定向不自动跟随，远程明文HTTP需要显式`--allow-insecure-http`。

## 3. 复用已有评测集

最近一批20题来自：

```bash
python scripts/eval_finance_mcp.py \
  --url https://ai-agent.kingdomai.com/fin_agent/mcp \
  --token-file /tmp/finance-eval-token.json \
  --cases-file outputs/mcp_both_sample_20260907/manifest.json \
  --limit 3 --concurrency 2 --response-mode both \
  --output-dir outputs/mcp_latest20_pilot
```

manifest读取`pilot`，不会把155条分组候选也自动跑掉。确认前三题后，去掉`--limit 3`并选**新的输出目录**可跑20题。`--case-ids BUS026 RTEF112`可指定题目。

研报题集可以重复指定`--cases-file`合并（67＋117＝184题）：

- `outputs/financial_qa_mainland_eval_20260902/cases_mainland_supported.json`
- `outputs/financial_qa_mainland_full_increment_20260903/cases_increment_no_news.json`

通用金融题源：`outputs/d4f10504-8df6-435e-9316-3d89b5fd1015/source_cases.json`。
历史outputs可能不在新克隆的仓库中，须携带所选题集JSON；脚本不会偷偷下载、改写题目或重建golden。

自定义文件支持`{"cases":[{"case_id":"Q001","question":"你的问题"}]}`或字符串数组。
入口覆盖仅使用题目已有`required_entries`；没有golden就留空，不杜撰正确率。

## 4. 报告格式与离线重新导出

参考最近`outputs/finance_python_filter_v2_20260907/同20题回归对比.xlsx`的结果明细，去掉跨版本对比，保留两个页签：

1. **评测结果**：问题、所选工具、执行时间、各数据集条数、入口覆盖、接口状态、LLM轮次、总Token、模型/API累计时间、人工结论。耗时有包含/并行关系，不把这几列直接相加。
2. **结果明细**：实际API及错误、数据集schema、前2条、summary原文、SQL核验条数和人工证据。长summary续行保全，长数据字段仅在预览中明确节选。

“接口完成”“入口覆盖”和“业务正确”分开。0条可是真实空结果；请求失败的条数显示“未返回数据集”，不伪装成0。自动脚本不消耗模型做二次裁判，也不读取DB写入虚假的SQL核验结论。

已有人工评审可用`--review-file reviews.json`导入，格式：

```json
{"Q001":{"verdict":"取数匹配","sql_row_count":0,"evidence":"公司、年份、机构条件完整；独立SQL为0条。"}}
```

`sql_row_count`也可用简短文字表达多个数据集，如`r1=17；r2=26`。无评审时显示未人工评审/未核验。

```bash
python scripts/eval_finance_mcp.py --export-only outputs/mcp_my_eval
```

此命令只重新导出Excel，不调用MCP/模型，不需要token。默认拒绝向已有运行目录发起新评测，避免混合参数或覆盖首跑；导出失败后JSON仍保留。

Excel优先使用可用的Artifact Tool；普通服务器未安装该工具时使用已有openpyxl依赖输出相同列、值及结构，无需安装Codex。开发环境可通过`FINANCE_EVAL_NODE`及`FINANCE_EVAL_ARTIFACT_MODULES`指定Node和node_modules目录。导出器不会把问题或summary中以`=`开头的文字当作公式执行。

退出码：0＝请求及返回结构均完成（不代表语义全对）；1＝至少一题失败/未执行/结构异常，报告仍生成；2＝配置、认证预检或导出失败。原始结果与失败证据不会自动重跑覆盖。

## 本次验证（2026-09-08）

- 使用服务端已有key对生产`https://ai-agent.kingdomai.com/fin_agent/mcp`执行只读tools/list，HTTP 200；确认工具名、data/summary/both及cc/dsh参数。没有调用生产模型或新增生产凭证。
- 38项测试通过：原有key兼容、4小时默认值、有效期精确边界、签名/归属篡改、父key轮换、0600文件及日志不泄露、MCP真实认证中间件、初始化/发现/查询、data/both、并发、默认无历史、认证失败停止和失败证据保留。
- 中间件与客户端集成测试使用真实FastMCP应用和替身数据网关，不伪称真实金融模型回归。
- 用9月7日v2批次已保存的BUS026、BUS093、RTEF112三题验证导出；本地Artifact Tool生成并检查两个页签，schema、样例与summary续行保留；原始业务错误仍显示未完成，没有借导出修改结论。
- 在服务器新建隔离临时目录测试同一导出脚本，确认Artifact Tool不可用时Python导出成功，两个页签及失败标记一致。退出码1是样例中原有失败题导致，非导出失败。
- 示例文件：`outputs/finance_mcp_cli_20260908/MCP评测结果.xlsx`，仅是历史数据导出示例。本轮0次新增模型调用，未运行全量评测，未修改生产.env、未重启生产服务、未提交/推送代码。
