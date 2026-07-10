import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.crawler.site_news_crawler import StockCodeResolver
from src.utils.mysql_utils import MySQLUtils, PAGE_TYPE_REPORT


SITE_NAME = "sina_stock_report"
LIST_URL = (
    "https://stock.finance.sina.com.cn/stock/go.php/vReport_List/"
    "kind/search/index.phtml?symbol={code}&t1=all"
)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _normalize_ws(text: str) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def _strip_label(text: str, labels: List[str]) -> str:
    normalized = _normalize_ws(text)
    for label in labels:
        for prefix in (f"{label}：", f"{label}:"):
            if normalized.startswith(prefix):
                return normalized.split(prefix, 1)[1].strip()
    return ""


def _extract_report_detail_fields(content_html: str, fallback_title: str = "") -> Dict[str, str]:
    soup = BeautifulSoup(content_html or "", "html.parser")
    wrapper = soup.select_one(".report-content-wrapper") or soup

    title_node = wrapper.select_one(".report-title") or wrapper.select_one("h1")
    title = _normalize_ws(title_node.get_text(" ", strip=True) if title_node else fallback_title)

    publish_date = ""
    institution = ""
    analyst = ""
    report_type = ""
    for span in wrapper.select(".report-head span"):
        text = _normalize_ws(span.get_text(" ", strip=True))
        if not text:
            continue
        if not publish_date and (text[:4].isdigit() and "-" in text):
            publish_date = text
            continue
        if not publish_date:
            publish_date = _strip_label(text, ["日期", "发布时间", "发布日期"]) or publish_date
        if not institution:
            institution = _strip_label(text, ["机构", "券商", "来源机构"]) or institution
            continue
        if not analyst:
            analyst = _strip_label(text, ["分析师", "研究员"]) or analyst
            continue

    for span in wrapper.select(".creab span"):
        text = _normalize_ws(span.get_text(" ", strip=True))
        if not text:
            continue
        if not report_type:
            report_type = _strip_label(text, ["类别", "类型"]) or report_type
        if not institution:
            institution = _strip_label(text, ["机构", "券商", "来源机构"]) or institution
        if not analyst:
            analyst = _strip_label(text, ["分析师", "研究员"]) or analyst
        if not publish_date:
            publish_date = _strip_label(text, ["日期", "发布时间", "发布日期"]) or publish_date

    body_node = wrapper.select_one(".report-content") or wrapper.select_one(".blk_container")
    if body_node is None:
        body_node = BeautifulSoup(str(wrapper), "html.parser")
        for selector in (".report-title", ".report-head", ".creab", ".report-footer", "script", "style"):
            for node in body_node.select(selector):
                node.decompose()

    body_text = ""
    if body_node:
        body_text = body_node.get_text("\n", strip=True)
        body_lines = [_normalize_ws(line) for line in body_text.splitlines()]
        body_text = "\n".join(line for line in body_lines if line)

    metadata_parts = []
    if report_type:
        metadata_parts.append(f"类别：{report_type}")
    if publish_date:
        metadata_parts.append(f"发布日期：{publish_date}")
    if institution:
        metadata_parts.append(f"机构：{institution}")
    if analyst:
        metadata_parts.append(f"研究员：{analyst}")
    metadata_line = " | ".join(metadata_parts)
    body_text_with_meta = "\n".join(part for part in [metadata_line, body_text] if part)

    plain_parts = [
        part
        for part in [
            title,
            body_text_with_meta,
        ]
        if part
    ]
    plain_text = "\n".join(plain_parts)

    return {
        "title": title or _normalize_ws(fallback_title),
        "publish_date": publish_date,
        "institution": institution,
        "analyst": analyst,
        "report_type": report_type,
        "content_text": body_text_with_meta,
        "plain_text": plain_text,
    }


def _normalize_report_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in items:
        fields = _extract_report_detail_fields(
            content_html=str(item.get("content_html") or ""),
            fallback_title=str(item.get("title") or ""),
        )
        merged = dict(item)
        merged["title"] = fields["title"] or str(item.get("title") or "")
        merged["publish_date"] = fields["publish_date"] or str(item.get("publish_date") or "")
        merged["institution"] = fields["institution"] or str(item.get("institution") or "")
        merged["analyst"] = fields["analyst"] or str(item.get("analyst") or "")
        merged["report_type"] = fields["report_type"] or str(item.get("report_type") or "")
        merged["content_text"] = fields["content_text"]
        merged["content"] = fields["plain_text"] or str(item.get("content") or "")
        normalized.append(merged)
    return normalized


def _extract_sina_report_rows(list_html: str, list_url: str, max_results: Optional[int] = None) -> List[Dict[str, str]]:
    soup = BeautifulSoup(list_html or "", "html.parser")
    items: List[Dict[str, str]] = []
    for row in soup.select("table.tb_01 tr"):
        cells = row.select("td")
        if len(cells) < 6:
            continue
        link = cells[1].select_one("a[href]")
        if not link:
            continue
        url = urljoin(list_url, str(link.get("href") or "").strip())
        title = _normalize_ws(link.get("title") or link.get_text(" ", strip=True))
        if not url or not title:
            continue
        items.append(
            {
                "url": url,
                "title": title,
                "report_type": _normalize_ws(cells[2].get_text(" ", strip=True)),
                "publish_date": _normalize_ws(cells[3].get_text(" ", strip=True)),
                "institution": _normalize_ws(cells[4].get_text(" ", strip=True)),
                "analyst": _normalize_ws(cells[5].get_text(" ", strip=True)),
            }
        )
        if max_results and len(items) >= max_results:
            break
    return items


def _fetch_detail_html(session: requests.Session, url: str, timeout: int) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def _crawl_sina_report_items(
    code: str,
    max_results: Optional[int],
    timeout: int,
) -> Dict[str, Any]:
    list_url = LIST_URL.format(code=code)
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    list_html = _fetch_detail_html(session, list_url, timeout=timeout)
    discovered_items = _extract_sina_report_rows(list_html, list_url, max_results=max_results)

    crawled_items: List[Dict[str, Any]] = []
    for item in discovered_items:
        detail_html = _fetch_detail_html(session, item["url"], timeout=timeout)
        merged = dict(item)
        merged["site"] = SITE_NAME
        merged["content_html"] = detail_html
        merged["content"] = ""
        crawled_items.append(merged)

    return {
        "site": SITE_NAME,
        "list_url": list_url,
        "discovered_items": discovered_items,
        "items": _normalize_report_items(crawled_items),
    }


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Sina stock research reports and store deduplicated details."
    )
    parser.add_argument("--code", required=True, help="Stock code or stock name.")
    parser.add_argument(
        "--stock-name-tsv",
        default="stock_name.tsv",
        help="TSV for stock code/name mapping.",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Max report detail URLs extracted from Sina list page.",
    )
    parser.add_argument(
        "--output",
        default="data/results/ths_stock_reports.json",
        help="Output json file for crawled detail pages.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only crawl and write file, skip DB writes.",
    )
    return parser.parse_args()


def _resolve_stock_code(keyword: str, stock_name_tsv: str) -> str:
    resolver = StockCodeResolver(stock_name_tsv)
    return resolver.resolve_to_6digit(keyword) or str(keyword).strip()


def _build_db_records(items: List[Dict[str, Any]], keyword: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        records.append(
            {
                "url": url,
                "host": urlparse(url).netloc,
                "search_key": keyword,
                "title": item.get("title", ""),
                "content": item.get("content") or item.get("content_text") or item.get("content_html") or "",
                "page_type": PAGE_TYPE_REPORT,
            }
        )
    return records


def main() -> None:
    args = _build_args()
    stock_code = _resolve_stock_code(args.code, args.stock_name_tsv)
    crawl_result = _crawl_sina_report_items(
        code=stock_code,
        max_results=args.max_results,
        timeout=max(1, int(args.timeout)),
    )
    items = crawl_result["items"]
    report_urls = [item["url"] for item in crawl_result["discovered_items"]]
    print(f"[Info] discovered report URLs: {len(report_urls)}")

    new_items = items
    if not args.dry_run:
        db = MySQLUtils()
        try:
            new_urls = set(db.filter_new_urls(report_urls))
        finally:
            db.close_db()
        new_items = [item for item in items if item["url"] in new_urls]
        print(f"[Info] new report URLs after DB dedup: {len(new_items)}")
    else:
        print("[Info] dry-run enabled, skip DB pre-dedup.")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "code": stock_code,
                "site": SITE_NAME,
                "list_url": crawl_result["list_url"],
                "discovered_urls": report_urls,
                "items": new_items,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"[Info] crawled report pages: {len(new_items)}")
    print(f"[Info] output file: {output_path}")

    if args.dry_run:
        print("[Info] dry-run enabled, skip DB upsert.")
        return

    records = _build_db_records(new_items, stock_code)
    print(f"[Info] db candidate records: {len(records)}")
    db = MySQLUtils()
    try:
        stats = db.insert_url_crawled_history_records(records)
        print(
            "[Info] page_crawled_history upsert stats: "
            f"submitted={stats['submitted']} inserted={stats['inserted']} "
            f"updated={stats['updated']} affected={stats['affected']}"
        )
    finally:
        db.close_db()


if __name__ == "__main__":
    main()
