import json
from pathlib import Path
from typing import Any, Dict, List

from src.services.runtime_artifact_service import RuntimeArtifactService


class ApplicationStudioError(ValueError):
    pass


class ApplicationStudioService:
    def __init__(self, applications_root: str = "src/applications") -> None:
        self.applications_root = Path(applications_root)
        self.runtime_artifacts = RuntimeArtifactService(applications_root=applications_root)

    def list_applications(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if not self.applications_root.exists():
            return rows
        for app_dir in sorted(self.applications_root.iterdir()):
            if not app_dir.is_dir():
                continue
            try:
                bundle = self.load_application_bundle(app_dir.name)
            except Exception:
                continue
            files = bundle.get("files") or {}
            config = files.get("application_config") or {}
            rows.append(
                {
                    "application_name": app_dir.name,
                    "display_name": str(config.get("display_name") or app_dir.name).strip(),
                    "status": str(config.get("status") or "").strip(),
                    "version": str(config.get("version") or "").strip(),
                    "enabled": bool(config.get("enabled", True)),
                    "domain": str(config.get("domain") or "").strip(),
                    "default_agents": [str(x).strip() for x in config.get("default_agents", []) if str(x).strip()],
                    "default_skills": [str(x).strip() for x in config.get("default_skills", []) if str(x).strip()],
                    "default_tools": [str(x).strip() for x in config.get("default_tools", []) if str(x).strip()],
                }
            )
        return rows

    def load_application_bundle(self, application_name: str) -> Dict[str, Any]:
        normalized = str(application_name or "").strip()
        if not normalized:
            raise ApplicationStudioError("application_name 不能为空")
        app_dir = self.applications_root / normalized
        markdown_path = app_dir / "APPLICATION.md"
        config_path = app_dir / "application.json"
        schema_path = app_dir / "schema.json"
        if not markdown_path.exists():
            raise FileNotFoundError(f"application markdown 不存在: {markdown_path}")

        config_obj = self._load_json_if_exists(config_path)
        schema_obj = self._load_json_if_exists(schema_path)
        return {
            "application_name": normalized,
            "files": {
                "application_md_text": markdown_path.read_text(encoding="utf-8"),
                "application_config_text": json.dumps(config_obj, ensure_ascii=False, indent=2),
                "schema_text": json.dumps(schema_obj, ensure_ascii=False, indent=2),
                "application_config": config_obj,
                "schema": schema_obj,
            },
            "meta": {
                "application_dir": str(app_dir),
            },
        }

    def build_application_template_bundle(self, application_name: str) -> Dict[str, Any]:
        normalized = str(application_name or "").strip()
        if not normalized:
            raise ApplicationStudioError("application_name 不能为空")
        title = normalized.replace("_", " ").strip() or "new application"
        markdown_text = "\n".join(
            [
                f"# {title.title()}",
                "",
                "## 目标",
                "",
                "请描述这个 application 面向什么用户和业务场景。",
                "",
                "## 默认入口能力",
                "",
                "- 对话入口",
                "- 任务入口",
                "- 工作区跳转",
                "",
                "## 边界",
                "",
                "- 不在这里定义复杂业务方法",
                "- 不在这里定义角色人格",
                "",
            ]
        )
        config_obj = {
            "name": normalized,
            "display_name": title.title(),
            "version": "v1",
            "status": "draft",
            "enabled": True,
            "owner": "applications",
            "domain": "generic",
            "capabilities": [],
            "tags": [],
            "keywords": [],
            "default_agents": [],
            "default_skills": [],
            "default_tools": [],
        }
        schema_obj = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{normalized} Session Payload",
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
        return {
            "application_name": normalized,
            "files": {
                "application_md_text": markdown_text,
                "application_config_text": json.dumps(config_obj, ensure_ascii=False, indent=2),
                "schema_text": json.dumps(schema_obj, ensure_ascii=False, indent=2),
                "application_config": config_obj,
                "schema": schema_obj,
            },
            "meta": {
                "template": True,
                "application_dir": str(self.applications_root / normalized),
            },
        }

    def save_application_bundle(
        self,
        *,
        application_name: str,
        application_md_text: str,
        application_config_text: str,
        schema_text: str,
    ) -> Dict[str, Any]:
        normalized = str(application_name or "").strip()
        if not normalized:
            raise ApplicationStudioError("application_name 不能为空")
        app_dir = self.applications_root / normalized
        app_dir.mkdir(parents=True, exist_ok=True)

        markdown_text = str(application_md_text or "")
        if not markdown_text.strip():
            raise ApplicationStudioError("APPLICATION.md 不能为空")

        config_obj = self._parse_required_json(application_config_text, "application.json")
        if str(config_obj.get("name") or "").strip() != normalized:
            raise ApplicationStudioError("application.json.name 必须与 application_name 一致")

        schema_obj = self._parse_required_json(schema_text, "schema.json")

        (app_dir / "APPLICATION.md").write_text(markdown_text, encoding="utf-8")
        (app_dir / "application.json").write_text(
            json.dumps(config_obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (app_dir / "schema.json").write_text(
            json.dumps(schema_obj, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.runtime_artifacts.sync_application(
            normalized,
            source_type="ui",
            changed_by="application_studio",
        )
        return self.load_application_bundle(normalized)

    @staticmethod
    def _load_json_if_exists(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _parse_required_json(text: str, label: str) -> Dict[str, Any]:
        try:
            obj = json.loads(text or "{}")
        except Exception as exc:
            raise ApplicationStudioError(f"{label} 不是合法 JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ApplicationStudioError(f"{label} 顶层必须是对象")
        return obj
