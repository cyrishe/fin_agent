from src.services.llm_stream_block_service import LlmStreamBlockBuilder
from src.web import flask_app as web


def _candidate_result(*, execution_ok: bool) -> dict:
    return {
        "coding_status": "implemented",
        "tool": {
            "manifest": {
                "tool_name": "ct_demo",
                "display_name": "演示工具",
                "description": "候选修改",
                "status": "draft",
                "current_revision": 4,
                "active_revision": 3,
            }
        },
        "test_result": {
            "ok": execution_ok,
            "execution_ok": execution_ok,
            "summary": "构造样本验证通过" if execution_ok else "构造样本验证失败",
            "cases": [],
        },
        "edit_summary": {
            "tool_name": "ct_demo",
            "display_name": "演示工具",
            "route": "local_patch",
            "impact_summary": "只调整一个阈值。",
            "base_revision": 3,
            "candidate_revision": 4,
            "affected_assets": ["design", "implementation"],
            "changes": [],
            "verification": {
                "status": "passed" if execution_ok else "failed",
                "summary": "构造样本验证通过" if execution_ok else "构造样本验证失败",
                "cases": [],
            },
        },
    }


def test_verified_edit_candidate_offers_candidate_activation() -> None:
    blocks = web._custom_tool_result_blocks(
        _candidate_result(execution_ok=True),
        LlmStreamBlockBuilder(run_id="verified_edit_candidate"),
    )

    interaction = next(block for block in blocks if block["block_type"] == "interaction")
    assert interaction["title"] == "确认候选修改"
    assert "当前仍使用版本 3" in interaction["content"]
    assert interaction["data"]["actions"][0]["label"] == "启用候选版本"
    assert interaction["data"]["actions"][0]["expected_revision"] == 4


def test_failed_edit_candidate_never_offers_backend_activation() -> None:
    blocks = web._custom_tool_result_blocks(
        _candidate_result(execution_ok=False),
        LlmStreamBlockBuilder(run_id="failed_edit_candidate"),
    )

    assert all(block["block_type"] != "interaction" for block in blocks)
    assessment = next(block for block in blocks if block["block_type"] == "assessment")
    assert assessment["data"]["overall"] == "fail"
