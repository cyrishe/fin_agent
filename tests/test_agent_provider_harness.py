from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any

import pytest

from src.services.agent_providers import (
    AgentCapabilityPolicy,
    AgentComplexityLevel,
    AgentEventCoalescer,
    AgentSkillHarness,
    ClaudeSdkSkillHarness,
    WebSearchPolicy,
    agent_capability_policy_from_env,
    build_agent_skill_harness,
    normalize_agent_run_result,
    resolve_agent_profile,
)
from src.services.agent_providers.claude import (
    DASHSCOPE_ANTHROPIC_BASE_URL,
    DEEPSEEK_ANTHROPIC_BASE_URL,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CLAUDE_PROVIDER,
)
from src.services.codex_exec_skill_harness import CodexExecSkillHarness, CodexSdkSkillHarness
from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService
from src.services.custom_tool_service import CustomToolAgentService, CustomToolStoreService
from src.services.llm_stream_block_service import LlmStreamBlockBuilder


def _message(class_name: str, **attrs: Any) -> Any:
    value = type(class_name, (), {})()
    for key, item in attrs.items():
        setattr(value, key, item)
    return value


def _simple_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "type", "status", "message"],
        "properties": {
            "source": {"type": "string", "enum": ["model"]},
            "type": {"type": "string", "enum": ["final"]},
            "status": {"type": "string", "enum": ["review"]},
            "message": {"type": "string"},
        },
    }


def test_provider_factory_returns_one_shared_harness_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROVIDER", "anthropic")
    monkeypatch.delenv("CUSTOM_TOOL_AGENT_COMPLEXITY", raising=False)

    codex = build_agent_skill_harness("codex")
    claude = build_agent_skill_harness("claude")

    assert isinstance(codex, CodexSdkSkillHarness)
    assert isinstance(claude, ClaudeSdkSkillHarness)
    assert isinstance(codex, AgentSkillHarness)
    assert isinstance(claude, AgentSkillHarness)
    assert codex.provider_name == "codex"
    assert claude.provider_name == "claude"
    assert codex.capabilities is None
    assert claude.capabilities is None


def test_four_complexity_levels_resolve_to_provider_specific_models() -> None:
    assert resolve_agent_profile("codex", "fastest").model == "gpt-5.6-luna"
    assert resolve_agent_profile("codex", "fastest").reasoning_effort == "low"
    assert resolve_agent_profile("codex", "fast").model == "gpt-5.6-terra"
    assert resolve_agent_profile("codex", "fast").reasoning_effort == "high"
    assert resolve_agent_profile("codex", "mid").reasoning_effort == "medium"
    assert resolve_agent_profile("codex", "high").model == "gpt-5.6-sol"
    assert resolve_agent_profile("codex", "high").reasoning_effort == "high"

    assert resolve_agent_profile("claude", "fastest").model == "deepseek-chat"
    assert resolve_agent_profile("claude", "fastest").thinking == "disabled"
    assert resolve_agent_profile("claude", "fast").reasoning_effort == "high"
    assert resolve_agent_profile("claude", "mid").model == "deepseek-chat"
    assert resolve_agent_profile("claude", "mid").reasoning_effort == "medium"
    assert resolve_agent_profile("claude", "high").model == "deepseek-reasoner"
    assert resolve_agent_profile("claude", "high").reasoning_effort == "high"
    assert resolve_agent_profile(
        "claude", "mid", claude_transport_provider="dashscope"
    ).model == "deepseek-v4-pro"


def test_factory_applies_profile_without_changing_capability_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CUSTOM_TOOL_AGENT_COMPLEXITY",
        "STOCK_AGENT_CUSTOM_TOOL_CODEX_MODEL",
        "STOCK_AGENT_CUSTOM_TOOL_CODEX_REASONING",
        "STOCK_AGENT_CUSTOM_TOOL_CLAUDE_MODEL",
        "STOCK_AGENT_CUSTOM_TOOL_CLAUDE_EFFORT",
        "STOCK_AGENT_CUSTOM_TOOL_CLAUDE_MAX_TURNS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_PROVIDER", "dashscope")

    codex = build_agent_skill_harness("codex", complexity=AgentComplexityLevel.FASTEST)
    claude = build_agent_skill_harness("claude", complexity="mid")

    assert codex.model == "gpt-5.6-luna"
    assert codex.reasoning_effort == "low"
    assert codex.complexity_level == "fastest"
    assert codex.capabilities is None
    assert claude.model == "deepseek-v4-pro"
    assert claude.effort == "medium"
    assert claude.max_turns == 20
    assert claude.complexity_level == "mid"
    assert claude.capabilities is None


def test_capability_policy_rejects_mcp_permission_drift() -> None:
    with pytest.raises(ValueError, match="explicit command or url"):
        AgentCapabilityPolicy(mcp_servers={"github": {"allowed_tools": ["list_issues"]}})
    with pytest.raises(ValueError, match="explicit allowed tool"):
        AgentCapabilityPolicy(mcp_servers={"github": {"type": "http", "url": "https://example.com/mcp"}})
    with pytest.raises(ValueError, match="unconfigured server"):
        AgentCapabilityPolicy(
            mcp_servers={"github": {"type": "http", "url": "https://example.com/mcp"}},
            mcp_allowed_tools=("mcp__other__read",),
        )


def test_runtime_capabilities_load_from_explicit_environment_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "agent-mcp.json"
    config.write_text(
        json.dumps({
            "mcp_servers": {
                "docs": {
                    "url": "https://example.com/mcp",
                    "bearer_token_env_var": "DOCS_MCP_TOKEN",
                    "allowed_tools": ["search"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("CUSTOM_TOOL_AGENT_WEB_SEARCH", "live")
    monkeypatch.setenv("CUSTOM_TOOL_AGENT_MCP_CONFIG_PATH", str(config))

    policy = agent_capability_policy_from_env()

    assert policy.web_search is WebSearchPolicy.LIVE
    assert policy.mcp_allowed_tools == ("mcp__docs__search",)


def test_provider_results_share_one_stable_runtime_envelope() -> None:
    result = normalize_agent_run_result(
        {"ok": True, "events": [{"source": "model", "type": "final"}], "final": {"status": "ok"}},
        provider="claude",
        stage="design",
        session_id="run_1",
    )

    assert result["protocol_version"] == "agent.run.v1"
    assert result["provider"] == "claude"
    assert result["stage"] == "design"
    assert result["session_id"] == "run_1"
    assert result["final"] == {"status": "ok"}
    for key in (
        "error",
        "timeout",
        "timeout_kind",
        "timeout_after_seconds",
        "provider_session_id",
        "raw_stdout",
        "raw_stderr",
        "last_message",
        "llm_usage",
        "context_bundle",
        "duration_ms",
    ):
        assert key in result


def test_provider_delta_events_are_coalesced_but_keep_stream_boundaries() -> None:
    coalescer = AgentEventCoalescer(min_chars=5)
    metadata = {"stage": "design"}

    assert coalescer.push({"source": "claude", "type": "agent_delta", "content": "ab", "metadata": metadata}) == []
    ready = coalescer.push({"source": "claude", "type": "agent_delta", "content": "cde", "metadata": metadata})
    assert [item["content"] for item in ready] == ["abcde"]
    boundary = coalescer.push({"source": "harness", "type": "turn_completed", "content": "done", "metadata": metadata})
    assert [item["type"] for item in boundary] == ["turn_completed"]


def test_claude_defaults_to_official_deepseek_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CLAUDE_PROVIDER",
        "CLAUDE_MODEL",
        "LLM_DEFAULT_MODEL",
        "STOCK_AGENT_CUSTOM_TOOL_CLAUDE_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    harness = ClaudeSdkSkillHarness(query_impl=lambda **kwargs: None)

    assert DEFAULT_CLAUDE_PROVIDER == "deepseek"
    assert DEFAULT_CLAUDE_MODEL == "deepseek-chat"
    assert harness.provider == "deepseek"
    assert harness.model == "deepseek-chat"
    assert harness.base_url == DEEPSEEK_ANTHROPIC_BASE_URL


def test_claude_and_codex_prepare_the_same_business_prompt() -> None:
    context = {"context_bundle": {"bundle_dir": "/tmp/bundle", "coding_workspace": {"editable": False}}}
    kwargs = {
        "skill_text": "---\nname: demo\n---\nDo the task.",
        "skill_root": "/tmp/skill",
        "user_request": "request",
        "context": context,
        "structured_output": True,
    }

    codex_prompt = CodexExecSkillHarness()._build_prompt(**kwargs)
    claude_prompt = ClaudeSdkSkillHarness(provider="anthropic", query_impl=lambda **items: None)._build_prompt(**kwargs)

    assert claude_prompt == codex_prompt


def test_dashscope_inlines_skill_without_requiring_local_claude_login(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\ndescription: demo\n---\nDo the task.", encoding="utf-8")
    plugin_root = tmp_path
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "demo-plugin"}), encoding="utf-8")

    dashscope = ClaudeSdkSkillHarness(provider="dashscope", query_impl=lambda **items: None)
    anthropic = ClaudeSdkSkillHarness(provider="anthropic", query_impl=lambda **items: None)

    assert dashscope._enabled_native_skill_config(skill.resolve()) == (None, "")
    plugin, qualified_skill = anthropic._enabled_native_skill_config(skill.resolve())
    assert plugin == {"type": "local", "path": str(plugin_root)}
    assert qualified_skill == "demo-plugin:demo"


def test_requirement_prompt_does_not_expose_implementation_file_guidance() -> None:
    kwargs = {
        "skill_text": "requirement instructions",
        "user_request": "理解需求",
        "context": {"context_bundle": {"bundle_dir": "/tmp/bundle"}},
        "structured_output": True,
        "stage": "requirement",
    }

    prompt = ClaudeSdkSkillHarness(provider="anthropic", query_impl=lambda **items: None)._build_prompt(**kwargs)

    assert "api_catalog/index.json" not in prompt
    assert "custom_tool_sdk.md" not in prompt


def test_design_prompt_keeps_finance_file_guidance() -> None:
    prompt = ClaudeSdkSkillHarness(provider="anthropic", query_impl=lambda **items: None)._build_prompt(
        skill_text="design instructions",
        user_request="设计工具",
        context={"context_bundle": {"bundle_dir": "/tmp/bundle"}},
        structured_output=True,
        stage="design",
    )

    assert "api_catalog/index.json" not in prompt
    assert "不要读取运行时和代码资料" in prompt
    assert "custom_tool_sdk.md" not in prompt


def test_custom_tool_service_selects_claude_without_changing_business_wrappers(tmp_path: Path) -> None:
    service = CustomToolAgentService(
        store=CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem"),
        agent_provider="claude",
    )

    assert isinstance(service.designer.harness, ClaudeSdkSkillHarness)
    assert isinstance(service.coder.harness, ClaudeSdkSkillHarness)


def test_custom_tool_service_uses_fast_claude_for_design_and_mid_claude_for_coding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CUSTOM_TOOL_DESIGN_PROVIDER", "claude")
    monkeypatch.setenv("CUSTOM_TOOL_DESIGN_COMPLEXITY", "fast")
    monkeypatch.setenv("CUSTOM_TOOL_CODING_PROVIDER", "claude")
    monkeypatch.setenv("CUSTOM_TOOL_CODING_COMPLEXITY", "mid")
    service = CustomToolAgentService(
        store=CustomToolStoreService(root_dir=str(tmp_path / "tools"), backend="filesystem"),
    )

    assert isinstance(service.designer.harness, ClaudeSdkSkillHarness)
    assert service.designer.harness.complexity_level == "fast"
    assert service.designer.harness.model == "deepseek-chat"
    assert isinstance(service.coder.harness, ClaudeSdkSkillHarness)
    assert service.coder.harness.complexity_level == "mid"
    assert service.coder.harness.model == "deepseek-chat"
    assert service.coder.harness.effort == "medium"


def test_claude_skill_run_uses_existing_skill_schema_and_final_contract(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any):
        captured["prompt"] = prompt
        captured["options"] = options
        yield _message(
            "SystemMessage",
            subtype="init",
            data={"session_id": "claude_session_1", "model": "test-model"},
        )
        yield _message(
            "StreamEvent",
            event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "working"}},
        )
        yield _message(
            "ResultMessage",
            subtype="success",
            is_error=False,
            result="done",
            structured_output={"source": "model", "type": "final", "status": "review", "message": "ok"},
            session_id="claude_session_1",
            usage={"input_tokens": 10, "output_tokens": 4},
        )

    skill = tmp_path / "skill" / "SKILL.md"
    schema = skill.parent / "schema.json"
    skill.parent.mkdir()
    skill.write_text("---\nname: demo\ndescription: demo\n---\nReturn the requested result.", encoding="utf-8")
    schema.write_text(json.dumps(_simple_schema()), encoding="utf-8")
    bundle_service = CustomToolContextBundleService(
        catalog_path=str(tmp_path / "missing-catalog.json"),
        root_dir=str(tmp_path / "bundles"),
    )
    harness = ClaudeSdkSkillHarness(
        cwd=str(tmp_path),
        model="test-model",
        provider="anthropic",
        query_impl=fake_query,
        context_bundle_service=bundle_service,
    )

    result = harness.run_skill(
        skill_path=str(skill),
        output_schema_path=str(schema),
        user_request="build it",
        context={"_provider_session_id": "claude_session_previous"},
        session_id="app_run_1",
        stage="design",
    )

    assert result["ok"] is True
    assert result["final"]["message"] == "ok"
    assert result["session_id"] == "app_run_1"
    assert result["provider_session_id"] == "claude_session_1"
    assert result["llm_usage"] == {"input_tokens": 10, "output_tokens": 4}
    assert "# SKILL" in captured["prompt"]
    assert "Return the requested result." in captured["prompt"]
    assert captured["options"].output_format["schema"] == _simple_schema()
    assert captured["options"].tools == []
    assert captured["options"].strict_mcp_config is True
    assert captured["options"].skills == []
    assert captured["options"].setting_sources == []
    assert captured["options"].resume == "claude_session_previous"
    assert "claude_session_previous" not in captured["prompt"]
    assert any(event.get("source") == "claude" and event.get("type") == "agent_delta" for event in result["events"])
    assert any(event.get("source") == "model" and event.get("type") == "final" for event in result["events"])


def test_claude_requires_authoritative_structured_output(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any):
        captured["options"] = options
        yield _message(
            "ResultMessage",
            subtype="success",
            is_error=False,
            result='{"source":"model","type":"final","status":"review","message":"not authoritative"}',
            structured_output=None,
            session_id="provider_session",
            usage={},
        )

    harness = ClaudeSdkSkillHarness(cwd=str(tmp_path), provider="anthropic", query_impl=fake_query)
    result = harness.run_turn(
        prompt="test",
        developer_instructions="read only",
        output_schema=_simple_schema(),
        stage="direct",
    )

    assert result["ok"] is False
    assert result["final"] == {}
    assert "missing_structured_output" in result["error"]
    assert captured["options"].tools == []


def test_dashscope_validates_text_json_when_native_structured_output_is_unavailable(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any):
        captured["options"] = options
        yield _message(
            "ResultMessage",
            subtype="success",
            is_error=False,
            result='{"status":"ok","message":"ds works"}',
            structured_output=None,
            session_id="dashscope_session",
            usage={},
        )

    schema = {
        "type": "object",
        "properties": {"status": {"const": "ok"}, "message": {"type": "string"}},
        "required": ["status", "message"],
        "additionalProperties": False,
    }
    harness = ClaudeSdkSkillHarness(
        cwd=str(tmp_path),
        provider="dashscope",
        model="deepseek-v4-flash",
        query_impl=fake_query,
    )

    result = harness.run_turn(
        prompt="test",
        developer_instructions="return the result",
        output_schema=schema,
        stage="direct",
    )

    assert result["ok"] is True
    assert result["final"] == {"status": "ok", "message": "ds works"}
    assert captured["options"].output_format is None
    assert "Return exactly one JSON object" in captured["options"].system_prompt


def test_dashscope_rejects_text_json_that_violates_output_schema(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: Any):
        yield _message(
            "ResultMessage",
            subtype="success",
            is_error=False,
            result='preface {"status":"wrong","message":"invalid"}',
            structured_output=None,
            session_id="dashscope_session",
            usage={},
        )

    schema = {
        "type": "object",
        "properties": {"status": {"const": "ok"}, "message": {"type": "string"}},
        "required": ["status", "message"],
        "additionalProperties": False,
    }
    harness = ClaudeSdkSkillHarness(
        cwd=str(tmp_path),
        provider="dashscope",
        model="deepseek-v4-flash",
        query_impl=fake_query,
    )

    result = harness.run_turn(
        prompt="test",
        developer_instructions="return the result",
        output_schema=schema,
        stage="direct",
    )

    assert result["ok"] is False
    assert result["final"] == {}
    assert "schema validation failed" in result["error"]


def test_dashscope_repairs_json_quoting_before_schema_validation(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: Any):
        yield _message(
            "ResultMessage",
            subtype="success",
            is_error=False,
            result='''```json\n{"status":"ok","message":"满足"百日地量"条件"}\n```''',
            structured_output=None,
            session_id="dashscope_session",
            usage={},
        )

    schema = {
        "type": "object",
        "properties": {"status": {"const": "ok"}, "message": {"type": "string"}},
        "required": ["status", "message"],
        "additionalProperties": False,
    }
    harness = ClaudeSdkSkillHarness(
        cwd=str(tmp_path),
        provider="dashscope",
        model="deepseek-v4-flash",
        query_impl=fake_query,
    )

    result = harness.run_turn(
        prompt="test",
        developer_instructions="return the result",
        output_schema=schema,
        stage="direct",
    )

    assert result["ok"] is True
    assert result["final"] == {"status": "ok", "message": '满足"百日地量"条件'}


def test_dashscope_recovers_structured_output_when_agent_returns_only_prose(tmp_path: Path) -> None:
    async def fake_query(*, prompt: str, options: Any):
        yield _message(
            "ResultMessage",
            subtype="success",
            is_error=False,
            result="需求已经分析完成，下一步应输出结构化结果。",
            structured_output=None,
            session_id="dashscope_session",
            usage={},
        )

    schema = {
        "type": "object",
        "properties": {"status": {"const": "ok"}, "message": {"type": "string"}},
        "required": ["status", "message"],
        "additionalProperties": False,
    }
    harness = ClaudeSdkSkillHarness(
        cwd=str(tmp_path),
        provider="dashscope",
        model="deepseek-v4-flash",
        query_impl=fake_query,
        structured_output_recovery=lambda prompt, answer, output_schema: {
            "status": "ok",
            "message": "recovered",
        },
    )

    result = harness.run_turn(
        prompt="test",
        developer_instructions="return the result",
        output_schema=schema,
        stage="direct",
    )

    assert result["ok"] is True
    assert result["final"] == {"status": "ok", "message": "recovered"}
    assert any(event.get("content") == "structured output recovery completed" for event in result["events"])


def test_claude_capabilities_keep_web_and_mcp_orthogonal_to_profile(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    async def fake_query(*, prompt: str, options: Any):
        captured["options"] = options
        yield _message(
            "ResultMessage",
            subtype="success",
            is_error=False,
            result='{"status":"ok","message":"configured"}',
            structured_output=None,
            session_id="dashscope_session",
            usage={},
        )

    capabilities = AgentCapabilityPolicy(
        web_search=WebSearchPolicy.LIVE,
        mcp_servers={
            "docs": {
                "command": "docs-mcp",
                "args": ["--stdio"],
                "allowed_tools": ["search"],
            }
        },
    )
    harness = ClaudeSdkSkillHarness(
        cwd=str(tmp_path),
        provider="dashscope",
        model="deepseek-v4-flash",
        effort="low",
        thinking="disabled",
        complexity_level="fastest",
        capabilities=capabilities,
        query_impl=fake_query,
    )

    result = harness.run_turn(
        prompt="test",
        developer_instructions="return the result",
        output_schema={
            "type": "object",
            "properties": {"status": {"const": "ok"}, "message": {"type": "string"}},
            "required": ["status", "message"],
            "additionalProperties": False,
        },
        stage="direct",
    )

    assert result["ok"] is True
    options = captured["options"]
    assert options.model == "deepseek-v4-flash"
    assert options.effort == "low"
    assert options.thinking == {"type": "disabled"}
    assert "WebSearch" in options.tools
    assert "WebSearch" not in options.disallowed_tools
    assert options.mcp_servers["docs"]["command"] == "docs-mcp"
    assert "mcp__docs__search" in options.allowed_tools
    assert options.env["ENABLE_TOOL_SEARCH"] == "true"


def test_disabled_external_capabilities_expose_neither_web_nor_mcp(monkeypatch) -> None:
    monkeypatch.setenv("CUSTOM_TOOL_AGENT_WEB_SEARCH", "disabled")
    monkeypatch.setenv("CUSTOM_TOOL_AGENT_MCP_CONFIG_PATH", "")

    policy = agent_capability_policy_from_env()
    harness = ClaudeSdkSkillHarness(capabilities=policy, query_impl=lambda **kwargs: None)
    values = harness._option_values(
        system_prompt="test",
        output_schema={"type": "object"},
        stage="requirement",
        cwd=Path(".").resolve(),
        readable_roots=(Path(".").resolve(),),
        allowed_write_paths=frozenset(),
        allow_writes=False,
        stderr_lines=[],
    )

    assert policy.web_search is WebSearchPolicy.DISABLED
    assert policy.mcp_servers == {}
    assert "WebSearch" not in values["tools"]
    assert "WebSearch" in values["disallowed_tools"]
    assert values["mcp_servers"] == {}


def test_codex_capabilities_disable_unselected_inherited_mcp_servers() -> None:
    capabilities = AgentCapabilityPolicy(
        web_search=WebSearchPolicy.PROVIDER_DEFAULT,
        mcp_servers={"github": {"url": "https://example.com/mcp", "allowed_tools": ["list_issues"]}},
    )
    harness = CodexSdkSkillHarness(capabilities=capabilities)

    overrides = harness._codex_config_overrides()

    assert 'web_search="cached"' in overrides
    assert "features.plugins=false" in overrides
    assert "features.apps=false" in overrides
    assert "features.skill_search=false" in overrides
    assert "mcp_servers.github.enabled=true" in overrides
    assert 'mcp_servers.github.url="https://example.com/mcp"' in overrides
    assert 'mcp_servers.github.enabled_tools=["list_issues"]' in overrides


def test_codex_spark_omits_unsupported_reasoning_summary() -> None:
    spark = CodexSdkSkillHarness(model="gpt-5.3-codex-spark")
    luna = CodexSdkSkillHarness(model="gpt-5.6-luna")

    assert spark._supports_reasoning_summary() is False
    assert luna._supports_reasoning_summary() is True


def test_capability_policy_refuses_inline_mcp_secrets() -> None:
    with pytest.raises(ValueError, match="process environment names"):
        AgentCapabilityPolicy(
            mcp_servers={
                "private": {
                    "command": "private-mcp",
                    "env": {"TOKEN": "must-not-enter-process-args"},
                    "allowed_tools": ["read"],
                }
            },
        )


def test_codex_explicit_capabilities_use_isolated_home_with_subscription_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    auth_file = codex_home / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    harness = CodexSdkSkillHarness(capabilities=AgentCapabilityPolicy())

    env, isolated_home = harness._sdk_runtime_env()
    try:
        isolated_path = Path(env["CODEX_HOME"])
        assert isolated_path != codex_home
        assert (isolated_path / "auth.json").is_symlink()
        assert (isolated_path / "auth.json").resolve() == auth_file.resolve()
        assert not (isolated_path / "config.toml").exists()
    finally:
        assert isolated_home is not None
        isolated_home.cleanup()


def test_codex_named_session_reuses_one_persistent_isolated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    auth_file = codex_home / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    session_root = tmp_path / "agent-sessions"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CUSTOM_TOOL_AGENT_SESSION_ROOT", str(session_root))
    harness = CodexSdkSkillHarness(capabilities=AgentCapabilityPolicy())

    first_env, first_temp = harness._sdk_runtime_env(session_id="coding-session-1")
    second_env, second_temp = harness._sdk_runtime_env(session_id="coding-session-1")

    assert first_temp is None
    assert second_temp is None
    assert first_env["CODEX_HOME"] == second_env["CODEX_HOME"]
    isolated_path = Path(first_env["CODEX_HOME"])
    assert isolated_path.is_dir()
    assert (isolated_path / "auth.json").resolve() == auth_file.resolve()
    assert not (isolated_path / "config.toml").exists()


def test_codex_auto_auth_uses_crs_key_and_writes_secret_free_provider_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setenv("CODEX_CRS_API_KEY", "crs-test-key")
    monkeypatch.setenv("CODEX_CRS_BASE_URL", "https://proxy.kingdomai.com/openai")
    monkeypatch.setenv("CODEX_CRS_MODEL", "gpt-5-codex")
    monkeypatch.setenv("CODEX_CRS_REASONING_EFFORT", "high")

    harness = CodexSdkSkillHarness(
        model="subscription-model",
        reasoning_effort="medium",
        capabilities=AgentCapabilityPolicy(),
    )
    env, isolated_home = harness._sdk_runtime_env()
    try:
        isolated_path = Path(env["CODEX_HOME"])
        config_file = isolated_path / "config.toml"
        config_text = config_file.read_text(encoding="utf-8")
        config = tomllib.loads(config_text)

        assert harness._resolved_auth_mode() == "crs_api_key"
        assert env["CODEX_CRS_API_KEY"] == "crs-test-key"
        assert config["model_provider"] == "crs"
        assert config["model"] == "gpt-5-codex"
        assert config["model_reasoning_effort"] == "high"
        assert config["disable_response_storage"] is True
        assert config["preferred_auth_method"] == "apikey"
        assert config["history"]["persistence"] == "none"
        assert config["model_providers"]["crs"] == {
            "name": "crs",
            "base_url": "https://proxy.kingdomai.com/openai",
            "wire_api": "responses",
            "env_key": "CODEX_CRS_API_KEY",
        }
        assert "crs-test-key" not in config_text
        assert not (isolated_path / "auth.json").exists()
    finally:
        assert isolated_home is not None
        isolated_home.cleanup()


def test_codex_explicit_subscription_wins_over_present_crs_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    auth_file = source_home / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setenv("CODEX_CRS_API_KEY", "crs-test-key")
    harness = CodexSdkSkillHarness(
        auth_mode="subscription",
        capabilities=AgentCapabilityPolicy(),
    )

    env, isolated_home = harness._sdk_runtime_env()
    try:
        isolated_path = Path(env["CODEX_HOME"])
        assert harness._resolved_auth_mode() == "subscription"
        assert "CODEX_CRS_API_KEY" not in env
        assert not (isolated_path / "config.toml").exists()
        assert (isolated_path / "auth.json").resolve() == auth_file.resolve()
    finally:
        assert isolated_home is not None
        isolated_home.cleanup()


def test_codex_explicit_crs_mode_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEX_CRS_API_KEY", raising=False)
    harness = CodexSdkSkillHarness(
        auth_mode="crs_api_key",
        capabilities=AgentCapabilityPolicy(),
    )

    with pytest.raises(RuntimeError, match="CODEX_CRS_API_KEY is required"):
        harness._sdk_runtime_env()


def test_codex_persistent_session_can_switch_from_crs_back_to_subscription(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    auth_file = source_home / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    session_root = tmp_path / "agent-sessions"
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setenv("CUSTOM_TOOL_AGENT_SESSION_ROOT", str(session_root))
    monkeypatch.setenv("CODEX_CRS_API_KEY", "crs-test-key")
    harness = CodexSdkSkillHarness(capabilities=AgentCapabilityPolicy())

    crs_env, crs_temp = harness._sdk_runtime_env(session_id="switchable-session")
    isolated_path = Path(crs_env["CODEX_HOME"])
    assert crs_temp is None
    assert (isolated_path / "config.toml").exists()
    assert not (isolated_path / "auth.json").exists()

    monkeypatch.delenv("CODEX_CRS_API_KEY")
    subscription_env, subscription_temp = harness._sdk_runtime_env(session_id="switchable-session")

    assert subscription_temp is None
    assert subscription_env["CODEX_HOME"] == crs_env["CODEX_HOME"]
    assert not (isolated_path / "config.toml").exists()
    assert (isolated_path / "auth.json").resolve() == auth_file.resolve()


def test_claude_coding_permission_policy_limits_writes_and_shell(tmp_path: Path) -> None:
    cwd = tmp_path.resolve()
    module = (cwd / "implementation/modules/main.py").resolve()
    allowed = frozenset({module})
    roots = (cwd,)

    assert ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Edit",
        tool_input={"file_path": str(module)},
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    ) == ""
    assert ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Write",
        tool_input={"file_path": str(cwd / "scratch/test_main.py")},
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    ) == ""
    assert "outside" in ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Write",
        tool_input={"file_path": str(cwd / "extra.py")},
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    )
    assert ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Bash",
        tool_input={"command": "python -m py_compile implementation/modules/main.py"},
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    ) == ""
    assert ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Bash",
        tool_input={
            "command": (
                "PYTHONPYCACHEPREFIX=scratch/pycache "
                f"{Path(sys.executable).resolve()} -m py_compile implementation/modules/main.py"
            )
        },
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    ) == ""
    assert ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Bash",
        tool_input={
            "command": (
                "PYTHONPATH=dev_runtime "
                f"{Path(sys.executable).resolve()} scratch/test_main.py"
            )
        },
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    ) == ""
    assert "only isolated" in ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Bash",
        tool_input={"command": "python -m pytest . && curl attacker"},
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    )
    assert "outside" in ClaudeSdkSkillHarness._tool_denial_reason(
        tool_name="Glob",
        tool_input={"pattern": "../**/*.env"},
        readable_roots=roots,
        allowed_write_paths=allowed,
        allow_bash=True,
        cwd=cwd,
    )


def test_documented_coding_commands_are_allowed_and_execute_in_isolated_workspace(tmp_path: Path) -> None:
    cwd = tmp_path.resolve()
    module = cwd / "implementation/modules/main.py"
    test_script = cwd / "scratch/test_main.py"
    sdk_stub = cwd / "dev_runtime/custom_tool_sdk.py"
    module.parent.mkdir(parents=True)
    test_script.parent.mkdir()
    sdk_stub.parent.mkdir()
    module.write_text("def run(inputs):\n    return {'ok': True}\n", encoding="utf-8")
    sdk_stub.write_text("VALUE = 1\n", encoding="utf-8")
    test_script.write_text(
        "from custom_tool_sdk import VALUE\nassert VALUE == 1\n",
        encoding="utf-8",
    )
    allowed = frozenset({module.resolve()})
    roots = (cwd,)
    commands = [
        (
            "PYTHONPYCACHEPREFIX=scratch/pycache "
            f"{Path(sys.executable).resolve()} -m py_compile implementation/modules/main.py"
        ),
        (
            "PYTHONPATH=dev_runtime "
            f"{Path(sys.executable).resolve()} scratch/test_main.py"
        ),
    ]

    for command in commands:
        assert ClaudeSdkSkillHarness._tool_denial_reason(
            tool_name="Bash",
            tool_input={"command": command},
            readable_roots=roots,
            allowed_write_paths=allowed,
            allow_bash=True,
            cwd=cwd,
        ) == ""
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_dashscope_credentials_are_bound_to_anthropic_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
    harness = ClaudeSdkSkillHarness(provider="dashscope", model="deepseek-v4-flash", query_impl=lambda **kwargs: None)

    provider_env = harness.provider_env()

    assert harness.base_url == DASHSCOPE_ANTHROPIC_BASE_URL
    assert provider_env["ANTHROPIC_BASE_URL"] == DASHSCOPE_ANTHROPIC_BASE_URL
    assert provider_env["ANTHROPIC_AUTH_TOKEN"] == "dashscope-secret"
    assert provider_env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert Path(provider_env["NODE_EXTRA_CA_CERTS"]).is_file()
    assert Path(provider_env["NODE_EXTRA_CA_CERTS"]).name == (
        "globalsign-root-r46.pem"
    )
    assert "DASHSCOPE_API_KEY" not in provider_env

    monkeypatch.setenv(
        "DASHSCOPE_ANTHROPIC_BASE_URL",
        "https://workspace.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
    )
    regional = ClaudeSdkSkillHarness(
        provider="dashscope",
        model="deepseek-v4-flash",
        query_impl=lambda **kwargs: None,
    )
    assert regional.base_url.startswith("https://workspace.ap-southeast-1.maas.aliyuncs.com")

    with pytest.raises(ValueError, match="may only use"):
        ClaudeSdkSkillHarness(
            provider="deepseek",
            base_url="https://example.com/anthropic",
            query_impl=lambda **kwargs: None,
        )
    with pytest.raises(ValueError, match="official Alibaba Cloud"):
        ClaudeSdkSkillHarness(
            provider="dashscope",
            base_url="https://example.com/apps/anthropic",
            query_impl=lambda **kwargs: None,
        )

    singapore = ClaudeSdkSkillHarness(
        provider="dashscope",
        base_url="https://workspace.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
        query_impl=lambda **kwargs: None,
    )
    assert singapore.base_url.endswith("/apps/anthropic")


def test_dashscope_ca_bundle_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    custom_bundle = tmp_path / "custom-ca.pem"
    custom_bundle.write_text("test certificate bundle", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret")
    monkeypatch.setenv(
        "DASHSCOPE_ANTHROPIC_CA_BUNDLE",
        str(custom_bundle),
    )

    provider_env = ClaudeSdkSkillHarness(
        provider="dashscope",
        model="deepseek-v4-flash",
        query_impl=lambda **kwargs: None,
    ).provider_env()

    assert provider_env["NODE_EXTRA_CA_CERTS"] == str(custom_bundle.resolve())


def test_deepseek_credentials_are_bound_to_official_anthropic_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    harness = ClaudeSdkSkillHarness(provider="deepseek", model="deepseek-v4-flash", query_impl=lambda **kwargs: None)

    provider_env = harness.provider_env()

    assert harness.base_url == DEEPSEEK_ANTHROPIC_BASE_URL
    assert provider_env["ANTHROPIC_BASE_URL"] == DEEPSEEK_ANTHROPIC_BASE_URL
    assert provider_env["ANTHROPIC_AUTH_TOKEN"] == "deepseek-secret"
    assert provider_env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert "DEEPSEEK_API_KEY" not in provider_env


def test_claude_delta_uses_existing_semantic_surface_pipeline() -> None:
    builder = LlmStreamBlockBuilder(run_id="claude_stream")
    payload = json.dumps({"questions": [{"id": "q1", "question": "范围？", "reason": "影响范围", "options": []}]})

    blocks = builder.event_to_blocks({
        "source": "claude",
        "type": "agent_delta",
        "content": payload,
        "metadata": {"stage": "design"},
    })

    assert any(block.get("block_id") == "design_questions" for block in blocks)


def test_only_structured_output_tool_json_is_forwarded_as_semantic_delta() -> None:
    state: dict[str, Any] = {}
    tool_start = _message(
        "StreamEvent",
        event={"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "t1", "name": "Bash"}},
    )
    tool_delta = _message(
        "StreamEvent",
        event={"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"command":"secret"}'}},
    )
    structured_start = _message(
        "StreamEvent",
        event={"type": "content_block_start", "index": 3, "content_block": {"type": "tool_use", "id": "t2", "name": "StructuredOutput"}},
    )
    structured_delta = _message(
        "StreamEvent",
        event={"type": "content_block_delta", "index": 3, "delta": {"type": "input_json_delta", "partial_json": '{"status":"review"}'}},
    )

    ClaudeSdkSkillHarness._normalize_message(tool_start, stage="design", stream_state=state)
    hidden, _ = ClaudeSdkSkillHarness._normalize_message(tool_delta, stage="design", stream_state=state)
    ClaudeSdkSkillHarness._normalize_message(structured_start, stage="design", stream_state=state)
    visible, _ = ClaudeSdkSkillHarness._normalize_message(structured_delta, stage="design", stream_state=state)

    assert hidden == []
    assert visible[0]["content"] == '{"status":"review"}'


def test_streamed_tool_call_is_not_repeated_by_assistant_message() -> None:
    state: dict[str, Any] = {}
    streamed = _message(
        "StreamEvent",
        event={
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "tool_1", "name": "Read"},
        },
    )
    assistant = _message(
        "AssistantMessage",
        content=[_message("ToolUseBlock", id="tool_1", name="Read")],
    )

    first, _ = ClaudeSdkSkillHarness._normalize_message(
        streamed,
        stage="coding",
        stream_state=state,
    )
    repeated, _ = ClaudeSdkSkillHarness._normalize_message(
        assistant,
        stage="coding",
        stream_state=state,
    )

    assert len(first) == 1
    assert repeated == []
