import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql

from src.skill_runtime.availability import legacy_skill_is_active
from src.services.capability_search_service import CapabilitySearchService
from src.tools.registry import TOOL_REGISTRY
from src.utils.system_db_utils import SystemDbUtils


ARTIFACT_TABLE = "aiia_runtime_artifact"
REVISION_TABLE = "aiia_runtime_artifact_revision"
EDGE_TABLE = "aiia_runtime_artifact_edge"


class RuntimeArtifactError(ValueError):
    pass


class RuntimeArtifactService:
    def __init__(
        self,
        *,
        tool_definitions_dir: str = "src/tools/definitions",
        tool_schemas_dir: str = "src/tools/schemas",
        tool_specs_dir: str = "src/tools/specs",
        tool_hub_path: str = "src/tools/tool_hub.json",
        skills_root: str = "src/skills",
        applications_root: str = "src/applications",
        agents_root: str = "src/agents",
    ) -> None:
        self.tool_definitions_dir = Path(tool_definitions_dir)
        self.tool_schemas_dir = Path(tool_schemas_dir)
        self.tool_specs_dir = Path(tool_specs_dir)
        self.tool_hub_path = Path(tool_hub_path)
        self.skills_root = Path(skills_root)
        self.applications_root = Path(applications_root)
        self.agents_root = Path(agents_root)

    @staticmethod
    def _list_bundle_dirs(root: Path, required_file: str) -> List[str]:
        if not root.exists():
            return []
        names: List[str] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if (child / required_file).exists():
                names.append(child.name)
        return names

    @staticmethod
    def _json_text(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"

    @staticmethod
    def _content_hash(*parts: str) -> str:
        digest = hashlib.sha1()
        for part in parts:
            digest.update(str(part or "").encode("utf-8"))
            digest.update(b"\n---\n")
        return digest.hexdigest()

    @staticmethod
    def _trim(text: Any) -> str:
        return str(text or "").strip()

    @staticmethod
    def _infer_skill_description(markdown_text: str) -> str:
        for line in str(markdown_text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            return line[:255]
        return ""

    @staticmethod
    def _infer_markdown_description(markdown_text: str) -> str:
        for line in str(markdown_text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            return line[:255]
        return ""

    @staticmethod
    def _load_json_if_exists(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_tool_hub_map(self) -> Dict[str, Dict[str, Any]]:
        if not self.tool_hub_path.exists():
            return {}
        raw = json.loads(self.tool_hub_path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else []
        result: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = self._trim(item.get("name"))
            if name:
                result[name] = item
        return result

    @staticmethod
    def _guess_tool_target(tool_name: str) -> str:
        return str(TOOL_REGISTRY.get(tool_name) or "").strip()

    def _build_fallback_tool_definition(
        self,
        *,
        tool_name: str,
        schema_obj: Dict[str, Any],
        spec_obj: Dict[str, Any],
        hub_entry: Dict[str, Any],
    ) -> Dict[str, Any]:
        display_name = self._trim(hub_entry.get("display_name")) or tool_name
        description = (
            self._trim(hub_entry.get("description"))
            or self._trim(spec_obj.get("purpose"))
            or f"{display_name} 的工具定义由 registry/spec/schema 自动推断生成。"
        )
        target = self._guess_tool_target(tool_name)
        return {
            "name": tool_name,
            "version": "v1",
            "status": "active",
            "identity": {
                "display_name": display_name,
                "description": description,
                "owner": "tools",
                "domain": "generic",
            },
            "capabilities": hub_entry.get("capabilities", []),
            "tags": hub_entry.get("keywords", []),
            "safety": {
                "side_effect": "none",
            },
            "profiles": {
                "real": {
                    "enabled": bool(target),
                    "implementation": {
                        "kind": "python",
                        "target": target,
                    },
                }
            },
            "input_schema": schema_obj.get("input_schema") if isinstance(schema_obj.get("input_schema"), dict) else {},
            "output_schema_ref": "",
            "examples": spec_obj.get("examples", []),
        }

    def _upsert_artifact(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        db = SystemDbUtils()
        try:
            with db.conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    f"""
                    SELECT artifact_id, current_revision_no
                    FROM {ARTIFACT_TABLE}
                    WHERE artifact_type = %s
                      AND name = %s
                      AND version = %s
                    LIMIT 1
                    """,
                    (payload["artifact_type"], payload["name"], payload["version"]),
                )
                row = cursor.fetchone()

                if row:
                    artifact_id = int(row["artifact_id"])
                    current_revision_no = int(row.get("current_revision_no") or 0)
                    cursor.execute(
                        f"""
                        UPDATE {ARTIFACT_TABLE}
                        SET status = %s,
                            display_name = %s,
                            description = %s,
                            owner = %s,
                            domain = %s,
                            capabilities_json = %s,
                            tags_json = %s,
                            keywords_json = %s,
                            side_effect_level = %s,
                            enabled = %s,
                            implementation_kind = %s,
                            implementation_target = %s,
                            source_manifest_json = %s,
                            retrieval_text = %s,
                            sync_status = %s,
                            last_synced_at = NOW(),
                            updated_by = %s,
                            updated_at = NOW()
                        WHERE artifact_id = %s
                        """,
                        (
                            payload["status"],
                            payload["display_name"],
                            payload["description"],
                            payload["owner"],
                            payload["domain"],
                            payload["capabilities_json"],
                            payload["tags_json"],
                            payload["keywords_json"],
                            payload["side_effect_level"],
                            payload["enabled"],
                            payload["implementation_kind"],
                            payload["implementation_target"],
                            payload["source_manifest_json"],
                            payload["retrieval_text"],
                            "synced",
                            payload["updated_by"],
                            artifact_id,
                        ),
                    )
                else:
                    cursor.execute(
                        f"""
                        INSERT INTO {ARTIFACT_TABLE} (
                          artifact_type, name, version, status, display_name, description,
                          owner, domain, capabilities_json, tags_json, keywords_json,
                          side_effect_level, enabled, implementation_kind, implementation_target,
                          source_manifest_json, retrieval_text, current_revision_no, sync_status,
                          last_synced_at, created_by, updated_by
                        ) VALUES (
                          %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s,
                          %s, %s, %s, %s,
                          %s, %s, %s, %s,
                          NOW(), %s, %s
                        )
                        """,
                        (
                            payload["artifact_type"],
                            payload["name"],
                            payload["version"],
                            payload["status"],
                            payload["display_name"],
                            payload["description"],
                            payload["owner"],
                            payload["domain"],
                            payload["capabilities_json"],
                            payload["tags_json"],
                            payload["keywords_json"],
                            payload["side_effect_level"],
                            payload["enabled"],
                            payload["implementation_kind"],
                            payload["implementation_target"],
                            payload["source_manifest_json"],
                            payload["retrieval_text"],
                            0,
                            "synced",
                            payload["created_by"],
                            payload["updated_by"],
                        ),
                    )
                    artifact_id = int(cursor.lastrowid)
                    current_revision_no = 0

                cursor.execute(
                    f"""
                    SELECT revision_id, content_hash, revision_no
                    FROM {REVISION_TABLE}
                    WHERE artifact_id = %s
                    ORDER BY revision_no DESC
                    LIMIT 1
                    """,
                    (artifact_id,),
                )
                latest_revision = cursor.fetchone()
                latest_hash = self._trim((latest_revision or {}).get("content_hash"))
                if latest_hash != payload["content_hash"]:
                    next_revision_no = current_revision_no + 1
                    cursor.execute(
                        f"""
                        INSERT INTO {REVISION_TABLE} (
                          artifact_id, revision_no, source_type, definition_json,
                          schema_json, spec_json, markdown_text, content_hash,
                          change_summary, created_by
                        ) VALUES (
                          %s, %s, %s, %s,
                          %s, %s, %s, %s,
                          %s, %s
                        )
                        """,
                        (
                            artifact_id,
                            next_revision_no,
                            payload["source_type"],
                            payload["definition_text"],
                            payload["schema_text"],
                            payload["spec_text"],
                            payload["markdown_text"],
                            payload["content_hash"],
                            payload["change_summary"],
                            payload["updated_by"],
                        ),
                    )
                    cursor.execute(
                        f"""
                        UPDATE {ARTIFACT_TABLE}
                        SET current_revision_no = %s,
                            updated_at = NOW()
                        WHERE artifact_id = %s
                        """,
                        (next_revision_no, artifact_id),
                    )
                    current_revision_no = next_revision_no

            db.conn.commit()
            return {
                "artifact_id": artifact_id,
                "current_revision_no": current_revision_no,
            }
        finally:
            db.close_db()

    def _replace_edges(self, *, artifact_id: int, edge_names: List[str]) -> None:
        db = SystemDbUtils()
        try:
            with db.conn.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {EDGE_TABLE} WHERE from_artifact_id = %s AND edge_type = 'uses'",
                    (artifact_id,),
                )
                if edge_names:
                    placeholders = ",".join(["%s"] * len(edge_names))
                    cursor.execute(
                        f"""
                        SELECT artifact_id, name
                        FROM {ARTIFACT_TABLE}
                        WHERE name IN ({placeholders})
                          AND enabled = 1
                        """,
                        tuple(edge_names),
                    )
                    rows = cursor.fetchall() or []
                    edge_order = 0
                    for row in rows:
                        to_artifact_id = int(row[0])
                        cursor.execute(
                            f"""
                            INSERT INTO {EDGE_TABLE} (
                              from_artifact_id, to_artifact_id, edge_type, edge_order, condition_text, enabled
                            ) VALUES (%s, %s, 'uses', %s, '', 1)
                            """,
                            (artifact_id, to_artifact_id, edge_order),
                        )
                        edge_order += 1
            db.conn.commit()
        finally:
            db.close_db()

    def sync_tool(self, tool_name: str, *, source_type: str = "file_sync", changed_by: str = "system") -> Dict[str, Any]:
        normalized = self._trim(tool_name)
        if not normalized:
            raise RuntimeArtifactError("tool_name 不能为空")
        definition_path = self.tool_definitions_dir / f"{normalized}.tool.json"
        schema_obj = self._load_json_if_exists(self.tool_schemas_dir / f"{normalized}.schema.json")
        spec_obj = self._load_json_if_exists(self.tool_specs_dir / f"{normalized}.spec.json")
        hub_entry = self._load_tool_hub_map().get(normalized) or {}
        if definition_path.exists():
            definition = json.loads(definition_path.read_text(encoding="utf-8"))
        else:
            if normalized not in TOOL_REGISTRY:
                raise FileNotFoundError(f"tool definition 不存在且 registry 中未注册: {definition_path}")
            definition = self._build_fallback_tool_definition(
                tool_name=normalized,
                schema_obj=schema_obj,
                spec_obj=spec_obj,
                hub_entry=hub_entry,
            )

        identity = definition.get("identity") if isinstance(definition.get("identity"), dict) else {}
        safety = definition.get("safety") if isinstance(definition.get("safety"), dict) else {}
        profiles = definition.get("profiles") if isinstance(definition.get("profiles"), dict) else {}
        real_profile = profiles.get("real") if isinstance(profiles.get("real"), dict) else {}
        implementation = real_profile.get("implementation") if isinstance(real_profile.get("implementation"), dict) else {}

        definition_text = self._json_text(definition)
        schema_text = self._json_text(schema_obj)
        spec_text = self._json_text(spec_obj)
        markdown_text = ""
        retrieval_text = "\n".join(
            part
            for part in [
                self._trim(normalized),
                self._trim(identity.get("display_name")),
                self._trim(identity.get("description")),
                self._trim(spec_obj.get("purpose")),
                "\n".join(str(x) for x in spec_obj.get("best_for", []) if self._trim(x)),
                "\n".join(str(x) for x in definition.get("capabilities", []) if self._trim(x)),
                "\n".join(str(x) for x in definition.get("tags", []) if self._trim(x)),
                "\n".join(str(x) for x in hub_entry.get("keywords", []) if self._trim(x)),
            ]
            if part
        )
        content_hash = self._content_hash(definition_text, schema_text, spec_text, markdown_text)
        result = self._upsert_artifact(
            {
                "artifact_type": "tool",
                "name": normalized,
                "version": self._trim(definition.get("version")) or "v1",
                "status": self._trim(definition.get("status")) or "active",
                "display_name": self._trim(identity.get("display_name")) or normalized,
                "description": self._trim(identity.get("description")),
                "owner": self._trim(identity.get("owner")) or "tools",
                "domain": self._trim(identity.get("domain")) or "generic",
                "capabilities_json": json.dumps(definition.get("capabilities", []), ensure_ascii=False),
                "tags_json": json.dumps(definition.get("tags", []), ensure_ascii=False),
                "keywords_json": json.dumps(hub_entry.get("keywords", []), ensure_ascii=False),
                "side_effect_level": self._trim(safety.get("side_effect")) or "none",
                "enabled": 1 if bool(real_profile.get("enabled")) else 0,
                "implementation_kind": self._trim(implementation.get("kind")),
                "implementation_target": self._trim(implementation.get("target")),
                "source_manifest_json": json.dumps(
                    {
                        "definition_path": str(definition_path),
                        "schema_path": str(self.tool_schemas_dir / f"{normalized}.schema.json"),
                        "spec_path": str(self.tool_specs_dir / f"{normalized}.spec.json"),
                        "tool_hub_path": str(self.tool_hub_path),
                    },
                    ensure_ascii=False,
                ),
                "retrieval_text": retrieval_text,
                "source_type": source_type,
                "definition_text": definition_text,
                "schema_text": schema_text,
                "spec_text": spec_text,
                "markdown_text": markdown_text,
                "content_hash": content_hash,
                "change_summary": f"sync tool {normalized}",
                "created_by": changed_by,
                "updated_by": changed_by,
            }
        )
        return {
            "artifact_type": "tool",
            "name": normalized,
            **result,
        }

    def sync_skill(self, skill_name: str, *, source_type: str = "file_sync", changed_by: str = "system") -> Dict[str, Any]:
        normalized = self._trim(skill_name)
        if not normalized:
            raise RuntimeArtifactError("skill_name 不能为空")
        skill_dir = self.skills_root / normalized
        skill_md_path = skill_dir / "SKILL.md"
        config_path = skill_dir / "skill.json"
        schema_path = skill_dir / "schema.json"
        if not skill_md_path.exists():
            raise FileNotFoundError(f"skill markdown 不存在: {skill_md_path}")

        markdown_text = skill_md_path.read_text(encoding="utf-8")
        config_obj = self._load_json_if_exists(config_path)
        schema_obj = self._load_json_if_exists(schema_path)
        definition_text = self._json_text(config_obj)
        schema_text = self._json_text(schema_obj)
        spec_text = ""
        retrieval_text = "\n".join(
            part
            for part in [
                normalized,
                CapabilitySearchService.build_skill_embedding_text(config_obj)
                or self._trim(config_obj.get("purpose"))
                or self._trim(config_obj.get("description"))
                or self._infer_skill_description(markdown_text),
            ]
            if part
        )
        content_hash = self._content_hash(definition_text, schema_text, spec_text, markdown_text)
        source_status = self._trim(config_obj.get("status")) or "active"
        lifecycle = self._trim(
            (
                config_obj.get("availability")
                if isinstance(config_obj.get("availability"), dict)
                else {}
            ).get("lifecycle")
        ).lower() or "active"
        artifact_status = (
            "deprecated"
            if source_status == "active" and lifecycle != "active"
            else source_status
        )

        result = self._upsert_artifact(
            {
                "artifact_type": "skill",
                "name": normalized,
                "version": "v1",
                "status": artifact_status,
                "display_name": normalized,
                "description": self._trim(config_obj.get("purpose")) or self._trim(config_obj.get("description")) or self._infer_skill_description(markdown_text),
                "owner": self._trim(config_obj.get("owner")) or "skills",
                "domain": "generic",
                "capabilities_json": json.dumps([], ensure_ascii=False),
                "tags_json": json.dumps(config_obj.get("tags", []), ensure_ascii=False),
                "keywords_json": json.dumps([], ensure_ascii=False),
                "side_effect_level": "none",
                "enabled": 1 if legacy_skill_is_active(config_obj) else 0,
                "implementation_kind": "skill_bundle",
                "implementation_target": str(skill_dir),
                "source_manifest_json": json.dumps(
                    {
                        "skill_md_path": str(skill_md_path),
                        "skill_config_path": str(config_path),
                        "schema_path": str(schema_path),
                    },
                    ensure_ascii=False,
                ),
                "retrieval_text": retrieval_text,
                "source_type": source_type,
                "definition_text": definition_text,
                "schema_text": schema_text,
                "spec_text": spec_text,
                "markdown_text": markdown_text,
                "content_hash": content_hash,
                "change_summary": f"sync skill {normalized}",
                "created_by": changed_by,
                "updated_by": changed_by,
            }
        )
        self._replace_edges(
            artifact_id=int(result["artifact_id"]),
            edge_names=[self._trim(name) for name in config_obj.get("tools", []) if self._trim(name)],
        )
        return {
            "artifact_type": "skill",
            "name": normalized,
            **result,
        }

    def sync_application(
        self,
        app_name: str,
        *,
        source_type: str = "file_sync",
        changed_by: str = "system",
    ) -> Dict[str, Any]:
        normalized = self._trim(app_name)
        if not normalized:
            raise RuntimeArtifactError("app_name 不能为空")
        app_dir = self.applications_root / normalized
        markdown_path = app_dir / "APPLICATION.md"
        config_path = app_dir / "application.json"
        schema_path = app_dir / "schema.json"
        if not markdown_path.exists():
            raise FileNotFoundError(f"application markdown 不存在: {markdown_path}")

        markdown_text = markdown_path.read_text(encoding="utf-8")
        config_obj = self._load_json_if_exists(config_path)
        schema_obj = self._load_json_if_exists(schema_path)
        definition_text = self._json_text(config_obj)
        schema_text = self._json_text(schema_obj)
        spec_text = ""
        description = self._infer_markdown_description(markdown_text)
        retrieval_text = "\n".join(
            part
            for part in [
                normalized,
                self._trim(config_obj.get("display_name")),
                description,
                markdown_text,
                self._trim(config_obj.get("default_agent")),
                "\n".join(str(x) for x in config_obj.get("default_skills", []) if self._trim(x)),
                "\n".join(str(x) for x in config_obj.get("default_tools", []) if self._trim(x)),
            ]
            if part
        )
        content_hash = self._content_hash(definition_text, schema_text, spec_text, markdown_text)

        result = self._upsert_artifact(
            {
                "artifact_type": "application",
                "name": normalized,
                "version": self._trim(config_obj.get("version")) or "v1",
                "status": self._trim(config_obj.get("status")) or "active",
                "display_name": self._trim(config_obj.get("display_name")) or normalized,
                "description": description,
                "owner": self._trim(config_obj.get("owner")) or "applications",
                "domain": self._trim(config_obj.get("domain")) or "generic",
                "capabilities_json": json.dumps(config_obj.get("capabilities", []), ensure_ascii=False),
                "tags_json": json.dumps(config_obj.get("tags", []), ensure_ascii=False),
                "keywords_json": json.dumps(config_obj.get("keywords", []), ensure_ascii=False),
                "side_effect_level": "none",
                "enabled": 1 if config_obj.get("enabled", True) else 0,
                "implementation_kind": "application_bundle",
                "implementation_target": str(app_dir),
                "source_manifest_json": json.dumps(
                    {
                        "application_md_path": str(markdown_path),
                        "application_config_path": str(config_path),
                        "schema_path": str(schema_path),
                    },
                    ensure_ascii=False,
                ),
                "retrieval_text": retrieval_text,
                "source_type": source_type,
                "definition_text": definition_text,
                "schema_text": schema_text,
                "spec_text": spec_text,
                "markdown_text": markdown_text,
                "content_hash": content_hash,
                "change_summary": f"sync application {normalized}",
                "created_by": changed_by,
                "updated_by": changed_by,
            }
        )
        self._replace_edges(
            artifact_id=int(result["artifact_id"]),
            edge_names=[
                self._trim(name)
                for name in (
                    [config_obj.get("default_agent")]
                    + list(config_obj.get("default_skills", []) or [])
                    + list(config_obj.get("default_tools", []) or [])
                )
                if self._trim(name)
            ],
        )
        return {
            "artifact_type": "application",
            "name": normalized,
            **result,
        }

    def list_applications(self) -> List[str]:
        return self._list_bundle_dirs(self.applications_root, "APPLICATION.md")

    def sync_agent(
        self,
        agent_name: str,
        *,
        source_type: str = "file_sync",
        changed_by: str = "system",
    ) -> Dict[str, Any]:
        normalized = self._trim(agent_name)
        if not normalized:
            raise RuntimeArtifactError("agent_name 不能为空")
        agent_dir = self.agents_root / normalized
        soul_path = agent_dir / "SOUL.md"
        config_path = agent_dir / "agent.json"
        schema_path = agent_dir / "schema.json"
        if not soul_path.exists():
            raise FileNotFoundError(f"agent soul markdown 不存在: {soul_path}")

        markdown_text = soul_path.read_text(encoding="utf-8")
        config_obj = self._load_json_if_exists(config_path)
        schema_obj = self._load_json_if_exists(schema_path)
        definition_text = self._json_text(config_obj)
        schema_text = self._json_text(schema_obj)
        spec_text = ""
        description = self._infer_markdown_description(markdown_text)
        retrieval_text = "\n".join(
            part
            for part in [
                normalized,
                self._trim(config_obj.get("display_name")),
                self._trim(config_obj.get("role")),
                description,
                markdown_text,
                "\n".join(str(x) for x in config_obj.get("responsibilities", []) if self._trim(x)),
                "\n".join(str(x) for x in config_obj.get("skills", []) if self._trim(x)),
                "\n".join(str(x) for x in config_obj.get("tools", []) if self._trim(x)),
                "\n".join(str(x) for x in config_obj.get("handoff_agents", []) if self._trim(x)),
            ]
            if part
        )
        content_hash = self._content_hash(definition_text, schema_text, spec_text, markdown_text)

        result = self._upsert_artifact(
            {
                "artifact_type": "agent",
                "name": normalized,
                "version": self._trim(config_obj.get("version")) or "v1",
                "status": self._trim(config_obj.get("status")) or "active",
                "display_name": self._trim(config_obj.get("display_name")) or normalized,
                "description": description,
                "owner": self._trim(config_obj.get("owner")) or "agents",
                "domain": self._trim(config_obj.get("domain")) or "generic",
                "capabilities_json": json.dumps(config_obj.get("capabilities", []), ensure_ascii=False),
                "tags_json": json.dumps(config_obj.get("tags", []), ensure_ascii=False),
                "keywords_json": json.dumps(config_obj.get("keywords", []), ensure_ascii=False),
                "side_effect_level": "none",
                "enabled": 1 if config_obj.get("enabled", True) else 0,
                "implementation_kind": "agent_bundle",
                "implementation_target": str(agent_dir),
                "source_manifest_json": json.dumps(
                    {
                        "soul_md_path": str(soul_path),
                        "agent_config_path": str(config_path),
                        "schema_path": str(schema_path),
                    },
                    ensure_ascii=False,
                ),
                "retrieval_text": retrieval_text,
                "source_type": source_type,
                "definition_text": definition_text,
                "schema_text": schema_text,
                "spec_text": spec_text,
                "markdown_text": markdown_text,
                "content_hash": content_hash,
                "change_summary": f"sync agent {normalized}",
                "created_by": changed_by,
                "updated_by": changed_by,
            }
        )
        self._replace_edges(
            artifact_id=int(result["artifact_id"]),
            edge_names=[
                self._trim(name)
                for name in (
                    list(config_obj.get("skills", []) or [])
                    + list(config_obj.get("tools", []) or [])
                    + list(config_obj.get("handoff_agents", []) or [])
                )
                if self._trim(name)
            ],
        )
        return {
            "artifact_type": "agent",
            "name": normalized,
            **result,
        }

    def list_agents(self) -> List[str]:
        return self._list_bundle_dirs(self.agents_root, "SOUL.md")

    def sync_all_design_time_artifacts(
        self,
        *,
        source_type: str = "file_sync",
        changed_by: str = "system",
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for app_name in self.list_applications():
            results.append(
                self.sync_application(app_name, source_type=source_type, changed_by=changed_by)
            )
        for agent_name in self.list_agents():
            results.append(
                self.sync_agent(agent_name, source_type=source_type, changed_by=changed_by)
            )
        for skill_name in self._list_bundle_dirs(self.skills_root, "SKILL.md"):
            results.append(self.sync_skill(skill_name, source_type=source_type, changed_by=changed_by))
        for path in sorted(self.tool_definitions_dir.glob("*.tool.json")):
            tool_name = path.name.replace(".tool.json", "")
            results.append(self.sync_tool(tool_name, source_type=source_type, changed_by=changed_by))
        return results
