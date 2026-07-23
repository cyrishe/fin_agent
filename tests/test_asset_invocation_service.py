import json

from src.services.asset_invocation_service import AssetInvocationService


class FakeStore:
    def exists(self, _name):
        return False


def write_tool(tmp_path, name, schema):
    definitions = tmp_path / "definitions"
    definitions.mkdir(exist_ok=True)
    (definitions / f"{name}.tool.json").write_text(
        json.dumps(
            {
                "identity": {"display_name": name, "description": "test tool"},
                "schemas": {"input": schema},
            }
        ),
        encoding="utf-8",
    )
    return definitions


def build_service(tmp_path, *, schema, response=None, llm=None, stock_identity_resolver=None):
    definitions = write_tool(tmp_path, "demo_tool", schema)

    def default_llm(_messages, **_kwargs):
        return response, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    return AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(definitions),
        skills_root=str(tmp_path / "skills"),
        llm_chat=llm or default_llm,
        stock_identity_resolver=stock_identity_resolver,
    )


def test_compiles_natural_language_into_single_tool_call(tmp_path):
    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock"],
            "properties": {"stock": {"type": "string"}},
        },
        response={
            "status": "ready",
            "arguments": {"stock": "贵州茅台"},
            "execution": {"mode": "single", "item_argument": "", "items": []},
            "missing_required": [],
            "reason": "参数已识别",
        },
    )

    result = service.plan(text="$demo_tool 查一下贵州茅台")

    assert result["status"] == "ready"
    assert result["calls"] == [{"stock": "贵州茅台"}]
    assert result["execution"]["mode"] == "single"


def test_compiles_attachment_request_into_map_calls(tmp_path):
    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock"],
            "properties": {"stock": {"type": "string"}, "window": {"type": "integer"}},
        },
        response={
            "status": "ready",
            "arguments": {"window": 30},
            "execution": {
                "mode": "map",
                "item_argument": "stock",
                "items": ["贵州茅台", "五粮液"],
            },
            "missing_required": [],
            "reason": "按附件中的股票逐一执行",
        },
    )

    result = service.plan(
        text="$demo_tool 用这个工具跑一下附件里的股票",
        attachments=[{"attachment_id": "a1", "file_name": "stocks.csv", "kind": "binary"}],
    )
    execution_plan = service.build_tool_execution_plan(result)

    assert result["status"] == "ready"
    assert result["calls"] == [
        {"window": 30, "stock": "贵州茅台"},
        {"window": 30, "stock": "五粮液"},
    ]
    assert len(execution_plan["work_items"]) == 2


def test_reports_only_real_missing_required_fields(tmp_path):
    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock"],
            "properties": {"stock": {"type": "string"}},
        },
        response={
            "status": "needs_input",
            "arguments": {},
            "execution": {"mode": "single", "item_argument": "", "items": []},
            "missing_required": ["stock"],
            "reason": "缺少标的",
        },
    )

    result = service.plan(text="$demo_tool 帮我运行")

    assert result["status"] == "needs_input"
    assert result["missing_required"] == ["stock"]


def test_no_parameter_tool_can_run_without_llm(tmp_path):
    def unexpected_llm(*_args, **_kwargs):
        raise AssertionError("no-input tool should not call the LLM")

    service = build_service(
        tmp_path,
        schema={"type": "object", "properties": {}},
        llm=unexpected_llm,
    )

    result = service.plan(text="$demo_tool")

    assert result["status"] == "ready"
    assert result["calls"] == [{}]


def test_development_test_can_plan_an_owned_inactive_custom_tool(tmp_path):
    class DraftStore:
        def __init__(self):
            self.allow_inactive = None

        def exists(self, name):
            return name == "draft_tool"

        def load_for_runtime(self, name, *, owner_ids, allow_inactive):
            assert name == "draft_tool"
            assert owner_ids == ["owner_a"]
            self.allow_inactive = allow_inactive
            return {
                "manifest": {"display_name": "草稿工具", "description": "开发中"},
                "input_schema": {"type": "object", "properties": {}},
                "design_contract": {},
                "sample_input": {},
            }

    store = DraftStore()
    service = AssetInvocationService(
        custom_tool_store=store,
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not run")),
    )

    result = service.plan(
        text="$draft_tool",
        owner_ids=["owner_a"],
        allow_inactive=True,
    )

    assert result["status"] == "ready"
    assert store.allow_inactive is True


def test_skill_defaults_to_a_natural_language_question(tmp_path):
    skills = tmp_path / "skills"
    skill_dir = skills / "demo_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.json").write_text("{}", encoding="utf-8")

    def llm(_messages, **_kwargs):
        return {
            "status": "ready",
            "arguments": {"question": "分析贵州茅台"},
            "execution": {"mode": "single", "item_argument": "", "items": []},
            "missing_required": [],
            "reason": "已整理任务",
        }, {}

    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(skills),
        llm_chat=llm,
    )

    result = service.plan(text="$demo_skill 帮我分析贵州茅台")

    assert result["target"] == {"kind": "skill", "name": "demo_skill"}
    assert result["calls"] == [{"question": "分析贵州茅台"}]


def test_resolves_stock_entity_and_builds_visible_preview(tmp_path):
    class Resolver:
        def resolve(self, value):
            assert value == "贵州茅台"
            return {"kind": "stock", "query": value, "code": "600519.SH", "name": "贵州茅台"}

    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock_code"],
            "properties": {
                "stock_code": {"type": "string", "title": "股票"},
                "window": {"type": "integer", "title": "观察窗口"},
            },
        },
        response={
            "status": "ready",
            "arguments": {"stock_code": "贵州茅台", "window": 30},
            "execution": {"mode": "single", "item_argument": "", "items": []},
            "entities": [{"kind": "stock", "argument": "stock_code"}],
            "missing_required": [],
            "reason": "参数已识别",
        },
        stock_identity_resolver=Resolver(),
    )

    result = service.plan(text="$demo_tool 看一下贵州茅台最近30天")

    assert result["calls"] == [{"stock_code": "600519.SH", "window": 30}]
    assert result["resolved_entities"] == [{
        "kind": "stock",
        "query": "贵州茅台",
        "code": "600519.SH",
        "name": "贵州茅台",
        "argument": "stock_code",
    }]
    assert "贵州茅台（600519）" in result["preview"]["message"]
    assert ".SH" not in result["preview"]["message"]
    assert result["preview"]["entities"] == [{"kind": "stock", "name": "贵州茅台", "display_code": "600519"}]
    assert "观察窗口=30" in result["preview"]["message"]


def test_unresolved_stock_entity_stops_before_execution(tmp_path):
    class Resolver:
        def resolve(self, _value):
            return None

    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock_code"],
            "properties": {"stock_code": {"type": "string"}},
        },
        response={
            "status": "ready",
            "arguments": {"stock_code": "不存在的公司"},
            "execution": {"mode": "single", "item_argument": "", "items": []},
            "entities": [{"kind": "stock", "argument": "stock_code"}],
            "missing_required": [],
            "reason": "参数已识别",
        },
        stock_identity_resolver=Resolver(),
    )

    result = service.plan(text="$demo_tool 不存在的公司")

    assert result["status"] == "needs_input"
    assert "未能确认股票标的" in result["message"]


def test_resolves_each_stock_in_an_array_argument(tmp_path):
    class Resolver:
        identities = {
            "贵州茅台": {"kind": "stock", "query": "贵州茅台", "code": "600519.SH", "name": "贵州茅台"},
            "000001.SZ": {"kind": "stock", "query": "000001.SZ", "code": "000001.SZ", "name": "平安银行"},
        }

        def resolve(self, value):
            return self.identities.get(value)

    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "properties": {"stock_list": {"type": "array", "items": {"type": "string"}}},
        },
        response={
            "status": "ready",
            "arguments": {"stock_list": ["贵州茅台", "000001.SZ"]},
            "execution": {"mode": "single", "item_argument": "", "items": []},
            "entities": [{"kind": "stock", "argument": "stock_list"}],
            "missing_required": [],
            "reason": "参数已识别",
        },
        stock_identity_resolver=Resolver(),
    )

    result = service.plan(text="$demo_tool 扫描贵州茅台和平安银行")

    assert result["status"] == "ready"
    assert result["calls"] == [{"stock_list": ["600519.SH", "000001.SZ"]}]
    assert [item["name"] for item in result["resolved_entities"]] == ["贵州茅台", "平安银行"]


def test_design_labels_enrich_the_runtime_schema_without_changing_field_names():
    schema = AssetInvocationService._with_design_labels(
        {"type": "object", "properties": {"report_period": {"type": "string"}}},
        {"inputs": [{"name": "report_period", "label": "报告期"}]},
    )

    assert schema["properties"]["report_period"] == {"type": "string", "title": "报告期"}
