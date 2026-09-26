from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import httpx
import pytest

from src.services.fixed_upstream_loopback_bridge import (
    FixedUpstreamLoopbackBridge,
)


def test_loopback_bridge_forwards_to_one_fixed_upstream_and_streams() -> None:
    received = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization")
            received["body"] = self.rfile.read(length)
            payload = b'data: {"type":"message_start"}\\n\\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    bridge = FixedUpstreamLoopbackBridge(
        f"http://127.0.0.1:{upstream.server_address[1]}/apps/anthropic",
        allow_insecure_upstream=True,
    )
    try:
        base_url = bridge.start()
        response = httpx.post(
            f"{base_url}/v1/messages?beta=1",
            headers={"Authorization": "Bearer test-token"},
            json={"model": "demo"},
            timeout=5,
        )

        assert response.status_code == 200
        assert response.text.startswith("data:")
        assert received["path"] == "/apps/anthropic/v1/messages?beta=1"
        assert received["authorization"] == "Bearer test-token"
        assert json.loads(received["body"]) == {"model": "demo"}
        assert httpx.post(
            f"{base_url.replace(base_url.rsplit('/', 1)[-1], 'wrong')}/v1/messages",
            timeout=5,
        ).status_code == 404
        assert httpx.get(
            f"{base_url}/v1/messages",
            timeout=5,
        ).status_code == 405
        assert httpx.post(
            f"{base_url}/not-allowed",
            timeout=5,
        ).status_code == 404
    finally:
        bridge.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=3)


def test_loopback_bridge_requires_https_by_default() -> None:
    with pytest.raises(ValueError, match="verified HTTPS"):
        FixedUpstreamLoopbackBridge("http://127.0.0.1:12345/apps/anthropic")
    with pytest.raises(ValueError, match="absolute upstream base"):
        FixedUpstreamLoopbackBridge(
            "https://user:password@example.com/apps/anthropic"
        )


def test_loopback_bridge_rejects_oversized_body_before_upstream() -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            raise AssertionError("oversized request must not reach upstream")

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    bridge = FixedUpstreamLoopbackBridge(
        f"http://127.0.0.1:{upstream.server_address[1]}/apps/anthropic",
        max_request_body_bytes=8,
        allow_insecure_upstream=True,
    )
    try:
        response = httpx.post(
            f"{bridge.start()}/v1/messages",
            content=b"123456789",
            timeout=5,
        )
        assert response.status_code == 413
    finally:
        bridge.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=3)
