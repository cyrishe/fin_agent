import pytest

pytest.importorskip("claude_agent_sdk")

from app.backend import ClaudeAgentBackend
from app.config import DEEPSEEK_ANTHROPIC_BASE_URL, ROOT_DIR, Settings
from app.contracts import BackendRequest


def test_real_sdk_accepts_generated_options() -> None:
    settings = Settings(
        root_dir=ROOT_DIR,
        backend="claude",
        anthropic_api_key="test-only-not-used",
        web_search_backend="builtin",
    )
    backend = ClaudeAgentBackend(settings)

    options = backend.build_options(
        BackendRequest(
            run_id="run_test",
            question="test",
            skill_names=["financial-research"],
            enable_web_search=True,
        )
    )

    assert options.skills == ["financial-research"]
    assert options.tools == ["Skill", "WebSearch"]
    assert options.permission_mode == "dontAsk"
    assert options.strict_mcp_config is True
    assert "Bash" in options.disallowed_tools
    assert "mcp__demo__get_current_time" in options.allowed_tools
    assert options.env["ANTHROPIC_API_KEY"] == "test-only-not-used"


def test_real_sdk_accepts_trusted_structured_output_schema() -> None:
    backend = ClaudeAgentBackend(
        Settings(root_dir=ROOT_DIR, backend="claude", anthropic_api_key="test-only-not-used")
    )

    options = backend.build_options(
        BackendRequest(
            run_id="run_structured",
            question="research",
            skill_names=["financial-research"],
            output_mode="research_json",
        )
    )

    assert options.output_format["type"] == "json_schema"
    assert options.output_format["schema"]["additionalProperties"] is False


def test_real_sdk_accepts_deepseek_v4_flash_profile() -> None:
    backend = ClaudeAgentBackend(
        Settings(
            root_dir=ROOT_DIR,
            backend="claude",
            provider="deepseek",
            model="deepseek-v4-flash",
            effort="max",
            base_url=DEEPSEEK_ANTHROPIC_BASE_URL,
            auth_token="deepseek-test-secret",
        )
    )

    options = backend.build_options(
        BackendRequest(
            run_id="run_deepseek",
            question="test",
            skill_names=["financial-research"],
            enable_web_search=False,
        )
    )

    assert options.model == "deepseek-v4-flash"
    assert options.effort == "max"
    assert options.env["ANTHROPIC_BASE_URL"] == DEEPSEEK_ANTHROPIC_BASE_URL
    assert options.env["ANTHROPIC_AUTH_TOKEN"] == "deepseek-test-secret"
    assert options.env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-v4-flash"
    assert options.env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
