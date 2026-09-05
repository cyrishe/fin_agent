import pytest

from src.services.top_level_shortcut_service import TopLevelShortcutError, TopLevelShortcutService


def test_structured_design_feedback_with_text_does_not_use_top_level_shortcut() -> None:
    plan = TopLevelShortcutService().resolve(
        text="增加信号日期",
        interaction_response={
            "interaction_id": "custom_tool.design_review",
            "action_id": "custom_tool.revise_design",
            "action": "edit",
            "expected_revision": 2,
            "feedback_text": "增加信号日期",
        },
        application_context={"default_agent": {"agent_name": "investment_analyst"}},
    )

    assert plan is None


def test_empty_text_confirmation_uses_top_level_shortcut() -> None:
    plan = TopLevelShortcutService().resolve(
        text="",
        interaction_response={
            "interaction_id": "custom_tool.design_review",
            "action_id": "custom_tool.confirm_design",
            "action": "accept",
            "expected_revision": 2,
        },
        application_context={"default_agent": {"agent_name": "investment_analyst"}},
    )

    assert plan["shortcut"]["handler"] == "custom_tool.action"


def test_design_failure_retry_uses_custom_tool_shortcut() -> None:
    plan = TopLevelShortcutService().resolve(
        text="",
        interaction_response={
            "interaction_id": "custom_tool.design_failure",
            "action_id": "custom_tool.retry_design",
            "action": "accept",
        },
        application_context={"default_agent": {"agent_name": "investment_analyst"}},
    )

    assert plan["entry"] == "custom_tool_flow"
    assert plan["turn_mode"] == "tool_development"
    assert plan["shortcut"]["handler"] == "custom_tool.action"


def test_confirmation_with_text_is_not_a_shortcut() -> None:
    plan = TopLevelShortcutService().resolve(
        text="先不要实现，把窗口改成 60 个交易日",
        interaction_response={
            "interaction_id": "custom_tool.design_review",
            "action_id": "custom_tool.confirm_design",
            "action": "accept",
            "expected_revision": 2,
        },
        application_context={"default_agent": {"agent_name": "investment_analyst"}},
    )

    assert plan is None


def test_custom_tool_create_command_uses_same_shortcut_framework() -> None:
    plan = TopLevelShortcutService().resolve(
        text="/custom_tool create 创建金叉工具",
        application_context={},
    )

    assert plan["shortcut"]["handler"] == "custom_tool.command"
    assert plan["shortcut"]["stage"] == "design"


@pytest.mark.parametrize(
    "text",
    [
        "帮我创建一个金叉判断工具",
        "请优化刚才的设计",
        "修改一下核心代码",
        "我想看看这个工具的流程图",
    ],
)
def test_natural_language_never_matches_top_level_shortcut(text: str) -> None:
    assert TopLevelShortcutService().resolve(text=text, application_context={}) is None


def test_unknown_interaction_is_rejected_at_top_level_boundary() -> None:
    with pytest.raises(TopLevelShortcutError, match="unknown custom tool interaction"):
        TopLevelShortcutService().resolve(
            text="",
            interaction_response={
                "interaction_id": "custom_tool.unknown",
                "action_id": "custom_tool.revise_design",
                "action": "edit",
            },
        )
