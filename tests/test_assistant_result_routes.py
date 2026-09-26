from __future__ import annotations

from flask import Flask

from src.web.assistant_result_routes import create_assistant_result_blueprint


class _ConversationService:
    def __init__(self, *, owner_id: str = "user_a", data_ref: str = "session://sess_a/vars/v1", stock_research: bool = True) -> None:
        self.owner_id = owner_id
        self.data_ref = data_ref
        self.stock_research = stock_research

    def get_thread(self, *, thread_id: int):
        if thread_id != 23:
            return None
        return {"thread_id": 23, "owner_type": "user", "owner_id": self.owner_id}

    def list_turns(self, **_kwargs):
        skill_entries = (
            [{"skill_id": "stock-research", "qualified_skill": "fin-agent-finance-business:stock-research"}]
            if self.stock_research
            else [{"skill_id": "earnings-analysis"}]
        )
        return [{
            "turn_id": 41,
            "user_input_text": "深度分析贵州茅台",
            "assistant_output_text": "# 贵州茅台深度研究\n\n核心判断。",
            "finished_at": "2026-08-03 12:00:00",
            "output_payload": {
                "message": "# 贵州茅台深度研究\n\n核心判断。",
                "result_refs": [{"result_ref": self.data_ref}],
                "financial_qa": {"skill_entries": skill_entries},
            },
        }]

    def get_turn(self, *, thread_id: int, turn_id: int, include_output_payload: bool):
        assert thread_id == 23
        assert include_output_payload is True
        if turn_id != 41:
            return None
        return self.list_turns()[0]


class _VariableStore:
    @staticmethod
    def parse_data_ref(data_ref: str):
        if data_ref != "session://sess_a/vars/v1":
            raise ValueError("invalid ref")
        return "sess_a", "v1"

    @staticmethod
    def load_data_ref(**kwargs):
        assert kwargs == {
            "session_id": "sess_a",
            "data_ref": "session://sess_a/vars/v1",
            "offset": 10,
            "limit": 10,
        }
        rows = [{"code": f"stock_{index}"} for index in range(11, 21)]
        return {
            "manifest": {"row_count": 50},
            "page": {
                "offset": 10,
                "limit": 10,
                "returned": 10,
                "total": 50,
                "has_more": True,
            },
            "rows": rows,
        }


class _PdfService:
    def __init__(self) -> None:
        self.values = []

    def render(self, value):
        self.values.append(value)
        return b"%PDF-1.7\nfin-agent-test"


def _app(*, owner_id: str = "user_a", data_ref: str = "session://sess_a/vars/v1", stock_research: bool = True, pdf_service=None) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(
        create_assistant_result_blueprint(
            conversation_service=_ConversationService(owner_id=owner_id, data_ref=data_ref, stock_research=stock_research),
            identity_resolver=lambda: {"user_id": "user_a"},
            variable_store=_VariableStore(),
            pdf_service=pdf_service or _PdfService(),
        )
    )
    return app


def test_result_page_loads_only_an_owned_thread_reference():
    response = _app().test_client().get(
        "/api/assistant/results/page",
        query_string={
            "thread_id": 23,
            "data_ref": "session://sess_a/vars/v1",
            "offset": 10,
            "limit": 10,
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert len(payload["rows"]) == 10
    assert payload["page"]["total"] == 50


def test_result_page_rejects_a_reference_not_recorded_on_the_thread():
    response = _app(data_ref="session://sess_a/vars/v2").test_client().get(
        "/api/assistant/results/page",
        query_string={
            "thread_id": 23,
            "data_ref": "session://sess_a/vars/v1",
        },
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "结果引用不属于该会话。"


def test_result_page_rejects_a_thread_owned_by_another_user():
    response = _app(owner_id="user_b").test_client().get(
        "/api/assistant/results/page",
        query_string={
            "thread_id": 23,
            "data_ref": "session://sess_a/vars/v1",
        },
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "无权访问该结果。"


def test_report_pdf_exports_the_persisted_owned_stock_research_turn():
    pdf_service = _PdfService()
    response = _app(pdf_service=pdf_service).test_client().get(
        "/api/assistant/results/report.pdf",
        query_string={"thread_id": 23, "turn_id": 41},
    )

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-1.7")
    assert "attachment" in response.headers["Content-Disposition"]
    assert len(pdf_service.values) == 1
    assert pdf_service.values[0].title == "贵州茅台深度研究"
    assert pdf_service.values[0].user_question == "深度分析贵州茅台"


def test_report_pdf_rejects_a_non_stock_research_turn():
    response = _app(stock_research=False).test_client().get(
        "/api/assistant/results/report.pdf",
        query_string={"thread_id": 23, "turn_id": 41},
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "该轮结果不是个股深度研究报告。"


def test_report_pdf_rejects_a_foreign_thread():
    response = _app(owner_id="user_b").test_client().get(
        "/api/assistant/results/report.pdf",
        query_string={"thread_id": 23, "turn_id": 41},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "无权访问该报告。"
