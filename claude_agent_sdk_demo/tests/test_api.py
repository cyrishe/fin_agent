import asyncio
import json

import httpx

from app.config import ROOT_DIR, Settings
from app.main import create_app


def _sse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_stream_endpoint_happy_path() -> None:
    app = create_app(Settings(root_dir=ROOT_DIR, backend="fake"))

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/runs/stream",
                json={
                    "question": "测试流",
                    "skill_names": ["financial-research"],
                    "enable_web_search": True,
                },
            )

    response = asyncio.run(request())
    events = _sse_events(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert events[0]["type"] == "run.started"
    assert any(event["type"] == "assistant.delta" for event in events)
    assert events[-1]["type"] == "run.completed"


def test_stream_endpoint_requires_configured_client_key() -> None:
    app = create_app(Settings(root_dir=ROOT_DIR, backend="fake", client_api_key="expected"))

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/v1/runs/stream", json={"question": "test"})

    response = asyncio.run(request())

    assert response.status_code == 401


def test_request_rejects_unknown_fields() -> None:
    app = create_app(Settings(root_dir=ROOT_DIR, backend="fake"))

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/v1/runs/stream", json={"question": "test", "surprise": True})

    response = asyncio.run(request())

    assert response.status_code == 422


def test_request_body_size_is_bounded() -> None:
    app = create_app(Settings(root_dir=ROOT_DIR, backend="fake", max_question_chars=10))

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/v1/runs/stream", json={"question": "x" * 100_000})

    response = asyncio.run(request())

    assert response.status_code == 413
