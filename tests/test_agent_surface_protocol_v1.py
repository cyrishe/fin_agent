import json
from pathlib import Path

from jsonschema import Draft202012Validator


PROTOCOL_SCHEMA = Path("src/protocols/agent_surface_protocol_v1.schema.json")
DESIGN_EXAMPLE = Path("docs/protocol_examples/agent_surface_v1_financial_tool_design.json")


def _validator() -> Draft202012Validator:
    schema = json.loads(PROTOCOL_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_financial_tool_design_protocol_example_is_valid() -> None:
    validator = _validator()
    example = json.loads(DESIGN_EXAMPLE.read_text(encoding="utf-8"))

    for message in example["messages"]:
        validator.validate(message)


def test_clarification_can_be_answered_in_the_unified_conversation() -> None:
    event = {
        "object_type": "surface.event",
        "protocol_version": "agent_surface.v1",
        "event_id": "evt_clarification",
        "event_type": "block.create",
        "context_id": "ctx_1",
        "run_id": "run_1",
        "seq": 1,
        "timestamp": "2026-07-12T10:00:00+08:00",
        "commit_state": "committed",
        "target": {
            "surface_id": "surface_1",
            "section_id": "interaction",
        },
        "payload": {
            "block_id": "design_clarification",
            "kind": "interaction",
            "status": "waiting_user",
            "revision": 0,
            "semantic": "finance.tool_design_clarification",
            "payload": {
                "interaction_id": "custom_tool.requirement_clarification",
                "intent": "clarify",
                "prompt": "请补充会改变工具设计的关键条件。",
                "submission_mode": "conversation",
                "fields": [
                    {
                        "field_id": "Q1",
                        "label": "输出候选列表还是排名？",
                        "description": "这会影响输出接口和验收方式。",
                        "value_type": "single_choice",
                        "required": True,
                        "options": [
                            {
                                "value": "ranking",
                                "label": "排序结果",
                                "description": "返回候选标的和分数。",
                                "recommended": True,
                            }
                        ],
                        "allow_custom": True,
                    }
                ],
            },
        },
    }

    _validator().validate(event)


def test_review_action_must_bind_to_an_artifact_revision() -> None:
    interaction = {
        "block_id": "design_review",
        "kind": "interaction",
        "status": "waiting_user",
        "revision": 0,
        "payload": {
            "interaction_id": "custom_tool.design_review",
            "intent": "confirm",
            "prompt": "是否按当前规格继续？",
            "submission_mode": "action",
            "actions": [
                {
                    "action_id": "custom_tool.confirm_design",
                    "label": "确认并继续",
                    "intent": "accept",
                }
            ],
            "resume_token": "opaque-token",
        },
    }
    event = {
        "object_type": "surface.event",
        "protocol_version": "agent_surface.v1",
        "event_id": "evt_review",
        "event_type": "block.create",
        "context_id": "ctx_1",
        "run_id": "run_1",
        "seq": 1,
        "timestamp": "2026-07-12T10:00:00+08:00",
        "commit_state": "committed",
        "target": {"surface_id": "surface_1", "section_id": "interaction"},
        "payload": interaction,
    }

    errors = list(_validator().iter_errors(event))

    def messages(error):
        yield error.message
        for child in error.context:
            yield from messages(child)

    assert errors
    assert any(
        "subject_ref" in message or "subject_revision" in message
        for error in errors
        for message in messages(error)
    )
