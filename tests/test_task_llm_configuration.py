from src.utils.ai_service import _resolved_llm_key_source


def test_task_compiler_accepts_existing_legacy_llm_key(monkeypatch):
    for key in ("DASHSCOPE_API_KEY", "LLM_API_KEY", "LLM_KEY"):
        monkeypatch.delenv(key, raising=False)
    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert _resolved_llm_key_source(endpoint) == ""
    monkeypatch.setenv("LLM_KEY", "test-placeholder")
    assert _resolved_llm_key_source(endpoint) == "LLM_KEY"
    monkeypatch.setenv("LLM_API_KEY", "test-placeholder")
    assert _resolved_llm_key_source(endpoint) == "LLM_API_KEY"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder")
    assert _resolved_llm_key_source(endpoint) == "DASHSCOPE_API_KEY"
