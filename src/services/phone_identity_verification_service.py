from __future__ import annotations

import logging
import os
import re
from importlib.util import find_spec
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

_MAINLAND_MOBILE_PATTERN = re.compile(r"1[3-9][0-9]{9}")
_SUPPORTED_PROVIDERS = frozenset({"disabled", "mock", "aliyun"})


class PhoneVerificationError(ValueError):
    """Stable public error raised by phone identity verification."""

    def __init__(self, code: str, public_message: str, retryable: bool):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable


@dataclass(frozen=True)
class PhoneVerificationResult:
    passed: bool
    provider: str
    code: str
    public_message: str
    verified_at: str
    request_id: str | None = None

    @property
    def message(self) -> str:
        """Compatibility alias for callers that render a generic message field."""

        return self.public_message


AliyunCaller = Callable[[str, str], object]


class PhoneIdentityVerificationService:
    """Verify that a mainland mobile number is registered to a real name.

    This service is deliberately separate from SMS/OTP verification. It checks
    carrier identity data and does not prove possession of the mobile device.
    """

    def __init__(
        self,
        *,
        provider: str = "disabled",
        mock_enabled: bool = False,
        aliyun_access_key_id: str | None = None,
        aliyun_access_key_secret: str | None = None,
        aliyun_endpoint: str = "cloudauth.aliyuncs.com",
        aliyun_region_id: str = "cn-beijing",
        connect_timeout_ms: int = 3_000,
        read_timeout_ms: int = 5_000,
        aliyun_caller: AliyunCaller | None = None,
    ):
        self.provider = (provider or "disabled").strip().lower()
        self.mock_enabled = mock_enabled
        self._aliyun_access_key_id = aliyun_access_key_id
        self._aliyun_access_key_secret = aliyun_access_key_secret
        self._aliyun_endpoint = aliyun_endpoint
        self._aliyun_region_id = aliyun_region_id
        self._connect_timeout_ms = max(1, connect_timeout_ms)
        self._read_timeout_ms = max(1, read_timeout_ms)
        self._aliyun_caller = aliyun_caller

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        aliyun_caller: AliyunCaller | None = None,
    ) -> PhoneIdentityVerificationService:
        source = os.environ if env is None else env
        provider = (
            _first_non_empty(
                source,
                "FIN_AGENT_PHONE_VERIFY_PROVIDER",
                "PNA_REALNAME_PROVIDER",
            )
            or "disabled"
        )
        return cls(
            provider=provider,
            mock_enabled=source.get("FIN_AGENT_PHONE_VERIFY_MOCK_ENABLED", "").strip() == "1",
            aliyun_access_key_id=_first_non_empty(
                source,
                "FIN_AGENT_PHONE_VERIFY_ALIYUN_ACCESS_KEY_ID",
                "ALIYUN_ACCESS_KEY_ID",
                "ALIBABA_CLOUD_ACCESS_KEY_ID",
                "AccessKeyID",
            ),
            aliyun_access_key_secret=_first_non_empty(
                source,
                "FIN_AGENT_PHONE_VERIFY_ALIYUN_ACCESS_KEY_SECRET",
                "ALIYUN_ACCESS_KEY_SECRET",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
                "AccessKeySecret",
            ),
            aliyun_endpoint=(
                _first_non_empty(
                    source,
                    "FIN_AGENT_PHONE_VERIFY_ALIYUN_ENDPOINT",
                    "ALIYUN_CLOUDAUTH_ENDPOINT",
                )
                or "cloudauth.aliyuncs.com"
            ),
            aliyun_region_id=(
                _first_non_empty(
                    source,
                    "FIN_AGENT_PHONE_VERIFY_ALIYUN_REGION_ID",
                    "ALIYUN_REGION_ID",
                )
                or "cn-beijing"
            ),
            connect_timeout_ms=_positive_int(
                source.get("FIN_AGENT_PHONE_VERIFY_CONNECT_TIMEOUT_MS"),
                default=3_000,
            ),
            read_timeout_ms=_positive_int(
                source.get("FIN_AGENT_PHONE_VERIFY_READ_TIMEOUT_MS"),
                default=5_000,
            ),
            aliyun_caller=aliyun_caller,
        )

    def status(self) -> dict[str, Any]:
        if self.provider == "disabled":
            configured = True
            enabled = False
        elif self.provider == "mock":
            configured = self.mock_enabled
            enabled = self.mock_enabled
        elif self.provider == "aliyun":
            configured = bool(
                self._aliyun_access_key_id and self._aliyun_access_key_secret
            )
            sdk_available = self._aliyun_caller is not None or all(
                find_spec(module_name) is not None
                for module_name in (
                    "alibabacloud_cloudauth20190307",
                    "alibabacloud_tea_openapi",
                    "alibabacloud_tea_util",
                )
            )
            enabled = configured and sdk_available
        else:
            configured = False
            enabled = False
        return {
            "provider": self.provider,
            "supported": self.provider in _SUPPORTED_PROVIDERS,
            "enabled": enabled,
            "configured": configured,
            "required_user_fields": ["real_name", "mobile"],
        }

    def verify(self, real_name: str, mobile: str) -> PhoneVerificationResult:
        normalized_name = _validate_real_name(real_name)
        normalized_mobile = _validate_normalized_mobile(mobile)

        if self.provider == "disabled":
            raise PhoneVerificationError(
                "phone_verification_disabled",
                "手机号实名核验当前未启用。",
                False,
            )
        if self.provider == "mock":
            if not self.mock_enabled:
                raise PhoneVerificationError(
                    "phone_verification_mock_disabled",
                    "手机号实名核验的模拟服务未启用。",
                    False,
                )
            return PhoneVerificationResult(
                passed=True,
                provider="mock",
                code="verified",
                public_message="手机号实名核验通过。",
                verified_at=_now_iso(),
            )
        if self.provider == "aliyun":
            return self._verify_with_aliyun(normalized_name, normalized_mobile)
        raise PhoneVerificationError(
            "phone_verification_provider_unsupported",
            "手机号实名核验服务配置不受支持。",
            False,
        )

    def _verify_with_aliyun(
        self,
        real_name: str,
        mobile: str,
    ) -> PhoneVerificationResult:
        if not (self._aliyun_access_key_id and self._aliyun_access_key_secret):
            raise PhoneVerificationError(
                "phone_verification_not_configured",
                "手机号实名核验服务尚未配置。",
                False,
            )

        try:
            response = (
                self._aliyun_caller(real_name, mobile)
                if self._aliyun_caller is not None
                else self._call_aliyun_sdk(real_name, mobile)
            )
        except PhoneVerificationError:
            raise
        except Exception:
            # Provider exceptions may embed request parameters. Keep the
            # operational signal without writing a name or phone to logs.
            logger.warning("Aliyun phone identity verification request failed")
            raise PhoneVerificationError(
                "phone_verification_unavailable",
                "手机号实名核验服务暂时不可用，请稍后重试。",
                True,
            ) from None

        body = _read_value(response, "body") or response
        provider_code = str(_read_value(body, "code", "Code") or "")
        provider_message = str(_read_value(body, "message", "Message") or "")
        request_id = _optional_text(
            _read_value(body, "request_id", "requestId", "RequestId")
        )
        result_object = _read_value(
            body,
            "result_object",
            "resultObject",
            "ResultObject",
        )
        biz_code = str(
            _read_value(result_object, "biz_code", "bizCode", "BizCode") or ""
        )

        if provider_code != "200":
            logger.warning(
                "Aliyun phone identity verification returned provider error "
                "code=%s request_id=%s",
                provider_code or "missing",
                request_id or "missing",
            )
            raise PhoneVerificationError(
                "phone_verification_unavailable",
                "手机号实名核验服务暂时不可用，请稍后重试。",
                True,
            )
        if biz_code == "2":
            raise PhoneVerificationError(
                "phone_identity_mismatch",
                "姓名与手机号实名信息不一致，请检查后重试。",
                False,
            )
        if biz_code == "3":
            raise PhoneVerificationError(
                "phone_identity_not_found",
                "运营商未查询到该姓名与手机号的实名记录。",
                False,
            )
        if biz_code != "1":
            logger.warning(
                "Aliyun phone identity verification returned unknown BizCode "
                "biz_code=%s request_id=%s provider_message_present=%s",
                biz_code or "missing",
                request_id or "missing",
                bool(provider_message),
            )
            raise PhoneVerificationError(
                "phone_verification_unavailable",
                "手机号实名核验服务暂时不可用，请稍后重试。",
                True,
            )

        return PhoneVerificationResult(
            passed=True,
            provider="aliyun",
            code="verified",
            public_message="手机号实名核验通过。",
            verified_at=_now_iso(),
            request_id=request_id,
        )

    def _call_aliyun_sdk(self, real_name: str, mobile: str) -> object:
        try:
            from alibabacloud_cloudauth20190307 import (
                models as cloudauth_models,
            )
            from alibabacloud_cloudauth20190307.client import (
                Client as CloudAuthClient,
            )
            from alibabacloud_tea_openapi import models as openapi_models
            from alibabacloud_tea_util import models as util_models
        except ImportError:
            raise PhoneVerificationError(
                "phone_verification_sdk_unavailable",
                "手机号实名核验服务暂时不可用，请稍后重试。",
                True,
            ) from None

        config = openapi_models.Config(
            access_key_id=self._aliyun_access_key_id,
            access_key_secret=self._aliyun_access_key_secret,
            endpoint=self._aliyun_endpoint,
            region_id=self._aliyun_region_id,
        )
        client = CloudAuthClient(config)
        request = cloudauth_models.Mobile2MetaVerifyRequest(
            mobile=mobile,
            param_type="normal",
            user_name=real_name,
        )

        call_with_options = getattr(
            client,
            "mobile_2meta_verify_with_options",
            None,
        )
        if callable(call_with_options):
            runtime = util_models.RuntimeOptions(
                connect_timeout=self._connect_timeout_ms,
                read_timeout=self._read_timeout_ms,
                autoretry=False,
                max_attempts=1,
            )
            return call_with_options(request, runtime)
        return client.mobile_2meta_verify(request)


def _validate_real_name(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if len(normalized) < 2:
        raise PhoneVerificationError(
            "invalid_real_name",
            "真实姓名至少需要 2 个字。",
            False,
        )
    return normalized


def _validate_normalized_mobile(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or _MAINLAND_MOBILE_PATTERN.fullmatch(value) is None
    ):
        raise PhoneVerificationError(
            "invalid_mobile",
            "手机号格式不正确，请输入规范化后的 11 位中国大陆手机号。",
            False,
        )
    return value


def _first_non_empty(source: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        value = source.get(key)
        if value is not None and value.strip():
            return value.strip()
    return None


def _positive_int(value: str | None, *, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _read_value(source: object, *names: str) -> object | None:
    if source is None:
        return None
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "PhoneIdentityVerificationService",
    "PhoneVerificationError",
    "PhoneVerificationResult",
]
