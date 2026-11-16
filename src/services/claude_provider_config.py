"""Provider settings for the optional CC report-question runtime."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DASHSCOPE_ANTHROPIC_BASE_URL = "https://dashscope.aliyuncs.com/apps/anthropic"
_DASHSCOPE_ROOT_R46_CA = Path(__file__).resolve().parents[2] / "config/certs/globalsign-root-r46.pem"

def _trim(value):
    return str(value or "").strip()


class ClaudeProviderConfig:
    def __init__(self, *, provider: str, model: str):
        self.provider = _trim(provider).lower()
        if self.provider not in {"anthropic", "deepseek", "dashscope", "gateway"}:
            raise ValueError(f"unsupported Claude provider: {self.provider or '-'}")
        self.model = _trim(model)
        self.capabilities = None
        self.base_url = self._resolve_base_url("")

    def provider_env(self) -> Dict[str, str]:
        env = {"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1"}
        if self.capabilities and self.capabilities.mcp_servers:
            env["ENABLE_TOOL_SEARCH"] = "true"
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
        if self.provider == "deepseek":
            token = _trim(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CLAUDE_AUTH_TOKEN"))
            if token:
                env["ANTHROPIC_AUTH_TOKEN"] = token
        elif self.provider == "dashscope":
            token = _trim(
                os.environ.get("DASHSCOPE_API_KEY")
                or os.environ.get("LLM_API_KEY")
                or os.environ.get("LLM_KEY")
                or os.environ.get("CLAUDE_AUTH_TOKEN")
            )
            if token:
                env["ANTHROPIC_AUTH_TOKEN"] = token
            # DashScope moved its public TLS chain to GlobalSign Root R46.
            # Some bundled Claude CLI builds have not refreshed that trust
            # anchor yet. Append the public root instead of disabling TLS.
            ca_bundle = _trim(
                os.environ.get("DASHSCOPE_ANTHROPIC_CA_BUNDLE")
                or os.environ.get("NODE_EXTRA_CA_CERTS")
            )
            if not ca_bundle and _DASHSCOPE_ROOT_R46_CA.is_file():
                ca_bundle = str(_DASHSCOPE_ROOT_R46_CA)
            if ca_bundle:
                ca_path = Path(ca_bundle).expanduser()
                if not ca_path.is_file():
                    raise ValueError(
                        "DashScope Anthropic CA bundle does not exist: "
                        f"{ca_path}"
                    )
                env["NODE_EXTRA_CA_CERTS"] = str(ca_path.resolve())
        else:
            api_key = _trim(os.environ.get("ANTHROPIC_API_KEY"))
            auth_token = _trim(os.environ.get("CLAUDE_AUTH_TOKEN"))
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            elif auth_token:
                env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        if self.provider in {"deepseek", "dashscope"}:
            env.update({
                "ANTHROPIC_MODEL": self.model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": self.model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": self.model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.model,
                "CLAUDE_CODE_SUBAGENT_MODEL": self.model,
            })
        return env

    def _resolve_base_url(self, configured: str) -> str:
        supplied = _trim(configured)
        expected = {
            "deepseek": DEEPSEEK_ANTHROPIC_BASE_URL,
        }.get(self.provider, "")
        if expected and supplied and supplied.rstrip("/") != expected:
            raise ValueError(f"{self.provider} credentials may only use {expected}")
        if expected:
            return expected
        if self.provider == "dashscope":
            endpoint = (
                supplied
                or _trim(os.environ.get("DASHSCOPE_ANTHROPIC_BASE_URL"))
                or DASHSCOPE_ANTHROPIC_BASE_URL
            )
            if not self._is_trusted_dashscope_endpoint(endpoint):
                raise ValueError("dashscope credentials require an official Alibaba Cloud Anthropic endpoint")
            return endpoint.rstrip("/")
        if self.provider in {"anthropic", "gateway"} and not supplied:
            supplied = _trim(os.environ.get("CLAUDE_BASE_URL"))
        if self.provider == "gateway" and not supplied:
            raise ValueError("CLAUDE_BASE_URL is required for gateway provider")
        return supplied.rstrip("/")

    @staticmethod
    def _is_trusted_dashscope_endpoint(value: str) -> bool:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        trusted_host = host in {
            "dashscope.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "coding.dashscope.aliyuncs.com",
            "coding-intl.dashscope.aliyuncs.com",
            "token-plan.cn-beijing.maas.aliyuncs.com",
        } or host.endswith(".maas.aliyuncs.com")
        return parsed.scheme == "https" and trusted_host and parsed.path.rstrip("/") == "/apps/anthropic"
