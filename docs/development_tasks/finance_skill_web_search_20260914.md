# 金融 Skill 外部搜索接入

Skill 使用既有 allowed-tools: mcp__finance__general_search 独立申请搜索能力。当前开启的系统方法：市场概览、板块热点、个股研究、个股研报、股票比较、基金分析、债券分析。其他方法仍可使用结构化金融数据。allowed-tools 是方法元数据，实际可调用还需 Agent profile 授权 general_search；Studio 只读展示申请状态。

general_search 沿用一个调用契约和 provider/coverage 返回字段：

- FIN_AGENT_SEARCH_PROVIDER=elasticsearch：内部新闻 ES，默认 coverage=internal_news。
- FIN_AGENT_SEARCH_PROVIDER=site_browser：免 API key 的定点公开站点搜索，默认 coverage=curated_public_web。当前用无头 Chrome 搜索新浪财经、证券时报，再以 HTTPS 读取正文，生成有长度上限的标题、发布日期和摘要；每站最多 6 条、总体最多读取 12 篇，2 个搜索页并行。不是全网搜索。
- FIN_AGENT_SEARCH_PROVIDER=brave：Brave Search Web API，默认 coverage=public_web，需服务端设置 FIN_AGENT_SEARCH_BRAVE_API_KEY。此模式不依赖内部 ES。
- 可选 FIN_AGENT_SEARCH_SEED_DOMAINS=sse.com.cn,szse.cn,cninfo.com.cn 把检索限定到这些站点。调用时 source_scope 可继续收窄到配置内的域名；未设置种子时允许公网搜索并可按 source_scope 限域。域名与返回 URL 均经校验，匹配不到种子域名返回空结果。

财经新闻来源组 source_scope=["finance_news"] 包含新浪财经 finance.sina.com.cn、上海证券报·中国证券网 cnstock.com、证券时报 stcn.com、中国证券报 cs.com.cn、证券日报 zqrb.cn。它是可选的搜索范围，不强制限制其他股票、基金或债券问题。若部署时还设置了全局 seed domains，要将这些媒体域名纳入全局列表后才能使用该组。

site_browser 只支持其中已验证可检索且可提取正文的新浪财经和证券时报；上海证券报搜索页已能发现链接，但新版详情页正文提取未打通，所以暂不纳入这个 provider。source_scope 可以用 finance_news、finance.sina.com.cn 或 stcn.com；其他域名会明确返回 invalid_request。该模式依赖服务器有 Chrome/Chromium 和兼容的 ChromeDriver/Selenium Manager；免费指无搜索 API 费用，不保证源站稳定、检索完整性或无运维成本。

需要正文时 general_search 可传 include_content=true：搜索后并行读取前两条受支持站点的 HTTPS HTML，每页最多读取 1 MB，提取的标题、发布日期、段落、标题层级和列表合计最多 5000 字符；导航、脚本、分享区、评论和广告区被去除。默认不开启正文读取以缩短普通检索的耗时。抓取失败或不是正文页时仅保留搜索摘要，不把其冒充原文。

general_search 默认 `sort=date_desc`，也支持 `date_asc`、`relevance`。Brave 按日期排序时最多先取 20 个候选，优先读取财经媒体文章发布日期；无法核对文章日期的页面仅以 Brave 的 `page_age` 作排序参考，`publish_time` 保持空。使用 `source_scope=["finance_news"]` 时，无文章日期的页面不进入最近新闻结果。`start_time`、`end_time` 可单独或一起传入；同时提供时还会传给 Brave 的 `freshness`。这保证返回列表在已召回候选内按日期降序，不保证搜索引擎索引已经覆盖最新文章或结果与标的直接相关。

中文查询会向 Brave 传 `country=CN`、`search_lang=zh-hans`、`ui_lang=zh-CN`；英文查询保持 Brave 默认语言。根据 Brave 官方 API 定义，这三项分别控制结果地区、搜索语言和响应界面语言，并非严格的大陆简体中文内容过滤。2026-09-14 本机同词对照：原句“三环集团近期上涨原因”在 2026-09-10 至 09-14 时间窗中，设置这三项后仍出现无关股票结果；再加 `lang:zh` 虽使返回结果语言均标为 `zh`，仍混有繁体和无关页面，因此未启用该运算符。官方 `inpage:"三环集团"` 与精确短语查询能减少候选，但结果仍混入同名港股 06951，不能取代证券代码核对。业务相关性判断与多查询应交给上层 Skill，不在基础搜索工具中写个股关键词过滤。

2026-09-14 对用户原句“三环集团近期上涨原因”设 2026-08-15 至 2026-09-14、财经媒体范围，本机 Brave 实测按发布日期排序后只有 2026-08-28 的三环集团半年报文章；同句定点站点搜索返回 2026-09-12 管理层文章和 2026-08-17 ESG 文章。两者都未凭原句稳定召回当日股价报道，不能据此宣称已经完整找到上涨原因。一次本机 Brave TLS 握手失败可由 curl 兼容路径恢复，key 经 stdin 传递而非进程参数。

固定 seed 是搜索范围，不是事实来源保证。Brave 返回标题、链接和摘要；机构预测、债券条款、基金费率等结论仍需核对原始披露和日期。搜索失败会返回 provider_error，不会静默回退为 ES 结果。生产部署需要运维把 key 写入服务端环境并重启服务；仓库不保存 key。

本地验证：pytest -q tests/test_finance_skill_search_capability.py tests/test_search_gateway_and_stock_news.py tests/test_financial_qa_cc_scenario.py。Brave provider 使用模拟 HTTP 响应验证站点过滤、参数和缺 key；真实公网返回须在完成服务端配置后再做端到端验收。

2026-09-14 本机实测 site_browser：以“三环集团”检索、2026-09-01 起筛选，约 10 秒返回 3 条新浪财经正文；“三环集团股价上涨原因”约 11 秒返回 3 条，包含当日“早盘涨超6%”报道。证券时报也找到结果，但该问题的相关结果日期早于筛选起点。注意该新浪报道标记为港股 06951，不能未经证券代码核对就用于 A 股 300408 的涨跌归因。此为本机公开网页验证，不是服务器部署或长稳证据。
