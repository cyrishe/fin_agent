from __future__ import annotations

import pytest

from src.finance_api.auth import FinanceApiAuthError, FinanceApiKeyAuth


KEY = "test-finance-api-key-1234567890"


def test_finance_api_key_auth_supports_bearer_and_header() -> None:
    auth = FinanceApiKeyAuth({"client-a": KEY})

    assert auth.authenticate(f"Bearer {KEY}").principal_id == "client-a"
    assert auth.authenticate(x_api_key=KEY).principal_id == "client-a"
    assert auth.status() == {
        "configured": True,
        "key_count": 1,
        "schemes": ["bearer", "x-api-key"],
    }


def test_finance_api_key_auth_fails_closed() -> None:
    with pytest.raises(ValueError, match="not configured"):
        FinanceApiKeyAuth.from_env({})

    auth = FinanceApiKeyAuth({"client-a": KEY})
    with pytest.raises(FinanceApiAuthError) as missing:
        auth.authenticate()
    assert missing.value.code == "missing_api_key"

    with pytest.raises(FinanceApiAuthError) as invalid:
        auth.authenticate("Bearer wrong-key-that-is-long-enough")
    assert invalid.value.code == "invalid_api_key"


def test_finance_api_key_auth_loads_multiple_named_keys() -> None:
    auth = FinanceApiKeyAuth.from_env(
        {
            "FINANCE_API_KEYS_JSON": (
                '{"internal":"aaaaaaaaaaaaaaaaaaaaaaaa",'
                '"partner":"bbbbbbbbbbbbbbbbbbbbbbbb"}'
            )
        }
    )
    assert auth.authenticate(x_api_key="bbbbbbbbbbbbbbbbbbbbbbbb").principal_id == "partner"
