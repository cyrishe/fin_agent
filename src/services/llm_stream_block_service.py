from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Mapping

from src.services.design_narrative_service import compose_design_narrative


def _trim(value: Any) -> str:
    return str(value or "").strip()


class _StructuredJsonDeltaReader:
    """Read useful values from an incomplete structured-output stream."""

    def __init__(self) -> None:
        self.buffer = ""
        self.decoder = json.JSONDecoder()

    def feed(self, chunk: str) -> None:
        self.buffer += chunk

    def objects(self, key: str) -> List[Dict[str, Any]]:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', self.buffer)
        if not match:
            return []
        cursor = match.end()
        items: List[Dict[str, Any]] = []
        while cursor < len(self.buffer):
            while cursor < len(self.buffer) and self.buffer[cursor] in " \t\r\n,":
                cursor += 1
            if cursor >= len(self.buffer) or self.buffer[cursor] == "]":
                break
            try:
                value, cursor = self.decoder.raw_decode(self.buffer, cursor)
            except json.JSONDecodeError:
                break
            if isinstance(value, Mapping):
                items.append(dict(value))
        return items

    def string(self, key: str) -> tuple[str, bool]:
        """Return the decoded prefix of a JSON string and whether it is complete."""
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"', self.buffer)
        if not match:
            return "", False
        cursor = match.end()
        chars: List[str] = []
        while cursor < len(self.buffer):
            char = self.buffer[cursor]
            if char == '"':
                return "".join(chars), True
            if char != "\\":
                chars.append(char)
                cursor += 1
                continue
            cursor += 1
            if cursor >= len(self.buffer):
                break
            escaped = self.buffer[cursor]
            if escaped == "u":
                code = self.buffer[cursor + 1:cursor + 5]
                if len(code) < 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", code):
                    break
                chars.append(chr(int(code, 16)))
                cursor += 5
                continue
            chars.append({
                '"': '"',
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }.get(escaped, escaped))
            cursor += 1
        return "".join(chars), False


class LlmStreamBlockBuilder:
    """Normalize LLM/Codex runtime events into stable frontend render blocks."""

    STAGE_TITLES = {
        "edit_plan": "修改范围",
        "requirement": "需求确认",
        "design": "方案设计",
        "flowchart": "流程图",
        "coding": "代码实现",
        "test": "样例测试",
        "view": "查看工具",
        "runtime": "处理中",
    }

    def __init__(self, *, run_id: str = "") -> None:
        self.run_id = _trim(run_id) or uuid.uuid4().hex
        self.started_at = time.time()
        self.seq = 0
        self.semantic_readers: Dict[str, _StructuredJsonDeltaReader] = {}
        self.semantic_counts: Dict[str, Dict[str, int]] = {}
        self.message_phases: Dict[str, str] = {}
        self.coding_updates: List[Dict[str, Any]] = []
        self.coding_stream_updates: Dict[str, str] = {}
        self.coding_activity_seen: set[str] = set()
        self.tool_output_buffers: Dict[str, str] = {}

    def event_to_blocks(self, event: Mapping[str, Any]) -> List[Dict[str, Any]]:
        source = _trim(event.get("source"))
        event_type = _trim(event.get("type"))
        raw_content = str(event.get("content") or "")
        content = _trim(raw_content)
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        stage = _trim(metadata.get("stage")) or self._infer_stage(event)
        if stage == "edit_coding":
            stage = "coding"
        if metadata.get("user_visible") is False:
            return []
        if not content and event_type not in {"agent_delta", "turn_started", "turn_completed", "stage_start", "stage_result", "context_ready", "final"}:
            return []

        slow_agent_source = source in {"codex", "claude", "deepseek_harness"}
        if slow_agent_source and event_type == "event" and content == "item/started":
            item = metadata.get("item") if isinstance(metadata.get("item"), Mapping) else {}
            item_id = _trim(item.get("id"))
            if item_id and _trim(item.get("type")) == "agentMessage":
                self.message_phases[item_id] = _trim(item.get("phase"))
            if stage == "coding":
                activity = self._coding_process_activity(item)
                if activity is not None:
                    blocks = [activity]
                    activity_key = _trim(activity.get("data", {}).get("activity"))
                    if activity_key in {"read", "write", "check", "sample"} and activity_key not in self.coding_activity_seen:
                        self.coding_activity_seen.add(activity_key)
                        blocks.append(self._coding_progress_update(_trim(activity.get("content"))))
                    return blocks
            return []
        if slow_agent_source and event_type in {"reasoning_summary_delta", "plan_delta"}:
            if content and not content.lstrip().startswith(("{", "[")):
                return [self._process_summary_block(
                    stage=stage,
                    event_type=event_type,
                    content=content,
                    metadata=metadata,
                )]
            return [self._progress_block(stage=stage, event_type=event_type)]
        if slow_agent_source and event_type == "item_completed":
            phase = self._message_phase(metadata)
            if stage == "coding" and phase in {"commentary", "progress"} and content:
                return [self._coding_progress_update(content, item_status="completed")]
            return [self._progress_block(stage=stage, event_type=event_type)]
        if slow_agent_source and event_type == "agent_update":
            return [self._progress_block(stage=stage, event_type=event_type)]
        if slow_agent_source and event_type == "agent_delta":
            item_id = _trim(metadata.get("item_id"))
            phase = self.message_phases.get(item_id, "") if item_id else ""
            if phase in {"commentary", "progress"}:
                return self._commentary_delta_blocks(
                    stage=stage,
                    chunk=raw_content,
                    stream_id=item_id or "commentary",
                )
            if phase and phase != "final_answer":
                return []
            return self._semantic_delta_blocks(
                stage=stage,
                chunk=raw_content,
                stream_id=item_id or "default",
            )
        if slow_agent_source and event_type in {"reasoning_delta", "event"}:
            return []
        if source == "tool" and event_type == "command_output":
            structured = self._structured_tool_output(
                stage=stage,
                stream_id=_trim(metadata.get("item_id") or metadata.get("call_id")) or "default",
                chunk=raw_content,
            )
            return [structured] if structured is not None else []
        if source == "tool" and event_type == "mcp_progress":
            return []
        if source == "model" and event_type == "final":
            return self.final_to_blocks(event, stage=stage)

        if stage == "coding" and event_type == "context_ready":
            return [self._progress_block(stage=stage, event_type=event_type)]
        if stage == "coding" and event_type == "contract_repair" and content:
            return [self._coding_progress_update(content, status="running")]
        if event_type in {"stage_start", "context_ready", "tool_call", "turn_started", "turn_completed", "stage_result", "error"}:
            blocks = [self._progress_block(
                stage=stage,
                event_type=event_type,
                failed=event_type == "error" or (event_type == "stage_result" and metadata.get("ok") is False),
            )]
            if stage == "coding" and event_type in {"stage_result", "error"} and self.coding_updates:
                failed = event_type == "error" or metadata.get("ok") is False
                blocks.append(self._coding_progress_update(
                    "本次实现未完成，请查看具体原因。" if failed else "实现与功能验证已完成。",
                    status="error" if failed else "completed",
                ))
            return blocks
        return []

    def _process_summary_block(
        self,
        *,
        stage: str,
        event_type: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        metadata = metadata if isinstance(metadata, Mapping) else {}
        stage_name = stage if stage in self.STAGE_TITLES else "runtime"
        progress_id = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            _trim(metadata.get("progress_id")),
        ).strip("_")
        status = _trim(metadata.get("status"))
        if status not in {"running", "completed", "error"}:
            status = "running"
        title = _trim(metadata.get("title")) or (
            "当前计划" if event_type == "plan_delta" else "当前分析"
        )
        return self._block(
            block_id=(
                f"{stage_name}_{progress_id}"
                if progress_id
                else f"{stage_name}_{event_type}"
            ),
            block_type="status",
            mode="replace",
            title=title,
            content=content,
            stage=stage_name,
            data={
                "role": "process",
                "stage": stage_name,
                "progress_id": progress_id,
                "status": status,
                "current_step": event_type,
                "format": "markdown",
                "summary": content,
            },
        )

    def _commentary_delta_blocks(self, *, stage: str, chunk: str, stream_id: str) -> List[Dict[str, Any]]:
        if not chunk:
            return []
        reader_key = f"commentary:{stage}:{stream_id}"
        reader = self.semantic_readers.setdefault(reader_key, _StructuredJsonDeltaReader())
        reader.feed(chunk)
        stripped = reader.buffer.lstrip()
        if stripped.startswith(("{", "[")):
            summary, complete = reader.string("implementation_summary")
        else:
            summary, complete = reader.buffer, False
        if stage != "coding" or not _trim(summary):
            return []
        return [self._coding_progress_stream(
            summary,
            stream_id=stream_id,
            complete=complete,
        )]

    def _message_phase(self, metadata: Mapping[str, Any]) -> str:
        item = metadata.get("item") if isinstance(metadata.get("item"), Mapping) else {}
        item_id = _trim(item.get("id") or metadata.get("item_id"))
        phase = _trim(item.get("phase") or metadata.get("phase"))
        return phase or (self.message_phases.get(item_id, "") if item_id else "")

    def _coding_progress_update(
        self,
        content: str,
        *,
        status: str = "running",
        item_status: str = "",
    ) -> Dict[str, Any]:
        summary = self._coding_progress_text(content)
        update_status = item_status or status
        if summary and (
            not self.coding_updates
            or _trim(self.coding_updates[-1].get("summary")) != summary
        ):
            self.coding_updates.append({
                "id": f"update_{len(self.coding_updates) + 1}",
                "summary": summary,
                "status": update_status,
                "elapsed_ms": int((time.time() - self.started_at) * 1000),
            })
        elif self.coding_updates:
            self.coding_updates[-1]["status"] = update_status
        visible_updates = [dict(item) for item in self.coding_updates[-6:]]
        if visible_updates:
            for item in visible_updates[:-1]:
                if _trim(item.get("status")) == "running":
                    item["status"] = "completed"
            visible_updates[-1]["status"] = update_status
        return self._block(
            block_id="coding_module_progress",
            block_type="workflow",
            mode="replace",
            title="实现进展",
            content=summary,
            stage="coding",
            data={
                "role": "conversation_progress",
                "status": status,
                "summary": summary,
                "items": visible_updates,
            },
        )

    def _coding_progress_stream(self, content: str, *, stream_id: str, complete: bool) -> Dict[str, Any]:
        summary = re.sub(r"\s+", " ", _trim(content))[:500]
        update_id = self.coding_stream_updates.get(stream_id)
        target = next(
            (item for item in self.coding_updates if _trim(item.get("id")) == update_id),
            None,
        )
        if target is None:
            update_id = f"update_{len(self.coding_updates) + 1}"
            target = {
                "id": update_id,
                "summary": summary,
                "status": "completed" if complete else "running",
                "elapsed_ms": int((time.time() - self.started_at) * 1000),
            }
            self.coding_updates.append(target)
            self.coding_stream_updates[stream_id] = update_id
        else:
            target["summary"] = summary
            target["status"] = "completed" if complete else "running"
        visible_updates = [dict(item) for item in self.coding_updates[-6:]]
        if visible_updates:
            for item in visible_updates[:-1]:
                if _trim(item.get("status")) == "running":
                    item["status"] = "completed"
        return self._block(
            block_id="coding_module_progress",
            block_type="workflow",
            mode="replace",
            title="实现进展",
            content=summary,
            stage="coding",
            data={
                "role": "conversation_progress",
                "status": "running",
                "summary": summary,
                "items": visible_updates,
                "streaming": not complete,
            },
        )

    @staticmethod
    def _coding_progress_text(content: str) -> str:
        text = _trim(content)
        if not text:
            return ""
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, Mapping):
                for key in ("message", "implementation_summary", "summary"):
                    candidate = re.sub(r"\s+", " ", _trim(parsed.get(key)))
                    if candidate:
                        return candidate[:500]
                return "已完成一项结构化实现更新。"
        return re.sub(r"\s+", " ", text)[:500]

    def _coding_process_activity(self, item: Mapping[str, Any]) -> Dict[str, Any] | None:
        item_type = _trim(item.get("type"))
        activity_key = ""
        title = ""
        summary = ""
        if item_type == "fileChange":
            activity_key = "write"
            title = "更新动态实现"
            summary = "正在写入本轮工具实现。"
        elif item_type == "commandExecution":
            command = _trim(item.get("command")).lower()
            actions = [
                action
                for action in item.get("commandActions") or []
                if isinstance(action, Mapping)
            ]
            action_types = {_trim(action.get("type")).lower() for action in actions}
            if "read" in action_types or "api_catalog" in command or "task_context.json" in command:
                activity_key = "read"
                title = "查阅实现资料"
                summary = "正在定位所需的数据接口与运行约定。"
            elif any(token in command for token in ("py_compile", "pytest", "unittest")):
                activity_key = "check"
                title = "运行功能检查"
                summary = "正在检查动态模块能否加载并运行。"
            elif "scratch/" in command or re.search(r"\bpython(?:3)?\b", command):
                activity_key = "sample"
                title = "运行代表性样例"
                summary = "正在执行工具样例并记录实际结果。"
            else:
                activity_key = "command"
                title = "执行实现步骤"
                summary = "正在完成当前实现操作。"
        if not summary:
            return None
        return self._block(
            block_id=f"coding_activity_{activity_key}",
            block_type="status",
            mode="replace",
            title=title,
            content=summary,
            stage="coding",
            data={
                "role": "process",
                "stage": "coding",
                "activity": activity_key,
                "status": "running",
                "summary": summary,
            },
        )

    def _structured_tool_output(self, *, stage: str, stream_id: str, chunk: str) -> Dict[str, Any] | None:
        if not chunk:
            return None
        buffer_key = f"{stage}:{stream_id}"
        buffer = f"{self.tool_output_buffers.get(buffer_key, '')}{chunk}"
        self.tool_output_buffers[buffer_key] = buffer[-50_000:]
        candidate = buffer.strip()
        if candidate.startswith("```") and candidate.endswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate)
        try:
            value = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            return None
        self.tool_output_buffers[buffer_key] = ""
        if not isinstance(value, (Mapping, list)):
            return None
        return self._block(
            block_id=f"{stage}_structured_tool_output",
            block_type="code",
            mode="replace",
            title="结构化执行结果",
            stage=stage,
            data={
                "role": "process",
                "stage": stage,
                "status": "completed",
                "summary": "已返回结构化执行结果。",
                "format": "json",
                "value": value,
            },
        )

    def _semantic_delta_blocks(self, *, stage: str, chunk: str, stream_id: str) -> List[Dict[str, Any]]:
        """Turn complete semantic units into UI blocks without exposing raw JSON."""
        if stage not in {"requirement", "design", "coding"} or not chunk:
            return []
        reader_key = f"{stage}:{stream_id}"
        reader = self.semantic_readers.setdefault(reader_key, _StructuredJsonDeltaReader())
        reader.feed(chunk)
        counts = self.semantic_counts.setdefault(reader_key, {})
        if stage == "requirement":
            return self._narrative_delta_blocks(
                reader,
                counts,
                key="requirement_brief",
                block_id="requirement_final_summary",
                title="需求理解",
                stage=stage,
            )
        if stage == "design":
            narrative = self._narrative_delta_blocks(
                reader,
                counts,
                key="document",
                block_id="design_artifact",
                title="正在形成设计方案",
                stage=stage,
            )
            if narrative:
                return narrative
            return self._design_delta_blocks(reader, counts)
        return self._coding_delta_blocks(reader, counts, stream_id=stream_id)

    def _narrative_delta_blocks(
        self,
        reader: _StructuredJsonDeltaReader,
        counts: Dict[str, int],
        *,
        key: str,
        block_id: str,
        title: str,
        stage: str,
    ) -> List[Dict[str, Any]]:
        text, complete = reader.string(key)
        if not _trim(text) or len(text) == counts.get(key, 0):
            return []
        counts[key] = len(text)
        return [self._block(
            block_id=block_id,
            block_type="narrative",
            mode="replace",
            title=title,
            content=text,
            stage=stage,
            data={
                "role": "conversation_stream",
                "format": "markdown",
                "provisional": not complete,
            },
        )]

    def _design_delta_blocks(
        self,
        reader: _StructuredJsonDeltaReader,
        counts: Dict[str, int],
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        questions = self._normalize_design_questions(reader.objects("questions"))
        if len(questions) > counts.get("questions", 0):
            counts["questions"] = len(questions)
            blocks.append(self._block(
                block_id="design_questions",
                block_type="interaction",
                mode="replace",
                title="正在形成待确认项",
                content="已完成的问题会先显示；全部设计完成后再统一提交。",
                stage="design",
                data={
                    "interaction_id": "custom_tool.requirement_clarification.preview",
                    "intent": "preview",
                    "submission_mode": "none",
                    "prompt": "已形成的待确认项",
                    "questions": questions,
                    "actions": [],
                    "provisional": True,
                },
            ))

        inputs = reader.objects("inputs")
        outputs = reader.objects("outputs")
        modules = reader.objects("modules")
        steps = reader.objects("steps")
        links = reader.objects("links")
        rules = reader.objects("rules")
        artifact_version = sum(map(len, (inputs, outputs, modules, steps, links, rules)))
        if artifact_version > counts.get("artifact", 0):
            counts["artifact"] = artifact_version
            blocks.append(self._block(
                block_id="design_artifact",
                block_type="artifact",
                mode="replace",
                title="正在形成工具设计",
                stage="design",
                data={
                    "artifact_type": "finance.tool_spec",
                    "lifecycle": "generating",
                    "summary": "完整的设计对象会边生成边补充，完成后进入统一确认。",
                    "items": [
                        {"label": "输入", "value": f"{len(inputs)} 个字段"},
                        {"label": "输出", "value": f"{len(outputs)} 个字段"},
                        {"label": "模块", "value": f"{len(modules)} 个"},
                        {"label": "规则", "value": f"{len(rules)} 条"},
                    ],
                    "details": {
                        "understanding": {},
                        "inputs": inputs,
                        "outputs": outputs,
                        "modules": modules,
                        "rules": rules,
                        "flow": {"steps": steps, "links": links},
                    },
                    "provisional": True,
                },
            ))
        return blocks

    def _coding_delta_blocks(
        self,
        reader: _StructuredJsonDeltaReader,
        counts: Dict[str, int],
        *,
        stream_id: str,
    ) -> List[Dict[str, Any]]:
        summary, complete = reader.string("implementation_summary")
        if _trim(summary) and len(summary) != counts.get("implementation_summary", 0):
            counts["implementation_summary"] = len(summary)
            return [self._coding_progress_stream(
                summary,
                stream_id=f"final:{stream_id}",
                complete=complete,
            )]
        modules = reader.objects("modules")
        if len(modules) <= counts.get("modules", 0):
            return []
        counts["modules"] = len(modules)
        return [self._block(
            block_id="custom_tool_draft_summary",
            block_type="code",
            mode="replace",
            title="正在生成核心模块",
            stage="coding",
            data={
                "files": [
                    {
                        "id": _trim(item.get("module_id")) or f"module_{index + 1}",
                        "name": _trim(item.get("module_id")) or f"module_{index + 1}",
                        "language": _trim(item.get("language")) or "python",
                        "content": str(item.get("source_code") or ""),
                    }
                    for index, item in enumerate(modules)
                ],
                "runtime": {"status": "generating"},
                "provisional": True,
            },
        )]

    def _progress_block(self, *, stage: str, event_type: str, failed: bool = False) -> Dict[str, Any]:
        if stage == "direct":  # Render older persisted events with the current stage name.
            stage = "view"
        stage_name = stage if stage in self.STAGE_TITLES else "runtime"
        failed = failed or event_type == "error"
        complete = event_type == "stage_result" and not failed
        status = "error" if failed else ("completed" if complete else "running")
        summary = self._activity_text(stage_name, event_type, failed=failed)
        return self._block(
            block_id=f"{stage_name}_live_progress",
            block_type="status",
            mode="replace",
            title=self.STAGE_TITLES[stage_name],
            content=summary,
            stage=stage_name,
            data={
                "role": "live_progress",
                "stage": stage_name,
                "status": status,
                "current_step": event_type,
                "summary": summary,
            },
        )

    @staticmethod
    def _activity_text(stage: str, event_type: str, *, failed: bool) -> str:
        if failed:
            return "处理失败，请查看错误信息。"
        completed = {
            "edit_plan": "修改范围已确定。",
            "requirement": "需求理解已形成。",
            "design": "设计方案已形成。",
            "flowchart": "流程图已形成。",
            "coding": "代码实现已完成。",
            "test": "样例运行已完成。",
            "view": "已有内容已读取。",
            "runtime": "处理已完成。",
        }
        if event_type == "stage_result":
            return completed[stage]
        activities = {
            "edit_plan": "正在对照当前版本确定最小修改范围…",
            "requirement": "正在理解你的需求…",
            "design": "正在整理实现方案…",
            "flowchart": "正在绘制流程图…",
            "coding": "正在实现并验证代码…",
            "test": "正在运行真实样例…",
            "view": "正在读取已有内容…",
            "runtime": "正在处理…",
        }
        if event_type == "context_ready":
            return "已准备好本次需要的上下文。"
        if event_type in {"turn_completed", "item_completed", "agent_update"}:
            return "正在整理本轮结果…"
        return activities[stage]

    def final_to_blocks(self, final: Mapping[str, Any], *, stage: str = "") -> List[Dict[str, Any]]:
        stage_name = _trim(stage) or self._infer_stage(final)
        status = _trim(final.get("status"))
        explicit_blocks = self._explicit_render_blocks(final, stage=stage_name)
        if explicit_blocks:
            return explicit_blocks
        blocks: List[Dict[str, Any]] = []
        if stage_name == "coding":
            blocks.extend(self._coding_final_blocks(final, status=status, stage=stage_name))
        else:
            blocks.extend(self._design_final_blocks(final, status=status, stage=stage_name))
        return blocks

    def _explicit_render_blocks(self, final: Mapping[str, Any], *, stage: str) -> List[Dict[str, Any]]:
        raw_blocks = final.get("render_blocks")
        if not isinstance(raw_blocks, list):
            return []
        blocks: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_blocks):
            if not isinstance(item, Mapping):
                continue
            block_type = _trim(item.get("block_type") or item.get("type"))
            if block_type not in {
                "markdown", "table", "bar_chart", "line_chart", "flowchart", "code",
                "narrative", "workflow", "artifact", "assessment",
            }:
                continue
            data = item.get("data") if isinstance(item.get("data"), Mapping) else {}
            blocks.append(self._block(
                block_id=_trim(item.get("block_id")) or f"{stage}_render_{idx + 1}",
                block_type=block_type,
                mode=_trim(item.get("mode")) or "replace",
                title=_trim(item.get("title")),
                content=_trim(item.get("content")),
                stage=stage,
                data=data,
                actions=item.get("actions") if isinstance(item.get("actions"), list) else [],
            ))
        return blocks

    def _design_final_blocks(self, final: Mapping[str, Any], *, status: str, stage: str) -> List[Dict[str, Any]]:
        design = final.get("design") if isinstance(final.get("design"), Mapping) else {}
        understanding = final.get("understanding") if isinstance(final.get("understanding"), Mapping) else {}
        existing_analysis = final.get("existing_analysis") if isinstance(final.get("existing_analysis"), Mapping) else {}
        design_context = final.get("design_context") if isinstance(final.get("design_context"), Mapping) else {}
        artifact_context = final.get("design_artifact") if isinstance(final.get("design_artifact"), Mapping) else {}
        artifact_id = _trim(artifact_context.get("design_artifact_id"))
        artifact_revision = int(artifact_context.get("design_revision") or 0)
        requirement_artifact_context = (
            final.get("requirement_artifact")
            if isinstance(final.get("requirement_artifact"), Mapping)
            else {}
        )
        requirement_artifact_id = _trim(
            requirement_artifact_context.get("requirement_artifact_id")
        )
        requirement_revision = int(
            requirement_artifact_context.get("requirement_revision") or 0
        )
        clarification_artifact_id = requirement_artifact_id or artifact_id
        clarification_revision = requirement_revision or artifact_revision
        message = _trim(final.get("message"))
        notice = [_trim(item) for item in final.get("notice") or [] if _trim(item)]
        questions = self._normalize_design_questions(final.get("questions"))
        finance_tool_profile = (
            dict(design.get("finance_tool_profile") or {})
            if isinstance(design.get("finance_tool_profile"), Mapping)
            else {}
        )
        planned_action = (
            _trim(finance_tool_profile.get("family")).lower() == "action"
        )
        if planned_action:
            # This is a system-owned execution boundary, not a permission
            # requested from or granted by the model.
            finance_tool_profile["execution_policy"] = "planned_non_executable"
        # A design body without its derived flow is still a draft. Keep this
        # check at the rendering boundary as well as in the workflow service so
        # a stale or alternate caller cannot surface a confirm action early.
        reviewable = (
            status in {"review", "design_ready"}
            and bool(_trim(design.get("mermaid")))
        )
        goal = _trim(understanding.get("goal"))
        requirement_brief = _trim(understanding.get("requirement_brief"))
        summary = (
            requirement_brief
            if stage == "requirement" and requirement_brief
            else message or compose_design_narrative(understanding, questions, design)
        )
        blocks: List[Dict[str, Any]] = []
        if not (stage == "design" and design and reviewable):
            blocks.append(self._block(
                block_id=f"{stage}_final_summary",
                block_type="narrative",
                mode="replace",
                title="需求理解" if stage == "requirement" else "",
                content=summary,
                stage=stage,
            ))

        if stage == "requirement":
            if questions and notice:
                prompt = "我会按下面的理解继续；其中需要你决定的项目已默认选择第一项。"
            elif questions:
                prompt = "请确认下面的关键选择；每项已默认选择第一项。"
            elif notice:
                prompt = "下面是我准备采用的处理方式。确认后，我会继续形成设计方案。"
            else:
                prompt = "如果上述理解符合你的预期，确认后我会继续形成设计方案。"
            blocks.append(self._block(
                block_id="requirement_review",
                block_type="interaction",
                mode="replace",
                title="确认需求",
                content=prompt,
                stage=stage,
                data={
                    "interaction_id": "custom_tool.requirement_clarification",
                    "intent": "provide_input",
                    "submission_mode": "conversation",
                    "prompt": prompt,
                    "subject_ref": clarification_artifact_id,
                    "subject_revision": clarification_revision,
                    "notice": notice,
                    "questions": questions,
                    "actions": [{
                        "action_id": "custom_tool.submit_clarification",
                        "label": "确认需求",
                        "intent": "submit",
                        "style": "primary",
                        "expected_revision": clarification_revision,
                    }],
                },
            ))

        if design:
            blocks.append(self._block(
                block_id=f"{stage}_artifact",
                block_type="artifact",
                mode="replace",
                title=_trim(design.get("display_name")) or "设计方案",
                stage=stage,
                data={
                    "artifact_id": artifact_id,
                    "artifact_type": "finance.tool_spec",
                    "content_schema_version": "finance.tool_spec.v1",
                    "lifecycle": "reviewable" if reviewable else "draft",
                    "revision": artifact_revision,
                    "summary": _trim(design.get("description")) or goal,
                    "design_context": dict(design_context),
                    "items": [
                        {"label": "工具标识", "value": _trim(design.get("tool_name")) or "-"},
                        {"label": "输入", "value": f"{len(design.get('inputs') or [])} 个字段"},
                        {"label": "输出", "value": f"{len(design.get('outputs') or [])} 个字段"},
                        {"label": "规则", "value": f"{len(design.get('rules') or design.get('logic') or [])} 条"},
                    ],
                    "details": {
                        "understanding": dict(understanding),
                        "document": _trim(design.get("document")),
                        "plan": _trim(design.get("plan")),
                        "inputs": [dict(item) for item in design.get("inputs") or [] if isinstance(item, Mapping)],
                        "outputs": [dict(item) for item in design.get("outputs") or [] if isinstance(item, Mapping)],
                        "process": [dict(item) for item in design.get("process") or [] if isinstance(item, Mapping)],
                        "modules": [dict(item) for item in design.get("modules") or [] if isinstance(item, Mapping)],
                        "rules": [dict(item) for item in design.get("rules") or [] if isinstance(item, Mapping)],
                        "logic": [str(item) for item in design.get("logic") or [] if _trim(item)],
                        "mermaid": _trim(design.get("mermaid")),
                        "data_requirements": [dict(item) for item in design.get("data_requirements") or [] if isinstance(item, Mapping)],
                        "existing_analysis": dict(existing_analysis),
                        "finance_tool_profile": finance_tool_profile,
                    },
                },
            ))

        if questions and stage != "requirement":
            blocks.append(self._block(
                block_id=f"{stage}_questions",
                block_type="interaction",
                mode="replace",
                title="有几个关键点想和你确认",
                content="这些选择会影响工具最终的计算或返回结果。",
                stage=stage,
                data={
                    "interaction_id": "custom_tool.requirement_clarification",
                    "intent": "provide_input",
                    "submission_mode": "conversation",
                    "prompt": "请选择常用答案，或直接在对话中补充。",
                    "subject_ref": clarification_artifact_id,
                    "subject_revision": clarification_revision,
                    "questions": questions,
                    "actions": [{
                        "action_id": "custom_tool.submit_clarification",
                        "label": "确定",
                        "intent": "submit",
                        "style": "primary",
                        "expected_revision": clarification_revision,
                    }],
                },
            ))
        if reviewable and not planned_action:
            blocks.append(self._block(
                block_id=f"{stage}_design_review",
                block_type="interaction",
                mode="replace",
                title="确认设计",
                content="确认后进入实现和样例验证；需要调整时，直接在对话中说明修改点。",
                stage=stage,
                data={
                    "interaction_id": "custom_tool.design_review",
                    "intent": "confirm",
                    "submission_mode": "action",
                    "prompt": "是否按当前规格继续？",
                    "subject_ref": artifact_id,
                    "subject_revision": artifact_revision,
                    "review": {
                        "assumptions": [str(item) for item in understanding.get("assumptions") or [] if _trim(item)],
                        "constraints": [str(item) for item in understanding.get("constraints") or [] if _trim(item)],
                    },
                    "actions": [
                        {
                            "action_id": "custom_tool.confirm_design",
                            "label": "确认并继续",
                            "intent": "accept",
                            "style": "primary",
                            "expected_revision": artifact_revision,
                        },
                        {
                            "action_id": "custom_tool.revise_design",
                            "label": "修改",
                            "intent": "edit",
                            "style": "default",
                            "expected_revision": artifact_revision,
                        },
                    ],
                },
            ))
        return blocks

    @staticmethod
    def _normalize_design_questions(raw_questions: Any) -> List[Dict[str, Any]]:
        questions: List[Dict[str, Any]] = []
        for index, item in enumerate(raw_questions if isinstance(raw_questions, list) else []):
            if isinstance(item, Mapping):
                question = _trim(item.get("question"))
                if not question:
                    continue
                candidates = [
                    _trim(candidate)
                    for candidate in item.get("candidate") or []
                    if _trim(candidate)
                ]
                questions.append({
                    "question": question,
                    "candidate": candidates,
                })
                continue
            text = _trim(item)
            if text:
                questions.append({
                    "question": text,
                    "candidate": [],
                })
        return questions

    def _coding_final_blocks(self, final: Mapping[str, Any], *, status: str, stage: str) -> List[Dict[str, Any]]:
        implementation = final.get("implementation") if isinstance(final.get("implementation"), Mapping) else {}
        modules = [item for item in implementation.get("modules") or [] if isinstance(item, Mapping)]
        implementation_summary = (
            _trim(final.get("implementation_summary"))
            or _trim(implementation.get("summary"))
        )
        verification = _trim(final.get("verification"))
        blocks = [self._block(
            block_id=f"{stage}_final_summary",
            block_type="narrative",
            mode="replace",
            title="实现结果",
            content="\n".join(
                item for item in [
                    _trim(final.get("message")),
                    implementation_summary,
                ] if item
            ),
            stage=stage,
        )]
        if verification:
            blocks.append(self._block(
                block_id=f"{stage}_alignment",
                block_type="narrative",
                mode="replace",
                title="运行验证与需求对齐",
                content=verification,
                stage=stage,
            ))
        if modules:
            blocks.append(self._block(
                block_id=f"{stage}_artifact",
                block_type="artifact",
                mode="replace",
                title="可运行版本",
                stage=stage,
                data={
                    "artifact_type": "finance.custom_tool_implementation",
                    "lifecycle": "draft",
                    "version": "0.1",
                    "summary": implementation_summary or f"已生成 {len(modules)} 个动态实现模块。",
                    "items": [
                        {"label": "实现模块", "value": f"{len(modules)} 个"},
                        {"label": "加载方式", "value": "数据库动态加载"},
                    ],
                    "details": {
                        "modules": [
                            {
                                "module_id": _trim(item.get("module_id")),
                                "role": _trim(item.get("role")),
                                "entrypoint": _trim(item.get("entrypoint")),
                                "lines": len(_trim(item.get("source_code")).splitlines()),
                            }
                            for item in modules
                        ]
                    },
                },
            ))
        tests = [item for item in final.get("tests") or [] if isinstance(item, Mapping)]
        risks = [str(item) for item in final.get("risks") or [] if _trim(item)]
        if tests or risks:
            passed = sum(
                1 for item in tests
                if _trim(item.get("status") or item.get("result")) in {"passed", "pass", "succeeded"}
            )
            overall = "pass" if tests and passed == len(tests) and not risks else ("warn" if status == "code_ready" else "fail")
            blocks.append(self._block(
                block_id=f"{stage}_assessment",
                block_type="assessment",
                mode="replace",
                title="验证结果",
                stage=stage,
                data={
                    "overall": overall,
                    "summary": f"{passed} / {len(tests)} 项样例通过" if tests else "存在需要检查的风险。",
                    "issues": risks,
                    "details": {
                        "tests": [
                            {
                                "name": _trim(item.get("name") or item.get("test_id")),
                                "status": _trim(item.get("status") or item.get("result")),
                                "summary": _trim(item.get("summary") or item.get("purpose")),
                            }
                            for item in tests
                        ]
                    },
                },
            ))
        return blocks

    def _block(
        self,
        *,
        block_id: str,
        block_type: str,
        mode: str,
        title: str = "",
        content: str = "",
        stage: str = "",
        data: Mapping[str, Any] | None = None,
        actions: Iterable[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        self.seq += 1
        return {
            "event": "block",
            "run_id": self.run_id,
            "seq": self.seq,
            "stage": _trim(stage) or "runtime",
            "block_id": block_id,
            "block_type": block_type,
            "mode": mode,
            "title": title,
            "content": content,
            "data": dict(data or {}),
            "actions": [dict(item) for item in (actions or [])],
            "elapsed_ms": int((time.time() - self.started_at) * 1000),
        }

    def make_block(
        self,
        *,
        block_id: str,
        block_type: str,
        mode: str = "replace",
        title: str = "",
        content: str = "",
        stage: str = "",
        data: Mapping[str, Any] | None = None,
        actions: Iterable[Mapping[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        return self._block(
            block_id=block_id,
            block_type=block_type,
            mode=mode,
            title=title,
            content=content,
            stage=stage,
            data=data,
            actions=actions,
        )

    @staticmethod
    def _infer_stage(event: Mapping[str, Any]) -> str:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        stage = _trim(metadata.get("stage"))
        if stage:
            return stage
        return "design"
