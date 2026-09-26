from pathlib import Path

from src.services.codex_exec_skill_harness import CodexCustomToolDesigner
from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService
from src.services.custom_tool_service import CustomToolAgentService, CustomToolStoreService


class _Harness:
    def __init__(self):
        self.calls = []

    def run_skill(self, **kwargs):
        self.calls.append(kwargs)
        stage = kwargs["stage"]
        if stage == "requirement":
            final = {
                "summary": "按双均线交叉识别信号，是否按此实现？",
                "requirement_brief": "识别指定股票最近60个交易日内的收盘价金叉并返回信号日期。",
                "questions": [],
            }
        elif stage == "design":
            final = {
                "summary": "模块和流程已经形成。",
                "change_summary": [],
                "design": {
                    "tool_name": "ct_golden_cross",
                    "display_name": "金叉识别",
                    "description": "识别近期金叉。",
                    "inputs": [],
                    "outputs": [],
                    "modules": [],
                    "rules": [],
                    "data_requirements": [],
                    "exceptions": [],
                    "acceptance": [],
                },
            }
        else:
            final = {
                "mermaid": "flowchart TD",
            }
        return {"ok": True, "events": [], "final": final}


def test_requirement_confirmation_does_not_hard_gate_on_questions() -> None:
    harness = _Harness()
    result = CodexCustomToolDesigner(harness=harness).design(
        "做一个金叉工具",
        context={"selected_skills": ["financial-tool-requirement"]},
    )

    assert [call["stage"] for call in harness.calls] == ["requirement", "design", "flowchart"]
    assert result["design"]["tool_name"] == "ct_golden_cross"
    assert "识别指定股票" in result["understanding"]["requirement_brief"]


def test_requirement_confirmation_stops_when_questions_remain() -> None:
    class QuestionHarness(_Harness):
        def run_skill(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["stage"] == "requirement":
                return {
                    "ok": True,
                    "events": [],
                    "final": {
                        "summary": "需要确认窗口。",
                        "requirement_brief": "识别指定股票的金叉。",
                        "questions": [{"question": "窗口是多少？", "candidate": ["30日", "60日"]}],
                    },
                }
            return super().run_skill(**kwargs)

    harness = QuestionHarness()
    result = CodexCustomToolDesigner(harness=harness).design(
        "做一个金叉工具",
        context={"selected_skills": ["financial-tool-requirement"]},
    )

    assert [call["stage"] for call in harness.calls] == ["requirement"]
    assert result["design"] == {}
    assert result["questions"][0]["candidate"][0] == "30日"


def test_requirement_questions_stop_even_when_planner_preselected_later_skills() -> None:
    class QuestionHarness(_Harness):
        def run_skill(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["stage"] == "requirement":
                return {
                    "ok": True,
                    "events": [],
                    "final": {
                        "summary": "需要先确认核心窗口。",
                        "requirement_brief": "识别指定股票的金叉。",
                        "questions": [{"question": "使用哪个窗口？", "candidate": ["30日", "60日"]}],
                    },
                }
            return super().run_skill(**kwargs)

    harness = QuestionHarness()
    result = CodexCustomToolDesigner(harness=harness).design(
        "做一个金叉工具",
        context={
            "selected_skills": [
                "financial-tool-requirement",
                "financial-tool-design",
                "financial-tool-flowchart",
            ]
        },
    )

    assert [call["stage"] for call in harness.calls] == ["requirement"]
    assert result["design"] == {}
    assert result["questions"][0]["candidate"][0] == "30日"


def test_design_and_flowchart_execute_in_order_and_merge_as_assets() -> None:
    harness = _Harness()
    result = CodexCustomToolDesigner(harness=harness).design(
        "按已确认需求设计",
        context={
            "selected_skills": ["financial-tool-design", "financial-tool-flowchart"],
            "requirement_brief": "识别指定股票的金叉。",
        },
    )

    assert [call["stage"] for call in harness.calls] == ["design", "flowchart"]
    assert result["design"]["tool_name"] == "ct_golden_cross"
    assert result["design"]["mermaid"] == "flowchart TD"


def test_design_modification_context_is_materialized_as_file_reference(tmp_path: Path) -> None:
    service = CustomToolContextBundleService(
        catalog_path=str(tmp_path / "missing.json"),
        root_dir=str(tmp_path / "bundles"),
    )
    bundle = service.build(
        stage="design",
        user_request="只调整计算顺序",
        context={
            "requirement_brief": "识别指定股票的金叉。",
            "current_design": {"tool_name": "ct_golden_cross", "modules": []},
            "_workspace_identity": {"owner_id": "u1"},
        },
    )

    prompt_context = service.prompt_context(bundle, {})
    assert prompt_context == {"requirement_brief": "识别指定股票的金叉。", "design_ref": "design.json"}
    assert Path(bundle["bundle_dir"], "design.json").is_file()


def test_test_context_exposes_data_catalog_universe_and_history_as_files(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        '{"version":"v1","subjects":{"stock":{"_meta":{"desc":"股票"},"basic_info":{"desc":"股票列表"}},"industry":{"_meta":{"desc":"行业"},"basic_info":{"desc":"行业列表"}}},"api_class_patterns":{}}',
        encoding="utf-8",
    )
    universe_path = tmp_path / "stocks.tsv"
    universe_path.write_text("600519.SH\t贵州茅台\n", encoding="utf-8")
    service = CustomToolContextBundleService(
        catalog_path=str(catalog_path),
        stock_universe_path=str(universe_path),
        root_dir=str(tmp_path / "bundles"),
    )

    bundle = service.build(
        stage="test",
        user_request="扩大范围直到找到有效样例",
        context={
            "tool_contract": {"input_schema": {"type": "object"}},
            "test_history": [{"turn": 1, "executions": [{"actual": {"total_count": 0}}]}],
            "_workspace_identity": {"owner_id": "u1"},
        },
    )

    prompt_context = service.prompt_context(bundle, {})
    assert prompt_context["test_history_ref"] == "test_data/test_history.json"
    assert Path(bundle["bundle_dir"], bundle["api_index"]).is_file()
    assert Path(bundle["bundle_dir"], bundle["stock_universe"]).read_text(encoding="utf-8") == "600519.SH\t贵州茅台\n"
    assert "test_history" not in prompt_context


def test_runtime_state_records_assets_without_business_workflow_status(tmp_path: Path) -> None:
    class Designer:
        def design(self, requirement_text, **kwargs):
            return {
                "ok": True,
                "message": "方案已形成。",
                "understanding": {"goal": "识别金叉"},
                "questions": [],
                "design": {"tool_name": "ct_golden_cross", "display_name": "金叉识别"},
                "existing_analysis": {},
                "events": [],
            }

    service = CustomToolAgentService(
        store=CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem"),
        designer=Designer(),
        use_codex=False,
    )
    result = service.start_create(
        "做一个金叉工具",
        selected_skills=["financial-tool-design", "financial-tool-flowchart"],
    )

    assert result["design_status"] == "review"
    assert "status" not in result["state"]
    assert result["state"]["design_contract"]["tool_name"] == "ct_golden_cross"


def test_requirement_state_does_not_create_an_empty_design_artifact(tmp_path: Path) -> None:
    class RequirementDesigner:
        def design(self, requirement_text, **kwargs):
            return {
                "ok": True,
                "message": "我理解为识别近期金叉，是否按此实现？",
                "understanding": {"goal": "识别近期金叉"},
                "questions": [],
                "design": {},
                "existing_analysis": {},
                "events": [],
            }

    service = CustomToolAgentService(
        store=CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem"),
        designer=RequirementDesigner(),
        use_codex=False,
    )
    result = service.start_create(
        "做一个金叉工具",
        selected_skills=["financial-tool-requirement"],
    )

    assert result["design_status"] == "clarification"
    assert "partial_design" not in result["state"]
    assert "design_artifact_id" not in result["state"]
    assert "design_revision" not in result["state"]
    assert "design_fingerprint" not in result["state"]
