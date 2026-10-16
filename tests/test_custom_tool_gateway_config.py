import pytest

from src.scenarios.custom_tool.dsh_intent_router import CustomToolIntentDshRouter
from src.scenarios.custom_tool.dsh_service import CustomToolDeepSeekHarnessSessionService


@pytest.mark.parametrize('service_class', [CustomToolIntentDshRouter, CustomToolDeepSeekHarnessSessionService])
@pytest.mark.parametrize('gateway', ['shared', 'legacy', 'missing_key'])
def test_gateway_credentials_stay_with_the_selected_provider(monkeypatch, tmp_path, service_class, gateway):
    monkeypatch.delenv('FINANCE_DSH_CUSTOM_TOOL_BASE_URL', raising=False)
    monkeypatch.setenv('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'legacy-key')
    monkeypatch.setenv('LLM_API_KEY', 'shared-key' if gateway != 'missing_key' else '')
    if gateway == 'legacy':
        monkeypatch.delenv('LLM_BASE_URL', raising=False)
    else:
        monkeypatch.setenv('LLM_BASE_URL', 'https://gateway.example/v1')
    service = service_class(enabled=False, root_dir=tmp_path / 'runtime', log_path=tmp_path / 'events.jsonl')
    try:
        assert service.api_key == {'shared': 'shared-key', 'legacy': 'legacy-key', 'missing_key': ''}[gateway]
        assert service.base_url == ('https://dashscope.aliyuncs.com/compatible-mode/v1' if gateway == 'legacy' else 'https://gateway.example/v1')
    finally:
        service.close()
