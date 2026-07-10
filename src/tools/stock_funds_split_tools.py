from typing import Any, Dict, List

from src.crawler.site_news_crawler import StockCodeResolver
from src.services.ths_funds_service import THSFundsService


DEFAULT_STOCK_NAME_TSV = "stock_name.tsv"


class StockFundsSplitTools:
    def __init__(self, stock_name_tsv: str = DEFAULT_STOCK_NAME_TSV) -> None:
        self.resolver = StockCodeResolver(stock_name_tsv)
        self._service = THSFundsService()
        self._code_to_name = self._build_code_to_name()

    def _build_code_to_name(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for name, code6 in self.resolver.name_to_code6.items():
            mapping[code6] = name
        return mapping

    def _resolve_stock_identity(self, value: str) -> Dict[str, str]:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("name 不能为空")
        code6 = self.resolver.resolve_to_6digit(raw)
        if not code6:
            raise ValueError(f"无法识别股票代码或名称: {raw}")
        return {
            "query": raw,
            "code": code6,
            "name": self._code_to_name.get(code6, "") or raw,
        }

    @staticmethod
    def _compact_bucket(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "inflow_wan": payload.get("inflow_wan"),
            "outflow_wan": payload.get("outflow_wan"),
            "net_inflow_wan": payload.get("net_inflow_wan") if "net_inflow_wan" in payload else payload.get("net_amount_wan"),
        }

    def get_realtime_funds_flow(self, name: str) -> Dict[str, Any]:
        resolved = self._resolve_stock_identity(name)
        snapshot = self._service.get_stock_funds_snapshot(code=resolved["code"], n_days=10, include_flow_news=False)
        order_stats = ((snapshot.get("today_funds") or {}).get("order_stats") or {})
        return {
            "name": resolved["name"],
            "code": resolved["code"],
            "source": str(snapshot.get("source") or "ths"),
            "funds": {
                "super_large": self._compact_bucket(order_stats.get("super_large_order") or {}),
                "large": self._compact_bucket(order_stats.get("large_order") or {}),
                "medium": self._compact_bucket(order_stats.get("mid_order") or {}),
                "small": self._compact_bucket(order_stats.get("small_order") or {}),
            },
        }

    def get_history_funds_flow(self, name: str, days: int = 20) -> Dict[str, Any]:
        resolved = self._resolve_stock_identity(name)
        window = max(1, min(int(days or 20), 120))
        snapshot = self._service.get_stock_funds_snapshot(code=resolved["code"], n_days=window, include_flow_news=False)
        history_rows = snapshot.get("historical_table") if isinstance(snapshot.get("historical_table"), list) else []
        history: List[Dict[str, Any]] = []
        for item in history_rows[:window]:
            if not isinstance(item, dict):
                continue
            history.append(
                {
                    "date": item.get("date"),
                    "net_inflow_wan": item.get("net_inflow_wan"),
                    "main_net_inflow_wan": item.get("main_net_wan"),
                }
            )
        return {
            "name": resolved["name"],
            "code": resolved["code"],
            "source": str(snapshot.get("source") or "ths"),
            "history": history,
        }

    def get_industry_funds_flow(self, name: str) -> Dict[str, Any]:
        resolved = self._resolve_stock_identity(name)
        snapshot = self._service.get_stock_funds_snapshot(code=resolved["code"], n_days=10, include_flow_news=False)
        industry = snapshot.get("industry_funds") if isinstance(snapshot.get("industry_funds"), dict) else {}

        def _compact_company_list(items: Any) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "stock_name": item.get("stock_name"),
                        "stock_code": item.get("stock_code"),
                        "main_net_inflow_wan": item.get("main_order_net_inflow_wan"),
                    }
                )
            return rows

        return {
            "name": resolved["name"],
            "code": resolved["code"],
            "source": str(snapshot.get("source") or "ths"),
            "industry_name": industry.get("industry_name"),
            "industry_net_flow_wan": industry.get("industry_net_flow_wan"),
            "top_buy": _compact_company_list(industry.get("top_buy")),
            "top_sell": _compact_company_list(industry.get("top_sell")),
        }


def run_realtime_funds_flow(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = StockFundsSplitTools().get_realtime_funds_flow(
            name=str(params.get("name") or params.get("code") or params.get("query") or "").strip()
        )
        return {"tool": "stock_realtime_funds_flow", "ok": True, "data": payload, "error": ""}
    except Exception as exc:
        return {"tool": "stock_realtime_funds_flow", "ok": False, "data": {}, "error": str(exc)}


def run_history_funds_flow(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = StockFundsSplitTools().get_history_funds_flow(
            name=str(params.get("name") or params.get("code") or params.get("query") or "").strip(),
            days=int(params.get("days", 20) or 20),
        )
        return {"tool": "stock_history_funds_flow", "ok": True, "data": payload, "error": ""}
    except Exception as exc:
        return {"tool": "stock_history_funds_flow", "ok": False, "data": {}, "error": str(exc)}


def run_industry_funds_flow(args: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(args or {})
    try:
        payload = StockFundsSplitTools().get_industry_funds_flow(
            name=str(params.get("name") or params.get("code") or params.get("query") or "").strip()
        )
        return {"tool": "stock_industry_funds_flow", "ok": True, "data": payload, "error": ""}
    except Exception as exc:
        return {"tool": "stock_industry_funds_flow", "ok": False, "data": {}, "error": str(exc)}
