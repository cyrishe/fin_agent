from __future__ import annotations

from dataclasses import dataclass
import sys
from types import ModuleType

import pytest

from src.services.phone_identity_verification_service import (
    PhoneIdentityVerificationService,
    PhoneVerificationError,
)


@dataclass
class _ResultObject:
    biz_code: str


@dataclass
class _Body:
    code: str
    message: str
    request_id: str
    result_object: _ResultObject


@dataclass
class _Response:
    body: _Body


def _aliyun_response(*, biz_code: str, request_id: str = "request-1") -> _Response:
    return _Response(
        body=_Body(
            code="200",
            message="provider detail",
            request_id=request_id,
            result_object=_ResultObject(biz_code=biz_code),
        )
    )


def _aliyun_service(caller) -> PhoneIdentityVerificationService:
    return PhoneIdentityVerificationService(
        provider="aliyun",
        aliyun_access_key_id="test-id",
        aliyun_access_key_secret="test-secret",
        aliyun_caller=caller,
    )


def test_from_env_defaults_to_disabled() -> None:
    service = PhoneIdentityVerificationService.from_env({})

    assert service.status() == {
        "provider": "disabled",
        "supported": True,
        "enabled": False,
        "configured": True,
        "required_user_fields": ["real_name", "mobile"],
    }
    with pytest.raises(PhoneVerificationError) as captured:
        service.verify("张三", "13800138000")

    assert captured.value.code == "phone_verification_disabled"
    assert captured.value.retryable is False


def test_fin_agent_provider_has_priority_over_compatible_pna_provider() -> None:
    service = PhoneIdentityVerificationService.from_env(
        {
            "FIN_AGENT_PHONE_VERIFY_PROVIDER": "disabled",
            "PNA_REALNAME_PROVIDER": "mock",
            "FIN_AGENT_PHONE_VERIFY_MOCK_ENABLED": "1",
        }
    )

    assert service.status()["provider"] == "disabled"


def test_mock_requires_explicit_fin_agent_switch() -> None:
    disabled_mock = PhoneIdentityVerificationService.from_env(
        {
            "FIN_AGENT_PHONE_VERIFY_PROVIDER": "mock",
            "PNA_REALNAME_MOCK_ENABLED": "1",
        }
    )
    with pytest.raises(PhoneVerificationError) as captured:
        disabled_mock.verify("张三", "13800138000")
    assert captured.value.code == "phone_verification_mock_disabled"

    enabled_mock = PhoneIdentityVerificationService.from_env(
        {
            "FIN_AGENT_PHONE_VERIFY_PROVIDER": "mock",
            "FIN_AGENT_PHONE_VERIFY_MOCK_ENABLED": "1",
        }
    )
    result = enabled_mock.verify(" 张三 ", "13800138000")

    assert result.passed is True
    assert result.provider == "mock"
    assert result.request_id is None


@pytest.mark.parametrize(
    "mobile",
    [
        "1380013800",
        "138001380000",
        "12800138000",
        "+8613800138000",
        " 13800138000",
        "138 0013 8000",
        "138٠٠١٣٨٠٠٠",
    ],
)
def test_mobile_must_already_be_normalized_mainland_number(mobile: str) -> None:
    service = PhoneIdentityVerificationService(
        provider="mock",
        mock_enabled=True,
    )

    with pytest.raises(PhoneVerificationError) as captured:
        service.verify("张三", mobile)

    assert captured.value.code == "invalid_mobile"
    assert captured.value.retryable is False


def test_real_name_requires_at_least_two_characters() -> None:
    service = PhoneIdentityVerificationService(
        provider="mock",
        mock_enabled=True,
    )

    with pytest.raises(PhoneVerificationError) as captured:
        service.verify("张", "13800138000")

    assert captured.value.code == "invalid_real_name"


def test_aliyun_biz_code_one_returns_stable_result() -> None:
    service = _aliyun_service(
        lambda real_name, mobile: _aliyun_response(
            biz_code="1",
            request_id="internal-request-id",
        )
    )

    result = service.verify("张三", "13800138000")

    assert result.passed is True
    assert result.provider == "aliyun"
    assert result.code == "verified"
    assert result.public_message == "手机号实名核验通过。"
    assert result.request_id == "internal-request-id"


@pytest.mark.parametrize(
    ("biz_code", "expected_code"),
    [
        ("2", "phone_identity_mismatch"),
        ("3", "phone_identity_not_found"),
    ],
)
def test_aliyun_identity_failures_are_stable_user_errors(
    biz_code: str,
    expected_code: str,
) -> None:
    service = _aliyun_service(
        lambda real_name, mobile: _aliyun_response(biz_code=biz_code)
    )

    with pytest.raises(PhoneVerificationError) as captured:
        service.verify("张三", "13800138000")

    assert captured.value.code == expected_code
    assert captured.value.retryable is False
    assert "provider detail" not in captured.value.public_message


def test_aliyun_exception_is_not_exposed_to_caller() -> None:
    private_provider_message = "secret provider diagnostic"

    def fail(_real_name: str, _mobile: str) -> object:
        raise RuntimeError(private_provider_message)

    service = _aliyun_service(fail)

    with pytest.raises(PhoneVerificationError) as captured:
        service.verify("张三", "13800138000")

    assert captured.value.code == "phone_verification_unavailable"
    assert captured.value.retryable is True
    assert private_provider_message not in captured.value.public_message
    assert private_provider_message not in str(captured.value)


def test_aliyun_provider_error_does_not_expose_provider_message() -> None:
    response = _Response(
        body=_Body(
            code="500",
            message="credential detail that must stay internal",
            request_id="request-500",
            result_object=_ResultObject(biz_code=""),
        )
    )
    service = _aliyun_service(lambda real_name, mobile: response)

    with pytest.raises(PhoneVerificationError) as captured:
        service.verify("张三", "13800138000")

    assert captured.value.code == "phone_verification_unavailable"
    assert "credential detail" not in captured.value.public_message


def test_aliyun_sdk_with_options_disables_retry_and_sets_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, **kwargs) -> None:
            captured["config"] = kwargs

    class FakeRequest:
        def __init__(self, **kwargs) -> None:
            captured["request"] = kwargs

    class FakeRuntimeOptions:
        def __init__(self, **kwargs) -> None:
            captured["runtime"] = kwargs

    class FakeClient:
        def __init__(self, config) -> None:
            captured["client_config"] = config

        def mobile_2meta_verify_with_options(self, request, runtime) -> _Response:
            captured["request_object"] = request
            captured["runtime_object"] = runtime
            return _aliyun_response(biz_code="1")

        def mobile_2meta_verify(self, request) -> object:
            raise AssertionError("with_options must be used when available")

    cloud_models = ModuleType("alibabacloud_cloudauth20190307.models")
    cloud_models.Mobile2MetaVerifyRequest = FakeRequest
    cloud_client = ModuleType("alibabacloud_cloudauth20190307.client")
    cloud_client.Client = FakeClient
    cloud_package = ModuleType("alibabacloud_cloudauth20190307")
    cloud_package.models = cloud_models

    openapi_models = ModuleType("alibabacloud_tea_openapi.models")
    openapi_models.Config = FakeConfig
    openapi_package = ModuleType("alibabacloud_tea_openapi")
    openapi_package.models = openapi_models

    util_models = ModuleType("alibabacloud_tea_util.models")
    util_models.RuntimeOptions = FakeRuntimeOptions
    util_package = ModuleType("alibabacloud_tea_util")
    util_package.models = util_models

    modules = {
        "alibabacloud_cloudauth20190307": cloud_package,
        "alibabacloud_cloudauth20190307.models": cloud_models,
        "alibabacloud_cloudauth20190307.client": cloud_client,
        "alibabacloud_tea_openapi": openapi_package,
        "alibabacloud_tea_openapi.models": openapi_models,
        "alibabacloud_tea_util": util_package,
        "alibabacloud_tea_util.models": util_models,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    service = PhoneIdentityVerificationService(
        provider="aliyun",
        aliyun_access_key_id="test-id",
        aliyun_access_key_secret="test-secret",
        connect_timeout_ms=1_234,
        read_timeout_ms=5_678,
    )

    assert service.verify("张三", "13800138000").passed is True
    assert captured["runtime"] == {
        "connect_timeout": 1_234,
        "read_timeout": 5_678,
        "autoretry": False,
        "max_attempts": 1,
    }
