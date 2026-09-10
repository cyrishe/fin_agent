# 金融MCP评测客户端

需要Python 3.10或更新版本，以及管理员提供的临时Token。模型和金融数据查询都在服务器执行。

## 首次准备

解压评测包，将管理员给你的Token文件保存为本目录的`token.json`。
首次安装依赖（以后无需重复安装）：

```bash
python3 -m pip install -r requirements-eval.txt
```

如果电脑的Python命令叫`python`，将上面的`python3`替换为`python`。
Windows使用Git Bash或WSL运行Shell；普通CMD/PowerShell不能直接解释.sh文件。

## 每次评测只执行一条命令

```bash
bash run_eval.sh
```

运行前用文本编辑器打开`run_eval.sh`，只改顶部“用户配置”部分即可。
自带6条研报样本。使用自己的样本时，把文件放在本目录，将`CASES_FILE`改为对应文件名。
相对路径始终以Shell所在目录为基准；文件名含空格也可以。

```json
{"cases":[
  {"case_id":"Q001","question":"机构看好山东黄金的主要理由有哪些？"},
  {"case_id":"Q002","question":"分析阳光电源近期研报中的竞争优势。"}
]}
```

常用配置：

```bash
SKILLS=()                                 # 自动路由（默认）
SKILLS=("equity-report-analysis")          # 指定研报Skill
SKILLS=("earnings-analysis" "valuation-analysis") # 多Skill，按顺序采用方法
TOOL="finance_data_query"                  # 指定金融数据查询入口，须同时将SKILLS设为()
DETAIL=true                               # 默认包含轮次、Token、耗时、执行步骤
LIMIT=3                                   # 可先测前3题；设为空字符串则测全部
```

以上是可选写法示例，不要把多个SKILLS赋值同时复制进去。其他参数及取值均已写在Shell配置旁的注释中。
Skill最终可用性取决于账号权限和服务器发布目录。MCP入口只有`finance_task`和`finance_data_query`；底层数据方法不是独立MCP入口。

## 查看结果

默认每次创建独立的`outputs/mcp_eval_时间戳/`目录，包含：

- `MCP评测结果.xlsx`：一题一行，完整问题、回答、关键指标及独立的明细表。
- `完整阅读.html`：长回答的连续阅读页，执行证据默认折叠；请与Excel放在同一目录。
- `results.json`、`results/`和`manifest.json`：完整返回记录及运行配置。

Token过期后向管理员领取新的文件即可。脚本不会打印Token，不会自动申请凭证或自动重试失败的查询。
接口不提供模型内部思考原文；报告保留实际返回的推理Token、轮次和执行证据。
退出码0表示接口流程完成，并不代表业务答案全部正确；1表示有失败题但报告已生成；2表示配置、认证或导出等错误。
