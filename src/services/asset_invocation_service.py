from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

from src.prompting.prompt_registry import get_prompt_registry
from src.services.attachment_service import AttachmentService
from src.services.file_io_tool_service import FileIoToolService
from src.services.stock_identity_resolver_service import StockIdentityResolverService
from src.utils.ai_service import chat_qwen_flash_json

if TYPE_CHECKING:
    from src.services.custom_tool_service import CustomToolStoreService


class AssetInvocationError(ValueError):
    pass


class AssetInvocationService:
    """Resolve an explicit `$asset` selection into a small executable plan."""

    _INVOCATION_RE = re.compile(r"^\s*\$([^\s]+)(?:\s+|$)")

    def __init__(
        self,
        *,
        custom_tool_store: Optional["CustomToolStoreService"] = None,
        attachment_service: Optional[AttachmentService] = None,
        file_io_service: Optional[FileIoToolService] = None,
        tool_definitions_dir: str = "src/tools/definitions",
        skills_root: str = "src/skills",
        llm_chat: Optional[Callable[..., Any]] = None,
        stock_identity_resolver: Optional[StockIdentityResolverService] = None,
    ) -> None:
        if custom_tool_store is None:
            from src.services.custom_tool_service import CustomToolStoreService

            custom_tool_store = CustomToolStoreService()
        self.custom_tool_store = custom_tool_store
        self.attachment_service = attachment_service or AttachmentService()
        self.file_io_service = file_io_service or FileIoToolService()
        self.tool_definitions_dir = Path(tool_definitions_dir)
        self.skills_root = Path(skills_root)
        self.llm_chat = llm_chat or chat_qwen_flash_json
        self.stock_identity_resolver = stock_identity_resolver or StockIdentityResolverService()
        self.registry = get_prompt_registry()

    @staticmethod
    def _trim(value: Any) -> str:
        return str(value or "").strip()

    def detect_target(self, *, text: str, selected_asset: Mapping[str, Any] | None = None) -> Dict[str, str]:
        selected = dict(selected_asset or {})
        name = self._trim(selected.get("name"))
        kind = self._trim(selected.get("kind")).lower()
        match = self._INVOCATION_RE.match(str(text or ""))
        if not name and match:
            name = self._trim(match.group(1))
        if not name:
            return {}
        if kind not in {"tool", "skill"}:
            if self.custom_tool_store.exists(name) or (self.tool_definitions_dir / f"{name}.tool.json").exists():
                kind = "tool"
            elif (self.skills_root / name).is_dir():
                kind = "skill"
        if kind not in {"tool", "skill"}:
            raise AssetInvocationError(f"未找到可调用的 Tool 或 Skill：{name}")
        return {"kind": kind, "name": name}

    def strip_invocation_prefix(self, text: str, *, target_name: str) -> str:
        raw = str(text or "").strip()
        match = self._INVOCATION_RE.match(raw)
        if match and self._trim(match.group(1)) == self._trim(target_name):
            return raw[match.end():].strip()
        return raw

    def load_contract(
        self,
        *,
        kind: str,
        name: str,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = False,
    ) -> Dict[str, Any]:
        normalized_kind = self._trim(kind).lower()
        normalized_name = self._trim(name)
        if normalized_kind == "tool":
            return self._load_tool_contract(
                normalized_name,
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
        if normalized_kind == "skill":
            return self._load_skill_contract(normalized_name)
        raise AssetInvocationError(f"不支持的资产类型：{normalized_kind or '-'}")

    def plan(
        self,
        *,
        text: str,
        selected_asset: Mapping[str, Any] | None = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        thread_context: Optional[Dict[str, Any]] = None,
        owner_ids: Optional[List[str]] = None,
        allow_inactive: bool = False,
    ) -> Dict[str, Any]:
        target = self.detect_target(text=text, selected_asset=selected_asset)
        if not target:
            return {}
        contract = self.load_contract(
            kind=target["kind"],
            name=target["name"],
            owner_ids=owner_ids,
            allow_inactive=allow_inactive,
        )
        user_request = self.strip_invocation_prefix(text, target_name=target["name"])
        attachment_payload = self._read_attachments(attachments or [])
        properties = self._schema_properties(contract.get("input_schema"))
        required = self._schema_required(contract.get("input_schema"))

        if not properties and not user_request and not attachment_payload:
            model_payload: Dict[str, Any] = {
                "status": "ready",
                "arguments": {},
                "execution": {"mode": "single", "item_argument": "", "items": []},
                "missing_required": [],
                "reason": "该资产无需输入参数，可直接执行。",
            }
            usage: Any = None
        else:
            messages = self.registry.render_messages(
                "system.assistant.asset_invocation",
                {
                    "asset_contract_json": json.dumps(contract, ensure_ascii=False, indent=2),
                    "user_request": user_request or "（用户未额外输入自然语言要求）",
                    "context_json": json.dumps(self._compact_context(thread_context or {}), ensure_ascii=False, indent=2),
                    "attachments_json": json.dumps(attachment_payload, ensure_ascii=False, indent=2),
                },
            )
            try:
                model_payload, usage = self.llm_chat(messages, enable_think=False)
            except Exception as exc:
                raise AssetInvocationError(f"调用解析失败：{exc}") from exc
            if not isinstance(model_payload, dict):
                raise AssetInvocationError("调用解析失败：LLM 未返回合法 JSON")

        execution = model_payload.get("execution") if isinstance(model_payload.get("execution"), dict) else {}
        mode = self._trim(execution.get("mode")).lower() or "single"
        if mode not in {"single", "map"}:
            mode = "single"
        arguments = model_payload.get("arguments") if isinstance(model_payload.get("arguments"), dict) else {}
        items = execution.get("items") if isinstance(execution.get("items"), list) else []
        item_argument = self._trim(execution.get("item_argument"))
        normalized_calls, missing_required = self._normalize_calls(
            target=target,
            input_schema=contract.get("input_schema") if isinstance(contract.get("input_schema"), dict) else {},
            arguments=arguments,
            mode=mode,
            item_argument=item_argument,
            items=items,
        )
        resolved_entities, unresolved_entities = self._resolve_entities(
            normalized_calls,
            model_payload.get("entities"),
        )
        model_missing = [self._trim(item) for item in (model_payload.get("missing_required") or []) if self._trim(item)]
        missing_required = list(dict.fromkeys([*model_missing, *missing_required]))
        status = "needs_input" if missing_required or unresolved_entities or not normalized_calls else "ready"
        message = (
            "还需要补充：" + "、".join(missing_required)
            if missing_required
            else "未能确认股票标的：" + "、".join(unresolved_entities) + "。请补充准确的公司全称或股票代码。"
            if unresolved_entities
            else self._trim(model_payload.get("reason")) or "调用参数已准备完成。"
        )
        invocation = {
            "version": "v1",
            "status": status,
            "target": target,
            "user_request": user_request,
            "arguments": arguments,
            "execution": {
                "mode": mode,
                "item_argument": item_argument,
                "items": items,
            },
            "calls": normalized_calls,
            "resolved_entities": resolved_entities,
            "missing_required": missing_required,
            "message": message,
            "contract": contract,
            "attachments": attachment_payload,
            "llm_usage": self._normalize_usage(usage),
        }
        invocation["preview"] = self.build_preview(invocation)
        return invocation

    def build_preview(self, invocation: Mapping[str, Any]) -> Dict[str, Any]:
        target = invocation.get("target") if isinstance(invocation.get("target"), Mapping) else {}
        contract = invocation.get("contract") if isinstance(invocation.get("contract"), Mapping) else {}
        calls = [dict(item) for item in (invocation.get("calls") or []) if isinstance(item, Mapping)]
        entities = [dict(item) for item in (invocation.get("resolved_entities") or []) if isinstance(item, Mapping)]
        entity_arguments = {self._trim(item.get("argument")) for item in entities}
        properties = self._schema_properties(contract.get("input_schema"))
        display_name = self._trim(contract.get("display_name")) or self._trim(target.get("name"))

        lines = [f"正在使用「{display_name}」。"]
        if entities:
            labels = []
            for entity in entities:
                name = self._trim(entity.get("name"))
                code = self._trim(entity.get("code")).split(".", 1)[0]
                label = f"{name}（{code}）" if name and code else name or code
                if label and label not in labels:
                    labels.append(label)
            if labels:
                lines.append("标的：" + "、".join(labels))
        if len(calls) > 1:
            lines.append(f"执行方式：逐项运行，共 {len(calls)} 项")
        if calls:
            argument_lines = []
            for name, value in calls[0].items():
                if name in entity_arguments or self._is_missing(value):
                    continue
                definition = properties.get(name) if isinstance(properties.get(name), Mapping) else {}
                label = self._trim(definition.get("title")) or name
                argument_lines.append(f"{label}={self._display_value(value)}")
            if argument_lines:
                lines.append("其他参数：" + "；".join(argument_lines))
        public_entities = [
            {
                "kind": self._trim(item.get("kind")),
                "name": self._trim(item.get("name")),
                "display_code": self._trim(item.get("code")).split(".", 1)[0],
            }
            for item in entities
        ]
        return {
            "target": {"kind": self._trim(target.get("kind")), "name": self._trim(target.get("name")), "display_name": display_name},
            "entities": public_entities,
            "call_count": len(calls),
            "message": "\n".join(lines),
        }

    def build_tool_execution_plan(self, invocation: Mapping[str, Any]) -> Dict[str, Any]:
        target = invocation.get("target") if isinstance(invocation.get("target"), Mapping) else {}
        tool_name = self._trim(target.get("name"))
        calls = [dict(item) for item in (invocation.get("calls") or []) if isinstance(item, Mapping)]
        return {
            "plan_type": "explicit_asset_invocation",
            "objective": self._trim(invocation.get("user_request")) or f"调用 {tool_name}",
            "selected_tools": [tool_name] if tool_name else [],
            "work_items": [
                {
                    "step_id": f"{tool_name}_{index + 1}",
                    "type": "tool",
                    "name": tool_name,
                    "intent": self._trim(invocation.get("user_request")),
                    "arguments": call,
                    "depends_on": [],
                }
                for index, call in enumerate(calls)
            ],
        }

    def guidance(self, contract: Mapping[str, Any]) -> Dict[str, Any]:
        schema = contract.get("input_schema") if isinstance(contract.get("input_schema"), Mapping) else {}
        properties = self._schema_properties(schema)
        required = set(self._schema_required(schema))
        fields = []
        for name, definition in properties.items():
            field = definition if isinstance(definition, Mapping) else {}
            fields.append({
                "name": name,
                "required": name in required,
                "type": self._trim(field.get("type")) or "string",
                "description": self._trim(field.get("description") or field.get("title")),
                "default": field.get("default"),
            })
        return {
            "fields": fields,
            "can_run_directly": not required,
            "requires_natural_language": bool(contract.get("requires_natural_language")),
        }

    def _load_tool_contract(
        self,
        name: str,
        *,
        owner_ids: Optional[List[str]],
        allow_inactive: bool,
    ) -> Dict[str, Any]:
        if self.custom_tool_store.exists(name):
            bundle = self.custom_tool_store.load_for_runtime(
                name,
                owner_ids=owner_ids,
                allow_inactive=allow_inactive,
            )
            manifest = bundle.get("manifest") if isinstance(bundle.get("manifest"), dict) else {}
            input_schema = bundle.get("input_schema") if isinstance(bundle.get("input_schema"), dict) else {}
            design_contract = bundle.get("design_contract") if isinstance(bundle.get("design_contract"), dict) else {}
            return {
                "kind": "tool",
                "name": name,
                "display_name": self._trim(manifest.get("display_name")) or name,
                "description": self._trim(manifest.get("description")),
                "input_schema": self._with_design_labels(input_schema, design_contract),
                "sample_input": bundle.get("sample_input") if isinstance(bundle.get("sample_input"), dict) else {},
                "custom_tool": True,
            }
        path = self.tool_definitions_dir / f"{name}.tool.json"
        if not path.exists():
            raise AssetInvocationError(f"Tool 不存在：{name}")
        definition = self._load_json(path)
        identity = definition.get("identity") if isinstance(definition.get("identity"), dict) else {}
        schemas = definition.get("schemas") if isinstance(definition.get("schemas"), dict) else {}
        input_schema = schemas.get("input") if isinstance(schemas.get("input"), dict) else {}
        return {
            "kind": "tool",
            "name": name,
            "display_name": self._trim(identity.get("display_name")) or name,
            "description": self._trim(identity.get("description")),
            "input_schema": input_schema,
            "sample_input": self._first_sample_input(definition),
            "custom_tool": False,
        }

    def _load_skill_contract(self, name: str) -> Dict[str, Any]:
        skill_dir = self.skills_root / name
        if not skill_dir.is_dir():
            raise AssetInvocationError(f"Skill 不存在：{name}")
        config = self._load_json(skill_dir / "skill.json")
        input_schema = config.get("input_schema") if isinstance(config.get("input_schema"), dict) else {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {
                    "type": "string",
                    "description": "希望该 Skill 完成的自然语言任务",
                }
            },
        }
        return {
            "kind": "skill",
            "name": name,
            "display_name": self._trim(config.get("display_name")) or name,
            "description": self._trim(config.get("purpose") or config.get("description")),
            "input_schema": input_schema,
            "sample_input": config.get("sample_input") if isinstance(config.get("sample_input"), dict) else {},
            "requires_natural_language": "question" in self._schema_required(input_schema),
        }

    def _read_attachments(self, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            item = {
                "attachment_id": self._trim(attachment.get("attachment_id")),
                "file_name": self._trim(attachment.get("file_name")),
                "kind": self._trim(attachment.get("kind")),
            }
            if item["kind"] in {"table", "document"}:
                try:
                    file_path = self.attachment_service.resolve_absolute_path(attachment)
                    parsed = self.file_io_service.run({
                        "action": "read",
                        "file_path": file_path,
                        "max_preview_rows": 50,
                        "max_chars": 10000,
                    })
                    item["parsed"] = self._compact_file_result(parsed)
                except Exception as exc:
                    item["parse_error"] = str(exc)
            results.append(item)
        return results

    def _normalize_calls(
        self,
        *,
        target: Dict[str, str],
        input_schema: Dict[str, Any],
        arguments: Dict[str, Any],
        mode: str,
        item_argument: str,
        items: List[Any],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        raw_calls: List[Dict[str, Any]] = []
        if mode == "map" and items:
            for item in items:
                if isinstance(item, Mapping):
                    raw_calls.append({**arguments, **dict(item)})
                elif item_argument:
                    raw_calls.append({**arguments, item_argument: item})
        else:
            raw_calls = [dict(arguments)]
        if target.get("kind") == "skill":
            missing: List[str] = []
            for call in raw_calls:
                missing.extend(self._missing_required(call, input_schema))
            if not raw_calls:
                missing.extend(self._schema_required(input_schema))
            return raw_calls, list(dict.fromkeys(missing))
        normalized_calls: List[Dict[str, Any]] = []
        missing: List[str] = []
        from src.tools.registry import normalize_tool_args_for_definition

        for call in raw_calls:
            normalized = normalize_tool_args_for_definition(target.get("name") or "", call)
            normalized_arguments = normalized.get("arguments") if isinstance(normalized.get("arguments"), dict) else call
            normalized_calls.append(normalized_arguments)
            missing.extend(self._trim(item) for item in (normalized.get("missing_required") or []) if self._trim(item))
            missing.extend(self._missing_required(normalized_arguments, input_schema))
        if not raw_calls:
            missing.extend(self._schema_required(input_schema))
        return normalized_calls, list(dict.fromkeys(missing))

    def _resolve_entities(self, calls: List[Dict[str, Any]], raw_entities: Any) -> tuple[List[Dict[str, str]], List[str]]:
        entities = [item for item in (raw_entities or []) if isinstance(item, Mapping)] if isinstance(raw_entities, list) else []
        resolved: List[Dict[str, str]] = []
        unresolved: List[str] = []
        identity_cache: Dict[tuple[str, str], Optional[Dict[str, str]]] = {}
        for entity in entities:
            if self._trim(entity.get("kind")).lower() != "stock":
                continue
            argument = self._trim(entity.get("argument"))
            if not argument:
                continue
            for call in calls:
                raw_value = call.get(argument)
                values = raw_value if isinstance(raw_value, list) else [raw_value]
                normalized_values: List[Any] = []
                for value in values:
                    query = self._trim(value)
                    key = (argument, query)
                    if not query:
                        normalized_values.append(value)
                        continue
                    if key not in identity_cache:
                        identity_cache[key] = self.stock_identity_resolver.resolve(query)
                    identity = identity_cache[key]
                    if not identity:
                        unresolved.append(query)
                        normalized_values.append(value)
                        continue
                    normalized_values.append(identity["code"])
                    resolved.append({**identity, "argument": argument})
                call[argument] = normalized_values if isinstance(raw_value, list) else (normalized_values[0] if normalized_values else raw_value)
        return resolved, list(dict.fromkeys(unresolved))

    @staticmethod
    def _display_value(value: Any) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    @staticmethod
    def _schema_properties(schema: Any) -> Dict[str, Any]:
        return dict(schema.get("properties") or {}) if isinstance(schema, Mapping) and isinstance(schema.get("properties"), Mapping) else {}

    def _schema_required(self, schema: Any) -> List[str]:
        return [self._trim(item) for item in ((schema.get("required") or []) if isinstance(schema, Mapping) else []) if self._trim(item)]

    def _missing_required(self, arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> List[str]:
        return [name for name in self._schema_required(schema) if self._is_missing(arguments.get(name))]

    @staticmethod
    def _is_missing(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, dict, set)):
            return not value
        return False

    def _compact_context(self, thread_context: Dict[str, Any]) -> Dict[str, Any]:
        questions: List[str] = []
        for turn in (thread_context.get("context_window") or [])[-5:]:
            if not isinstance(turn, Mapping):
                continue
            value = self._trim(turn.get("user_input_text") or turn.get("question") or turn.get("user_text"))
            if value:
                questions.append(value[:500])
        return {"recent_user_questions": questions}

    @staticmethod
    def _with_design_labels(input_schema: Dict[str, Any], design_contract: Mapping[str, Any]) -> Dict[str, Any]:
        schema = dict(input_schema)
        properties = {
            str(name): dict(definition) if isinstance(definition, Mapping) else definition
            for name, definition in (input_schema.get("properties") or {}).items()
        }
        for field in design_contract.get("inputs") or []:
            if not isinstance(field, Mapping):
                continue
            name = str(field.get("name") or "").strip()
            label = str(field.get("label") or "").strip()
            if name in properties and isinstance(properties[name], dict) and label:
                properties[name].setdefault("title", label)
        schema["properties"] = properties
        return schema

    def _compact_file_result(self, result: Any) -> Dict[str, Any]:
        if not isinstance(result, Mapping):
            return {"ok": False}
        data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
        content = data.get("data") if isinstance(data.get("data"), Mapping) else {}
        document = content.get("document") if isinstance(content.get("document"), Mapping) else {}
        return {
            "ok": bool(result.get("ok")),
            "file_type": self._trim(data.get("file_type")),
            "header": list(content.get("header") or [])[:50],
            "rows": list(content.get("rows") or [])[:50],
            "preview_text": self._trim(document.get("preview_text"))[:10000],
            "error": self._trim(result.get("error")),
        }

    @staticmethod
    def _first_sample_input(definition: Dict[str, Any]) -> Dict[str, Any]:
        tests = definition.get("tests") if isinstance(definition.get("tests"), dict) else {}
        samples = tests.get("sample_inputs") if isinstance(tests.get("sample_inputs"), list) else []
        first = samples[0] if samples and isinstance(samples[0], dict) else {}
        return dict(first.get("arguments") or {}) if isinstance(first.get("arguments"), dict) else {}

    @staticmethod
    def _normalize_usage(usage: Any) -> Dict[str, int]:
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        if isinstance(usage, Mapping):
            source = usage
        else:
            source = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
            }
        return {key: int(source.get(key, 0) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
