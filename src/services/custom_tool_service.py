from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.services.codex_exec_skill_harness import (
    CodexCustomToolCoder,
    CodexCustomToolDesigner,
    CodexSdkSkillHarness,
)
from src.services.python_execution_runtime import PythonExecutionRuntime


class CustomToolError(ValueError):
    pass


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


class CustomToolStoreService:
    def __init__(self, *, root_dir: str = "data/custom_tools") -> None:
        self.root_dir = Path(root_dir)

    def tool_dir(self, tool_name: str) -> Path:
        name = self.normalize_tool_name(tool_name)
        if not name:
            raise CustomToolError("tool_name is required")
        return self.root_dir / name

    @staticmethod
    def normalize_tool_name(value: Any) -> str:
        raw = _trim(value).lower()
        raw = re.sub(r"[^a-z0-9_]+", "_", raw)
        raw = re.sub(r"_+", "_", raw).strip("_")
        if raw and not raw.startswith("ct_"):
            raw = f"ct_{raw}"
        return raw[:64]

    def exists(self, tool_name: str) -> bool:
        try:
            return (self.tool_dir(tool_name) / "manifest.json").exists()
        except CustomToolError:
            return False

    def save_draft(self, design: Mapping[str, Any], *, owner_id: str = "") -> Dict[str, Any]:
        manifest = dict(design.get("manifest") or {})
        tool_name = self.normalize_tool_name(manifest.get("tool_name"))
        if not tool_name:
            raise CustomToolError("manifest.tool_name is required")
        manifest["tool_name"] = tool_name
        manifest["status"] = "draft"
        manifest["owner_id"] = _trim(owner_id)
        manifest.setdefault("visibility", "personal")
        code = _trim(design.get("code"))
        if not code:
            raise CustomToolError("code is required")
        root = self.tool_dir(tool_name)
        root.mkdir(parents=True, exist_ok=True)
        revision_no = self._next_revision(root)
        manifest["current_revision"] = revision_no
        manifest["code_hash"] = hashlib.sha256(code.encode("utf-8")).hexdigest()
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        root.joinpath("input_schema.json").write_text(_json_text(design.get("input_schema") or {}), encoding="utf-8")
        root.joinpath("output_schema.json").write_text(_json_text(design.get("output_schema") or {}), encoding="utf-8")
        root.joinpath("tool.py").write_text(code + "\n", encoding="utf-8")
        rev_dir = root / "revisions" / str(revision_no)
        rev_dir.mkdir(parents=True, exist_ok=True)
        rev_dir.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        rev_dir.joinpath("tool.py").write_text(code + "\n", encoding="utf-8")
        return self.load(tool_name)

    def load(self, tool_name: str) -> Dict[str, Any]:
        root = self.tool_dir(tool_name)
        if not (root / "manifest.json").exists():
            raise CustomToolError(f"custom tool not found: {tool_name}")
        return {
            "manifest": json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8")),
            "input_schema": self._load_json(root / "input_schema.json"),
            "output_schema": self._load_json(root / "output_schema.json"),
            "code": root.joinpath("tool.py").read_text(encoding="utf-8") if (root / "tool.py").exists() else "",
            "root": str(root),
        }

    def list_tools(
        self,
        *,
        include_inactive: bool = False,
        owner_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not self.root_dir.exists():
            return []
        allowed_owners = self._normalize_owner_ids(owner_ids)
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.root_dir.iterdir()):
            if not path.is_dir() or not (path / "manifest.json").exists():
                continue
            try:
                manifest = json.loads(path.joinpath("manifest.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            if not include_inactive and _trim(manifest.get("status")) != "active":
                continue
            if owner_ids is not None and not self._is_visible_to_owner(manifest, allowed_owners):
                continue
            rows.append(manifest)
        return rows

    def load_for_runtime(
        self,
        tool_name: str,
        *,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = False,
    ) -> Dict[str, Any]:
        bundle = self.load(tool_name)
        manifest = dict(bundle["manifest"])
        if not allow_inactive and _trim(manifest.get("status")) != "active":
            raise CustomToolError("custom tool is not active")
        if owner_ids is not None and not self._is_visible_to_owner(manifest, self._normalize_owner_ids(owner_ids)):
            raise CustomToolError("custom tool is not visible to current user")
        return bundle

    def commit(self, tool_name: str, *, owner_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        bundle = self.load_for_runtime(tool_name, owner_ids=owner_ids, allow_inactive=True)
        manifest = dict(bundle["manifest"])
        last_test = manifest.get("last_test") if isinstance(manifest.get("last_test"), dict) else {}
        if last_test.get("ok") is not True:
            raise CustomToolError("custom tool must pass call/smoke test before commit")
        manifest["status"] = "active"
        root = self.tool_dir(manifest["tool_name"])
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        return self.load(manifest["tool_name"])

    def record_test(self, tool_name: str, result: Mapping[str, Any]) -> Dict[str, Any]:
        bundle = self.load(tool_name)
        manifest = dict(bundle["manifest"])
        manifest["last_test"] = {
            "ok": bool(result.get("ok")),
            "error": _trim(result.get("error")),
            "backend": _trim(((result.get("meta") or {}).get("diagnostics") or {}).get("backend")),
        }
        root = self.tool_dir(manifest["tool_name"])
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        return self.load(manifest["tool_name"])

    def _next_revision(self, root: Path) -> int:
        manifest = self._load_json(root / "manifest.json")
        current = int(manifest.get("current_revision") or 0) if manifest else 0
        return current + 1

    @staticmethod
    def _normalize_owner_ids(owner_ids: Optional[List[str]]) -> set[str]:
        return {_trim(item) for item in (owner_ids or []) if _trim(item)}

    @staticmethod
    def _is_visible_to_owner(manifest: Mapping[str, Any], owner_ids: set[str]) -> bool:
        if _trim(manifest.get("visibility")) != "personal":
            return True
        owner_id = _trim(manifest.get("owner_id"))
        return bool(owner_id and owner_id in owner_ids)

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


class CustomToolDesigner:
    """Small, replaceable designer for natural-language custom tool drafts."""

    def design(self, requirement_text: str) -> Dict[str, Any]:
        text = _trim(requirement_text)
        if not text:
            return {
                "status": "need_more_info",
                "missing": ["工具目标", "输入", "输出", "计算逻辑"],
                "message": "请补充这个工具要解决什么问题、输入是什么、输出是什么、核心计算逻辑是什么。",
            }
        return {
            "status": "need_more_info",
            "missing": ["工具英文名", "中文名和描述", "输入 schema", "输出 schema", "计算逻辑", "样例输入"],
            "message": (
                "自定义工具需要先形成稳定设计：英文名、中文名、描述、输入、输出、计算逻辑和样例。"
                "当前默认 designer 不做领域硬匹配；后续由 LLM designer 生成设计，或由测试注入具体 designer。"
            ),
        }


class CustomToolRuntimeService:
    def __init__(
        self,
        *,
        store: Optional[CustomToolStoreService] = None,
        python_runtime: Optional[PythonExecutionRuntime] = None,
        runtime_root: str = "data/runtime_custom_tools",
    ) -> None:
        self.store = store or CustomToolStoreService()
        self.python_runtime = python_runtime or PythonExecutionRuntime(allow_unsafe_backends=True)
        self.runtime_root = Path(runtime_root)

    def run(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = True,
    ) -> Dict[str, Any]:
        try:
            bundle = self.store.load_for_runtime(
                tool_name,
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
        except CustomToolError as exc:
            return self._error(tool_name, "permission_or_lifecycle_error", str(exc))
        manifest = bundle["manifest"]
        args = dict(arguments or {})
        validation_errors = self._validate_input(args, bundle.get("input_schema") or {})
        if validation_errors:
            return self._error(manifest["tool_name"], "input_validation_error", "; ".join(validation_errors))
        code = self._wrap_code(bundle.get("code") or "", tool_name=manifest["tool_name"])
        run_dir = Path(tempfile.mkdtemp(prefix=f"{manifest['tool_name']}_", dir=str(self._ensure_runtime_root())))
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        input_dir.joinpath("input.json").write_text(json.dumps(args, ensure_ascii=False, indent=2), encoding="utf-8")
        runtime_cfg = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
        profile = {
            "name": "custom_tool_python_v1",
            "backend": _trim(runtime_cfg.get("backend")) or "auto",
            "network": "none",
            "workspace_access": "none",
            "limits": {"timeout_ms": int(runtime_cfg.get("timeout_ms") or 5000)},
        }
        runtime_result = self.python_runtime.execute(
            code=code,
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            profile=profile,
        )
        diagnostics = dict(runtime_result.get("diagnostics") or {})
        if not runtime_result.get("ok"):
            return self._error(manifest["tool_name"], _trim(runtime_result.get("failure_kind")) or "runtime_error", _trim(diagnostics.get("stderr")), diagnostics=diagnostics)
        output_path = output_dir / "output.json"
        if not output_path.exists():
            return self._error(manifest["tool_name"], "output_missing", "custom tool did not write output.json", diagnostics=diagnostics)
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return self._error(manifest["tool_name"], "output_json_error", str(exc), diagnostics=diagnostics)
        result = {
            "tool": manifest["tool_name"],
            "ok": True,
            "data": payload if isinstance(payload, dict) else {"value": payload},
            "error": "",
            "meta": {
                "custom_tool": True,
                "display_name": manifest.get("display_name"),
                "revision": manifest.get("current_revision"),
                "diagnostics": diagnostics,
            },
        }
        self.store.record_test(manifest["tool_name"], result)
        return result

    def _ensure_runtime_root(self) -> Path:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        return self.runtime_root

    def _validate_input(self, payload: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
        errors: List[str] = []
        for key in schema.get("required") or []:
            if key not in payload:
                errors.append(f"missing required input: {key}")
        properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
        if schema.get("additionalProperties") is False:
            for key in payload.keys():
                if key not in properties:
                    errors.append(f"unknown input: {key}")
        return errors

    def _wrap_code(self, code: str, *, tool_name: str) -> str:
        repo_root = str(Path.cwd().resolve())
        code_body = self._strip_future_imports(code)
        return f'''from __future__ import annotations

import json
import os
import sys
import types

_CUSTOM_TOOL_REPO_ROOT = {repo_root!r}
if _CUSTOM_TOOL_REPO_ROOT not in sys.path:
    sys.path.insert(0, _CUSTOM_TOOL_REPO_ROOT)


def finance_query(request: str) -> dict:
    try:
        from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService
        return FinanceDataToolRuntimeService().execute_request(request=str(request or "").strip())
    except Exception as exc:
        return {{"ok": False, "error": str(exc), "data": {{}}}}


def web_search(query: str, limit: int = 5) -> dict:
    return {{
        "ok": False,
        "error": "web_search provider is not configured for custom_tool_sdk",
        "query": str(query or ""),
        "limit": int(limit or 5),
        "items": [],
    }}


_sdk = types.ModuleType("custom_tool_sdk")
_sdk.finance_query = finance_query
_sdk.web_search = web_search
sys.modules["custom_tool_sdk"] = _sdk

{code_body}

with open(os.environ["CODE_INPUT_JSON"], "r", encoding="utf-8") as f:
    _inputs = json.load(f)
_data = run(_inputs)
with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "output.json"), "w", encoding="utf-8") as f:
    json.dump(_data, f, ensure_ascii=False)
'''

    @staticmethod
    def _strip_future_imports(code: str) -> str:
        lines = []
        for line in str(code or "").splitlines():
            if line.strip().startswith("from __future__ import "):
                continue
            lines.append(line)
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _error(tool_name: str, kind: str, message: str, *, diagnostics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "tool": tool_name,
            "ok": False,
            "data": {},
            "error": message or kind,
            "meta": {"custom_tool": True, "failure_kind": kind, "diagnostics": diagnostics or {}},
        }


class CustomToolAgentService:
    def __init__(
        self,
        *,
        store: Optional[CustomToolStoreService] = None,
        designer: Optional[CustomToolDesigner] = None,
        coder: Optional[Any] = None,
        runtime: Optional[CustomToolRuntimeService] = None,
        use_codex: Optional[bool] = None,
    ) -> None:
        self.store = store or CustomToolStoreService()
        codex_enabled = bool(use_codex) if use_codex is not None else _trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX", "1")) not in {"0", "false", "False", "no"}
        self.designer = designer or (self._default_codex_designer() if codex_enabled else CustomToolDesigner())
        self.coder = coder or (self._default_codex_coder() if codex_enabled else None)
        self.runtime = runtime or CustomToolRuntimeService(store=self.store)

    @staticmethod
    def _clean_state_for_context(state: Mapping[str, Any] | None) -> Dict[str, Any]:
        source = dict(state or {})
        for key in ("events", "coding_events", "raw", "last_message", "raw_stdout", "raw_stderr"):
            source.pop(key, None)
        return source

    def _default_codex_harness(self) -> Any:
        runner = _trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_RUNNER") or "sdk")
        if runner == "exec":
            return None
        harness = CodexSdkSkillHarness(
            cwd=".",
            timeout_seconds=int(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_TIMEOUT_SECONDS") or 180),
            hard_timeout_seconds=int(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_HARD_TIMEOUT_SECONDS") or 900),
            model=_trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_MODEL")),
            sandbox=_trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_SANDBOX") or "workspace-write"),
        )
        return harness if harness.available() else None

    def _default_codex_designer(self) -> CodexCustomToolDesigner:
        harness = self._default_codex_harness()
        return CodexCustomToolDesigner(harness=harness) if harness is not None else CodexCustomToolDesigner()

    def _default_codex_coder(self) -> CodexCustomToolCoder:
        harness = self._default_codex_harness()
        return CodexCustomToolCoder(harness=harness) if harness is not None else CodexCustomToolCoder()

    def start_create(
        self,
        requirement_text: str,
        *,
        owner_id: str = "",
        state: Optional[Mapping[str, Any]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        design_result = self._call_designer(requirement_text, state=state, owner_id=owner_id, event_sink=event_sink)
        status = _trim(design_result.get("status"))
        if status == "ok":
            return self._return_legacy_design(requirement_text, owner_id=owner_id, design=design_result["design"])
        design_ready = status in {"review", "design_ready"}
        understanding = design_result.get("understanding") if isinstance(design_result.get("understanding"), Mapping) else {}
        existing_analysis = design_result.get("existing_analysis") if isinstance(design_result.get("existing_analysis"), Mapping) else {}
        questions = design_result.get("questions") if isinstance(design_result.get("questions"), list) else []
        design = design_result.get("design") if isinstance(design_result.get("design"), Mapping) else {}
        design_artifact = self._design_artifact_identity(design, state=state)
        if not design_ready:
            next_state = {
                "status": "collect_requirement",
                "requirement_text": requirement_text,
                "owner_id": owner_id,
                "partial_design": design,
                "understanding": dict(understanding),
                "questions": questions,
                **design_artifact,
            }
            return {
                "message": design_result.get("message") or "请补充工具需求。",
                "design_status": status or "clarification",
                "understanding": dict(understanding),
                "questions": questions or design_result.get("missing") or [],
                "design": design,
                "design_artifact": design_artifact,
                "existing_analysis": dict(existing_analysis),
                "events": design_result.get("events") or [],
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        next_state = {
            "status": "awaiting_design_confirmation",
            "requirement_text": requirement_text,
            "owner_id": owner_id,
            "design_contract": design,
            "understanding": dict(understanding),
            "existing_analysis": dict(existing_analysis),
            **design_artifact,
        }
        tool_name = _trim(design.get("tool_name")) or "custom_tool"
        display_name = _trim(design.get("display_name")) or tool_name
        message = (
            f"已生成自定义工具设计：{tool_name} / {display_name}\n"
            f"描述：{_trim(design.get('description'))}\n"
            f"实现逻辑：{self._logic_text(design)}\n"
            "回复“确认实现”后生成 draft；需要调整则直接说明修改点。"
        )
        return {
            "message": message,
            "design_status": status,
            "understanding": dict(understanding),
            "state": next_state,
            "design": design,
            "design_artifact": design_artifact,
            "existing_analysis": dict(existing_analysis),
            "events": design_result.get("events") or [],
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def continue_flow(
        self,
        text: str,
        *,
        state: Mapping[str, Any],
        owner_id: str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        status = _trim(state.get("status"))
        raw = _trim(text)
        if status == "collect_requirement":
            requirement = "\n".join([_trim(state.get("requirement_text")), raw]).strip()
            return self.start_create(requirement, owner_id=owner_id or _trim(state.get("owner_id")), state=state, event_sink=event_sink)
        if status == "awaiting_design_confirmation":
            if raw in {"确认", "确认实现", "可以", "实现", "生成", "ok", "OK"}:
                return self._confirm_and_code(state=state, owner_id=owner_id, event_sink=event_sink)
            requirement = "\n".join([_trim(state.get("requirement_text")), raw]).strip()
            return self.start_create(requirement, owner_id=owner_id or _trim(state.get("owner_id")), state=state, event_sink=event_sink)
        return {"message": "当前没有进行中的自定义工具创建流程。", "state": {}}

    def continue_flow_action(
        self,
        action_id: str,
        *,
        state: Mapping[str, Any],
        expected_revision: Optional[int] = None,
        owner_id: str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Resolve a trusted UI action without executing model-provided commands."""
        normalized_action = _trim(action_id)
        status = _trim(state.get("status"))
        if status == "awaiting_design_confirmation" and normalized_action == "custom_tool.confirm_design":
            current_revision = int(state.get("design_revision") or 0)
            if expected_revision is not None and int(expected_revision) != current_revision:
                raise CustomToolError(
                    f"design revision changed: expected {int(expected_revision)}, current {current_revision}"
                )
            return self._confirm_and_code(state=state, owner_id=owner_id, event_sink=event_sink)
        raise CustomToolError(
            f"action {normalized_action or '-'} is not allowed while custom tool state is {status or '-'}"
        )

    @staticmethod
    def interaction_user_text(action_id: str) -> str:
        labels = {
            "custom_tool.confirm_design": "确认并继续",
        }
        normalized_action = _trim(action_id)
        if normalized_action not in labels:
            raise CustomToolError(f"unknown custom tool action: {normalized_action or '-'}")
        return labels[normalized_action]

    def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        owner_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self.runtime.run(
            tool_name,
            arguments or {},
            owner_ids=owner_ids,
            allow_inactive=True,
        )

    def commit(self, tool_name: str, *, owner_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.store.commit(tool_name, owner_ids=owner_ids)

    def _call_designer(
        self,
        requirement_text: str,
        *,
        state: Optional[Mapping[str, Any]],
        owner_id: str,
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        context = {
            "owner_id": owner_id,
            "state": self._clean_state_for_context(state),
        }
        try:
            return self.designer.design(requirement_text, context=context, event_sink=event_sink)
        except TypeError:
            return self.designer.design(requirement_text)

    def _confirm_and_code(
        self,
        *,
        state: Mapping[str, Any],
        owner_id: str,
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        actual_owner = owner_id or _trim(state.get("owner_id"))
        legacy_design = state.get("design") if isinstance(state.get("design"), Mapping) else {}
        if legacy_design and legacy_design.get("code"):
            return self._save_test_and_return(legacy_design, owner_id=actual_owner)
        design_contract = state.get("design_contract") if isinstance(state.get("design_contract"), Mapping) else {}
        if not design_contract:
            next_state = {**self._clean_state_for_context(state), "status": "collect_requirement"}
            return {
                "message": "当前设计稿为空，请补充需求后重新生成设计。",
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        if self.coder is None:
            next_state = {**self._clean_state_for_context(state), "status": "awaiting_design_confirmation"}
            return {
                "message": "当前未启用 Codex coding，请先配置 coding runner 或补充可执行代码。",
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        try:
            coding_result = self.coder.code(
                design_contract,
                requirement_text=_trim(state.get("requirement_text")),
                context={"state": self._clean_state_for_context(state)},
                event_sink=event_sink,
            )
        except TypeError:
            coding_result = self.coder.code(
                design_contract,
                requirement_text=_trim(state.get("requirement_text")),
                context={"state": self._clean_state_for_context(state)},
            )
        if _trim(coding_result.get("status")) != "code_ready":
            next_state = {
                **self._clean_state_for_context(state),
                "status": "awaiting_design_confirmation",
                "coding_feedback": coding_result.get("message") or coding_result.get("final", {}).get("need_design_fix"),
            }
            return {
                "message": coding_result.get("message") or "代码生成需要回到设计阶段确认。",
                "events": coding_result.get("events") or [],
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        bundle_design = self._bundle_from_coding_final(
            design_contract,
            coding_result.get("final") if isinstance(coding_result.get("final"), Mapping) else {},
        )
        return self._save_test_and_return(
            bundle_design,
            owner_id=actual_owner,
            events=coding_result.get("events") or [],
        )

    def _return_legacy_design(self, requirement_text: str, *, owner_id: str, design: Mapping[str, Any]) -> Dict[str, Any]:
        next_state = {
            "status": "awaiting_design_confirmation",
            "requirement_text": requirement_text,
            "owner_id": owner_id,
            "design": dict(design),
        }
        manifest = design["manifest"]
        message = (
            f"已生成自定义工具设计：{manifest['tool_name']} / {manifest['display_name']}\n"
            f"描述：{manifest['description']}\n"
            f"实现逻辑：{manifest['implementation_logic']}\n"
            "回复“确认实现”后生成 draft；需要调整则直接说明修改点。"
        )
        return {
            "message": message,
            "state": next_state,
            "design": dict(design),
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _save_test_and_return(self, design: Mapping[str, Any], *, owner_id: str, events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        saved = self.store.save_draft(design, owner_id=owner_id)
        manifest = saved["manifest"]
        sample_input = design.get("sample_input") if isinstance(design.get("sample_input"), Mapping) else {}
        test_result = self.runtime.run(
            manifest["tool_name"],
            sample_input,
            owner_ids=[owner_id] if owner_id else None,
            allow_inactive=True,
        )
        saved = self.store.record_test(manifest["tool_name"], test_result)
        next_status = "draft_ready" if test_result.get("ok") else "draft_needs_test"
        next_state = {
            "status": next_status,
            "tool_name": manifest["tool_name"],
            "owner_id": owner_id,
        }
        return {
            "message": (
                f"已生成 draft：{manifest['tool_name']}。\n"
                f"样例测试：{'通过' if test_result.get('ok') else '失败'}"
                + (f"\n错误：{test_result.get('error')}" if not test_result.get("ok") else "")
                + (
                    f"\n确认可用后执行 /custom_tool commit {manifest['tool_name']}。"
                    if test_result.get("ok")
                    else f"\n请使用 /custom_tool call {manifest['tool_name']} {{...}} 提供有效输入测试，通过后再 commit。"
                )
            ),
            "state": next_state,
            "tool": saved,
            "test_result": test_result,
            "events": events or [],
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _bundle_from_coding_final(self, design: Mapping[str, Any], final: Mapping[str, Any]) -> Dict[str, Any]:
        tool_name = self.store.normalize_tool_name(design.get("tool_name"))
        display_name = _trim(design.get("display_name")) or tool_name
        description = _trim(design.get("description"))
        code = self._select_code(final)
        if not code:
            raise CustomToolError("coding final does not include python code")
        return {
            "manifest": {
                "tool_name": tool_name,
                "display_name": display_name,
                "description": description,
                "visibility": "personal",
                "capabilities": ["custom_tool"],
                "implementation_logic": self._logic_text(design) or _trim(final.get("code_summary")),
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 30000},
            },
            "input_schema": self._schema_from_fields(design.get("inputs") if isinstance(design.get("inputs"), list) else []),
            "output_schema": self._schema_from_fields(design.get("outputs") if isinstance(design.get("outputs"), list) else []),
            "code": code,
            "sample_input": self._sample_input(final),
        }

    @staticmethod
    def _logic_text(design: Mapping[str, Any]) -> str:
        logic = design.get("logic")
        if isinstance(logic, list):
            return "\n".join(_trim(item) for item in logic if _trim(item))
        rules = design.get("rules") if isinstance(design.get("rules"), list) else []
        rule_lines = [
            f"{_trim(item.get('name'))}: {_trim(item.get('logic'))}".strip(": ")
            for item in rules
            if isinstance(item, Mapping) and (_trim(item.get("name")) or _trim(item.get("logic")))
        ]
        if rule_lines:
            return "\n".join(rule_lines)
        modules = design.get("modules") if isinstance(design.get("modules"), list) else []
        module_lines = [
            f"{_trim(item.get('name'))}: {_trim(item.get('responsibility'))}".strip(": ")
            for item in modules
            if isinstance(item, Mapping) and (_trim(item.get("name")) or _trim(item.get("responsibility")))
        ]
        if module_lines:
            return "\n".join(module_lines)
        return _trim(logic)

    @staticmethod
    def _design_artifact_identity(
        design: Mapping[str, Any],
        *,
        state: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        current_text = json.dumps(dict(design), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        current_fingerprint = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
        previous_state = state if isinstance(state, Mapping) else {}
        previous_fingerprint = _trim(previous_state.get("design_fingerprint"))
        previous_revision = int(previous_state.get("design_revision") or 0)
        revision = previous_revision if previous_fingerprint == current_fingerprint else previous_revision + 1
        if revision < 1:
            revision = 1
        artifact_id = _trim(previous_state.get("design_artifact_id"))
        if not artifact_id:
            seed = _trim(design.get("tool_name")) or current_fingerprint[:16]
            normalized_seed = re.sub(r"[^a-zA-Z0-9_]+", "_", seed).strip("_") or current_fingerprint[:16]
            artifact_id = f"finance_tool_spec_{normalized_seed[:48]}"
        return {
            "design_artifact_id": artifact_id,
            "design_revision": revision,
            "design_fingerprint": current_fingerprint,
        }

    @staticmethod
    def _schema_from_fields(fields: List[Any]) -> Dict[str, Any]:
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for item in fields:
            if not isinstance(item, Mapping):
                continue
            name = _trim(item.get("name"))
            if not name:
                continue
            field_type = _trim(item.get("type")) or "string"
            json_type = "object" if field_type == "dict" else field_type
            if json_type not in {"string", "number", "boolean", "array", "object", "integer"}:
                json_type = "string"
            properties[name] = {"type": json_type, "description": _trim(item.get("description"))}
            if item.get("required") is True:
                required.append(name)
        schema: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": True}
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _select_code(final: Mapping[str, Any]) -> str:
        files = final.get("files") if isinstance(final.get("files"), list) else []
        candidates: List[str] = []
        for item in files:
            if not isinstance(item, Mapping):
                continue
            content = _trim(item.get("content"))
            path = _trim(item.get("path"))
            role = _trim(item.get("role"))
            if content and (role == "tool" or path.endswith(".py") or "def run(" in content):
                candidates.append(content)
        return candidates[0] if candidates else ""

    @staticmethod
    def _sample_input(final: Mapping[str, Any]) -> Dict[str, Any]:
        sample_input_json = _trim(final.get("sample_input_json"))
        if sample_input_json:
            try:
                parsed = json.loads(sample_input_json)
                if isinstance(parsed, Mapping):
                    return dict(parsed)
            except Exception:
                pass
        tests = final.get("tests") if isinstance(final.get("tests"), list) else []
        for item in tests:
            if isinstance(item, Mapping) and isinstance(item.get("input"), Mapping):
                return dict(item.get("input") or {})
        return {}
