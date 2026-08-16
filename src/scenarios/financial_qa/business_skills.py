from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Optional

import yaml


_ALLOWED_COMPANION_DIRS = frozenset({"agents", "assets", "examples"})
_SNAPSHOT_FORMAT_VERSION = 4


def _trim(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class _FinanceSkillReferenceSnapshot:
    path: str
    content_hash: str
    content: bytes


@dataclass(frozen=True)
class _FinanceSkillCompanionSnapshot:
    path: str
    content_hash: str
    content: bytes


@dataclass(frozen=True)
class _FinanceBusinessSkillSnapshot:
    skill_id: str
    category: str
    relative_path: str
    description: str
    method: str
    method_content: bytes
    content_hash: str
    allowed_tools: tuple[str, ...]
    execution_budget: str
    references: tuple[_FinanceSkillReferenceSnapshot, ...]
    companion_files: tuple[_FinanceSkillCompanionSnapshot, ...]


@dataclass(frozen=True)
class _FinanceBusinessCatalogSnapshot:
    revision: str
    revision_payload: bytes
    plugin_name: str
    plugin_manifest: bytes
    catalog_content: bytes
    runtime_root: Path
    skills: tuple[_FinanceBusinessSkillSnapshot, ...]


class FinanceBusinessSkillCatalog:
    """Immutable runtime snapshot for Finance CC business methods."""

    def __init__(
        self,
        *,
        root: str | Path = "src/skills/finance-business",
        snapshot_root: str | Path = "outputs/runtime_skill_snapshots/finance-business",
    ) -> None:
        self.root = Path(root)
        self.catalog_path = self.root / "catalog.json"
        self.snapshot_root = Path(snapshot_root)
        self._validated_runtime_bindings: dict[
            str,
            tuple[Path, tuple[str, ...]],
        ] = {}
        self._snapshot = self._compile_snapshot()

    def entries(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> list[Dict[str, Any]]:
        snapshot = self._snapshot
        allowed = {
            _trim(item)
            for item in (allowed_skill_ids or [])
            if _trim(item)
        }
        restrict = allowed_skill_ids is not None
        entries: list[Dict[str, Any]] = []
        for skill in snapshot.skills:
            if restrict and skill.skill_id not in allowed:
                continue
            entries.append(
                {
                    "id": skill.skill_id,
                    "category": skill.category,
                    "path": skill.relative_path,
                    "description": skill.description,
                    "_skill_file": (
                        snapshot.runtime_root
                        / skill.relative_path
                        / "SKILL.md"
                    ),
                    "_method": skill.method,
                    "_content_hash": skill.content_hash,
                    "_allowed_tools": list(skill.allowed_tools),
                    "execution_budget": skill.execution_budget,
                    "_reference_index": [
                        {
                            "path": reference.path,
                            "content_hash": reference.content_hash,
                        }
                        for reference in skill.references
                    ],
                }
            )
        return entries

    def public_entries(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> list[Dict[str, Any]]:
        return [
            {key: value for key, value in entry.items() if not key.startswith("_")}
            for entry in self.entries(allowed_skill_ids=allowed_skill_ids)
        ]

    def load(
        self,
        skill_id: str,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        normalized = _trim(skill_id)
        entry = next(
            (
                item
                for item in self.entries(allowed_skill_ids=allowed_skill_ids)
                if item["id"] == normalized
            ),
            None,
        )
        if entry is None:
            return {
                "skill_id": normalized,
                "error": "该业务 Skill 未注册、未授权或当前不可用。",
                "guidance": "这不会中断对话；请由 Finance CC 使用现有数据工具继续合理处理。",
            }
        return {
            "skill_id": normalized,
            "description": entry["description"],
            "method": entry["_method"],
            "control": "Finance CC 继续持有当前会话、工具选择和最终回答。",
        }

    def load_reference(
        self,
        skill_id: str,
        reference_path: str,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
        expected_revision: str = "",
    ) -> Dict[str, Any]:
        """Read one reference from the immutable in-memory Skill snapshot.

        Finance CC deliberately has no general filesystem tools.  Progressive
        Skill material therefore goes through this exact, content-addressed
        lookup instead of reopening source or runtime files during a turn.
        """

        normalized_skill_id = _trim(skill_id)
        normalized_reference = _trim(reference_path).replace("\\", "/")
        normalized_revision = _trim(expected_revision)
        if normalized_revision and normalized_revision != self._snapshot.revision:
            return {
                "skill_id": normalized_skill_id,
                "reference": normalized_reference,
                "error": "本轮业务 Skill 快照已更新，请使用同一修订重新执行。",
            }
        allowed = {
            _trim(item)
            for item in (allowed_skill_ids or [])
            if _trim(item)
        }
        if allowed_skill_ids is not None and normalized_skill_id not in allowed:
            return {
                "skill_id": normalized_skill_id,
                "reference": normalized_reference,
                "error": "该业务 Skill 未授权或当前不可用。",
            }
        try:
            candidate = PurePosixPath(normalized_reference)
            if (
                not normalized_reference
                or candidate.is_absolute()
                or ".." in candidate.parts
                or not candidate.parts
                or candidate.parts[0] != "references"
            ):
                raise ValueError("invalid reference path")
            canonical_reference = candidate.as_posix()
        except (TypeError, ValueError):
            return {
                "skill_id": normalized_skill_id,
                "reference": normalized_reference,
                "error": "业务 Skill 参考路径无效。",
            }
        skill = next(
            (
                item
                for item in self._snapshot.skills
                if item.skill_id == normalized_skill_id
            ),
            None,
        )
        if skill is None:
            return {
                "skill_id": normalized_skill_id,
                "reference": canonical_reference,
                "error": "该业务 Skill 未注册或当前不可用。",
            }
        reference = next(
            (
                item
                for item in skill.references
                if item.path == canonical_reference
            ),
            None,
        )
        if reference is None:
            return {
                "skill_id": normalized_skill_id,
                "reference": canonical_reference,
                "error": "该参考未在当前 Skill 快照中注册。",
                "available_references": [item.path for item in skill.references],
            }
        try:
            content = reference.content.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "skill_id": normalized_skill_id,
                "reference": canonical_reference,
                "error": "该参考不是可读取的 UTF-8 文本。",
            }
        return {
            "skill_id": normalized_skill_id,
            "reference": canonical_reference,
            "revision": self._snapshot.revision,
            "content_hash": reference.content_hash,
            "content": content,
        }

    @property
    def revision(self) -> str:
        return self._snapshot.revision

    @property
    def runtime_root(self) -> Path:
        """Return the content-addressed plugin directory used by Finance CC."""

        return self._snapshot.runtime_root

    def snapshot_metadata(self) -> Dict[str, Any]:
        """Return immutable-snapshot evidence without exposing Skill bodies."""

        snapshot = self._snapshot
        return {
            "format_version": _SNAPSHOT_FORMAT_VERSION,
            "revision": snapshot.revision,
            "plugin_name": snapshot.plugin_name,
            "skill_count": len(snapshot.skills),
            "skills": [
                {
                    "id": skill.skill_id,
                    "content_hash": skill.content_hash,
                    "allowed_tools": list(skill.allowed_tools),
                    "execution_budget": skill.execution_budget,
                    "reference_index": [
                        {
                            "path": reference.path,
                            "content_hash": reference.content_hash,
                        }
                        for reference in skill.references
                    ],
                    "companion_index": [
                        {
                            "path": companion.path,
                            "content_hash": companion.content_hash,
                        }
                        for companion in skill.companion_files
                    ],
                }
                for skill in snapshot.skills
            ],
        }

    def runtime_binding(self) -> Dict[str, Any]:
        """Return one internally consistent CC runtime binding."""

        snapshot = self._snapshot
        return {
            "revision": snapshot.revision,
            "runtime_root": snapshot.runtime_root,
            "skill_names": [
                f"{snapshot.plugin_name}:{skill.skill_id}"
                for skill in snapshot.skills
            ]
            if snapshot.plugin_name
            else [],
        }

    def validate_runtime_binding(self, binding: Mapping[str, Any]) -> None:
        """Fail closed when a content-addressed CC plugin has drifted."""

        if not isinstance(binding, Mapping):
            raise RuntimeError("invalid Finance Skill runtime binding")
        revision = _trim(binding.get("revision"))
        raw_root = _trim(binding.get("runtime_root"))
        raw_names = binding.get("skill_names")
        if not revision or not raw_root or not isinstance(raw_names, (list, tuple)):
            raise RuntimeError("invalid Finance Skill runtime binding")
        try:
            if len(revision) != 64:
                raise ValueError("invalid revision")
            int(revision, 16)
            runtime_root = Path(raw_root).absolute()
        except (OSError, TypeError, ValueError) as exc:
            raise RuntimeError("invalid Finance Skill runtime binding") from exc
        known = self._validated_runtime_bindings.get(revision)
        if known is None or runtime_root != known[0]:
            raise RuntimeError("invalid Finance Skill runtime binding")
        binding_names = tuple(_trim(item) for item in raw_names if _trim(item))
        known_names = known[1]
        selected_names = set(binding_names)
        if (
            len(selected_names) != len(binding_names)
            or any(item not in known_names for item in binding_names)
            or tuple(item for item in known_names if item in selected_names)
            != binding_names
        ):
            raise RuntimeError("Finance Skill runtime binding names mismatch")

    def turn_snapshot(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Return one revision-consistent routing and permission view."""

        snapshot = self._snapshot
        allowed = {
            _trim(item)
            for item in (allowed_skill_ids or [])
            if _trim(item)
        }
        restrict = allowed_skill_ids is not None
        skills = [
            skill
            for skill in snapshot.skills
            if not restrict or skill.skill_id in allowed
        ]
        return {
            "revision": snapshot.revision,
            "runtime_root": snapshot.runtime_root,
            "skill_names": [
                f"{snapshot.plugin_name}:{skill.skill_id}"
                for skill in skills
            ]
            if snapshot.plugin_name
            else [],
            "routing_summary": "\n".join(
                f"- {skill.skill_id}: {skill.description}"
                for skill in skills
            ),
            "allowed_tools_by_skill": {
                skill.skill_id: list(skill.allowed_tools)
                for skill in skills
            },
            "execution_budget_by_skill": {
                skill.skill_id: skill.execution_budget
                for skill in skills
            },
        }

    def discovery_snapshot(self) -> Dict[str, Any]:
        """Return one internally consistent, body-free Hub catalog view."""

        snapshot = self._snapshot
        return {
            "revision": snapshot.revision,
            "entries": [
                {
                    "id": skill.skill_id,
                    "category": skill.category,
                    "path": skill.relative_path,
                    "description": skill.description,
                    "allowed_tools": list(skill.allowed_tools),
                    "execution_budget": skill.execution_budget,
                }
                for skill in snapshot.skills
            ],
        }

    def studio_detail(self, skill_id: str) -> Dict[str, Any] | None:
        """Return a read-only Studio projection from the immutable snapshot.

        This is a presentation read model, not a second Skill contract.  Runtime
        loading continues to use ``load`` and ``load_reference`` so the Studio
        cannot change execution semantics or grant tools.
        """

        normalized = _trim(skill_id)
        skill = next(
            (item for item in self._snapshot.skills if item.skill_id == normalized),
            None,
        )
        if skill is None:
            return None

        interface: Mapping[str, Any] = {}
        interface_file = next(
            (
                item
                for item in skill.companion_files
                if item.path == "agents/openai.yaml"
            ),
            None,
        )
        if interface_file is not None:
            try:
                payload = yaml.safe_load(interface_file.content.decode("utf-8"))
                raw_interface = (
                    payload.get("interface")
                    if isinstance(payload, Mapping)
                    else None
                )
                if isinstance(raw_interface, Mapping):
                    interface = raw_interface
            except (UnicodeDecodeError, yaml.YAMLError):
                interface = {}

        references: list[Dict[str, Any]] = []
        for reference in skill.references:
            title = Path(reference.path).stem.replace("-", " ")
            try:
                for line in reference.content.decode("utf-8").splitlines():
                    if line.startswith("# ") and line[2:].strip():
                        title = line[2:].strip()
                        break
            except UnicodeDecodeError:
                pass
            references.append(
                {
                    "path": reference.path,
                    "title": title,
                    "content_hash": reference.content_hash,
                }
            )

        allowed_tools = list(skill.allowed_tools)
        return {
            "skill_id": skill.skill_id,
            "display_name": _trim(interface.get("display_name")) or skill.skill_id,
            "short_description": (
                _trim(interface.get("short_description")) or skill.description
            ),
            "default_prompt": _trim(interface.get("default_prompt")),
            "description": skill.description,
            "category": skill.category,
            "skill_markdown": skill.method,
            "content_hash": skill.content_hash,
            "revision": self._snapshot.revision,
            "references": references,
            "controls": {
                "execution_budget": skill.execution_budget,
                "supplemental_tools": allowed_tools,
                "web_search_enabled": any(
                    tool.endswith("general_search")
                    for tool in allowed_tools
                ),
            },
        }

    def reload(self) -> Dict[str, Any]:
        """Atomically replace the snapshot after an explicit publish/reload."""

        snapshot = self._compile_snapshot()
        self._snapshot = snapshot
        return self.snapshot_metadata()

    def routing_summary(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> str:
        return "\n".join(
            f"- {item['id']}: {item['description']}"
            for item in self.public_entries(
                allowed_skill_ids=allowed_skill_ids,
            )
        )

    def qualified_skill_names(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> list[str]:
        snapshot = self._snapshot
        plugin_name = snapshot.plugin_name
        if not plugin_name:
            return []
        allowed = {
            _trim(item)
            for item in (allowed_skill_ids or [])
            if _trim(item)
        }
        restrict = allowed_skill_ids is not None
        return [
            f"{plugin_name}:{skill.skill_id}"
            for skill in snapshot.skills
            if not restrict or skill.skill_id in allowed
        ]

    def allowed_tools_by_skill(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, list[str]]:
        """Return native Skill tool grants without exposing private loader fields."""

        return {
            entry["id"]: list(entry.get("_allowed_tools") or [])
            for entry in self.entries(allowed_skill_ids=allowed_skill_ids)
        }

    def execution_budget_by_skill(
        self,
        *,
        allowed_skill_ids: Optional[Iterable[str]] = None,
    ) -> Dict[str, str]:
        """Return the published runtime budget class for each Skill."""

        return {
            entry["id"]: _trim(entry.get("execution_budget")) or "standard"
            for entry in self.entries(allowed_skill_ids=allowed_skill_ids)
        }

    def _plugin_name(self) -> str:
        return self._snapshot.plugin_name

    def _compile_snapshot(self) -> _FinanceBusinessCatalogSnapshot:
        plugin_name, plugin_manifest = self._read_plugin_manifest()
        try:
            catalog_content = self.catalog_path.read_bytes()
            payload = json.loads(catalog_content.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("invalid Finance Skill catalog") from exc
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("skills"),
            list,
        ):
            raise RuntimeError("invalid Finance Skill catalog shape")
        raw_entries = payload["skills"]
        skills: list[_FinanceBusinessSkillSnapshot] = []
        seen: set[str] = set()
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                continue
            skill_id = _trim(raw.get("id"))
            relative_path = _trim(raw.get("path"))
            if (
                not skill_id
                or not relative_path
                or skill_id in seen
            ):
                continue
            skill_file = self._skill_file(relative_path)
            if skill_file is None or not skill_file.is_file():
                continue
            try:
                method_content = skill_file.read_bytes()
                method_text = method_content.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            method = method_text.strip()
            frontmatter = self._frontmatter(method_text)
            frontmatter_name = _trim(frontmatter.get("name"))
            description = _trim(frontmatter.get("description"))
            if frontmatter_name != skill_id:
                raise RuntimeError(
                    f"Finance Skill frontmatter name mismatch: {skill_id}"
                )
            if not description:
                raise RuntimeError(
                    f"Finance Skill frontmatter description missing: {skill_id}"
                )
            seen.add(skill_id)
            skills.append(
                _FinanceBusinessSkillSnapshot(
                    skill_id=skill_id,
                    category=_trim(raw.get("category")),
                    relative_path=relative_path,
                    description=description,
                    method=method,
                    method_content=method_content,
                    content_hash=self._content_hash(method_content),
                    allowed_tools=tuple(self._allowed_tools(frontmatter)),
                    execution_budget=self._execution_budget(frontmatter),
                    references=self._reference_index(skill_file.parent),
                    companion_files=self._companion_index(skill_file.parent),
                )
            )
        file_hashes = {
            ".claude-plugin/plugin.json": self._content_hash(plugin_manifest),
            "catalog.json": self._content_hash(catalog_content),
        }
        for skill in skills:
            file_hashes[f"{skill.relative_path}/SKILL.md"] = skill.content_hash
            for reference in skill.references:
                file_hashes[
                    f"{skill.relative_path}/{reference.path}"
                ] = reference.content_hash
            for companion in skill.companion_files:
                file_hashes[
                    f"{skill.relative_path}/{companion.path}"
                ] = companion.content_hash
        revision_payload = {
            "snapshot_format_version": _SNAPSHOT_FORMAT_VERSION,
            "plugin_name": plugin_name,
            "plugin_manifest_hash": self._content_hash(plugin_manifest),
            "catalog_content_hash": self._content_hash(catalog_content),
            "files": file_hashes,
            "skills": [
                {
                    "id": skill.skill_id,
                    "category": skill.category,
                    "path": skill.relative_path,
                    "description": skill.description,
                    "content_hash": skill.content_hash,
                    "allowed_tools": list(skill.allowed_tools),
                    "execution_budget": skill.execution_budget,
                    "references": [
                        {
                            "path": reference.path,
                            "content_hash": reference.content_hash,
                        }
                        for reference in skill.references
                    ],
                    "companion_files": [
                        {
                            "path": companion.path,
                            "content_hash": companion.content_hash,
                        }
                        for companion in skill.companion_files
                    ],
                }
                for skill in skills
            ],
        }
        revision_payload_content = json.dumps(
            revision_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        revision = self._content_hash(
            revision_payload_content
        )
        snapshot = _FinanceBusinessCatalogSnapshot(
            revision=revision,
            revision_payload=revision_payload_content,
            plugin_name=plugin_name,
            plugin_manifest=plugin_manifest,
            catalog_content=catalog_content,
            runtime_root=self.snapshot_root / revision,
            skills=tuple(skills),
        )
        self._materialize_snapshot(snapshot)
        self._validated_runtime_bindings[revision] = (
            snapshot.runtime_root.absolute(),
            tuple(
                f"{snapshot.plugin_name}:{skill.skill_id}"
                for skill in snapshot.skills
            ),
        )
        return snapshot

    def _read_plugin_manifest(self) -> tuple[str, bytes]:
        try:
            content = (
                self.root / ".claude-plugin" / "plugin.json"
            ).read_bytes()
            payload = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("invalid Finance Skill plugin manifest") from exc
        name = _trim(payload.get("name")) if isinstance(payload, Mapping) else ""
        if not name:
            raise RuntimeError("Finance Skill plugin manifest missing name")
        return name, content

    def _materialize_snapshot(
        self,
        snapshot: _FinanceBusinessCatalogSnapshot,
    ) -> None:
        """Build one immutable-by-convention CC plugin directory per revision."""

        target = snapshot.runtime_root
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._validate_materialized_snapshot(target, snapshot)
            self._make_snapshot_read_only(target)
            return

        temp_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{snapshot.revision[:12]}-",
                dir=str(self.snapshot_root),
            )
        )
        try:
            for relative_path, content in self._snapshot_files(snapshot).items():
                destination = temp_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            (temp_dir / ".snapshot.json").write_bytes(
                self._snapshot_marker(snapshot)
            )
            try:
                temp_dir.replace(target)
            except OSError:
                if not target.exists():
                    raise
                self._validate_materialized_snapshot(target, snapshot)
            self._validate_materialized_snapshot(target, snapshot)
            self._make_snapshot_read_only(target)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def _validate_materialized_snapshot(
        self,
        target: Path,
        snapshot: _FinanceBusinessCatalogSnapshot,
    ) -> None:
        self._validate_runtime_snapshot(
            target,
            expected_revision=snapshot.revision,
        )
        expected_files = self._snapshot_files(snapshot)
        try:
            for relative_path, expected_content in expected_files.items():
                if (target / relative_path).read_bytes() != expected_content:
                    raise ValueError("runtime snapshot content mismatch")
            if (target / ".snapshot.json").read_bytes() != self._snapshot_marker(
                snapshot
            ):
                raise ValueError("runtime snapshot marker mismatch")
        except (OSError, ValueError) as exc:
            raise RuntimeError("invalid Finance Skill runtime snapshot") from exc

    def _validate_runtime_snapshot(
        self,
        target: Path,
        *,
        expected_revision: str,
    ) -> Dict[str, Any]:
        """Validate a current or previously pinned content-addressed plugin."""

        try:
            if target.is_symlink() or not target.is_dir():
                raise ValueError("runtime snapshot root is invalid")
            resolved_target = target.resolve()
            resolved_target.relative_to(self.snapshot_root.resolve())
            if resolved_target.name != expected_revision:
                raise ValueError("runtime snapshot directory mismatch")
            marker_path = resolved_target / ".snapshot.json"
            if marker_path.is_symlink():
                raise ValueError("snapshot marker cannot be a symlink")
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if not isinstance(marker, Mapping):
                raise ValueError("snapshot marker shape mismatch")
            if int(marker.get("format_version") or 0) != _SNAPSHOT_FORMAT_VERSION:
                raise ValueError("runtime snapshot format mismatch")
            revision = _trim(marker.get("revision"))
            if revision != expected_revision:
                raise ValueError("runtime snapshot revision mismatch")
            revision_payload = marker.get("revision_payload")
            if not isinstance(revision_payload, Mapping):
                raise ValueError("runtime revision payload missing")
            canonical_payload = json.dumps(
                revision_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if self._content_hash(canonical_payload) != revision:
                raise ValueError("runtime snapshot digest mismatch")
            if (
                int(revision_payload.get("snapshot_format_version") or 0)
                != _SNAPSHOT_FORMAT_VERSION
            ):
                raise ValueError("runtime revision format mismatch")
            raw_files = revision_payload.get("files")
            if not isinstance(raw_files, Mapping) or not raw_files:
                raise ValueError("runtime file manifest missing")
            file_hashes: dict[str, str] = {}
            for raw_path, raw_hash in raw_files.items():
                relative_path = _trim(raw_path)
                content_hash = _trim(raw_hash).lower()
                pure_path = PurePosixPath(relative_path)
                if (
                    not relative_path
                    or pure_path.is_absolute()
                    or ".." in pure_path.parts
                    or relative_path != pure_path.as_posix()
                    or relative_path == ".snapshot.json"
                    or len(content_hash) != 64
                ):
                    raise ValueError("runtime file manifest entry invalid")
                int(content_hash, 16)
                file_hashes[relative_path] = content_hash
            actual_files: set[str] = set()
            for path in resolved_target.rglob("*"):
                if path.is_symlink():
                    raise ValueError("runtime snapshot cannot contain symlinks")
                if path.name.startswith("._"):
                    # AppleDouble metadata is not CC plugin content.
                    continue
                if path.is_file():
                    actual_files.add(path.relative_to(resolved_target).as_posix())
            if actual_files != {*file_hashes, ".snapshot.json"}:
                raise ValueError("runtime snapshot file set mismatch")
            for relative_path, content_hash in file_hashes.items():
                if self._content_hash(
                    (resolved_target / relative_path).read_bytes()
                ) != content_hash:
                    raise ValueError("runtime snapshot content hash mismatch")
            plugin_name = _trim(revision_payload.get("plugin_name"))
            raw_skills = revision_payload.get("skills")
            if not plugin_name or not isinstance(raw_skills, list):
                raise ValueError("runtime skill identity missing")
            skill_names = tuple(
                f"{plugin_name}:{skill_id}"
                for item in raw_skills
                if isinstance(item, Mapping)
                and (skill_id := _trim(item.get("id")))
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("invalid Finance Skill runtime snapshot") from exc
        return {
            "revision": expected_revision,
            "skill_names": skill_names,
        }

    @staticmethod
    def _snapshot_files(
        snapshot: _FinanceBusinessCatalogSnapshot,
    ) -> dict[str, bytes]:
        files: dict[str, bytes] = {
            ".claude-plugin/plugin.json": snapshot.plugin_manifest,
            "catalog.json": snapshot.catalog_content,
        }
        for skill in snapshot.skills:
            files[f"{skill.relative_path}/SKILL.md"] = skill.method_content
            for reference in skill.references:
                files[f"{skill.relative_path}/{reference.path}"] = reference.content
            for companion in skill.companion_files:
                files[f"{skill.relative_path}/{companion.path}"] = companion.content
        return files

    @staticmethod
    def _snapshot_marker(snapshot: _FinanceBusinessCatalogSnapshot) -> bytes:
        return json.dumps(
            {
                "format_version": _SNAPSHOT_FORMAT_VERSION,
                "revision": snapshot.revision,
                "revision_payload": json.loads(
                    snapshot.revision_payload.decode("utf-8")
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

    @staticmethod
    def _make_snapshot_read_only(target: Path) -> None:
        """Prevent accidental in-place edits after atomic materialization."""

        try:
            paths = sorted(
                target.rglob("*"),
                key=lambda item: len(item.parts),
                reverse=True,
            )
            for path in paths:
                if path.is_symlink():
                    raise ValueError("runtime snapshot cannot contain symlinks")
                path.chmod(path.stat().st_mode & ~0o222)
            target.chmod(target.stat().st_mode & ~0o222)
        except (OSError, ValueError) as exc:
            raise RuntimeError("cannot seal Finance Skill runtime snapshot") from exc

    def _skill_file(self, relative_path: str) -> Optional[Path]:
        candidate = (self.root / relative_path / "SKILL.md").resolve()
        root = self.root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _frontmatter(text: str) -> Mapping[str, Any]:
        if not text.startswith("---\n"):
            return {}
        marker = text.find("\n---\n", 4)
        if marker < 0:
            return {}
        try:
            payload = yaml.safe_load(text[4:marker])
        except yaml.YAMLError:
            return {}
        return payload if isinstance(payload, Mapping) else {}

    def _reference_index(
        self,
        skill_dir: Path,
    ) -> tuple[_FinanceSkillReferenceSnapshot, ...]:
        references_dir = skill_dir / "references"
        if not references_dir.is_dir():
            return ()
        resolved_skill_dir = skill_dir.resolve()
        references: list[_FinanceSkillReferenceSnapshot] = []
        for path in sorted(references_dir.rglob("*")):
            if (
                path.is_symlink()
                or path.name.startswith("._")
                or not path.is_file()
            ):
                continue
            resolved_path = path.resolve()
            try:
                relative_path = resolved_path.relative_to(resolved_skill_dir)
            except ValueError:
                continue
            try:
                content = resolved_path.read_bytes()
            except OSError:
                continue
            references.append(
                _FinanceSkillReferenceSnapshot(
                    path=relative_path.as_posix(),
                    content_hash=self._content_hash(content),
                    content=content,
                )
            )
        return tuple(references)

    def _companion_index(
        self,
        skill_dir: Path,
    ) -> tuple[_FinanceSkillCompanionSnapshot, ...]:
        resolved_skill_dir = skill_dir.resolve()
        companions: list[_FinanceSkillCompanionSnapshot] = []
        for path in sorted(skill_dir.rglob("*")):
            if (
                path.is_symlink()
                or path.name.startswith("._")
                or not path.is_file()
            ):
                continue
            resolved_path = path.resolve()
            try:
                relative_path = resolved_path.relative_to(resolved_skill_dir)
            except ValueError:
                continue
            if relative_path.as_posix() == "SKILL.md":
                continue
            if relative_path.parts and relative_path.parts[0] == "references":
                continue
            if (
                not relative_path.parts
                or relative_path.parts[0] not in _ALLOWED_COMPANION_DIRS
            ):
                # Finance business Skills are natural-language methods.  The
                # first runtime slice does not package arbitrary executable
                # scripts from a Skill directory.
                continue
            try:
                content = resolved_path.read_bytes()
            except OSError:
                continue
            companions.append(
                _FinanceSkillCompanionSnapshot(
                    path=relative_path.as_posix(),
                    content_hash=self._content_hash(content),
                    content=content,
                )
            )
        return tuple(companions)

    @staticmethod
    def _content_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _allowed_tools(frontmatter: Mapping[str, Any]) -> list[str]:
        raw = frontmatter.get("allowed-tools")
        if isinstance(raw, str):
            values = raw.replace(",", " ").split()
        elif isinstance(raw, list):
            values = raw
        else:
            values = []
        seen: set[str] = set()
        normalized: list[str] = []
        for item in values:
            tool_name = _trim(item)
            if not tool_name or tool_name in seen:
                continue
            seen.add(tool_name)
            normalized.append(tool_name)
        return normalized

    @staticmethod
    def _execution_budget(frontmatter: Mapping[str, Any]) -> str:
        value = _trim(frontmatter.get("execution-budget")).lower() or "standard"
        if value not in {"standard", "long"}:
            raise RuntimeError("Finance Skill execution-budget must be standard or long")
        return value
