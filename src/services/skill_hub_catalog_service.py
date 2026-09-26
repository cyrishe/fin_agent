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
)


def _trim(value: Any) -> str:
    return str(value or "").strip()


class SkillHubCatalogService:
    """One authorized active Skill view for Hub, explicit use, and Finance CC."""

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
        records = self.candidate_store.list_available(owner_ids=owners)
        return self.business_catalog.with_active_skills(records)

    def catalog(self, *, owner_ids: Iterable[str] = ()) -> Dict[str, Any]:
        owners = tuple(owner_ids)
        catalog = self.runtime_catalog(owner_ids=owners)
        business_snapshot = catalog.discovery_snapshot()
        items = [
            *self._business_method_items(business_snapshot, catalog=catalog, owner_ids=owners),
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
        *, catalog: FinanceBusinessSkillCatalog, owner_ids: Iterable[str] = (),
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
            catalog_id = f"skill:business_method:{skill_id}"
            rows.append(
                {
                    "catalog_id": catalog_id,
                    "skill_name": skill_id,
                    "display_name": _trim(detail.get("display_name")) or skill_id,
                    "purpose": entry["description"],
                    "description": entry["description"],
                    "short_description": _trim(detail.get("short_description")),
                    "default_prompt": _trim(detail.get("default_prompt")),
                    "category": entry.get("category") or "",
                    "skill_type": "business_method",
                    "invocation_mode": "finance_cc_preference",
                    "invocation_enabled": True,
                    "editable": False,
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
                    "tools": list(entry.get("allowed_tools") or []),
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
    ) -> Dict[str, Any] | None:
        """Return detail only from the caller's active authorized snapshot."""

        normalized_name = _trim(skill_name)
        normalized_catalog_id = _trim(catalog_id)
        owners = tuple(owner_ids)
        catalog = self.runtime_catalog(owner_ids=owners)
        matches = [
            item
            for item in [*self._business_method_items(catalog.discovery_snapshot(), catalog=catalog,
                owner_ids=owners), *self._legacy_compiled_items()]
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

        detail = catalog.studio_detail(normalized_name)
        if detail is None:
            return None
        return {
            **item,
            **detail,
            "detail_available": True,
            "editable": False,
            "candidate_supported": False,
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
        catalog = self.runtime_catalog(owner_ids=owner_ids)
        if catalog.studio_detail(normalized_name) is None:
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
