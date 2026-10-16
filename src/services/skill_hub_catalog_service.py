from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import quote

from src.scenarios.financial_qa.business_skills import (
    FinanceBusinessSkillCatalog,
)
from src.services.asset_invocation_service import AssetInvocationService
from src.services.skill_studio_service import SkillStudioService
from src.services.skill_candidate_store_service import (
    DatabaseSkillCandidateStoreService,
    SkillCandidateConflictError,
    SkillCandidateNotFoundError,
    SkillCandidateStoreError,
)


def _trim(value: Any) -> str:
    return str(value or "").strip()


class SkillHubCatalogService:
    """Active execution catalog plus ownership-scoped inspection and editing."""

    def __init__(
        self,
        *,
        business_catalog: Optional[FinanceBusinessSkillCatalog] = None,
        legacy_skill_studio: Optional[SkillStudioService] = None,
        candidate_store: Any = None,
    ) -> None:
        self.business_catalog = business_catalog or FinanceBusinessSkillCatalog()
        self.legacy_skill_studio = legacy_skill_studio or SkillStudioService()
        self._candidate_store = candidate_store
        self._registry_injected = candidate_store is not None

    @property
    def candidate_store(self):
        if self._candidate_store is None:
            self._candidate_store = DatabaseSkillCandidateStoreService()
        return self._candidate_store

    def runtime_catalog(self, *, owner_ids: Iterable[str] = ()) -> FinanceBusinessSkillCatalog:
        owners = tuple(sorted({_trim(item) for item in owner_ids if _trim(item)}))
        # Anonymous/default startup is system-only. Authenticated requests (and
        # injected offline stores) include public and the caller's private Skills.
        if not owners and not self._registry_injected:
            return self.business_catalog
        records = self.candidate_store.list_available(owner_ids=(*owners, "system"))
        return self.business_catalog.with_active_skills(records)

    def catalog(self, *, owner_ids: Iterable[str] = (), is_admin: bool = False) -> Dict[str, Any]:
        owners = tuple(owner_ids)
        catalog = self.runtime_catalog(owner_ids=owners)
        business_snapshot = catalog.discovery_snapshot()
        items = [
            *self._business_method_items(business_snapshot, catalog=catalog, owner_ids=owners, is_admin=is_admin),
            *self._legacy_compiled_items(),
        ]
        revision_payload = {
            "business_revision": business_snapshot["revision"],
            "items": items,
        }
        revision = hashlib.sha256(
            json.dumps(
                revision_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "revision": revision,
            "business_revision": business_snapshot["revision"],
            "items": items,
        }

    def list_skills(self, *, owner_ids: Iterable[str] = ()) -> list[Dict[str, Any]]:
        return list(self.catalog(owner_ids=owner_ids)["items"])

    def _business_method_items(
        self,
        snapshot: Mapping[str, Any],
        *, catalog: FinanceBusinessSkillCatalog, owner_ids: Iterable[str] = (), is_admin: bool = False,
    ) -> list[Dict[str, Any]]:
        revision = _trim(snapshot.get("revision"))
        rows: list[Dict[str, Any]] = []
        for entry in snapshot.get("entries") or []:
            if not isinstance(entry, Mapping):
                continue
            skill_id = _trim(entry.get("id"))
            if not skill_id:
                continue
            detail = catalog.studio_detail(skill_id) or {}
            owner = _trim(detail.get("owner")) or "system"
            visibility = _trim(detail.get("visibility")) or "public"
            viewable = owner == "system" or owner in owner_ids
            editable = is_admin if owner == "system" else owner in owner_ids
            catalog_id = f"skill:business_method:{skill_id}"
            rows.append(
                {
                    "catalog_id": catalog_id,
                    "skill_name": skill_id,
                    "display_name": _trim(detail.get("display_name")) or skill_id,
                    "purpose": entry["description"],
                    "description": entry["description"],
                    "short_description": _trim(detail.get("short_description")),
                    "default_prompt": _trim(detail.get("default_prompt")) if viewable else "",
                    "category": entry.get("category") or "",
                    "skill_type": "business_method",
                    "invocation_mode": "finance_cc_preference",
                    "invocation_enabled": True,
                    "editable": editable,
                    "viewable": viewable,
                    "source": "finance_business_snapshot",
                    "workspace_url": (
                        f"/skills/studio/{quote(skill_id, safe='')}"
                        f"?catalog_id={quote(catalog_id, safe='')}"
                    ),
                    "snapshot_revision": revision,
                    "owner": owner,
                    "auth": visibility,
                    "scope": "system" if owner == "system" else visibility,
                    "active_revision_no": int(detail.get("active_revision_no") or 0),
                    "owned": owner != "system" and owner in owner_ids,
                    "availability": {
                        "lifecycle": "active",
                        "retrieval_mode": "retrievable",
                    },
                    "tool_mode": "runtime_policy",
                    "tools": list(entry.get("allowed_tools") or []) if viewable else [],
                    "example_count": 0,
                }
            )
        return rows

    def _legacy_compiled_items(self) -> list[Dict[str, Any]]:
        rows: list[Dict[str, Any]] = []
        for raw in self.legacy_skill_studio.list_compiled_skills():
            if _trim(raw.get("auth")) != "public":
                continue
            skill_name = _trim(raw.get("skill_name"))
            if not skill_name:
                continue
            availability = (
                raw.get("availability")
                if isinstance(raw.get("availability"), Mapping)
                else {}
            )
            purpose = _trim(raw.get("purpose") or raw.get("description"))
            rows.append(
                {
                    "catalog_id": f"skill:legacy_compiled:{skill_name}",
                    "skill_name": skill_name,
                    "display_name": _trim(raw.get("display_name")) or skill_name,
                    "purpose": purpose,
                    "description": purpose,
                    "category": "legacy",
                    "skill_type": "legacy_compiled",
                    "invocation_mode": "legacy_runner",
                    "invocation_enabled": (
                        AssetInvocationService.skill_config_is_invocable(raw)
                    ),
                    # The public Hub is discovery-only.  Legacy write APIs
                    # retain their existing compatibility surface but are not
                    # advertised as editable here.
                    "editable": False,
                    "source": "legacy_skill_bundle",
                    "workspace_url": (
                        f"/skills/studio/{quote(skill_name, safe='')}"
                        "?catalog_id="
                        f"{quote(f'skill:legacy_compiled:{skill_name}', safe='')}"
                    ),
                    "snapshot_revision": "",
                    "owner": _trim(raw.get("owner")) or "system",
                    "auth": "public",
                    "availability": dict(availability),
                    "tool_mode": _trim(raw.get("tool_mode")) or "strict",
                    "tools": [
                        _trim(item)
                        for item in raw.get("tools") or []
                        if _trim(item)
                    ],
                    "example_count": int(raw.get("example_count") or 0),
                }
            )
        return rows

    def detail(
        self,
        skill_name: str,
        *,
        catalog_id: str = "",
        owner_ids: Iterable[str] = (),
        is_admin: bool = False,
    ) -> Dict[str, Any] | None:
        """Return detail only from the caller's active authorized snapshot."""

        normalized_name = _trim(skill_name)
        normalized_catalog_id = _trim(catalog_id)
        owners = tuple(owner_ids)
        catalog = self.runtime_catalog(owner_ids=owners)
        matches = [
            item
            for item in [*self._business_method_items(catalog.discovery_snapshot(), catalog=catalog,
                owner_ids=owners, is_admin=is_admin), *self._legacy_compiled_items()]
            if _trim(item.get("skill_name")) == normalized_name
        ]
        if normalized_catalog_id:
            matches = [
                item
                for item in matches
                if _trim(item.get("catalog_id")) == normalized_catalog_id
            ]
        if not matches:
            return None
        item = next(
            (
                candidate
                for candidate in matches
                if candidate.get("skill_type") == "business_method"
            ),
            matches[0],
        )
        if item.get("skill_type") != "business_method":
            return {
                **item,
                "detail_available": False,
                "migration_note": (
                    "这是历史 compiled Skill。新 Studio 当前只展示其目录身份；"
                    "旧 bundle 编辑与执行仍保持隔离。"
                ),
            }

        if not item.get("viewable"):
            return {**item, "detail_available": False}
        detail = catalog.studio_detail(normalized_name)
        if detail is None:
            return None
        definition = {}
        if detail.get("active_revision_no"):
            record = self.candidate_store.load_revision(normalized_name, int(detail["active_revision_no"]), owner_id=detail["owner"])
            definition = {key: record.get(key) for key in
                ("control_manifest", "flowchart", "requirement", "change_summary", "authoring_evidence")}
        return {
            **item,
            **detail,
            "definition": definition,
            "detail_available": True,
            "editable": item["editable"],
            "candidate_supported": item["editable"],
            "publish_supported": False,
            "test_supported": False,
        }

    def load_business_reference(
        self,
        skill_name: str,
        reference_path: str,
        *,
        expected_revision: str = "",
        owner_ids: Iterable[str] = (),
    ) -> Dict[str, Any]:
        normalized_name = _trim(skill_name)
        owners = tuple(owner_ids)
        catalog = self.runtime_catalog(owner_ids=owners)
        detail = catalog.studio_detail(normalized_name)
        if detail is None or (detail["owner"] != "system" and detail["owner"] not in owners):
            return {
                "skill_id": normalized_name,
                "reference": _trim(reference_path),
                "error": "该 CC-native Skill 不存在或当前不可查看。",
            }
        return catalog.load_reference(
            normalized_name,
            reference_path,
            expected_revision=expected_revision,
        )

    def _editable_owner(self, skill_id: str, *, actor_id: str, is_admin: bool) -> str:
        if self.business_catalog.studio_detail(skill_id) is not None:
            if not is_admin:
                raise PermissionError("只有管理员可以修改系统 Skill。")
            return "system"
        # Owner-scoped lookup also covers private, not-yet-active candidates.
        self.candidate_store.load_latest(skill_id, owner_id=actor_id)
        return actor_id

    def definition(self, skill_id: str, *, actor_id: str, is_admin: bool = False) -> Dict[str, Any]:
        """Editor source, including pending revisions; never reconstructed from runtime text."""
        owner = self._editable_owner(skill_id, actor_id=actor_id, is_admin=is_admin)
        try:
            return self.candidate_store.load_latest(skill_id, owner_id=owner)
        except SkillCandidateNotFoundError:
            if owner != "system":
                raise
        detail = self.business_catalog.studio_detail(skill_id)
        references = {ref["path"]: self.business_catalog.load_reference(skill_id, ref["path"])["content"]
                      for ref in detail["references"]}
        return {"skill_id": skill_id, "owner_id": "system", "revision_no": 0,
            "candidate_revision_no": 0, "active_revision_no": 0,
            "skill_markdown": detail["skill_markdown"], "display_name": detail["display_name"],
            "description": detail["description"], "references": references,
            "control_manifest": {}, "flowchart": {}, "visibility": "public",
            "content_hash": hashlib.sha256(json.dumps({"markdown": detail["skill_markdown"],
                "references": references}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}

    def save_definition(self, skill_id: str, payload: Mapping[str, Any], *,
                        actor_id: str, is_admin: bool = False) -> Dict[str, Any]:
        current = self.definition(skill_id, actor_id=actor_id, is_admin=is_admin)
        expected = payload.get("expected_candidate_revision")
        if (type(expected) is not int or expected != current["candidate_revision_no"]
                or payload.get("expected_content_hash") != current["content_hash"]):
            raise SkillCandidateConflictError("方法已发生变化，请重新读取后修改。")
        for key in ("skill_markdown", "display_name"):
            if not isinstance(payload.get(key), str) or not payload[key].strip():
                raise ValueError(f"{key} 必须是非空文本。")
        try:
            references = DatabaseSkillCandidateStoreService._normalize_references(payload.get("references", current.get("references")))
        except SkillCandidateStoreError as exc:
            raise ValueError(str(exc)) from exc
        control = payload.get("control_manifest", current.get("control_manifest", {}))
        if not isinstance(control, dict):
            raise ValueError("control_manifest 必须是对象。")
        # These are the two machine-addressed composition lists used at runtime.
        for key, identity in (("related_skills", "skill_id"), ("tool_connections", "tool_name")):
            entries = control.get(key, [])
            if not isinstance(entries, list) or any(not isinstance(item, dict) or not isinstance(item.get(identity), str) for item in entries):
                raise ValueError(f"{key} 的引用格式不正确。")
        markdown = payload["skill_markdown"].replace("\r\n", "\n").strip()
        frontmatter = self.business_catalog._frontmatter(markdown)
        marker = markdown.find("\n---\n", 4)
        if (not isinstance(frontmatter, Mapping) or frontmatter.get("name") != skill_id
                or not _trim(frontmatter.get("description")) or marker < 0 or not markdown[marker + 5:].strip()):
            raise ValueError("SKILL.md 需要有效的 name、description 和正文；name 不能修改。")
        candidate = {**current, "revision_no": expected + 1, "base_revision_no": expected,
            "display_name": payload["display_name"].strip(), "skill_markdown": markdown,
            "description": _trim(frontmatter["description"]), "references": references,
            "control_manifest": control, "change_summary": _trim(payload.get("change_summary")) or "在方法库中编辑",
            "authoring_evidence": {**current.get("authoring_evidence", {}), "edited_by": actor_id},
            "content_hash": hashlib.sha256(json.dumps({"markdown": markdown,
                "references": references, "control": control, "display_name": payload["display_name"]},
                ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        try:
            self.business_catalog._execution_budget(frontmatter)
        except RuntimeError as exc:
            raise ValueError(str(exc)) from exc
        # Validate before creating a durable candidate, with no mutation of active content.
        self.business_catalog.with_active_skills([{**candidate, "active_revision_no": expected + 1}])
        if not expected:
            return self.candidate_store.create_candidate(candidate, owner_id=current["owner_id"])
        return self.candidate_store.save_revision(candidate, owner_id=current["owner_id"], expected_base_revision=expected)

    def activate_definition(self, skill_id: str, *, actor_id: str, is_admin: bool = False,
                            expected_candidate_revision: int, expected_active_revision: int) -> Dict[str, Any]:
        owner = self._editable_owner(skill_id, actor_id=actor_id, is_admin=is_admin)
        return self.activate_candidate(skill_id, owner_id=owner,
            expected_candidate_revision=expected_candidate_revision, expected_active_revision=expected_active_revision)

    def activate_candidate(self, skill_id: str, *, owner_id: str,
        expected_candidate_revision: int, expected_active_revision: int) -> Dict[str, Any]:
        candidate = self.candidate_store.load_latest(skill_id, owner_id=owner_id)
        if int(candidate["candidate_revision_no"]) != int(expected_candidate_revision):
            raise SkillCandidateConflictError("Skill candidate revision changed")
        # Validate the exact immutable revision before moving its active pointer.
        self.business_catalog.with_active_skills([{**candidate,
            "active_revision_no": int(expected_candidate_revision)}])
        return self.candidate_store.activate_candidate(skill_id, owner_id=owner_id,
            expected_candidate_revision=expected_candidate_revision,
            expected_active_revision=expected_active_revision)

    def set_visibility(self, skill_id: str, *, owner_id: str, visibility: str,
        expected_active_revision: int) -> Dict[str, Any]:
        return self.candidate_store.set_visibility(skill_id, owner_id=owner_id,
            visibility=visibility, expected_active_revision=expected_active_revision)
