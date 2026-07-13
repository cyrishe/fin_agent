from pathlib import Path

import pytest

from app.config import DEEPSEEK_ANTHROPIC_BASE_URL, Settings


def test_fake_settings_are_ready_without_sdk_or_credentials(tmp_path: Path) -> None:
    settings = Settings(root_dir=tmp_path, backend="fake")

    result = settings.readiness()

    assert result["ready"] is True
    assert result["credentials_configured"] is False


def test_gateway_rejects_non_loopback_plain_http(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        backend="fake",
        provider="gateway",
        base_url="http://maas.example.com",
    )

    result = settings.readiness()

    assert result["ready"] is False
    assert any("HTTPS" in issue for issue in result["issues"])


def test_provider_env_contains_secrets_but_readiness_does_not(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        backend="fake",
        anthropic_api_key="top-secret-key",
    )

    assert settings.provider_env()["ANTHROPIC_API_KEY"] == "top-secret-key"
    assert "top-secret-key" not in str(settings.readiness())
    assert "top-secret-key" not in repr(settings)


def test_deepseek_profile_has_safe_defaults_and_dedicated_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_PROVIDER", "deepseek")
    monkeypatch.setenv("CLAUDE_DEMO_BACKEND", "claude")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-secret")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-be-reused")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("CLAUDE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_EFFORT", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EFFORT_LEVEL", raising=False)

    settings = Settings.from_env()
    env = settings.provider_env()

    assert settings.model == "deepseek-v4-flash"
    assert settings.base_url == DEEPSEEK_ANTHROPIC_BASE_URL
    assert settings.effort == "max"
    assert settings.readiness()["ready"] is True
    assert env["ANTHROPIC_AUTH_TOKEN"] == "deepseek-test-secret"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "deepseek-v4-flash"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "deepseek-v4-flash"
    assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"


def test_deepseek_key_cannot_be_sent_to_an_arbitrary_host(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        backend="claude",
        provider="deepseek",
        model="deepseek-v4-flash",
        effort="max",
        base_url="https://attacker.example",
        auth_token="deepseek-test-secret",
    )

    assert settings.readiness()["ready"] is False
    with pytest.raises(ValueError, match="official Anthropic endpoint"):
        settings.provider_env()


def test_credential_placeholder_is_not_ready(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        backend="claude",
        anthropic_api_key="replace-me",
    )

    result = settings.readiness()

    assert result["ready"] is False
    assert any("placeholder" in issue for issue in result["issues"])
