from app.normalizer import ClaudeEventNormalizer


def _message(class_name: str, **attrs):
    cls = type(class_name, (), {})
    value = cls()
    for key, item in attrs.items():
        setattr(value, key, item)
    return value


def test_normalizes_text_delta() -> None:
    message = _message(
        "StreamEvent",
        event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}},
    )

    events = ClaudeEventNormalizer().normalize(message)

    assert [(event.type, event.data) for event in events] == [("assistant.delta", {"text": "hello"})]


def test_normalizes_tool_lifecycle() -> None:
    normalizer = ClaudeEventNormalizer()
    start = _message(
        "StreamEvent",
        event={
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "tool_use", "id": "tool_1", "name": "WebSearch"},
        },
    )
    delta = _message(
        "StreamEvent",
        event={
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"query":"a'},
        },
    )

    started = normalizer.normalize(start)[0]
    input_delta = normalizer.normalize(delta)[0]

    assert started.type == "tool.started"
    assert started.data["tool_name"] == "WebSearch"
    assert input_delta.type == "tool.input.delta"
    assert input_delta.data["tool_use_id"] == "tool_1"


def test_result_is_authoritative_final() -> None:
    message = _message(
        "ResultMessage",
        is_error=False,
        subtype="success",
        result="done",
        structured_output=None,
        session_id="session_1",
        num_turns=2,
        duration_ms=100,
        total_cost_usd=0.01,
        usage={"input_tokens": 10, "output_tokens": 5},
        stop_reason="end_turn",
    )

    event = ClaudeEventNormalizer().normalize(message)[0]

    assert event.type == "backend.result"
    assert event.data["result"] == "done"
    assert event.data["session_id"] == "session_1"
