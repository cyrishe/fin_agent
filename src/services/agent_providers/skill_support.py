from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.services.custom_tool_context_bundle_service import CustomToolContextBundleService


SKILL_EXECUTION_SYSTEM_PROMPT = (
    "Execute only the Skill task supplied in the user prompt. The SKILL.md content is already present; do not read it "
    "again. Do not inspect AGENTS.md, memory files, unrelated skills, or unrelated repository files. Read only the "
    "playbooks and references explicitly selected by the Skill, plus the minimum required context-bundle files. "
    "If the Skill routes to a playbook, load that playbook before answering; never skip that required playbook read. "
    "Do not modify workspace files."
)

CODING_WORKSPACE_SYSTEM_PROMPT = (
    "Execute only the Skill task supplied in the user prompt. The SKILL.md content is already present; do not read it "
    "again. Do not inspect AGENTS.md, memory files, unrelated skills, or unrelated repository files. Read only the "
    "task, design, feedback, implementation, API catalog, and Skill reference files explicitly supplied in the "
    "context bundle. You may edit only the module files listed in CONTEXT.current_implementation.module_files. "
    "Do not modify any other file."
)


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class AgentSkillHarnessSupport:
    """Provider-neutral preparation and result recovery for skill harnesses."""

    def __init__(
        self,
        *,
        cwd: str = ".",
        timeout_seconds: int = 180,
        hard_timeout_seconds: int = 0,
        model: str = "",
        context_bundle_service: Optional[CustomToolContextBundleService] = None,
    ) -> None:
        self.cwd = Path(cwd)
        self.timeout_seconds = int(timeout_seconds or 180)
        self.hard_timeout_seconds = int(hard_timeout_seconds or max(self.timeout_seconds * 5, 900))
        self.model = _trim(model)
        self.context_bundle_service = context_bundle_service or CustomToolContextBundleService()

    @staticmethod
    def _send_event(event: Dict[str, Any], event_sink: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        if event_sink is None:
            return
        try:
            event_sink(event)
        except Exception:
            return

    @classmethod
    def _append_event(
        cls,
        events: List[Dict[str, Any]],
        event: Dict[str, Any],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> None:
        events.append(event)
        cls._send_event(event, event_sink)

    def _build_prompt(
        self,
        *,
        skill_text: str,
        skill_root: str = "",
        user_request: str,
        context: Mapping[str, Any],
        structured_output: bool = False,
        stage: str = "",
    ) -> str:
        bundle = context.get("context_bundle") if isinstance(context.get("context_bundle"), Mapping) else {}
        bundle_dir = _trim(bundle.get("bundle_dir"))
        coding_workspace = bundle.get("coding_workspace") if isinstance(bundle.get("coding_workspace"), Mapping) else {}
        editable_workspace = coding_workspace.get("editable") is True
        output_instruction = (
            ""
            if structured_output
            else (
                "只输出该 SKILL 要求的 NDJSON 事件；不要修改工作区文件。\n"
                "最后一行必须是 source=model,type=final 的 JSON 对象。\n"
            )
        )
        workspace_instruction = (
            "当前实现已放在隔离的临时 Coding 工作区。先用 rg/sed 按需定位相关函数、反馈和 API，"
            "只修改 CONTEXT.current_implementation.module_files 列出的模块文件。最终结构化输出必须反映修改后的内容；"
            "外层系统会从工作区回收源码并保存数据库 revision。\n"
            if editable_workspace
            else "不要修改工作区文件。\n"
        )
        skill_resources = ""
        if _trim(skill_root):
            skill_resources = (
                "# SKILL RESOURCES\n"
                f"Skill 目录：{_trim(skill_root)}\n"
                "SKILL 中的相对文件引用均从该目录解析；只读取当前任务明确需要的文件。\n\n"
            )
        file_context = ""
        if _trim(stage) in {"requirement", "flowchart", "test"}:
            file_context = "先读取 CONTEXT 中与本任务直接相关的引用文件；不要展开资料包中的其他资产。\n"
        elif _trim(stage) == "design":
            file_context = "如果 CONTEXT 提供 design_ref，只按需读取该设计资产；不要读取运行时和代码资料。\n"
        elif _trim(stage) == "coding":
            file_context = (
                "先读取 CONTEXT 中与本任务直接相关的引用文件；不要把资料包全部展开。\n"
                "如果任务需要金融数据工具能力，读取 api_catalog/index.json，再只读取相关 subject 文件。\n"
                "生成代码时参考 custom_tool_sdk.md。\n"
            )
        return (
            "请严格按照下面的 SKILL 执行任务。\n"
            f"{workspace_instruction}"
            f"{output_instruction}\n"
            "# AVAILABLE FILE CONTEXT\n"
            f"资料包目录：{bundle_dir}\n"
            f"{file_context}\n"
            "# SKILL\n"
            f"{skill_text}\n\n"
            f"{skill_resources}"
            "# CONTEXT\n"
            f"{_json_text(dict(context))}\n\n"
            "# USER REQUEST\n"
            f"{user_request}\n"
        )

    def _build_native_skill_prompt(
        self,
        *,
        user_request: str,
        context: Mapping[str, Any],
        stage: str,
    ) -> str:
        bundle = context.get("context_bundle") if isinstance(context.get("context_bundle"), Mapping) else {}
        bundle_dir = _trim(bundle.get("bundle_dir"))
        return (
            "执行当前已启用的 Skill。\n"
            f"资料包目录：{bundle_dir}\n"
            "按需读取 CONTEXT 中的引用文件，不要展开无关资产。\n\n"
            "# CONTEXT\n"
            f"{_json_text(dict(context))}\n\n"
            "# USER REQUEST\n"
            f"{user_request}\n"
        )

    def _execution_cwd(self, bundle: Mapping[str, Any]) -> Path:
        workspace = bundle.get("coding_workspace") if isinstance(bundle.get("coding_workspace"), Mapping) else {}
        bundle_dir = _trim(bundle.get("bundle_dir"))
        if workspace and bundle_dir:
            return Path(bundle_dir)
        return self.cwd

    def _public_bundle(self, bundle: Mapping[str, Any]) -> Dict[str, Any]:
        method = getattr(self.context_bundle_service, "public_bundle", None)
        return dict(method(bundle)) if callable(method) else dict(bundle)

    def _prompt_context(self, bundle: Mapping[str, Any], fallback: Mapping[str, Any]) -> Dict[str, Any]:
        method = getattr(self.context_bundle_service, "prompt_context", None)
        return dict(method(bundle, fallback)) if callable(method) else dict(fallback)

    def _collect_coding_result(self, bundle: Mapping[str, Any], final: Mapping[str, Any]) -> Dict[str, Any]:
        method = getattr(self.context_bundle_service, "collect_coding_result", None)
        return dict(method(bundle, final)) if callable(method) else dict(final)

    @staticmethod
    def _developer_instructions(bundle: Mapping[str, Any]) -> str:
        workspace = bundle.get("coding_workspace") if isinstance(bundle.get("coding_workspace"), Mapping) else {}
        return CODING_WORKSPACE_SYSTEM_PROMPT if workspace.get("editable") is True else SKILL_EXECUTION_SYSTEM_PROMPT

    def _resolve_output_schema_file(self, *, skill_file: Path, output_schema_path: str = "") -> Optional[Path]:
        explicit_path = _trim(output_schema_path)
        if explicit_path:
            schema_file = Path(explicit_path)
            if not schema_file.is_absolute():
                schema_file = self.cwd / schema_file
            if not schema_file.exists():
                raise FileNotFoundError(f"output schema file not found: {schema_file}")
            self._load_output_schema(schema_file)
            return schema_file
        for filename in ("schema.json", "output_schema.json"):
            candidate = skill_file.parent / filename
            if candidate.exists():
                self._load_output_schema(candidate)
                return candidate
        return None

    @staticmethod
    def _load_output_schema(schema_file: Path) -> Dict[str, Any]:
        try:
            payload = json.loads(schema_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid output schema JSON: {schema_file}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"output schema must be a JSON object: {schema_file}")
        return dict(payload)

    @staticmethod
    def _infer_stage(skill_file: Path) -> str:
        return "coding" if "coding" in skill_file.name.lower() else "design"
