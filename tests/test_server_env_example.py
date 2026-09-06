from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def test_finance_api_server_env_example_has_canonical_model_route() -> None:
    values = dotenv_values(ROOT / "deploy" / "finance-api" / ".env.example")

    required = {
        "FINANCE_API_KEYS_JSON",
        "SYSTEM_DB_URL",
        "KINGDOMAI_DB_URL",
        "STOCK_AGENT_CODEX_AUTH_MODE",
        "CODEX_CRS_API_KEY",
        "CODEX_CRS_BASE_URL",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_ANTHROPIC_BASE_URL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
    }
    assert required <= set(values)
    assert values["STOCK_AGENT_CODEX_AUTH_MODE"] == "crs_api_key"
    assert values["CODEX_CRS_BASE_URL"] == "https://proxy.kingdomai.com/openai"
    assert "FINANCE_DSH_API_KEY" not in values
    assert "FINANCE_DSH_BASE_URL" not in values
    assert "FINANCE_DSH_MODEL" not in values
    assert values["DASHSCOPE_BASE_URL"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert values["DASHSCOPE_ANTHROPIC_BASE_URL"] == (
        "https://dashscope.aliyuncs.com/apps/anthropic"
    )
    assert all(
        "CHANGE_ME" in str(values[name])
        for name in (
            "FINANCE_API_KEYS_JSON",
            "SYSTEM_DB_URL",
            "KINGDOMAI_DB_URL",
            "CODEX_CRS_API_KEY",
            "DASHSCOPE_API_KEY",
            "LLM_API_KEY",
        )
    )


def test_root_env_example_documents_server_runtime_overrides() -> None:
    values = dotenv_values(ROOT / ".env.example")

    assert "CODEX_CRS_API_KEY" in values
    assert "FINANCE_DSH_API_KEY" not in values
    assert "FINANCE_DSH_BASE_URL" not in values
    assert "DASHSCOPE_ANTHROPIC_BASE_URL" in values
