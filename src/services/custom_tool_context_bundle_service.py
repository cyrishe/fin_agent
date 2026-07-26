from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import uuid
from typing import Any, Dict, Mapping

from src.services.coding_execution_contract import (
    compile_command,
    focused_test_command,
    python_executable,
)


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
        coding_api_guide_path: str = "src/skills/financial-tool-development/skills/financial-tool-implementation/references/finance-api-coding-guide.md",
        root_dir: str = "data/custom_tool_context",
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.stock_universe_path = Path(stock_universe_path)
        self.coding_api_guide_path = Path(coding_api_guide_path)
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
                subject_root = subject_dir / subject
                dataview_rows = []
                for dataview, dataview_obj in subject_obj.items():
                    if str(dataview).startswith("_") or not isinstance(dataview_obj, Mapping):
                        continue
                    dataview_file = subject_root / f"{dataview}.json"
                    dataview_doc = self._compact_dataview(
                        subject=subject,
                        dataview=str(dataview),
                        definition=dataview_obj,
                        patterns=patterns,
                    )
                    self._write_private(dataview_file, dataview_doc)
                    dataview_rows.append({
                        "dataview": dataview,
                        "description": _trim(dataview_obj.get("desc")),
                        "method_names": [
                            _trim(method.get("name"))
                            for method in dataview_doc.get("methods") or []
                            if isinstance(method, Mapping) and _trim(method.get("name"))
                        ],
                        "file": str(dataview_file.relative_to(bundle_dir)),
                    })
                subject_file = subject_root / "index.json"
                self._write_private(subject_file, {
                    "subject": subject,
                    "meta": subject_obj.get("_meta") or {},
                    "dataviews": dataview_rows,
                })
                index_subjects.append({
                    "subject": subject,
                    "description": _trim((subject_obj.get("_meta") or {}).get("desc")),
                    "rules": (subject_obj.get("_meta") or {}).get("rules") or [],
                    "dataviews": dataview_rows,
                    "file": str(subject_file.relative_to(bundle_dir)),
                })
            self._write_private(
                api_dir / "index.json",
                {
                    "version": catalog.get("version"),
                    "source_catalog": str(self.catalog_path),
                    "usage": [
                        "先读取本 index，按任务判断需要哪些 subject/dataview。",
                        "读取相关 subject/index.json 定位 dataview 文件；不要全量读取所有 subject 或 dataview。",
                        "每个 dataview 文件独立提供字段、具体方法、参数、返回规则和示例。",
                        (
                            "生成自定义工具代码时，优先调用 custom_tool_sdk.finance_query(request=...)，不要直接访问数据库或底层 provider。"
                            if coding_stage
                            else "测试规划可据此了解系统能够提供的股票、指数、行业、板块、基金、债券等真实输入范围。"
                        ),
                    ],
                    "subjects": index_subjects,
                },
            )
            public_bundle["api_index"] = str((api_dir / "index.json").relative_to(bundle_dir))
            if not coding_stage:
                self._write_private(api_dir / "request_patterns.json", patterns)
                public_bundle["request_patterns"] = str(
                    (api_dir / "request_patterns.json").relative_to(bundle_dir)
                )
            if coding_stage:
                if self.coding_api_guide_path.is_file():
                    coding_api_guide = api_dir / "CODING_GUIDE.md"
                    self._write_private(
                        coding_api_guide,
                        self.coding_api_guide_path.read_text(encoding="utf-8"),
                    )
                    public_bundle["api_coding_guide"] = str(coding_api_guide.relative_to(bundle_dir))
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
                    "module_template": "DYNAMIC_TOOL_TEMPLATE.py",
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
        for module in merged_modules:
            if _trim(module.get("source_code")):
                result["code"] = _trim(module.get("source_code"))
                break
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
            view = self._compact_dataview(
                subject=subject,
                dataview=dataview,
                definition=dataview_obj,
                patterns=patterns,
            )
            available_fields = set((view.get("fields") or {}).keys())
            sources.append({
                "source_ref": source_ref or f"{subject}.{dataview}",
                "purpose": purpose,
                "requested_fields": requested_fields,
                "query_fields": [field for field in requested_fields if field in available_fields],
                "unavailable_requested_fields": [field for field in requested_fields if field not in available_fields],
                "subject": subject,
                "dataview": dataview,
                "asset": f"api_catalog/subjects/{subject}/{dataview}.json",
                "method_names": [
                    _trim(method.get("name"))
                    for method in view.get("methods") or []
                    if isinstance(method, Mapping) and _trim(method.get("name"))
                ],
            })
        return {
            "usage": [
                "data_needs 是设计说明的数据主题、字段和用途，不是 API 名称或 allowlist。",
                "sources 只提供优先检索的 dataview 资产路径，不复制完整 API 说明；需要其他数据能力时再查 index.json。",
                "实际查询统一通过 custom_tool_sdk.finance_query(request=...)。",
                "查询只使用 catalog 中真实存在的字段；不要为计算方便发明字段、别名或代理口径。",
            ],
            "data_needs": data_needs,
            "sources": sources,
        }

    @staticmethod
    def _coding_api_pattern(value: Any) -> Dict[str, Any]:
        pattern = value if isinstance(value, Mapping) else {}
        call_pattern = _trim(pattern.get("call_pattern")).replace("r{id}", "{result_name}")
        compact = {
            key: (
                CustomToolContextBundleService._coding_rules(pattern.get(key))
                if key == "rules"
                else pattern.get(key)
            )
            for key in ("desc", "args", "rules", "methods", "output_rule")
            if pattern.get(key) not in (None, "", [], {})
        }
        if call_pattern:
            compact["call_pattern"] = call_pattern
        return compact

    @staticmethod
    def _coding_rules(value: Any) -> list[str]:
        """Keep Coding assets semantic; do not expose the legacy BI step language."""
        if not isinstance(value, list):
            return []
        rules: list[str] = []
        for item in value:
            text = _trim(item)
            if not text:
                continue
            lower = text.lower()
            if "agg" in lower and "column" in lower and "previous" in lower:
                rules.append(
                    "A previously computed per-stock result used for aggregation must include code or stock_code."
                )
                continue
            if "previous per-stock result field like" in lower:
                rules.append(
                    "A metric may use a defined dataview field or a field from a previous per-stock result."
                )
                continue
            rules.append(text)
        return rules

    @staticmethod
    def _parse_source_ref(source_ref: str) -> tuple[str, str]:
        text = _trim(source_ref)
        dataview_file_match = re.search(
            r"subjects/([^/]+)/([^/.#]+)\.json$",
            text,
        )
        if dataview_file_match:
            return dataview_file_match.group(1), dataview_file_match.group(2)
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
        self._write_private(
            bundle_dir / "DYNAMIC_TOOL_TEMPLATE.py",
            '''"""Reference pattern for a Fin Agent dynamic tool.

Copy the shape, then replace the example calculation and public fields with the
current Design. This file is reference-only; implement in the module path named
by CONTEXT.current_implementation.module_files.
"""

from custom_tool_sdk import finance_query


def _result(*, result: dict, key_process_info: dict) -> dict:
    """Keep the public conclusion and its compact supporting facts together."""
    return {**result, "key_process_info": dict(key_process_info)}


def run(inputs: dict) -> dict:
    stock_code = str(inputs.get("stock_code") or "").strip()
    if not stock_code:
        return _result(
            result={"ok": False, "reason": "stock_code is required"},
            key_process_info={"stage": "input_validation"},
        )

    response = finance_query(
        request=(
            'latest_quote = stock.quote(filter = "code = '
            + stock_code
            + '", order = "tradedate desc", limit = 1) '
            '-> code, tradedate, close'
        )
    )
    rows = list(response.get("data") or []) if response.get("ok") else []
    key_process_info = {
        "stock_code": stock_code,
        "sample_count": len(rows),
        "query_ok": bool(response.get("ok")),
    }
    if rows:
        key_process_info.update({
            "as_of_date": rows[0].get("tradedate"),
            "close": rows[0].get("close"),
        })
    if not response.get("ok"):
        return _result(
            result={"ok": False, "reason": str(response.get("error") or "finance query failed")},
            key_process_info=key_process_info,
        )
    if not rows:
        return _result(
            result={"ok": True, "reason": "no matching data"},
            key_process_info=key_process_info,
        )
    return _result(
        result={"ok": True, "close": rows[0].get("close")},
        key_process_info=key_process_info,
    )
'''
        )
        interpreter = python_executable()
        self._write_private(
            bundle_dir / "CODING_WORKSPACE.md",
            f"""# Coding workspace

- Edit only the module paths listed in `CONTEXT.current_implementation.module_files`.
- On a first implementation, read `DYNAMIC_TOOL_TEMPLATE.py`; it is a reference, not an editable module.
- Put temporary focused tests under `scratch/`; they are never persisted as user assets.
- Python interpreter: `{interpreter}`. Use this exact interpreter instead of `python` or the system `python3`.
- For compilation use `{compile_command("<module>")}`.
- For focused tests use `{focused_test_command("<test-or-script>")}` so `custom_tool_sdk` is available.
- Focused tests may use `from test_support import load_module, install_rows`.
- Read `api_catalog/CODING_GUIDE.md` first. Use `task_context.json` when present, then follow the subject index to the relevant dataview files. Do not load the whole catalog or treat it as an API allowlist.
"""
        )

    def _compact_subject(
        self,
        subject: str,
        subject_obj: Mapping[str, Any],
        patterns: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        api_patterns = patterns if isinstance(patterns, Mapping) else {}
        dataviews = {}
        for name, dataview in subject_obj.items():
            if str(name).startswith("_") or not isinstance(dataview, dict):
                continue
            dataviews[name] = self._compact_dataview(
                subject=subject,
                dataview=str(name),
                definition=dataview,
                patterns=api_patterns,
            )
        return {
            "subject": subject,
            "meta": subject_obj.get("_meta") or {},
            "dataviews": dataviews,
        }

    def _compact_dataview(
        self,
        *,
        subject: str,
        dataview: str,
        definition: Mapping[str, Any],
        patterns: Mapping[str, Any],
    ) -> Dict[str, Any]:
        apis = definition.get("api") if isinstance(definition.get("api"), list) else []
        return {
            "subject": subject,
            "dataview": dataview,
            "description": definition.get("desc") or definition.get("description") or "",
            "rules": self._coding_rules(definition.get("rules")),
            "fields": self._compact_fields(definition.get("fields")),
            "methods": [
                self._subject_method(
                    subject=subject,
                    dataview=dataview,
                    api=api,
                    dataview_definition=definition,
                    patterns=patterns,
                )
                for api in apis
                if isinstance(api, Mapping)
            ],
            "kd": definition.get("kd") if "kd" in definition else None,
            **(
                {"computed": definition.get("computed")}
                if definition.get("computed") not in (None, {}, [])
                else {}
            ),
        }

    def _subject_method(
        self,
        *,
        subject: str,
        dataview: str,
        api: Mapping[str, Any],
        dataview_definition: Mapping[str, Any],
        patterns: Mapping[str, Any],
    ) -> Dict[str, Any]:
        api_name = _trim(api.get("api_name"))
        api_class = _trim(api.get("api_class"))
        raw_class_definition = (
            patterns.get(api_class)
            if isinstance(patterns.get(api_class), Mapping)
            else {}
        )
        class_definition = self._coding_api_pattern(raw_class_definition)
        call_pattern = _trim(class_definition.pop("call_pattern", ""))
        if call_pattern:
            call_pattern = (
                call_pattern
                .replace("{api_name}", api_name)
                .replace("{subject}", subject)
                .replace("{dataview}", dataview)
            )
        method = {
            "name": api_name,
            "description": _trim(api.get("api_function")),
            "type": api_class,
            **({"call": call_pattern} if call_pattern else {}),
            **class_definition,
            "examples": self._method_examples(
                api=api,
                api_class=api_class,
                class_definition=raw_class_definition,
                dataview_definition=dataview_definition,
                dataview=dataview,
            ),
        }
        kd = dataview_definition.get("kd")
        if "<field>" in api_name and kd:
            method["available_names"] = {
                "pattern": api_name,
                "field_methods": kd,
            }
        if (
            subject == "stock"
            and dataview == "quote"
            and api_name == "stock.quote.kd_<field>_<method>"
        ):
            minute_fields = {
                field: methods
                for field, methods in (kd.items() if isinstance(kd, Mapping) else [])
                if str(field).startswith("minute_")
            }
            same_minute = self._coding_api_pattern(
                patterns.get("intraday_same_minute_kday_metric")
            )
            same_minute_call = _trim(same_minute.pop("call_pattern", ""))
            if same_minute_call:
                same_minute["call"] = same_minute_call.replace("r{id}", "{result_name}")
            method["same_minute_variant"] = {
                "fields": minute_fields,
                **same_minute,
            }
        return method

    def _method_examples(
        self,
        *,
        api: Mapping[str, Any],
        api_class: str,
        class_definition: Mapping[str, Any],
        dataview_definition: Mapping[str, Any],
        dataview: str,
    ) -> list[str]:
        api_name = _trim(api.get("api_name"))
        result_name = f"{self._safe_id(dataview) or 'query'}_rows"
        candidates = []
        explicit = api.get("examples")
        if isinstance(explicit, list):
            candidates.extend(explicit)
        elif _trim(api.get("example")):
            candidates.append(api.get("example"))
        candidates.extend(class_definition.get("examples") or [])

        examples: list[str] = []
        for candidate in candidates:
            normalized = self._coding_example(
                candidate,
                api_name=api_name,
                result_name=result_name,
            )
            if normalized and normalized not in examples:
                examples.append(normalized)
            if len(examples) >= 3:
                break
        if examples:
            return examples
        return [
            self._generated_method_example(
                api_name=api_name,
                api_class=api_class,
                result_name=result_name,
                dataview_definition=dataview_definition,
            )
        ]

    @classmethod
    def _coding_example(
        cls,
        value: Any,
        *,
        api_name: str,
        result_name: str,
    ) -> str:
        text = _trim(value)
        if not text:
            return ""
        request_line = next(
            (line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("note:")),
            "",
        )
        match = re.match(r"r\d+\s*=\s*([A-Za-z0-9_.<>]+)\s*\(", request_line)
        if not match or not cls._api_example_matches(api_name, match.group(1)):
            return ""
        normalized = re.sub(r"^r\d+\s*=", f"{result_name} =", text, count=1)
        if re.search(r"\br\d+\.", normalized):
            return ""
        return normalized

    @staticmethod
    def _api_example_matches(api_pattern: str, example_api: str) -> bool:
        escaped = re.escape(api_pattern)
        escaped = escaped.replace(re.escape("<field>"), r"[A-Za-z0-9_]+")
        escaped = escaped.replace(re.escape("<method>"), r"[A-Za-z0-9_]+")
        return re.fullmatch(escaped, example_api) is not None

    @staticmethod
    def _generated_method_example(
        *,
        api_name: str,
        api_class: str,
        result_name: str,
        dataview_definition: Mapping[str, Any],
    ) -> str:
        field_names = list(
            (dataview_definition.get("fields") or {}).keys()
            if isinstance(dataview_definition.get("fields"), Mapping)
            else []
        )
        output_fields = ", ".join(field_names[:6]) or "field1, field2"
        kd = dataview_definition.get("kd")
        if api_class in {"kday_metric", "kday_margin_metric"}:
            if isinstance(kd, Mapping) and kd:
                field = str(next(iter(kd)))
                supported = kd.get(field) or []
                method = str(supported[0] if supported else "avg")
            else:
                field = str(kd[0]) if isinstance(kd, list) and kd else "field"
                method = "percentile" if api_name.endswith("_percentile") else "avg"
            concrete_api = api_name.replace("<field>", field).replace("<method>", method)
            return (
                f"{result_name} = {concrete_api}(k = 20, realtime = 0) "
                f"-> code, name, value as {field}_{method}_20d"
            )
        if api_class == "constituent_aggregate":
            group_fields = [
                field for field in field_names if not field.startswith("stock_")
            ][:2]
            groups = ", ".join(group_fields) or "subject_code, subject_name"
            return (
                f"{result_name} = {api_name}("
                f'agg = count(stock.quote.code), group_by = "{groups}", limit = 20'
                f") -> {groups}, member_count"
            )
        if api_class == "intraday_cross_section_aggregate":
            return (
                f"{result_name} = {api_name}("
                "agg = avg(stock.quote.pct), realtime = 2"
                ") -> avg_pct"
            )
        if api_class == "dynamic_quote_cal":
            return (
                f"{result_name} = {api_name}("
                'k = 20, fields = "code, name, tradedate, open, close", '
                'task = "统计每只股票近20个交易日收盘价高于开盘价的天数", '
                "realtime = 0"
                ") -> code, name, close_gt_open_days"
            )
        return f"{result_name} = {api_name}(limit = 20) -> {output_fields}"

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

- Entry point: `run(inputs: dict) -> dict`.
- Return a JSON-serializable object containing `key_process_info`.
- Read financial data through `custom_tool_sdk.finance_query`; do not access databases, providers, credentials, or the network directly.
"""

    @staticmethod
    def _sdk_doc() -> str:
        return """# custom_tool_sdk

`finance_query(request: str) -> dict`

Request format: `result_name = api_name(arguments) -> output_fields`.

The whole `filter` argument is a quoted string. Values inside it are bare protocol literals, for example `filter = "code = 600519.SH and tradedate <= 2026-07-23"`.

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

Check `result["ok"]`, then read rows from `result["data"]`.

"""
