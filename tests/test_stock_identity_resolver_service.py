from src.services.stock_identity_resolver_service import StockIdentityResolverService


class FakeRuntime:
    def __init__(self, rows_by_filter):
        self.rows_by_filter = rows_by_filter
        self.requests = []

    def execute_request(self, *, request):
        self.requests.append(request)
        rows = next((rows for key, rows in self.rows_by_filter.items() if key in request), [])
        return {
            "validation": {"ok": True},
            "result": {"data": {"rows": rows}},
        }


def test_resolves_company_name_through_stock_basic_info():
    runtime = FakeRuntime({"name = 贵州茅台": [{"code": "600519.SH", "name": "贵州茅台"}]})
    result = StockIdentityResolverService(runtime=runtime).resolve("贵州茅台")

    assert result == {"kind": "stock", "query": "贵州茅台", "code": "600519.SH", "name": "贵州茅台"}
    assert len(runtime.requests) == 1
    assert "stock.basic_info" in runtime.requests[0]


def test_resolves_short_code_without_guessing_exchange():
    runtime = FakeRuntime({"code = 600519.SH": [{"code": "600519.SH", "name": "贵州茅台"}]})
    result = StockIdentityResolverService(runtime=runtime).resolve("600519")

    assert result["code"] == "600519.SH"
    assert len(runtime.requests) == 3
