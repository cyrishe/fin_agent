import json

from src.services import runtime_conversation_service as runtime_module


class _HistoryCursor:
    def __init__(self) -> None:
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, _params):
        self.sql = " ".join(str(sql).split())

    def fetchall(self):
        return [(
            11,
            1,
            "查看结果",
            "{}",
            "已完成",
            json.dumps({
                "mode": "custom_tool_flow",
                "surface_blocks": [{"block_id": "result"}],
                "workspace": None,
            }, ensure_ascii=False),
            "completed",
            None,
            None,
            "claude-sonnet",
            json.dumps({
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            }),
        )]


class _HistoryDb:
    latest = None

    def __init__(self) -> None:
        self._cursor = _HistoryCursor()
        self.conn = self
        self.closed = False
        _HistoryDb.latest = self

    def __enter__(self):
        return self._cursor

    def cursor(self):
        return self._cursor

    def close_db(self):
        self.closed = True


def test_history_query_projects_only_renderable_output_fields(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "SystemDbUtils", _HistoryDb)

    turns = runtime_module.RuntimeConversationService().list_turns(
        thread_id=7,
        limit=100,
        include_output_payload=True,
        history_payload_only=True,
    )

    assert "JSON_OBJECT" in _HistoryDb.latest._cursor.sql
    assert "JSON_EXTRACT" in _HistoryDb.latest._cursor.sql
    assert turns[0]["output_payload"] == {
        "mode": "custom_tool_flow",
        "surface_blocks": [{"block_id": "result"}],
    }
    assert turns[0]["model_name"] == "claude-sonnet"
    assert turns[0]["token_usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }
    assert _HistoryDb.latest.closed is True


def test_history_compaction_preserves_nested_metric_and_table_cells() -> None:
    payload = {
        "render_payload": {
            "sections": [{
                "blocks": [
                    {
                        "type": "metric_strip",
                        "data": {
                            "items": [
                                {"label": "计划执行", "value": 2},
                                {"label": "成功返回", "value": 2},
                            ]
                        },
                    },
                    {
                        "type": "table",
                        "data": {
                            "columns": ["结果", "数量"],
                            "rows": [{"结果": "数据不足", "数量": 1}],
                        },
                    },
                ]
            }]
        }
    }

    compacted = runtime_module.RuntimeConversationService._compact_history_payload(payload)

    blocks = compacted["render_payload"]["sections"][0]["blocks"]
    assert blocks[0]["data"]["items"] == [
        {"label": "计划执行", "value": 2},
        {"label": "成功返回", "value": 2},
    ]
    assert blocks[1]["data"]["rows"] == [{"结果": "数据不足", "数量": 1}]


def test_thread_query_can_return_context_without_a_second_connection(monkeypatch) -> None:
    class ThreadCursor(_HistoryCursor):
        def fetchone(self):
            return (
                7,
                "测试会话",
                "user",
                "guest_a",
                "active",
                None,
                None,
                None,
                json.dumps({"assistant_context": {"custom_tool_state": {"tool_name": "ct_demo"}}}),
            )

    class ThreadDb(_HistoryDb):
        def __init__(self) -> None:
            super().__init__()
            self._cursor = ThreadCursor()

    monkeypatch.setattr(runtime_module, "SystemDbUtils", ThreadDb)

    thread = runtime_module.RuntimeConversationService().get_thread(thread_id=7, include_context=True)

    assert thread["thread_id"] == 7
    assert thread["_thread_context"] == {"custom_tool_state": {"tool_name": "ct_demo"}}


def test_ensure_existing_thread_requires_matching_owner(monkeypatch) -> None:
    class ThreadOwnerCursor(_HistoryCursor):
        def fetchone(self):
            return ("user", "guest_a")

    class ThreadOwnerDb(_HistoryDb):
        def __init__(self) -> None:
            super().__init__()
            self._cursor = ThreadOwnerCursor()

    monkeypatch.setattr(runtime_module, "SystemDbUtils", ThreadOwnerDb)

    thread_id = runtime_module.RuntimeConversationService().ensure_thread(
        thread_id=7,
        owner_type="user",
        owner_id="guest_a",
    )

    assert thread_id == 7
    assert ThreadOwnerDb.latest.closed is True


def test_ensure_existing_thread_rejects_foreign_owner(monkeypatch) -> None:
    class ThreadOwnerCursor(_HistoryCursor):
        def fetchone(self):
            return ("user", "guest_a")

    class ThreadOwnerDb(_HistoryDb):
        def __init__(self) -> None:
            super().__init__()
            self._cursor = ThreadOwnerCursor()

    monkeypatch.setattr(runtime_module, "SystemDbUtils", ThreadOwnerDb)

    try:
        runtime_module.RuntimeConversationService().ensure_thread(
            thread_id=7,
            owner_type="user",
            owner_id="guest_b",
        )
    except runtime_module.RuntimeConversationAccessError as exc:
        assert str(exc) == "无权访问该 thread"
    else:
        raise AssertionError("foreign thread owner must be rejected")

    assert ThreadOwnerDb.latest.closed is True
