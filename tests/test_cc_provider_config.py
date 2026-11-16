import pytest

from src.services.claude_provider_config import ClaudeProviderConfig


def test_dashscope_config_uses_trusted_endpoint_and_injected_key(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fake-key')
    monkeypatch.delenv('DASHSCOPE_ANTHROPIC_BASE_URL', raising=False)
    env = ClaudeProviderConfig(provider='dashscope', model='flash').provider_env()
    assert env['ANTHROPIC_BASE_URL'] == 'https://dashscope.aliyuncs.com/apps/anthropic'
    assert env['ANTHROPIC_AUTH_TOKEN'] == 'fake-key'
    assert env['ANTHROPIC_MODEL'] == 'flash'


def test_dashscope_rejects_untrusted_endpoint(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_ANTHROPIC_BASE_URL', 'https://invalid.example/apps/anthropic')
    with pytest.raises(ValueError, match='official Alibaba'):
        ClaudeProviderConfig(provider='dashscope', model='flash')
