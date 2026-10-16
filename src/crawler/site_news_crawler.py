import argparse
import concurrent.futures
import datetime
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4 import Tag
import requests
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.selenium_manager import SeleniumManager


@dataclass
class SiteConfig:
    name: str
    enabled: bool
    search_url_template: str
    allowed_domains: List[str]
    result_css_selectors: List[str]
    result_link_patterns: List[str]
    result_item_selector: str
    result_item_link_selector: str
    result_item_time_selector: str
    search_result_keep_days: int
    article_content_selectors: List[str]
    article_remove_selectors: List[str]
    follow_css_selectors: List[str]
    follow_link_patterns: List[str]
    max_results_per_site: int
    max_follow_links_per_page: int
    search_wait_seconds: float
    article_wait_seconds: float
    keyword_mode: str
    search_scroll_times: int
    search_scroll_pause: float
    use_nuxt_article_id_fallback: bool
    entry_mode: str = "search_page"
    seed_urls: Optional[List[str]] = None
    article_title_selectors: Optional[List[str]] = None
    article_time_selectors: Optional[List[str]] = None
    article_fallback_modes: Optional[List[str]] = None
    fetch_mode: str = "selenium"

    @classmethod
    def from_dict(cls, item: Dict[str, Any]) -> "SiteConfig":
        result_cfg = item.get("result") if isinstance(item.get("result"), dict) else {}
        article_cfg = item.get("article") if isinstance(item.get("article"), dict) else {}
        follow_cfg = item.get("follow") if isinstance(item.get("follow"), dict) else {}
        fetch_cfg = item.get("fetch") if isinstance(item.get("fetch"), dict) else {}

        def _list_value(*candidates: Any, default: Optional[List[str]] = None) -> List[str]:
            for candidate in candidates:
                if isinstance(candidate, list):
                    return [str(x).strip() for x in candidate if str(x).strip()]
                if isinstance(candidate, str) and candidate.strip():
                    return [candidate.strip()]
            return list(default or [])

        def _str_value(*candidates: Any, default: str = "") -> str:
            for candidate in candidates:
                if candidate is None:
                    continue
                text = str(candidate).strip()
                if text:
                    return text
            return default

        def _int_value(*candidates: Any, default: int = 0, minimum: Optional[int] = None) -> int:
            for candidate in candidates:
                if candidate is None or candidate == "":
                    continue
                try:
                    value = int(candidate)
                    if minimum is not None:
                        value = max(minimum, value)
                    return value
                except (TypeError, ValueError):
                    continue
            return default

        def _float_value(*candidates: Any, default: float = 0.0) -> float:
            for candidate in candidates:
                if candidate is None or candidate == "":
                    continue
                try:
                    return float(candidate)
                except (TypeError, ValueError):
                    continue
            return default

        return cls(
            name=item["name"],
            enabled=bool(item.get("enabled", True)),
            search_url_template=_str_value(item.get("search_url_template"), item.get("entry_url"), default=""),
            allowed_domains=_list_value(item.get("allowed_domains")),
            result_css_selectors=_list_value(
                item.get("result_css_selectors"),
                result_cfg.get("link_selector"),
                default=["a[href]"],
            ),
            result_link_patterns=_list_value(item.get("result_link_patterns"), result_cfg.get("link_patterns")),
            result_item_selector=_str_value(item.get("result_item_selector"), result_cfg.get("item_selector")),
            result_item_link_selector=_str_value(item.get("result_item_link_selector"), result_cfg.get("item_link_selector")),
            result_item_time_selector=_str_value(item.get("result_item_time_selector"), result_cfg.get("time_selector")),
            search_result_keep_days=_int_value(item.get("search_result_keep_days"), result_cfg.get("keep_days"), default=0, minimum=0),
            article_content_selectors=_list_value(item.get("article_content_selectors"), article_cfg.get("content_selector")),
            article_remove_selectors=_list_value(item.get("article_remove_selectors"), article_cfg.get("remove_selector")),
            follow_css_selectors=_list_value(
                item.get("follow_css_selectors"),
                follow_cfg.get("link_selector"),
                default=["a[href]"],
            ),
            follow_link_patterns=_list_value(item.get("follow_link_patterns"), follow_cfg.get("link_patterns")),
            max_results_per_site=_int_value(item.get("max_results_per_site"), result_cfg.get("max_links"), default=10, minimum=1),
            max_follow_links_per_page=_int_value(item.get("max_follow_links_per_page"), follow_cfg.get("max_links_per_page"), default=5, minimum=0),
            search_wait_seconds=_float_value(item.get("search_wait_seconds"), fetch_cfg.get("search_wait_seconds"), default=2.5),
            article_wait_seconds=_float_value(item.get("article_wait_seconds"), fetch_cfg.get("article_wait_seconds"), default=2.0),
            keyword_mode=_str_value(item.get("keyword_mode"), default="raw"),
            search_scroll_times=_int_value(item.get("search_scroll_times"), result_cfg.get("scroll_times"), default=0, minimum=0),
            search_scroll_pause=_float_value(item.get("search_scroll_pause"), result_cfg.get("scroll_pause"), default=1.0),
            use_nuxt_article_id_fallback=bool(item.get("use_nuxt_article_id_fallback", False)),
            entry_mode=_str_value(item.get("entry_mode"), default="search_page").lower(),
            seed_urls=_list_value(item.get("seed_urls"), item.get("entry_urls")),
            article_title_selectors=_list_value(article_cfg.get("title_selector"), default=[]),
            article_time_selectors=_list_value(article_cfg.get("time_selector"), default=[]),
            article_fallback_modes=_list_value(article_cfg.get("fallback_mode"), default=["next_data", "nuxt_content", "full_page"]),
            fetch_mode=_str_value(fetch_cfg.get("mode"), item.get("fetch_mode"), default="selenium").lower(),
        )


def load_site_configs(path: str) -> List[SiteConfig]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw_sites = data.get("sites", data if isinstance(data, list) else [])
    return [SiteConfig.from_dict(item) for item in raw_sites]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def _is_allowed_domain(url: str, allowed_domains: Sequence[str]) -> bool:
    if not allowed_domains:
        return True
    domain = _domain_of(url)
    return any(domain == d or domain.endswith(f".{d}") for d in allowed_domains)


def _match_patterns(url: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    return any(re.search(pattern, url) for pattern in patterns)


def _first_text_by_selectors(soup: BeautifulSoup, selectors: Sequence[str]) -> str:
    for selector in selectors:
        if not str(selector or "").strip():
            continue
        node = soup.select_one(selector)
        if not node:
            continue
        text = _normalize_text(node.get_text(" ", strip=True))
        if text:
            return text
    return ""


_NOISE_CLASS_OR_ID_RE = re.compile(
    r"(chart|kline|quote|行情|stock|sidebar|side|footer|nav|menu|toolbar|recommend|related|tag|advert|ad-)",
    re.IGNORECASE,
)
_MARKET_WIDGET_TEXT_RE = re.compile(
    r"(最新价|涨跌额|涨跌幅|成交量|成交额|换手率|市盈率|总市值|相关股票|相关板块|查询该股行情|实时资金流向|深度数据揭秘)",
    re.IGNORECASE,
)
_PAGE_CHROME_TEXT_RE = re.compile(
    r"(主管主办|网站信息|联系我们|相关链接|登录\s*\|\s*注册|电子报|客户端|我要举报)",
    re.IGNORECASE,
)


def _node_text_len(node: Tag) -> int:
    return len(_normalize_text(node.get_text(" ", strip=True)))


def _link_density(node: Tag) -> float:
    text_len = _node_text_len(node)
    if text_len == 0:
        return 0.0
    link_text = " ".join(a.get_text(" ", strip=True) for a in node.select("a"))
    link_len = len(_normalize_text(link_text))
    return link_len / max(1, text_len)


def _is_noise_block(node: Tag) -> bool:
    attrs_dict = getattr(node, "attrs", None) or {}
    attrs = " ".join(
        [
            str(attrs_dict.get("id") or ""),
            " ".join(
                attrs_dict.get("class", [])
                if isinstance(attrs_dict.get("class"), list)
                else [str(attrs_dict.get("class") or "")]
            ),
        ]
    )
    attrs = attrs.strip()
    text = _normalize_text(node.get_text(" ", strip=True))

    if attrs and _NOISE_CLASS_OR_ID_RE.search(attrs):
        return True
    if text and _MARKET_WIDGET_TEXT_RE.search(text) and _link_density(node) > 0.25:
        return True
    # 通用降噪：链接密度极高且文本较长，通常是导航/相关推荐/行情组件
    if _node_text_len(node) >= 80 and _link_density(node) >= 0.6:
        return True
    return False


def _clean_content_node(node: Tag) -> None:
    # 先按结构特征清掉噪音块，再做空节点清理
    for sub in list(node.find_all(True)):
        if _is_noise_block(sub):
            sub.decompose()
    for sub in list(node.find_all(True)):
        if not _normalize_text(sub.get_text(" ", strip=True)):
            sub.decompose()


def _looks_like_page_chrome(text: str) -> bool:
    if not text:
        return False
    return len(_PAGE_CHROME_TEXT_RE.findall(text)) >= 2


def _extract_nuxt_content(raw_html: str) -> Optional[Tuple[str, str]]:
    """
    部分 Nuxt SSR 页面正文不直接出现在可见节点里，而在 window.__NUXT__ 的 content 字段。
    这里做一个通用兜底解析。
    """
    m = re.search(r'"content":"(.*?)","url":', raw_html, flags=re.DOTALL)
    if not m:
        return None
    escaped = m.group(1)
    try:
        html_fragment = json.loads(f'"{escaped}"')
    except Exception:
        return None
    if not html_fragment:
        return None
    soup = BeautifulSoup(html_fragment, "html.parser")
    text = _normalize_text(soup.get_text(" ", strip=True))
    if not text:
        return None
    return text, str(soup)


def _extract_next_data_content(raw_html: str) -> Optional[Tuple[str, str]]:
    """
    部分 Next.js 页面把正文放在 __NEXT_DATA__ 的 pageProps 里，DOM 中只有空容器。
    """
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        raw_html,
        flags=re.DOTALL,
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None

    page_props = ((data.get("props") or {}).get("pageProps") or {})
    payload_candidates = [
        page_props.get("data"),
        page_props,
    ]
    html_fragment = ""
    for payload in payload_candidates:
        if not isinstance(payload, dict):
            continue
        text_info = payload.get("textInfo")
        if isinstance(text_info, dict):
            html_fragment = str(text_info.get("content") or "").strip()
        if not html_fragment:
            html_fragment = str(payload.get("content") or "").strip()
        if html_fragment:
            break
    if not html_fragment:
        return None

    soup = BeautifulSoup(html_fragment, "html.parser")
    text = _normalize_text(soup.get_text(" ", strip=True))
    if not text:
        return None
    return text, str(soup)


def _extract_nuxt_article_urls(raw_html: str, base_url: str) -> List[str]:
    """
    从 Nuxt 状态里提取 article_id，拼成 /a/{id} 详情链接。
    """
    ids = re.findall(r'article_id:"([a-zA-Z0-9]+)"', raw_html)
    if not ids:
        ids = re.findall(r'"article_id":"([a-zA-Z0-9]+)"', raw_html)
    urls = [urljoin(base_url, f"/a/{aid}") for aid in ids]
    return list(dict.fromkeys(urls))


def _parse_time_text(text: str, now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    if not text:
        return None
    now = now or datetime.datetime.now()
    raw = _normalize_text(text)
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m-%d %H:%M:%S",
        "%m-%d %H:%M",
        "%m-%d",
    ]
    for fmt in formats:
        try:
            parsed = datetime.datetime.strptime(raw, fmt)
            if fmt.startswith("%m-%d"):
                parsed = parsed.replace(year=now.year)
                if parsed > now + datetime.timedelta(days=1):
                    parsed = parsed.replace(year=now.year - 1)
            return parsed
        except ValueError:
            continue
    return None


class SiteNewsCrawler:
    def __init__(
        self,
        sites: List[SiteConfig],
        code_resolver: Optional["StockCodeResolver"] = None,
        workers: int = 4,
        site_search_workers: int = 3,
        per_site_workers: int = 1,
        page_load_timeout: int = 30,
        headless: bool = True,
    ):
        self.sites = [s for s in sites if s.enabled]
        self.workers = max(1, int(workers))
        self.site_search_workers = max(1, int(site_search_workers))
        self.per_site_workers = max(1, int(per_site_workers))
        self.page_load_timeout = page_load_timeout
        self.headless = headless
        self.code_resolver = code_resolver
        self._visited_lock = threading.Lock()
        self._visited_urls = set()
        self._site_locks = {
            s.name: threading.Semaphore(self.per_site_workers)
            for s in self.sites
        }
        self.last_search_errors: Dict[str, str] = {}
        self.last_search_counts: Dict[str, int] = {}

    def _resolve_site_keyword(self, keyword: str, site: SiteConfig) -> str:
        if site.keyword_mode == "stock_code_6":
            if not self.code_resolver:
                return keyword
            code = self.code_resolver.resolve_to_6digit(keyword)
            return code or keyword
        return keyword

    def _create_driver(self) -> webdriver.Chrome:
        options = webdriver.ChromeOptions()
        options.page_load_strategy = "eager"
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        try:
            driver = webdriver.Chrome(options=options)
        except WebDriverException:
            managed_paths = SeleniumManager().binary_paths(
                ["--browser", "chrome", "--skip-driver-in-path"]
            )
            driver = webdriver.Chrome(
                service=ChromeService(managed_paths["driver_path"]),
                options=options,
            )
        driver.set_page_load_timeout(self.page_load_timeout)
        return driver

    @staticmethod
    def _http_get(url: str, timeout: int) -> str:
        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        response.raise_for_status()
        return response.text

    def _render(
        self,
        url: str,
        wait_seconds: float,
        scroll_times: int = 0,
        scroll_pause: float = 1.0,
    ) -> str:
        driver = self._create_driver()
        try:
            try:
                driver.get(url)
            except TimeoutException:
                print(f"[Warn] render timeout: {url}, skipped")
                return ""

            time.sleep(wait_seconds)
            for _ in range(max(0, int(scroll_times))):
                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(max(0.2, float(scroll_pause)))
                except WebDriverException:
                    break

            try:
                return driver.page_source or ""
            except WebDriverException:
                return ""
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def _fetch_page(
        self,
        url: str,
        wait_seconds: float,
        site: SiteConfig,
        *,
        is_search: bool = False,
    ) -> str:
        if str(site.fetch_mode or "").strip().lower() == "requests":
            try:
                return self._http_get(url, timeout=max(5, int(self.page_load_timeout)))
            except Exception as exc:
                print(f"[Warn] request fetch failed: {url} error={exc}")
                return ""
        return self._render(
            url,
            wait_seconds,
            scroll_times=site.search_scroll_times if is_search else 0,
            scroll_pause=site.search_scroll_pause,
        )

    def _extract_links(
        self,
        html: str,
        base_url: str,
        css_selectors: Sequence[str],
        link_patterns: Sequence[str],
        allowed_domains: Sequence[str],
        limit: Optional[int] = None,
    ) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: List[str] = []
        selectors = list(css_selectors) or ["a[href]"]
        for selector in selectors:
            for node in soup.select(selector):
                href = node.get("href")
                if not href:
                    continue
                full_url = urljoin(base_url, href.strip())
                if full_url.startswith("javascript:"):
                    continue
                if not _is_allowed_domain(full_url, allowed_domains):
                    continue
                if not _match_patterns(full_url, link_patterns):
                    continue
                links.append(full_url)
                if limit and len(links) >= limit:
                    return list(dict.fromkeys(links))
        return list(dict.fromkeys(links))

    def _extract_links_with_time_filter(
        self,
        html: str,
        base_url: str,
        site: SiteConfig,
        keep_days: int,
        limit: Optional[int] = None,
    ) -> List[str]:
        if keep_days <= 0 or not site.result_item_selector:
            return self._extract_links(
                html=html,
                base_url=base_url,
                css_selectors=site.result_css_selectors,
                link_patterns=site.result_link_patterns,
                allowed_domains=site.allowed_domains,
                limit=limit,
            )

        soup = BeautifulSoup(html, "html.parser")
        now = datetime.datetime.now()
        cutoff = now - datetime.timedelta(days=keep_days)
        links: List[str] = []

        for item in soup.select(site.result_item_selector):
            link_node = (
                item.select_one(site.result_item_link_selector)
                if site.result_item_link_selector
                else item.select_one("a[href]")
            )
            if not link_node:
                continue
            href = (link_node.get("href") or "").strip()
            if not href:
                continue
            full_url = urljoin(base_url, href)
            if full_url.startswith("javascript:"):
                continue
            if not _is_allowed_domain(full_url, site.allowed_domains):
                continue
            if not _match_patterns(full_url, site.result_link_patterns):
                continue

            if site.result_item_time_selector:
                time_node = item.select_one(site.result_item_time_selector)
                parsed_time = _parse_time_text(time_node.get_text(" ", strip=True)) if time_node else None
                if parsed_time and parsed_time < cutoff:
                    continue

            links.append(full_url)
            if limit and len(links) >= limit:
                break

        return list(dict.fromkeys(links))

    def _extract_article(self, html: str, site: SiteConfig) -> Tuple[str, str, str]:
        soup = BeautifulSoup(html, "html.parser")

        for selector in site.article_remove_selectors:
            for node in soup.select(selector):
                node.decompose()

        title = _first_text_by_selectors(soup, site.article_title_selectors or [])
        if not title:
            title = _normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else ""

        content_blocks: List[str] = []
        content_html_blocks: List[str] = []
        for selector in site.article_content_selectors:
            for node in soup.select(selector):
                if not isinstance(node, Tag):
                    continue
                node_copy = BeautifulSoup(str(node), "html.parser")
                if node_copy.body and node_copy.body.contents:
                    candidate = node_copy.body.contents[0]
                    if isinstance(candidate, Tag):
                        _clean_content_node(candidate)
                        text = _normalize_text(candidate.get_text(" ", strip=True))
                        if text:
                            content_blocks.append(text)
                            content_html_blocks.append(str(candidate))
                        continue
                _clean_content_node(node)
                text = _normalize_text(node.get_text(" ", strip=True))
                if text:
                    content_blocks.append(text)
                    content_html_blocks.append(str(node))

        fallback_modes = set(site.article_fallback_modes or ["next_data", "nuxt_content", "full_page"])
        next_data_ret = _extract_next_data_content(html) if "next_data" in fallback_modes else None
        if content_blocks:
            idx = max(range(len(content_blocks)), key=lambda i: len(content_blocks[i]))
            content = content_blocks[idx]
            content_html = content_html_blocks[idx]
        else:
            if next_data_ret:
                content, content_html = next_data_ret
            else:
                candidates = []
                candidates_html = []
                for node in soup.select("article, main, div, section"):
                    text = _normalize_text(node.get_text(" ", strip=True))
                    if len(text) >= 120:
                        candidates.append(text)
                        candidates_html.append(str(node))
                if candidates:
                    idx = max(range(len(candidates)), key=lambda i: len(candidates[i]))
                    content = candidates[idx]
                    content_html = candidates_html[idx]
                else:
                    content = _normalize_text(soup.get_text(" ", strip=True))
                    content_html = str(soup)

        if next_data_ret and _looks_like_page_chrome(content):
            content, content_html = next_data_ret

        # Nuxt 状态兜底: 若正文太短，尝试从 __NUXT__ 里的 content 字段提取。
        if len(content) < 80:
            if next_data_ret:
                content, content_html = next_data_ret
            nuxt_ret = _extract_nuxt_content(html) if "nuxt_content" in fallback_modes else None
            if nuxt_ret and len(content) < 80:
                content, content_html = nuxt_ret
        return title, content, content_html

    def _extract_article_publish_time(self, html: str, site: SiteConfig, url: str = "") -> str:
        soup = BeautifulSoup(html, "html.parser")
        text = _first_text_by_selectors(soup, site.article_time_selectors or [])
        if text:
            return text
        if url:
            match = re.search(r"/(\d{4})[-/]?(\d{2})[-/]?(\d{2})/", url)
            if match:
                return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return ""

    def _site_by_url(self, url: str, fallback: SiteConfig) -> SiteConfig:
        for site in self.sites:
            if _is_allowed_domain(url, site.allowed_domains):
                return site
        return fallback

    def _search_urls(
        self,
        keyword: str,
        site: SiteConfig,
        limit_override: Optional[int],
        keep_days_override: Optional[int],
    ) -> List[str]:
        mode = str(site.entry_mode or "search_page").strip().lower()
        if mode == "direct_page":
            urls = [str(u).strip() for u in (site.seed_urls or [site.search_url_template]) if str(u).strip()]
            limit = limit_override if limit_override is not None else len(urls)
            return urls[:limit] if limit else urls

        site_keyword = self._resolve_site_keyword(keyword, site)
        raw_urls = site.seed_urls or [site.search_url_template]
        rendered_urls: List[str] = []
        for raw_url in raw_urls:
            raw_url = str(raw_url or "").strip()
            if not raw_url:
                continue
            rendered_urls.append(
                raw_url.format(
                    keyword=quote_plus(site_keyword),
                    raw_keyword=site_keyword,
                    site_keyword=site_keyword,
                    original_keyword=keyword,
                )
            )

        limit = limit_override if limit_override is not None else site.max_results_per_site
        keep_days = site.search_result_keep_days if keep_days_override is None else max(0, int(keep_days_override))
        links: List[str] = []
        for url in rendered_urls:
            html = self._fetch_page(url, site.search_wait_seconds, site, is_search=True)
            if not html:
                continue
            page_links = self._extract_links_with_time_filter(
                html=html,
                base_url=url,
                site=site,
                keep_days=keep_days,
                limit=limit,
            )
            if site.use_nuxt_article_id_fallback and keep_days <= 0:
                nuxt_links = _extract_nuxt_article_urls(html, url)
                for candidate in nuxt_links:
                    if not _is_allowed_domain(candidate, site.allowed_domains):
                        continue
                    if not _match_patterns(candidate, site.result_link_patterns):
                        continue
                    page_links.append(candidate)
            links.extend(page_links)
            links = list(dict.fromkeys(links))
            if limit and len(links) >= limit:
                return links[:limit]
        return links[:limit] if limit else links

    def search(
        self,
        keyword: str,
        max_results_per_site: Optional[int] = None,
        keep_days_override: Optional[int] = None,
    ) -> Dict[str, List[str]]:
        if not self.sites:
            self.last_search_errors = {}
            self.last_search_counts = {}
            return {}

        result: Dict[str, List[str]] = {}
        search_errors: Dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.site_search_workers, len(self.sites))
        ) as pool:
            future_map = {
                pool.submit(self._search_urls, keyword, site, max_results_per_site, keep_days_override): site
                for site in self.sites
            }
            for fut in concurrent.futures.as_completed(future_map):
                site = future_map[fut]
                try:
                    result[site.name] = fut.result()
                except Exception as exc:
                    error = str(exc).strip().splitlines()[0] or type(exc).__name__
                    search_errors[site.name] = error
                    print(f"[Warn] search failed: site={site.name} error={error}")
                    result[site.name] = []
        self.last_search_errors = search_errors
        self.last_search_counts = {
            name: len(urls) for name, urls in result.items()
        }
        print(
            f"[Crawler] keyword={keyword} search_counts="
            f"{self.last_search_counts}"
        )
        return result

    def _crawl_single(self, url: str, depth: int, source_site: SiteConfig) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        site = self._site_by_url(url, source_site)
        site_lock = self._site_locks.get(site.name)
        try:
            if site_lock:
                site_lock.acquire()
            html = self._fetch_page(url, site.article_wait_seconds, site, is_search=False)
            if not html:
                return None, []
            title, content, content_html = self._extract_article(html, site)
            publish_time = self._extract_article_publish_time(html, site, url=url)
            follow_urls = self._extract_links(
                html=html,
                base_url=url,
                css_selectors=site.follow_css_selectors,
                link_patterns=site.follow_link_patterns or site.result_link_patterns,
                allowed_domains=site.allowed_domains,
                limit=site.max_follow_links_per_page,
            )
            item = {
                "site": site.name,
                "url": url,
                "title": title,
                "publish_time": publish_time,
                "content": content,
                "content_html": content_html,
                "depth": depth,
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            return item, follow_urls
        except Exception as exc:
            print(f"[Warn] crawl failed: {url} error={exc}")
            return None, []
        finally:
            if site_lock:
                site_lock.release()

    def _crawl_frontier(self, frontier: List[Tuple[str, int, SiteConfig, Optional[str]]], keyword: str, max_follow_depth: int) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for depth in range(max_follow_depth + 1):
            current_level = [item for item in frontier if item[1] == depth]
            if not current_level:
                continue

            tasks: List[Tuple[str, int, SiteConfig, Optional[str]]] = []
            with self._visited_lock:
                for url, d, site, parent_key in current_level:
                    if url in self._visited_urls:
                        continue
                    self._visited_urls.add(url)
                    tasks.append((url, d, site, parent_key))

            next_level: List[Tuple[str, int, SiteConfig, Optional[str]]] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
                future_map = {
                    pool.submit(self._crawl_single, url, d, site): (url, d, site, parent_key)
                    for url, d, site, parent_key in tasks
                }
                for fut in concurrent.futures.as_completed(future_map):
                    url, d, source_site, parent_key = future_map[fut]
                    item, follow_urls = fut.result()
                    if item:
                        item["search_key"] = keyword
                        item["parent_url"] = parent_key
                        results.append(item)
                    if d < max_follow_depth:
                        for next_url in follow_urls:
                            next_level.append((next_url, d + 1, source_site, url))
            frontier.extend(next_level)
        return results

    def crawl_urls(
        self,
        keyword: str,
        urls: Sequence[str],
        source_site_name: Optional[str] = None,
        max_follow_depth: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._visited_lock:
            self._visited_urls = set()

        if not urls:
            return []

        source_site = next((site for site in self.sites if site.name == source_site_name), None)
        if source_site is None:
            source_site = self._site_by_url(str(urls[0]), self.sites[0]) if self.sites else None
        if source_site is None:
            return []

        frontier: List[Tuple[str, int, SiteConfig, Optional[str]]] = [
            (str(u), 0, source_site, None) for u in urls if str(u).strip()
        ]
        return self._crawl_frontier(frontier, keyword=keyword, max_follow_depth=max_follow_depth)

    def crawl(
        self,
        keyword: str,
        max_follow_depth: int = 1,
        max_results_per_site: Optional[int] = None,
        keep_days_override: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        with self._visited_lock:
            self._visited_urls = set()

        seeds: List[Tuple[str, SiteConfig]] = []
        search_result_map = self.search(
            keyword=keyword,
            max_results_per_site=max_results_per_site,
            keep_days_override=keep_days_override,
        )
        for site in self.sites:
            for u in search_result_map.get(site.name, []):
                seeds.append((u, site))

        frontier: List[Tuple[str, int, SiteConfig, Optional[str]]] = [(u, 0, s, None) for u, s in seeds]
        return self._crawl_frontier(frontier, keyword=keyword, max_follow_depth=max_follow_depth)


class StockCodeResolver:
    def __init__(self, stock_name_tsv: str):
        self.stock_name_tsv = stock_name_tsv
        self.name_to_code6: Dict[str, str] = {}
        self.code_tokens_to_code6: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.stock_name_tsv or not os.path.exists(self.stock_name_tsv):
            return
        with open(self.stock_name_tsv, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "\t" not in line:
                    continue
                full_code, name = line.split("\t", 1)
                full_code = full_code.strip().upper()
                name = name.strip()
                m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", full_code)
                if not m:
                    continue
                code6, ex = m.group(1), m.group(2)
                self.name_to_code6[name] = code6
                self.code_tokens_to_code6[code6] = code6
                self.code_tokens_to_code6[f"{code6}.{ex}"] = code6
                self.code_tokens_to_code6[f"{ex}{code6}"] = code6
                self.code_tokens_to_code6[f"{code6}{ex}"] = code6

    def resolve_to_6digit(self, keyword: str) -> Optional[str]:
        if not keyword:
            return None
        raw = keyword.strip()
        upper = raw.upper().replace("-", "").replace("_", "")
        upper = upper.replace(" ", "")

        if raw in self.name_to_code6:
            return self.name_to_code6[raw]
        if upper in self.code_tokens_to_code6:
            return self.code_tokens_to_code6[upper]

        m = re.search(r"(\d{6})", upper)
        if m:
            return m.group(1)

        return None


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Config-driven headless crawler for finance news sites.")
    parser.add_argument("--keyword", required=True, help="Search keyword.")
    parser.add_argument(
        "--config",
        default="src/crawler/finance_news_sites.json",
        help="Path to site config JSON file.",
    )
    parser.add_argument("--workers", type=int, default=4, help="Thread worker count.")
    parser.add_argument("--follow-depth", type=int, default=1, help="Max follow-link depth.")
    parser.add_argument("--max-results-per-site", type=int, default=None, help="Override config limit.")
    parser.add_argument(
        "--stock-name-tsv",
        default="stock_name.tsv",
        help="TSV with code<tab>name, used for stock code normalization.",
    )
    parser.add_argument(
        "--output",
        default="data/results/finance_news_output.json",
        help="Output file path.",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    sites = load_site_configs(args.config)
    resolver = StockCodeResolver(args.stock_name_tsv) if args.stock_name_tsv else None
    crawler = SiteNewsCrawler(sites=sites, code_resolver=resolver, workers=args.workers)
    items = crawler.crawl(
        keyword=args.keyword,
        max_follow_depth=args.follow_depth,
        max_results_per_site=args.max_results_per_site,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[Info] done: {len(items)} pages -> {args.output}")


if __name__ == "__main__":
    main()
