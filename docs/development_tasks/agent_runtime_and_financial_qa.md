# Agent Runtime 与 Financial QA 开发任务包

负责人建议：Agent Runtime / Finance CC Agent。本文区分本轮已修复项和仍待开发项，不把日志、类名或入口测试等同于产品能力。

## 当前主线

```text
身份与会话
  → 显式资产调用判断
  → 顶层 LLM 语义路由
  → investment_analyst.normal_qa
  → Finance CC
  → Business Skill / 金融数据 Tool
  → result_ref / evidence
  → Finance Renderer
```

这条主线无需重写。普通金融问题由 Finance CC 总控；明确 `$Tool/$Skill` 才进入资产调用；HARD 只持有 owner、thread、asset、revision、result ref 和权限，理解与表达继续由 SOFT 负责。

## 本轮已经修复

### AR-FIX-01 Thread 与 Attachment owner 边界

- 复用已有 `thread_id` 前统一验证 `owner_type + owner_id`。
- missing 与 foreign thread 使用相同拒绝信息，避免枚举。
- chat、显式资产调用和历史图片引用都以当前 owner 读取附件。
- mixed batch 在严格入口整体拒绝；历史图片失效继续走“请重新上传”的自然语义。

仍需补 HTTP/SSE 入口级攻击回归：foreign payload/cookie thread、foreign stream run、mixed-owner attachment。

### AR-FIX-02 有语义的会话窗口

根因不是单纯的截断长度，而是旧 fallback 只保存“本轮已给出回答”，随后上下文又截到 10 字。

本轮已改为：

- 无额外 LLM 调用，确定性保留对象、日期、数值和结论；
- assistant 历史预览上限约 360 字，不带回整表；
- sync、普通 stream 和显式资产 stream 使用同一 summary helper；
- 显式资产 compact context 读取真实的 `role/text` 结构，并保留最近 user/assistant turn。

### AR-FIX-03 Finance CC 正式回答不再被日志截断

正式 `result` 保留完整文本；仅写入 observability JSONL 的副本限制为 2000 字。日志容量政策不得改变用户答案。

### AR-FIX-04 Context Resolution 空历史快捷路径

根因：首轮没有 context window、reference objects、历史附件和活动工作流时，仍先调用 ContextResolution LLM，随后顶层路由再调用一次 LLM。

已实现：没有任何可解析上下文时，ContextResolution 直接返回原问题；Agent 和 turn mode 仍由顶层 LLM 决定。

验收：

- 首轮完整问题少一次无意义模型调用。
- “和五日前比呢”“第二个呢”仍进入语义解析。
- 话题切换不会被旧金融上下文强行续接。
- 不新增“这个/第二个/刚才”关键词分支。

## AR-P1-02 Finance CC 输出与过程完整性

### 冷 client 过程事件

冷 client 创建工具 runtime 时使用 `event_sink=None`；只有复用或预热 client 才重新 `begin_turn(event_sink=...)`。预热失败、池耗尽或配置变化后，目录读取和金融查询可能没有中间反馈。

本轮已修复：cold client 完成构建后使用本轮真实 `event_sink` 重新 `begin_turn`，与 prewarmed/reused 路径保持同一 tracker 语义。

验收：cold、prewarmed、reused 三条路径产生一致的用户可见工具进度。

### 自动 K 线数据边界

companion evidence 固定请求最近 22 个已完成日线，但没有清楚展示真实起止日期，也未对比实时行情日期。

验收：

- 补图查询发送 running/completed 事件。
- 图表显示真实 `min_date ~ max_date`，并注明“已完成日线”。
- 实时行情比日线新一个交易日时自然说明；滞后两个以上交易日时显式提示数据边界或不展示。
- 补图失败不影响主答案。

### 能力声明与真实权限

`investment_analyst`、prompt 与 runtime 对研报原文检索的声明不一致。要么将工具纳入 supplementary permission 交集，要么删除能力承诺；不能只在提示词中声称可用。

## AR-P1-03 多用户 StreamRunRegistry 与 CC Pool

当前 stream start 将完整 payload 放进进程全局字典，GET 只凭 `run_id` claim；缺 owner 复核、TTL、容量和多 worker 一致性。CC pool 在所有 client busy 时也可能继续创建，`max_live_clients` 不是硬上限；prewarm 仅由本地 `run_server()` 触发。

验收：

- 抽象 `StreamRunRegistry(run_id, owner_id, created_at, expires_at, payload)`。
- GET 再解析身份并核对 owner；foreign、expired、missing 使用同一公共错误。
- TTL、最大 pending、原子 claim、重复 claim 拒绝。
- 本地可先用带锁内存实现；生产后端可替换 Redis/数据库。
- CC 使用信号量或 admission queue，实际 client 数不超过硬上限。
- 等待时有“等待可用分析会话”进度。
- pool 初始化/关闭进入应用 lifecycle，支持 WSGI/Gunicorn。
- 同 session 串行、不同 session 并行，shutdown 可唤醒等待者。

## AR-P1-04 系统参考资产内存 Snapshot

请求热路径仍反复读取：Finance Skill catalog/frontmatter、Finance CC system/context、金融 API catalog 和 prompt template。CC 恢复还会遍历变量并加载完整结果。

目标：文件仍是编辑与发布资产，请求只读已发布的不可变内存 snapshot。

验收：

- snapshot 带 revision/content hash；发布后原子切换。
- Skill/catalog/prompt 连续 100 轮的文件读取接近初始化/发布次数，而非轮次数。
- API catalog 写入后主动失效，下一请求读取新 revision。
- CC 冷恢复只加载 result index/manifest；引用 `rN` 时再按需读取全量。
- 多实例加载同一 revision 得到同一内容。
- 不在各模块各造一套无失效规则的缓存。

## AR-P1-05 Runtime Storage Inventory 与 Retention

现有 session variables、runtime artifacts、CC sessions 和 custom-tool context 已达到 GB 级。先做只读 inventory 和 dry-run，不直接增加自动删除。

验收：

- 列出 owner、thread、asset revision、result ref 的引用关系和存储占用。
- 区分 active、orphan、expired、candidate revision 和正式资产。
- retention 具有 TTL、owner quota、幂等、审计和 dry-run。
- session variable 与其 artifact 按引用图一起处理。
- 不破坏活动 thread、分页结果、有效 candidate revision 和 CC resume。

## AR-P1-06 普通金融附件入口

当前图片有 vision path，`$资产 + 附件` 有显式调用 path；但普通“分析这份财报/Excel”不会进入 Finance CC 的真实分析主线。

验收：

- 附件先解析为 owner-scoped artifact/ref、schema 与有限摘要。
- Finance CC 只拿引用，通过受控只读工具按需读取，不把全文/大表注入 prompt。
- PDF/Word/Excel 有明确支持边界；未支持格式在执行前解释。
- 增加单文件、请求总量、文件数和解压后资源上限。
- 现有 `$Tool + Excel` 不回归。

## AR-P1-07 Finance QA 全链路 Eval Gate

入口正确只说明 route/Skill entrance，不能说明执行正确。

full execution 至少记录：

- 顶层路由、Finance Skill 与最终答案是否存在；
- provider/tool/error；
- 总轮次、工具轮次、有效轮次、错误修正轮次；
- route、catalog、query、companion、render 各阶段耗时；
- 结果正确性、日期口径和证据边界。

不追求 100% 一次调用；目标是识别明确重复、资料未读造成的错误重试和无反馈长尾。

## 当前性能参考（历史混合版本日志）

对 244 条成功 Finance CC 日志的只读统计：

```text
median duration        22.0 s
p90                    57.9 s
max                   159.6 s
> 60 s                 23 / 244
mean tool calls         4.72
> 10 tool calls        21 / 244
prewarmed              221 / 244
```

这些日志跨多个代码和 prompt 版本，不能当当前回归成绩；但它们说明冷启动已不是唯一长尾，目录读取、查询修正、分页和多轮模型决策更值得逐项测量。

## 推荐顺序

```text
入口 owner 回归
  → Finance CC cold progress / K 线边界
  → StreamRunRegistry / Pool 硬上限
  → 不可变参考 Snapshot
  → Storage dry-run retention
  → 普通金融附件纵切面
  → Full-execution Eval Gate
```
