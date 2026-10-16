# 公告原生抽取流程

独立 Python 脚本，读取公告正文，必要时下载 PDF，调用一次 LLM 提取，再把带证据的结果保存为 JSON；加 `--write` 才追加到 kingdomai 的四张 `notice_*` 表。图像页先由同一个模型转录；抽取输出无法保存时最多完整重提取一次。没有 Agent、工具循环、模型 SDK、队列或应用服务依赖。

## 表结构判断

继续使用 `notice_documents`、`notice_events`、`notice_metric_facts`、`notice_participants`，不新增类别专表。本轮没有修改表结构。

| 表 | 公告自身的职责 | 消费方式 |
| --- | --- | --- |
| documents | 来源文档及抽取版本、正文快照、原始类型、摘要、忽略原因 | 找公告及核对原文；保留多个版本 |
| events | 本次事项、进展、条件、关键日期、前次披露引用 | 回答发生了什么、到哪一步、还需什么条件 |
| metric_facts | 从属于事项的主体、数值/区间、单位、期间和口径 | 回答多少钱、多少股、当月还是累计、实际还是计划 |
| participants | 事项内主体和角色 | 区分买卖方、借款人、担保人、诉讼当事人、交易标的 |

这与研报共有来源和数值证据的表达方式，但业务中心不同：公告以事项与本次变化为中心；不设置研究观点、评级、目标价、分析师、盈利模型，也不依赖 report 表或强制绑定 `metric_def`。同一份公告可包含多个事项；非数值事项仍然有价值。13 大类只是检索分类，不能充当流程状态或强制字段模板。

财务/经营类重点是量价、利润与现金流；交易/融资类重点是交易方向、角色、金额及生效条件；监管/诉讼类重点是程序进展与责任；治理类重点是人事和议案；交流材料保留公司答复。共同协议只固定稳定的主体、数值、期间、日期和证据，具体业务含义由摘要、事项说明及 `basis` 表达。

`actual/forecast` 单独不足以判断能否加总。合同已签，不代表收入已实现；额度已获批，不代表余额已用足。读取时必须同时读取指标名、主体、scope、basis 和证据。表里的单位、主体原称尚不是统一主数据，不能不核对口径就跨公告求和。

## 安装与运行

Python 3.9+。使用虚拟环境，避免修改系统 Python：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-notice.txt
.venv/bin/python scripts/notice_pipeline.py \
  --pg --since 2026-09-02 --until 2026-10-02 --limit 65 \
  --env-file .env --output outputs/notice_run
```

日期区间是左闭右开。默认最近日期排序后取 limit 份，**不是自动保证每类各 5 份**。可以用 `--ids 123 456` 指定来源 ID。PG 连接会话固定为只读，不更新源表。

先查看输出，确定要写入时在相同命令增加：

```sh
--write --batch notice_native_20261002_final
```

已有成功结果直接复用。相同来源、正文及抽取配置只追加一个版本，重跑不重复插入；四表在同一文档事务内提交，失败回滚。本脚本不会建表、删表、覆盖已有人工样本。若已用另一 batch 写入同一版本，再改 batch 重跑不会复制一份。

也可以离线提供从 PG 导出的源记录：

```sh
.venv/bin/python scripts/notice_pipeline.py \
  --input cases.json --env-file .env --output outputs/notice_run
```

`cases.json` 为对象或对象数组：`id`、`url`、`title`、`content`、`pub_time`、`stk_code`、`notice_type` 等源字段。url 和 title 必填；content 为空时下载该 URL 的 PDF。来源身份按 `postgresql.public.news_notice` 保存，因此其他来源应先调整来源标识，勿冒充 PG 公告。构造测试数据不得写入业务库。

常用参数：`--workers 2` 控制并发；`--model` 覆盖配置模型；`--max-tokens 16000`；`--max-chars 160000`；`--pdf` 强制从原件重新解析已有正文。超长正文不静默截断、不发布半份结果，实际需求超出上限时明确调大预算或分篇处理。

## 配置

只读 `.env`，不会执行其中内容，也不回显密钥。

- 模型：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_DEFAULT_MODEL`。本轮验证的是现有 DeepSeek v4.1 Flash 网关。使用兼容 chat/completions 的 HTTP JSON 请求，默认 `enable_thinking=false`；可用 `NOTICE_ENABLE_THINKING=true` 开启。更换提供商时需确认开关及 JSON 输出兼容性。
- PG：`NOTICE_PG_HOST`、`NOTICE_PG_PORT`、`NOTICE_PG_USER`、`NOTICE_PG_PASSWORD`、`NOTICE_PG_DB_NAME`。兼容现有 `.env` 末尾 HOST/PORT/USER/PASSWORD/DB_NAME，文件中的数据库 USER 不会被操作系统 USER 意外替换。
- MySQL：沿用 `KINGDOMAI_DB_CREDENTIAL_SOURCE` 指向的连接 URL（默认 `REPORT_DB_URL`），可用 `KINGDOMAI_DB_HOST`、`KINGDOMAI_DB_PORT` 覆盖地址，目标库固定 kingdomai。这只是复用现有凭据配置，**没有读取或依赖研报数据表**。

JSON + HTTP 核心只用标准库；dotenv 仅用于 `.env`；psycopg 仅用于读 PG；PyMySQL 仅用于写 MySQL；pdfplumber 仅用于 PDF。PDF 库在 Python 进程内运行，无需系统 pdftotext、浏览器或 Node。

## 证据、恢复与边界

输出目录保存 `manifest.json`、代码和提示词快照、source 快照、原始 PDF、每次 LLM 调用的返回/耗时/usage、归一化结果。保留原文引用和 Unicode 半开区间；PDF 有真实物理页映射。合并表格另保存从边框恢复的行列范围，属于有原件可核查的解析表示，不冒称 PDF 原始文本中的标记。图像页的转录和调用记录独立缓存在 `ocr/`，证据标明图像转录及物理页，未把模型 OCR 文字冒充无误的原始数字文本。

模型仅输出内容和段落号；脚本生成 ID、哈希、原文引用、关联关系和版本。校验只阻止无法解析、不可存储、证据地址不存在等确定性问题，不按行业词表判断金融业务对错。结构通过不等于语义正确，尤其不能把“原文中存在这个数”当作主体和口径都正确。

- `ignore` 是筛除低价值材料的结果，不代表错误状态。只忽略无实质业务信息的纯程序内容；不能按法律意见书、会议资料等标题直接跳过。真实议案获批、重大交易条件仍可有价值。
- 空正文自动补 PDF；已有正文可能丢表，可以用 `--pdf` 对照修复。PDF 中有图像且可提取文字少于 30 个非空白字符的页面，自动由现有模型逐页转录，不额外安装 OCR 引擎。转录失败或截断则停止该文档，保留已完成页，下次可续跑；不标成 ignore。
- 文字较多但局部表格为图片的页面、乱码或复杂跨页表仍可能漏信息；识别出的数值和盖章遮挡部分需要抽检。不自动关联跨公告事项、选择最新有效更正值或穷尽长财报所有附注。
- 本地 original_ref 不是线上下载服务；正式调度需持久化输出目录。输出含公告正文和业务数据，默认不提交 Git。
- 本轮是离线数据流程，未把新 notice 表接入生产聊天/MCP；业务问答测试检验结构化材料能否支撑回答，不代表线上入口已经开放。

多个试验批次共存时，查询必须明确 batch/version；不要把人工基准、原生抽取与重抽版本当成多笔业务相加。跨公告更正须结合原文关系和披露时间确认适用值，不能简单按抓取时间取最新。

## 测试和效果复核

```sh
.venv/bin/python -m pytest tests/test_notice_pipeline.py -q
.venv/bin/python scripts/eval_notice_pipeline.py \
  --run outputs/notice_native_20261002/final \
  --reference outputs/notice_pilot_20261002/reviewed \
  --questions tests/evals/notice_business_questions.json --env-file .env
```

参考样本是本会话先前逐篇研读结果，**不是独立专家金标**。数值/单位覆盖率只做缺项诊断，不是准确率；语义问题由实际业务问答和原文复核判断。问题脚本给模型的只有原生抽取结果，不提供参考答案。全链路生产质量和全部子类型的覆盖不能由这组离线样本外推。

建表定义和首批结果见 [公告样本研究](development_tasks/announcement_research_20261002/pilot_results.md)。本轮实际验证结果见 [原生流程复核](development_tasks/announcement_research_20261002/native_review.md)。
