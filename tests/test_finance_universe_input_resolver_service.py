from src.services.finance_universe_input_resolver_service import (
    FinanceUniverseInputResolverService,
)


class _Runtime:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def execute_request(self, *, request):
        self.requests.append(request)
        return self.payloads.pop(0)


def _payload(rows, *, name_resolution=None):
    data = {"status": "ok", "rows": rows}
    if name_resolution is not None:
        data["name_resolution"] = name_resolution
    return {
        "ok": True,
        "validation": {"ok": True, "errors": []},
        "execution": {"ok": True, "status": "ok"},
        "result": {"data": data},
    }


def test_resolves_named_plate_then_materializes_all_members() -> None:
    runtime = _Runtime(
        [
            _payload([{"plate_code": "883643", "plate_name": "CPO"}]),
            _payload(
                [
                    {
                        "plate_code": "883643",
                        "plate_name": "CPO",
                        "stock_code": "300502.SZ",
                        "stock_name": "新易盛",
                    },
                    {
                        "plate_code": "883643",
                        "plate_name": "CPO",
                        "stock_code": "300308.SZ",
                        "stock_name": "中际旭创",
                    },
                ]
            ),
        ]
    )
    service = FinanceUniverseInputResolverService(runtime=runtime)

    result = service.resolve(
        {"kind": "finance_universe", "subject_type": "plate", "query": "CPO板块"}
    )

    assert result["status"] == "ready"
    assert result["items"] == ["300502.SZ", "300308.SZ"]
    assert result["resolved_subject"] == {
        "code": "883643",
        "name": "CPO",
        "subject_type": "plate",
    }
    assert "plate.basic_info" in runtime.requests[0]
    assert "plate.constitution" in runtime.requests[1]
    assert "plate_code = 883643" in runtime.requests[1]


def test_index_adapts_basic_info_fields_to_constitution_fields() -> None:
    runtime = _Runtime(
        [
            _payload([{"code": "000300.SH", "name": "沪深300"}]),
            _payload(
                [
                    {
                        "index_code": "000300.SH",
                        "index_name": "沪深300",
                        "stock_code": "600519.SH",
                        "stock_name": "贵州茅台",
                    }
                ]
            ),
        ]
    )
    service = FinanceUniverseInputResolverService(runtime=runtime)

    result = service.resolve(
        {"kind": "finance_universe", "subject_type": "index", "query": "沪深300"}
    )

    assert result["status"] == "ready"
    assert result["items"] == ["600519.SH"]
    assert "filter = \"name = 沪深300\"" in runtime.requests[0]
    assert "-> code, name" in runtime.requests[0]
    assert "filter = \"index_code = 000300.SH\"" in runtime.requests[1]
    assert "-> index_code, index_name, stock_code, stock_name" in runtime.requests[1]


def test_ambiguous_finance_universe_returns_selection_instead_of_guessing() -> None:
    runtime = _Runtime(
        [
            _payload(
                [],
                name_resolution={
                    "status": "ambiguous",
                    "candidates": [
                        {"plate_code": "A", "plate_name": "光模块"},
                        {"plate_code": "B", "plate_name": "高速光模块"},
                    ],
                },
            )
        ]
    )
    service = FinanceUniverseInputResolverService(runtime=runtime)

    result = service.resolve(
        {"kind": "finance_universe", "subject_type": "plate", "query": "光"}
    )

    assert result["status"] == "needs_selection"
    assert result["items"] == []
    assert result["candidates"] == [
        {"code": "A", "name": "光模块"},
        {"code": "B", "name": "高速光模块"},
    ]
