from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Iterable, List, Mapping


def _trim(value: Any) -> str:
    return str(value or "").strip()


class LlmStreamBlockBuilder:
    """Normalize LLM/Codex runtime events into stable frontend render blocks."""

    def __init__(self, *, run_id: str = "") -> None:
        self.run_id = _trim(run_id) or uuid.uuid4().hex
        self.started_at = time.time()
        self.seq = 0

    def event_to_blocks(self, event: Mapping[str, Any]) -> List[Dict[str, Any]]:
        source = _trim(event.get("source"))
        event_type = _trim(event.get("type"))
        content = _trim(event.get("content"))
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        stage = _trim(metadata.get("stage")) or self._infer_stage(event)
        if not content and event_type not in {"turn_started", "turn_completed", "stage_start", "stage_result", "context_ready", "final"}:
            return []

        if source == "codex" and event_type in {"reasoning_summary_delta", "reasoning_delta", "plan_delta"}:
            return [self._block(
                block_id=f"{stage}_thinking",
                block_type="markdown",
                mode="append",
                title="思考过程",
                content=content,
                stage=stage,
            )]
        if source == "codex" and event_type in {"agent_delta", "agent_update", "item_completed"}:
            return [self._block(
                block_id=f"{stage}_assistant",
                block_type="markdown",
                mode="append",
                title="模型输出",
                content=content,
                stage=stage,
            )]
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

        status_text = self._status_text(event_type, content)
        return [self._block(
            block_id=f"{stage}_status",
            block_type="status",
            mode="replace",
            title="执行状态",
            content=status_text,
            stage=stage,
            data={"source": source, "event_type": event_type, "metadata": dict(metadata)},
        )] if status_text else []

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
            data_json = _trim(item.get("data_json"))
            if data_json and not data:
                try:
                    parsed = json.loads(data_json)
                    if isinstance(parsed, Mapping):
                        data = parsed
                except Exception:
                    data = {}
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
        artifact_context = final.get("design_artifact") if isinstance(final.get("design_artifact"), Mapping) else {}
        artifact_id = _trim(artifact_context.get("design_artifact_id"))
        artifact_revision = int(artifact_context.get("design_revision") or 0)
        message = _trim(final.get("message"))
        questions = self._normalize_design_questions(final.get("questions"))
        reviewable = status in {"review", "design_ready"}
        goal = _trim(understanding.get("goal"))
        expected_result = _trim(understanding.get("expected_result"))
        summary = message or goal or ("工具规格已经形成。" if reviewable else "还需要补充信息。")
        if expected_result and expected_result != summary:
            summary = f"{summary}\n预期结果：{expected_result}"
        blocks = [self._block(
            block_id=f"{stage}_final_summary",
            block_type="narrative",
            mode="replace",
            title="设计结果" if reviewable else "需要补充信息",
            content=summary,
            stage=stage,
        )]

        if design:
            blocks.append(self._block(
                block_id=f"{stage}_artifact",
                block_type="artifact",
                mode="replace",
                title=_trim(design.get("display_name")) or "工具规格",
                stage=stage,
                data={
                    "artifact_id": artifact_id,
                    "artifact_type": "finance.tool_spec",
                    "content_schema_version": "finance.tool_spec.v1",
                    "lifecycle": "reviewable" if reviewable else "draft",
                    "revision": artifact_revision,
                    "summary": _trim(design.get("description")) or goal,
                    "items": [
                        {"label": "工具标识", "value": _trim(design.get("tool_name")) or "-"},
                        {"label": "输入", "value": f"{len(design.get('inputs') or [])} 个字段"},
                        {"label": "输出", "value": f"{len(design.get('outputs') or [])} 个字段"},
                        {"label": "规则", "value": f"{len(design.get('rules') or design.get('logic') or [])} 条"},
                    ],
                    "details": {
                        "understanding": dict(understanding),
                        "inputs": [dict(item) for item in design.get("inputs") or [] if isinstance(item, Mapping)],
                        "outputs": [dict(item) for item in design.get("outputs") or [] if isinstance(item, Mapping)],
                        "modules": [dict(item) for item in design.get("modules") or [] if isinstance(item, Mapping)],
                        "rules": [dict(item) for item in design.get("rules") or [] if isinstance(item, Mapping)],
                        "logic": [str(item) for item in design.get("logic") or [] if _trim(item)],
                        "flow": dict(design.get("flow")) if isinstance(design.get("flow"), Mapping) else {"steps": [], "links": []},
                        "data_requirements": [dict(item) for item in design.get("data_requirements") or [] if isinstance(item, Mapping)],
                        "exceptions": [dict(item) for item in design.get("exceptions") or [] if isinstance(item, Mapping)],
                        "acceptance": [dict(item) for item in design.get("acceptance") or [] if isinstance(item, Mapping)],
                        "existing_analysis": dict(existing_analysis),
                    },
                },
            ))

        if questions:
            blocks.append(self._block(
                block_id=f"{stage}_questions",
                block_type="interaction",
                mode="replace",
                title="需要补充",
                content="请在对话中补充以下信息。",
                stage=stage,
                data={
                    "interaction_id": "custom_tool.requirement_clarification",
                    "intent": "provide_input",
                    "submission_mode": "conversation",
                    "prompt": "请选择常用答案，或直接在对话中补充。",
                    "questions": questions,
                    "actions": [],
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
                        "data_requirements": len(design.get("data_requirements") or []),
                        "acceptance": len(design.get("acceptance") or []),
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
                questions.append({
                    "id": _trim(item.get("id")) or f"Q{index + 1}",
                    "question": question,
                    "reason": _trim(item.get("reason")),
                    "answer_type": _trim(item.get("answer_type")) or "text",
                    "required": item.get("required") is True,
                    "options": [dict(option) for option in item.get("options") or [] if isinstance(option, Mapping)],
                    "allow_custom": item.get("allow_custom") is not False,
                })
                continue
            text = _trim(item)
            if text:
                questions.append({
                    "id": f"Q{index + 1}",
                    "question": text,
                    "reason": "",
                    "answer_type": "text",
                    "required": True,
                    "options": [],
                    "allow_custom": True,
                })
        return questions[:5]

    def _coding_final_blocks(self, final: Mapping[str, Any], *, status: str, stage: str) -> List[Dict[str, Any]]:
        blocks = [self._block(
            block_id=f"{stage}_final_summary",
            block_type="narrative",
            mode="replace",
            title="实现结果" if status == "code_ready" else "需要回到设计",
            content="\n".join(
                item for item in [
                    _trim(final.get("message")),
                    _trim(final.get("code_summary")),
                ] if item
            ),
            stage=stage,
        )]
        files = [item for item in final.get("files") or [] if isinstance(item, Mapping)]
        if files:
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
                    "summary": _trim(final.get("code_summary")) or f"已生成 {len(files)} 个实现文件。",
                    "items": [
                        {"label": "实现文件", "value": f"{len(files)} 个"},
                    ],
                    "details": {
                        "files": [
                            {
                                "path": _trim(item.get("path")),
                                "role": _trim(item.get("role")),
                                "lines": len(_trim(item.get("content")).splitlines()),
                            }
                            for item in files
                        ]
                    },
                },
            ))
        tests = [item for item in final.get("tests") or [] if isinstance(item, Mapping)]
        risks = [str(item) for item in final.get("risks") or [] if _trim(item)]
        if tests or risks:
            passed = sum(1 for item in tests if _trim(item.get("status")) in {"passed", "pass", "succeeded"})
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
                                "name": _trim(item.get("name")),
                                "status": _trim(item.get("status")),
                                "summary": _trim(item.get("summary")),
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
        status = _trim(event.get("status"))
        if status in {"code_ready", "need_design_fix"}:
            return "coding"
        return "design"

    @staticmethod
    def _status_text(event_type: str, content: str) -> str:
        labels = {
            "stage_start": "阶段开始",
            "context_ready": "上下文资料已准备",
            "tool_call": "调用 Codex",
            "turn_started": "模型回合开始",
            "turn_completed": "模型回合完成",
            "tool_result": "工具调用完成",
            "stage_result": "阶段结果已解析",
        }
        label = labels.get(event_type, content)
        if not label:
            return ""
        if content and content != label:
            return f"{label}: {content}"
        return label
