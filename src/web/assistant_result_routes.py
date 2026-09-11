from __future__ import annotations

from io import BytesIO
import re
from typing import Any, Callable, Mapping

from flask import Blueprint, current_app, jsonify, request, send_file

from src.services.financial_report_pdf_service import (
    FinancialReportPdfService,
    PdfReportInput,
)
from src.services.runtime_conversation_service import RuntimeConversationService
from src.services.session_variable_store_service import SessionVariableStoreService


def _contains_data_ref(value: Any, expected: str, *, depth: int = 0) -> bool:
    if depth > 10:
        return False
    if isinstance(value, Mapping):
        return any(
            _contains_data_ref(item, expected, depth=depth + 1)
            for item in value.values()
        )
    if isinstance(value, list):
        return any(
            _contains_data_ref(item, expected, depth=depth + 1)
            for item in value
        )
    return isinstance(value, str) and value == expected


def _used_stock_research(output_payload: Mapping[str, Any]) -> bool:
    financial_qa = (
        output_payload.get("financial_qa")
        if isinstance(output_payload.get("financial_qa"), Mapping)
        else {}
    )
    entries = (
        financial_qa.get("skill_entries")
        if isinstance(financial_qa.get("skill_entries"), list)
        else []
    )
    return any(
        str(item.get("skill_id") or item.get("qualified_skill") or "")
        .strip()
        .rsplit(":", 1)[-1]
        == "stock-research"
        for item in entries
        if isinstance(item, Mapping)
    )


def _report_title(report_text: str) -> str:
    for line in str(report_text or "").splitlines():
        candidate = re.sub(r"^#{1,4}\s+", "", line.strip())
        candidate = re.sub(r"[*_`]", "", candidate).strip()
        if 2 <= len(candidate) <= 80:
            return candidate
    return "Fin Agent 个股深度研究报告"


def _download_name(title: str) -> str:
    normalized = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", str(title or "")).strip(" ._")
    return f"{(normalized or 'Fin_Agent_个股深度研究报告')[:64]}.pdf"


def create_assistant_result_blueprint(
    *,
    conversation_service: RuntimeConversationService,
    identity_resolver: Callable[[], Mapping[str, Any]],
    variable_store: SessionVariableStoreService | None = None,
    pdf_service: FinancialReportPdfService | None = None,
) -> Blueprint:
    store = variable_store or SessionVariableStoreService()
    report_pdf = pdf_service
    blueprint = Blueprint("assistant_results", __name__)

    @blueprint.after_request
    def disable_result_cache(response):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Vary"] = "Cookie"
        return response

    @blueprint.get("/api/assistant/results/page")
    def load_result_page():
        try:
            thread_id = int(request.args.get("thread_id") or 0)
            offset = max(0, int(request.args.get("offset") or 0))
            limit = max(1, min(100, int(request.args.get("limit") or 10)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "分页参数格式不正确。"}), 400

        data_ref = str(request.args.get("data_ref") or "").strip()
        if thread_id <= 0 or not data_ref:
            return jsonify({"ok": False, "error": "缺少结果引用或会话标识。"}), 400

        identity = identity_resolver()
        owner_id = str(identity.get("user_id") or "").strip()
        thread = conversation_service.get_thread(thread_id=thread_id)
        if (
            not thread
            or str(thread.get("owner_type") or "") != "user"
            or str(thread.get("owner_id") or "") != owner_id
        ):
            return jsonify({"ok": False, "error": "无权访问该结果。"}), 403

        turns = conversation_service.list_turns(
            thread_id=thread_id,
            limit=100,
            include_output_payload=True,
            history_payload_only=False,
        )
        if not any(
            _contains_data_ref(turn.get("output_payload"), data_ref)
            for turn in turns
            if isinstance(turn, Mapping)
        ):
            return jsonify({"ok": False, "error": "结果引用不属于该会话。"}), 403

        try:
            session_id, _ = store.parse_data_ref(data_ref)
            page = store.load_data_ref(
                session_id=session_id,
                data_ref=data_ref,
                offset=offset,
                limit=limit,
            )
        except (FileNotFoundError, ValueError):
            return jsonify({"ok": False, "error": "结果数据已失效或不存在。"}), 404
        except Exception:
            current_app.logger.exception("assistant result page load failed")
            return jsonify({"ok": False, "error": "结果分页读取失败，请稍后重试。"}), 500

        return jsonify({"ok": True, **page})

    @blueprint.get("/api/assistant/results/report.pdf")
    def download_financial_report_pdf():
        try:
            thread_id = int(request.args.get("thread_id") or 0)
            turn_id = int(request.args.get("turn_id") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "会话或轮次标识格式不正确。"}), 400
        if thread_id <= 0 or turn_id <= 0:
            return jsonify({"ok": False, "error": "缺少会话或轮次标识。"}), 400

        identity = identity_resolver()
        owner_id = str(identity.get("user_id") or "").strip()
        thread = conversation_service.get_thread(thread_id=thread_id)
        if (
            not thread
            or str(thread.get("owner_type") or "") != "user"
            or str(thread.get("owner_id") or "") != owner_id
        ):
            return jsonify({"ok": False, "error": "无权访问该报告。"}), 403

        turn = conversation_service.get_turn(
            thread_id=thread_id,
            turn_id=turn_id,
            include_output_payload=True,
        )
        if not turn:
            return jsonify({"ok": False, "error": "报告轮次不存在。"}), 404
        output_payload = (
            turn.get("output_payload")
            if isinstance(turn.get("output_payload"), Mapping)
            else {}
        )
        if not _used_stock_research(output_payload):
            return jsonify({"ok": False, "error": "该轮结果不是个股深度研究报告。"}), 409
        report_text = str(
            output_payload.get("message") or turn.get("assistant_output_text") or ""
        ).strip()
        if not report_text:
            return jsonify({"ok": False, "error": "该轮报告正文为空。"}), 409

        title = _report_title(report_text)
        try:
            renderer = report_pdf or FinancialReportPdfService()
            pdf_bytes = renderer.render(
                PdfReportInput(
                    title=title,
                    report_text=report_text,
                    user_question=str(turn.get("user_input_text") or "").strip(),
                    generated_at=str(turn.get("finished_at") or "").strip(),
                )
            )
        except Exception:
            current_app.logger.exception(
                "assistant financial report PDF export failed thread_id=%s turn_id=%s",
                thread_id,
                turn_id,
            )
            return jsonify({"ok": False, "error": "PDF 生成失败，请稍后重试。"}), 500

        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=_download_name(title),
            max_age=0,
        )

    return blueprint
