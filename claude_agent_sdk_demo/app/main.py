from __future__ import annotations

import asyncio
import hmac
from pathlib import Path
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from .backend import ClaudeAgentBackend
from .config import Settings
from .contracts import RunRequest
from .event_stream import encode_sse, encode_sse_comment
from .fake_backend import FakeAgentBackend
from .harness import AgentHarness
from .middleware import RequestBodyLimitMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or Settings.from_env()
    backend = ClaudeAgentBackend(runtime) if runtime.backend == "claude" else FakeAgentBackend()
    harness = AgentHarness(backend, runtime)
    semaphore = asyncio.Semaphore(runtime.max_concurrent_runs)
    app = FastAPI(title="Claude Agent SDK Harness Demo", version="0.1.0")
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=runtime.max_request_bytes)

    async def require_client_key(x_demo_api_key: str | None = Header(default=None)) -> None:
        if runtime.client_api_key and not hmac.compare_digest(x_demo_api_key or "", runtime.client_api_key):
            raise HTTPException(status_code=401, detail="invalid demo API key")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html_path = Path(__file__).with_name("static") / "index.html"
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    @app.get("/healthz")
    async def healthz() -> dict:
        status = runtime.readiness()
        return {"ok": status["ready"], "config": status}

    @app.get("/v1/skills", dependencies=[Depends(require_client_key)])
    async def list_skills() -> dict:
        return {"skills": harness.available_skills()}

    @app.post("/v1/runs/stream", dependencies=[Depends(require_client_key)])
    async def run_stream(payload: RunRequest, request: Request) -> StreamingResponse:
        readiness = runtime.readiness()
        if not readiness["ready"]:
            raise HTTPException(status_code=503, detail={"message": "backend is not ready", "issues": readiness["issues"]})
        run_id = f"run_{uuid.uuid4().hex}"
        try:
            harness.validate_request(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        async def generate():
            queue: asyncio.Queue[object] = asyncio.Queue(maxsize=128)
            sentinel = object()

            async def produce() -> None:
                try:
                    async with semaphore:
                        async for event in harness.stream(payload, run_id=run_id):
                            await queue.put(event)
                finally:
                    await queue.put(sentinel)

            producer = asyncio.create_task(produce(), name=f"claude-demo-{run_id}")
            try:
                while True:
                    if await request.is_disconnected():
                        producer.cancel()
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=runtime.heartbeat_seconds)
                    except asyncio.TimeoutError:
                        yield encode_sse_comment(f"heartbeat {run_id}")
                        continue
                    if item is sentinel:
                        break
                    yield encode_sse(item)  # type: ignore[arg-type]
            finally:
                if not producer.done():
                    producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Run-Id": run_id,
            },
        )

    return app


app = create_app()
