from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.skill_runtime.availability import legacy_skill_is_publicly_invocable
from src.services.attachment_service import AttachmentService
from src.services.file_io_tool_service import FileIoToolService
from src.services.invocation_input_resolver_service import (
    InvocationInputError,
    InvocationInputResolverService,
)
from src.services.stock_identity_resolver_service import StockIdentityResolverService
from src.utils.ai_service import chat_qwen_flash_json

if TYPE_CHECKING:
    from src.services.custom_tool_service import CustomToolStoreService


class AssetInvocationError(ValueError):
    pass


class AssetInvocationService:
    """Resolve an explicit `$asset` selection into a small executable plan."""

    _INVOCATION_RE = re.compile(
        r"(?<!\S)\$(?:(tool|skill):)?"
        r"(?=[A-Za-z0-9_.\-\u4e00-\u9fff]*[A-Za-z_\u4e00-\u9fff])"
        r"([A-Za-z0-9_.\-\u4e00-\u9fff]+)"
        r"(?=\s|$|[，,。；;！？!?])",
        flags=re.IGNORECASE,
    )
    _ASSET_REF_RE = re.compile(
        r"^(tool|skill):([A-Za-z0-9_.\-\u4e00-\u9fff]+)$",
        flags=re.IGNORECASE,
    )
    _NAMED_INVOCATION_RE = re.compile(
        r"^\s*(?:请)?(?:使用|用|调用|运行(?:一下)?|执行(?:一下)?)\s*"
        r"(?:(?:当前|本|我们)?(?:系统|平台)(?:中|内|里)(?:的)?\s*)?"
        r"[「『“\"'`]?([A-Za-z0-9_.\-\u4e00-\u9fff]+)[」』”\"'`]?\s*"
        r"(工具|tool|skill)(?=\s|来|跑|处理|分析|查询|计算|算|帮|对|$)",
        flags=re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        custom_tool_store: Optional["CustomToolStoreService"] = None,
        attachment_service: Optional[AttachmentService] = None,
        file_io_service: Optional[FileIoToolService] = None,
        input_resolver: Optional[InvocationInputResolverService] = None,
        tool_definitions_dir: str = "src/tools/definitions",
        skills_root: str = "src/skills",
        llm_chat: Optional[Callable[..., Any]] = None,
        stock_identity_resolver: Optional[StockIdentityResolverService] = None,
    ) -> None:
        if custom_tool_store is None:
            from src.services.custom_tool_service import CustomToolStoreService

            custom_tool_store = CustomToolStoreService()
        self.custom_tool_store = custom_tool_store
        self.attachment_service = attachment_service or AttachmentService()
        self.file_io_service = file_io_service or FileIoToolService()
        self.input_resolver = input_resolver or InvocationInputResolverService(
            attachment_service=self.attachment_service,
            file_io_service=self.file_io_service,
        )
        self.tool_definitions_dir = Path(tool_definitions_dir)
        self.skills_root = Path(skills_root)
        self.llm_chat = llm_chat or chat_qwen_flash_json
        self.stock_identity_resolver = stock_identity_resolver or StockIdentityResolverService()
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _normalize_lookup_text(cls, value: Any) -> str:
        normalized = unicodedata.normalize("NFKC", cls._trim(value)).lower()
        return re.sub(r"\s+", " ", normalized)

    @classmethod
    def _parse_asset_ref(cls, value: Any) -> Dict[str, str]:
        match = cls._ASSET_REF_RE.match(cls._trim(value))
        if not match:
            return {}
        return {
            "kind": cls._trim(match.group(1)).lower(),
            "name": cls._trim(match.group(2)),
        }

    def _selected_reference(self, selected_asset: Mapping[str, Any] | None) -> Dict[str, str]:
        selected = dict(selected_asset or {})
        ref_target = self._parse_asset_ref(selected.get("ref"))
        kind = self._trim(selected.get("kind")).lower()
        name = self._trim(selected.get("name"))
        if kind and kind not in {"tool", "skill"}:
            raise AssetInvocationError("所选资产类型无效，请重新选择。")
        if ref_target:
            if kind and kind != ref_target["kind"]:
                raise AssetInvocationError("所选资产信息不一致，请重新选择。")
            if name and self._normalize_lookup_text(name) != self._normalize_lookup_text(ref_target["name"]):
                raise AssetInvocationError("所选资产信息不一致，请重新选择。")
            return ref_target
        if self._trim(selected.get("ref")):
            raise AssetInvocationError("所选资产引用无效，请重新选择。")
        if not name:
            return {}
        return {"kind": kind, "name": name}

    def _text_reference(self, text: str) -> Dict[str, Any]:
        raw = str(text or "")
        dollar_match = self._INVOCATION_RE.search(raw)
        if dollar_match:
            return {
                "kind": self._trim(dollar_match.group(1)).lower(),
                "name": self._trim(dollar_match.group(2)),
                "start": int(dollar_match.start()),
                "end": int(dollar_match.end()),
                "syntax": "dollar",
            }
        named_match = self._NAMED_INVOCATION_RE.match(raw)
        if not named_match:
            return {}
        raw_kind = self._trim(named_match.group(2)).lower()
        return {
            "kind": "skill" if raw_kind == "skill" else "tool",
            "name": self._trim(named_match.group(1)),
            "start": int(named_match.start()),
            "end": int(named_match.end()),
            "syntax": "named",
        }

    def detect_target(
        self,
        *,
        text: str,
        selected_asset: Mapping[str, Any] | None = None,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = False,
    ) -> Dict[str, str]:
        resolution = self._resolve_invocation_target(
            text=text,
            selected_asset=selected_asset,
            owner_ids=owner_ids,
            allow_inactive=allow_inactive,
        )
        if resolution.get("status") == "none":
            return {}
        if resolution.get("status") != "resolved":
            raise AssetInvocationError(self._trim(resolution.get("message")) or "请选择要调用的 Tool 或 Skill。")
        return dict(resolution["target"])

    def has_explicit_invocation(
        self,
        *,
        text: str,
        selected_asset: Mapping[str, Any] | None = None,
    ) -> bool:
        selected = dict(selected_asset or {})
        return bool(
            self._trim(selected.get("ref"))
            or self._trim(selected.get("name"))
            or self._INVOCATION_RE.search(str(text or ""))
            or self._NAMED_INVOCATION_RE.match(str(text or ""))
        )

    def strip_invocation_prefix(self, text: str, *, target_name: str) -> str:
        raw = str(text or "").strip()
        reference = self._text_reference(raw)
        if reference:
            start = int(reference.get("start") or 0)
            end = int(reference.get("end") or 0)
            prefix = raw[:start].strip()
            if prefix in {"请用", "请使用", "请调用", "用", "使用", "调用"}:
                start = 0
            remainder = f"{raw[:start]} {raw[end:]}".strip()
            return remainder.lstrip("，,。；;：: ")
        return raw

    @classmethod
    def _tool_definition_is_invocable(cls, definition: Mapping[str, Any]) -> bool:
        availability = definition.get("availability") if isinstance(definition.get("availability"), Mapping) else {}
        status = cls._trim(definition.get("status")).lower() or "active"
        lifecycle = cls._trim(availability.get("lifecycle")).lower() or "active"
        visibility = cls._trim(availability.get("visibility")).lower() or "visible"
        profiles = definition.get("profiles") if isinstance(definition.get("profiles"), Mapping) else {}
        real_profile = profiles.get("real") if isinstance(profiles.get("real"), Mapping) else {}
        return (
            status == "active"
            and lifecycle == "active"
            and visibility != "hidden"
            and real_profile.get("enabled") is not False
        )

    @classmethod
    def skill_config_is_invocable(cls, config: Mapping[str, Any]) -> bool:
        """Return the canonical public legacy-Skill execution predicate."""

        return legacy_skill_is_publicly_invocable(config)

    @classmethod
    def _asset_ref(cls, kind: Any, name: Any) -> str:
        return f"{cls._trim(kind).lower()}:{cls._trim(name)}"

    def _asset_from_contract(
        self,
        contract: Mapping[str, Any],
        *,
        keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        kind = self._trim(contract.get("kind")).lower()
        name = self._trim(contract.get("name"))
        display_name = self._trim(contract.get("display_name")) or name
        summary = self._trim(contract.get("description")) or f"{display_name}（{kind or 'asset'}）"
        fields = self.guidance(contract).get("fields") or []
        aliases = [
            self._trim(item)
            for item in (contract.get("aliases") or [])
            if self._trim(item)
        ]
        tags = [
            self._trim(item)
            for item in (contract.get("tags") or [])
            if self._trim(item)
        ]
        finance_tool_profile = (
            dict(contract.get("finance_tool_profile") or {})
            if isinstance(contract.get("finance_tool_profile"), Mapping)
            else {}
        )
        search_parts = [
            name,
            display_name,
            summary,
            *aliases,
            *tags,
            *[
                self._trim(finance_tool_profile.get(key))
                for key in (
                    "family",
                    "execution_shape",
                    "output_semantic",
                    "summary",
                )
                if self._trim(finance_tool_profile.get(key))
            ],
            *[self._trim(item) for item in (keywords or []) if self._trim(item)],
        ]
        asset = {
            "ref": self._asset_ref(kind, name),
            "kind": kind,
            "name": name,
            "display_name": display_name,
            "summary": summary,
            "invocation": f"${name}",
            "input_fields": [dict(item) for item in fields if isinstance(item, Mapping)],
            "aliases": list(dict.fromkeys(aliases)),
            "tags": list(dict.fromkeys(tags)),
            "custom_tool": bool(contract.get("custom_tool")),
            "version": self._trim(contract.get("version")) or "v1",
            "revision": int(contract.get("revision") or 0),
            "_search_text": "\n".join(item for item in search_parts if item),
        }
        if finance_tool_profile:
            asset["finance_tool_profile"] = finance_tool_profile
        if "editable" in contract:
            asset["editable"] = bool(contract.get("editable"))
        return asset

    @staticmethod
    def _public_asset(asset: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            key: asset[key]
            for key in (
                "ref",
                "kind",
                "name",
                "display_name",
                "summary",
                "invocation",
                "input_fields",
                "aliases",
                "tags",
                "custom_tool",
                "editable",
                "version",
                "revision",
                "finance_tool_profile",
            )
            if key in asset
        }

    def _list_static_tool_assets(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not self.tool_definitions_dir.exists():
            return rows
        for path in sorted(self.tool_definitions_dir.glob("*.tool.json")):
            if path.name.startswith("._"):
                continue
            definition = self._load_json(path)
            if not definition or not self._tool_definition_is_invocable(definition):
                continue
            name = path.name.removesuffix(".tool.json")
            identity = definition.get("identity") if isinstance(definition.get("identity"), Mapping) else {}
            schemas = definition.get("schemas") if isinstance(definition.get("schemas"), Mapping) else {}
            input_schema = schemas.get("input") if isinstance(schemas.get("input"), Mapping) else {}
            contract = {
                "kind": "tool",
                "name": name,
                "display_name": self._trim(identity.get("display_name")) or name,
                "description": self._trim(identity.get("description")),
                "input_schema": dict(input_schema),
                "aliases": [
                    self._trim(item)
                    for item in (identity.get("aliases") or [])
                    if self._trim(item)
                ],
                "tags": [
                    self._trim(item)
                    for item in (
                        list(definition.get("tags") or [])
                        + list(definition.get("capabilities") or [])
                    )
                    if self._trim(item)
                ],
                "custom_tool": False,
                "version": self._trim(definition.get("version")) or "v1",
                "revision": int(definition.get("revision") or 0),
            }
            keywords = [
                *[self._trim(item) for item in (identity.get("aliases") or []) if self._trim(item)],
                *[self._trim(item) for item in (definition.get("tags") or []) if self._trim(item)],
                *[self._trim(item) for item in (definition.get("capabilities") or []) if self._trim(item)],
            ]
            rows.append(self._asset_from_contract(contract, keywords=keywords))
        return rows

    def _list_custom_tool_assets(
        self,
        *,
        owner_ids: Optional[List[str]],
        allow_inactive: bool,
    ) -> List[Dict[str, Any]]:
        list_tools = getattr(self.custom_tool_store, "list_tools", None)
        if not callable(list_tools):
            return []
        visible_owner_ids = (
            [self._trim(item) for item in owner_ids if self._trim(item)]
            if owner_ids is not None
            else ["__public_asset_catalog__"]
        )
        try:
            manifests = list_tools(
                include_inactive=allow_inactive,
                owner_ids=visible_owner_ids,
            )
        except TypeError:
            manifests = list_tools(owner_ids=visible_owner_ids)
        except Exception:
            return []
        rows: List[Dict[str, Any]] = []
        for manifest_like in manifests or []:
            if not isinstance(manifest_like, Mapping):
                continue
            manifest = dict(manifest_like)
            name = self._trim(manifest.get("tool_name"))
            if not name:
                continue
            try:
                bundle = self.custom_tool_store.load_for_runtime(
                    name,
                    owner_ids=visible_owner_ids,
                    allow_inactive=allow_inactive,
                )
            except Exception:
                continue
            loaded_manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), Mapping) else manifest
            finance_tool_profile = (
                dict(bundle.get("finance_tool_profile") or {})
                if isinstance(bundle.get("finance_tool_profile"), Mapping)
                else {}
            )
            if self._trim(finance_tool_profile.get("family")).lower() == "action":
                # Action assets are currently design-only and must not appear
                # in the invocable Tool/Skill picker.
                continue
            contract = {
                "kind": "tool",
                "name": name,
                "display_name": self._trim(loaded_manifest.get("display_name")) or name,
                "description": self._trim(loaded_manifest.get("description")),
                "input_schema": (
                    bundle.get("input_schema")
                    if isinstance(bundle.get("input_schema"), Mapping)
                    else {}
                ),
                "aliases": [
                    self._trim(item)
                    for item in (
                        list(loaded_manifest.get("aliases") or [])
                        + list(loaded_manifest.get("keywords") or [])
                    )
                    if self._trim(item)
                ],
                "tags": [
                    self._trim(item)
                    for item in (
                        list(loaded_manifest.get("tags") or [])
                        + list(loaded_manifest.get("capabilities") or [])
                    )
                    if self._trim(item)
                ],
                "custom_tool": True,
                "editable": self._trim(loaded_manifest.get("owner_id")) in set(visible_owner_ids),
                "version": self._trim(loaded_manifest.get("version")) or "v1",
                "revision": int(loaded_manifest.get("current_revision") or 0),
                "finance_tool_profile": finance_tool_profile,
            }
            keywords = [
                *[self._trim(item) for item in (loaded_manifest.get("keywords") or []) if self._trim(item)],
                *[self._trim(item) for item in (loaded_manifest.get("best_for") or []) if self._trim(item)],
            ]
            rows.append(self._asset_from_contract(contract, keywords=keywords))
        return rows

    def _list_skill_assets(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not self.skills_root.exists():
            return rows
        for skill_dir in sorted(self.skills_root.iterdir()):
            config_path = skill_dir / "skill.json"
            skill_md_path = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not config_path.is_file() or not skill_md_path.is_file():
                continue
            config = self._load_json(config_path)
            if not config or not self.skill_config_is_invocable(config):
                continue
            name = skill_dir.name
            input_schema = config.get("input_schema") if isinstance(config.get("input_schema"), Mapping) else {
                "type": "object",
                "required": ["question"],
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "希望该 Skill 完成的自然语言任务",
                    }
                },
            }
            contract = {
                "kind": "skill",
                "name": name,
                "display_name": self._trim(config.get("display_name")) or name,
                "description": self._trim(config.get("purpose") or config.get("description")),
                "input_schema": dict(input_schema),
                "requires_natural_language": "question" in self._schema_required(input_schema),
                "aliases": [
                    self._trim(item)
                    for item in (config.get("aliases") or [])
                    if self._trim(item)
                ],
                "tags": [
                    self._trim(item)
                    for item in (config.get("tags") or [])
                    if self._trim(item)
                ],
                "custom_tool": False,
                "version": self._trim(config.get("version")) or "v1",
                "revision": int(config.get("revision") or 0),
            }
            keywords = [
                *[self._trim(item) for item in (config.get("tags") or []) if self._trim(item)],
                *[self._trim(item) for item in (config.get("best_for") or []) if self._trim(item)],
            ]
            rows.append(self._asset_from_contract(contract, keywords=keywords))
        return rows

    def _collect_invocable_assets(
        self,
        *,
        owner_ids: Optional[List[str]],
        allow_inactive: bool,
    ) -> List[Dict[str, Any]]:
        rows = [
            *self._list_static_tool_assets(),
            *self._list_custom_tool_assets(
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            ),
            *self._list_skill_assets(),
        ]
        unique: Dict[str, Dict[str, Any]] = {}
        for item in rows:
            ref = self._trim(item.get("ref"))
            if ref and ref not in unique:
                unique[ref] = item
        return sorted(
            unique.values(),
            key=lambda item: (
                0 if self._trim(item.get("kind")) == "tool" else 1,
                self._normalize_lookup_text(item.get("display_name")),
                self._normalize_lookup_text(item.get("name")),
            ),
        )

    def _asset_match_score(self, asset: Mapping[str, Any], query: str) -> Optional[float]:
        needle = self._normalize_lookup_text(query)
        if not needle:
            return 0.0
        name = self._normalize_lookup_text(asset.get("name"))
        display_name = self._normalize_lookup_text(asset.get("display_name"))
        search_text = self._normalize_lookup_text(asset.get("_search_text"))
        if name.startswith(needle):
            return 100.0 - max(0, len(name) - len(needle)) * 0.01
        if display_name.startswith(needle):
            return 90.0 - max(0, len(display_name) - len(needle)) * 0.01
        if needle in name:
            return 80.0
        if needle in display_name:
            return 70.0
        if needle in search_text:
            return 55.0
        if len(needle) < 2:
            return None
        ratio = max(
            SequenceMatcher(None, needle, name).ratio(),
            SequenceMatcher(None, needle, display_name).ratio(),
        )
        return 40.0 * ratio if ratio >= 0.58 else None

    def list_invocable_assets(
        self,
        *,
        owner_ids: Optional[List[str]] = None,
        query: str = "",
        kind: str = "",
        limit: Optional[int] = None,
        allow_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        normalized_kind = self._trim(kind).lower()
        if normalized_kind and normalized_kind not in {"tool", "skill"}:
            raise AssetInvocationError(f"不支持的资产类型：{normalized_kind}")
        rows = self._collect_invocable_assets(
            owner_ids=owner_ids,
            allow_inactive=allow_inactive,
        )
        if normalized_kind:
            rows = [item for item in rows if self._trim(item.get("kind")) == normalized_kind]
        needle = self._trim(query)
        if needle:
            ranked = [
                (score, item)
                for item in rows
                if (score := self._asset_match_score(item, needle)) is not None
            ]
            ranked.sort(
                key=lambda row: (
                    -row[0],
                    self._normalize_lookup_text(row[1].get("display_name")),
                    self._normalize_lookup_text(row[1].get("name")),
                )
            )
            rows = [item for _score, item in ranked]
        if limit is not None:
            rows = rows[:max(0, int(limit))]
        return [self._public_asset(item) for item in rows]

    def query_invocable_assets(
        self,
        query: str,
        *,
        owner_ids: Optional[List[str]] = None,
        kind: str = "",
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        return self.list_invocable_assets(
            owner_ids=owner_ids,
            query=query,
            kind=kind,
            limit=limit,
        )

    def _direct_exact_asset(
        self,
        *,
        name: str,
        kind: str,
        owner_ids: Optional[List[str]],
        allow_inactive: bool,
    ) -> List[Dict[str, Any]]:
        kinds = [kind] if kind in {"tool", "skill"} else ["tool", "skill"]
        rows: List[Dict[str, Any]] = []
        for candidate_kind in kinds:
            try:
                contract = self.load_contract(
                    kind=candidate_kind,
                    name=name,
                    owner_ids=owner_ids,
                    allow_inactive=allow_inactive,
                )
            except (AssetInvocationError, FileNotFoundError, ValueError):
                continue
            rows.append(self._public_asset(self._asset_from_contract(contract)))
        return rows

    def _resolve_reference_query(
        self,
        *,
        name: str,
        kind: str,
        owner_ids: Optional[List[str]],
        allow_inactive: bool,
    ) -> Dict[str, Any]:
        normalized_name = self._normalize_lookup_text(name)
        catalog = self.list_invocable_assets(
            owner_ids=owner_ids,
            kind=kind,
            allow_inactive=allow_inactive,
        )
        exact_name = [
            item
            for item in catalog
            if self._normalize_lookup_text(item.get("name")) == normalized_name
        ]
        exact_display = [
            item
            for item in catalog
            if self._normalize_lookup_text(item.get("display_name")) == normalized_name
        ]
        exact = exact_name or exact_display
        if not exact:
            exact = self._direct_exact_asset(
                name=name,
                kind=kind,
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
        if len(exact) == 1:
            item = exact[0]
            return {
                "status": "resolved",
                "query": name,
                "target": {
                    "kind": self._trim(item.get("kind")),
                    "name": self._trim(item.get("name")),
                },
                "asset": item,
                "candidates": [item],
            }
        if len(exact) > 1:
            return {
                "status": "ambiguous",
                "query": name,
                "candidates": exact,
                "message": f"“{name}”对应多个可调用资产，请选择 Tool 或 Skill。",
            }
        candidates = self.query_invocable_assets(
            name,
            owner_ids=owner_ids,
            kind=kind,
            limit=5,
        )
        return {
            "status": "needs_selection",
            "query": name,
            "candidates": candidates,
            "message": (
                f"没有找到“{name}”的精确匹配，请从候选中选择。"
                if candidates
                else f"未找到或无权调用该 Tool 或 Skill：{name}"
            ),
        }

    def _resolve_invocation_target(
        self,
        *,
        text: str,
        selected_asset: Mapping[str, Any] | None,
        owner_ids: Optional[List[str]],
        allow_inactive: bool,
    ) -> Dict[str, Any]:
        selected_reference = self._selected_reference(selected_asset)
        text_reference = self._text_reference(text)
        if not selected_reference and not text_reference:
            return {"status": "none"}

        selected_resolution = (
            self._resolve_reference_query(
                name=selected_reference["name"],
                kind=selected_reference.get("kind") or "",
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
            if selected_reference
            else {}
        )
        text_resolution = (
            self._resolve_reference_query(
                name=self._trim(text_reference.get("name")),
                kind=self._trim(text_reference.get("kind")).lower(),
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
            if text_reference
            else {}
        )

        if selected_reference and text_reference:
            selected_target = (
                selected_resolution.get("target")
                if selected_resolution.get("status") == "resolved"
                else {}
            )
            text_target = (
                text_resolution.get("target")
                if text_resolution.get("status") == "resolved"
                else {}
            )
            selected_ref = self._asset_ref(
                selected_target.get("kind"),
                selected_target.get("name"),
            ) if selected_target else ""
            text_ref = self._asset_ref(
                text_target.get("kind"),
                text_target.get("name"),
            ) if text_target else ""
            text_candidate_refs = {
                self._trim(item.get("ref"))
                for item in (text_resolution.get("candidates") or [])
                if isinstance(item, Mapping)
            }
            selected_disambiguates_same_name = (
                text_resolution.get("status") == "ambiguous"
                and selected_ref in text_candidate_refs
            )
            if (
                not selected_ref
                or (text_ref and selected_ref != text_ref)
                or (not text_ref and not selected_disambiguates_same_name)
            ):
                raise AssetInvocationError("所选资产与输入中的 $ 引用不一致，请重新选择。")
            return selected_resolution
        return selected_resolution or text_resolution

    def _asset_resolution_needs_input(self, resolution: Mapping[str, Any]) -> Dict[str, Any]:
        candidates = [
            dict(item)
            for item in (resolution.get("candidates") or [])
            if isinstance(item, Mapping)
        ]
        message = self._trim(resolution.get("message")) or "请选择要调用的 Tool 或 Skill。"
        return {
            "version": "v1",
            "status": "needs_input",
            "target": {},
            "user_request": "",
            "arguments": {},
            "execution": {
                "mode": "single",
                "item_argument": "",
                "items": [],
                "source": {},
                "item_count": 0,
            },
            "calls": [],
            "resolved_entities": [],
            "missing_required": ["asset"],
            "message": message,
            "contract": {},
            "attachments": [],
            "llm_usage": self._normalize_usage(None),
            "candidates": candidates,
            "asset_resolution": {
                "status": self._trim(resolution.get("status")) or "needs_selection",
                "query": self._trim(resolution.get("query")),
                "candidates": candidates,
            },
        }

    def load_contract(
        self,
        *,
        kind: str,
        name: str,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = False,
    ) -> Dict[str, Any]:
        normalized_kind = self._trim(kind).lower()
        normalized_name = self._trim(name)
        if normalized_kind == "tool":
            return self._load_tool_contract(
                normalized_name,
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
        if normalized_kind == "skill":
            return self._load_skill_contract(normalized_name)
        raise AssetInvocationError(f"不支持的资产类型：{normalized_kind or '-'}")

    def plan(
        self,
        *,
        text: str,
        selected_asset: Mapping[str, Any] | None = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        thread_context: Optional[Dict[str, Any]] = None,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = False,
    ) -> Dict[str, Any]:
        resolution = self._resolve_invocation_target(
            text=text,
            selected_asset=selected_asset,
            owner_ids=owner_ids,
            allow_inactive=allow_inactive,
        )
        if resolution.get("status") == "none":
            return {}
        if resolution.get("status") != "resolved":
            return self._asset_resolution_needs_input(resolution)
        target = dict(resolution["target"])
        contract = self.load_contract(
            kind=target["kind"],
            name=target["name"],
            owner_ids=owner_ids,
            allow_inactive=allow_inactive,
        )
        user_request = self.strip_invocation_prefix(text, target_name=target["name"])
        input_bundle = self.input_resolver.inspect(attachments or [])
        attachment_payload = input_bundle["attachments"]
        attachment_prompt_payload = input_bundle["prompt_attachments"]
        properties = self._schema_properties(contract.get("input_schema"))
        required = self._schema_required(contract.get("input_schema"))

        if not properties and not user_request and not attachment_payload:
            model_payload: Dict[str, Any] = {
                "status": "ready",
                "arguments": {},
                "execution": {"mode": "single", "item_argument": "", "items": []},
                "missing_required": [],
                "reason": "该资产无需输入参数，可直接执行。",
            }
            usage: Any = None
        else:
            messages = self.registry.render_messages(
                "system.assistant.asset_invocation",
                {
                    "asset_contract_json": json.dumps(contract, ensure_ascii=False, indent=2),
                    "user_request": user_request or "（用户未额外输入自然语言要求）",
                    "context_json": json.dumps(self._compact_context(thread_context or {}), ensure_ascii=False, indent=2),
                    "attachments_json": json.dumps(attachment_prompt_payload, ensure_ascii=False, indent=2),
                },
            )
            try:
                model_payload, usage = self.llm_chat(messages, enable_think=False)
            except Exception as exc:
                raise AssetInvocationError(f"调用解析失败：{exc}") from exc
            if not isinstance(model_payload, dict):
                raise AssetInvocationError("调用解析失败：LLM 未返回合法 JSON")

        execution = model_payload.get("execution") if isinstance(model_payload.get("execution"), dict) else {}
        mode = self._trim(execution.get("mode")).lower() or "single"
        if mode not in {"single", "map"}:
            mode = "single"
        arguments = model_payload.get("arguments") if isinstance(model_payload.get("arguments"), dict) else {}
        items = execution.get("items") if isinstance(execution.get("items"), list) else []
        item_argument = self._trim(execution.get("item_argument"))
        source = execution.get("source") if isinstance(execution.get("source"), Mapping) else {}
        try:
            source_items = self.input_resolver.materialize(source, attachment_payload)
        except InvocationInputError as exc:
            raise AssetInvocationError(str(exc)) from exc
        if source_items:
            items = source_items
        mode, arguments, items = self._align_execution_with_schema(
            mode=mode,
            arguments=arguments,
            item_argument=item_argument,
            items=items,
            input_schema=contract.get("input_schema") if isinstance(contract.get("input_schema"), dict) else {},
        )
        normalized_calls, missing_required = self._normalize_calls(
            target=target,
            input_schema=contract.get("input_schema") if isinstance(contract.get("input_schema"), dict) else {},
            arguments=arguments,
            mode=mode,
            item_argument=item_argument,
            items=items,
        )
        resolved_entities, unresolved_entities = self._resolve_entities(
            normalized_calls,
            model_payload.get("entities"),
        )
        model_missing = [self._trim(item) for item in (model_payload.get("missing_required") or []) if self._trim(item)]
        missing_required = list(dict.fromkeys([*model_missing, *missing_required]))
        status = "needs_input" if missing_required or unresolved_entities or not normalized_calls else "ready"
        message = (
            "还需要补充：" + "、".join(missing_required)
            if missing_required
            else "未能确认股票标的：" + "、".join(unresolved_entities) + "。请补充准确的公司全称或股票代码。"
            if unresolved_entities
            else self._trim(model_payload.get("reason")) or "调用参数已准备完成。"
        )
        item_count = self._execution_item_count(
            items=items,
            calls=normalized_calls,
            raw_entities=model_payload.get("entities"),
        )
        invocation = {
            "version": "v1",
            "status": status,
            "target": target,
            "user_request": user_request,
            "arguments": arguments,
            "execution": {
                "mode": mode,
                "item_argument": item_argument,
                "items": items,
                "source": dict(source),
                "item_count": item_count,
            },
            "calls": normalized_calls,
            "resolved_entities": resolved_entities,
            "missing_required": missing_required,
            "message": message,
            "contract": contract,
            "attachments": attachment_prompt_payload,
            "llm_usage": self._normalize_usage(usage),
        }
        invocation["preview"] = self.build_preview(invocation)
        return invocation

    def build_preview(self, invocation: Mapping[str, Any]) -> Dict[str, Any]:
        target = invocation.get("target") if isinstance(invocation.get("target"), Mapping) else {}
        contract = invocation.get("contract") if isinstance(invocation.get("contract"), Mapping) else {}
        calls = [dict(item) for item in (invocation.get("calls") or []) if isinstance(item, Mapping)]
        entities = [dict(item) for item in (invocation.get("resolved_entities") or []) if isinstance(item, Mapping)]
        entity_arguments = {self._trim(item.get("argument")) for item in entities}
        properties = self._schema_properties(contract.get("input_schema"))
        display_name = self._trim(contract.get("display_name")) or self._trim(target.get("name"))

        lines = [f"正在使用「{display_name}」。"]
        if entities:
            labels = []
            for entity in entities:
                name = self._trim(entity.get("name"))
                code = self._trim(entity.get("code")).split(".", 1)[0]
                label = f"{name}（{code}）" if name and code else name or code
                if label and label not in labels:
                    labels.append(label)
            if labels:
                lines.append("标的：" + "、".join(labels))
        if len(calls) > 1:
            lines.append(f"执行方式：逐项运行，共 {len(calls)} 项")
        elif calls and int((invocation.get("execution") or {}).get("item_count") or 0) > 1:
            lines.append(f"执行方式：批量运行，共 {invocation['execution']['item_count']} 项")
        if calls:
            argument_lines = []
            for name, value in calls[0].items():
                if name in entity_arguments or self._is_missing(value):
                    continue
                definition = properties.get(name) if isinstance(properties.get(name), Mapping) else {}
                label = self._trim(definition.get("title")) or name
                argument_lines.append(f"{label}={self._display_value(value)}")
            if argument_lines:
                lines.append("其他参数：" + "；".join(argument_lines))
        public_entities = [
            {
                "kind": self._trim(item.get("kind")),
                "name": self._trim(item.get("name")),
                "display_code": self._trim(item.get("code")).split(".", 1)[0],
            }
            for item in entities
        ]
        return {
            "target": {"kind": self._trim(target.get("kind")), "name": self._trim(target.get("name")), "display_name": display_name},
            "entities": public_entities,
            "call_count": len(calls),
            "message": "\n".join(lines),
        }

    def build_tool_execution_plan(self, invocation: Mapping[str, Any]) -> Dict[str, Any]:
        target = invocation.get("target") if isinstance(invocation.get("target"), Mapping) else {}
        tool_name = self._trim(target.get("name"))
        calls = [dict(item) for item in (invocation.get("calls") or []) if isinstance(item, Mapping)]
        return {
            "plan_type": "explicit_asset_invocation",
            "objective": self._trim(invocation.get("user_request")) or f"调用 {tool_name}",
            "selected_tools": [tool_name] if tool_name else [],
            "work_items": [
                {
                    "step_id": f"{tool_name}_{index + 1}",
                    "type": "tool",
                    "name": tool_name,
                    "intent": self._trim(invocation.get("user_request")),
                    "arguments": call,
                    "depends_on": [],
                }
                for index, call in enumerate(calls)
            ],
        }

    def guidance(self, contract: Mapping[str, Any]) -> Dict[str, Any]:
        schema = contract.get("input_schema") if isinstance(contract.get("input_schema"), Mapping) else {}
        properties = self._schema_properties(schema)
        required = set(self._schema_required(schema))
        fields = []
        for name, definition in properties.items():
            field = definition if isinstance(definition, Mapping) else {}
            fields.append({
                "name": name,
                "label": self._trim(field.get("title") or field.get("label")) or name,
                "required": name in required,
                "type": self._trim(field.get("type")) or "string",
                "description": self._trim(field.get("description")),
                "default": field.get("default"),
            })
        return {
            "fields": fields,
            "can_run_directly": not required,
            "requires_natural_language": bool(contract.get("requires_natural_language")),
        }

    def _load_tool_contract(
        self,
        name: str,
        *,
        owner_ids: Optional[List[str]],
        allow_inactive: bool,
    ) -> Dict[str, Any]:
        if self.custom_tool_store.exists(name):
            bundle = self.custom_tool_store.load_for_runtime(
                name,
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
            manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), dict) else {}
            input_schema = bundle.get("input_schema") if isinstance(bundle.get("input_schema"), dict) else {}
            design_contract = bundle.get("design_contract") if isinstance(bundle.get("design_contract"), dict) else {}
            finance_tool_profile = (
                dict(bundle.get("finance_tool_profile") or {})
                if isinstance(bundle.get("finance_tool_profile"), Mapping)
                else {}
            )
            if self._trim(finance_tool_profile.get("family")).lower() == "action":
                raise AssetInvocationError(
                    f"Tool 当前仅有设计方案，不能调用：{name}"
                )
            return {
                "kind": "tool",
                "name": name,
                "display_name": self._trim(manifest.get("display_name")) or name,
                "description": self._trim(manifest.get("description")),
                "input_schema": self._with_design_labels(input_schema, design_contract),
                "sample_input": bundle.get("sample_input") if isinstance(bundle.get("sample_input"), dict) else {},
                "custom_tool": True,
                "aliases": [
                    self._trim(item)
                    for item in (
                        list(manifest.get("aliases") or [])
                        + list(manifest.get("keywords") or [])
                    )
                    if self._trim(item)
                ],
                "tags": [
                    self._trim(item)
                    for item in (
                        list(manifest.get("tags") or [])
                        + list(manifest.get("capabilities") or [])
                    )
                    if self._trim(item)
                ],
                "version": self._trim(manifest.get("version")) or "v1",
                "revision": int(manifest.get("current_revision") or 0),
                "finance_tool_profile": finance_tool_profile,
            }
        path = self.tool_definitions_dir / f"{name}.tool.json"
        if not path.exists():
            raise AssetInvocationError(f"Tool 不存在：{name}")
        definition = self._load_json(path)
        if not definition or not self._tool_definition_is_invocable(definition):
            raise AssetInvocationError(f"Tool 不可调用或无权调用：{name}")
        identity = definition.get("identity") if isinstance(definition.get("identity"), dict) else {}
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
        return {
            "kind": "tool",
            "name": name,
            "display_name": self._trim(identity.get("display_name")) or name,
            "description": self._trim(identity.get("description")),
            "input_schema": input_schema,
            "sample_input": self._first_sample_input(definition),
            "custom_tool": False,
            "aliases": [
                self._trim(item)
                for item in (identity.get("aliases") or [])
                if self._trim(item)
            ],
            "tags": [
                self._trim(item)
                for item in (
                    list(definition.get("tags") or [])
                    + list(definition.get("capabilities") or [])
                )
                if self._trim(item)
            ],
            "version": self._trim(definition.get("version")) or "v1",
            "revision": int(definition.get("revision") or 0),
        }

    def _load_skill_contract(self, name: str) -> Dict[str, Any]:
        skill_dir = self.skills_root / name
        config_path = skill_dir / "skill.json"
        skill_md_path = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not config_path.is_file() or not skill_md_path.is_file():
            raise AssetInvocationError(f"Skill 不存在或不可调用：{name}")
        config = self._load_json(config_path)
        if not config or not self.skill_config_is_invocable(config):
            raise AssetInvocationError(f"Skill 不存在或不可调用：{name}")
        input_schema = config.get("input_schema") if isinstance(config.get("input_schema"), dict) else {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {
                    "type": "string",
                    "description": "希望该 Skill 完成的自然语言任务",
                }
            },
        }
        return {
            "kind": "skill",
            "name": name,
            "display_name": self._trim(config.get("display_name")) or name,
            "description": self._trim(config.get("purpose") or config.get("description")),
            "input_schema": input_schema,
            "sample_input": config.get("sample_input") if isinstance(config.get("sample_input"), dict) else {},
            "requires_natural_language": "question" in self._schema_required(input_schema),
            "aliases": [
                self._trim(item)
                for item in (config.get("aliases") or [])
                if self._trim(item)
            ],
            "tags": [
                self._trim(item)
                for item in (config.get("tags") or [])
                if self._trim(item)
            ],
            "custom_tool": False,
            "version": self._trim(config.get("version")) or "v1",
            "revision": int(config.get("revision") or 0),
        }

    def _align_execution_with_schema(
        self,
        *,
        mode: str,
        arguments: Dict[str, Any],
        item_argument: str,
        items: List[Any],
        input_schema: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any], List[Any]]:
        if not item_argument or not items:
            return mode, arguments, items
        properties = self._schema_properties(input_schema)
        definition = properties.get(item_argument) if isinstance(properties.get(item_argument), Mapping) else {}
        normalized_items = self._dedupe_items(items)
        if self._trim(definition.get("type")).lower() == "array" and all(
            not isinstance(item, Mapping) for item in normalized_items
        ):
            return "single", {**arguments, item_argument: normalized_items}, normalized_items
        return "map", arguments, normalized_items

    @staticmethod
    def _dedupe_items(items: List[Any]) -> List[Any]:
        values: List[Any] = []
        seen: set[str] = set()
        for item in items:
            key = (
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                if isinstance(item, (dict, list))
                else str(item).strip()
            )
            if key in seen:
                continue
            seen.add(key)
            values.append(item)
        return values

    def _execution_item_count(
        self,
        *,
        items: List[Any],
        calls: List[Dict[str, Any]],
        raw_entities: Any,
    ) -> int:
        if items:
            return len(items)
        if len(calls) > 1:
            return len(calls)
        if len(calls) != 1 or not isinstance(raw_entities, list):
            return 0
        for entity in raw_entities:
            if not isinstance(entity, Mapping):
                continue
            argument = self._trim(entity.get("argument"))
            value = calls[0].get(argument)
            if isinstance(value, list):
                return len(value)
        return 0

    def _normalize_calls(
        self,
        *,
        target: Dict[str, str],
        input_schema: Dict[str, Any],
        arguments: Dict[str, Any],
        mode: str,
        item_argument: str,
        items: List[Any],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        raw_calls: List[Dict[str, Any]] = []
        if mode == "map" and items:
            for item in items:
                if isinstance(item, Mapping):
                    raw_calls.append({**arguments, **dict(item)})
                elif item_argument:
                    raw_calls.append({**arguments, item_argument: item})
        else:
            raw_calls = [dict(arguments)]
        if target.get("kind") == "skill":
            missing: List[str] = []
            for call in raw_calls:
                missing.extend(self._missing_required(call, input_schema))
            if not raw_calls:
                missing.extend(self._schema_required(input_schema))
            return raw_calls, list(dict.fromkeys(missing))
        normalized_calls: List[Dict[str, Any]] = []
        missing: List[str] = []
        from src.tools.registry import normalize_tool_args_for_definition

        for call in raw_calls:
            normalized = normalize_tool_args_for_definition(target.get("name") or "", call)
            normalized_arguments = normalized.get("arguments") if isinstance(normalized.get("arguments"), dict) else call
            normalized_calls.append(normalized_arguments)
            missing.extend(self._trim(item) for item in (normalized.get("missing_required") or []) if self._trim(item))
            missing.extend(self._missing_required(normalized_arguments, input_schema))
        if not raw_calls:
            missing.extend(self._schema_required(input_schema))
        return normalized_calls, list(dict.fromkeys(missing))

    def _resolve_entities(self, calls: List[Dict[str, Any]], raw_entities: Any) -> tuple[List[Dict[str, str]], List[str]]:
        entities = [item for item in (raw_entities or []) if isinstance(item, Mapping)] if isinstance(raw_entities, list) else []
        resolved: List[Dict[str, str]] = []
        unresolved: List[str] = []
        identity_cache: Dict[tuple[str, str], Optional[Dict[str, str]]] = {}
        for entity in entities:
            if self._trim(entity.get("kind")).lower() != "stock":
                continue
            argument = self._trim(entity.get("argument"))
            if not argument:
                continue
            for call in calls:
                raw_value = call.get(argument)
                values = raw_value if isinstance(raw_value, list) else [raw_value]
                normalized_values: List[Any] = []
                for value in values:
                    query = self._trim(value)
                    key = (argument, query)
                    if not query:
                        normalized_values.append(value)
                        continue
                    if key not in identity_cache:
                        identity_cache[key] = self.stock_identity_resolver.resolve(query)
                    identity = identity_cache[key]
                    if not identity:
                        unresolved.append(query)
                        normalized_values.append(value)
                        continue
                    normalized_values.append(identity["code"])
                    resolved.append({**identity, "argument": argument})
                call[argument] = normalized_values if isinstance(raw_value, list) else (normalized_values[0] if normalized_values else raw_value)
        return resolved, list(dict.fromkeys(unresolved))

    @staticmethod
    def _display_value(value: Any) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    @staticmethod
    def _schema_properties(schema: Any) -> Dict[str, Any]:
        return dict(schema.get("properties") or {}) if isinstance(schema, Mapping) and isinstance(schema.get("properties"), Mapping) else {}

    def _schema_required(self, schema: Any) -> List[str]:
        return [self._trim(item) for item in ((schema.get("required") or []) if isinstance(schema, Mapping) else []) if self._trim(item)]

    def _missing_required(self, arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
        return [name for name in self._schema_required(schema) if self._is_missing(arguments.get(name))]

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, dict, set)):
            return not value
        return False

    def _compact_context(self, thread_context: Dict[str, Any]) -> Dict[str, Any]:
        questions: List[str] = []
        recent_turns: List[Dict[str, str]] = []
        for turn in (thread_context.get("context_window") or [])[-5:]:
            if not isinstance(turn, Mapping):
                continue
            value = self._trim(
                turn.get("text")
                or turn.get("user_input_text")
                or turn.get("question")
                or turn.get("user_text")
            )
            if not value:
                continue
            role = self._trim(turn.get("role")).lower()
            recent_turns.append({"role": role or "unknown", "text": value[:500]})
            if role in {"", "user"}:
                questions.append(value[:500])
        return {
            "recent_user_questions": questions,
            "recent_turns": recent_turns,
        }

    @staticmethod
    def _with_design_labels(input_schema: Dict[str, Any], design_contract: Mapping[str, Any]) -> Dict[str, Any]:
        schema = dict(input_schema)
        properties = {
            str(name): dict(definition) if isinstance(definition, Mapping) else definition
            for name, definition in (input_schema.get("properties") or {}).items()
        }
        for field in design_contract.get("inputs") or []:
            if not isinstance(field, Mapping):
                continue
            name = str(field.get("name") or "").strip()
            label = str(field.get("label") or "").strip()
            if name in properties and isinstance(properties[name], dict) and label:
                properties[name].setdefault("title", label)
        schema["properties"] = properties
        return schema

    @staticmethod
    def _first_sample_input(definition: Dict[str, Any]) -> Dict[str, Any]:
        tests = definition.get("tests") if isinstance(definition.get("tests"), dict) else {}
        samples = tests.get("sample_inputs") if isinstance(tests.get("sample_inputs"), list) else []
        first = samples[0] if samples and isinstance(samples[0], dict) else {}
        return dict(first.get("arguments") or {}) if isinstance(first.get("arguments"), dict) else {}

    @staticmethod
    def _normalize_usage(usage: Any) -> Dict[str, int]:
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if isinstance(usage, Mapping):
            source = usage
        else:
            source = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
            }
        return {key: int(source.get(key, 0) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
