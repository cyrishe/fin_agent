import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from src.utils import ai_service


def test_ai_service_loads_repo_env_before_client_initialization() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    code = (
        "from src.utils.ai_service import llm_config_summary; "
        "s=llm_config_summary(); "
        "assert s['key_present'] is True; "
        "assert s['key_source'] in {'LLM_API_KEY', 'LLM_KEY', 'DASHSCOPE_API_KEY'}; "
        "assert 'dashscope.aliyuncs.com/compatible-mode/v1' in s['endpoint']"
    )
    env = os.environ.copy()
    for name in ("LLM_API_KEY", "LLM_KEY", "DASHSCOPE_API_KEY", "LLM_ENDPOINT", "LLM_BASE_URL"):
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
