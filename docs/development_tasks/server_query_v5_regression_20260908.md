# 显式 query 协议：服务器四题回归

日期：2026-09-08。实测代码：`654da7d934e2a04d531456557911e61d1df80a82`；对照：`4fdda49` 同日原始评测。

## 结论

四题均完成 MCP 请求，数据与 summary、detail 均返回。按“数据入口合理、查询可执行、零行允许”的口径，四题可以完成交付；但不是四题全程无错误，更不是 summary 的全部陈述均已验证。

- `.query` 已成为正式入口，本次没有方法名解析失败；原先的 `.lower()` 和外层 JSON 错误未复现。
- 阳光电源仍有一次字段错误，静态检查指出 `list_date` 应为 `listed_date`，模型自行修复后完成。
- 蔚来有效查询为空，结束阶段仍尝试追加查询，被 guard 拒绝；没有再次访问数据库。其轮数和 token 反而增加。
- 科大讯飞直接选到研报观点，取得 8 条；不再先查预测指标。
- 本轮平均服务执行时间由 **38.01 秒降至 28.07 秒（约 26%）**。仅四题单次观察，包含模型、缓存和数据库波动，不能外推总体性能。

## 简明对比

耗时使用相同的服务端 execution duration；token 统一为输入（含缓存读取）加输出。

| 原题 | 原轮数→本轮 | 原耗时→本轮（秒） | 原 token→本轮 | 返回行数 | 判断 |
|---|---:|---:|---:|---:|---|
| 网易的净利率变化反映了什么行业趋势？ | 6→5 | 51.47→33.30 | 52,265→40,615 | 0 | 查财务数据合理；无协议错误，但空结果后做了较多身份补查 |
| AI大模型技术发展对科大讯飞的业务增长有何催化作用？ | 5→3 | 35.21→21.33 | 58,010→26,431 | 8 | 直接查研报核心观点，入口与问题相符 |
| 看多蔚来汽车的机构主要理由是什么？ | 5→6 | 27.36→26.42 | 33,134→61,165 | 0 | 有效空结果可接受；结束后多尝试一次工具调用，不是数据协议错误 |
| 阳光电源在光伏行业的竞争格局中处于什么位置？ | 7→6 | 37.98→31.25 | 66,231→72,731 | 25 | 一次字段纠错成功，最终有研报及经营数据；入口加载仍有缺口 |

25 行为基础信息 1、财务 1、业务分部 20、研报 3，不能理解为 25 篇研报。

| 原题编号 | 模型响应累计时间（秒） | Provider 执行累计时间（秒） | 静态错误 |
|---|---:|---:|---|
| RTE009 | 31.71 | 1.34 | 0 |
| RTE010 | 18.62 | 2.55 | 0 |
| RTE017 | 26.07 | 0.18 | 0；另有结束阶段 guard 拒绝 1 次 |
| RTE020 | 30.57 | 0.39 | 1，下一轮修复成功 |

模型时间包含请求准备、推理及生成，不能单独称为“思考耗时”。Provider 时间不是纯 SQL 时间；并行跨度不能与总耗时直接相加。14 个实际进入静态检查的子步骤中 13 个通过，1 个失败后修复；被 guard 拒绝的两个子步骤没有进入静态检查，不计入该分母。

## 关键过程与仍需关注的问题

**网易：**读取 basic_info、financial_3_table 执行包 → 两个有效空查询 → 名称/代码补查 → 再补查 → summary。所有 filter 使用支持的比较、集合和 contains。summary 虽说明无数据，仍加入未经本轮证据支持的净利率及行业背景推断；取数通过不等于这些分析已被验证。

**科大讯飞：**读取 report 与两个业务 Skill → `stock.report.query` → summary，三轮完成。summary 把信达、中邮、东方称为“买方机构”不准确，还夹入“快速回答模式”的过程文字。属于表达质量问题，本轮不据此再追加具体 case 提示。

**蔚来：**report 执行包 → 名称与评级筛选为空 → 港股代码查询为空，同时读取 basic_info → 基础信息为空 → final 阶段仍生成 NIO 名称补查 → guard 拒绝 → summary。原生记录明确是 `disallowed_tool_after_completion`，不是 `.query` 或 filter 解析失败，也不是旧的次数边界 bug。

**阳光电源：**

1. 先读 financial_3_table/query、business_segment/query 和 stock-research Skill，没有读 basic_info 包。
2. 三步 flow 的第一步为 `stock.basic_info.query(...) -> code, name, industry, list_date`，静态检查拒绝；后两步未执行。
3. 下一轮改为 `listed_date`，同一 flow 三步执行成功。
4. 加载 report/query；下一轮取到 3 篇研报，再生成 summary。

这再次印证：当前 readiness 仍是阶段级，读过一个执行包，并不确保本次全部目标 API 的字段契约都已加载。统一命名解决了目录 OP 与调用形式不一致，但没有解决这一加载缺口。当前 loop 可以修复此例；本轮没有通过别名映射、单题补丁或扩大字段列表掩盖它。

## 环境与发布范围

- 在服务器 `/home/che/cyris/fin_agent` 运行，使用服务器现有环境变量，未复制或修改个人/服务端密钥。
- DeepSeek 官方 `deepseek-v4-flash-0731`，DSH，默认 low，阶段策略仍会调整推理强度；standard / research fast / both / detail=true / max_rows=100，并发 2。
- 真正的 HTTP MCP 回环监听，使用临时内存 key；缺失/错误 key 返回 401，有效 key 返回 200。真实模型、真实数据库，不是 mock。
- 独立 runtime 与日志，关闭本次评测的生产使用量记账；不测试公网 Nginx 路由或正式进程热更新。
- 当前修改已推 GitHub、Codeup，服务器代码已同步。正式 Web、Finance API 进程仍需人工完整重启。
- 服务器协议专项 **274 passed**。先前本地扩大范围 **575 passed、2 skipped**，Node **41 passed**；不是全仓库测试结果。
- 另一个任务的未提交研报 Skill adaptive-v2 改动未混入本次发布。

四题结束后测试脚本在关闭未启用的 CC 占位对象时抛出 AttributeError；所有请求和结果此前已经完成。已补齐占位对象的 close，并增加仅鉴权/预热/清理的 preflight，修复只影响评测脚本清理，不改业务运行路径。

服务器原始结果位置：`outputs/server_query_v5_20260908/`（每题 JSON、manifest、events、native 事件）；保留在测试环境，不提交模型内部推理或完整数据结果到 Git。

正式生效操作（本次无需修改 Nginx、数据库、依赖或 .env）：

```bash
sudo systemctl restart fin-agent-web.service fin-agent-finance-api.service
systemctl is-active fin-agent-web.service fin-agent-finance-api.service
```
