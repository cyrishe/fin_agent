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
        if not content and event_type not in {"turn_started", "turn_completed", "stage_start", "stage_result", "context_ready"}:
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
            if block_type not in {"markdown", "table", "bar_chart", "line_chart", "flowchart", "code", "action"}:
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
        message = _trim(final.get("message"))
        questions = [str(item) for item in final.get("questions") or [] if _trim(item)]
        title = "设计结果" if status == "design_ready" else "需要补充信息"
        summary_lines = []
        if message:
            summary_lines.append(message)
        if design:
            summary_lines.extend([
                "",
                f"- tool_name: `{_trim(design.get('tool_name')) or '-'}`",
                f"- display_name: {_trim(design.get('display_name')) or '-'}",
                f"- description: {_trim(design.get('description')) or '-'}",
            ])
        if questions:
            summary_lines.extend(["", "需要确认：", *[f"- {item}" for item in questions]])
        blocks = [self._block(
            block_id=f"{stage}_final_summary",
            block_type="markdown",
            mode="replace",
            title=title,
            content="\n".join(summary_lines).strip(),
            stage=stage,
        )]
        blocks.extend(self._fields_table_blocks(design, stage=stage))
        logic = [str(item) for item in design.get("logic") or [] if _trim(item)] if isinstance(design, Mapping) else []
        if logic:
            blocks.append(self._block(
                block_id=f"{stage}_logic_flow",
                block_type="flowchart",
                mode="replace",
                title="实现逻辑",
                stage=stage,
                data={"nodes": [{"label": item} for item in logic]},
            ))
        if status == "design_ready":
            blocks.append(self._block(
                block_id=f"{stage}_confirm_action",
                block_type="action",
                mode="replace",
                title="下一步",
                content="确认后进入代码生成和样例测试。",
                stage=stage,
                actions=[{"id": "confirm_implementation", "label": "确认实现", "command": "确认实现"}],
            ))
        return blocks

    def _coding_final_blocks(self, final: Mapping[str, Any], *, status: str, stage: str) -> List[Dict[str, Any]]:
        blocks = [self._block(
            block_id=f"{stage}_final_summary",
            block_type="markdown",
            mode="replace",
            title="代码结果" if status == "code_ready" else "需要回到设计",
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
                block_id=f"{stage}_files",
                block_type="table",
                mode="replace",
                title="生成文件",
                stage=stage,
                data={
                    "headers": ["path", "role", "lines"],
                    "rows": [[_trim(item.get("path")), _trim(item.get("role")), len(_trim(item.get("content")).splitlines())] for item in files],
                },
            ))
            main_file = next((item for item in files if _trim(item.get("content")) and (_trim(item.get("role")) == "tool" or _trim(item.get("path")).endswith(".py"))), None)
            if main_file:
                blocks.append(self._block(
                    block_id=f"{stage}_main_code",
                    block_type="code",
                    mode="replace",
                    title=_trim(main_file.get("path")) or "生成代码",
                    content=_trim(main_file.get("content")),
                    stage=stage,
                ))
        tests = [item for item in final.get("tests") or [] if isinstance(item, Mapping)]
        if tests:
            blocks.append(self._block(
                block_id=f"{stage}_tests",
                block_type="table",
                mode="replace",
                title="测试用例",
                stage=stage,
                data={
                    "headers": ["name", "status", "summary"],
                    "rows": [[_trim(item.get("name")), _trim(item.get("status")), _trim(item.get("summary"))] for item in tests],
                },
            ))
        risks = [str(item) for item in final.get("risks") or [] if _trim(item)]
        if risks:
            blocks.append(self._block(
                block_id=f"{stage}_risks",
                block_type="markdown",
                mode="replace",
                title="风险提示",
                content="\n".join(f"- {item}" for item in risks),
                stage=stage,
            ))
        return blocks

    def _fields_table_blocks(self, design: Mapping[str, Any], *, stage: str) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for key, title in (("inputs", "输入字段"), ("outputs", "输出字段")):
            rows = []
            for item in design.get(key) or []:
                if not isinstance(item, Mapping):
                    continue
                rows.append([
                    _trim(item.get("name")),
                    _trim(item.get("type")),
                    "是" if item.get("required") is True else "否",
                    _trim(item.get("description")),
                ])
            if rows:
                blocks.append(self._block(
                    block_id=f"{stage}_{key}",
                    block_type="table",
                    mode="replace",
                    title=title,
                    stage=stage,
                    data={"headers": ["name", "type", "required", "description"], "rows": rows},
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
