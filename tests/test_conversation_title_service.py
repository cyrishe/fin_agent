from src.services.conversation_title_service import ConversationTitleService
from src.services import runtime_conversation_service as runtime_module


def test_conversation_title_uses_flash_llm_and_normalizes_result() -> None:
    calls = []

    def fake_llm(messages, enable_think=False):
        calls.append({"messages": messages, "enable_think": enable_think})
        return {"title": "《A股放量突破工具设计》。"}, {"total_tokens": 18}

    result = ConversationTitleService(llm_call=fake_llm).generate(
        user_text="/custom_tool create 我想设计一个识别A股放量突破的工具",
    )

    assert result["title"] == "A股放量突破工具设计"
    assert result["source"] == "llm"
    assert result["model_name"] == "deepseek-v4-flash"
    assert calls[0]["enable_think"] is False
    assert "/custom_tool create" in calls[0]["messages"][1]["content"]


def test_conversation_title_falls_back_without_blocking_when_llm_fails() -> None:
    def failed_llm(_messages, enable_think=False):
        raise TimeoutError("maas timeout")

    result = ConversationTitleService(llm_call=failed_llm).generate(
        user_text="/custom_tool create 帮我做一个市场情绪温度计，需要适合盘后复盘",
    )

    assert result == {
        "title": "帮我做一个市场情绪温度计",
        "source": "fallback",
        "model_name": "deepseek-v4-flash",
    }


class _Cursor:
    def __init__(self) -> None:
        self.executed = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.executed.append((" ".join(str(sql).split()), params))


class _Connection:
    def __init__(self, cursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


class _Db:
    latest = None

    def __init__(self) -> None:
        self.cursor = _Cursor()
        self.conn = _Connection(self.cursor)
        self.closed = False
        _Db.latest = self

    def close_db(self):
        self.closed = True


def test_runtime_title_update_only_replaces_expected_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "SystemDbUtils", _Db)
    service = runtime_module.RuntimeConversationService()

    updated = service.update_thread_title(
        thread_id=42,
        title="市场情绪温度计",
        expected_title="Investment Workbench",
    )

    db = _Db.latest
    assert updated is True
    assert db.conn.committed is True
    assert db.closed is True
    assert "WHERE thread_id = %s AND title = %s" in db.cursor.executed[0][0]
    assert db.cursor.executed[0][1] == ("市场情绪温度计", 42, "Investment Workbench")
