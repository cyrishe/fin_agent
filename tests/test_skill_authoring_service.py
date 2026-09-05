import json
import re
from typing import Any, Dict

import pytest

from src.services.skill_authoring_service import (
    SkillAuthoringError,
    SkillAuthoringService,
    SkillCapabilityDiscoveryService,
)
from src.services.skill_candidate_store_service import (
    DatabaseSkillCandidateStoreService,
    InMemorySkillCandidateStoreService,
    SkillCandidateConflictError,
    SkillCandidateNotFoundError,
)


class _BusinessCatalog:
    def discovery_snapshot(self) -> Dict[str, Any]:
        return {
            "revision": "business-r1",
            "entries": [
                {
                    "id": "stock-research",
                    "category": "equity-research",
                    "description": "对个股进行证据驱动的专业研究",
                    "allowed_tools": ["financial_news_search"],
                },
                {
                    "id": "market-overview",
                    "category": "market",
                    "description": "分析市场整体环境",
                    "allowed_tools": [],
                },
            ],
        }


class _ToolRegistry:
    def list_active_tools(self):
        return [
            {
                "tool_name": "stock.quote",
                "display_name": "股票行情",
                "purpose": "查询一个或多个股票的行情序列",
                "best_for": ["个股价格和交易轨迹"],
                "subject_tags": ["stock"],
                "side_effect_level": "read_only",
                "required_inputs": ["subject_codes"],
            },
            {
                "tool_name": "financial_news_search",
                "display_name": "金融新闻搜索",
                "purpose": "搜索公司相关新闻和催化剂",
                "best_for": ["公司事件"],
                "subject_tags": ["stock"],
                "side_effect_level": "read_only",
                "required_inputs": ["query"],
            },
        ]


class _Harness:
    provider_name = "codex"
    model = "test-model"

    def __init__(self) -> None:
        self.calls = []

    def available(self) -> bool:
        return True

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        is_revision = '"mode": "revise"' in kwargs["prompt"]
        suffix = "（修订）" if is_revision else ""
        return {
            "ok": True,
            "provider": "codex",
            "duration_ms": 123,
            "llm_usage": {"input_tokens": 10},
            "final": {
                "skill_markdown": (
                    "---\n"
                    "name: stock-analysis\n"
                    "description: 基于行情、新闻与专业研究方法分析单只股票。\n"
                    "---\n\n"
                    f"# 个股分析{suffix}\n\n"
                    "先确认分析对象，再收集证据并形成有边界的结论。\n"
                ),
                "control_patch": {
                    "tool_connections": [
                        {"tool_name": "stock.quote", "purpose": "获取行情证据"},
                        {"tool_name": "financial_news_search", "purpose": "补充公司事件"},
                        {"tool_name": "invented.tool", "purpose": "不应被接受"},
                    ],
                    "related_skills": [
                        {"skill_id": "stock-research", "purpose": "复用研究方法"},
                        {"skill_id": "invented-skill", "purpose": "不应被接受"},
                    ],
                    "workflow_steps": [
                        {
                            "id": "confirm-subject",
                            "title": "确认对象",
                            "instruction": "确认股票和分析时间范围。",
                            "uses": [],
                        },
                        {
                            "id": "collect-evidence",
                            "title": "收集证据",
                            "instruction": "查询行情并使用专业研究方法组织证据。",
                            "uses": [
                                "tool:stock.quote",
                                "tool:financial_news_search",
                                "skill:stock-research",
                                "tool:invented.tool",
                            ],
                        },
                        {
                            "id": "form-conclusion",
                            "title": "形成结论",
                            "instruction": "输出结论、风险和待验证问题。",
                            "uses": [],
                        },
                    ],
                },
                "change_summary": "生成个股分析候选" if not is_revision else "调整候选表达",
            },
        }


def _service(*, harness=None, store=None):
    discovery = SkillCapabilityDiscoveryService(
        business_catalog=_BusinessCatalog(),
        tool_registry=_ToolRegistry(),
    )
    return SkillAuthoringService(
        store=store or InMemorySkillCandidateStoreService(),
        discovery_service=discovery,
        agent_harness=harness or _Harness(),
    )


def test_discovery_uses_only_real_active_local_capabilities() -> None:
    discovery = SkillCapabilityDiscoveryService(
        business_catalog=_BusinessCatalog(),
        tool_registry=_ToolRegistry(),
    ).discover("做一个个股行情和新闻分析 Skill")

    assert discovery["business_revision"] == "business-r1"
    assert len(discovery["tool_revision"]) == 64
    assert discovery["skills"][0]["skill_id"] == "stock-research"
    assert {item["tool_name"] for item in discovery["tools"]} == {
        "stock.quote",
        "financial_news_search",
    }


def test_create_candidate_compiles_markdown_bindings_and_flowchart() -> None:
    harness = _Harness()
    service = _service(harness=harness)

    candidate = service.create_candidate(
        requirement="做一个个股分析 Skill，先确认对象，再看行情和新闻，最后给出风险。",
        owner_id="user-1",
    )

    assert re.fullmatch(r"stock-analysis-[a-f0-9]{8}", candidate["skill_id"])
    assert candidate["revision_no"] == 1
    assert candidate["active_revision_no"] == 0
    assert candidate["published"] is False
    assert f"name: {candidate['skill_id']}" in candidate["skill_markdown"]
    assert "allowed-tools:\n- mcp__finance__financial_news_search" in candidate["skill_markdown"]
    assert "mcp__finance__stock.quote" not in candidate["skill_markdown"]
    assert "invented.tool" not in candidate["skill_markdown"]
    assert candidate["control_manifest"]["tool_connections"] == [
        {
            "tool_name": "stock.quote",
            "purpose": "获取行情证据",
            "side_effect_level": "read_only",
            "runtime_name": "mcp__finance__stock.quote",
            "access": "core_agent_tool",
        },
        {
            "tool_name": "financial_news_search",
            "purpose": "补充公司事件",
            "side_effect_level": "read_only",
            "runtime_name": "mcp__finance__financial_news_search",
            "access": "supplemental_request",
        },
    ]
    assert candidate["control_manifest"]["related_skills"][0]["skill_id"] == "stock-research"
    assert "Tool: stock.quote" in candidate["flowchart"]["source"]
    assert "Skill: stock-research" in candidate["flowchart"]["source"]
    assert set(candidate["resolution_notes"]) == {
        "ignored unknown tool: invented.tool",
        "ignored unknown skill: invented-skill",
    }
    assert harness.calls[0]["stage"] == "skill_authoring"
    assert harness.calls[0]["output_schema"]["additionalProperties"] is False


def test_non_ascii_proposed_name_falls_back_to_a_valid_system_identity() -> None:
    proposed_name, description, body = SkillAuthoringService._parse_skill_markdown(
        "---\nname: 个股分析\ndescription: 分析单只股票。\n---\n\n# 个股分析\n\n执行分析。"
    )

    assert proposed_name == "custom-skill"
    assert description == "分析单只股票。"
    assert body.startswith("# 个股分析")


def test_revision_is_immutable_and_rejects_stale_base_before_second_agent_call() -> None:
    harness = _Harness()
    store = InMemorySkillCandidateStoreService()
    service = _service(harness=harness, store=store)
    first = service.create_candidate(
        requirement="做一个个股分析 Skill。",
        owner_id="user-1",
    )

    second = service.revise_candidate(
        skill_id=first["skill_id"],
        feedback="把风险反证放到结论前。",
        base_revision_no=1,
        owner_id="user-1",
    )

    assert second["skill_id"] == first["skill_id"]
    assert second["revision_no"] == 2
    assert second["base_revision_no"] == 1
    assert second["feedback"] == "把风险反证放到结论前。"
    assert "个股分析（修订）" in second["skill_markdown"]
    assert "个股分析（修订）" not in store.load_revision(
        first["skill_id"], 1, owner_id="user-1"
    )["skill_markdown"]

    with pytest.raises(SkillCandidateConflictError):
        service.revise_candidate(
            skill_id=first["skill_id"],
            feedback="从旧版本再次修改。",
            base_revision_no=1,
            owner_id="user-1",
        )
    assert len(harness.calls) == 2


def test_candidate_store_enforces_owner_scope() -> None:
    service = _service()
    candidate = service.create_candidate(
        requirement="做一个个股分析 Skill。",
        owner_id="user-1",
    )

    with pytest.raises(SkillCandidateNotFoundError):
        service.load_candidate(
            skill_id=candidate["skill_id"],
            revision_no=1,
            owner_id="user-2",
        )


class _CandidateDatabase:
    def __init__(self) -> None:
        self.artifact = None
        self.revisions = {}
        self.commits = 0
        self.rollbacks = 0

    def connect(self):
        return _CandidateConnection(self)


class _CandidateConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return _CandidateCursor(self.database)

    def commit(self):
        self.database.commits += 1

    def rollback(self):
        self.database.rollbacks += 1

    def close(self):
        return None


class _CandidateCursor:
    def __init__(self, database):
        self.database = database
        self._result = None
        self.lastrowid = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        self._result = None
        if normalized.startswith("insert into aiia_runtime_artifact ("):
            self.lastrowid = 41
            self.database.artifact = {
                "artifact_id": 41,
                "artifact_type": params[0],
                "name": params[1],
                "display_name": params[2],
                "description": params[3],
                "owner": params[4],
                "source_manifest_json": params[8],
                "current_revision_no": 0,
            }
            return
        if normalized.startswith("insert into aiia_runtime_artifact_revision"):
            self.database.revisions[int(params[1])] = {
                "artifact_id": int(params[0]),
                "revision_no": int(params[1]),
                "definition_json": params[2],
                "schema_json": params[3],
                "spec_json": params[4],
                "markdown_text": params[5],
                "content_hash": params[6],
                "change_summary": params[7],
                "created_by": params[8],
                "created_at": "2026-08-11T00:00:00+00:00",
            }
            return
        if normalized.startswith("select * from aiia_runtime_artifact where"):
            artifact = self.database.artifact
            self._result = (
                dict(artifact)
                if artifact
                and params[0] == artifact["artifact_type"]
                and params[1] == artifact["name"]
                and params[2] == artifact["owner"]
                else None
            )
            return
        if normalized.startswith("select * from aiia_runtime_artifact_revision where"):
            self._result = dict(self.database.revisions.get(int(params[1])) or {}) or None
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self._result


def test_database_candidate_keeps_active_pointer_empty_on_create() -> None:
    database = _CandidateDatabase()
    store = DatabaseSkillCandidateStoreService(connection_factory=database.connect)
    candidate = _service(store=InMemorySkillCandidateStoreService()).create_candidate(
        requirement="做一个个股分析 Skill。",
        owner_id="user-1",
    )

    persisted = store.create_candidate(candidate, owner_id="user-1")

    assert database.artifact["current_revision_no"] == 0
    assert json.loads(database.artifact["source_manifest_json"])["candidate_revision_no"] == 1
    assert persisted["revision_no"] == 1
    assert persisted["active_revision_no"] == 0
    assert persisted["published"] is False
    with pytest.raises(SkillCandidateNotFoundError):
        store.load_revision(candidate["skill_id"], 1, owner_id="user-2")


class _FailingHarness(_Harness):
    def run_turn(self, **kwargs):
        return {"ok": False, "error": "secret provider detail", "final": {}}


class _InvalidThenValidHarness(_Harness):
    def run_turn(self, **kwargs):
        if not self.calls:
            self.calls.append(kwargs)
            return {
                "ok": True,
                "provider": "codex",
                "duration_ms": 50,
                "final": {
                    "skill_markdown": (
                        "---\nname: stock-analysis\n"
                        "description: 分析单只股票。\n---\n"
                    ),
                    "control_patch": {
                        "tool_connections": [],
                        "related_skills": [],
                        "workflow_steps": [
                            {"id": "one", "title": "一步", "instruction": "执行一步。", "uses": []},
                            {"id": "two", "title": "二步", "instruction": "执行二步。", "uses": []},
                        ],
                    },
                    "change_summary": "incomplete",
                },
            }
        result = super().run_turn(**kwargs)
        result["duration_ms"] = 75
        return result


def test_semantically_invalid_markdown_gets_one_bounded_repair() -> None:
    harness = _InvalidThenValidHarness()
    service = _service(harness=harness)

    candidate = service.create_candidate(
        requirement="做一个个股分析 Skill。",
        owner_id="user-1",
    )

    assert len(harness.calls) == 2
    assert '"repair"' in harness.calls[1]["prompt"]
    assert candidate["authoring_evidence"]["attempt_count"] == 2
    assert candidate["authoring_evidence"]["duration_ms"] == 125
    assert "# 个股分析" in candidate["skill_markdown"]


def test_agent_failure_does_not_create_a_candidate_or_leak_provider_detail() -> None:
    store = InMemorySkillCandidateStoreService()
    service = _service(harness=_FailingHarness(), store=store)

    with pytest.raises(SkillAuthoringError) as exc_info:
        service.create_candidate(
            requirement="做一个个股分析 Skill。",
            owner_id="user-1",
        )

    assert exc_info.value.code == "skill_authoring_provider_failed"
    assert "secret" not in str(exc_info.value)
    assert store.list_candidates(owner_id="user-1") == []


def test_skill_candidate_api_contract_and_json_boundary(monkeypatch) -> None:
    from src.web import flask_app as web

    calls: Dict[str, Any] = {}
    candidate = {
        "skill_id": "stock-analysis-12345678",
        "display_name": "个股分析",
        "description": "desc",
        "revision_no": 1,
        "candidate_revision_no": 1,
        "active_revision_no": 0,
        "base_revision_no": 0,
        "skill_markdown": "# raw",
        "control_manifest": {
            "tool_connections": [],
            "related_skills": [],
            "workflow_steps": [],
        },
        "flowchart": {"format": "mermaid", "source": "flowchart TD"},
        "content_hash": "hash",
        "change_summary": "created",
        "created_at": "2026-08-11T00:00:00+00:00",
        "published": False,
    }

    class _ApiService:
        def create_candidate(self, *, requirement, owner_id):
            calls["create"] = {"requirement": requirement, "owner_id": owner_id}
            return candidate

        def list_candidates(self, *, owner_id, limit):
            calls["list"] = {"owner_id": owner_id, "limit": limit}
            return [candidate]

        def load_candidate(self, **kwargs):
            return candidate

        def revise_candidate(self, **kwargs):
            return {**candidate, "revision_no": 2, "candidate_revision_no": 2}

    monkeypatch.setattr(web, "skill_authoring_service", _ApiService())
    monkeypatch.setattr(
        web,
        "_resolve_current_guest_identity",
        lambda: {"user_id": "guest-1", "user_type": "guest", "session_token": "token"},
    )
    client = web.app.test_client()

    create = client.post(
        "/api/skill-hub/candidates",
        json={"requirement": "做一个个股分析 Skill。"},
    )
    listing = client.get("/api/skill-hub/candidates?limit=5")
    wrong_content_type = client.post(
        "/api/skill-hub/candidates",
        data={"requirement": "form request"},
    )

    assert create.status_code == 201
    assert create.get_json()["candidate"]["skill_id"] == candidate["skill_id"]
    assert calls["create"]["owner_id"] == "guest-1"
    assert listing.status_code == 200
    assert listing.get_json()["items"][0]["tool_count"] == 0
    assert calls["list"] == {"owner_id": "guest-1", "limit": 5}
    assert wrong_content_type.status_code == 400
    assert wrong_content_type.get_json()["code"] == "invalid_skill_authoring_request"
