import json
from types import SimpleNamespace

import pytest

from src.services.follow_up_question_service import FollowUpQuestionService


def response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                           usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})


def test_questions_use_current_question_and_answer_without_modifying_them():
    seen = []
    service = FollowUpQuestionService(complete=lambda messages: seen.append(messages) or response('{"questions": ["现金流为何落后？", "同业如何？", "现金流为何落后？"]}'))
    result = service.generate(question="利润增长如何？", answer="利润增长10%，现金流证据不足。")
    assert result["questions"] == ["现金流为何落后？", "同业如何？"]
    assert json.loads(seen[0][1]["content"])["answer"] == "利润增长10%，现金流证据不足。"
    assert result["llm_usage"]["call_count"] == 1


@pytest.mark.parametrize("content", ["not JSON", '{"questions": "bad"}', '[]', '{"questions": []}'])
def test_unusable_optional_response_retains_usage_but_not_fake_questions(content):
    result = FollowUpQuestionService(complete=lambda messages: response(content)).generate(question="q", answer="a")
    assert result["questions"] == []
    assert result["llm_usage"]["total_tokens"] == 30


def test_optional_timeout_does_not_fail_answer():
    def fail(messages):
        raise TimeoutError("provider timeout")
    assert FollowUpQuestionService(complete=fail).generate(question="q", answer="a") == {"questions": [], "llm_usage": {}}


def test_chat_attaches_questions_and_counts_added_call_once(monkeypatch):
    from src.web import flask_app as web
    monkeypatch.setattr(FollowUpQuestionService, "generate", lambda self, **kwargs: {"questions": ["还有哪些风险？"], "llm_usage": {"total_tokens": 30, "call_count": 1}})
    result = {"financial_qa": {"runtime": "dsh"}, "message": "原答案", "surface_blocks": [], "llm_usage": {"total_tokens": 100, "call_count": 3}}
    web._attach_answer_summary(result, raw_user_text="q", assistant_message="原答案")
    assert result["follow_up_questions"] == ["还有哪些风险？"]
    assert result["llm_usage"]["total_tokens"] == 130
    assert result["llm_usage"]["call_count"] == 4
    assert result["message"] == "原答案" and result["surface_blocks"] == []


@pytest.mark.parametrize("extra", [{"data_only": True}, {"financial_qa": {"error": "timeout"}}, {"financial_qa": {}}])
def test_non_answer_paths_skip_recommendation(monkeypatch, extra):
    from src.web import flask_app as web
    monkeypatch.setattr(FollowUpQuestionService, "generate", lambda *a, **k: pytest.fail("must skip"))
    result = {"financial_qa": {"runtime": "dsh"}, **extra}
    web._attach_follow_up_questions(result, question="q", answer="a")
    assert "follow_up_questions" not in result
