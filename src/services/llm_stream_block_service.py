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
    """Read complete objects from selected arrays in an incomplete JSON stream."""

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


class LlmStreamBlockBuilder:
    """Normalize LLM/Codex runtime events into stable frontend render blocks."""

    STAGE_TITLES = {
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
        self.coding_plan_items: List[Dict[str, Any]] = []
        self.coding_milestones: set[str] = set()

    def event_to_blocks(self, event: Mapping[str, Any]) -> List[Dict[str, Any]]:
        source = _trim(event.get("source"))
        event_type = _trim(event.get("type"))
        raw_content = str(event.get("content") or "")
        content = _trim(raw_content)
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        stage = _trim(metadata.get("stage")) or self._infer_stage(event)
        if not content and event_type not in {"agent_delta", "turn_started", "turn_completed", "stage_start", "stage_result", "context_ready", "final"}:
            return []

        slow_agent_source = source in {"codex", "claude"}
        if slow_agent_source and event_type == "event" and content == "item/started":
            item = metadata.get("item") if isinstance(metadata.get("item"), Mapping) else {}
            item_id = _trim(item.get("id"))
            if item_id and _trim(item.get("type")) == "agentMessage":
                self.message_phases[item_id] = _trim(item.get("phase"))
            if stage == "coding":
                milestone = self._coding_item_milestone(item)
                if milestone is not None:
                    key, summary = milestone
                    if key not in self.coding_milestones:
                        self.coding_milestones.add(key)
                        return [self._coding_progress_update(summary)]
            return []
        if slow_agent_source and event_type in {"reasoning_summary_delta", "plan_delta"}:
            return [self._progress_block(stage=stage, event_type=event_type)]
        if slow_agent_source and event_type == "item_completed":
            phase = self._message_phase(metadata)
            if stage == "coding" and phase in {"commentary", "progress"} and content:
                return [self._coding_progress_update(content)]
            return [self._progress_block(stage=stage, event_type=event_type)]
        if slow_agent_source and event_type == "agent_update":
            return [self._progress_block(stage=stage, event_type=event_type)]
        if slow_agent_source and event_type == "agent_delta":
            item_id = _trim(metadata.get("item_id"))
            phase = self.message_phases.get(item_id, "") if item_id else ""
            if phase and phase != "final_answer":
                return []
            return self._semantic_delta_blocks(
                stage=stage,
                chunk=raw_content,
                stream_id=item_id or "default",
            )
        if slow_agent_source and event_type in {"reasoning_delta", "event"}:
            return []
        if source == "tool" and event_type in {"command_output", "mcp_progress"}:
            return [self._block(
                block_id=f"{stage}_tool_output",
                block_type="code",
                mode="append",
                title="代码执行输出",
                content=content,
                stage=stage,
            )]
        if source == "model" and event_type == "final":
            return self.final_to_blocks(event, stage=stage)

        if stage == "coding" and event_type == "context_ready":
            raw_items = metadata.get("module_plan") if isinstance(metadata.get("module_plan"), list) else []
            self.coding_plan_items = [dict(item) for item in raw_items if isinstance(item, Mapping)]
            blocks = [self._progress_block(stage=stage, event_type=event_type)]
            if self.coding_plan_items:
                api_sources = [_trim(item) for item in metadata.get("api_sources") or [] if _trim(item)]
                summary = "已准备模块化实现工作区"
                if api_sources:
                    summary += f"，并定位数据接口：{', '.join(api_sources)}"
                blocks.append(self._coding_progress_update(summary))
            return blocks
        if event_type in {"stage_start", "context_ready", "tool_call", "turn_started", "turn_completed", "stage_result", "error"}:
            return [self._progress_block(
                stage=stage,
                event_type=event_type,
                failed=event_type == "error" or (event_type == "stage_result" and metadata.get("ok") is False),
            )]
        return []

    def _message_phase(self, metadata: Mapping[str, Any]) -> str:
        item = metadata.get("item") if isinstance(metadata.get("item"), Mapping) else {}
        item_id = _trim(item.get("id") or metadata.get("item_id"))
        phase = _trim(item.get("phase") or metadata.get("phase"))
        return phase or (self.message_phases.get(item_id, "") if item_id else "")

    def _coding_progress_update(self, content: str) -> Dict[str, Any]:
        summary = re.sub(r"\s+", " ", _trim(content))[:500]
        return self._block(
            block_id="coding_module_progress",
            block_type="workflow",
            mode="replace",
            title="实现进展",
            content=summary,
            stage="coding",
            data={
                "role": "process",
                "summary": summary,
                "items": self.coding_plan_items,
            },
        )

    @staticmethod
    def _coding_item_milestone(item: Mapping[str, Any]) -> tuple[str, str] | None:
        item_type = _trim(item.get("type"))
        if item_type == "fileChange":
            return "module_written", "核心模块已经写入，正在进行聚焦验证。"
        if item_type != "commandExecution":
            return None
        command = _trim(item.get("command")).lower()
        if any(token in command for token in ("py_compile", "pytest", "scratch/", "unittest")):
            return "module_test", "正在运行模块编译和聚焦样例测试。"
        if "api_catalog" in command or "task_context.json" in command:
            return "api_context", "正在核对设计所需的数据接口和字段。"
        return None

    def _semantic_delta_blocks(self, *, stage: str, chunk: str, stream_id: str) -> List[Dict[str, Any]]:
        """Turn complete semantic units into UI blocks without exposing raw JSON."""
        if stage not in {"design", "coding"} or not chunk:
            return []
        reader_key = f"{stage}:{stream_id}"
        reader = self.semantic_readers.setdefault(reader_key, _StructuredJsonDeltaReader())
        reader.feed(chunk)
        counts = self.semantic_counts.setdefault(reader_key, {})
        if stage == "design":
            return self._design_delta_blocks(reader, counts)
        return self._coding_delta_blocks(reader, counts)

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
    ) -> List[Dict[str, Any]]:
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
        message = _trim(final.get("message"))
        notice = [_trim(item) for item in final.get("notice") or [] if _trim(item)]
        questions = self._normalize_design_questions(final.get("questions"))
        reviewable = status in {"review", "design_ready"}
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
                    "subject_revision": artifact_revision,
                    "notice": notice,
                    "questions": questions,
                    "actions": [{
                        "action_id": "custom_tool.submit_clarification",
                        "label": "确认需求",
                        "intent": "submit",
                        "style": "primary",
                        "expected_revision": artifact_revision,
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
                    "subject_revision": artifact_revision,
                    "questions": questions,
                    "actions": [{
                        "action_id": "custom_tool.submit_clarification",
                        "label": "确定",
                        "intent": "submit",
                        "style": "primary",
                        "expected_revision": artifact_revision,
                    }],
                },
            ))
        if reviewable:
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
