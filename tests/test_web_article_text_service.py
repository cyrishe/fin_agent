from src.services.web_article_text_service import extract_article_markdown, fetch_article_markdown


def test_article_extraction_keeps_structure_and_drops_page_noise():
    html = """
    <html><head><meta property="og:title" content="宏观政策观察">
    <meta property="article:published_time" content="2026-09-14T08:00:00+08:00">
    <script>AD_TRACKING_SENTINEL</script></head>
    <body><nav>首页导航</nav><div class="detail-content">
    <div class="share">分享到微信</div><h2>主要变化</h2>
    <p>财政政策继续发力，支持实体经济和产业升级。</p>
    <ul><li>投资规模上升</li><li>结构持续优化</li></ul>
    <p>政策实施仍需观察后续数据，短期与长期影响应分开分析。财政和货币政策需要结合统计期与落实进度解读。企业盈利、消费和投资数据的变化也需要持续核对，跨地区差异也要列入观察。</p>
    </div><footer>版权与友情链接</footer></body></html>
    """
    markdown = extract_article_markdown(html)
    assert markdown.startswith("# 宏观政策观察\n\n发布日期：2026-09-14T08:00:00+08:00")
    assert "## 主要变化" in markdown
    assert "- 投资规模上升" in markdown
    assert "首页导航" not in markdown
    assert "分享到微信" not in markdown
    assert "AD_TRACKING_SENTINEL" not in markdown
    assert len(markdown) < len(html) // 2


def test_article_fetch_only_allows_curated_https_hosts():
    class _Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            class _Response:
                headers = {"Content-Type": "text/html; charset=utf-8"}
                content = b"<h1>Title</h1><div id='artibody'><p>" + b"content " * 30 + b"</p></div>"
                def raise_for_status(self):
                    pass
                def iter_content(self, chunk_size):
                    yield self.content
                def close(self):
                    pass
            return _Response()

    session = _Session()
    assert fetch_article_markdown("http://finance.sina.com.cn/a", session=session) == ""
    assert fetch_article_markdown("https://finance.sina.com.cn.evil.test/a", session=session) == ""
    assert not session.calls
    assert "content" in fetch_article_markdown("https://finance.sina.com.cn/a", session=session)
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[0][1]["stream"] is True


def test_securities_times_header_date_is_preserved():
    html = """
    <html><head><title>业绩增长</title></head><body>
    <div class="detail-info"><span>来源：人民财讯</span><span>2026-08-27 17:03</span></div>
    <div class="article-content"><p>三环集团公布半年报，主营业务产品需求持续增长。</p>
    <p>报告期内电子元件和光通信产品的销售额都有增长，相关产品的市场需求和订单持续改善。</p>
        <p>公司同时披露了产能扩张和后续研发安排，市场仍需关注具体执行进度及下一期财务数据。</p>
        <p>分析时还应对比同行公司的订单和盈利变化，并核对公告日期与所引用数字的统计口径。</p></div>
    </body></html>
    """
    assert "发布日期：2026-08-27 17:03" in extract_article_markdown(html)


def test_sina_mobile_redirect_is_bounded_and_keeps_short_news():
    class Response:
        def __init__(self, target=None):
            self.status_code = 302 if target else 200
            self.headers = {"Location": target} if target else {"Content-Type": "text/html"}
        def raise_for_status(self): pass
        def close(self): pass
        def iter_content(self, chunk_size):
            yield '<h1>快讯</h1><div id="artibody"><p>被动元件板块走高，多家公司的股价随行业消息上涨。</p></div>'.encode()
    class Session:
        def __init__(self, target): self.target, self.calls = target, []
        def get(self, url, **kwargs):
            self.calls.append(url)
            return Response(self.target) if len(self.calls) == 1 else Response()
    allowed = Session("https://cj.sina.com.cn/article")
    assert "行业消息" in fetch_article_markdown("https://cj.sina.cn/article", session=allowed)
    blocked = Session("http://127.0.0.1/private")
    assert fetch_article_markdown("https://cj.sina.cn/article", session=blocked) == ""
    assert len(blocked.calls) == 1


def test_sina_article_and_flash_news_publication_dates():
    body = '<div id="artibody"><p>被动元件板块走高，多家公司的股价随行业消息上涨。</p></div>'
    mobile = '<meta property="article:published_time" content=""><span class="date">2026年09月14日 11:38</span>'
    flash = '<meta name="bytedance:published_time" content="2026-09-14T09:41:33+08:00">'
    assert "发布日期：2026-09-14 11:38" in extract_article_markdown(mobile + body)
    assert "发布日期：2026-09-14T09:41:33+08:00" in extract_article_markdown(flash + body)
