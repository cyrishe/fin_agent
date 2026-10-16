from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable
from urllib.parse import urlparse

from src.services.search_gateway_service import SearchGatewayService
from src.services.web_article_text_service import fetch_article_markdown
from src.services.stock_news_index_service import read_stock_news_index


TOOL_NAME = "general_search"


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    if params.get("urls") or params.get("stock_code"):
        return _read_selected_source(params)
    result = SearchGatewayService().search(
        query=str(params.get("query") or params.get("q") or "").strip(),
        limit=params.get("limit", 10),
        start_time=str(params.get("start_time") or params.get("start_date") or "").strip(),
        end_time=str(params.get("end_time") or params.get("end_date") or "").strip(),
        category_scope=_list(params.get("category_scope")),
        source_scope=_list(params.get("source_scope")),
        sort=str(params.get("sort") or "date_desc").strip().lower(),
    )
    ok = result.get("status") == "ok"
    items = [dict(item) for item in result.get("items") or []]
    if ok and params.get("include_content") is True and items:
        with ThreadPoolExecutor(max_workers=2) as pool:
            content = list(pool.map(fetch_article_markdown, [str(item.get("url") or "") for item in items[:2]]))
        for item, markdown in zip(items[:2], content):
            if markdown:
                item["content_markdown"] = markdown
    return {
        "tool": TOOL_NAME,
        "ok": ok,
        "provider": str(result.get("provider") or ""),
        "coverage": str(result.get("coverage") or ""),
        "query": str(result.get("query") or ""),
        "data": items,
        "total": int(result.get("total") or 0),
        "error": "" if ok else str(result.get("reason") or "search failed"),
    }


def _read_selected_source(params: Dict[str, Any]) -> Dict[str, Any]:
    result = {"tool": TOOL_NAME, "ok": True, "provider": "source_pages",
              "coverage": "curated_public_web", "query": str(params.get("query") or ""),
              "data": [], "total": 0, "error": ""}
    try:
        urls = list(dict.fromkeys(_list(params.get("urls"))))
        if urls:
            if len(urls) > 5:
                raise ValueError("一次最多读取 5 个选定链接")
            with ThreadPoolExecutor(max_workers=3) as pool:
                texts = list(pool.map(fetch_article_markdown, urls))
            for url, text in zip(urls, texts):
                result["data"].append({"document_id": url, "url": url,
                    "title": text.splitlines()[0].removeprefix("# ") if text else "正文未取得",
                    "source": urlparse(url).hostname or "", "publish_time": next((line.removeprefix("发布日期：") for line in text.splitlines() if line.startswith("发布日期：")), ""),
                    "snippet": "" if text else "该链接未取得可用正文；不能据此判断文章内容。",
                    "category": "news", "score": 0.0, "content_markdown": text})
            result["total"] = len(urls)
            if not any(texts):
                raise ValueError("选定链接均未取得可用正文")
        else:
            payload = read_stock_news_index(str(params["stock_code"]),
                limit=min(50, max(1, int(params.get("limit", 20)))),
                start_time=str(params.get("start_time") or ""), end_time=str(params.get("end_time") or ""))
            result.update(data=payload["items"], total=payload["total"], index_url=payload["index_url"])
    except (ValueError, TypeError) as exc:
        result.update(ok=False, error=str(exc))
    return result
