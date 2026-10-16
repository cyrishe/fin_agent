from pathlib import Path
import pytest

from src.scenarios.financial_qa.dsh_service import FinanceDeepSeekHarnessSessionService
from src.services.finance_data_tool_catalog_service import FinanceDataToolCatalogService
from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call
from src.experiments.staged_data_protocol.phase2.call_validator import validate_call
from src.experiments.staged_data_protocol.phase2.models import ResultHandle


def test_independent_service_instances_cannot_overwrite_worker_context(tmp_path: Path):
    first, second = [FinanceDeepSeekHarnessSessionService(
        enabled=False, root_dir=tmp_path, worker_count=1,
    ) for _ in range(2)]
    a, b = first._workers[0], second._workers[0]
    assert a.home != b.home
    assert a.context_path != b.context_path
    assert a.trace_path != b.trace_path
    a.context_path.parent.mkdir(parents=True)
    b.context_path.parent.mkdir(parents=True)
    a.context_path.write_text('{"revision":"api"}')
    b.context_path.write_text('{"revision":"chat"}')
    assert a.context_path.read_text() == '{"revision":"api"}'


def test_catalog_annotations_are_not_executable_output_fields():
    catalog = FinanceDataToolCatalogService()
    source = catalog.load_raw_catalog()
    examples_checked = 0
    for subject, views in source["subjects"].items():
        for view, spec in views.items():
            if view.startswith("_") or not isinstance(spec, dict):
                continue
            model = catalog.get_model_dataview(subject, view)
            for function in model["functions"]:
                for example in function.get("examples", []):
                    call = parse_api_call(example)
                    assert all("note:" not in field for field in call.outputs)
                    examples_checked += 1
    assert examples_checked > 0


def test_result_set_reference_repair_preserves_comparison_meaning():
    handles = {"r1": ResultHandle(name="r1", api="stock.basic_info", columns=["code", "industry"],
                                 data={"rows": [{"code": "300750.SZ", "industry": "锂电池"}]})}
    invalid = parse_api_call('r2 = industry.constitution(filter="industry_name in r1.industry and stock_code != r1.code", limit=-1) -> stock_code, stock_name')
    validation = validate_call(invalid, handles)
    assert not validation.ok
    assert any("Keep the original comparison meaning" in e for e in validation.errors)
    valid = parse_api_call('r2 = industry.constitution(filter="industry_name in r1.industry and stock_code != 300750.SZ", limit=-1) -> stock_code, stock_name')
    assert validate_call(valid, handles).ok


def test_parser_enforces_one_invocation_without_corrupting_arguments():
    single = 'r1 = stock.quote(filter="name = 某公司(集团)", limit=5) -> code, name'
    assert parse_api_call(single).args == {"filter": "name = 某公司(集团)", "limit": 5}
    nested = 'r2 = plate.constitution.agg(agg=avg(stock.quote.pct), group_by="plate_code") -> plate_code, avg_pct'
    assert parse_api_call(nested).args["agg"] == "avg(stock.quote.pct)"
    for separator in ("\n", "; ", " "):
        with pytest.raises(ValueError, match="one API call"):
            parse_api_call(single + separator + nested)
    with pytest.raises(ValueError, match="unclosed"):
        parse_api_call('r1 = stock.quote(filter="name = 未关闭) -> code')
