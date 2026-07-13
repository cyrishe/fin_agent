import json

import pytest

from app.deepseek_probe import DeepSeekProbeError, summarize_anthropic_sse


def test_summarize_anthropic_sse_accepts_streamed_tool_call() -> None:
    payloads = [
        json.dumps({"type": "message_start", "message": {"role": "assistant"}}),
        json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tool_1",
                    "name": "get_current_time",
                    "input": {},
                },
            }
        ),
        json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            }
        ),
        json.dumps({"type": "content_block_stop", "index": 0}),
        json.dumps({"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
        json.dumps({"type": "message_stop"}),
    ]

    result = summarize_anthropic_sse("deepseek-v4-flash", payloads)

    assert result.tool_name == "get_current_time"
    assert result.tool_input_keys == []
    assert result.stop_reason == "tool_use"
    assert result.message_stop_seen is True


def test_summarize_anthropic_sse_rejects_missing_tool_call() -> None:
    payloads = [
        json.dumps({"type": "message_start", "message": {"role": "assistant"}}),
        json.dumps({"type": "message_stop"}),
    ]

    with pytest.raises(DeepSeekProbeError, match="forced get_current_time"):
        summarize_anthropic_sse("deepseek-v4-flash", payloads)
