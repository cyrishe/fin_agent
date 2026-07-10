from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from src.crawler.site_news_crawler import StockCodeResolver
from src.services.ths_funds_service import THSFundsService
from src.utils.mysql_utils import MySQLUtils, PAGE_TYPE_NEWS


DEFAULT_STOCK_NAME_TSV = "stock_name.tsv"


class StockFundsTool:
    def __init__(self, stock_name_tsv: str = DEFAULT_STOCK_NAME_TSV) -> None:
        self.stock_name_tsv = stock_name_tsv
        self.resolver = StockCodeResolver(stock_name_tsv)
        self._code_to_name = self._build_code_to_name()
        self._service = THSFundsService()

    def _build_code_to_name(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for name, code6 in self.resolver.name_to_code6.items():
            mapping[code6] = name
        return mapping

    def _resolve_stock_identity(self, value: str) -> Dict[str, str]:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("code 不能为空")
        code6 = self.resolver.resolve_to_6digit(raw)
        if not code6:
            raise ValueError(f"无法识别股票代码或名称: {raw}")
        exchange = ""
        for token, resolved_code in self.resolver.code_tokens_to_code6.items():
            if resolved_code != code6 or "." not in token:
                continue
            exchange = token.split(".", 1)[1]
            break
        if not exchange:
            exchange = "SH" if code6.startswith(("60", "68", "69", "90")) else "BJ" if code6.startswith(("43", "82", "83", "87", "88", "92")) else "SZ"
        return {
            "query": raw,
            "code": code6,
            "stk_code": f"{code6}.{exchange}",
            "name": self._code_to_name.get(code6, ""),
        }

    @staticmethod
    def _normalize_flow_news_item(item: Dict[str, Any], resolved: Dict[str, str]) -> Dict[str, Any]:
        content = str(item.get("content_excerpt") or "").strip()
        return {
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "site": str(item.get("source") or "10jqka").strip(),
            "publish_time": str(item.get("publish_time") or "").strip(),
            "snippet": content[:180],
            "matched_keywords": list(item.get("matched_keywords") or []),
            "stock_code": resolved.get("code", ""),
            "stock_name": resolved.get("name", ""),
        }

    @staticmethod
    def _dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            key = str(item.get("url") or "").strip() or f"{item.get('title')}::{item.get('publish_time')}"
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _build_history_records(search_key: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for item in items:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            records.append(
                {
                    "url": url,
                    "host": str(item.get("host") or urlparse(url).netloc),
                    "search_key": str(search_key or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "content": str(item.get("content") or "").strip(),
                    "page_type": PAGE_TYPE_NEWS,
                    "crawled_time": str(item.get("publish_time") or "").strip(),
                }
            )
        return records

    def search(
        self,
        code: str,
        n_days: int = 30,
        include_flow_news: bool = True,
        persist: bool = True,
        dedupe: bool = True,
        db: Optional[MySQLUtils] = None,
    ) -> Dict[str, Any]:
        resolved = self._resolve_stock_identity(code)
        snapshot = self._service.get_stock_funds_snapshot(
            code=resolved["code"],
            n_days=max(1, int(n_days)),
            include_flow_news=bool(include_flow_news),
        )
        flow_news_items = [
            self._normalize_flow_news_item(item, resolved)
            for item in (snapshot.get("capital_flow_news") or [])
            if isinstance(item, dict)
        ]
        if dedupe:
            flow_news_items = self._dedupe_items(flow_news_items)
        history_records = self._build_history_records(resolved["code"], flow_news_items)

        persist_stats: Dict[str, int] = {"submitted": 0, "inserted": 0, "updated": 0, "affected": 0}
        owns_db = db is None
        if persist and history_records:
            db = db or MySQLUtils()
            try:
                persist_stats = db.insert_url_crawled_history_records(history_records)
            finally:
                if owns_db and db is not None:
                    db.close_db()

        return {
            "snapshot": snapshot,
            "capital_flow_news_items": flow_news_items,
            "render_blocks": _build_funds_render_blocks(snapshot),
        }


def _build_funds_render_blocks(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_url = str(snapshot.get("page_url") or "").strip()
    chart_payloads = snapshot.get("chart_payloads") if isinstance(snapshot.get("chart_payloads"), dict) else {}
    render_blocks: List[Dict[str, Any]] = []

    today_funds = snapshot.get("today_funds") if isinstance(snapshot.get("today_funds"), dict) else {}
    industry_funds = snapshot.get("industry_funds") if isinstance(snapshot.get("industry_funds"), dict) else {}
    metric_items = [
        {"label": "当日主力净流入", "value": today_funds.get("main_net_inflow_wan")},
        {"label": "行业资金净额", "value": industry_funds.get("industry_net_flow_wan")},
        {"label": "行业名称", "value": industry_funds.get("industry_name")},
    ]
    metric_items = [item for item in metric_items if item.get("value") not in (None, "", [])]
    if metric_items:
        render_blocks.append(
            {
                "type": "metric_strip",
                "title": "资金概览",
                "data": {"items": metric_items},
                "meta": {"source": "ths", "url": page_url, "unit": "万元"},
            }
        )

    pie_payload = chart_payloads.get("realtime_trade_distribution_pie")
    if isinstance(pie_payload, dict):
        items = []
        for item in pie_payload.get("series") or []:
            if not isinstance(item, dict):
                continue
            items.append({"label": str(item.get("name") or ""), "value": item.get("value")})
        if items:
            render_blocks.append(
                {
                    "type": "pie",
                    "title": str(pie_payload.get("title") or "实时成交分布"),
                    "data": {"items": items},
                    "meta": {"source": "ths", "url": page_url, "unit": str(pie_payload.get("unit") or "万元")},
                }
            )

    for key, title in (
        ("intraday_funds_line", "当日分时资金线"),
        ("historical_net_inflow_line", "历史资金净流入"),
    ):
        payload = chart_payloads.get(key)
        if not isinstance(payload, dict):
            continue
        render_blocks.append(
            {
                "type": "line",
                "title": str(payload.get("title") or title),
                "data": {
                    "x_axis": list(payload.get("categories") or payload.get("x_axis") or []),
                    "series": list(payload.get("series") or []),
                },
                "meta": {"source": "ths", "url": page_url, "unit": str(payload.get("unit") or "万元")},
            }
        )

    size_order = chart_payloads.get("size_order_bar")
    if isinstance(size_order, dict):
        render_blocks.append(
            {
                "type": "bar",
                "title": str(size_order.get("title") or "大小单净额与净占比"),
                "data": {
                    "x_axis": list(size_order.get("categories") or size_order.get("x_axis") or []),
                    "series": list(size_order.get("series") or []),
                },
                "meta": {"source": "ths", "url": page_url, "unit": str(size_order.get("left_unit") or "万元")},
            }
        )

    recent_main = chart_payloads.get("recent_main_funds_bar_line")
    if isinstance(recent_main, dict):
        render_blocks.append(
            {
                "type": "line",
                "title": str(recent_main.get("title") or "最近N日主力净额与5日净额"),
                "data": {
                    "x_axis": list(recent_main.get("categories") or recent_main.get("x_axis") or []),
                    "series": [
                        {"name": str(item.get("name") or ""), "data": list(item.get("data") or [])}
                        for item in (recent_main.get("series") or [])
                        if isinstance(item, dict)
                    ],
                },
                "meta": {"source": "ths", "url": page_url, "unit": str(recent_main.get("left_unit") or "万元")},
            }
        )

    industry = chart_payloads.get("industry_top_buy_sell_bar")
    if isinstance(industry, dict):
        render_blocks.append(
            {
                "type": "bar",
                "title": str(industry.get("title") or "行业主力买卖前5"),
                "data": {
                    "groups": [
                        {
                            "name": str(item.get("name") or ""),
                            "x_axis": list(item.get("categories") or item.get("x_axis") or []),
                            "data": list(item.get("data") or []),
                        }
                        for item in (industry.get("series") or [])
                        if isinstance(item, dict)
                    ]
                },
                "meta": {"source": "ths", "url": page_url, "unit": str(industry.get("left_unit") or "万元")},
            }
        )

    history_rows = snapshot.get("historical_table")
    if isinstance(history_rows, list) and history_rows:
        render_blocks.append(
            {
                "type": "table",
                "title": "历史资金表",
                "data": {
                    "columns": list(history_rows[0].keys()) if isinstance(history_rows[0], dict) else [],
                    "rows": history_rows,
                },
                "meta": {"source": "ths", "url": page_url, "unit": ""},
            }
        )

    return render_blocks


def get_stock_funds_snapshot_tool(
    code: str,
    n_days: int = 30,
    include_flow_news: bool = True,
    persist: bool = True,
    dedupe: bool = True,
    db: Optional[MySQLUtils] = None,
) -> Dict[str, Any]:
    tool = StockFundsTool()
    return tool.search(
        code=code,
        n_days=n_days,
        include_flow_news=include_flow_news,
        persist=persist,
        dedupe=dedupe,
        db=db,
    )


def run(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = get_stock_funds_snapshot_tool(
            code=str(params.get("code") or params.get("query") or params.get("name") or params.get("company") or "").strip(),
            n_days=int(params.get("n_days", 30) or 30),
            include_flow_news=bool(params.get("include_flow_news", True)),
            persist=bool(params.get("persist", True)),
            dedupe=bool(params.get("dedupe", True)),
        )
        return {
            "tool": "stock_funds",
            "ok": True,
            "data": payload,
            "error": "",
        }
    except Exception as exc:
        return {
            "tool": "stock_funds",
            "ok": False,
            "data": {},
            "error": str(exc),
        }
