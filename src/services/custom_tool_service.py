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
from src.services.custom_tool_design_protocol_service import (
    CustomToolDesignProtocolError,
    CustomToolDesignProtocolService,
)


class CustomToolError(ValueError):
    pass


DESIGN_SCENARIO_CREATE_FIRST_ROUND = "create_first_round"
DESIGN_SCENARIO_CREATE_REVISION_ROUND = "create_revision_round"
DESIGN_SCENARIO_OPTIMIZE_EXISTING_TOOL = "optimize_existing_tool"
DESIGN_SCENARIOS = {
    DESIGN_SCENARIO_CREATE_FIRST_ROUND,
    DESIGN_SCENARIO_CREATE_REVISION_ROUND,
    DESIGN_SCENARIO_OPTIMIZE_EXISTING_TOOL,
}
DESIGN_POLICY_MINIMUM_CORE = {
    "scope_mode": "minimum_viable_core",
    "progressive_expansion": True,
    "implicit_adjacent_features": False,
    "first_round_budget": {
        "required_questions": 3,
        "modules": 3,
        "rules": 5,
        "outputs": 5,
        "exceptions": 3,
        "acceptance": 5,
        "flow_steps": 7,
    },
}


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


class CustomToolStoreService:
    def __init__(self, *, root_dir: Optional[str] = None, backend: str = "") -> None:
        storage_backend = _trim(backend or os.environ.get("FIN_AGENT_CUSTOM_TOOL_STORAGE") or "database").lower()
        self._database_store = None
        if root_dir is None and storage_backend == "database":
            from src.services.database_custom_tool_store_service import DatabaseCustomToolStoreService

            self._database_store = DatabaseCustomToolStoreService(error_type=CustomToolError)
        self.root_dir = Path(root_dir or "data/custom_tools")

    def tool_dir(self, tool_name: str) -> Path:
        if self._database_store is not None:
            raise CustomToolError("database custom tools do not have user-visible directories")
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
        if self._database_store is not None:
            return self._database_store.exists(tool_name)
        try:
            return (self.tool_dir(tool_name) / "manifest.json").exists()
        except CustomToolError:
            return False

    def save_draft(self, design: Mapping[str, Any], *, owner_id: str = "") -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.save_draft(design, owner_id=owner_id)
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
        spec = {
            "sample_input": dict(design.get("sample_input") or {}),
            "modules": [dict(item) for item in design.get("modules") or [] if isinstance(item, Mapping)],
            "proposed_tests": [dict(item) for item in design.get("proposed_tests") or [] if isinstance(item, Mapping)],
            "design_contract": dict(design.get("design_contract") or {}),
            "design_provenance": dict(design.get("design_provenance") or {}),
            "design_feedback_evidence": [
                dict(item) for item in design.get("design_feedback_evidence") or [] if isinstance(item, Mapping)
            ],
        }
        root.joinpath("spec.json").write_text(_json_text(spec), encoding="utf-8")
        root.joinpath("tool.py").write_text(code + "\n", encoding="utf-8")
        rev_dir = root / "revisions" / str(revision_no)
        rev_dir.mkdir(parents=True, exist_ok=True)
        rev_dir.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        rev_dir.joinpath("spec.json").write_text(_json_text(spec), encoding="utf-8")
        rev_dir.joinpath("tool.py").write_text(code + "\n", encoding="utf-8")
        return self.load(tool_name)

    def load(self, tool_name: str) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.load(tool_name)
        root = self.tool_dir(tool_name)
        if not (root / "manifest.json").exists():
            raise CustomToolError(f"custom tool not found: {tool_name}")
        spec = self._load_json(root / "spec.json")
        return {
            "manifest": json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8")),
            "input_schema": self._load_json(root / "input_schema.json"),
            "output_schema": self._load_json(root / "output_schema.json"),
            "code": root.joinpath("tool.py").read_text(encoding="utf-8") if (root / "tool.py").exists() else "",
            "modules": [dict(item) for item in spec.get("modules") or [] if isinstance(item, Mapping)],
            "sample_input": dict(spec.get("sample_input") or {}),
            "proposed_tests": [dict(item) for item in spec.get("proposed_tests") or [] if isinstance(item, Mapping)],
            "design_contract": dict(spec.get("design_contract") or {}),
            "design_provenance": dict(spec.get("design_provenance") or {}),
            "design_feedback_evidence": [
                dict(item) for item in spec.get("design_feedback_evidence") or [] if isinstance(item, Mapping)
            ],
            "root": str(root),
        }

    def list_tools(
        self,
        *,
        include_inactive: bool = False,
        owner_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        if self._database_store is not None:
            return self._database_store.list_tools(include_inactive=include_inactive, owner_ids=owner_ids)
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
        if self._database_store is not None:
            return self._database_store.load_for_runtime(tool_name, owner_ids=owner_ids, allow_inactive=allow_inactive)
        bundle = self.load(tool_name)
        manifest = dict(bundle["manifest"])
        if not allow_inactive and _trim(manifest.get("status")) != "active":
            raise CustomToolError("custom tool is not active")
        if owner_ids is not None and not self._is_visible_to_owner(manifest, self._normalize_owner_ids(owner_ids)):
            raise CustomToolError("custom tool is not visible to current user")
        return bundle

    def commit(self, tool_name: str, *, owner_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.commit(tool_name, owner_ids=owner_ids)
        bundle = self.load_for_runtime(tool_name, owner_ids=owner_ids, allow_inactive=True)
        manifest = dict(bundle["manifest"])
        last_test = manifest.get("last_test") if isinstance(manifest.get("last_test"), dict) else {}
        if last_test.get("gate_passed") is not True:
            raise CustomToolError("custom tool must pass call/smoke test before commit")
        manifest["status"] = "active"
        root = self.tool_dir(manifest["tool_name"])
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        return self.load(manifest["tool_name"])

    def record_test(self, tool_name: str, result: Mapping[str, Any]) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.record_test(tool_name, result)
        bundle = self.load(tool_name)
        manifest = dict(bundle["manifest"])
        manifest["last_test"] = {
            "ok": bool(result.get("ok")),
            "execution_ok": bool(result.get("execution_ok", result.get("ok"))),
            "contract_ok": bool(result.get("contract_ok", result.get("ok"))),
            "business_ok": bool(result.get("business_ok", result.get("ok"))),
            "gate_passed": bool(result.get("gate_passed", result.get("ok"))),
            "error": _trim(result.get("error")),
            "backend": _trim(((result.get("meta") or {}).get("diagnostics") or {}).get("backend")),
        }
        root = self.tool_dir(manifest["tool_name"])
        root.joinpath("manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        return self.load(manifest["tool_name"])

    def publish(
        self,
        tool_name: str,
        *,
        owner_ids: Optional[List[str]] = None,
        actor_id: str = "",
        actor_scopes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if self._database_store is not None:
            return self._database_store.publish(
                tool_name,
                owner_ids=owner_ids,
                actor_id=actor_id,
                actor_scopes=actor_scopes,
            )
        if "custom_tool:publish" not in {_trim(item) for item in actor_scopes or []}:
            raise CustomToolError("public publication requires custom_tool:publish permission")
        bundle = self.load_for_runtime(tool_name, owner_ids=owner_ids, allow_inactive=False)
        manifest = dict(bundle["manifest"])
        if (manifest.get("last_test") or {}).get("gate_passed") is not True:
            raise CustomToolError("custom tool must pass the test gate before publication")
        manifest["visibility"] = "public"
        manifest["published_by"] = _trim(actor_id)
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
        finance_query_fixture: Optional[Mapping[str, Any]] = None,
        max_finance_queries: int = 4,
    ) -> None:
        self.store = store or CustomToolStoreService()
        self.python_runtime = python_runtime or PythonExecutionRuntime(allow_unsafe_backends=True)
        self.runtime_root = Path(runtime_root)
        self.finance_query_fixture = dict(finance_query_fixture) if isinstance(finance_query_fixture, Mapping) else None
        self.max_finance_queries = max(1, min(int(max_finance_queries or 4), 8))

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
        runtime_result: Dict[str, Any] = {}
        finance_responses: Dict[str, Any] = {}
        finance_bridge_rounds = 0
        finance_bridge_errors: List[str] = []
        for attempt in range(1, self.max_finance_queries + 2):
            for child in output_dir.iterdir():
                if child.is_file():
                    child.unlink()
            input_dir.joinpath("finance_responses.json").write_text(
                json.dumps(finance_responses, ensure_ascii=False),
                encoding="utf-8",
            )
            runtime_result = self.python_runtime.execute(
                code=code,
                input_dir=str(input_dir),
                output_dir=str(output_dir),
                profile=profile,
                attempt=attempt,
            )
            pending_requests = self._load_finance_requests(output_dir / "finance_requests.json")
            unresolved = [request for request in pending_requests if request not in finance_responses]
            if not unresolved:
                break
            if len(finance_responses) + len(unresolved) > self.max_finance_queries:
                return self._error(
                    manifest["tool_name"],
                    "finance_query_limit",
                    f"custom tool exceeded finance query limit ({self.max_finance_queries})",
                )
            finance_bridge_rounds += 1
            for request in unresolved:
                if len(request) > 4000:
                    return self._error(manifest["tool_name"], "finance_query_invalid", "finance query is too long")
                allowed, denial = self._finance_request_allowed(request, bundle)
                if not allowed:
                    return self._error(manifest["tool_name"], "finance_query_denied", denial)
                try:
                    from src.services.finance_data_tool_runtime_service import FinanceDataToolRuntimeService

                    finance_responses[request] = FinanceDataToolRuntimeService().execute_request(request=request)
                except Exception:
                    finance_bridge_errors.append("finance API execution failed")
                    finance_responses[request] = {
                        "ok": False,
                        "error": "finance API execution failed",
                        "data": [],
                    }
        else:
            return self._error(manifest["tool_name"], "finance_query_round_limit", "finance query bridge did not converge")
        diagnostics = dict(runtime_result.get("diagnostics") or {})
        execution_logs = self._load_execution_logs(output_dir / "execution_logs.jsonl")
        diagnostics.update({
            "finance_query_count": len(finance_responses),
            "finance_bridge_rounds": finance_bridge_rounds,
            "finance_bridge_errors": finance_bridge_errors,
            "execution_log_count": len(execution_logs),
        })
        if execution_logs:
            diagnostics["execution_logs"] = execution_logs
        if not runtime_result.get("ok"):
            return self._error(manifest["tool_name"], _trim(runtime_result.get("failure_kind")) or "runtime_error", _trim(diagnostics.get("stderr")), diagnostics=diagnostics)
        output_path = output_dir / "output.json"
        if not output_path.exists():
            return self._error(manifest["tool_name"], "output_missing", "custom tool did not write output.json", diagnostics=diagnostics)
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return self._error(manifest["tool_name"], "output_json_error", str(exc), diagnostics=diagnostics)
        output_errors = self._validate_json_value(payload, bundle.get("output_schema") or {}, path="output")
        if output_errors:
            return self._error(
                manifest["tool_name"],
                "output_validation_error",
                "; ".join(output_errors),
                diagnostics=diagnostics,
            )
        result = {
            "tool": manifest["tool_name"],
            "ok": True,
            "data": payload if isinstance(payload, dict) else {"value": payload},
            "error": "",
            "meta": {
                "custom_tool": True,
                "display_name": manifest.get("display_name"),
                "revision": manifest.get("current_revision"),
                "execution_logs": execution_logs,
                "diagnostics": diagnostics,
            },
        }
        return result

    def _ensure_runtime_root(self) -> Path:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        return self.runtime_root

    def _validate_input(self, payload: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
        return self._validate_json_value(payload, schema, path="input")

    @staticmethod
    def _load_finance_requests(path: Path) -> List[str]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [str(item).strip() for item in payload if isinstance(item, str) and str(item).strip()] if isinstance(payload, list) else []

    @staticmethod
    def _load_execution_logs(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        logs: List[Dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        for line in lines[:50]:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict) or item.get("level") not in {"info", "debug"}:
                continue
            logs.append({
                "level": str(item.get("level")),
                "message": _trim(item.get("message"))[:160],
                "data": dict(item.get("data") or {}) if isinstance(item.get("data"), Mapping) else {},
            })
        return logs

    @staticmethod
    def _finance_request_allowed(request: str, bundle: Mapping[str, Any]) -> tuple[bool, str]:
        design = bundle.get("design_contract") if isinstance(bundle.get("design_contract"), Mapping) else {}
        requirements = design.get("data_requirements") if isinstance(design.get("data_requirements"), list) else []
        allowed_apis = {
            _trim(item.get("source_ref"))
            for item in requirements
            if isinstance(item, Mapping) and _trim(item.get("source_ref"))
        }
        try:
            from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call

            api_name = _trim(parse_api_call(request).api)
        except Exception:
            return False, "finance query could not be parsed"
        if allowed_apis and api_name not in allowed_apis:
            return False, f"finance API {api_name or '-'} is outside the confirmed design"
        return True, ""

    @classmethod
    def _validate_json_value(cls, value: Any, schema: Mapping[str, Any], *, path: str) -> List[str]:
        if not isinstance(schema, Mapping) or not schema:
            return []
        expected_type = _trim(schema.get("type"))
        type_checks = {
            "object": lambda item: isinstance(item, Mapping),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
        }
        if expected_type in type_checks and not type_checks[expected_type](value):
            return [f"{path} must be {expected_type}"]
        errors: List[str] = []
        if expected_type == "object" and isinstance(value, Mapping):
            properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
            for key in schema.get("required") or []:
                if key not in value:
                    errors.append(f"missing required {path}: {key}")
            if schema.get("additionalProperties") is False:
                for key in value.keys():
                    if key not in properties:
                        errors.append(f"unknown {path}: {key}")
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, Mapping):
                    errors.extend(cls._validate_json_value(value[key], child_schema, path=f"{path}.{key}"))
        if expected_type == "array" and isinstance(value, list) and isinstance(schema.get("items"), Mapping):
            for index, item in enumerate(value):
                errors.extend(cls._validate_json_value(item, schema["items"], path=f"{path}[{index}]"))
        return errors

    def _wrap_code(self, code: str, *, tool_name: str) -> str:
        code_body = self._strip_future_imports(code)
        fixture_json = json.dumps(self.finance_query_fixture, ensure_ascii=False) if self.finance_query_fixture is not None else ""
        fixture_branch = (
            f"_raw = json.loads({fixture_json!r})"
            if fixture_json
            else """request_text = str(request or \"\").strip()
    try:
        with open(os.path.join(os.environ[\"CODE_INPUT_DIR\"], \"finance_responses.json\"), \"r\", encoding=\"utf-8\") as cache_file:
            response_cache = json.load(cache_file)
    except Exception:
        response_cache = {}
    if isinstance(response_cache, dict) and request_text in response_cache:
        _raw = response_cache[request_text]
    else:
        request_path = os.path.join(os.environ[\"CODE_OUTPUT_DIR\"], \"finance_requests.json\")
        try:
            with open(request_path, \"r\", encoding=\"utf-8\") as request_file:
                requests = json.load(request_file)
        except Exception:
            requests = []
        if not isinstance(requests, list):
            requests = []
        if request_text and request_text not in requests:
            requests.append(request_text)
        with open(request_path, \"w\", encoding=\"utf-8\") as request_file:
            json.dump(requests, request_file, ensure_ascii=False)
        _raw = {\"ok\": False, \"error\": \"finance query pending host execution\", \"data\": []}"""
        )
        return f'''from __future__ import annotations

import json
import os
import types
import sys


def _normalize_finance_query_result(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {{"ok": False, "error": "finance query returned an invalid envelope", "data": [], "rows": [], "columns": []}}
    normalized = dict(raw)
    validation = raw.get("validation") if isinstance(raw.get("validation"), dict) else {{}}
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {{}}
    provider_data = result.get("data")
    if isinstance(provider_data, list):
        rows = provider_data
    elif isinstance(provider_data, dict) and isinstance(provider_data.get("rows"), list):
        rows = provider_data.get("rows") or []
    elif isinstance(raw.get("rows"), list):
        rows = raw.get("rows") or []
    elif isinstance(raw.get("data"), list):
        rows = raw.get("data") or []
    else:
        rows = []
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    explicit_error = str(raw.get("error") or "").strip()
    ok = raw.get("ok") is not False and validation.get("ok") is not False and not explicit_error
    normalized.update({{
        "ok": bool(ok),
        "error": explicit_error or ("; ".join(str(item) for item in errors) if errors else ""),
        "data": rows,
        "rows": rows,
        "columns": list(result.get("columns") or raw.get("columns") or []),
    }})
    return normalized


def finance_query(request: str) -> dict:
    {fixture_branch}
    return _normalize_finance_query_result(_raw)


def web_search(query: str, limit: int = 5) -> dict:
    return {{
        "ok": False,
        "error": "web_search provider is not configured for custom_tool_sdk",
        "query": str(query or ""),
        "limit": int(limit or 5),
        "items": [],
    }}


_execution_log_count = 0


def _execution_log(level: str, message: str, data: object = None) -> None:
    global _execution_log_count
    if _execution_log_count >= 50:
        return
    normalized_data = data if isinstance(data, dict) else ({{"value": data}} if data is not None else {{}})
    try:
        normalized_data = json.loads(json.dumps(normalized_data, ensure_ascii=False, default=str))
    except Exception:
        normalized_data = {{"value": str(normalized_data)}}
    record = {{
        "level": level,
        "message": str(message or "")[:160],
        "data": normalized_data,
    }}
    line = json.dumps(record, ensure_ascii=False)
    if len(line) > 8000:
        record["data"] = {{"summary": str(normalized_data)[:6000], "truncated": True}}
        line = json.dumps(record, ensure_ascii=False)
    with open(os.path.join(os.environ["CODE_OUTPUT_DIR"], "execution_logs.jsonl"), "a", encoding="utf-8") as log_file:
        log_file.write(line + "\\n")
    _execution_log_count += 1


def info(message: str, data: object = None) -> None:
    _execution_log("info", message, data)


def debug(message: str, data: object = None) -> None:
    _execution_log("debug", message, data)


_sdk = types.ModuleType("custom_tool_sdk")
_sdk.finance_query = finance_query
_sdk.web_search = web_search
_sdk.info = info
_sdk.debug = debug
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
        design_protocol: Optional[CustomToolDesignProtocolService] = None,
        use_codex: Optional[bool] = None,
    ) -> None:
        self.store = store or CustomToolStoreService()
        codex_enabled = bool(use_codex) if use_codex is not None else _trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX", "1")) not in {"0", "false", "False", "no"}
        self.designer = designer or (self._default_codex_designer() if codex_enabled else CustomToolDesigner())
        self.coder = coder or (self._default_codex_coder() if codex_enabled else None)
        self.runtime = runtime or CustomToolRuntimeService(store=self.store)
        self.design_protocol = design_protocol or CustomToolDesignProtocolService()

    @staticmethod
    def _clean_state_for_context(state: Mapping[str, Any] | None) -> Dict[str, Any]:
        source = dict(state or {})
        for key in ("events", "coding_events", "raw", "last_message", "raw_stdout", "raw_stderr", "feedback_ledger"):
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
        design_scenario: str = DESIGN_SCENARIO_CREATE_FIRST_ROUND,
        turn_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        requirement = _trim(requirement_text)
        if not requirement:
            raise CustomToolError("创建工具时请先描述目标、输入、输出或核心规则。")
        scenario = self._normalize_design_scenario(design_scenario)
        prior_state = dict(state or {}) if scenario != DESIGN_SCENARIO_CREATE_FIRST_ROUND else {}
        design_round = 1 if scenario == DESIGN_SCENARIO_CREATE_FIRST_ROUND else max(2, int(prior_state.get("design_round") or 1) + 1)
        feedback_ledger = self.design_protocol.append_feedback(
            prior_state.get("feedback_ledger"),
            text=requirement,
            design_round=design_round,
            turn_id=turn_id,
            kind="initial_requirement" if scenario == DESIGN_SCENARIO_CREATE_FIRST_ROUND else "feedback",
        )
        design_result = self._call_designer(
            requirement,
            state=prior_state,
            owner_id=owner_id,
            design_scenario=scenario,
            design_round=design_round,
            event_sink=event_sink,
        )
        status = _trim(design_result.get("status"))
        if status == "ok":
            return self._return_legacy_design(requirement, owner_id=owner_id, design=design_result["design"])
        if _trim(design_result.get("protocol_mode")) == "revision_fields":
            try:
                merged = self.design_protocol.apply_revision(
                    self.design_protocol.canonical_from_state(prior_state),
                    design_result,
                )
            except CustomToolDesignProtocolError as exc:
                raise CustomToolError(str(exc)) from exc
            design_result = {
                **design_result,
                "status": merged["status"],
                "understanding": merged["understanding"],
                "questions": merged["questions"],
                "design": merged["design"],
                "existing_analysis": merged["existing_analysis"],
            }
        design_ready = status in {"review", "design_ready"}
        understanding = design_result.get("understanding") if isinstance(design_result.get("understanding"), Mapping) else {}
        existing_analysis = design_result.get("existing_analysis") if isinstance(design_result.get("existing_analysis"), Mapping) else {}
        if scenario == DESIGN_SCENARIO_CREATE_FIRST_ROUND:
            existing_analysis = self._empty_existing_analysis()
        questions = design_result.get("questions") if isinstance(design_result.get("questions"), list) else []
        design = design_result.get("design") if isinstance(design_result.get("design"), Mapping) else {}
        design_artifact = self._design_artifact_identity(design, state=prior_state)
        canonical_requirement = _trim(prior_state.get("requirement_text")) if prior_state else requirement
        if not canonical_requirement:
            canonical_requirement = requirement
        design_context = {
            "scenario": scenario,
            "round": design_round,
            "is_first_round": scenario == DESIGN_SCENARIO_CREATE_FIRST_ROUND,
        }
        if not design_ready:
            next_state = {
                "status": "collect_requirement",
                "requirement_text": canonical_requirement,
                "latest_feedback_text": requirement,
                "feedback_ledger": feedback_ledger,
                "owner_id": owner_id,
                "design_scenario": scenario,
                "design_round": design_round,
                "partial_design": design,
                "understanding": dict(understanding),
                "questions": questions,
                "existing_analysis": dict(existing_analysis),
                **design_artifact,
            }
            return {
                "message": design_result.get("message") or "请补充工具需求。",
                "design_status": status or "clarification",
                "understanding": dict(understanding),
                "questions": questions or design_result.get("missing") or [],
                "design": design,
                "design_artifact": design_artifact,
                "design_context": design_context,
                "existing_analysis": dict(existing_analysis),
                "events": design_result.get("events") or [],
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        next_state = {
            "status": "awaiting_design_confirmation",
            "requirement_text": canonical_requirement,
            "latest_feedback_text": requirement,
            "feedback_ledger": feedback_ledger,
            "owner_id": owner_id,
            "design_scenario": scenario,
            "design_round": design_round,
            "design_contract": design,
            "understanding": dict(understanding),
            "existing_analysis": dict(existing_analysis),
            "questions": questions,
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
            "design_context": design_context,
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
        turn_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        status = _trim(state.get("status"))
        raw = _trim(text)
        if status == "collect_requirement":
            return self.start_create(
                raw,
                owner_id=owner_id or _trim(state.get("owner_id")),
                state=state,
                design_scenario=DESIGN_SCENARIO_CREATE_REVISION_ROUND,
                turn_id=turn_id,
                event_sink=event_sink,
            )
        if status == "awaiting_design_confirmation":
            if raw in {"确认", "确认实现", "可以", "实现", "生成", "ok", "OK"}:
                return self._confirm_and_code(state=state, owner_id=owner_id, event_sink=event_sink)
            return self.start_create(
                raw,
                owner_id=owner_id or _trim(state.get("owner_id")),
                state=state,
                design_scenario=DESIGN_SCENARIO_CREATE_REVISION_ROUND,
                turn_id=turn_id,
                event_sink=event_sink,
            )
        if status in {"draft_ready", "draft_needs_test"}:
            if not raw:
                return {"message": "请说明需要修改的实现或测试问题。", "state": dict(state)}
            revision_state = {
                **self._clean_state_for_context(state),
                "status": "awaiting_design_confirmation",
                "coding_feedback": raw,
            }
            return self._confirm_and_code(state=revision_state, owner_id=owner_id, event_sink=event_sink)
        if status == "coding_failed":
            retry_state = {
                **self._clean_state_for_context(state),
                "status": "awaiting_design_confirmation",
            }
            if raw and raw not in {"重试", "重新生成", "再试一次", "确认", "确认实现"}:
                retry_state["coding_feedback"] = raw
            return self._confirm_and_code(state=retry_state, owner_id=owner_id, event_sink=event_sink)
        return {"message": "当前没有进行中的自定义工具创建流程。", "state": {}}

    def continue_flow_action(
        self,
        action_id: str,
        *,
        state: Mapping[str, Any],
        expected_revision: Optional[int] = None,
        owner_id: str = "",
        turn_id: Optional[int] = None,
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
            confirmed_state = dict(state)
            confirmed_state["feedback_ledger"] = self.design_protocol.append_feedback(
                state.get("feedback_ledger"),
                text="确认设计并进入 Coding",
                design_round=int(state.get("design_round") or state.get("design_revision") or 1),
                turn_id=turn_id,
                kind="confirmation",
            )
            return self._confirm_and_code(state=confirmed_state, owner_id=owner_id, event_sink=event_sink)
        if status == "draft_ready" and normalized_action == "custom_tool.activate_draft":
            tool_name = _trim(state.get("tool_name"))
            if not tool_name:
                raise CustomToolError("draft state does not include tool_name")
            current_revision = int(state.get("implementation_revision") or 0)
            if expected_revision is not None and int(expected_revision) != current_revision:
                raise CustomToolError(
                    f"implementation revision changed: expected {int(expected_revision)}, current {current_revision}"
                )
            committed = self.commit(tool_name, owner_ids=[owner_id] if owner_id else None)
            return {
                "message": f"{_trim((committed.get('manifest') or {}).get('display_name')) or tool_name} 已确认并启用。",
                "state": {},
                "tool": committed,
                "activation": {
                    "status": "active",
                    "tool_name": tool_name,
                    "implementation_revision": int((committed.get("manifest") or {}).get("current_revision") or 0),
                },
                "thread_context_patch": {"custom_tool_state": None},
            }
        if status == "coding_failed" and normalized_action == "custom_tool.retry_coding":
            retry_state = {
                **self._clean_state_for_context(state),
                "status": "awaiting_design_confirmation",
            }
            return self._confirm_and_code(state=retry_state, owner_id=owner_id, event_sink=event_sink)
        raise CustomToolError(
            f"action {normalized_action or '-'} is not allowed while custom tool state is {status or '-'}"
        )

    @staticmethod
    def interaction_user_text(action_id: str) -> str:
        labels = {
            "custom_tool.confirm_design": "确认并继续",
            "custom_tool.activate_draft": "确认并启用",
            "custom_tool.retry_coding": "重试实现",
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
        design_scenario: str,
        design_round: int,
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "owner_id": owner_id,
            "design_scenario": design_scenario,
            "design_round": design_round,
            "design_policy": DESIGN_POLICY_MINIMUM_CORE,
        }
        if design_scenario == DESIGN_SCENARIO_CREATE_FIRST_ROUND:
            context["state"] = {}
        else:
            canonical = self.design_protocol.canonical_from_state(state or {})
            context.update({
                "canonical_understanding": canonical["understanding"],
                "canonical_design": canonical["design"],
                "canonical_existing_analysis": canonical["existing_analysis"],
                "canonical_revision": int((state or {}).get("design_revision") or 0),
                "canonical_fingerprint": _trim((state or {}).get("design_fingerprint")),
            })
        try:
            return self.designer.design(requirement_text, context=context, event_sink=event_sink)
        except TypeError:
            return self.designer.design(requirement_text)

    @staticmethod
    def _normalize_design_scenario(value: Any) -> str:
        scenario = _trim(value) or DESIGN_SCENARIO_CREATE_FIRST_ROUND
        if scenario not in DESIGN_SCENARIOS:
            raise CustomToolError(f"unsupported design scenario: {scenario}")
        return scenario

    @staticmethod
    def _empty_existing_analysis() -> Dict[str, Any]:
        return {
            "analyzed": False,
            "current_behavior": [],
            "gaps": [],
            "affected_areas": [],
            "evidence": [],
        }

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
            coding_context: Dict[str, Any] = {"state": self._clean_state_for_context(state)}
            current_tool_name = _trim(state.get("tool_name"))
            if current_tool_name and self.store.exists(current_tool_name):
                current_bundle = self.store.load(current_tool_name)
                coding_context["current_implementation"] = {
                    "revision": int((current_bundle.get("manifest") or {}).get("current_revision") or 0),
                    "modules": [dict(item) for item in current_bundle.get("modules") or [] if isinstance(item, Mapping)],
                    "last_test": dict((current_bundle.get("manifest") or {}).get("last_test") or {}),
                }
            coding_result = self.coder.code(
                design_contract,
                requirement_text=_trim(state.get("requirement_text")),
                context=coding_context,
                event_sink=event_sink,
            )
        except TypeError:
            coding_result = self.coder.code(
                design_contract,
                requirement_text=_trim(state.get("requirement_text")),
                context={"state": self._clean_state_for_context(state)},
            )
        coding_status = _trim(coding_result.get("status"))
        if coding_status != "code_ready":
            retryable = coding_status == "coding_failed"
            coding_error = coding_result.get("error") if isinstance(coding_result.get("error"), Mapping) else {}
            next_state = {
                **self._clean_state_for_context(state),
                "status": "coding_failed" if retryable else "awaiting_design_confirmation",
                "coding_feedback": coding_result.get("message") or coding_result.get("final", {}).get("need_design_fix"),
                "coding_error": dict(coding_error),
            }
            return {
                "message": coding_result.get("message") or "代码生成需要回到设计阶段确认。",
                "coding_status": coding_status or "need_design_fix",
                "coding_error": dict(coding_error),
                "events": coding_result.get("events") or [],
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        bundle_design = self._bundle_from_coding_final(
            design_contract,
            coding_result.get("final") if isinstance(coding_result.get("final"), Mapping) else {},
        )
        bundle_design["requirement_text"] = _trim(state.get("requirement_text"))
        bundle_design["design_provenance"] = {
            "artifact_id": _trim(state.get("design_artifact_id")),
            "revision": int(state.get("design_revision") or 0),
            "fingerprint": _trim(state.get("design_fingerprint")),
        }
        bundle_design["design_feedback_evidence"] = [
            dict(item) for item in state.get("feedback_ledger") or [] if isinstance(item, Mapping)
        ]
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
        proposed_tests = [dict(item) for item in design.get("proposed_tests") or [] if isinstance(item, Mapping)]
        test_result = self.runtime.run(
            manifest["tool_name"],
            sample_input,
            owner_ids=[owner_id] if owner_id else None,
            allow_inactive=True,
        )
        expected = self._expected_for_sample(proposed_tests, sample_input)
        execution_ok = bool(test_result.get("ok"))
        contract_ok = execution_ok
        business_ok = execution_ok and self._business_result_matches(
            test_result.get("data") if isinstance(test_result.get("data"), Mapping) else {},
            expected,
        )
        test_result.update({
            "execution_ok": execution_ok,
            "contract_ok": contract_ok,
            "business_ok": business_ok,
            "gate_passed": execution_ok and contract_ok and business_ok,
        })
        execution_logs = [
            dict(item)
            for item in ((test_result.get("meta") or {}).get("execution_logs") or [])
            if isinstance(item, Mapping)
        ]
        test_result["cases"] = [{
            "test_id": "sample_smoke",
            "category": "happy_path",
            "status": "passed" if test_result.get("gate_passed") else "failed",
            "input": sample_input,
            "expected": expected or {"business_result": "no top-level error and no ok=false"},
            "actual": dict(test_result.get("data") or {}),
            "logs": execution_logs,
            "purpose": "验证动态加载、沙箱执行、输出 Schema 和样例业务预期。",
            "error": _trim(test_result.get("error")) or ("业务结果未达到样例预期。" if not business_ok else ""),
        }]
        test_result["proposed_cases"] = proposed_tests
        test_result["summary"] = "1 / 1 项运行测试通过" if test_result.get("gate_passed") else "0 / 1 项运行测试通过"
        saved = self.store.record_test(manifest["tool_name"], test_result)
        next_status = "draft_ready" if test_result.get("gate_passed") else "draft_needs_test"
        next_state = {
            "status": next_status,
            "tool_name": manifest["tool_name"],
            "owner_id": owner_id,
            "implementation_revision": int(manifest.get("current_revision") or 0),
            "requirement_text": _trim(design.get("requirement_text")),
            "design_contract": dict(design.get("design_contract") or {}),
        }
        return {
            "message": (
                f"已生成 draft：{manifest['tool_name']}。\n"
                f"样例测试：{'通过' if test_result.get('gate_passed') else '失败'}"
                + (f"\n错误：{test_result.get('error') or '业务结果未达到样例预期'}" if not test_result.get("gate_passed") else "")
                + (
                    "\n确认可用后可在当前设计卡片中启用；启用后仍保持个人私有。"
                    if test_result.get("gate_passed")
                    else "\n请修订实现或测试输入；只有三道测试门禁全部通过后才能启用。"
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
                "implementation_logic": self._logic_text(design) or _trim((final.get("implementation") or {}).get("summary")),
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 30000},
            },
            "input_schema": self._schema_from_fields(design.get("inputs") if isinstance(design.get("inputs"), list) else []),
            "output_schema": self._schema_from_fields(
                design.get("outputs") if isinstance(design.get("outputs"), list) else [],
                require_all=True,
            ),
            "code": code,
            "sample_input": self._sample_input(final),
            "modules": [dict(item) for item in ((final.get("implementation") or {}).get("modules") or []) if isinstance(item, Mapping)],
            "proposed_tests": [dict(item) for item in final.get("tests") or [] if isinstance(item, Mapping)],
            "design_contract": dict(design),
        }

    @staticmethod
    def _expected_for_sample(tests: List[Dict[str, Any]], sample_input: Mapping[str, Any]) -> Dict[str, Any]:
        for item in tests:
            if _trim(item.get("category")) != "happy_path":
                continue
            input_value = item.get("input") if isinstance(item.get("input"), Mapping) else None
            expected_value = item.get("expected") if isinstance(item.get("expected"), Mapping) else None
            if input_value is None:
                try:
                    parsed_input = json.loads(_trim(item.get("input_json")) or "{}")
                    input_value = parsed_input if isinstance(parsed_input, Mapping) else None
                except json.JSONDecodeError:
                    input_value = None
            if expected_value is None:
                try:
                    parsed_expected = json.loads(_trim(item.get("expected_json")) or "{}")
                    expected_value = parsed_expected if isinstance(parsed_expected, Mapping) else None
                except json.JSONDecodeError:
                    expected_value = None
            if input_value is None or dict(input_value) == dict(sample_input):
                return dict(expected_value or {})
        return {}

    @classmethod
    def _business_result_matches(cls, actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
        if actual.get("ok") is False or actual.get("error") not in (None, "", {}, []):
            return False
        return cls._contains_expected(actual, expected)

    @classmethod
    def _contains_expected(cls, actual: Any, expected: Any) -> bool:
        if isinstance(expected, Mapping):
            return isinstance(actual, Mapping) and all(
                key in actual and cls._contains_expected(actual[key], value)
                for key, value in expected.items()
            )
        if isinstance(expected, list):
            return isinstance(actual, list) and actual == expected
        return actual == expected

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
    def _schema_from_fields(fields: List[Any], *, require_all: bool = False) -> Dict[str, Any]:
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
            field_schema: Dict[str, Any] = {"type": json_type, "description": _trim(item.get("description"))}
            raw_values = item.get("values") or []
            if json_type == "string":
                values = [value for value in raw_values if isinstance(value, str)]
            elif json_type == "integer":
                values = [value for value in raw_values if isinstance(value, int) and not isinstance(value, bool)]
            elif json_type == "number":
                values = [value for value in raw_values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            elif json_type == "boolean":
                values = [value for value in raw_values if isinstance(value, bool)]
            else:
                values = []
            if values:
                field_schema["enum"] = values
            properties[name] = field_schema
            if require_all or item.get("required") is True:
                required.append(name)
        schema: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": True}
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _select_code(final: Mapping[str, Any]) -> str:
        implementation = final.get("implementation") if isinstance(final.get("implementation"), Mapping) else {}
        entry_module = _trim(implementation.get("entry_module"))
        modules = [item for item in implementation.get("modules") or [] if isinstance(item, Mapping)]
        for item in modules:
            if _trim(item.get("module_id")) == entry_module and _trim(item.get("source_code")):
                return _trim(item.get("source_code"))
        for item in modules:
            if _trim(item.get("source_code")):
                return _trim(item.get("source_code"))
        return ""

    @staticmethod
    def _sample_input(final: Mapping[str, Any]) -> Dict[str, Any]:
        if isinstance(final.get("sample_input"), Mapping):
            return dict(final.get("sample_input") or {})
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
