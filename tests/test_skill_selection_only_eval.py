import json

import pytest

from scripts.eval_skill_selection_only import replay_request, score


def response(ids, *, name="mcp__finance__read_finance_skill", finish="tool_calls"):
    return {"choices": [{"finish_reason": finish, "message": {"tool_calls": [
        {"function": {"name": name, "arguments": json.dumps({"skill_ids": ids})}}
    ]}}]}


@pytest.fixture
def case():
    return {"primary": "equity-report-analysis", "acceptable": ["equity-report-analysis", "stock-research"], "expected_generic": False}


def test_specialist_and_parent_are_compatible_but_not_identical(case):
    specific = score(case, response(["equity-report-analysis", "stock-research"]))
    assert specific["primary_hit"] and specific["acceptable_selection"]
    parent = score(case, response(["stock-research"]))
    assert not parent["primary_hit"] and parent["acceptable_selection"]


def test_unexpected_extra_skill_is_kept_for_review(case):
    result = score(case, response(["equity-report-analysis", "unregistered-skill"]))
    assert result["primary_hit"]
    assert not result["acceptable_selection"]
    assert "unregistered-skill" in result["selected"]


def test_empty_selection_and_data_tool_are_not_false_skill_hits(case):
    assert not score(case, response([]))["primary_hit"]
    generic = {"primary": None, "acceptable": [], "expected_generic": True}
    assert score(generic, response([]))["primary_hit"]
    assert not score(case, response([], name="mcp__finance__finance_query"))["primary_hit"]
    assert not score(case, response(["equity-report-analysis"], finish="length"))["primary_hit"]


def test_malformed_arguments_are_scored_without_execution(case):
    value = response([])
    value["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "not json"
    assert score(case, value)["invalid_calls"] == ["invalid_skill_arguments"]


def test_native_catalog_alternative_is_generic_but_never_a_skill_hit(case):
    value = response([], name="mcp__finance__read_finance_catalog")
    call = value["choices"][0]["message"]["tool_calls"][0]["function"]
    call["arguments"] = json.dumps({"subject": "stock", "dataview": "quote", "operation": "query"})
    generic = {"primary": None, "acceptable": [], "expected_generic": True}
    assert not score(generic, value)["primary_hit"]  # Original Skill-only scorer unchanged.
    assert score(generic, value, allow_catalog=True)["primary_hit"]
    assert not score(case, value, allow_catalog=True)["primary_hit"]
    call["arguments"] = "[]"
    assert score(generic, value, allow_catalog=True)["invalid_calls"] == ["invalid_catalog_arguments"]


def test_selection_does_not_hide_invalid_native_catalog_arguments():
    generic = {"primary": None, "acceptable": [], "expected_generic": True}
    value = response([], name="mcp__finance__read_finance_catalog")
    call = value["choices"][0]["message"]["tool_calls"][0]["function"]
    call["arguments"] = '{"subject":"a company"}'
    schemas = {call["name"]:{"type":"object", "properties":{"subject":{"enum":["stock","index"]}}}}
    result = score(generic,value,allow_catalog=True,tool_schemas=schemas)
    assert not result["primary_hit"] and result["invalid_calls"]
    call["arguments"] = '{"subject":"stock"}'
    assert score(generic,value,allow_catalog=True,tool_schemas=schemas)["primary_hit"]


def test_replay_preserves_model_contract_and_only_returns_selection(monkeypatch):
    import requests
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-only-placeholder")
    original = {"model": "test-model", "messages": [{"role": "user", "content": "机构如何看茅台？"}],
                "tools": [{"type": "function", "function": {"name": "read_finance_skill"}}],
                "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 1536}
    calls = []
    completion = response(["equity-report-analysis"])

    class Reply:
        status_code = 200

        def json(self):
            return completion

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Reply()

    monkeypatch.setattr(requests, "post", post)
    sent, received = replay_request(original)
    assert len(calls) == 1
    assert sent["messages"] == original["messages"] and sent["tools"] == original["tools"]
    assert sent["stream"] is False and "stream_options" not in sent
    assert original["stream"] is True
    assert "tool_choice" not in sent
    assert received is completion  # Never forwarded to a Harness or tool handler.
