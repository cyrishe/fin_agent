from __future__ import annotations

from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT_DIR = Path(__file__).resolve().parents[1]
DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DASHSCOPE_ANTHROPIC_BASE_URL = "https://dashscope.aliyuncs.com/apps/anthropic"
DEEPSEEK_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})
EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
CREDENTIAL_PLACEHOLDERS = frozenset({"replace-me", "your-key", "your-api-key"})


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration. Secret values are excluded from repr and summaries."""

    root_dir: Path = ROOT_DIR
    backend: str = "fake"
    provider: str = "anthropic"
    model: str = "sonnet"
    effort: str = ""
    base_url: str = ""
    auth_token: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    client_api_key: str = field(default="", repr=False)
    allow_existing_login: bool = False
    allow_session_resume: bool = False
    web_search_backend: str = "builtin"
    searxng_base_url: str = ""
    max_turns: int = 8
    max_budget_usd: float = 1.0
    hard_timeout_seconds: int = 300
    idle_timeout_seconds: int = 90
    heartbeat_seconds: int = 15
    max_concurrent_runs: int = 4
    max_question_chars: int = 20_000
    max_metadata_bytes: int = 4_096

    @classmethod
    def from_env(cls) -> "Settings":
        root_dir = Path(os.getenv("CLAUDE_DEMO_ROOT", str(ROOT_DIR))).expanduser().resolve()
        provider = os.getenv("CLAUDE_PROVIDER", "anthropic").strip().lower()
        deepseek_mode = provider == "deepseek"
        dashscope_mode = provider == "dashscope"
        model_default = (
            "deepseek-v4-flash"
            if deepseek_mode
            else (os.getenv("LLM_DEFAULT_MODEL", "deepseek-v4-flash") if dashscope_mode else "sonnet")
        )
        base_url_default = (
            DEEPSEEK_ANTHROPIC_BASE_URL
            if deepseek_mode
            else (DASHSCOPE_ANTHROPIC_BASE_URL if dashscope_mode else "")
        )
        if deepseek_mode or dashscope_mode:
            # Do not accidentally reuse an Anthropic credential left in the
            # parent shell when switching this demo to another provider.
            provider_key = (
                os.getenv("DEEPSEEK_API_KEY")
                if deepseek_mode
                else (os.getenv("DASHSCOPE_API_KEY") or os.getenv("LLM_KEY"))
            )
            auth_token = (os.getenv("CLAUDE_AUTH_TOKEN") or provider_key or "").strip()
            anthropic_api_key = ""
        else:
            auth_token = (
                os.getenv("CLAUDE_AUTH_TOKEN") or os.getenv("ANTHROPIC_AUTH_TOKEN") or ""
            ).strip()
            anthropic_api_key = (
                os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or ""
            ).strip()
        return cls(
            root_dir=root_dir,
            backend=os.getenv("CLAUDE_DEMO_BACKEND", "fake").strip().lower(),
            provider=provider,
            model=(os.getenv("CLAUDE_MODEL") or os.getenv("ANTHROPIC_MODEL") or model_default).strip(),
            effort=(
                os.getenv("CLAUDE_EFFORT")
                or os.getenv("CLAUDE_CODE_EFFORT_LEVEL")
                or ("max" if deepseek_mode else "")
            ).strip().lower(),
            base_url=(
                os.getenv("CLAUDE_BASE_URL")
                or os.getenv("ANTHROPIC_BASE_URL")
                or base_url_default
            ).strip().rstrip("/"),
            auth_token=auth_token,
            anthropic_api_key=anthropic_api_key,
            client_api_key=os.getenv("DEMO_CLIENT_API_KEY", "").strip(),
            allow_existing_login=_env_bool("CLAUDE_ALLOW_EXISTING_LOGIN"),
            allow_session_resume=_env_bool("DEMO_ALLOW_SESSION_RESUME"),
            web_search_backend=os.getenv("WEB_SEARCH_BACKEND", "builtin").strip().lower(),
            searxng_base_url=os.getenv("SEARXNG_BASE_URL", "").strip().rstrip("/"),
            max_turns=_env_int("CLAUDE_MAX_TURNS", 8),
            max_budget_usd=_env_float("CLAUDE_MAX_BUDGET_USD", 1.0),
            hard_timeout_seconds=_env_int("CLAUDE_HARD_TIMEOUT_SECONDS", 300),
            idle_timeout_seconds=_env_int("CLAUDE_IDLE_TIMEOUT_SECONDS", 90),
            heartbeat_seconds=_env_int("SSE_HEARTBEAT_SECONDS", 15),
            max_concurrent_runs=_env_int("MAX_CONCURRENT_RUNS", 4),
            max_question_chars=_env_int("MAX_QUESTION_CHARS", 20_000),
            max_metadata_bytes=_env_int("MAX_METADATA_BYTES", 4_096),
        )

    @property
    def workspace_dir(self) -> Path:
        return self.root_dir / "workspace"

    @property
    def system_prompt_path(self) -> Path:
        return self.root_dir / "prompts" / "system.md"

    @property
    def claude_config_dir(self) -> Path:
        return self.root_dir / ".runtime" / "claude"

    @property
    def max_request_bytes(self) -> int:
        return self.max_question_chars * 4 + self.max_metadata_bytes + 4_096

    def provider_env(self) -> dict[str, str]:
        env = {
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
            "CLAUDE_CONFIG_DIR": str(self.claude_config_dir),
        }
        trusted_provider_endpoints = {
            "deepseek": DEEPSEEK_ANTHROPIC_BASE_URL,
            "dashscope": DASHSCOPE_ANTHROPIC_BASE_URL,
        }
        expected_endpoint = trusted_provider_endpoints.get(self.provider)
        if expected_endpoint and self.base_url != expected_endpoint:
            raise ValueError(
                f"{self.provider} credentials may only be sent to its configured official Anthropic endpoint"
            )
        if self.base_url:
            env["ANTHROPIC_BASE_URL"] = self.base_url
        if self.auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = self.auth_token
        elif self.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        if self.provider in {"deepseek", "dashscope"}:
            # Pin every Claude Code model role to the selected DeepSeek model. This
            # avoids an unexpected provider/model switch for background work.
            env.update(
                {
                    "ANTHROPIC_MODEL": self.model,
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": self.model,
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": self.model,
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.model,
                    "CLAUDE_CODE_SUBAGENT_MODEL": self.model,
                }
            )
            if self.effort:
                env["CLAUDE_CODE_EFFORT_LEVEL"] = self.effort
        return env

    def readiness(self) -> dict[str, Any]:
        issues: list[str] = []
        warnings: list[str] = []
        if self.backend not in {"fake", "claude"}:
            issues.append("CLAUDE_DEMO_BACKEND must be 'fake' or 'claude'")
        if self.provider not in {"anthropic", "deepseek", "dashscope", "gateway"}:
            issues.append(
                "CLAUDE_PROVIDER must be 'anthropic', 'deepseek', 'dashscope', or 'gateway'"
            )
        if self.effort and self.effort not in EFFORT_LEVELS:
            issues.append("CLAUDE_EFFORT must be low, medium, high, xhigh, or max")
        if self.web_search_backend not in {"builtin", "searxng", "disabled"}:
            issues.append("WEB_SEARCH_BACKEND must be builtin, searxng, or disabled")
        if self.backend == "claude" and importlib.util.find_spec("claude_agent_sdk") is None:
            issues.append("claude-agent-sdk is not installed")
        if self.backend == "claude" and not (
            self.auth_token or self.anthropic_api_key or self.allow_existing_login
        ):
            issues.append("explicit model credentials are missing")
        if self.backend == "claude" and (
            self.auth_token.lower() in CREDENTIAL_PLACEHOLDERS
            or self.anthropic_api_key.lower() in CREDENTIAL_PLACEHOLDERS
        ):
            issues.append("replace the credential placeholder before making a model request")
        if self.provider == "gateway":
            if not self.base_url:
                issues.append("CLAUDE_BASE_URL is required for gateway mode")
            elif not _safe_service_url(self.base_url):
                issues.append("CLAUDE_BASE_URL must use HTTPS or a loopback HTTP address")
            warnings.append("gateway must implement Anthropic Messages streaming semantics")
        if self.provider == "deepseek":
            if self.base_url != DEEPSEEK_ANTHROPIC_BASE_URL:
                issues.append(
                    f"DeepSeek mode requires the official endpoint {DEEPSEEK_ANTHROPIC_BASE_URL}"
                )
            if self.model not in DEEPSEEK_MODELS:
                issues.append(
                    "DeepSeek mode requires an explicit deepseek-v4-flash or deepseek-v4-pro model ID"
                )
            warnings.append(
                "DeepSeek Anthropic compatibility is text/tool oriented; image, document, and MCP connector blocks are unsupported"
            )
        if self.provider == "dashscope":
            if self.base_url != DASHSCOPE_ANTHROPIC_BASE_URL:
                issues.append(
                    f"DashScope mode requires the official endpoint {DASHSCOPE_ANTHROPIC_BASE_URL}"
                )
            warnings.append(
                "DashScope provider capabilities depend on its Anthropic compatibility layer and selected model; run the credentialed tool/SDK probes"
            )
        if self.web_search_backend == "searxng":
            if not self.searxng_base_url:
                issues.append("SEARXNG_BASE_URL is required for searxng search")
            elif not _safe_service_url(self.searxng_base_url):
                issues.append("SEARXNG_BASE_URL must use HTTPS or a loopback HTTP address")
        if self.web_search_backend == "builtin" and self.provider == "gateway":
            warnings.append("built-in WebSearch requires gateway/model support; use searxng as a portable fallback")
        return {
            "ready": not issues,
            "backend": self.backend,
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort or None,
            "base_url_configured": bool(self.base_url),
            "credentials_configured": bool(self.auth_token or self.anthropic_api_key),
            "web_search_backend": self.web_search_backend,
            "issues": issues,
            "warnings": warnings,
        }


def _safe_service_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme == "https" and bool(parsed.netloc):
        return True
    return parsed.scheme == "http" and (parsed.hostname or "").lower() in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
