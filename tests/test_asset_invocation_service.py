import io
import json
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from src.services.attachment_service import AttachmentService
from src.services.asset_invocation_service import AssetInvocationError, AssetInvocationService
from src.services.file_io_tool_service import FileIoToolService
from src.services.invocation_input_resolver_service import InvocationInputResolverService
from src.services.skill_studio_service import SkillStudioService


class FakeStore:
    def exists(self, _name):
        return False


class FakeStrategyStore:
    def exists(self, name):
        return name == "strategy_tool"

    def load_for_runtime(self, name, **_kwargs):
        assert name == "strategy_tool"
        return {
            "manifest": {
                "tool_name": name,
                "display_name": "单股策略",
                "description": "逐股判断信号。",
                "status": "active",
                "current_revision": 2,
                "capabilities": ["custom_tool", "strategy"],
            },
            "input_schema": {
                "type": "object",
                "required": ["stock_code"],
                "properties": {"stock_code": {"type": "string"}},
            },
            "design_contract": {},
            "finance_tool_profile": {
                "protocol": "finance_tool_profile.v1",
                "family": "strategy",
                "execution_shape": "entity_local",
                "output_semantic": "signal",
            },
            "strategy_runtime_profile": {
                "protocol": "strategy_runtime_profile.v1",
                "binding": {"field": "stock_code"},
                "required_history_sessions": 10,
                "default_run_sessions": 1,
                "default_universe_ref": {},
                "market_code": "CN",
            },
        }


class FakeEntityListStore:
    def __init__(self, execution_shape="entity_local"):
        self.execution_shape = execution_shape

    def exists(self, name):
        return name == "entity_list_tool"

    def load_for_runtime(self, name, **_kwargs):
        assert name == "entity_list_tool"
        return {
            "manifest": {
                "tool_name": name,
                "display_name": "多目标质量诊断",
                "description": "对目标列表中的每只股票独立诊断。",
                "status": "active",
                "current_revision": 1,
                "capabilities": ["custom_tool"],
            },
            "input_schema": {
                "type": "object",
                "required": ["stock_codes"],
                "properties": {
                    "stock_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "window": {"type": "integer", "default": 20},
                },
            },
            "design_contract": {},
            "finance_tool_profile": {
                "protocol": "finance_tool_profile.v1",
                "family": "analytics",
                "execution_shape": self.execution_shape,
                "output_semantic": "assessment",
            },
        }


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


def write_skill(
    skills_root,
    name,
    *,
    purpose="测试 Skill",
    auth="public",
    lifecycle="active",
    display_name="",
    input_schema=None,
):
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        json.dumps(
            {
                "skill_name": name,
                "display_name": display_name,
                "purpose": purpose,
                "auth": auth,
                "availability": {
                    "lifecycle": lifecycle,
                    "retrieval_mode": "direct_only",
                },
                "input_schema": input_schema
                or {
                    "type": "object",
                    "required": ["question"],
                    "properties": {
                        "question": {
                            "type": "string",
                            "title": "分析要求",
                            "description": "希望该 Skill 完成的任务",
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (skill_dir / "schema.json").write_text(
        json.dumps({"type": "object", "properties": {}}),
        encoding="utf-8",
    )
    return skill_dir


def build_service(
    tmp_path,
    *,
    schema,
    response=None,
    llm=None,
    stock_identity_resolver=None,
    attachment_service=None,
):
    definitions = write_tool(tmp_path, "demo_tool", schema)

    def default_llm(_messages, **_kwargs):
        return response, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    return AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(definitions),
        skills_root=str(tmp_path / "skills"),
        llm_chat=llm or default_llm,
        stock_identity_resolver=stock_identity_resolver,
        attachment_service=attachment_service,
        file_io_service=FileIoToolService(),
    )


def test_compact_context_uses_runtime_text_shape_and_keeps_roles(tmp_path):
    service = build_service(
        tmp_path,
        schema={"type": "object", "properties": {}},
        response={},
    )

    context = service._compact_context(  # noqa: SLF001
        {
            "context_window": [
                {"role": "user", "text": "比较贵州茅台、五粮液和泸州老窖"},
                {"role": "assistant", "text": "已返回三家公司结果。"},
            ]
        }
    )

    assert context["recent_user_questions"] == [
        "比较贵州茅台、五粮液和泸州老窖"
    ]
    assert context["recent_turns"] == [
        {"role": "user", "text": "比较贵州茅台、五粮液和泸州老窖"},
        {"role": "assistant", "text": "已返回三家公司结果。"},
    ]


def upload_text(attachment_service, *, file_name, content, mime_type):
    return attachment_service.save_upload(
        FileStorage(
            stream=io.BytesIO(content),
            filename=file_name,
            content_type=mime_type,
        )
    )


def stock_resolver():
    class Resolver:
        identities = {
            "贵州茅台": {"kind": "stock", "query": "贵州茅台", "code": "600519.SH", "name": "贵州茅台"},
            "五粮液": {"kind": "stock", "query": "五粮液", "code": "000858.SZ", "name": "五粮液"},
        }

        def resolve(self, value):
            return self.identities.get(value)

    return Resolver()


def xlsx_bytes(rows):
    from openpyxl import Workbook

    output = io.BytesIO()
    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    workbook.save(output)
    return output.getvalue()


def docx_bytes_with_stock_table():
    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>待分析公司</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>公司</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>贵州茅台</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>五粮液</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


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


def test_explicit_natural_language_tool_name_uses_the_same_invocation_path(tmp_path):
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
            "execution": {"mode": "single", "item_argument": "", "items": [], "source": {}},
            "missing_required": [],
            "reason": "参数已识别",
        },
    )
    text = "请用 demo_tool 工具跑一下贵州茅台"

    assert service.has_explicit_invocation(text=text) is True
    assert service.detect_target(text=text) == {"kind": "tool", "name": "demo_tool"}
    assert service.strip_invocation_prefix(text, target_name="demo_tool") == "跑一下贵州茅台"
    assert service.plan(text=text)["calls"] == [{"stock": "贵州茅台"}]


@pytest.mark.parametrize(
    "text",
    [
        "请用系统中的 demo_tool 工具跑一下贵州茅台",
        "使用系统里的「demo_tool」工具分析贵州茅台",
        "调用平台内 demo_tool tool 查询贵州茅台",
        "用系统中demo_tool工具帮我算一下贵州茅台",
        "运行一下demo_tool工具查询贵州茅台",
        "执行 demo_tool 工具分析贵州茅台",
    ],
)
def test_explicit_system_named_tool_phrasing_uses_invocation_path(tmp_path, text):
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
            "execution": {"mode": "single", "item_argument": "", "items": [], "source": {}},
            "missing_required": [],
            "reason": "参数已识别",
        },
    )

    assert service.has_explicit_invocation(text=text) is True
    assert service.detect_target(text=text) == {"kind": "tool", "name": "demo_tool"}
    assert service.plan(text=text)["calls"] == [{"stock": "贵州茅台"}]


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


def test_materializes_full_excel_column_for_scalar_tool_and_deduplicates(tmp_path):
    attachment_service = AttachmentService(data_root=tmp_path / "data")
    attachment = upload_text(
        attachment_service,
        file_name="stocks.xlsx",
        content=xlsx_bytes([
            ["公司", "备注"],
            ["贵州茅台", "核心"],
            ["五粮液", "关注"],
            ["贵州茅台", "重复"],
        ]),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock"],
            "properties": {"stock": {"type": "string"}},
        },
        response={
            "status": "ready",
            "arguments": {},
            "execution": {
                "mode": "map",
                "item_argument": "stock",
                "items": [],
                "source": {
                    "attachment_id": attachment["attachment_id"],
                    "table_index": 0,
                    "column": "公司",
                },
            },
            "entities": [{"kind": "stock", "argument": "stock"}],
            "missing_required": [],
            "reason": "从公司列读取标的",
        },
        stock_identity_resolver=stock_resolver(),
        attachment_service=attachment_service,
    )

    result = service.plan(
        text="$demo_tool 跑一下附件中的公司",
        attachments=[attachment],
    )

    assert result["status"] == "ready"
    assert result["execution"]["mode"] == "map"
    assert result["execution"]["item_count"] == 2
    assert result["calls"] == [
        {"stock": "600519.SH"},
        {"stock": "000858.SZ"},
    ]
    assert service.build_tool_execution_plan(result)["work_items"][1]["arguments"] == {
        "stock": "000858.SZ"
    }


def test_array_tool_receives_one_call_from_the_same_attachment_source(tmp_path):
    attachment_service = AttachmentService(data_root=tmp_path / "data")
    attachment = upload_text(
        attachment_service,
        file_name="stocks.xlsx",
        content=xlsx_bytes([["公司"], ["贵州茅台"], ["五粮液"]]),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock_list"],
            "properties": {"stock_list": {"type": "array", "items": {"type": "string"}}},
        },
        response={
            "status": "ready",
            "arguments": {},
            "execution": {
                "mode": "map",
                "item_argument": "stock_list",
                "items": [],
                "source": {
                    "attachment_id": attachment["attachment_id"],
                    "table_index": 0,
                    "column": "公司",
                },
            },
            "entities": [{"kind": "stock", "argument": "stock_list"}],
            "missing_required": [],
            "reason": "批量工具直接接收公司列表",
        },
        stock_identity_resolver=stock_resolver(),
        attachment_service=attachment_service,
    )

    result = service.plan(
        text="$demo_tool 跑一下附件中的公司",
        attachments=[attachment],
    )

    assert result["execution"]["mode"] == "single"
    assert result["calls"] == [{"stock_list": ["600519.SH", "000858.SZ"]}]
    assert len(service.build_tool_execution_plan(result)["work_items"]) == 1


def test_entity_local_array_contract_receives_one_complete_target_list(tmp_path):
    def llm(_messages, **_kwargs):
        return {
            "status": "ready",
            "arguments": {
                "stock_codes": ["贵州茅台", "五粮液"],
                "window": 30,
            },
            "execution": {
                "mode": "single",
                "item_argument": "",
                "items": [],
                "source": {},
            },
            "entities": [{"kind": "stock", "argument": "stock_codes"}],
            "missing_required": [],
            "reason": "按同一规则诊断两只股票。",
        }, {}

    service = AssetInvocationService(
        custom_tool_store=FakeEntityListStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=llm,
        stock_identity_resolver=stock_resolver(),
    )

    result = service.plan(text="$entity_list_tool 诊断贵州茅台和五粮液")

    assert result["status"] == "ready"
    assert result["execution"]["mode"] == "single"
    assert result["calls"] == [
        {"stock_codes": ["600519.SH", "000858.SZ"], "window": 30},
    ]
    assert "研究范围：共 2 个目标" in result["preview"]["message"]
    assert len(service.build_tool_execution_plan(result)["work_items"]) == 1


def test_cross_sectional_array_contract_keeps_the_complete_universe_in_one_call(tmp_path):
    def llm(_messages, **_kwargs):
        return {
            "status": "ready",
            "arguments": {"stock_codes": ["600519.SH", "000858.SZ"]},
            "execution": {
                "mode": "single",
                "item_argument": "",
                "items": [],
                "source": {},
            },
            "entities": [],
            "missing_required": [],
            "reason": "在完整集合中横向比较。",
        }, {}

    service = AssetInvocationService(
        custom_tool_store=FakeEntityListStore(execution_shape="cross_sectional"),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=llm,
    )

    result = service.plan(text="$entity_list_tool 横向比较两只股票")

    assert result["execution"]["mode"] == "single"
    assert result["calls"] == [{
        "stock_codes": ["600519.SH", "000858.SZ"],
        "window": 20,
    }]
    assert len(service.build_tool_execution_plan(result)["work_items"]) == 1


def test_table_source_materializes_rows_that_are_not_sent_to_the_model(tmp_path):
    attachment_service = AttachmentService(data_root=tmp_path / "data")
    values = [f"公司{index:02d}" for index in range(1, 26)]
    attachment = upload_text(
        attachment_service,
        file_name="many_stocks.xlsx",
        content=xlsx_bytes([["公司"], *[[value] for value in values]]),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    seen_messages = []

    def llm(messages, **_kwargs):
        seen_messages.append(messages)
        return {
            "status": "ready",
            "arguments": {},
            "execution": {
                "mode": "map",
                "item_argument": "stock",
                "items": [],
                "source": {
                    "attachment_id": attachment["attachment_id"],
                    "table_index": 0,
                    "column": "公司",
                },
            },
            "missing_required": [],
            "reason": "按原表公司列逐项运行",
        }, {}

    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock"],
            "properties": {"stock": {"type": "string"}},
        },
        llm=llm,
        attachment_service=attachment_service,
    )

    result = service.plan(text="$demo_tool 处理附件中的全部公司", attachments=[attachment])

    rendered_prompt = "\n".join(str(message.get("content") or "") for message in seen_messages[0])
    assert '"row_count": 25' in rendered_prompt
    assert '"preview_truncated": true' in rendered_prompt
    assert "公司25" not in rendered_prompt
    assert result["calls"][-1] == {"stock": "公司25"}
    assert len(result["calls"]) == 25


def test_txt_upload_and_docx_table_are_available_to_the_invocation_compiler(tmp_path):
    attachment_service = AttachmentService(data_root=tmp_path / "data")
    text_attachment = upload_text(
        attachment_service,
        file_name="stocks.txt",
        content="贵州茅台\n五粮液\n".encode("utf-8"),
        mime_type="text/plain",
    )
    word_attachment = upload_text(
        attachment_service,
        file_name="stocks.docx",
        content=docx_bytes_with_stock_table(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    seen_messages = []

    def llm(messages, **_kwargs):
        seen_messages.append(messages)
        return {
            "status": "ready",
            "arguments": {},
            "execution": {
                "mode": "map",
                "item_argument": "stock",
                "items": [],
                "source": {
                    "attachment_id": word_attachment["attachment_id"],
                    "table_index": 0,
                    "column": "公司",
                },
            },
            "entities": [{"kind": "stock", "argument": "stock"}],
            "missing_required": [],
            "reason": "从 Word 表格读取标的",
        }, {}

    service = build_service(
        tmp_path,
        schema={
            "type": "object",
            "required": ["stock"],
            "properties": {"stock": {"type": "string"}},
        },
        llm=llm,
        stock_identity_resolver=stock_resolver(),
        attachment_service=attachment_service,
    )

    result = service.plan(
        text="$demo_tool 处理这两个附件中的公司",
        attachments=[text_attachment, word_attachment],
    )

    assert text_attachment["kind"] == "document"
    assert result["calls"] == [{"stock": "600519.SH"}, {"stock": "000858.SZ"}]
    rendered_prompt = "\n".join(
        str(message.get("content") or "")
        for message in seen_messages[0]
    )
    assert "贵州茅台" in rendered_prompt
    assert "五粮液" in rendered_prompt
    assert "table_preview" in rendered_prompt


def test_plain_text_line_source_materializes_full_file_without_copying_it_into_prompt(tmp_path, monkeypatch):
    attachment_service = AttachmentService(data_root=tmp_path / "data")
    stock_codes = [f"{index:06d}.SZ" for index in range(1, 1501)]
    attachment = upload_text(
        attachment_service,
        file_name="stocks.txt",
        content=("\n".join(stock_codes) + "\n").encode("utf-8"),
        mime_type="text/plain",
    )
    seen_messages = []

    def llm(messages, **_kwargs):
        seen_messages.append(messages)
        return {
            "status": "ready",
            "arguments": {},
            "execution": {
                "mode": "map",
                "item_argument": "stock_code",
                "items": [],
                "source": {
                    "kind": "attachment_lines",
                    "attachment_id": attachment["attachment_id"],
                },
            },
            "entities": [],
            "missing_required": [],
            "reason": "从纯文本逐行读取标的",
        }, {}

    service = AssetInvocationService(
        custom_tool_store=FakeStrategyStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=llm,
        attachment_service=attachment_service,
        file_io_service=FileIoToolService(),
    )
    monkeypatch.setattr(
        "src.tools.registry.normalize_tool_args_for_definition",
        lambda *_args, **_kwargs: pytest.fail("loaded custom contract must be reused"),
    )

    result = service.plan(text="$strategy_tool 处理附件中的全部股票", attachments=[attachment])

    rendered_prompt = "\n".join(
        str(message.get("content") or "")
        for message in seen_messages[0]
    )
    assert '"document_line_count": 1500' in rendered_prompt
    assert '"document_lines"' not in rendered_prompt
    assert result["status"] == "ready"
    assert result["execution"]["item_count"] == 1500
    assert result["calls"][0] == {"stock_code": "000001.SZ"}
    assert result["calls"][-1] == {"stock_code": "001500.SZ"}


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
    (skill_dir / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (skill_dir / "skill.json").write_text(
        json.dumps({"purpose": "测试分析", "auth": "public"}),
        encoding="utf-8",
    )

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


def test_single_stock_skill_contract_maps_multiple_companies_without_changing_the_skill(tmp_path):
    def llm(messages, **_kwargs):
        rendered = json.dumps(messages, ensure_ascii=False)
        assert "code" in rendered
        assert "单只股票的标准证券代码" in rendered
        return {
            "status": "ready",
            "arguments": {"focus": "资金与风险"},
            "execution": {
                "mode": "map",
                "item_argument": "code",
                "items": ["贵州茅台", "五粮液"],
                "source": {},
            },
            "entities": [{"kind": "stock", "argument": "code"}],
            "missing_required": [],
            "reason": "逐只运行单股票 Skill",
        }, {}

    skills = tmp_path / "skills"
    write_skill(
        skills,
        "single_stock_skill",
        input_schema={
            "type": "object",
            "required": ["code"],
            "properties": {
                "code": {
                    "type": "string",
                    "description": "单只股票的标准证券代码",
                },
                "focus": {"type": "string"},
            },
        },
    )
    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(skills),
        llm_chat=llm,
        stock_identity_resolver=stock_resolver(),
    )

    result = service.plan(text="$single_stock_skill 分析贵州茅台和五粮液，重点看资金与风险")

    assert result["target"] == {"kind": "skill", "name": "single_stock_skill"}
    assert result["calls"] == [
        {"focus": "资金与风险", "code": "600519.SH"},
        {"focus": "资金与风险", "code": "000858.SZ"},
    ]
    assert result["execution"]["mode"] == "map"


def test_strategy_wrapper_resolves_finance_universe_without_changing_tool_schema(tmp_path):
    class UniverseResolver:
        def resolve(self, source):
            assert source == {
                "kind": "finance_universe",
                "subject_type": "plate",
                "query": "CPO板块",
            }
            return {
                "status": "ready",
                "message": "已解析CPO的2只成分股。",
                "items": ["300502.SZ", "300308.SZ"],
                "records": [
                    {"code": "300502.SZ", "name": "新易盛"},
                    {"code": "300308.SZ", "name": "中际旭创"},
                ],
                "member_count": 2,
                "resolved_subject": {
                    "code": "883643",
                    "name": "CPO",
                    "subject_type": "plate",
                },
                "evidence": {
                    "identity_api": "plate.basic_info",
                    "membership_api": "plate.constitution",
                },
            }

    def llm(messages, **_kwargs):
        rendered = json.dumps(messages, ensure_ascii=False)
        assert "strategy_runtime_profile" in rendered
        assert "stock_code" in rendered
        return {
            "status": "ready",
            "arguments": {},
            "execution": {
                "mode": "map",
                "item_argument": "stock_code",
                "items": [],
                "source": {
                    "kind": "finance_universe",
                    "subject_type": "plate",
                    "query": "CPO板块",
                },
            },
            "entities": [{"kind": "stock", "argument": "stock_code"}],
            "missing_required": ["stock_code"],
            "reason": "先解析板块成分，再逐股运行。",
        }, {}

    input_resolver = InvocationInputResolverService(
        finance_universe_resolver=UniverseResolver()
    )
    service = AssetInvocationService(
        custom_tool_store=FakeStrategyStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=llm,
        input_resolver=input_resolver,
    )

    result = service.plan(text="$strategy_tool 扫描一下CPO板块的个股")

    assert result["status"] == "ready"
    assert result["missing_required"] == []
    assert result["calls"] == [
        {"stock_code": "300502.SZ"},
        {"stock_code": "300308.SZ"},
    ]
    assert result["source_resolution"]["resolved_subject"]["name"] == "CPO"
    assert "股票池：CPO，共 2 只" in result["preview"]["message"]
    assert len(service.build_tool_execution_plan(result)["work_items"]) == 2


def test_entity_local_list_tool_resolves_plate_and_injects_one_complete_list(tmp_path):
    class UniverseResolver:
        def resolve(self, source):
            assert source == {
                "kind": "finance_universe",
                "subject_type": "plate",
                "query": "CPO板块",
            }
            return {
                "status": "ready",
                "message": "已解析CPO的2只成分股。",
                "items": ["300502.SZ", "300308.SZ"],
                "records": [
                    {"code": "300502.SZ", "name": "新易盛"},
                    {"code": "300308.SZ", "name": "中际旭创"},
                ],
                "member_count": 2,
                "resolved_subject": {
                    "code": "883643",
                    "name": "CPO",
                    "subject_type": "plate",
                },
                "evidence": {},
            }

    def llm(_messages, **_kwargs):
        return {
            "status": "ready",
            "arguments": {"window": 30},
            "execution": {
                "mode": "map",
                "item_argument": "stock_codes",
                "items": [],
                "source": {
                    "kind": "finance_universe",
                    "subject_type": "plate",
                    "query": "CPO板块",
                },
            },
            "entities": [{"kind": "stock", "argument": "stock_codes"}],
            "missing_required": ["stock_codes"],
            "reason": "解析板块后逐目标诊断。",
        }, {}

    service = AssetInvocationService(
        custom_tool_store=FakeEntityListStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=llm,
        input_resolver=InvocationInputResolverService(
            finance_universe_resolver=UniverseResolver()
        ),
    )

    result = service.plan(text="$entity_list_tool 扫描一下CPO板块的个股")

    assert result["status"] == "ready"
    assert result["missing_required"] == []
    assert result["calls"] == [
        {"stock_codes": ["300502.SZ", "300308.SZ"], "window": 30},
    ]
    assert result["source_resolution"]["member_count"] == 2
    assert result["execution"]["mode"] == "single"
    assert len(service.build_tool_execution_plan(result)["work_items"]) == 1


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
    assert result["execution"]["item_count"] == 2
    assert [item["name"] for item in result["resolved_entities"]] == ["贵州茅台", "平安银行"]


def test_design_labels_enrich_the_runtime_schema_without_changing_field_names():
    schema = AssetInvocationService._with_design_labels(
        {"type": "object", "properties": {"report_period": {"type": "string"}}},
        {"inputs": [{"name": "report_period", "label": "报告期"}]},
    )

    assert schema["properties"]["report_period"] == {"type": "string", "title": "报告期"}


def test_skill_studio_catalog_only_lists_real_direct_skill_bundles(tmp_path):
    skills = tmp_path / "skills"
    write_skill(skills, "public_skill", purpose="公开分析", auth="public")
    write_skill(skills, "internal_skill", purpose="内部编排", auth="internal")
    (skills / "plugin_container").mkdir()
    (skills / "plugin_container" / "catalog.json").write_text("{}", encoding="utf-8")
    invalid = skills / "invalid_skill"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("# invalid\n", encoding="utf-8")
    (invalid / "skill.json").write_text("[]", encoding="utf-8")
    (invalid / "schema.json").write_text("{}", encoding="utf-8")
    missing_schema = skills / "missing_schema"
    missing_schema.mkdir()
    (missing_schema / "SKILL.md").write_text("# missing schema\n", encoding="utf-8")
    (missing_schema / "skill.json").write_text("{}", encoding="utf-8")

    items = SkillStudioService(skills_root=str(skills)).list_skills()

    assert [item["skill_name"] for item in items] == ["internal_skill", "public_skill"]
    assert next(item for item in items if item["skill_name"] == "internal_skill")["auth"] == "internal"


def test_unified_invocable_catalog_is_owner_scoped_and_minimal(tmp_path):
    definitions = write_tool(
        tmp_path,
        "demo_tool",
        {
            "type": "object",
            "required": ["stock_code"],
            "properties": {
                "stock_code": {
                    "type": "string",
                    "title": "股票",
                    "description": "股票代码",
                }
            },
        },
    )
    definition_path = definitions / "demo_tool.tool.json"
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    definition["identity"] = {"display_name": "趋势工具", "description": "判断股票趋势。"}
    definition["status"] = "active"
    definition["availability"] = {"lifecycle": "active", "visibility": "visible"}
    definition_path.write_text(json.dumps(definition), encoding="utf-8")

    retired_path = definitions / "retired_tool.tool.json"
    retired_path.write_text(
        json.dumps(
            {
                "identity": {"display_name": "旧工具", "description": "已经停用。"},
                "status": "retired",
                "availability": {"lifecycle": "retired", "visibility": "visible"},
                "schemas": {"input": {"type": "object", "properties": {}}},
            }
        ),
        encoding="utf-8",
    )
    skills = tmp_path / "skills"
    write_skill(skills, "public_skill", purpose="完成公开深度分析。", auth="public")
    write_skill(skills, "internal_skill", purpose="仅用于内部编排。", auth="internal")
    write_skill(skills, "retired_skill", purpose="旧 Skill。", lifecycle="retired")

    class Store:
        seen_owner_ids = None

        def exists(self, name):
            return name == "ct_owned"

        def list_tools(self, *, include_inactive, owner_ids):
            self.seen_owner_ids = owner_ids
            return [{
                "tool_name": "ct_owned",
                "display_name": "我的评分工具",
                "description": "对单只股票进行评分。",
                "status": "active",
            }]

        def load_for_runtime(self, name, *, owner_ids, allow_inactive):
            assert name == "ct_owned"
            assert owner_ids == ["owner-a"]
            assert allow_inactive is False
            return {
                "manifest": {
                    "tool_name": name,
                    "display_name": "我的评分工具",
                    "description": "对单只股票进行评分。",
                    "owner_id": "owner-a",
                },
                "input_schema": {
                    "type": "object",
                    "required": ["stock_code"],
                    "properties": {
                        "stock_code": {
                            "type": "string",
                            "title": "股票",
                            "description": "股票代码",
                        }
                    },
                },
                "finance_tool_profile": {
                    "protocol": "finance_tool_profile.v1",
                    "family": "analytics",
                    "execution_shape": "entity_local",
                    "output_semantic": "metric",
                    "summary": "计算单股评分。",
                },
            }

    store = Store()
    service = AssetInvocationService(
        custom_tool_store=store,
        tool_definitions_dir=str(definitions),
        skills_root=str(skills),
        llm_chat=lambda *_args, **_kwargs: ({}, {}),
    )

    items = service.list_invocable_assets(owner_ids=["owner-a"])
    refs = [item["ref"] for item in items]

    assert refs == ["tool:ct_owned", "tool:demo_tool", "skill:public_skill"]
    assert "tool:retired_tool" not in refs
    assert "skill:internal_skill" not in refs
    assert "skill:retired_skill" not in refs
    assert store.seen_owner_ids == ["owner-a"]
    owned = next(item for item in items if item["ref"] == "tool:ct_owned")
    assert owned == {
        "ref": "tool:ct_owned",
        "kind": "tool",
        "name": "ct_owned",
        "display_name": "我的评分工具",
        "summary": "对单只股票进行评分。",
        "invocation": "$ct_owned",
        "input_fields": [{
            "name": "stock_code",
            "label": "股票",
            "required": True,
            "type": "string",
            "description": "股票代码",
            "default": None,
        }],
        "aliases": [],
        "tags": [],
        "custom_tool": True,
        "editable": True,
        "version": "v1",
        "revision": 0,
        "finance_tool_profile": {
            "protocol": "finance_tool_profile.v1",
            "family": "analytics",
            "execution_shape": "entity_local",
            "output_semantic": "metric",
            "summary": "计算单股评分。",
        },
    }


def test_design_only_action_asset_is_not_invocable_or_suggested(tmp_path):
    profile = {
        "protocol": "finance_tool_profile.v1",
        "family": "action",
        "execution_shape": "portfolio_stateful",
        "output_semantic": "action_receipt",
        "execution_policy": "planned_non_executable",
    }

    class ActionStore:
        def exists(self, name):
            return name == "ct_order_plan"

        def list_tools(self, *, include_inactive, owner_ids):
            return [{"tool_name": "ct_order_plan", "status": "active"}]

        def load_for_runtime(self, name, *, owner_ids, allow_inactive):
            return {
                "manifest": {
                    "tool_name": name,
                    "display_name": "订单方案",
                    "description": "仅设计订单确认流程。",
                    "owner_id": "owner-a",
                },
                "input_schema": {"type": "object", "properties": {}},
                "finance_tool_profile": profile,
            }

    service = AssetInvocationService(
        custom_tool_store=ActionStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=lambda *_args, **_kwargs: ({}, {}),
    )

    assert service.list_invocable_assets(owner_ids=["owner-a"]) == []
    result = service.plan(text="$ct_order_plan", owner_ids=["owner-a"])
    assert result["status"] == "needs_input"
    assert result["calls"] == []
    with pytest.raises(AssetInvocationError, match="仅有设计方案"):
        service.load_contract(
            kind="tool",
            name="ct_order_plan",
            owner_ids=["owner-a"],
        )


def test_explicit_asset_resolver_supports_namespace_display_and_inline_dollar(tmp_path):
    definitions = write_tool(
        tmp_path,
        "demo_tool",
        {"type": "object", "properties": {}},
    )
    definition_path = definitions / "demo_tool.tool.json"
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    definition["identity"] = {"display_name": "趋势分析", "description": "分析趋势。"}
    definition_path.write_text(json.dumps(definition), encoding="utf-8")
    skills = tmp_path / "skills"
    write_skill(skills, "demo_skill", display_name="深度分析", purpose="完成深度分析。")

    def llm(_messages, **_kwargs):
        return {
            "arguments": {"question": "分析贵州茅台"},
            "execution": {"mode": "single", "items": []},
            "missing_required": [],
        }, {}

    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(definitions),
        skills_root=str(skills),
        llm_chat=llm,
    )

    assert service.plan(text="$tool:demo_tool")["target"] == {
        "kind": "tool",
        "name": "demo_tool",
    }
    assert service.plan(text="$趋势分析")["target"] == {
        "kind": "tool",
        "name": "demo_tool",
    }
    assert service.plan(text="$skill:demo_skill 分析贵州茅台")["target"] == {
        "kind": "skill",
        "name": "demo_skill",
    }
    inline = service.plan(text="请用 $demo_tool 跑一下贵州茅台")
    assert inline["status"] == "ready"
    assert inline["user_request"] == "跑一下贵州茅台"


def test_fuzzy_asset_reference_only_returns_candidates_and_never_executes(tmp_path):
    definitions = write_tool(
        tmp_path,
        "demo_tool",
        {"type": "object", "properties": {}},
    )

    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(definitions),
        skills_root=str(tmp_path / "skills"),
        llm_chat=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fuzzy resolution must not call the LLM")
        ),
    )

    result = service.plan(text="$demo_tol")

    assert result["status"] == "needs_input"
    assert result["asset_resolution"]["status"] == "needs_selection"
    assert result["candidates"][0]["ref"] == "tool:demo_tool"
    assert result["calls"] == []


def test_dollar_amount_is_not_mistaken_for_an_asset_reference(tmp_path):
    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(tmp_path / "definitions"),
        skills_root=str(tmp_path / "skills"),
        llm_chat=lambda *_args, **_kwargs: ({}, {}),
    )

    assert service.has_explicit_invocation(text="当前价格是 $100") is False
    assert service.plan(text="当前价格是 $100") == {}


def test_ambiguous_asset_name_requires_kind_instead_of_preferring_tool(tmp_path):
    definitions = write_tool(
        tmp_path,
        "same_name",
        {"type": "object", "properties": {}},
    )
    skills = tmp_path / "skills"
    write_skill(skills, "same_name", purpose="同名 Skill。")
    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(definitions),
        skills_root=str(skills),
        llm_chat=lambda *_args, **_kwargs: ({}, {}),
    )

    ambiguous = service.plan(text="$same_name")

    assert ambiguous["status"] == "needs_input"
    assert ambiguous["asset_resolution"]["status"] == "ambiguous"
    assert {item["ref"] for item in ambiguous["candidates"]} == {
        "tool:same_name",
        "skill:same_name",
    }
    assert service.plan(text="$tool:same_name")["target"] == {
        "kind": "tool",
        "name": "same_name",
    }


def test_selected_asset_ref_is_compatible_but_conflicts_are_rejected(tmp_path):
    definitions = write_tool(
        tmp_path,
        "demo_tool",
        {"type": "object", "properties": {}},
    )
    skills = tmp_path / "skills"
    write_skill(skills, "demo_skill", purpose="测试 Skill。")
    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(definitions),
        skills_root=str(skills),
        llm_chat=lambda *_args, **_kwargs: ({}, {}),
    )

    assert service.plan(
        text="",
        selected_asset={"ref": "tool:demo_tool"},
    )["target"] == {"kind": "tool", "name": "demo_tool"}
    assert service.plan(
        text="",
        selected_asset={"kind": "tool", "name": "demo_tool"},
    )["target"] == {"kind": "tool", "name": "demo_tool"}

    with pytest.raises(AssetInvocationError, match="不一致"):
        service.plan(
            text="$skill:demo_skill",
            selected_asset={"ref": "tool:demo_tool"},
        )
    with pytest.raises(AssetInvocationError, match="不一致"):
        service.plan(
            text="$demo_tol",
            selected_asset={"ref": "tool:demo_tool"},
        )
    with pytest.raises(AssetInvocationError, match="不一致"):
        service.plan(
            text="",
            selected_asset={
                "ref": "tool:demo_tool",
                "kind": "skill",
                "name": "demo_tool",
            },
        )


def test_execution_contract_rejects_internal_skill_and_retired_tool(tmp_path):
    definitions = write_tool(
        tmp_path,
        "retired_tool",
        {"type": "object", "properties": {}},
    )
    definition_path = definitions / "retired_tool.tool.json"
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    definition["status"] = "retired"
    definition["availability"] = {
        "lifecycle": "retired",
        "visibility": "visible",
    }
    definition_path.write_text(json.dumps(definition), encoding="utf-8")
    skills = tmp_path / "skills"
    write_skill(skills, "internal_skill", purpose="内部 Skill。", auth="internal")
    service = AssetInvocationService(
        custom_tool_store=FakeStore(),
        tool_definitions_dir=str(definitions),
        skills_root=str(skills),
        llm_chat=lambda *_args, **_kwargs: ({}, {}),
    )

    with pytest.raises(AssetInvocationError, match="不可调用"):
        service.load_contract(kind="tool", name="retired_tool")
    with pytest.raises(AssetInvocationError, match="不可调用"):
        service.load_contract(kind="skill", name="internal_skill")
