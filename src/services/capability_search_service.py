from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from src.services.active_tool_registry_service import ActiveToolRegistryService
from src.services.capability_embedding_service import CapabilityEmbeddingService
from src.services.custom_tool_service import CustomToolStoreService

if TYPE_CHECKING:
    from src.services.quant_research_capability_adapter_service import QuantResearchCapabilityAdapterService
    from src.services.skill_studio_service import SkillStudioService
    from src.services.tool_studio_service import ToolStudioService


class CapabilitySearchService:
    DEFAULT_SKILL_TOP_K = 12
    DEFAULT_TOOL_TOP_K = 16
    VALID_TOOL_SUBJECT_TAGS = {"stock", "fund", "bond", "industry", "plate", "index", "hot_event", "general"}

    def __init__(
        self,
        *,
        skill_studio_service: Optional["SkillStudioService"] = None,
        tool_studio_service: Optional["ToolStudioService"] = None,
        embedding_service: Optional[CapabilityEmbeddingService] = None,
        skills_root: str = "src/skills",
        tool_definitions_dir: str = "src/tools/definitions",
        tool_specs_dir: str = "src/tools/specs",
        tool_schemas_dir: str = "src/tools/schemas",
        quant_capability_adapter_service: Optional["QuantResearchCapabilityAdapterService"] = None,
        active_tool_registry_service: Optional[ActiveToolRegistryService] = None,
        custom_tool_store_service: Optional[CustomToolStoreService] = None,
        tool_catalog_service: Optional[Any] = None,
        skill_top_k: int = DEFAULT_SKILL_TOP_K,
        tool_top_k: int = DEFAULT_TOOL_TOP_K,
    ) -> None:
        self.skill_studio_service = skill_studio_service
        self.tool_studio_service = tool_studio_service
        self.tool_catalog_service = tool_catalog_service
        self.quant_capability_adapter_service = quant_capability_adapter_service
        self.embedding_service = embedding_service or CapabilityEmbeddingService()
        self.skills_root = Path(skills_root)
        self.tool_definitions_dir = Path(tool_definitions_dir)
        self.tool_specs_dir = Path(tool_specs_dir)
        self.tool_schemas_dir = Path(tool_schemas_dir)
        self.active_tool_registry_service = active_tool_registry_service or ActiveToolRegistryService(
            definitions_dir=tool_definitions_dir,
            specs_dir=tool_specs_dir,
            schemas_dir=tool_schemas_dir,
            tool_hub_path=str(Path(tool_definitions_dir).parent / "tool_hub.json"),
        )
        self.custom_tool_store_service = custom_tool_store_service or CustomToolStoreService()
        self.skill_top_k = max(1, int(skill_top_k or self.DEFAULT_SKILL_TOP_K))
        self.tool_top_k = max(1, int(tool_top_k or self.DEFAULT_TOOL_TOP_K))

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def build_skill_retrieval_fields(cls, skill_like: Dict[str, Any]) -> Dict[str, Any]:
        purpose = cls._trim(skill_like.get("purpose")) or cls._trim(skill_like.get("description"))
        best_for = [cls._trim(x) for x in (skill_like.get("best_for") or []) if cls._trim(x)]
        return {
            "purpose": purpose,
            "best_for": best_for,
        }

    @classmethod
    def build_skill_embedding_text(cls, skill_like: Dict[str, Any]) -> str:
        fields = cls.build_skill_retrieval_fields(skill_like)
        parts = [
            fields["purpose"],
            "\n".join(fields["best_for"]),
        ]
        text = "\n".join(part for part in parts if part)
        return text.strip()

    @classmethod
    def build_tool_retrieval_fields(cls, tool_like: Dict[str, Any]) -> Dict[str, Any]:
        purpose = cls._trim(tool_like.get("purpose")) or cls._trim(tool_like.get("description"))
        best_for = [cls._trim(x) for x in (tool_like.get("best_for") or []) if cls._trim(x)]
        return {
            "purpose": purpose,
            "best_for": best_for,
        }

    @classmethod
    def build_tool_embedding_text(cls, tool_like: Dict[str, Any]) -> str:
        fields = cls.build_tool_retrieval_fields(tool_like)
        parts = [
            fields["purpose"],
            "\n".join(fields["best_for"]),
        ]
        text = "\n".join(part for part in parts if part)
        return text.strip()

    def find_for_agent_runtime(
        self,
        *,
        query: str,
        tool_queries: Optional[List[str]] = None,
        work_context: Optional[Dict[str, Any]] = None,
        application_context: Optional[Dict[str, Any]] = None,
        tool_subject_tags: Optional[List[str]] = None,
        skill_top_k: Optional[int] = None,
        tool_top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        ctx = work_context if isinstance(work_context, dict) else {}
        app_ctx = application_context if isinstance(application_context, dict) else {}
        normalized_query = self._trim(query)
        resolved_skill_top_k = max(1, int(skill_top_k or self.skill_top_k))
        resolved_tool_top_k = max(1, int(tool_top_k or self.tool_top_k))
        normalized_tool_queries = [self._trim(item) for item in (tool_queries or []) if self._trim(item)]

        requested_subject_tags = self._normalize_tool_subject_tags(tool_subject_tags)
        custom_tool_owner_ids = [
            self._trim(item)
            for item in (ctx.get("_custom_tool_owner_ids") or [])
            if self._trim(item)
        ]
        skill_catalog = self._load_skill_catalog(app_ctx, ctx)
        raw_tool_catalog = self._load_tool_catalog(
            app_ctx,
            custom_tool_owner_ids=custom_tool_owner_ids,
        )
        subject_filter_applied = self._subject_filter_is_active(requested_subject_tags, raw_tool_catalog)
        tool_catalog = self._filter_tool_catalog_by_subject(raw_tool_catalog, requested_subject_tags)
        quant_capability_catalog = self._load_quant_capability_catalog(app_ctx)
        ranked_skills = self._rank_skills(normalized_query, skill_catalog, ctx)[:resolved_skill_top_k]
        ranked_tools = self._rank_tools(
            normalized_query,
            tool_catalog,
            tool_queries=normalized_tool_queries,
            top_k=resolved_tool_top_k,
        )[:resolved_tool_top_k]
        ranked_quant_capabilities = self._rank_quant_capabilities(
            normalized_query,
            quant_capability_catalog,
        )[:resolved_tool_top_k]

        return {
            "query": normalized_query,
            "retrieval_meta": {
                "mode": "embedding_with_fallback",
                "skill_top_k": resolved_skill_top_k,
                "tool_top_k": resolved_tool_top_k,
                "quant_capability_top_k": resolved_tool_top_k,
                "tool_query_count": len(normalized_tool_queries) if normalized_tool_queries else 1,
                "quant_capability_count": len(ranked_quant_capabilities),
                "tool_subject_tags": requested_subject_tags,
                "tool_subject_filter_applied": subject_filter_applied,
            },
            "skills": ranked_skills,
            "tools": ranked_tools,
            "quant_capabilities": ranked_quant_capabilities,
            "planner_skills": [self._to_planner_skill_candidate(item) for item in ranked_skills],
            "planner_tools": [self._to_planner_tool_candidate(item) for item in ranked_tools],
            "planner_quant_capabilities": [
                self._to_planner_quant_capability_candidate(item)
                for item in ranked_quant_capabilities
            ],
        }

    def has_tool_subject_tags(self, *, application_context: Optional[Dict[str, Any]] = None) -> bool:
        app_ctx = application_context if isinstance(application_context, dict) else {}
        return any(self._normalize_tool_subject_tags(item.get("subject_tags")) for item in self._load_tool_catalog(app_ctx))

    def warm_tool_embeddings(self, *, application_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        app_ctx = application_context if isinstance(application_context, dict) else {}
        tool_catalog = self._load_tool_catalog(app_ctx)
        documents = [self._build_tool_retrieval_document(item) for item in tool_catalog]
        warm = self.embedding_service.warm_texts(documents)
        return {
            **warm,
            "tool_count": len(tool_catalog),
            "tool_names": [self._trim(item.get("tool_name")) for item in tool_catalog if self._trim(item.get("tool_name"))],
        }

    def _load_skill_catalog(
        self,
        application_context: Dict[str, Any],
        work_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        # Skills are triggered through an explicit path, not automatic capability selection.
        # Keep the planner retrieval surface tool-first so a broad skill cannot shadow a
        # narrow data tool.
        return []

    def _load_quant_capability_catalog(self, application_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.quant_capability_adapter_service is None:
            return []
        execution_agent = application_context.get("execution_agent") if isinstance(application_context.get("execution_agent"), dict) else {}
        allowed_capabilities = {
            self._trim(item)
            for item in (execution_agent.get("quant_capabilities") or [])
            if self._trim(item)
        }
        try:
            result = self.quant_capability_adapter_service.list_candidates()
        except Exception:
            return []
        rows: List[Dict[str, Any]] = []
        for item in result.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            capability_id = self._trim(item.get("capability_id"))
            if not capability_id:
                continue
            if allowed_capabilities and capability_id not in allowed_capabilities:
                continue
            availability = self._normalize_availability(item)
            if availability["lifecycle"] != "active":
                continue
            if availability["visibility"] == "hidden":
                continue
            if availability["retrieval_mode"] != "retrievable":
                continue
            execution_policy = item.get("execution_policy") if isinstance(item.get("execution_policy"), dict) else {}
            if execution_policy.get("read_only_candidate") is not True:
                continue
            if execution_policy.get("direct_execution") is not False:
                continue
            rows.append(
                {
                    "capability_id": capability_id,
                    "version": self._trim(item.get("version")),
                    "display_name": self._trim(item.get("display_name")) or capability_id,
                    "capability_type": self._trim(item.get("capability_type")),
                    "purpose": self._trim(item.get("purpose")),
                    "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
                    "params_schema": dict(item.get("params_schema") or {}),
                    "data_contract": dict(item.get("data_contract") or {}),
                    "spec_refs": [
                        dict(ref)
                        for ref in (item.get("spec_refs") or [])
                        if isinstance(ref, dict)
                    ],
                    "evidence": dict(item.get("evidence") or {}),
                    "execution_policy": dict(execution_policy),
                    "availability": availability,
                }
            )
        return rows

    def _load_tool_catalog(
        self,
        application_context: Dict[str, Any],
        *,
        custom_tool_owner_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        execution_agent = application_context.get("execution_agent") if isinstance(application_context.get("execution_agent"), dict) else {}
        allowed_tools = {
            self._trim(item)
            for item in (execution_agent.get("tools") or [])
            if self._trim(item)
        }
        rows: List[Dict[str, Any]] = []
        for item in self._list_tools(custom_tool_owner_ids=custom_tool_owner_ids):
            tool_name = self._trim(item.get("tool_name"))
            if not tool_name:
                continue
            capabilities = [self._trim(x) for x in item.get("capabilities", []) if self._trim(x)]
            is_custom_tool = "custom_tool" in capabilities
            if allowed_tools and tool_name not in allowed_tools and not is_custom_tool:
                continue
            availability = self._normalize_availability(item)
            if availability["lifecycle"] != "active":
                continue
            if availability["visibility"] == "hidden":
                continue
            if availability["retrieval_mode"] != "retrievable":
                continue
            rows.append(
                {
                    "tool_name": tool_name,
                    "display_name": self._trim(item.get("display_name")) or tool_name,
                    "purpose": self._trim(item.get("purpose")),
                    "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
                    "description": self._trim(item.get("description")),
                    "availability": availability,
                    "capabilities": capabilities,
                    "subject_tags": self._normalize_tool_subject_tags(item.get("subject_tags")),
                    "tool_priority": self._normalize_tool_priority(item.get("tool_priority")),
                    "keywords": [self._trim(x) for x in item.get("keywords", []) if self._trim(x)],
                    "required_inputs": [self._trim(x) for x in item.get("required_inputs", []) if self._trim(x)],
                    "optional_inputs": [self._trim(x) for x in item.get("optional_inputs", []) if self._trim(x)],
                    "input_notes": [self._trim(x) for x in item.get("input_notes", []) if self._trim(x)],
                    "output_fields": [self._trim(x) for x in item.get("output_fields", []) if self._trim(x)],
                }
            )
        return rows

    def _filter_tool_catalog_by_subject(
        self,
        tool_catalog: List[Dict[str, Any]],
        subject_tags: List[str],
    ) -> List[Dict[str, Any]]:
        if not self._subject_filter_is_active(subject_tags, tool_catalog):
            return tool_catalog
        wanted = set(subject_tags)
        filtered = [
            item
            for item in tool_catalog
            if (
                wanted.intersection(self._normalize_tool_subject_tags(item.get("subject_tags")))
                or "custom_tool" in (item.get("capabilities") or [])
            )
        ]
        return filtered or tool_catalog

    def _subject_filter_is_active(self, subject_tags: List[str], tool_catalog: List[Dict[str, Any]]) -> bool:
        concrete_tags = [item for item in subject_tags if item != "general"]
        if not concrete_tags:
            return False
        return any(self._normalize_tool_subject_tags(item.get("subject_tags")) for item in tool_catalog)

    def _normalize_tool_subject_tags(self, value: Any) -> List[str]:
        result: List[str] = []
        for item in value or []:
            normalized = self._trim(item).lower()
            if normalized in self.VALID_TOOL_SUBJECT_TAGS and normalized not in result:
                result.append(normalized)
        return result

    def _normalize_tool_priority(self, value: Any) -> int:
        try:
            priority = int(value)
        except (TypeError, ValueError):
            return 1
        return priority if priority > 0 else 1

    def _normalize_availability(self, item: Dict[str, Any]) -> Dict[str, str]:
        availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
        lifecycle = self._trim(availability.get("lifecycle") or item.get("lifecycle") or "active").lower() or "active"
        retrieval_mode = self._trim(availability.get("retrieval_mode") or item.get("retrieval_mode") or "retrievable").lower() or "retrievable"
        visibility = self._trim(availability.get("visibility") or item.get("visibility") or "visible").lower() or "visible"
        if lifecycle not in {"active", "retired"}:
            lifecycle = "active"
        if retrieval_mode not in {"retrievable", "direct_only"}:
            retrieval_mode = "retrievable"
        if visibility not in {"visible", "hidden"}:
            visibility = "visible"
        return {
            "lifecycle": lifecycle,
            "retrieval_mode": retrieval_mode,
            "visibility": visibility,
        }

    def _rank_skills(
        self,
        text: str,
        skill_catalog: List[Dict[str, Any]],
        work_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        documents = [self._build_skill_retrieval_document(item) for item in skill_catalog]
        scores = self.embedding_service.score(query=text, documents=documents)
        active_skill = self._trim(
            work_context.get("thread_active_skill_canonical_name")
            or work_context.get("thread_active_skill_name")
            or work_context.get("active_skill_canonical_name")
            or work_context.get("active_skill_name")
        )
        ranked: List[tuple[float, Dict[str, Any]]] = []
        for item, score in zip(skill_catalog, scores):
            normalized_score = float(score or 0.0)
            if active_skill and self._trim(item.get("skill_name")) == active_skill and self._looks_like_active_skill_run_request(text):
                normalized_score += 0.08
            if normalized_score <= 0:
                continue
            ranked.append(
                (
                    normalized_score,
                    {
                        "skill_name": self._trim(item.get("skill_name")),
                        "purpose": self._trim(item.get("purpose")),
                        "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
                        "tool_mode": self._trim(item.get("tool_mode")) or "strict",
                        "tools": [self._trim(x) for x in item.get("tools", []) if self._trim(x)],
                        "default_max_steps": int(item.get("default_max_steps", 0) or 0),
                    },
                )
            )
        ranked.sort(key=lambda row: (-float(row[0] or 0.0), row[1].get("skill_name") or ""))
        return [item for _score, item in ranked]

    def _rank_quant_capabilities(
        self,
        text: str,
        capability_catalog: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        documents = [self._build_quant_capability_retrieval_document(item) for item in capability_catalog]
        scores = self.embedding_service.score(query=text, documents=documents)
        ranked: List[tuple[float, Dict[str, Any]]] = []
        for item, score in zip(capability_catalog, scores):
            normalized_score = float(score or 0.0)
            if normalized_score <= 0:
                continue
            ranked.append(
                (
                    normalized_score,
                    {
                        "capability_id": self._trim(item.get("capability_id")),
                        "version": self._trim(item.get("version")),
                        "display_name": self._trim(item.get("display_name")) or self._trim(item.get("capability_id")),
                        "capability_type": self._trim(item.get("capability_type")),
                        "purpose": self._trim(item.get("purpose")),
                        "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
                        "params_schema": dict(item.get("params_schema") or {}),
                        "data_contract": dict(item.get("data_contract") or {}),
                        "spec_refs": [
                            dict(ref)
                            for ref in (item.get("spec_refs") or [])
                            if isinstance(ref, dict)
                        ],
                        "evidence": dict(item.get("evidence") or {}),
                        "execution_policy": dict(item.get("execution_policy") or {}),
                    },
                )
            )
        ranked.sort(key=lambda row: (-float(row[0] or 0.0), row[1].get("capability_id") or ""))
        return [item for _score, item in ranked]

    def _rank_tools(
        self,
        text: str,
        tool_catalog: List[Dict[str, Any]],
        *,
        tool_queries: Optional[List[str]] = None,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        normalized_tool_queries = [self._trim(item) for item in (tool_queries or []) if self._trim(item)]
        if normalized_tool_queries:
            ranked_from_splits = self._rank_tools_for_split_queries(normalized_tool_queries, tool_catalog, top_k=top_k)
            if ranked_from_splits:
                return ranked_from_splits
        documents = [self._build_tool_retrieval_document(item) for item in tool_catalog]
        scores = self.embedding_service.score(query=text, documents=documents)
        ranked: List[tuple[float, Dict[str, Any]]] = []
        for item, score in zip(tool_catalog, scores):
            normalized_score = float(score or 0.0)
            if normalized_score <= 0:
                continue
            ranked.append(
                (
                    normalized_score,
                    {
                        "tool_name": self._trim(item.get("tool_name")),
                        "display_name": self._trim(item.get("display_name")) or self._trim(item.get("tool_name")),
                        "purpose": self._trim(item.get("purpose")) or self._trim(item.get("description")),
                        "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
                        "description": self._trim(item.get("description")),
                        "capabilities": [self._trim(x) for x in item.get("capabilities", []) if self._trim(x)],
                        "subject_tags": self._normalize_tool_subject_tags(item.get("subject_tags")),
                        "tool_priority": self._normalize_tool_priority(item.get("tool_priority")),
                        "keywords": [self._trim(x) for x in item.get("keywords", []) if self._trim(x)],
                        "required_inputs": [self._trim(x) for x in item.get("required_inputs", []) if self._trim(x)],
                        "optional_inputs": [self._trim(x) for x in item.get("optional_inputs", []) if self._trim(x)],
                        "input_notes": [self._trim(x) for x in item.get("input_notes", []) if self._trim(x)],
                        "output_fields": [self._trim(x) for x in item.get("output_fields", []) if self._trim(x)],
                    },
                )
            )
        ranked.sort(key=lambda row: (-float(row[0] or 0.0), int(row[1].get("tool_priority") or 1), row[1].get("tool_name") or ""))
        return [item for _score, item in ranked]

    def _rank_tools_for_split_queries(
        self,
        tool_queries: List[str],
        tool_catalog: List[Dict[str, Any]],
        *,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not tool_catalog:
            return []
        documents = [self._build_tool_retrieval_document(item) for item in tool_catalog]
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()
        max_tools = max(1, int(top_k or self.tool_top_k))
        per_query_top_k = min(3, max_tools)
        for query in tool_queries:
            scores = self.embedding_service.score(query=query, documents=documents)
            ranked: List[Tuple[float, Dict[str, Any]]] = []
            for item, score in zip(tool_catalog, scores):
                normalized_score = float(score or 0.0)
                if normalized_score <= 0:
                    continue
                ranked.append((normalized_score, self._build_ranked_tool_item(item)))
            ranked.sort(key=lambda row: (-float(row[0] or 0.0), int(row[1].get("tool_priority") or 1), row[1].get("tool_name") or ""))
            for _score, item in ranked[:per_query_top_k]:
                tool_name = self._trim(item.get("tool_name"))
                if not tool_name or tool_name in seen:
                    continue
                seen.add(tool_name)
                merged.append(item)
                if len(merged) >= max_tools:
                    return merged
        return merged

    def _build_ranked_tool_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tool_name": self._trim(item.get("tool_name")),
            "display_name": self._trim(item.get("display_name")) or self._trim(item.get("tool_name")),
            "purpose": self._trim(item.get("purpose")) or self._trim(item.get("description")),
            "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
            "description": self._trim(item.get("description")),
            "capabilities": [self._trim(x) for x in item.get("capabilities", []) if self._trim(x)],
            "subject_tags": self._normalize_tool_subject_tags(item.get("subject_tags")),
            "tool_priority": self._normalize_tool_priority(item.get("tool_priority")),
            "keywords": [self._trim(x) for x in item.get("keywords", []) if self._trim(x)],
            "required_inputs": [self._trim(x) for x in item.get("required_inputs", []) if self._trim(x)],
            "optional_inputs": [self._trim(x) for x in item.get("optional_inputs", []) if self._trim(x)],
            "input_notes": [self._trim(x) for x in item.get("input_notes", []) if self._trim(x)],
            "output_fields": [self._trim(x) for x in item.get("output_fields", []) if self._trim(x)],
        }

    def _build_skill_retrieval_document(self, item: Dict[str, Any]) -> str:
        text = self.build_skill_embedding_text(item)
        return text or self._trim(item.get("skill_name"))

    def _build_tool_retrieval_document(self, item: Dict[str, Any]) -> str:
        text = self.build_tool_embedding_text(item)
        return text or self._trim(item.get("tool_name"))

    def _build_quant_capability_retrieval_document(self, item: Dict[str, Any]) -> str:
        parts = [
            self._trim(item.get("purpose")),
            "\n".join(self._trim(x) for x in item.get("best_for", []) if self._trim(x)),
            self._trim(item.get("display_name")),
            self._trim(item.get("capability_id")),
        ]
        return "\n".join(part for part in parts if part).strip()

    def _looks_like_active_skill_run_request(self, text: str) -> bool:
        normalized = self._trim(text).lower()
        if not normalized:
            return False
        return any(keyword in normalized for keyword in ["运行", "执行", "跑一下", "run", "当前这个", "刚才那个", "这个 skill", "这个技能"])

    def _to_planner_skill_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "skill_name": self._trim(item.get("skill_name")),
            "purpose": self._trim(item.get("purpose")),
            "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
        }

    def _to_planner_tool_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tool_name": self._trim(item.get("tool_name")),
            "display_name": self._trim(item.get("display_name")) or self._trim(item.get("tool_name")),
            "purpose": self._trim(item.get("purpose")) or self._trim(item.get("description")),
            "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
            "required_inputs": [self._trim(x) for x in item.get("required_inputs", []) if self._trim(x)],
            "optional_inputs": [self._trim(x) for x in item.get("optional_inputs", []) if self._trim(x)],
            "input_notes": [self._trim(x) for x in item.get("input_notes", []) if self._trim(x)],
            "output_fields": [self._trim(x) for x in item.get("output_fields", []) if self._trim(x)],
            "subject_tags": self._normalize_tool_subject_tags(item.get("subject_tags")),
            "tool_priority": self._normalize_tool_priority(item.get("tool_priority")),
        }

    def _to_planner_quant_capability_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "capability_id": self._trim(item.get("capability_id")),
            "version": self._trim(item.get("version")),
            "display_name": self._trim(item.get("display_name")) or self._trim(item.get("capability_id")),
            "capability_type": self._trim(item.get("capability_type")),
            "purpose": self._trim(item.get("purpose")),
            "best_for": [self._trim(x) for x in item.get("best_for", []) if self._trim(x)],
            "params_schema": dict(item.get("params_schema") or {}),
            "data_contract": dict(item.get("data_contract") or {}),
            "spec_refs": [
                dict(ref)
                for ref in (item.get("spec_refs") or [])
                if isinstance(ref, dict)
            ],
            "execution_policy": {
                "mode": self._trim((item.get("execution_policy") or {}).get("mode")),
                "read_only_candidate": (item.get("execution_policy") or {}).get("read_only_candidate") is True,
                "direct_execution": (item.get("execution_policy") or {}).get("direct_execution") is True,
            },
        }

    def _list_skills(self) -> List[Dict[str, Any]]:
        if self.skill_studio_service is not None:
            return self.skill_studio_service.list_skills()
        rows: List[Dict[str, Any]] = []
        if not self.skills_root.exists():
            return rows
        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            config_path = skill_dir / "skill.json"
            config_obj: Dict[str, Any] = {}
            if config_path.exists():
                try:
                    config_obj = json.loads(config_path.read_text(encoding="utf-8"))
                except Exception:
                    config_obj = {}
            tool_policy = config_obj.get("tool_policy") if isinstance(config_obj.get("tool_policy"), dict) else {}
            rows.append(
                {
                    "skill_name": skill_dir.name,
                    "purpose": self._trim(config_obj.get("purpose")) or self._trim(config_obj.get("description")),
                    "best_for": [self._trim(x) for x in (config_obj.get("best_for") or []) if self._trim(x)],
                    "skill_body": self._trim(config_obj.get("skill_body")) or self._trim(config_obj.get("execution_process")),
                    "availability": self._normalize_availability(config_obj),
                    "tool_mode": self._trim(tool_policy.get("mode")) or "strict",
                    "tools": [self._trim(x) for x in (config_obj.get("tools") or []) if self._trim(x)],
                    "default_max_steps": int(config_obj.get("default_max_steps", 0) or 0),
                }
            )
        return rows

    def _list_tools(self, *, custom_tool_owner_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if self.tool_studio_service is not None:
            rows = self.tool_studio_service.list_tools()
        elif self.tool_catalog_service is not None:
            rows = self.tool_catalog_service.list_active_tools()
        else:
            rows = self.active_tool_registry_service.list_active_tools()
        if not custom_tool_owner_ids:
            return rows
        known_names = {self._trim(item.get("tool_name")) for item in rows if self._trim(item.get("tool_name"))}
        try:
            manifests = self.custom_tool_store_service.list_tools(owner_ids=custom_tool_owner_ids)
        except Exception:
            return rows
        for manifest in manifests:
            tool_name = self._trim(manifest.get("tool_name"))
            if not tool_name or tool_name in known_names:
                continue
            try:
                bundle = self.custom_tool_store_service.load(tool_name)
            except Exception:
                continue
            input_schema = bundle.get("input_schema") if isinstance(bundle.get("input_schema"), dict) else {}
            output_schema = bundle.get("output_schema") if isinstance(bundle.get("output_schema"), dict) else {}
            properties = input_schema.get("properties") if isinstance(input_schema.get("properties"), dict) else {}
            output_properties = output_schema.get("properties") if isinstance(output_schema.get("properties"), dict) else {}
            required_inputs = [self._trim(item) for item in (input_schema.get("required") or []) if self._trim(item)]
            optional_inputs = [self._trim(name) for name in properties.keys() if self._trim(name) and self._trim(name) not in required_inputs]
            input_notes = [
                f"{self._trim(name)}: {self._trim(schema.get('description'))}"
                for name, schema in properties.items()
                if self._trim(name) and isinstance(schema, dict) and self._trim(schema.get("description"))
            ]
            capabilities = [self._trim(item) for item in (manifest.get("capabilities") or []) if self._trim(item)]
            if "custom_tool" not in capabilities:
                capabilities.append("custom_tool")
            rows.append(
                {
                    "tool_name": tool_name,
                    "display_name": self._trim(manifest.get("display_name")) or tool_name,
                    "purpose": self._trim(manifest.get("description")),
                    "description": self._trim(manifest.get("description")),
                    "best_for": [self._trim(item) for item in (manifest.get("best_for") or []) if self._trim(item)],
                    "availability": {
                        "lifecycle": "active",
                        "retrieval_mode": "retrievable",
                        "visibility": "visible",
                    },
                    "capabilities": capabilities,
                    "subject_tags": [self._trim(item) for item in (manifest.get("subject_tags") or []) if self._trim(item)],
                    "keywords": [self._trim(item) for item in (manifest.get("keywords") or []) if self._trim(item)],
                    "required_inputs": required_inputs,
                    "optional_inputs": optional_inputs,
                    "input_notes": input_notes,
                    "output_fields": [f"data.{self._trim(name)}" for name in output_properties.keys() if self._trim(name)],
                    "tool_priority": 1,
                }
            )
            known_names.add(tool_name)
        return rows

    def _build_tool_output_fields(self, spec: Dict[str, Any], schema: Dict[str, Any]) -> List[str]:
        seen: List[str] = []
        output_guidance = spec.get("output_guidance") if isinstance(spec.get("output_guidance"), dict) else {}
        for field in output_guidance.get("high_value_for_reasoning") or []:
            normalized = self._trim(field)
            if normalized and normalized not in seen:
                seen.append(normalized)
        for field in output_guidance.get("high_value_for_render") or []:
            normalized = self._trim(field)
            if normalized and normalized not in seen:
                seen.append(normalized)
        for field in self._extract_schema_output_fields(schema):
            normalized = self._trim(field)
            if normalized and normalized not in seen:
                seen.append(normalized)
        return seen[:12]

    def _extract_schema_output_fields(self, schema: Dict[str, Any]) -> List[str]:
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        data_schema = properties.get("data") if isinstance(properties.get("data"), dict) else {}
        data_type = self._trim(data_schema.get("type")).lower()
        if data_type == "array":
            item_schema = data_schema.get("items") if isinstance(data_schema.get("items"), dict) else {}
            item_properties = item_schema.get("properties") if isinstance(item_schema.get("properties"), dict) else {}
            return [f"data[].{self._trim(name)}" for name in item_properties.keys() if self._trim(name)]
        if data_type == "object":
            data_properties = data_schema.get("properties") if isinstance(data_schema.get("properties"), dict) else {}
            return [f"data.{self._trim(name)}" for name in data_properties.keys() if self._trim(name)]
        return []

    def _load_tool_hub_map(self) -> Dict[str, Dict[str, Any]]:
        hub_path = self.tool_definitions_dir.parent / "tool_hub.json"
        if not hub_path.exists():
            return {}
        try:
            payload = json.loads(hub_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        rows = payload.get("tools") if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        result: Dict[str, Dict[str, Any]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = self._trim(item.get("name"))
            if name:
                result[name] = item
        return result
