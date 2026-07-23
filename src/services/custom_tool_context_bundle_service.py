from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import sys
import uuid
from typing import Any, Dict, Mapping


def _trim(value: Any) -> str:
    return str(value or "").strip()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


class CustomToolContextBundleService:
    """Build compact, file-based context for Codex custom-tool runs."""

    def __init__(
        self,
        *,
        catalog_path: str = "src/tools/finance_data/catalog/api_view_catalog.json",
        stock_universe_path: str = "stock_name.tsv",
        root_dir: str = "data/custom_tool_context",
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.stock_universe_path = Path(stock_universe_path)
        self.root_dir = Path(root_dir)

    def build(
        self,
        *,
        stage: str,
        user_request: str,
        context: Mapping[str, Any] | None = None,
        run_id: str = "",
    ) -> Dict[str, Any]:
        stage_name = _trim(stage).lower()
        coding_stage = stage_name == "coding"
        data_catalog_stage = stage_name in {"coding", "test"}
        raw_context = dict(context or {})
        identity = raw_context.pop("_workspace_identity", None)
        identity = identity if isinstance(identity, Mapping) else {}
        owner_scope = self._owner_scope(identity.get("owner_id"))
        run_scope = self._safe_id(run_id)
        random_scope = uuid.uuid4().hex[:16]
        # A Coding session owns one isolated workspace.  Later repair turns
        # resume the same Codex history and edit the same files instead of
        # rebuilding and re-sending the implementation context.
        bundle_id = f"coding_{run_scope[:24]}" if coding_stage and run_scope else (
            f"{run_scope[:15]}_{random_scope}" if run_scope else random_scope
        )
        bundle_dir = self.root_dir / owner_scope / bundle_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        self._restrict_directory(bundle_dir)
        self._restrict_directory(bundle_dir.parent)

        prompt_context, coding_workspace = self._materialize_context(
            bundle_dir=bundle_dir,
            stage=stage_name,
            context=raw_context,
        )
        coding_module_records = list(coding_workspace.pop("_module_records", []))
        self._write_private(
            bundle_dir / "task.json",
            {
                "stage": stage_name,
                "run_id": _trim(run_id),
                "user_request": user_request,
                "context": prompt_context,
            },
        )
        public_bundle = {
            "bundle_id": bundle_id,
            "run_id": _trim(run_id),
            "owner_scope": owner_scope,
            "bundle_dir": str(bundle_dir.resolve()),
            "task": str((bundle_dir / "task.json").relative_to(bundle_dir)),
            "coding_workspace": coding_workspace,
        }
        if data_catalog_stage:
            api_dir = bundle_dir / "api_catalog"
            subject_dir = api_dir / "subjects"
            subject_dir.mkdir(parents=True, exist_ok=True)
            catalog = self._load_catalog()
            subjects = catalog.get("subjects") if isinstance(catalog.get("subjects"), dict) else {}
            patterns = catalog.get("api_class_patterns") if isinstance(catalog.get("api_class_patterns"), dict) else {}
            index_subjects = []
            for subject, subject_obj in sorted(subjects.items()):
                if not isinstance(subject_obj, dict):
                    continue
                subject_file = subject_dir / f"{subject}.json"
                self._write_private(subject_file, self._compact_subject(subject, subject_obj))
                index_subjects.append({
                    "subject": subject,
                    "description": _trim((subject_obj.get("_meta") or {}).get("desc")),
                    "rules": (subject_obj.get("_meta") or {}).get("rules") or [],
                    "dataviews": [key for key in subject_obj.keys() if not str(key).startswith("_")],
                    "file": str(subject_file.relative_to(bundle_dir)),
                })
            self._write_private(
                api_dir / "index.json",
                {
                    "version": catalog.get("version"),
                    "source_catalog": str(self.catalog_path),
                    "usage": [
                        "先读取本 index，按任务判断需要哪些 subject/dataview。",
                        "只读取相关 subject 文件，不要全量读取所有 subject。",
                        (
                            "生成自定义工具代码时，优先调用 custom_tool_sdk.finance_query(request=...)，不要直接访问数据库或底层 provider。"
                            if coding_stage
                            else "测试规划可据此了解系统能够提供的股票、指数、行业、板块、基金、债券等真实输入范围。"
                        ),
                    ],
                    "subjects": index_subjects,
                },
            )
            self._write_private(api_dir / "request_patterns.json", patterns)
            public_bundle.update({
                "api_index": str((api_dir / "index.json").relative_to(bundle_dir)),
                "request_patterns": str((api_dir / "request_patterns.json").relative_to(bundle_dir)),
            })
            if coding_stage:
                task_api_context = self._task_api_context(raw_context, catalog)
                if task_api_context.get("sources") or task_api_context.get("data_needs"):
                    task_api_path = api_dir / "task_context.json"
                    self._write_private(task_api_path, task_api_context)
                    public_bundle["api_task_context"] = str(task_api_path.relative_to(bundle_dir))
                    public_bundle["api_sources"] = [
                        _trim(item.get("source_ref"))
                        for item in task_api_context.get("sources") or []
                        if isinstance(item, Mapping) and _trim(item.get("source_ref"))
                    ]
                self._write_private(bundle_dir / "runtime_contract.md", self._runtime_contract())
                self._write_private(bundle_dir / "custom_tool_sdk.md", self._sdk_doc())
                self._materialize_coding_support(bundle_dir)
                public_bundle.update({
                    "runtime_contract": "runtime_contract.md",
                    "custom_tool_sdk": "custom_tool_sdk.md",
                    "coding_guide": "CODING_WORKSPACE.md",
                })
            elif self.stock_universe_path.is_file():
                stock_universe = bundle_dir / "test_data" / "stock_universe.tsv"
                self._write_private(stock_universe, self.stock_universe_path.read_text(encoding="utf-8"))
                public_bundle["stock_universe"] = str(stock_universe.relative_to(bundle_dir))
        return {
            **public_bundle,
            "_prompt_context": prompt_context,
            "_coding_module_records": coding_module_records,
        }

    def collect_coding_result(self, bundle: Mapping[str, Any], final: Mapping[str, Any]) -> Dict[str, Any]:
        """Use edited workspace modules as the source of truth for this Coding result."""
        result = dict(final or {})
        bundle_dir_text = _trim(bundle.get("bundle_dir"))
        rows = bundle.get("_coding_module_records") if isinstance(bundle.get("_coding_module_records"), list) else []
        if not bundle_dir_text or not rows:
            return result
        bundle_dir = Path(bundle_dir_text).resolve()
        workspace_modules: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            module_id = _trim(row.get("module_id"))
            module_path = _trim(row.get("path"))
            if not module_id or not module_path:
                continue
            source_file = (bundle_dir / module_path).resolve()
            if not source_file.is_relative_to(bundle_dir):
                continue
            try:
                source = source_file.read_text(encoding="utf-8")
            except OSError:
                continue
            workspace_modules[module_id] = {
                **{str(key): value for key, value in row.items() if key not in {"path", "original_sha256"}},
                "source_code": source,
            }
        implementation = result.get("implementation") if isinstance(result.get("implementation"), Mapping) else {}
        modules = [dict(item) for item in implementation.get("modules") or [] if isinstance(item, Mapping)]
        returned = {_trim(item.get("module_id")): item for item in modules if _trim(item.get("module_id"))}
        merged_modules: list[Dict[str, Any]] = []
        for module_id, workspace_module in workspace_modules.items():
            merged_modules.append({**workspace_module, **returned.pop(module_id, {})})
            merged_modules[-1]["source_code"] = workspace_module["source_code"]
        merged_modules.extend(returned.values())
        result["implementation"] = {
            **dict(implementation),
            "entry_module": _trim(implementation.get("entry_module")) or (merged_modules[0].get("module_id") if merged_modules else ""),
            "modules": merged_modules,
        }
        return result

    def public_bundle(self, bundle: Mapping[str, Any]) -> Dict[str, Any]:
        return {str(key): value for key, value in bundle.items() if not str(key).startswith("_")}

    def prompt_context(self, bundle: Mapping[str, Any], fallback: Mapping[str, Any]) -> Dict[str, Any]:
        value = bundle.get("_prompt_context")
        return dict(value) if isinstance(value, Mapping) else dict(fallback)

    def _materialize_context(
        self,
        *,
        bundle_dir: Path,
        stage: str,
        context: Mapping[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        stage_name = _trim(stage).lower()
        prompt_context: Dict[str, Any] = {}
        coding_workspace: Dict[str, Any] = {"editable": False, "module_files": []}
        design: Dict[str, Any] = {}
        for key, value in context.items():
            if key in {"design", "current_design"} and isinstance(value, Mapping):
                design = dict(value)
                self._write_private(bundle_dir / "design.json", design)
                prompt_context["design_ref"] = "design.json"
                continue
            if key == "requirement_brief" and isinstance(value, str):
                prompt_context["requirement_brief"] = value
                continue
            if key == "requirement_brief" and isinstance(value, Mapping):
                prompt_context["requirement_brief"] = dict(value)
                continue
            if key == "test_feedback" and isinstance(value, Mapping):
                feedback_path = bundle_dir / "feedback" / "latest_test.json"
                self._write_private(feedback_path, dict(value))
                prompt_context["test_feedback_ref"] = str(feedback_path.relative_to(bundle_dir))
                continue
            if key == "test_history" and isinstance(value, (list, tuple)) and stage_name == "test":
                history_path = bundle_dir / "test_data" / "test_history.json"
                self._write_private(history_path, list(value))
                prompt_context["test_history_ref"] = str(history_path.relative_to(bundle_dir))
                continue
            if key == "current_implementation" and isinstance(value, Mapping) and stage_name == "coding":
                workspace = self._materialize_implementation(bundle_dir=bundle_dir, implementation=value)
                coding_workspace = workspace
                prompt_context["current_implementation"] = {
                    "revision": int(value.get("revision") or 0),
                    "manifest_ref": workspace.get("manifest"),
                    "module_files": workspace.get("module_files") or [],
                    "last_test_ref": "" if "test_feedback_ref" in prompt_context else workspace.get("last_test_ref") or "",
                }
                continue
            if not str(key).startswith("_"):
                prompt_context[str(key)] = value
        if stage_name == "coding" and not coding_workspace.get("module_files") and design:
            coding_workspace = self._materialize_new_implementation(bundle_dir=bundle_dir, design=design)
            prompt_context["current_implementation"] = {
                "revision": 0,
                "manifest_ref": coding_workspace.get("manifest"),
                "module_files": coding_workspace.get("module_files") or [],
                "module_plan_ref": coding_workspace.get("module_plan"),
                "last_test_ref": "",
            }
        elif stage_name == "coding" and coding_workspace.get("module_files") and design:
            module_plan_path = bundle_dir / "implementation" / "module_plan.json"
            self._write_private(module_plan_path, self._module_plan_payload(design))
            coding_workspace["module_plan"] = str(module_plan_path.relative_to(bundle_dir))
            coding_workspace["module_plan_items"] = self._module_plan_items(design)
            if isinstance(prompt_context.get("current_implementation"), dict):
                prompt_context["current_implementation"]["module_plan_ref"] = coding_workspace["module_plan"]
        if "test_feedback_ref" in prompt_context and isinstance(prompt_context.get("current_implementation"), dict):
            prompt_context["current_implementation"]["last_test_ref"] = ""
        return prompt_context, coding_workspace

    def _materialize_new_implementation(
        self,
        *,
        bundle_dir: Path,
        design: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Create an isolated first-implementation workspace for native Codex editing."""
        implementation_dir = bundle_dir / "implementation"
        modules_dir = implementation_dir / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)
        self._restrict_directory(implementation_dir)
        self._restrict_directory(modules_dir)
        tool_name = _trim(design.get("tool_name")) or "custom_tool"
        source_path = modules_dir / self._module_filename(tool_name, index=0)
        if not source_path.is_file():
            self._write_private(source_path, "")
        relative_path = str(source_path.relative_to(bundle_dir))
        module_plan_path = implementation_dir / "module_plan.json"
        self._write_private(module_plan_path, self._module_plan_payload(design))
        manifest_path = implementation_dir / "manifest.json"
        self._write_private(
            manifest_path,
            {
                "revision": 0,
                "entry_module": "main",
                "modules": [{
                    "module_id": "main",
                    "role": "dynamic_entry",
                    "language": "python",
                    "entrypoint": "run",
                    "path": relative_path,
                }],
            },
        )
        return {
            "editable": True,
            "first_implementation": True,
            "manifest": str(manifest_path.relative_to(bundle_dir)),
            "module_plan": str(module_plan_path.relative_to(bundle_dir)),
            "module_plan_items": self._module_plan_items(design),
            "module_files": [relative_path],
            "last_test_ref": "",
            "_module_records": [{
                "module_id": "main",
                "path": relative_path,
                "original_sha256": hashlib.sha256(b"").hexdigest(),
            }],
        }

    @staticmethod
    def _module_plan_payload(design: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "entrypoint": "run(inputs: dict) -> dict",
            "implementation_note": (
                "Use design.plan to choose logical function groups inside the dynamic entry module; they are not separately published files."
            ),
            "modules": [{
                "name": "main",
                "responsibility": "根据设计方案组织动态入口和必要内部函数。",
                "functions": [],
            }],
        }

    @staticmethod
    def _module_plan_items(design: Mapping[str, Any]) -> list[Dict[str, Any]]:
        return [{
            "id": "main",
            "title": "核心实现模块",
            "message": "根据设计方案拆分必要的内部函数并逐步验证。",
            "status": "pending",
        }]

    def _materialize_implementation(
        self,
        *,
        bundle_dir: Path,
        implementation: Mapping[str, Any],
    ) -> Dict[str, Any]:
        implementation_dir = bundle_dir / "implementation"
        modules_dir = implementation_dir / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)
        self._restrict_directory(implementation_dir)
        self._restrict_directory(modules_dir)
        manifest_rows = []
        module_files = []
        for index, item in enumerate(implementation.get("modules") or []):
            if not isinstance(item, Mapping):
                continue
            module = dict(item)
            source = str(module.pop("source_code", "") or "")
            module_id = _trim(module.get("module_id")) or f"module_{index + 1}"
            filename = self._module_filename(module_id, index=index)
            source_path = modules_dir / filename
            self._write_private(source_path, source)
            relative_path = str(source_path.relative_to(bundle_dir))
            module_files.append(relative_path)
            manifest_rows.append({
                **module,
                "module_id": module_id,
                "path": relative_path,
                "original_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            })
        last_test = implementation.get("last_test") if isinstance(implementation.get("last_test"), Mapping) else {}
        last_test_ref = ""
        if last_test:
            last_test_path = implementation_dir / "last_test.json"
            self._write_private(last_test_path, dict(last_test))
            last_test_ref = str(last_test_path.relative_to(bundle_dir))
        manifest_path = implementation_dir / "manifest.json"
        self._write_private(
            manifest_path,
            {
                "revision": int(implementation.get("revision") or 0),
                "modules": manifest_rows,
            },
        )
        return {
            "editable": bool(module_files),
            "manifest": str(manifest_path.relative_to(bundle_dir)),
            "module_files": module_files,
            "last_test_ref": last_test_ref,
            "_module_records": [
                {
                    "module_id": row["module_id"],
                    "path": row["path"],
                    "original_sha256": row["original_sha256"],
                }
                for row in manifest_rows
            ],
        }

    @staticmethod
    def _owner_scope(owner_id: Any) -> str:
        normalized = _trim(owner_id) or "anonymous"
        return "owner_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _module_filename(module_id: str, *, index: int) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", module_id).strip("_")
        return f"{index + 1:03d}_{normalized or f'module_{index + 1}'}.py"

    @staticmethod
    def _restrict_directory(path: Path) -> None:
        try:
            path.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _write_private(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        CustomToolContextBundleService._restrict_directory(path.parent)
        text = value if isinstance(value, str) else _json_text(value)
        path.write_text(text, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _load_catalog(self) -> Dict[str, Any]:
        if not self.catalog_path.exists():
            return {"version": "", "api_class_patterns": {}, "subjects": {}}
        return json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def _task_api_context(self, context: Mapping[str, Any], catalog: Mapping[str, Any]) -> Dict[str, Any]:
        """Expose design data needs and any legacy catalog locators to Coding, without enforcing either."""
        design_value = context.get("design") or context.get("current_design")
        design = design_value if isinstance(design_value, Mapping) else {}
        subjects = catalog.get("subjects") if isinstance(catalog.get("subjects"), Mapping) else {}
        patterns = catalog.get("api_class_patterns") if isinstance(catalog.get("api_class_patterns"), Mapping) else {}
        sources: list[Dict[str, Any]] = []
        data_needs: list[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for requirement in design.get("data_requirements") or []:
            if not isinstance(requirement, Mapping):
                continue
            topic = _trim(requirement.get("topic"))
            requested_fields = list(dict.fromkeys(
                _trim(field) for field in requirement.get("fields") or [] if _trim(field)
            ))
            purpose = _trim(requirement.get("purpose"))
            if topic or requested_fields or purpose:
                data_needs.append({
                    "topic": topic,
                    "fields": requested_fields,
                    "purpose": purpose,
                })
            source_ref = _trim(requirement.get("source_ref"))
            subject, dataview = self._parse_source_ref(source_ref)
            subject_obj = subjects.get(subject) if isinstance(subjects.get(subject), Mapping) else {}
            dataview_obj = subject_obj.get(dataview) if isinstance(subject_obj.get(dataview), Mapping) else {}
            if not subject or not dataview or not dataview_obj or (subject, dataview) in seen:
                continue
            seen.add((subject, dataview))
            compact = self._compact_subject(subject, {dataview: dataview_obj})
            view = (compact.get("dataviews") or {}).get(dataview) or {}
            available_fields = set((view.get("fields") or {}).keys())
            api_classes = list(dict.fromkeys(
                _trim(api.get("api_class"))
                for api in view.get("api") or []
                if isinstance(api, Mapping) and _trim(api.get("api_class"))
            ))
            sources.append({
                "source_ref": source_ref or f"{subject}.{dataview}",
                "purpose": purpose,
                "requested_fields": requested_fields,
                "query_fields": [field for field in requested_fields if field in available_fields],
                "unavailable_requested_fields": [field for field in requested_fields if field not in available_fields],
                "subject": subject,
                "dataview": dataview,
                "definition": view,
                "request_patterns": {
                    name: patterns.get(name)
                    for name in api_classes
                    if isinstance(patterns.get(name), Mapping)
                },
            })
        return {
            "usage": [
                "data_needs 是设计说明的数据主题、字段和用途，不是 API 名称或 allowlist。",
                "sources 只可能来自旧版本设计，作为优先参考；需要其他真实数据能力时再查 index.json。",
                "实际查询统一通过 custom_tool_sdk.finance_query(request=...)。",
                "查询只使用 catalog 中真实存在的字段；不要为计算方便发明字段、别名或代理口径。",
            ],
            "data_needs": data_needs,
            "sources": sources,
        }

    @staticmethod
    def _parse_source_ref(source_ref: str) -> tuple[str, str]:
        text = _trim(source_ref)
        catalog_match = re.search(r"subjects/([^/.#]+)\.json#dataviews\.([^/#]+)$", text)
        if catalog_match:
            return catalog_match.group(1), catalog_match.group(2)
        simple_match = re.fullmatch(r"([A-Za-z0-9_]+)[./]([A-Za-z0-9_]+)", text)
        return (simple_match.group(1), simple_match.group(2)) if simple_match else ("", "")

    def _materialize_coding_support(self, bundle_dir: Path) -> None:
        scratch_dir = bundle_dir / "scratch"
        dev_runtime_dir = bundle_dir / "dev_runtime"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        dev_runtime_dir.mkdir(parents=True, exist_ok=True)
        self._restrict_directory(scratch_dir)
        self._restrict_directory(dev_runtime_dir)
        self._write_private(
            dev_runtime_dir / "custom_tool_sdk.py",
            '''"""Local test double for isolated Coding checks only."""

_handler = None
_logs = []


def set_finance_query_handler(handler):
    global _handler
    _handler = handler


def finance_query(request: str):
    if _handler is None:
        raise RuntimeError("set a finance query handler in the focused test")
    return _handler(request)


def info(message: str, data=None):
    _logs.append({"level": "info", "message": message, "data": data or {}})


def debug(message: str, data=None):
    _logs.append({"level": "debug", "message": message, "data": data or {}})


def logs():
    return list(_logs)
'''
        )
        self._write_private(
            dev_runtime_dir / "test_support.py",
            '''"""Small helpers for focused tests of dynamic tool modules."""

import importlib.util
from pathlib import Path

from custom_tool_sdk import set_finance_query_handler


def load_module(module_path: str, name: str = "dynamic_tool"):
    path = Path(module_path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load dynamic module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_rows(rows):
    data = list(rows)
    columns = list(data[0].keys()) if data else []
    set_finance_query_handler(
        lambda request: {"ok": True, "error": "", "columns": columns, "data": data, "rows": data}
    )
'''
        )
        python_executable = str(Path(sys.executable).resolve())
        self._write_private(
            bundle_dir / "CODING_WORKSPACE.md",
            f"""# Coding workspace

- Edit only the module paths listed in `CONTEXT.current_implementation.module_files`.
- `implementation/module_plan.json` describes logical function groups; keep one dynamic entry module.
- Put temporary focused tests under `scratch/`; they are never persisted as user assets.
- Python interpreter: `{python_executable}`. Use this exact interpreter instead of `python` or the system `python3`.
- For compilation use `PYTHONPYCACHEPREFIX=scratch/pycache {python_executable} -m py_compile <module>`.
- For focused tests use `PYTHONPATH=dev_runtime {python_executable} <test-or-script>` so `custom_tool_sdk` is available.
- Focused tests may use `from test_support import load_module, install_rows` to load the entry file directly and install deterministic API rows. Build enough dated rows for the Design's minimum window; do not import the numeric module filename as a Python package.
- `api_catalog/task_context.json` is the first reference for the Design's data needs. Match its topics and fields against `index.json`, then read only the relevant subject and request pattern. Do not treat it as an API allowlist.
- Compile and test each cohesive function group before moving to the next one.
"""
        )

    def _compact_subject(self, subject: str, subject_obj: Mapping[str, Any]) -> Dict[str, Any]:
        dataviews = {}
        for name, dataview in subject_obj.items():
            if str(name).startswith("_") or not isinstance(dataview, dict):
                continue
            dataviews[name] = {
                "desc": dataview.get("desc") or dataview.get("description") or "",
                "rules": dataview.get("rules") or [],
                "fields": self._compact_fields(dataview.get("fields")),
                "api": dataview.get("api") or [],
                "kd": dataview.get("kd") if "kd" in dataview else None,
            }
        return {
            "subject": subject,
            "meta": subject_obj.get("_meta") or {},
            "dataviews": dataviews,
        }

    @staticmethod
    def _compact_fields(fields: Any) -> Dict[str, Any]:
        if not isinstance(fields, dict):
            return {}
        result: Dict[str, Any] = {}
        for name, spec in fields.items():
            if isinstance(spec, dict):
                result[name] = {
                    "desc": spec.get("desc") or spec.get("description") or "",
                    "aliases": spec.get("aliases") or [],
                    "type": spec.get("type") or "",
                }
            else:
                result[name] = spec
        return result

    @staticmethod
    def _safe_id(value: str) -> str:
        raw = _trim(value)
        raw = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw).strip("_")
        return raw[:80]

    @staticmethod
    def _runtime_contract() -> str:
        return """# Custom Tool Runtime Contract

- Python entrypoint: `run(inputs: dict) -> dict`.
- Return value must be JSON serializable.
- Do not read secrets.
- Do not directly access database tables, raw provider modules, or network from generated tool code.
- If finance data is needed, use `custom_tool_sdk.finance_query(request=...)`.
- Use `custom_tool_sdk.info(message, data)` for stage/count summaries and `custom_tool_sdk.debug(message, data)` for the formula inputs and intermediate values that explain the result.
- Keep these facts small and structured so the test result can show users how the conclusion was reached.
"""

    @staticmethod
    def _sdk_doc() -> str:
        return """# custom_tool_sdk

Generated custom-tool code can use these stable helpers:

```python
from custom_tool_sdk import debug, finance_query, info

def run(inputs: dict) -> dict:
    info("calculation_started", {"stock_code": inputs.get("stock_code")})
    quote = finance_query(
        request='r1 = stock.quote(filter = "code = 600519.SH", order = "tradedate desc", limit = 1) -> code, name, tradedate, close, pct'
    )
    debug("quote_selected", {"close": (quote.get("data") or [{}])[0].get("close")})
    return {"quote": quote}
```

## finance_query

`finance_query(request: str) -> dict`

Use the finance data protocol request string. The API catalog files describe available subjects, dataviews, fields, and request patterns.

The injected SDK normalizes every successful query to this stable envelope:

```python
{
    "ok": True,
    "error": "",
    "columns": ["code", "tradedate", "adjclose"],
    "data": [{"code": "600519.SH", "tradedate": "...", "adjclose": 1.0}],
    "rows": [...]  # same list as data
}
```

Generated modules should read rows from `result["data"]` after checking `result["ok"]`. Provider-specific nesting remains internal to the SDK adapter.

## info / debug

- `info(message: str, data: dict | None = None)` records stage summaries and counts.
- `debug(message: str, data: dict | None = None)` records the formula inputs and core intermediate metrics that explain the final result.
- Keep records small and structured. Do not log secrets, raw provider envelopes, or full market-data rows.

"""
