"""Bounded article-to-Markdown extraction for curated financial news sites."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


NEWS_DOMAINS = (
    "finance.sina.com.cn",  # 新浪财经
    "cnstock.com",           # 上海证券报·中国证券网
    "stcn.com",              # 证券时报
    "cs.com.cn",             # 中国证券报·中证网
    "zqrb.cn",               # 证券日报
)

_BODY_SELECTORS = (
    "#artibody", ".detail-content", "#qmt_content_div", ".article-content", ".article-body",
    "#article-content", "#articleContent", ".news-content", "article",
)
_NOISE_SELECTORS = (
    "script", "style", "noscript", "iframe", "nav", "footer", "aside", "form",
    ".share", ".sharing", ".comment", ".comments", ".recommend", ".related",
    ".advertisement", ".advert", ".ad", ".article-tools", ".article-toolbar",
)
MAX_HTML_BYTES = 1_000_000
MAX_MARKDOWN_CHARS = 5_000
ARTICLE_DOMAINS = (*NEWS_DOMAINS, "cj.sina.cn", "cj.sina.com.cn", "k.sina.cn", "finance.sina.cn")


def _host_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    return parsed.scheme == "https" and not parsed.username and not parsed.password and port in (None, 443) and any(
        host == domain or host.endswith("." + domain) for domain in ARTICLE_DOMAINS
    )


def extract_article_markdown(html: bytes | str, *, max_chars: int = MAX_MARKDOWN_CHARS) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select(", ".join(_NOISE_SELECTORS)):
        node.decompose()
    body = next(
        (node for selector in _BODY_SELECTORS if (node := soup.select_one(selector))
         and len(node.get_text(" ", strip=True)) >= 20),
        None,
    )
    if body is None:
        return ""
    lines: list[str] = []
    title = soup.find("h1") or soup.find("meta", attrs={"property": "og:title"}) or soup.title
    title_text = title.get("content", "") if title and title.name == "meta" else title.get_text(" ", strip=True) if title else ""
    if title_text:
        lines.append("# " + re.sub(r"\s+", " ", title_text).split("_新浪财经")[0].strip())
    published = next((node for node in soup.select('meta[property="article:published_time"], meta[name="bytedance:published_time"]') if node.get("content")), None)
    if published and published.get("content"):
        lines.append("发布日期：" + published["content"])
    else:
        # Securities Times exposes the timestamp in the article header, not a meta tag.
        info = soup.select_one(".detail-info, .date-source, .art_time, .time-source, span.date")
        date_text = info.get_text(" ", strip=True).replace("年", "-").replace("月", "-").replace("日", "") if info else ""
        match = re.search(r"20\d{2}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?", date_text)
        if match:
            lines.append("发布日期：" + match.group(0))
    for node in body.find_all(["h2", "h3", "p", "li", "blockquote"]) or [body]:
        if node.name == "p" and node.find_parent(("li", "blockquote")):
            continue
        value = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if not value or value == title_text:
            continue
        if node.name == "blockquote" and value.startswith("炒股就看"):
            continue
        prefix = {"h2": "## ", "h3": "### ", "li": "- ", "blockquote": "> "}.get(node.name, "")
        line = prefix + value
        if lines and lines[-1] == line:
            continue
        if sum(len(item) + 2 for item in lines) + len(line) > max_chars:
            break
        lines.append(line)
    return "\n\n".join(lines)


def fetch_finance_html(url: str, *, session: requests.Session | None = None) -> bytes:
    if not _host_allowed(url):
        return b""
    client = session or requests.Session()
    response = None
    try:
        for attempt in range(4):
            response = client.get(
                url, timeout=4, stream=True, allow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0 (compatible; FinAgentArticleReader/1.0)"},
            )
            if getattr(response, "status_code", 200) not in (301, 302, 303, 307, 308):
                break
            target = urljoin(url, response.headers.get("Location", ""))
            if attempt == 3 or not _host_allowed(target):
                return b""
            response.close()
            url = target
        response.raise_for_status()
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            return b""
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=64_000):
            size += len(chunk)
            if size > MAX_HTML_BYTES:
                return b""
            chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException:
        return b""
    finally:
        if response is not None:
            response.close()


def fetch_article_markdown(url: str, *, session: requests.Session | None = None) -> str:
    html = fetch_finance_html(url, session=session)
    return extract_article_markdown(html) if html else ""
