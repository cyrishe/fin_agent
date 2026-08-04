import json
from pathlib import Path
from typing import Any, Dict, List

from src.services.capability_search_service import CapabilitySearchService
from src.services.runtime_artifact_service import RuntimeArtifactService
from src.skill_runtime import SkillRunner
from src.skill_runtime.intent_router import IntentRouter
from src.skill_runtime.skill_bundle_compiler import SkillBundleCompiler
from src.skill_runtime.tool_argument_planner import ToolArgumentPlanner
from src.tools.registry import list_tools


class SkillStudioError(ValueError):
    pass


class SkillStudioService:
    def __init__(self, skills_root: str = "src/skills") -> None:
        self.skills_root = Path(skills_root)
        self.runtime_artifacts = RuntimeArtifactService(skills_root=skills_root)
        self.runner = SkillRunner(skills_root=skills_root)
        self.bundle_compiler = SkillBundleCompiler(skills_root=skills_root)
        self.intent_router = IntentRouter()
        self.argument_planner = ToolArgumentPlanner()

    def list_skills(self) -> List[Dict[str, Any]]:
        """Compatibility alias for the legacy compiled-Skill catalog."""

        return self.list_compiled_skills()

    def list_compiled_skills(self) -> List[Dict[str, Any]]:
        """List only bundles executable by the legacy SkillRunner."""

        rows: List[Dict[str, Any]] = []
        if not self.skills_root.exists():
            return rows
        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith("."):
                continue
            if skill_dir.name.endswith("__refine_draft"):
                continue
            config_path = skill_dir / "skill.json"
            skill_md_path = skill_dir / "SKILL.md"
            schema_path = skill_dir / "schema.json"
            if (
                not config_path.is_file()
                or not skill_md_path.is_file()
                or not schema_path.is_file()
            ):
                continue
            skill_name = skill_dir.name
            try:
                skill_config = json.loads(config_path.read_text(encoding="utf-8"))
                output_schema = json.loads(schema_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(skill_config, dict) or not isinstance(output_schema, dict):
                continue
            input_schema = skill_config.get("input_schema") if isinstance(skill_config.get("input_schema"), dict) else {
                "type": "object",
                "required": ["question"],
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "希望该 Skill 完成的自然语言任务",
                    }
                },
            }
            examples_dir = skill_dir / "examples"
            retrieval_fields = CapabilitySearchService.build_skill_retrieval_fields(skill_config)
            rows.append(
                {
                    "skill_name": skill_name,
                    "status": str(skill_config.get("status") or "").strip(),
                    "purpose": str(skill_config.get("purpose") or skill_config.get("description") or "").strip(),
                    "description": str(skill_config.get("purpose") or skill_config.get("description") or "").strip(),
                    "best_for": [str(x).strip() for x in skill_config.get("best_for", []) if str(x).strip()],
                    "skill_body": str(skill_config.get("skill_body") or skill_config.get("execution_process") or "").strip(),
                    "execution_process": str(skill_config.get("skill_body") or skill_config.get("execution_process") or "").strip(),
                    "output_mode": str(skill_config.get("output_mode") or "default").strip(),
                    "owner": str(skill_config.get("owner") or "system").strip(),
                    "auth": str(skill_config.get("auth") or "public").strip(),
                    "availability": skill_config.get("availability") if isinstance(skill_config.get("availability"), dict) else {"lifecycle": "active", "retrieval_mode": "retrievable"},
                    "tags": [str(x).strip() for x in skill_config.get("tags", []) if str(x).strip()],
                    "created_from": str(skill_config.get("created_from") or "").strip(),
                    "tool_mode": str(((skill_config.get("tool_policy") or {}).get("mode") or "strict")).strip(),
                    "tools": [str(x).strip() for x in skill_config.get("tools", []) if str(x).strip()],
                    "default_max_steps": int(skill_config.get("default_max_steps", 0) or 0),
                    "presentation_preference": str(skill_config.get("presentation_preference") or skill_config.get("expected_render_page_type") or "").strip(),
                    "expected_render_page_type": str(skill_config.get("expected_render_page_type") or "").strip(),
                    "embedding_text": CapabilitySearchService.build_skill_embedding_text(skill_config),
                    "retrieval_profile": retrieval_fields,
                    "example_count": len(list(examples_dir.glob("*.json"))) if examples_dir.exists() else 0,
                    "input_schema": input_schema,
                    "sample_input": skill_config.get("sample_input") if isinstance(skill_config.get("sample_input"), dict) else {},
                    "requires_natural_language": "question" in (input_schema.get("required") or []),
                }
            )
        return rows

    def load_skill_bundle(self, skill_name: str) -> Dict[str, Any]:
        skill = self.runner.load_skill(skill_name)
        skill_dir = Path(skill.skill_dir)
        skill_md_path = skill_dir / "SKILL.md"
        schema_path = skill_dir / "schema.json"
        config_path = skill_dir / "skill.json"
        examples_dir = skill_dir / "examples"

        config_obj = {}
        if config_path.exists():
            config_obj = json.loads(config_path.read_text(encoding="utf-8"))
        retrieval_fields = CapabilitySearchService.build_skill_retrieval_fields(config_obj)
        embedding_text = CapabilitySearchService.build_skill_embedding_text(config_obj)

        examples: List[Dict[str, Any]] = []
        if examples_dir.exists():
            for example_path in sorted(examples_dir.glob("*.json")):
                if example_path.name.startswith("."):
                    continue
                examples.append(
                    {
                        "name": example_path.name,
                        "path": str(example_path),
                        "payload": json.loads(example_path.read_text(encoding="utf-8")),
                    }
                )

        return {
            "skill_name": skill.name,
            "files": {
                "skill_md_text": skill_md_path.read_text(encoding="utf-8"),
                "skill_config_text": json.dumps(config_obj, ensure_ascii=False, indent=2),
                "output_schema_text": json.dumps(skill.output_schema, ensure_ascii=False, indent=2),
                "skill_config": config_obj,
                "output_schema": skill.output_schema,
            },
            "meta": {
                "skill_dir": str(skill_dir),
                "available_tools": list_tools(),
                "retrieval_profile": {
                    "purpose": retrieval_fields.get("purpose") or "",
                    "best_for": retrieval_fields.get("best_for") or [],
                    "embedding_text": embedding_text,
                    "future_rerank_ready": True,
                },
            },
            "examples": examples,
        }

    def load_skill_bundle_with_fallback(self, skill_name: str) -> Dict[str, Any]:
        normalized = str(skill_name or "").strip()
        if not normalized:
            raise SkillStudioError("skill_name 不能为空")
        try:
            return self.load_skill_bundle(normalized)
        except FileNotFoundError:
            pass

        draft_suffix = "__refine_draft"
        if normalized.endswith(draft_suffix):
            source_skill_name = normalized[: -len(draft_suffix)].strip()
            if source_skill_name:
                try:
                    source_bundle = self.load_skill_bundle(source_skill_name)
                    return self._build_refine_draft_fallback_bundle(
                        target_skill_name=normalized,
                        source_skill_name=source_skill_name,
                        source_bundle=source_bundle,
                    )
                except FileNotFoundError:
                    pass
        return self.build_skill_template_bundle(normalized)

    def build_skill_template_bundle(self, skill_name: str) -> Dict[str, Any]:
        normalized = str(skill_name or "").strip()
        if not normalized:
            raise SkillStudioError("skill_name 不能为空")
        title = normalized.replace("_", " ").strip() or "new skill"
        skill_md = "\n".join(
            [
                f"# {title.title()}",
                "",
                "请在这里维护补充说明或备份信息；真正参与执行的主体请写入 skill.json 的 `skill_body`。",
                "",
            ]
        )
        skill_config = {
            "status": "draft",
            "created_from": "template",
            "purpose": "",
            "best_for": [],
            "skill_body": "",
            "tool_policy": {"mode": "strict"},
            "output_mode": "default",
            "owner": "system",
            "auth": "public",
            "availability": {
                "lifecycle": "active",
                "retrieval_mode": "retrievable"
            },
            "tags": [],
            "tools": [],
            "default_max_steps": 6,
            "presentation_preference": "",
            "expected_render_page_type": "",
            "enable_think": False,
        }
        output_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{normalized} Output",
            "type": "object",
            "required": ["summary"],
            "properties": {
                "summary": {"type": "string", "description": "skill 最终摘要"},
                "render_payload": {"type": "object", "description": "可选的前端渲染载荷"},
            },
            "additionalProperties": True,
        }
        return {
            "skill_name": normalized,
            "files": {
                "skill_md_text": skill_md,
                "skill_config_text": json.dumps(skill_config, ensure_ascii=False, indent=2),
                "output_schema_text": json.dumps(output_schema, ensure_ascii=False, indent=2),
                "skill_config": skill_config,
                "output_schema": output_schema,
            },
            "meta": {
                "template": True,
                "skill_dir": str(self.skills_root / normalized),
                "available_tools": list_tools(),
                "retrieval_profile": {
                    "purpose": "",
                    "best_for": [],
                    "embedding_text": "",
                    "future_rerank_ready": True,
                },
            },
            "examples": [],
        }

    def _build_refine_draft_fallback_bundle(
        self,
        *,
        target_skill_name: str,
        source_skill_name: str,
        source_bundle: Dict[str, Any],
    ) -> Dict[str, Any]:
        files = source_bundle.get("files") if isinstance(source_bundle, dict) else {}
        source_config = files.get("skill_config") if isinstance(files, dict) and isinstance(files.get("skill_config"), dict) else {}
        source_schema = files.get("output_schema") if isinstance(files, dict) and isinstance(files.get("output_schema"), dict) else {}
        cloned_config = json.loads(json.dumps(source_config, ensure_ascii=False)) if source_config else {}
        cloned_schema = json.loads(json.dumps(source_schema, ensure_ascii=False)) if source_schema else {}
        cloned_config["status"] = "draft"
        cloned_config["created_from"] = "refine_recovery"
        cloned_config["source_skill_name"] = source_skill_name
        title = target_skill_name.replace("_", " ").strip().title()
        source_md = str(files.get("skill_md_text") or "")
        skill_md = source_md
        if source_md:
            skill_md = source_md.replace(f"name: {source_skill_name}", f"name: {target_skill_name}", 1)
        if not skill_md:
            skill_md = "\n".join(
                [
                    f"# {title}",
                    "",
                    f"该 draft 由 `{source_skill_name}` 恢复生成，可继续编辑后再保存。",
                    "",
                ]
            )
        return {
            "skill_name": target_skill_name,
            "files": {
                "skill_md_text": skill_md,
                "skill_config_text": json.dumps(cloned_config, ensure_ascii=False, indent=2),
                "output_schema_text": json.dumps(cloned_schema, ensure_ascii=False, indent=2),
                "skill_config": cloned_config,
                "output_schema": cloned_schema,
            },
            "meta": {
                "template": True,
                "recovered_from": source_skill_name,
                "skill_dir": str(self.skills_root / target_skill_name),
                "available_tools": list_tools(),
                "retrieval_profile": {
                    "purpose": self._trim(cloned_config.get("purpose")) or self._trim(cloned_config.get("description")),
                    "best_for": [self._trim(x) for x in cloned_config.get("best_for", []) if self._trim(x)],
                    "embedding_text": CapabilitySearchService.build_skill_embedding_text(cloned_config),
                    "future_rerank_ready": True,
                },
            },
            "examples": list(source_bundle.get("examples") or []),
        }

    def save_skill_bundle(
        self,
        *,
        skill_name: str,
        skill_md_text: str,
        skill_config_text: str,
        output_schema_text: str,
    ) -> Dict[str, Any]:
        skill_dir = self.skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_md = str(skill_md_text or "")
        if not skill_md.strip():
            raise SkillStudioError("SKILL.md 不能为空")

        try:
            skill_config = json.loads(skill_config_text or "{}")
        except Exception as exc:
            raise SkillStudioError(f"skill.json 不是合法 JSON: {exc}") from exc
        if not isinstance(skill_config, dict):
            raise SkillStudioError("skill.json 顶层必须是对象")
        if not self._trim(skill_config.get("purpose")) and self._trim(skill_config.get("description")):
            skill_config["purpose"] = self._trim(skill_config.get("description"))
        if not self._trim(skill_config.get("skill_body")) and self._trim(skill_config.get("execution_process")):
            skill_config["skill_body"] = self._trim(skill_config.get("execution_process"))
        availability = skill_config.get("availability") if isinstance(skill_config.get("availability"), dict) else {}
        skill_config["availability"] = {
            "lifecycle": self._trim(availability.get("lifecycle") or "active") or "active",
            "retrieval_mode": self._trim(availability.get("retrieval_mode") or "retrievable") or "retrievable",
        }

        try:
            output_schema = json.loads(output_schema_text or "{}")
        except Exception as exc:
            raise SkillStudioError(f"schema.json 不是合法 JSON: {exc}") from exc
        if not isinstance(output_schema, dict):
            raise SkillStudioError("schema.json 顶层必须是对象")

        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (skill_dir / "skill.json").write_text(json.dumps(skill_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (skill_dir / "schema.json").write_text(json.dumps(output_schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.runtime_artifacts.sync_skill(skill_name, source_type="ui", changed_by="skill_studio")

        return self.load_skill_bundle(skill_name)

    def update_skill_availability(
        self,
        *,
        skill_name: str,
        lifecycle: str = "",
        retrieval_mode: str = "",
    ) -> Dict[str, Any]:
        bundle = self.load_skill_bundle(skill_name)
        files = bundle.get("files") if isinstance(bundle, dict) else {}
        skill_config = files.get("skill_config") if isinstance(files.get("skill_config"), dict) else {}
        availability = skill_config.get("availability") if isinstance(skill_config.get("availability"), dict) else {}
        skill_config["availability"] = {
            "lifecycle": self._trim(lifecycle) or self._trim(availability.get("lifecycle")) or "active",
            "retrieval_mode": self._trim(retrieval_mode) or self._trim(availability.get("retrieval_mode")) or "retrievable",
        }
        return self.save_skill_bundle(
            skill_name=str(skill_name or "").strip(),
            skill_md_text=str(files.get("skill_md_text") or ""),
            skill_config_text=json.dumps(skill_config, ensure_ascii=False, indent=2),
            output_schema_text=json.dumps(files.get("output_schema") or {}, ensure_ascii=False, indent=2),
        )

    def preview_tool_selection(
        self,
        *,
        skill_name: str,
        input_payload: Dict[str, Any],
        tool_mode: str = "",
    ) -> Dict[str, Any]:
        skill = self.runner.load_skill(skill_name)
        if tool_mode:
            tool_policy = dict(skill.config.get("tool_policy") or {})
            tool_policy["mode"] = str(tool_mode).strip()
            skill.config["tool_policy"] = tool_policy
        detail = self.runner.tool_selector.select_detailed(
            skill_name=skill.name,
            skill_md=skill.skill_md,
            skill_config=skill.config,
            input_payload=input_payload,
        )
        return {
            "skill_name": skill.name,
            "tool_mode": str((skill.config.get("tool_policy") or {}).get("mode") or ""),
            "selection": detail,
        }

    def preview_execution_plan(
        self,
        *,
        skill_name: str,
        input_payload: Dict[str, Any],
        tool_mode: str = "",
    ) -> Dict[str, Any]:
        return self.bundle_compiler.build_execution_plan(
            skill_name=skill_name,
            input_payload=input_payload,
            tool_mode=tool_mode,
        )

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def preview_tool_argument_plans(
        self,
        *,
        skill_name: str,
        input_payload: Dict[str, Any],
        tool_mode: str = "",
        execution_profile: str = "",
    ) -> Dict[str, Any]:
        execution_plan = self.preview_execution_plan(
            skill_name=skill_name,
            input_payload=input_payload,
            tool_mode=tool_mode,
        )
        tool_names = execution_plan.get("selected_tools") or []
        plans = self.argument_planner.build_batch(
            tool_names=tool_names,
            user_text=str(input_payload.get("question") or input_payload.get("user_text") or "").strip(),
            context=input_payload,
        )
        forced_profile = str(execution_profile or input_payload.get("_execution_profile") or "").strip()
        if forced_profile:
            for plan in plans:
                if isinstance(plan, dict):
                    plan["execution_profile"] = forced_profile
        return {
            "skill_name": skill_name,
            "execution_plan": execution_plan,
            "tool_argument_plans": plans,
        }

    def preview_intent_route(
        self,
        *,
        user_text: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        route = self.intent_router.route(user_text=user_text, context=context)
        return self.intent_router.build_route_snapshot(
            route=route,
            user_text=user_text,
            context=context,
            source="natural_language_preview",
        )
