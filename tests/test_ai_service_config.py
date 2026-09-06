import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.utils import ai_service


def test_ai_service_loads_repo_env_before_client_initialization() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    code = (
        "import os, dotenv\n"
        "def load_env(path, override=False):\n"
        f"    assert str(path) == {str(repo_root / '.env')!r}\n"
        "    assert override is False\n"
        "    os.environ.update(DASHSCOPE_API_KEY='test-key', LLM_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1')\n"
        "dotenv.load_dotenv = load_env\n"
        "from src.utils.ai_service import llm_config_summary; "
        "s=llm_config_summary(); "
        "assert s['key_present'] is True; "
        "assert s['key_source'] == 'DASHSCOPE_API_KEY'; "
        "assert 'dashscope.aliyuncs.com' in s['endpoint']"
    )
    env = os.environ.copy()
    for name in (
        "LLM_API_KEY",
        "LLM_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "LLM_ENDPOINT",
        "LLM_BASE_URL",
    ):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("endpoint", [
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://ws-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
])
def test_dashscope_route_never_selects_personal_deepseek_key(monkeypatch, endpoint) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "maas-key")
    monkeypatch.setenv("LLM_KEY", "personal-key")

    source = ai_service._resolved_llm_key_source(endpoint)

    assert source == "DASHSCOPE_API_KEY"


@pytest.mark.parametrize("endpoint", ["https://api.deepseek.com/v1", "https://dashscope.evilaliyuncs.com/v1"])
def test_non_aliyun_endpoints_are_not_classified_as_dashscope(endpoint):
    assert not ai_service.is_dashscope_endpoint(endpoint)


def test_legacy_explicit_llm_endpoint_remains_compatible(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_ENDPOINT", "https://example.test/v1")
    assert ai_service._resolved_llm_base_url() == "https://example.test/v1"


def test_flash_structured_request_uses_json_response_format(monkeypatch) -> None:
    captured = {}

    def fake_completion(messages, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
            usage=SimpleNamespace(),
        )

    monkeypatch.setattr(ai_service, "_create_llm_completion", fake_completion)

    content, _usage = ai_service.chat_qwen_flash_structured([{"role": "user", "content": "test"}])

    assert content == '{"ok":true}'
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["enable_think"] is False
