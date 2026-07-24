from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional

from src.services.codex_exec_skill_harness import (
    CodexCustomToolCoder,
    CodexCustomToolDesigner,
    CodexCustomToolTester,
)
from src.services.agent_providers import build_agent_skill_harness
from src.services.python_execution_runtime import PythonExecutionRuntime
from src.services.custom_tool_design_protocol_service import CustomToolDesignProtocolService
from src.services.design_narrative_service import compose_design_narrative


class CustomToolError(ValueError):
    pass


DEFAULT_MAX_TEST_TURNS = 4

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
            "implementation_explanation": dict(design.get("implementation_explanation") or {}),
            "implementation_review": dict(design.get("implementation_review") or {}),
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
            "implementation_explanation": dict(spec.get("implementation_explanation") or {}),
            "implementation_review": dict(spec.get("implementation_review") or {}),
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
        if last_test.get("execution_ok") is not True:
            raise CustomToolError("custom tool must complete a technical run before commit")
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
        if (manifest.get("last_test") or {}).get("execution_ok") is not True:
            raise CustomToolError("custom tool must complete a technical run before publication")
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
            diagnostics["actual_output"] = payload
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

    @classmethod
    def _finance_request_allowed(cls, request: str, bundle: Mapping[str, Any]) -> tuple[bool, str]:
        """Allow system Finance APIs; Design does not act as a runtime allowlist."""
        try:
            from src.experiments.staged_data_protocol.phase2.catalog import resolve_api
            from src.experiments.staged_data_protocol.phase2.call_parser import parse_api_call

            api_call = parse_api_call(request)
            api_name = _trim(api_call.api)
            if api_name and resolve_api(api_name):
                return True, ""
        except Exception:
            return False, "finance query could not be parsed"
        return False, f"finance API {api_name or '-'} is not available in the system API catalog"

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
        tester: Optional[Any] = None,
        runtime: Optional[CustomToolRuntimeService] = None,
        design_protocol: Optional[CustomToolDesignProtocolService] = None,
        use_codex: Optional[bool] = None,
        agent_provider: str = "",
        design_provider: str = "",
        coding_provider: str = "",
        design_complexity: str = "",
        coding_complexity: str = "",
        finance_cc_service: Optional[Any] = None,
    ) -> None:
        self.store = store or CustomToolStoreService()
        enabled_setting = os.environ.get("CUSTOM_TOOL_AGENT_ENABLED")
        if enabled_setting is None:
            enabled_setting = os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX", "1")
        agent_enabled = bool(use_codex) if use_codex is not None else _trim(enabled_setting) not in {"0", "false", "False", "no"}
        explicit_provider = _trim(agent_provider).lower()
        legacy_provider = _trim(os.environ.get("CUSTOM_TOOL_AGENT_PROVIDER")).lower()
        legacy_complexity = _trim(os.environ.get("CUSTOM_TOOL_AGENT_COMPLEXITY")).lower()
        self.design_provider = _trim(
            design_provider or explicit_provider or os.environ.get("CUSTOM_TOOL_DESIGN_PROVIDER") or legacy_provider or "claude"
        ).lower()
        self.coding_provider = _trim(
            coding_provider or explicit_provider or os.environ.get("CUSTOM_TOOL_CODING_PROVIDER") or legacy_provider or "codex"
        ).lower()
        self.design_complexity = _trim(
            design_complexity or os.environ.get("CUSTOM_TOOL_DESIGN_COMPLEXITY") or legacy_complexity or "fast"
        ).lower()
        self.coding_complexity = _trim(
            coding_complexity or os.environ.get("CUSTOM_TOOL_CODING_COMPLEXITY") or legacy_complexity or "fastest"
        ).lower()
        self.agent_provider = explicit_provider or legacy_provider or self.design_provider
        self.designer = designer or (self._default_agent_designer() if agent_enabled else CustomToolDesigner())
        self.coder = coder or (self._default_agent_coder() if agent_enabled else None)
        self.tester = tester or (self._default_agent_tester() if agent_enabled else None)
        self.runtime = runtime or CustomToolRuntimeService(store=self.store)
        self.design_protocol = design_protocol or CustomToolDesignProtocolService()
        self.finance_cc_service = finance_cc_service

    @property
    def finance_cc_enabled(self) -> bool:
        return (
            self.finance_cc_service is not None
            and _trim(os.environ.get("FINANCE_CC_TOOL_DEVELOPMENT_ENABLED")).lower() in {"1", "true", "yes", "on"}
        )

    def set_finance_cc_service(self, service: Any) -> None:
        self.finance_cc_service = service

    @staticmethod
    def _clean_state_for_context(state: Mapping[str, Any] | None) -> Dict[str, Any]:
        source = dict(state or {})
        for key in ("status", "events", "coding_events", "raw", "last_message", "raw_stdout", "raw_stderr"):
            source.pop(key, None)
        return source

    def _default_agent_harness(self, provider: str, complexity: str) -> Any:
        from src.services.agent_providers import AgentCapabilityPolicy

        runner = _trim(os.environ.get("STOCK_AGENT_CUSTOM_TOOL_CODEX_RUNNER") or "sdk")
        if provider == "codex" and runner == "exec":
            return None
        harness = build_agent_skill_harness(
            provider,
            cwd=".",
            complexity=complexity,
            capabilities=AgentCapabilityPolicy(),
        )
        return harness if provider != "codex" or harness.available() else None

    def _default_agent_designer(self) -> CodexCustomToolDesigner:
        harness = self._default_agent_harness(self.design_provider, self.design_complexity)
        return CodexCustomToolDesigner(harness=harness) if harness is not None else CodexCustomToolDesigner()

    def _default_agent_coder(self) -> CodexCustomToolCoder:
        harness = self._default_agent_harness(self.coding_provider, self.coding_complexity)
        return CodexCustomToolCoder(harness=harness) if harness is not None else CodexCustomToolCoder()

    def _default_agent_tester(self) -> CodexCustomToolTester:
        harness = self._default_agent_harness(self.design_provider, self.design_complexity)
        return CodexCustomToolTester(harness=harness) if harness is not None else CodexCustomToolTester()

    # Backward-compatible private helpers for existing tests and integrations.
    def _default_codex_harness(self) -> Any:
        return self._default_agent_harness(self.coding_provider, self.coding_complexity)

    def _default_codex_designer(self) -> CodexCustomToolDesigner:
        return self._default_agent_designer()

    def _default_codex_coder(self) -> CodexCustomToolCoder:
        return self._default_agent_coder()

    def start_create(
        self,
        requirement_text: str,
        *,
        owner_id: str = "",
        state: Optional[Mapping[str, Any]] = None,
        selected_skills: Optional[List[str]] = None,
        turn_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Enter the same LLM-planned tool workflow used by every later turn."""
        if selected_skills is None:
            return self.handle_turn(
                requirement_text,
                state=dict(state or {}),
                owner_id=owner_id,
                turn_id=turn_id,
                thread_id=thread_id,
                event_sink=event_sink,
            )
        return self._run_design_skills(
            requirement_text,
            owner_id=owner_id,
            state=state,
            selected_skills=selected_skills,
            turn_id=turn_id,
            event_sink=event_sink,
        )

    def _run_design_skills(
        self,
        requirement_text: str,
        *,
        owner_id: str = "",
        state: Optional[Mapping[str, Any]] = None,
        selected_skills: List[str],
        turn_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        requirement = _trim(requirement_text)
        if not requirement:
            raise CustomToolError("创建工具时请先描述目标、输入、输出或核心规则。")
        prior_state = dict(state or {})
        design_round = max(1, int(prior_state.get("design_round") or 0) + 1)
        feedback_ledger = self.design_protocol.append_feedback(
            prior_state.get("feedback_ledger"),
            text=requirement,
            design_round=design_round,
            turn_id=turn_id,
            kind="initial_requirement" if not prior_state else "feedback",
        )
        design_result = self._call_designer(
            requirement,
            state=prior_state,
            owner_id=owner_id,
            selected_skills=selected_skills,
            turn_id=turn_id,
            event_sink=event_sink,
        )
        if not design_result.get("ok"):
            return {
                "message": _trim(design_result.get("message")) or "Design 调用失败，当前设计和实现均未改变。",
                "design_status": "design_failed",
                "error": design_result.get("error") or "design execution failed",
                "events": design_result.get("events") or [],
                "state": prior_state,
                "thread_context_patch": {"custom_tool_state": prior_state},
            }
        if isinstance(design_result.get("design"), Mapping) and design_result.get("design", {}).get("code"):
            return self._return_legacy_design(requirement, owner_id=owner_id, design=design_result["design"])
        understanding = design_result.get("understanding") if isinstance(design_result.get("understanding"), Mapping) else {}
        existing_analysis = design_result.get("existing_analysis") if isinstance(design_result.get("existing_analysis"), Mapping) else {}
        questions = design_result.get("questions") if isinstance(design_result.get("questions"), list) else []
        design = design_result.get("design") if isinstance(design_result.get("design"), Mapping) else {}
        design_ready = bool(design)
        design_artifact = self._design_artifact_identity(design, state=prior_state) if design_ready else {}
        canonical_requirement = _trim(prior_state.get("requirement_text")) if prior_state else requirement
        if not canonical_requirement:
            canonical_requirement = requirement
        design_context = {
            "round": design_round,
            "is_first_round": not bool(prior_state),
        }
        if not design_ready:
            narration = compose_design_narrative(understanding, questions, design)
            next_state = {
                "requirement_text": canonical_requirement,
                "latest_feedback_text": requirement,
                "feedback_ledger": feedback_ledger,
                "owner_id": owner_id,
                "design_round": design_round,
                "understanding": dict(understanding),
                "questions": questions,
                "existing_analysis": dict(existing_analysis),
                **design_artifact,
            }
            return {
                "message": design_result.get("message") or narration,
                "design_status": "clarification",
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
            "requirement_text": canonical_requirement,
            "latest_feedback_text": requirement,
            "feedback_ledger": feedback_ledger,
            "owner_id": owner_id,
            "design_round": design_round,
            "design_contract": design,
            "understanding": dict(understanding),
            "existing_analysis": dict(existing_analysis),
            "questions": questions,
            **design_artifact,
        }
        tool_name = _trim(design.get("tool_name")) or "custom_tool"
        display_name = _trim(design.get("display_name")) or tool_name
        message = design_result.get("message") or compose_design_narrative(understanding, questions, design)
        return {
            "message": message,
            "design_status": "review",
            "understanding": dict(understanding),
            "state": next_state,
            "design": design,
            "design_artifact": design_artifact,
            "design_context": design_context,
            "existing_analysis": dict(existing_analysis),
            "events": design_result.get("events") or [],
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def handle_turn(
        self,
        text: str,
        *,
        state: Mapping[str, Any],
        ui_action: Optional[Mapping[str, Any]] = None,
        owner_id: str = "",
        turn_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Plan and execute one natural-language turn in the financial-tool domain."""
        raw = _trim(text)
        current_state = self._clean_state_for_context(state)
        if not raw:
            return {"message": "请说明本轮希望查看或调整的内容。", "state": current_state}
        if not self.finance_cc_enabled:
            return {
                "message": "Finance CC 当前不可用，已有需求、设计和实现均未改变。",
                "state": current_state,
                "error": "Finance CC controller is unavailable",
                "thread_context_patch": {"custom_tool_state": current_state},
            }
        return self._handle_finance_cc_turn(
            raw,
            state=current_state,
            ui_action=ui_action,
            owner_id=owner_id,
            thread_id=thread_id,
            turn_id=turn_id,
            event_sink=event_sink,
        )

    def _handle_finance_cc_turn(
        self,
        text: str,
        *,
        state: Mapping[str, Any],
        ui_action: Optional[Mapping[str, Any]],
        owner_id: str,
        thread_id: Optional[int],
        turn_id: Optional[int],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        """Adapt one Finance CC result into the existing conversation contract."""
        current_state = dict(state or {})
        context = {
            "selected_agent": "investment_analyst",
            "turn_mode": "tool_development",
            "entry": "custom_tool_flow",
            "has_custom_tool_state": bool(current_state),
            "custom_tool_state": current_state,
            "custom_tool_name": _trim(current_state.get("tool_name")),
            "ui_action": dict(ui_action or {}),
        }
        cc_result = self.finance_cc_service.run_turn(
            thread_id=thread_id or 0,
            turn_id=turn_id or "",
            owner_id=owner_id,
            user_text=text,
            context=context,
            event_sink=event_sink,
        )
        next_state = dict(current_state)
        feedback_ledger = self.design_protocol.append_feedback(
            next_state.get("feedback_ledger"),
            text=text,
            design_round=max(1, int(next_state.get("design_round") or 0) + 1),
            turn_id=turn_id,
            kind="initial_requirement" if not next_state else "feedback",
        )
        next_state["feedback_ledger"] = feedback_ledger
        next_state["owner_id"] = owner_id or _trim(next_state.get("owner_id"))
        next_state.setdefault("requirement_text", text)

        design_status = ""
        notice: List[str] = []
        questions: List[Dict[str, Any]] = []
        test_evidence: Dict[str, Any] = {}
        for update in cc_result.get("artifact_updates") or []:
            if not isinstance(update, Mapping):
                continue
            artifact_type = _trim(update.get("artifact_type"))
            payload = update.get("payload") if isinstance(update.get("payload"), Mapping) else {}
            if artifact_type == "requirement":
                brief = _trim(payload.get("requirement_brief"))
                if brief:
                    next_state["requirement_brief"] = brief
                    next_state.pop("understanding", None)
                notice = [_trim(item) for item in payload.get("notice") or [] if _trim(item)]
                questions = [dict(item) for item in payload.get("questions") or [] if isinstance(item, Mapping)]
                if questions:
                    next_state["notice"] = notice
                    next_state["questions"] = questions
                else:
                    # The final requirement brief is the only design input.
                    # Interaction details remain in the persisted turn, not the next stage state.
                    next_state.pop("notice", None)
                    next_state.pop("questions", None)
                design_status = "clarification"
            elif artifact_type == "design":
                design_value = payload.get("design")
                design = (
                    {"document": _trim(design_value)}
                    if isinstance(design_value, str) and _trim(design_value)
                    else dict(design_value)
                    if isinstance(design_value, Mapping)
                    else {}
                )
                if design:
                    next_state["design_contract"] = dict(design)
                    next_state["tool_name"] = _trim(design.get("tool_name"))
                    next_state.update(self._design_artifact_identity(design, state=next_state))
                    design_status = "review"
            elif artifact_type == "flow":
                mermaid = _trim(payload.get("mermaid"))
                design = dict(next_state.get("design_contract") or {})
                if mermaid and design:
                    design["mermaid"] = mermaid
                    next_state["design_contract"] = design
                    design_status = "review"
            elif artifact_type == "test_evidence":
                test_evidence = dict(payload)
                design_status = "test"

        implementation_runs = [
            dict(item) for item in cc_result.get("implementation_runs") or [] if isinstance(item, Mapping)
        ]
        latest_implementation = implementation_runs[-1] if implementation_runs else {}
        if isinstance(latest_implementation.get("state"), Mapping):
            next_state.update(dict(latest_implementation.get("state") or {}))

        interaction_requests = [
            dict(item) for item in cc_result.get("interaction_requests") or [] if isinstance(item, Mapping)
        ]
        if interaction_requests and not questions:
            questions = [
                dict(item)
                for request in interaction_requests
                for item in request.get("questions") or []
                if isinstance(item, Mapping)
            ]
            next_state["questions"] = questions

        design = dict(next_state.get("design_contract") or {})
        requirement_brief = _trim(next_state.get("requirement_brief"))
        legacy_understanding = (
            dict(next_state.get("understanding") or {})
            if isinstance(next_state.get("understanding"), Mapping)
            else {}
        )
        understanding = (
            {"requirement_brief": requirement_brief}
            if requirement_brief
            else legacy_understanding
        )
        response = {
            "message": _trim(cc_result.get("result")) or "本轮处理已完成。",
            "state": next_state,
            "thread_context_patch": {"custom_tool_state": next_state},
            "design_status": design_status or ("review" if design else "clarification"),
            "understanding": understanding,
            "notice": notice,
            "questions": questions,
            "design": design,
            "finance_cc": cc_result,
        }
        view_assets = [
            {"type": _trim(item.get("asset_type")), "payload": item.get("payload")}
            for item in cc_result.get("asset_reads") or []
            if isinstance(item, Mapping)
            and _trim(item.get("asset_type")) in {"design", "flow", "code", "tests", "tool_contract"}
            and item.get("payload") is not None
        ]
        if view_assets:
            response["view_assets"] = view_assets
        if test_evidence:
            response["test_evidence"] = test_evidence
        if latest_implementation:
            response["coding_status"] = _trim(latest_implementation.get("coding_status")) or (
                "complete" if latest_implementation.get("ok") else "coding_failed"
            )
            response["coding_error"] = dict(latest_implementation.get("coding_error") or {})
            response["test_result"] = dict(latest_implementation.get("test_result") or {})
            response["tool"] = dict(latest_implementation.get("tool") or {})
            response["implementation_meta"] = dict(latest_implementation.get("implementation_meta") or {})
            response["implementation_explanation"] = dict(
                latest_implementation.get("implementation_explanation") or {}
            )
            response["implementation_review"] = dict(
                latest_implementation.get("implementation_review") or {}
            )
            response["coding_tests"] = [
                dict(item)
                for item in latest_implementation.get("coding_tests") or []
                if isinstance(item, Mapping)
            ]
        if not cc_result.get("ok"):
            error = _trim(cc_result.get("error")) or "Finance CC execution failed"
            saved_types = [
                _trim(item.get("artifact_type"))
                for item in cc_result.get("artifact_updates") or []
                if isinstance(item, Mapping) and _trim(item.get("artifact_type"))
            ]
            response["error"] = error
            if saved_types:
                response["message"] = (
                    f"本轮已保存 {', '.join(dict.fromkeys(saved_types))}，但后续处理未完成；可以从当前结果继续。"
                )
            elif latest_implementation:
                response["message"] = "本轮实现结果已经保存，但会话收尾未完成；可以从当前实现继续。"
            else:
                response["message"] = "本轮处理失败，已有业务资产未改变；本轮反馈已经记录，可以继续重试。"
        return response

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
        if normalized_action == "custom_tool.confirm_design":
            current_revision = int(state.get("design_revision") or 0)
            if expected_revision is not None and current_revision > 0 and int(expected_revision) != current_revision:
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
        if normalized_action == "custom_tool.activate_draft":
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
        if normalized_action == "custom_tool.retry_coding":
            retry_state = self._clean_state_for_context(state)
            return self._confirm_and_code(state=retry_state, owner_id=owner_id, event_sink=event_sink)
        raise CustomToolError(f"unknown custom tool action: {normalized_action or '-'}")

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
        selected_skills: Optional[List[str]],
        turn_id: Optional[int],
        event_sink: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "_workspace_identity": {
                "owner_id": owner_id,
                "turn_id": int(turn_id) if turn_id is not None else 0,
            },
        }
        if selected_skills:
            context["selected_skills"] = list(dict.fromkeys(_trim(item) for item in selected_skills if _trim(item)))
        prior = dict(state or {})
        requirement_brief = _trim(prior.get("requirement_brief"))
        if requirement_brief:
            context["requirement_brief"] = requirement_brief
        elif isinstance(prior.get("understanding"), Mapping) and prior.get("understanding"):
            context["requirement_brief"] = dict(prior.get("understanding") or {})
        current_design = (
            prior.get("design_contract")
            if isinstance(prior.get("design_contract"), Mapping)
            else prior.get("partial_design")
            if isinstance(prior.get("partial_design"), Mapping)
            else {}
        )
        if current_design:
            context["current_design"] = dict(current_design)
        try:
            return self.designer.design(requirement_text, context=context, event_sink=event_sink)
        except TypeError:
            return self.designer.design(requirement_text)

    def _confirm_and_code(
        self,
        *,
        state: Mapping[str, Any],
        owner_id: str,
        selected_skills: Optional[List[str]] = None,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        actual_owner = owner_id or _trim(state.get("owner_id"))
        legacy_design = state.get("design") if isinstance(state.get("design"), Mapping) else {}
        if legacy_design and legacy_design.get("code"):
            return self._save_test_and_return(legacy_design, owner_id=actual_owner)
        design_contract = state.get("design_contract") if isinstance(state.get("design_contract"), Mapping) else {}
        if not design_contract:
            next_state = self._clean_state_for_context(state)
            return {
                "message": "当前设计稿为空，请补充需求后重新生成设计。",
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        if self.coder is None:
            next_state = self._clean_state_for_context(state)
            return {
                "message": "当前未启用 Agent coding，请先配置 coding runner 或补充可执行代码。",
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        try:
            coding_context: Dict[str, Any] = {}
            agent_runtime = (
                dict(state.get("agent_runtime") or {})
                if isinstance(state.get("agent_runtime"), Mapping)
                else {}
            )
            agent_runtime.setdefault("session_id", uuid.uuid4().hex)
            coding_context["_agent_runtime"] = agent_runtime
            coding_feedback = _trim(state.get("coding_feedback"))
            if coding_feedback:
                coding_context["coding_feedback"] = coding_feedback
            test_feedback = state.get("test_feedback")
            if isinstance(test_feedback, Mapping) and test_feedback:
                coding_context["test_feedback"] = dict(test_feedback)
            if selected_skills:
                coding_context["selected_skills"] = list(dict.fromkeys(
                    _trim(item) for item in selected_skills if _trim(item)
                ))
            current_tool_name = _trim(state.get("tool_name"))
            if current_tool_name and self.store.exists(current_tool_name):
                current_bundle = self.store.load(current_tool_name)
                coding_context["current_implementation"] = {
                    "revision": int((current_bundle.get("manifest") or {}).get("current_revision") or 0),
                    "modules": [dict(item) for item in current_bundle.get("modules") or [] if isinstance(item, Mapping)],
                    "last_test": dict((current_bundle.get("manifest") or {}).get("last_test") or {}),
                }
            coding_context["_workspace_identity"] = {
                "owner_id": actual_owner,
                "tool_name": current_tool_name or _trim(design_contract.get("tool_name")),
            }
            coding_result = self.coder.code(
                design_contract,
                requirement_text=(
                    _trim(state.get("requirement_brief"))
                    or _trim(state.get("requirement_text"))
                ),
                context=coding_context,
                event_sink=event_sink,
            )
        except TypeError:
            coding_result = self.coder.code(
                design_contract,
                requirement_text=(
                    _trim(state.get("requirement_brief"))
                    or _trim(state.get("requirement_text"))
                ),
                context=coding_context,
            )
        coding_raw = coding_result.get("raw") if isinstance(coding_result.get("raw"), Mapping) else {}
        agent_runtime = (
            dict(coding_result.get("agent_runtime") or {})
            if isinstance(coding_result.get("agent_runtime"), Mapping)
            else dict(coding_context.get("_agent_runtime") or {})
        )
        context_bundle = coding_raw.get("context_bundle") if isinstance(coding_raw.get("context_bundle"), Mapping) else {}
        implementation_meta = {
            "provider": self.coding_provider,
            "complexity": self.coding_complexity,
            "model": _trim(getattr(getattr(self.coder, "harness", None), "model", "")),
            "reasoning_effort": _trim(
                getattr(getattr(self.coder, "harness", None), "reasoning_effort", "")
            ),
            "session_id": _trim(agent_runtime.get("session_id")),
            "provider_session_id": _trim(agent_runtime.get("provider_session_id")),
            "duration_ms": int(coding_raw.get("duration_ms") or 0),
            "context_bundle": {
                key: context_bundle.get(key)
                for key in (
                    "bundle_id", "owner_scope", "bundle_dir", "api_index", "api_task_context",
                    "api_sources", "runtime_contract", "custom_tool_sdk", "coding_guide",
                    "module_template", "coding_workspace",
                )
                if context_bundle.get(key)
            },
        }
        if not coding_result.get("ok"):
            coding_error = coding_result.get("error") if isinstance(coding_result.get("error"), Mapping) else {}
            next_state = {
                **self._clean_state_for_context(state),
                "agent_runtime": agent_runtime,
                "coding_feedback": coding_result.get("message"),
                "coding_error": dict(coding_error),
            }
            return {
                "message": coding_result.get("message") or "本次代码实现没有产生可执行模块。",
                "coding_status": "coding_failed",
                "coding_error": dict(coding_error),
                "events": coding_result.get("events") or [],
                "implementation_meta": implementation_meta,
                "state": next_state,
                "thread_context_patch": {"custom_tool_state": next_state},
            }
        coding_final = coding_result.get("final") if isinstance(coding_result.get("final"), Mapping) else {}
        bundle_design = self._bundle_from_coding_final(
            design_contract,
            coding_final,
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
        result = self._save_test_and_return(
            bundle_design,
            owner_id=actual_owner,
            events=coding_result.get("events") or [],
        )
        result["implementation_meta"] = implementation_meta
        result["coding_status"] = "implemented"
        result["implementation_review"] = self._implementation_review(coding_final)
        result["implementation_explanation"] = self._implementation_explanation(coding_final)
        result["coding_tests"] = [
            {
                key: item.get(key)
                for key in (
                    "test_id", "purpose", "input_json", "actual_output_json",
                    "result", "checks", "evidence", "error",
                )
            }
            for item in coding_final.get("tests") or []
            if isinstance(item, Mapping)
        ]
        # Preserve system-owned assets and feedback; no workflow status gates the next turn.
        next_state = {
            **self._clean_state_for_context(state),
            **dict(result.get("state") or {}),
            "agent_runtime": agent_runtime,
        }
        result["state"] = next_state
        result["thread_context_patch"] = {"custom_tool_state": next_state}
        return result

    def implement_dynamic_tool(
        self,
        *,
        state: Mapping[str, Any],
        owner_id: str,
        instruction: str = "",
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Let Codex implement, validate, and review one dynamic-tool revision."""
        coding_state = self._clean_state_for_context(state)
        if _trim(instruction):
            coding_state["coding_feedback"] = _trim(instruction)
        return self._confirm_and_code(
            state=coding_state,
            owner_id=owner_id,
            event_sink=event_sink,
        )

    def _run_existing_test(
        self,
        *,
        request: str,
        state: Mapping[str, Any],
        owner_id: str,
        event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        tool_name = _trim(state.get("tool_name"))
        if not tool_name or not self.store.exists(tool_name):
            return {
                "message": "当前还没有可运行的工具实现。",
                "state": dict(state),
                "thread_context_patch": {"custom_tool_state": dict(state)},
            }
        from src.services.asset_invocation_service import AssetInvocationError, AssetInvocationService

        bundle = self.store.load(tool_name)
        if self.tester is None:
            return {
                "message": "当前未启用测试 Skill，工具实现未改变。",
                "test_status": "failed",
                "state": dict(state),
                "thread_context_patch": {"custom_tool_state": dict(state)},
            }
        tool_contract = {
            "manifest": dict(bundle.get("manifest") or {}),
            "input_schema": dict(bundle.get("input_schema") or {}),
            "output_schema": dict(bundle.get("output_schema") or {}),
            "sample_input": dict(bundle.get("sample_input") or {}),
        }
        max_test_turns = max(1, int(os.environ.get("CUSTOM_TOOL_TEST_MAX_TURNS") or DEFAULT_MAX_TEST_TURNS))
        invocation_service = AssetInvocationService(custom_tool_store=self.store)
        cases: List[Dict[str, Any]] = []
        invocations: List[Dict[str, Any]] = []
        test_history: List[Dict[str, Any]] = []
        all_events: List[Dict[str, Any]] = []
        presentation: Dict[str, Any] = {}
        assessment = ""
        plan_message = ""
        planning_error = ""
        planning_feedback = ""
        finished_by_skill = False
        max_turns_reached = False
        planner_turns = 0

        for test_turn in range(1, max_test_turns + 1):
            planner_turns = test_turn
            test_plan = self.tester.plan(
                request,
                context={
                    "tool_contract": tool_contract,
                    "test_history": test_history,
                    "test_turn": test_turn,
                    "max_test_turns": max_test_turns,
                    **({"planning_feedback": planning_feedback} if planning_feedback else {}),
                    "_workspace_identity": {"owner_id": owner_id, "tool_name": tool_name},
                },
                event_sink=event_sink,
            )
            all_events.extend(test_plan.get("events") or [])
            if not test_plan.get("ok"):
                planning_error = _trim(test_plan.get("error") or test_plan.get("message")) or "测试样例规划失败"
                planning_feedback = f"上一轮测试规划未产生可执行结果：{planning_error}。请根据现有真实记录重新规划。"
                if test_turn < max_test_turns:
                    continue
                max_turns_reached = True
                break

            planning_error = ""
            planning_feedback = ""
            plan_message = _trim(test_plan.get("message")) or plan_message
            assessment = _trim(test_plan.get("assessment")) or assessment
            if isinstance(test_plan.get("presentation"), Mapping):
                presentation = dict(test_plan.get("presentation") or {})
            planned_cases = [dict(item) for item in test_plan.get("cases") or [] if isinstance(item, Mapping)]
            next_action = _trim(test_plan.get("next_action"))
            if next_action == "finish":
                finished_by_skill = True
                break
            if not planned_cases:
                planning_error = "测试 Skill 要求继续，但没有给出下一批测试样例"
                planning_feedback = planning_error + "。请给出至少一个可执行 case，或在证据充分时结束。"
                if test_turn < max_test_turns:
                    continue
                max_turns_reached = True
                break

            turn_cases: List[Dict[str, Any]] = []
            for planned_index, planned_case in enumerate(planned_cases):
                case_request = _trim(planned_case.get("request")) or request
                test_id_prefix = f"interactive_{test_turn}_{planned_index + 1}"
                try:
                    invocation = invocation_service.plan(
                        text=case_request,
                        selected_asset={"kind": "tool", "name": tool_name},
                        owner_ids=[owner_id] if owner_id else None,
                        allow_inactive=True,
                    )
                except AssetInvocationError as exc:
                    turn_cases.append({
                        "test_id": test_id_prefix,
                        "category": "interactive_run",
                        "status": "failed",
                        "input": {},
                        "actual": {},
                        "logs": [],
                        "purpose": _trim(planned_case.get("purpose")) or case_request,
                        "error": str(exc),
                    })
                    continue
                invocations.append(invocation)
                if invocation.get("status") != "ready":
                    turn_cases.append({
                        "test_id": test_id_prefix,
                        "category": "interactive_run",
                        "status": "failed",
                        "input": {},
                        "actual": {},
                        "logs": [],
                        "purpose": _trim(planned_case.get("purpose")) or case_request,
                        "error": _trim(invocation.get("message")) or "测试输入不完整",
                    })
                    continue
                for call_index, arguments in enumerate(invocation.get("calls") or []):
                    if not isinstance(arguments, Mapping):
                        continue
                    run_result = self.runtime.run(
                        tool_name,
                        dict(arguments),
                        owner_ids=[owner_id] if owner_id else None,
                        allow_inactive=True,
                    )
                    logs = [
                        dict(item)
                        for item in ((run_result.get("meta") or {}).get("execution_logs") or [])
                        if isinstance(item, Mapping)
                    ]
                    turn_cases.append({
                        "test_id": f"{test_id_prefix}_{call_index + 1}",
                        "category": "interactive_run",
                        "status": "passed" if run_result.get("ok") else "failed",
                        "input": dict(arguments),
                        "actual": dict(run_result.get("data") or {}),
                        "logs": logs,
                        "purpose": _trim(planned_case.get("purpose")) or case_request,
                        "error": _trim(run_result.get("error")),
                    })
            cases.extend(turn_cases)
            test_history.append({
                "turn": test_turn,
                "planned_cases": planned_cases,
                "executions": turn_cases,
            })
            if test_turn == max_test_turns:
                max_turns_reached = True

        execution_ok = bool(cases) and all(item["status"] == "passed" for item in cases)
        evidence_status = "sufficient" if finished_by_skill and bool(cases) else "inconclusive"
        technical_summary = f"{sum(item['status'] == 'passed' for item in cases)} / {len(cases)} 项技术运行成功"
        summary_parts = [technical_summary]
        if assessment:
            summary_parts.append(assessment)
        if max_turns_reached:
            summary_parts.append(f"已达到最多 {max_test_turns} 轮测试，基于现有证据结束")
        if planning_error:
            summary_parts.append(planning_error)
        test_result = {
            "ok": execution_ok,
            "execution_ok": execution_ok,
            "contract_ok": execution_ok,
            "evidence_status": evidence_status,
            "assessment": assessment,
            "test_turns": planner_turns,
            "max_test_turns": max_test_turns,
            "max_turns_reached": max_turns_reached,
            "error": planning_error or ("" if execution_ok else next((item["error"] for item in cases if item["error"]), "工具运行失败")),
            "cases": cases,
            "summary": "；".join(summary_parts) + "；业务结果请用户确认",
        }
        saved = self.store.record_test(tool_name, test_result)
        next_state = {
            **self._clean_state_for_context(state),
            "tool_name": tool_name,
            "owner_id": owner_id or _trim(state.get("owner_id")),
        }
        return {
            "message": "\n".join(item for item in [plan_message, test_result["summary"]] if item),
            "test_status": "passed" if execution_ok else "failed",
            "test_result": test_result,
            "invocations": invocations,
            "presentation": dict(presentation),
            "events": all_events,
            "tool": saved,
            "state": next_state,
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _return_legacy_design(self, requirement_text: str, *, owner_id: str, design: Mapping[str, Any]) -> Dict[str, Any]:
        next_state = {
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
        test_result.update({
            "execution_ok": execution_ok,
            "contract_ok": contract_ok,
        })
        execution_logs = [
            dict(item)
            for item in ((test_result.get("meta") or {}).get("execution_logs") or [])
            if isinstance(item, Mapping)
        ]
        actual_output = test_result.get("data") if isinstance(test_result.get("data"), Mapping) else {}
        if not actual_output:
            diagnostics = (test_result.get("meta") or {}).get("diagnostics") or {}
            if isinstance(diagnostics.get("actual_output"), Mapping):
                actual_output = dict(diagnostics["actual_output"])
        test_result["cases"] = [{
            "test_id": "sample_smoke",
            "category": "happy_path",
            "status": "passed" if execution_ok else "failed",
            "input": sample_input,
            "expected": expected or {"business_result": "no top-level error and no ok=false"},
            "actual": dict(actual_output),
            "logs": execution_logs,
            "purpose": "验证动态加载、沙箱执行、输出 Schema 和样例业务预期。",
            "error": _trim(test_result.get("error")),
        }]
        test_result["proposed_cases"] = proposed_tests
        test_result["summary"] = "1 / 1 项技术运行成功" if execution_ok else "0 / 1 项技术运行成功"
        saved = self.store.record_test(manifest["tool_name"], test_result)
        next_state = {
            "tool_name": manifest["tool_name"],
            "owner_id": owner_id,
            "implementation_revision": int(manifest.get("current_revision") or 0),
            "requirement_text": _trim(design.get("requirement_text")),
            "design_contract": dict(design.get("design_contract") or {}),
        }
        if not execution_ok:
            next_state["test_feedback"] = self._test_feedback(
                test_result,
                sample_input=sample_input,
                expected=expected,
                execution_logs=execution_logs,
            )
        return {
            "message": (
                f"已生成 draft：{manifest['tool_name']}。\n"
                f"样例技术运行：{'成功' if execution_ok else '失败'}"
                + (f"\n错误：{test_result.get('error') or '运行失败'}" if not execution_ok else "")
                + (
                    "\n以下实际结果和核心日志供你确认业务逻辑；确认后可启用。"
                    if execution_ok
                    else "\n实现和失败现场均已保存，可根据真实错误继续修改。"
                )
            ),
            "state": next_state,
            "tool": saved,
            "test_result": test_result,
            "events": events or [],
            "thread_context_patch": {"custom_tool_state": next_state},
        }

    def _bundle_from_coding_final(self, design: Mapping[str, Any], final: Mapping[str, Any]) -> Dict[str, Any]:
        tool_contract = (
            dict(final.get("tool_contract") or {})
            if isinstance(final.get("tool_contract"), Mapping)
            else {}
        )
        tool_name = self.store.normalize_tool_name(
            tool_contract.get("tool_name") or design.get("tool_name")
        )
        display_name = (
            _trim(tool_contract.get("display_name"))
            or _trim(design.get("display_name"))
            or tool_name
        )
        description = _trim(tool_contract.get("description")) or _trim(design.get("description"))
        input_fields = (
            tool_contract.get("inputs")
            if isinstance(tool_contract.get("inputs"), list)
            else design.get("inputs")
            if isinstance(design.get("inputs"), list)
            else []
        )
        output_fields = (
            tool_contract.get("outputs")
            if isinstance(tool_contract.get("outputs"), list)
            else design.get("outputs")
            if isinstance(design.get("outputs"), list)
            else []
        )
        output_fields = self._with_key_process_info_output(output_fields)
        code = self._select_code(final)
        if not code:
            raise CustomToolError("coding final does not include python code")
        legacy_implementation = (
            final.get("implementation")
            if isinstance(final.get("implementation"), Mapping)
            else {}
        )
        return {
            "manifest": {
                "tool_name": tool_name,
                "display_name": display_name,
                "description": description,
                "visibility": "personal",
                "capabilities": ["custom_tool"],
                "implementation_logic": (
                    self._logic_text(design)
                    or _trim(final.get("implementation_summary"))
                    or _trim(legacy_implementation.get("summary"))
                ),
                "runtime": {"kind": "python_sandbox", "backend": "local_dev", "timeout_ms": 30000},
            },
            "input_schema": self._schema_from_fields(input_fields),
            "output_schema": self._schema_from_fields(output_fields),
            "code": code,
            "sample_input": self._sample_input(final),
            "modules": [dict(item) for item in legacy_implementation.get("modules") or [] if isinstance(item, Mapping)],
            "proposed_tests": [dict(item) for item in final.get("tests") or [] if isinstance(item, Mapping)],
            "implementation_explanation": self._implementation_explanation(final),
            "implementation_review": self._implementation_review(final),
            "design_contract": dict(design),
        }

    @staticmethod
    def _with_key_process_info_output(fields: List[Any]) -> List[Dict[str, Any]]:
        """Apply the one platform-wide explainability field without judging its business contents."""
        normalized = [dict(item) for item in fields if isinstance(item, Mapping)]
        for index, item in enumerate(normalized):
            if _trim(item.get("name")) != "key_process_info":
                continue
            normalized[index] = {
                **item,
                "name": "key_process_info",
                "type": "object",
                "required": True,
                "description": (
                    _trim(item.get("description"))
                    or "解释本次结果所需的核心中间指标、样本和判断条件。"
                ),
            }
            return normalized
        normalized.append({
            "name": "key_process_info",
            "type": "object",
            "required": True,
            "description": "解释本次结果所需的核心中间指标、样本和判断条件。",
        })
        return normalized

    @staticmethod
    def _implementation_explanation(final: Mapping[str, Any]) -> Dict[str, Any]:
        legacy = final.get("implementation_explanation")
        if isinstance(legacy, Mapping):
            return dict(legacy)
        summary = _trim(final.get("implementation_summary"))
        return {"summary": summary} if summary else {}

    @staticmethod
    def _implementation_review(final: Mapping[str, Any]) -> Dict[str, Any]:
        legacy = final.get("implementation_review")
        if isinstance(legacy, Mapping):
            return dict(legacy)
        legacy = final.get("technical_summary")
        if isinstance(legacy, Mapping):
            return dict(legacy)
        summary = _trim(final.get("verification"))
        return {"summary": summary} if summary else {}

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

    @staticmethod
    def _logic_text(design: Mapping[str, Any]) -> str:
        document = _trim(design.get("document"))
        if document:
            return document
        plan = _trim(design.get("plan"))
        if plan:
            return plan
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
            field_schema: Dict[str, Any] = {"type": json_type, "description": _trim(item.get("description"))}
            label = _trim(item.get("label"))
            if label:
                field_schema["title"] = label
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
            if item.get("required") is True:
                required.append(name)
        schema: Dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": True}
        if required:
            schema["required"] = required
        return schema

    @staticmethod
    def _test_feedback(
        test_result: Mapping[str, Any],
        *,
        sample_input: Mapping[str, Any],
        expected: Mapping[str, Any],
        execution_logs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Keep the concrete failed-run evidence for the next Coding turn."""
        actual = test_result.get("data") if isinstance(test_result.get("data"), Mapping) else {}
        if not actual:
            diagnostics = (test_result.get("meta") or {}).get("diagnostics") or {}
            if isinstance(diagnostics.get("actual_output"), Mapping):
                actual = dict(diagnostics["actual_output"])
        return {
            "summary": _trim(test_result.get("summary")) or "样例测试未通过",
            "execution_ok": bool(test_result.get("execution_ok")),
            "contract_ok": bool(test_result.get("contract_ok")),
            "error": _trim(test_result.get("error")),
            "input": dict(sample_input),
            "expected": dict(expected),
            "actual": dict(actual),
            "logs": [dict(item) for item in execution_logs],
        }

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
