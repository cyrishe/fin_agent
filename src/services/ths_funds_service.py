import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests import RequestException


class THSFundsService:
    """Parse 10jqka stock funds pages into a frontend-friendly JSON dict."""

    BASE_URL = "https://stockpage.10jqka.com.cn"
    PAGE_URL = BASE_URL + "/{code}/funds/"
    REAL_FUNDS_URL = BASE_URL + "/spService/{code}/Funds/realFunds/free/1/"
    LINE_FUNDS_URL = BASE_URL + "/spService/{code}/Funds/lineFunds"
    STOCK_HOME_URL = BASE_URL + "/{code}/"
    CAPITAL_FLOW_NEWS_KEYWORDS = (
        "资金",
        "净流入",
        "净流出",
        "主力",
        "融资买入",
        "融资净买入",
        "融资净偿还",
        "融资余额",
        "融券",
        "ETF资金",
        "资金流向",
        "获融资买入",
    )
    ARTICLE_CONTENT_SELECTORS = (
        ".article-content",
        ".main-text",
        "#main-content",
        ".content",
        "article",
    )

    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text == "--":
            return None
        text = text.rstrip("%")
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _to_int_stripped(code: Any) -> str:
        text = str(code or "").strip()
        digits = re.sub(r"\D+", "", text)
        return digits or text

    @staticmethod
    def _parse_hidden_json(soup: BeautifulSoup, element_id: str) -> Any:
        node = soup.select_one(f"#{element_id}")
        if not node:
            return None
        text = node.get_text(strip=True)
        if not text:
            return None
        return json.loads(text)

    def _get_text(self, url: str, referer: str = "") -> str:
        headers = {}
        if referer:
            headers["Referer"] = referer
        resp = self.session.get(url, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def _get_json(self, url: str, referer: str = "") -> Dict[str, Any]:
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            headers["Referer"] = referer
        resp = self.session.get(url, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _empty_realtime_trade_distribution() -> Dict[str, Any]:
        return {
            "total_inflow_wan": None,
            "total_outflow_wan": None,
            "net_inflow_wan": None,
            "trade_distribution": {
                "large_order": {"inflow": None, "outflow": None, "net": None},
                "mid_order": {"inflow": None, "outflow": None, "net": None},
                "small_order": {"inflow": None, "outflow": None, "net": None},
            },
            "pie_segments": [],
            "industry_flow": {
                "industry_name": "",
                "industry_code": "",
                "industry_net_inflow_wan": None,
                "same_industry_stocks": [],
                "top_buy": [],
                "top_sell": [],
            },
        }

    @staticmethod
    def _empty_today_funds() -> Dict[str, Any]:
        return {
            "main_net_inflow_wan": None,
            "order_stats": {
                "large_order": {"net_amount_wan": None, "net_ratio_pct": None},
                "mid_order": {"net_amount_wan": None, "net_ratio_pct": None},
                "small_order": {"net_amount_wan": None, "net_ratio_pct": None},
            },
            "intraday_line": [],
            "extra_info": {},
        }

    @staticmethod
    def _empty_hidden_series() -> Dict[str, Any]:
        return {
            "main_history_series": [],
            "main_history_series_free": [],
            "bucket_summary": {},
            "bucket_summary_free": {},
        }

    @classmethod
    def _empty_result(cls, stock_code: str) -> Dict[str, Any]:
        return {
            "source": "10jqka_stock_funds",
            "stock_code": stock_code,
            "stock_name": stock_code,
            "page_url": cls.PAGE_URL.format(code=stock_code),
            "realtime_trade_distribution": cls._empty_realtime_trade_distribution(),
            "today_funds": cls._empty_today_funds(),
            "size_order_stats": cls._empty_today_funds()["order_stats"],
            "capital_flow_news": [],
            "industry_funds": cls._empty_realtime_trade_distribution()["industry_flow"],
            "recent_n_day_series": {"n_days": 0, "series": []},
            "historical_table": [],
            "hidden_series": cls._empty_hidden_series(),
            "readable_sections": {},
            "chart_payloads": {},
            "available_sections": [],
            "errors": [],
            "notes": [],
        }

    @staticmethod
    def _extract_stock_name(soup: BeautifulSoup, code: str) -> str:
        strong = soup.select_one("#stockNamePlace")
        if strong:
            text = strong.get_text("\n", strip=True).splitlines()
            if text:
                return text[0].strip()
        title = soup.title.get_text(strip=True) if soup.title else ""
        match = re.match(r"(.+?)\(", title)
        if match:
            return match.group(1).strip()
        return code

    @staticmethod
    def _extract_article_date(url: str, soup: BeautifulSoup) -> str:
        for selector in (".time", ".news_info", ".info", ".newstime"):
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        match = re.search(r"/(\d{8})/c\d+\.shtml", url)
        if match:
            return match.group(1)
        return ""

    @classmethod
    def _is_capital_flow_news_title(cls, title: str) -> bool:
        text = str(title or "").strip()
        return any(keyword in text for keyword in cls.CAPITAL_FLOW_NEWS_KEYWORDS)

    @classmethod
    def _extract_article_content(cls, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for selector in cls.ARTICLE_CONTENT_SELECTORS:
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return ""

    def _fetch_capital_flow_news(self, stock_code: str, stock_name: str, max_items: int = 8) -> List[Dict[str, Any]]:
        home_url = self.STOCK_HOME_URL.format(code=stock_code)
        html = self._get_text(home_url)
        soup = BeautifulSoup(html, "html.parser")
        news_links: List[Dict[str, Any]] = []
        seen = set()
        for anchor in soup.select("#xwgg ul.news_list a[href], #xwgg .news_title a[href]"):
            href = str(anchor.get("href") or "").strip()
            title = anchor.get_text(" ", strip=True)
            if not href or not title or not self._is_capital_flow_news_title(title):
                continue
            href = urljoin(home_url, href)
            if href in seen:
                continue
            seen.add(href)
            news_links.append({"title": title, "url": href})
            if len(news_links) >= max_items:
                break

        items: List[Dict[str, Any]] = []
        for item in news_links:
            article_html = ""
            article_text = ""
            publish_time = ""
            try:
                article_html = self._get_text(item["url"], referer=home_url)
                article_text = self._extract_article_content(article_html)
                publish_time = self._extract_article_date(item["url"], BeautifulSoup(article_html, "html.parser"))
            except Exception:
                article_text = ""
                publish_time = ""
            items.append(
                {
                    "title": item["title"],
                    "url": item["url"],
                    "publish_time": publish_time,
                    "source": "10jqka",
                    "matched_keywords": [kw for kw in self.CAPITAL_FLOW_NEWS_KEYWORDS if kw in item["title"]],
                    "content_excerpt": article_text[:500],
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                }
            )
        return items

    @classmethod
    def _parse_real_funds(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        flash = payload.get("flash") if isinstance(payload.get("flash"), list) else []
        title = payload.get("title") if isinstance(payload.get("title"), dict) else {}
        field = payload.get("field") if isinstance(payload.get("field"), dict) else {}

        buckets = {
            "large_order": {
                "inflow": cls._to_float(flash[5].get("sr")) if len(flash) > 5 else None,
                "outflow": cls._to_float(flash[0].get("sr")) if len(flash) > 0 else None,
            },
            "mid_order": {
                "inflow": cls._to_float(flash[4].get("sr")) if len(flash) > 4 else None,
                "outflow": cls._to_float(flash[1].get("sr")) if len(flash) > 1 else None,
            },
            "small_order": {
                "inflow": cls._to_float(flash[3].get("sr")) if len(flash) > 3 else None,
                "outflow": cls._to_float(flash[2].get("sr")) if len(flash) > 2 else None,
            },
        }
        for item in buckets.values():
            inflow = item.get("inflow")
            outflow = item.get("outflow")
            item["net"] = (inflow - outflow) if inflow is not None and outflow is not None else None

        return {
            "total_inflow_wan": cls._to_float(title.get("zlr")),
            "total_outflow_wan": cls._to_float(title.get("zlc")),
            "net_inflow_wan": cls._to_float(title.get("je")),
            "trade_distribution": buckets,
            "pie_segments": [
                {
                    "name": str(item.get("name") or ""),
                    "amount_wan": cls._to_float(item.get("sr")),
                }
                for item in flash
                if isinstance(item, dict)
            ],
            "industry_flow": {
                "industry_name": str(field.get("hyname") or ""),
                "industry_code": str(field.get("py") or ""),
                "industry_net_inflow_wan": cls._to_float(field.get("hyje")),
                "same_industry_stocks": [
                    {
                        "stock_code": cls._to_int_stripped(item.get("stockcode")),
                        "stock_name": str(item.get("stockname") or ""),
                        "net_inflow_wan": cls._to_float(item.get("je")),
                        "change_pct": cls._to_float(item.get("zdf")),
                    }
                    for item in field.get("hyzdf") or []
                    if isinstance(item, dict)
                ],
                "top_buy": [
                    {
                        "stock_code": cls._to_int_stripped(item.get("stockcode")),
                        "stock_name": str(item.get("stockname") or ""),
                        "main_order_net_inflow_wan": cls._to_float(item.get("ddje")),
                    }
                    for item in field.get("desc") or []
                    if isinstance(item, dict)
                ],
                "top_sell": [
                    {
                        "stock_code": cls._to_int_stripped(item.get("stockcode")),
                        "stock_name": str(item.get("stockname") or ""),
                        "main_order_net_inflow_wan": cls._to_float(item.get("ddje")),
                    }
                    for item in field.get("asc") or []
                    if isinstance(item, dict)
                ],
            },
        }

    @classmethod
    def _parse_line_series(cls, line_text: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for chunk in str(line_text or "").split("|"):
            parts = chunk.split(";")
            if len(parts) != 4:
                continue
            large = cls._to_float(parts[1])
            mid = cls._to_float(parts[2])
            small = cls._to_float(parts[3])
            rows.append(
                {
                    "time": parts[0],
                    "large_order_net_wan": large,
                    "mid_order_net_wan": mid,
                    "small_order_net_wan": small,
                    "main_net_wan": large,
                    "sum_net_wan": (large or 0.0) + (mid or 0.0) + (small or 0.0),
                }
            )
        return rows

    @classmethod
    def _parse_line_funds(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        diff = payload.get("diff") if isinstance(payload.get("diff"), dict) else {}
        dde = payload.get("dde") if isinstance(payload.get("dde"), dict) else {}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        return {
            "main_net_inflow_wan": cls._to_float(dde.get("zllx")),
            "order_stats": {
                "large_order": {
                    "net_amount_wan": cls._to_float(diff.get("ddje")),
                    "net_ratio_pct": cls._to_float(diff.get("ddjb")),
                },
                "mid_order": {
                    "net_amount_wan": cls._to_float(diff.get("zdje")),
                    "net_ratio_pct": cls._to_float(diff.get("zdjb")),
                },
                "small_order": {
                    "net_amount_wan": cls._to_float(diff.get("xdje")),
                    "net_ratio_pct": cls._to_float(diff.get("xdjb")),
                },
            },
            "intraday_line": cls._parse_line_series(payload.get("line") or ""),
            "extra_info": info,
        }

    @classmethod
    def _parse_history_table(cls, soup: BeautifulSoup, table_selector: str = "#history_table table.m_table_3") -> List[Dict[str, Any]]:
        table = soup.select_one(table_selector)
        if not table:
            return []
        rows = []
        trs = table.select("tr")
        for tr in trs[2:]:
            cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
            if len(cells) < 10:
                continue
            rows.append(
                {
                    "date": cells[0],
                    "close": cls._to_float(cells[1]),
                    "change_pct": cls._to_float(cells[2]),
                    "net_inflow_wan": cls._to_float(cells[3]),
                    "five_day_main_net_wan": cls._to_float(cells[4]),
                    "large_order_net_wan": cls._to_float(cells[5]),
                    "large_order_net_ratio_pct": cls._to_float(cells[6]),
                    "mid_order_net_wan": cls._to_float(cells[7]),
                    "mid_order_net_ratio_pct": cls._to_float(cells[8]),
                    "small_order_net_wan": cls._to_float(cells[9]),
                    "small_order_net_ratio_pct": cls._to_float(cells[10]) if len(cells) > 10 else None,
                }
            )
        return rows

    @classmethod
    def _parse_hidden_series(cls, soup: BeautifulSoup) -> Dict[str, Any]:
        return {
            "main_history_series": cls._parse_hidden_json(soup, "lszjlx_data") or [],
            "main_history_series_free": cls._parse_hidden_json(soup, "lszjlx_data_free") or [],
            "bucket_summary": cls._parse_hidden_json(soup, "lstj_data") or {},
            "bucket_summary_free": cls._parse_hidden_json(soup, "lstj_data_free") or {},
        }

    @staticmethod
    def _filter_history_rows(rows: List[Dict[str, Any]], n_days: int) -> List[Dict[str, Any]]:
        if n_days <= 0:
            return rows
        return rows[:n_days]

    @staticmethod
    def _fmt_metric(label: str, value: Any, unit: str = "") -> Dict[str, Any]:
        return {
            "label": label,
            "value": value,
            "unit": unit,
        }

    @classmethod
    def _build_readable_sections(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        realtime = result.get("realtime_trade_distribution") or {}
        today = result.get("today_funds") or {}
        order_stats = result.get("size_order_stats") or {}
        industry = result.get("industry_funds") or {}
        recent = (result.get("recent_n_day_series") or {}).get("series") or []
        history = result.get("historical_table") or []

        return {
            "headline_metrics": [
                cls._fmt_metric("实时总流入", realtime.get("total_inflow_wan"), "万元"),
                cls._fmt_metric("实时总流出", realtime.get("total_outflow_wan"), "万元"),
                cls._fmt_metric("实时净流入", realtime.get("net_inflow_wan"), "万元"),
                cls._fmt_metric("当日主力净流入", today.get("main_net_inflow_wan"), "万元"),
            ],
            "size_order_summary": [
                cls._fmt_metric("大单净额", ((order_stats.get("large_order") or {}).get("net_amount_wan")), "万元"),
                cls._fmt_metric("大单净占比", ((order_stats.get("large_order") or {}).get("net_ratio_pct")), "%"),
                cls._fmt_metric("中单净额", ((order_stats.get("mid_order") or {}).get("net_amount_wan")), "万元"),
                cls._fmt_metric("中单净占比", ((order_stats.get("mid_order") or {}).get("net_ratio_pct")), "%"),
                cls._fmt_metric("小单净额", ((order_stats.get("small_order") or {}).get("net_amount_wan")), "万元"),
                cls._fmt_metric("小单净占比", ((order_stats.get("small_order") or {}).get("net_ratio_pct")), "%"),
            ],
            "industry_summary": [
                cls._fmt_metric("所属行业", industry.get("industry_name"), ""),
                cls._fmt_metric("行业资金净流入", industry.get("industry_net_inflow_wan"), "万元"),
            ],
            "recent_series_summary": recent,
            "historical_table_summary": history,
            "capital_flow_news_summary": result.get("capital_flow_news") or [],
        }

    @classmethod
    def _build_chart_payloads(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        realtime = result.get("realtime_trade_distribution") or {}
        trade_dist = realtime.get("trade_distribution") or {}
        today = result.get("today_funds") or {}
        order_stats = result.get("size_order_stats") or {}
        recent_n_day = (result.get("recent_n_day_series") or {}).get("series") or []
        history_rows = result.get("historical_table") or []
        industry = result.get("industry_funds") or {}

        pie_data = []
        for item in realtime.get("pie_segments") or []:
            if not isinstance(item, dict):
                continue
            pie_data.append(
                {
                    "name": str(item.get("name") or ""),
                    "value": item.get("amount_wan"),
                    "unit": "万元",
                }
            )

        intraday_line = today.get("intraday_line") or []
        intraday_categories = [str(x.get("time") or "") for x in intraday_line if isinstance(x, dict)]

        recent_categories = [str(x.get("date") or "") for x in recent_n_day if isinstance(x, dict)]
        recent_main = [x.get("value") for x in recent_n_day if isinstance(x, dict)]
        recent_five = [x.get("field") for x in recent_n_day if isinstance(x, dict)]

        history_categories = [str(x.get("date") or "") for x in history_rows if isinstance(x, dict)]

        return {
            "realtime_trade_distribution_pie": {
                "chart_type": "pie",
                "title": "实时成交分布",
                "unit": "万元",
                "legend": [x.get("name") for x in pie_data],
                "series": pie_data,
            },
            "intraday_funds_line": {
                "chart_type": "line",
                "title": "当日分时资金线",
                "unit": "万元",
                "categories": intraday_categories,
                "series": [
                    {
                        "name": "大单净额",
                        "data": [x.get("large_order_net_wan") for x in intraday_line if isinstance(x, dict)],
                    },
                    {
                        "name": "中单净额",
                        "data": [x.get("mid_order_net_wan") for x in intraday_line if isinstance(x, dict)],
                    },
                    {
                        "name": "小单净额",
                        "data": [x.get("small_order_net_wan") for x in intraday_line if isinstance(x, dict)],
                    },
                    {
                        "name": "合计净额",
                        "data": [x.get("sum_net_wan") for x in intraday_line if isinstance(x, dict)],
                    },
                ],
            },
            "size_order_bar": {
                "chart_type": "bar",
                "title": "大小单净额与净占比",
                "categories": ["大单", "中单", "小单"],
                "left_unit": "万元",
                "right_unit": "%",
                "series": [
                    {
                        "name": "净额",
                        "axis": "left",
                        "data": [
                            ((order_stats.get("large_order") or {}).get("net_amount_wan")),
                            ((order_stats.get("mid_order") or {}).get("net_amount_wan")),
                            ((order_stats.get("small_order") or {}).get("net_amount_wan")),
                        ],
                    },
                    {
                        "name": "净占比",
                        "axis": "right",
                        "data": [
                            ((order_stats.get("large_order") or {}).get("net_ratio_pct")),
                            ((order_stats.get("mid_order") or {}).get("net_ratio_pct")),
                            ((order_stats.get("small_order") or {}).get("net_ratio_pct")),
                        ],
                    },
                ],
            },
            "recent_main_funds_bar_line": {
                "chart_type": "bar_line",
                "title": f"最近{(result.get('recent_n_day_series') or {}).get('n_days') or 0}日主力净额与5日净额",
                "categories": recent_categories,
                "left_unit": "万元",
                "right_unit": "万元",
                "series": [
                    {
                        "name": "主力净额",
                        "type": "bar",
                        "axis": "left",
                        "data": recent_main,
                    },
                    {
                        "name": "5日主力净额",
                        "type": "line",
                        "axis": "right",
                        "data": recent_five,
                    },
                ],
            },
            "historical_net_inflow_line": {
                "chart_type": "line",
                "title": "历史资金净流入",
                "unit": "万元",
                "categories": history_categories,
                "series": [
                    {
                        "name": "资金净流入",
                        "data": [x.get("net_inflow_wan") for x in history_rows if isinstance(x, dict)],
                    },
                    {
                        "name": "5日主力净额",
                        "data": [x.get("five_day_main_net_wan") for x in history_rows if isinstance(x, dict)],
                    },
                ],
            },
            "industry_top_buy_sell_bar": {
                "chart_type": "grouped_bar",
                "title": "行业主力买入卖出前5",
                "left_unit": "万元",
                "series": [
                    {
                        "name": "主力买入前5",
                        "categories": [x.get("stock_name") for x in industry.get("top_buy") or [] if isinstance(x, dict)],
                        "data": [x.get("main_order_net_inflow_wan") for x in industry.get("top_buy") or [] if isinstance(x, dict)],
                    },
                    {
                        "name": "主力卖出前5",
                        "categories": [x.get("stock_name") for x in industry.get("top_sell") or [] if isinstance(x, dict)],
                        "data": [x.get("main_order_net_inflow_wan") for x in industry.get("top_sell") or [] if isinstance(x, dict)],
                    },
                ],
            },
            "raw_tables": {
                "historical_table": history_rows,
                "capital_flow_news": result.get("capital_flow_news") or [],
                "same_industry_stocks": industry.get("same_industry_stocks") or [],
            },
            "raw_lists": {
                "pie_segments": realtime.get("pie_segments") or [],
                "intraday_line": intraday_line,
                "recent_n_day_series": recent_n_day,
            },
            "readable_labels": {
                "realtime_total_inflow": "实时总流入",
                "realtime_total_outflow": "实时总流出",
                "realtime_net_inflow": "实时净流入",
                "today_main_net_inflow": "当日主力净流入",
                "large_order": "大单",
                "mid_order": "中单",
                "small_order": "小单",
                "five_day_main_net": "5日主力净额",
            },
        }

    def get_stock_funds_snapshot(
        self,
        code: str,
        n_days: int = 30,
        include_flow_news: bool = True,
    ) -> Dict[str, Any]:
        stock_code = self._to_int_stripped(code)
        page_url = self.PAGE_URL.format(code=stock_code)
        result = self._empty_result(stock_code)
        result["page_url"] = page_url
        result["capital_flow_news"] = [] if include_flow_news else []
        result["recent_n_day_series"]["n_days"] = n_days
        result["notes"] = [
            "实时资金、当日净额、行业资金来自 10jqka 的 realFunds/lineFunds 接口。",
            "历史表和 5日主力净额来自资金页 HTML 表格与隐藏 JSON。",
            "capital_flow_news 目前从同花顺个股首页新闻区筛选资金相关标题并抓正文摘要。",
        ]

        soup: Optional[BeautifulSoup] = None

        try:
            html = self._get_text(page_url)
            soup = BeautifulSoup(html, "html.parser")
            result["stock_name"] = self._extract_stock_name(soup, stock_code)
            history_table = self._parse_history_table(soup)
            hidden_series = self._parse_hidden_series(soup)
            main_history_series = hidden_series.get("main_history_series") or []
            recent_series = main_history_series[-n_days:] if n_days > 0 else main_history_series
            result["historical_table"] = self._filter_history_rows(history_table, n_days)
            result["hidden_series"] = hidden_series
            result["recent_n_day_series"] = {
                "n_days": n_days,
                "series": recent_series,
            }
            result["available_sections"].extend(
                ["historical_table", "hidden_series", "recent_n_day_series"]
            )
        except (RequestException, ValueError, json.JSONDecodeError) as exc:
            result["errors"].append(
                {
                    "section": "funds_page_html",
                    "message": str(exc),
                }
            )

        if include_flow_news:
            try:
                result["capital_flow_news"] = self._fetch_capital_flow_news(
                    stock_code=stock_code,
                    stock_name=result["stock_name"],
                )
                if result["capital_flow_news"]:
                    result["available_sections"].append("capital_flow_news")
            except (RequestException, ValueError, json.JSONDecodeError) as exc:
                result["errors"].append(
                    {
                        "section": "capital_flow_news",
                        "message": str(exc),
                    }
                )

        try:
            real_payload = self._get_json(self.REAL_FUNDS_URL.format(code=stock_code), referer=page_url)
            realtime_data = self._parse_real_funds(real_payload)
            result["realtime_trade_distribution"] = realtime_data
            result["industry_funds"] = realtime_data.get("industry_flow", self._empty_realtime_trade_distribution()["industry_flow"])
            result["available_sections"].extend(["realtime_trade_distribution", "industry_funds"])
        except (RequestException, ValueError, json.JSONDecodeError) as exc:
            result["errors"].append(
                {
                    "section": "real_funds",
                    "message": str(exc),
                }
            )

        try:
            line_payload = self._get_json(self.LINE_FUNDS_URL.format(code=stock_code), referer=page_url)
            today_funds = self._parse_line_funds(line_payload)
            result["today_funds"] = today_funds
            result["size_order_stats"] = today_funds.get("order_stats", self._empty_today_funds()["order_stats"])
            result["available_sections"].extend(["today_funds", "size_order_stats"])
        except (RequestException, ValueError, json.JSONDecodeError) as exc:
            result["errors"].append(
                {
                    "section": "line_funds",
                    "message": str(exc),
                }
            )

        result["available_sections"] = sorted(set(result["available_sections"]))
        result["readable_sections"] = self._build_readable_sections(result)
        result["chart_payloads"] = self._build_chart_payloads(result)
        return result


if __name__ == "__main__":
    import sys

    code = sys.argv[1] if len(sys.argv) > 1 else "300502"
    service = THSFundsService()
    print(json.dumps(service.get_stock_funds_snapshot(code=code), ensure_ascii=False, indent=2))
