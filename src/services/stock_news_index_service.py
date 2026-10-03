"""Read bounded, dated headline candidates from stock-specific news indexes."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.services.web_article_text_service import _host_allowed, fetch_finance_html


def sina_stock_index_url(code: str) -> str:
    value = code.strip().lower()
    suffix = re.fullmatch(r"(\d{6})\.(sh|sz|bj)", value)
    if suffix:
        value = suffix[2] + suffix[1]
    if not re.fullmatch(r"(?:sh|sz|bj)\d{6}", value):
        raise ValueError("stock_code 需包含市场，如 300408.SZ 或 sz300408")
    return f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{value}.phtml"


def read_stock_news_index(code: str, *, limit: int = 20, start_time: str = "", end_time: str = "") -> dict:
    url = sina_stock_index_url(code)
    html = fetch_finance_html(url)
    if not html:
        raise ValueError("个股资讯目录读取失败")
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".datelist")
    if container is None:
        raise ValueError("页面未提供可识别的资讯目录")
    items, seen = [], set()
    for anchor in container.select("a[href]"):
        previous = anchor.previous_sibling
        stamp = re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}", str(previous or ""))
        link = urljoin(url, str(anchor.get("href") or ""))
        title = anchor.get_text(" ", strip=True)
        if not stamp or not title or link in seen or not _host_allowed(link):
            continue
        published = re.sub(r"\s+", " ", stamp[0])
        if start_time and published[:10] < start_time[:10]:
            continue
        if end_time and published[:10] > end_time[:10]:
            continue
        seen.add(link)
        items.append({"document_id": link, "title": title, "url": link,
                      "source": urlparse(link).hostname, "publish_time": published,
                      "snippet": "", "category": "news", "score": 0.0})
    items.sort(key=lambda item: item["publish_time"], reverse=True)
    return {"items": items[:limit], "total": len(items), "index_url": url}
