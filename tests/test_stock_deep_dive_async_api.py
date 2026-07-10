import pytest

pytest.importorskip("sentence_transformers")

from src.web.flask_app import app
from src.services.task_service import TaskCapacityError


class FakeAsyncTaskService:
    def submit_skill_job(self, **kwargs):
        return {
            "job_id": "job_demo_skill_001",
            "task_type": kwargs.get("skill_name") or "unknown",
            "status": "queued",
            "current_stage": "queued",
            "progress": 0.0,
            "runtime_config": {
                "default_execution_profile": kwargs.get("execution_profile") or "real",
            },
        }

    def submit_stock_deep_dive(self, **kwargs):
        return {
            "job_id": "job_demo_001",
            "task_type": "stock_deep_dive",
            "status": "queued",
            "current_stage": "queued",
            "progress": 0.0,
            "runtime_config": {
                "default_execution_profile": kwargs.get("execution_profile") or "real",
            },
        }

    def get_job(self, job_id):
        if job_id != "job_demo_001":
            return None
        return {
            "job_id": "job_demo_001",
            "task_type": "stock_deep_dive",
            "status": "running",
            "current_stage": "collecting_data",
            "progress": 35.0,
        }

    def get_steps(self, job_id):
        return [
            {
                "seq": 1,
                "stage": "queued",
                "step_type": "task",
                "title": "任务已创建",
                "status": "completed",
            },
            {
                "seq": 2,
                "stage": "collecting_data",
                "step_type": "tool_call",
                "title": "调用 stock_quote",
                "status": "completed",
                "tool_name": "stock_quote",
            },
        ]

    def get_result_map(self, job_id):
        return {
            "render_payload": {
                "page_type": "stock_deep_dive",
                "title": "中国电建深度分析",
            }
        }


class FakeBusyAsyncTaskService:
    def submit_stock_deep_dive(self, **kwargs):
        raise TaskCapacityError("异步任务容量已满: inflight=50, limit=50")


def test_create_stock_deep_dive_job(monkeypatch):
    monkeypatch.setattr("src.web.flask_app.async_task_service", FakeAsyncTaskService())
    client = app.test_client()
    response = client.post("/api/skills/stock_deep_dive/jobs", json={"code": "601669", "name": "中国电建"})
    data = response.get_json()

    assert response.status_code == 202
    assert data["ok"] is True
    assert data["job"]["job_id"] == "job_demo_001"


def test_create_hotspot_trace_job(monkeypatch):
    monkeypatch.setattr("src.web.flask_app.async_task_service", FakeAsyncTaskService())
    client = app.test_client()
    response = client.post(
        "/api/skills/hotspot_trace/jobs",
        json={"trace_type": "concept", "trace_key": "机器人", "concept": "机器人"},
    )
    data = response.get_json()

    assert response.status_code == 202
    assert data["ok"] is True
    assert data["job"]["job_id"] == "job_demo_skill_001"
    assert data["job"]["task_type"] == "hotspot_trace"


def test_create_stock_deep_dive_job_accepts_execution_profile(monkeypatch):
    monkeypatch.setattr("src.web.flask_app.async_task_service", FakeAsyncTaskService())
    client = app.test_client()
    response = client.post(
        "/api/skills/stock_deep_dive/jobs",
        json={"code": "601669", "name": "中国电建", "execution_profile": "mock"},
    )
    data = response.get_json()

    assert response.status_code == 202
    assert data["ok"] is True
    assert data["job"]["runtime_config"]["default_execution_profile"] == "mock"


def test_router_preview_returns_mock_argument_plans(monkeypatch):
    client = app.test_client()
    response = client.post(
        "/api/router/preview",
        json={
            "user_text": "请从行情、资金、研报、新闻和风险角度，对天孚通信做一份分析。",
            "context": {"name": "天孚通信", "code": "300394"},
            "execution_profile": "mock",
        },
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["ok"] is True
    plans = ((data.get("skill_debug") or {}).get("tool_argument_plans") or [])
    assert plans
    assert all(plan.get("execution_profile") == "mock" for plan in plans)


def test_get_task_job(monkeypatch):
    monkeypatch.setattr("src.web.flask_app.async_task_service", FakeAsyncTaskService())
    client = app.test_client()
    response = client.get("/api/tasks/job_demo_001")
    data = response.get_json()

    assert response.status_code == 200
    assert data["job"]["status"] == "running"
    assert data["job"]["current_stage"] == "collecting_data"


def test_get_task_steps(monkeypatch):
    monkeypatch.setattr("src.web.flask_app.async_task_service", FakeAsyncTaskService())
    client = app.test_client()
    response = client.get("/api/tasks/job_demo_001/steps")
    data = response.get_json()

    assert response.status_code == 200
    assert len(data["items"]) == 2
    assert data["items"][1]["tool_name"] == "stock_quote"


def test_get_task_result(monkeypatch):
    monkeypatch.setattr("src.web.flask_app.async_task_service", FakeAsyncTaskService())
    client = app.test_client()
    response = client.get("/api/tasks/job_demo_001/result?result_type=render_payload")
    data = response.get_json()

    assert response.status_code == 200
    assert data["content"]["page_type"] == "stock_deep_dive"


def test_create_stock_deep_dive_job_capacity_limited(monkeypatch):
    monkeypatch.setattr("src.web.flask_app.async_task_service", FakeBusyAsyncTaskService())
    client = app.test_client()
    response = client.post("/api/skills/stock_deep_dive/jobs", json={"code": "601669", "name": "中国电建"})
    data = response.get_json()

    assert response.status_code == 429
    assert data["ok"] is False
    assert "容量已满" in data["error"]
