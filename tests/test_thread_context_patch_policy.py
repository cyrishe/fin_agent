from src.web.flask_app import _merge_thread_context_patches


def test_thread_context_patch_ignores_derived_conversation_states():
    result = _merge_thread_context_patches(
        {
            "conversation_state": {"state": "suspended"},
            "interaction_frame": {"interaction_mode": "resume_previous_task"},
            "continuity_axes": {"domain": "business"},
            "active_focus_type": "custom_tool",
            "active_focus_id": "demo",
            "custom_tool_state": {"status": "draft_needs_test", "tool_name": "demo"},
            "recent_result_subject": "贵州茅台",
        }
    )

    assert result == {
        "custom_tool_state": {"status": "draft_needs_test", "tool_name": "demo"},
        "recent_result_subject": "贵州茅台",
    }
