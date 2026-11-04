from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import secrets
import threading
from typing import Optional
from urllib.parse import urlsplit

import httpx


logger = logging.getLogger(__name__)

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class _RequestBodyTooLarge(ValueError):
    pass


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


class FixedUpstreamLoopbackBridge:
    """Tunnel one local HTTP prefix to one verified HTTPS upstream.

    The bridge exists for native agent runtimes whose embedded TLS trust store
    can lag the host Python trust store. It never disables upstream TLS
    verification, never accepts a caller-selected target, and only listens on
    loopback behind an unguessable path prefix.
    """

    def __init__(
        self,
        upstream_base_url: str,
        *,
        timeout_seconds: float = 300,
        max_request_body_bytes: int = 16 * 1024 * 1024,
        max_concurrency: int = 16,
        allowed_paths: tuple[str, ...] = (
            "/v1/messages",
            "/v1/messages/count_tokens",
        ),
        allow_insecure_upstream: bool = False,
    ) -> None:
        parsed = urlsplit(str(upstream_base_url or "").strip())
        if (
            parsed.scheme != "https"
            and not (allow_insecure_upstream and parsed.scheme == "http")
        ):
            raise ValueError("loopback bridge requires a verified HTTPS upstream")
        if (
            not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("loopback bridge requires an absolute upstream base URL")
        self.upstream_base_url = (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
        )
        self._token = secrets.token_urlsafe(24)
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._max_request_body_bytes = max(1, int(max_request_body_bytes))
        self._allowed_paths = frozenset(
            path if path.startswith("/") else f"/{path}"
            for path in allowed_paths
        )
        if not self._allowed_paths:
            raise ValueError("loopback bridge requires at least one allowed path")
        self._request_slots = threading.BoundedSemaphore(
            max(1, int(max_concurrency))
        )
        self._metrics_guard = threading.Lock()
        self._total_requests = 0
        self._active_requests = 0
        self._upstream_failures = 0
        self._last_error_type = ""
        self._guard = threading.Lock()
        self._server: Optional[_LoopbackServer] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[httpx.Client] = None

    @property
    def base_url(self) -> str:
        with self._guard:
            if self._server is None:
                raise RuntimeError("loopback bridge is not started")
            port = int(self._server.server_address[1])
        return f"http://127.0.0.1:{port}/{self._token}"

    def diagnostics(self) -> dict[str, int | str]:
        """Return transport-only counters without URLs, credentials or bodies."""
        with self._metrics_guard:
            return {
                "total_requests": self._total_requests,
                "active_requests": self._active_requests,
                "upstream_failures": self._upstream_failures,
                "last_error_type": self._last_error_type,
            }

    def _request_started(self) -> None:
        with self._metrics_guard:
            self._total_requests += 1
            self._active_requests += 1

    def _request_finished(self) -> None:
        with self._metrics_guard:
            self._active_requests = max(0, self._active_requests - 1)

    def _upstream_failed(self, exc: Exception) -> None:
        error_type = type(exc).__name__
        with self._metrics_guard:
            self._upstream_failures += 1
            self._last_error_type = error_type
        logger.warning(
            "Fixed upstream bridge request failed: %s",
            error_type,
        )

    def start(self) -> str:
        with self._guard:
            if self._server is not None:
                port = int(self._server.server_address[1])
                return f"http://127.0.0.1:{port}/{self._token}"
            self._client = httpx.Client(
                timeout=httpx.Timeout(
                    connect=15,
                    read=self._timeout_seconds,
                    write=60,
                    pool=15,
                ),
                follow_redirects=False,
                trust_env=True,
            )
            bridge = self

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, _format: str, *args) -> None:
                    return

                def do_POST(self) -> None:  # noqa: N802
                    self._forward()

                def do_GET(self) -> None:  # noqa: N802
                    self._send_error(405, "method not allowed")

                def do_PUT(self) -> None:  # noqa: N802
                    self._send_error(405, "method not allowed")

                def do_PATCH(self) -> None:  # noqa: N802
                    self._send_error(405, "method not allowed")

                def do_DELETE(self) -> None:  # noqa: N802
                    self._send_error(405, "method not allowed")

                def _read_body(self) -> bytes:
                    if (
                        str(self.headers.get("Transfer-Encoding") or "").lower()
                        == "chunked"
                    ):
                        chunks: list[bytes] = []
                        total = 0
                        while True:
                            size_line = self.rfile.readline().split(b";", 1)[0]
                            size = int(size_line.strip() or b"0", 16)
                            if size <= 0:
                                self.rfile.readline()
                                break
                            total += size
                            if total > bridge._max_request_body_bytes:
                                raise _RequestBodyTooLarge
                            chunks.append(self.rfile.read(size))
                            self.rfile.read(2)
                        return b"".join(chunks)
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > bridge._max_request_body_bytes:
                        raise _RequestBodyTooLarge
                    return self.rfile.read(length) if length > 0 else b""

                def _forward(self) -> None:
                    self.connection.settimeout(60)
                    parsed_path = urlsplit(self.path)
                    prefix = f"/{bridge._token}"
                    if not (
                        parsed_path.path == prefix
                        or parsed_path.path.startswith(f"{prefix}/")
                    ):
                        self._send_error(404, "not found")
                        return
                    suffix = parsed_path.path[len(prefix) :]
                    if suffix not in bridge._allowed_paths:
                        self._send_error(404, "not found")
                        return
                    if not bridge._request_slots.acquire(blocking=False):
                        self._send_error(503, "bridge is busy")
                        return
                    bridge._request_started()
                    target = f"{bridge.upstream_base_url}{suffix}"
                    if parsed_path.query:
                        target = f"{target}?{parsed_path.query}"
                    headers = {
                        name: value
                        for name, value in self.headers.items()
                        if name.lower()
                        not in {*_HOP_BY_HOP_HEADERS, "host", "content-length"}
                    }
                    response_started = False
                    try:
                        body = self._read_body()
                        assert bridge._client is not None
                        with bridge._client.stream(
                            self.command,
                            target,
                            headers=headers,
                            content=body,
                        ) as upstream:
                            response_started = True
                            self.send_response(upstream.status_code)
                            for name, value in upstream.headers.multi_items():
                                if name.lower() in {
                                    *_HOP_BY_HOP_HEADERS,
                                    "content-length",
                                }:
                                    continue
                                self.send_header(name, value)
                            self.send_header("Transfer-Encoding", "chunked")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            if self.command != "HEAD":
                                for chunk in upstream.iter_raw():
                                    if not chunk:
                                        continue
                                    self.wfile.write(
                                        f"{len(chunk):X}\r\n".encode("ascii")
                                    )
                                    self.wfile.write(chunk)
                                    self.wfile.write(b"\r\n")
                                    self.wfile.flush()
                                self.wfile.write(b"0\r\n\r\n")
                                self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    except _RequestBodyTooLarge:
                        if not response_started:
                            self._send_error(413, "request body too large")
                    except (OSError, ValueError):
                        if not response_started:
                            self._send_error(400, "invalid request body")
                    except Exception as exc:
                        # Once response headers have been sent, writing a second
                        # HTTP response would corrupt the stream. Closing the
                        # connection lets the CLI classify it as a retriable
                        # mid-response transport failure.
                        bridge._upstream_failed(exc)
                        if not response_started:
                            self._send_error(
                                502,
                                "verified upstream request failed",
                            )
                    finally:
                        bridge._request_finished()
                        bridge._request_slots.release()
                        self.close_connection = True

                def _send_error(self, status: int, message: str) -> None:
                    payload = json.dumps(
                        {"type": "error", "error": {"message": message}},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(payload)
                    self.close_connection = True

            server = _LoopbackServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(
                target=server.serve_forever,
                name="fixed-upstream-loopback-bridge",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            thread.start()
            port = int(server.server_address[1])
            return f"http://127.0.0.1:{port}/{self._token}"

    def close(self) -> None:
        with self._guard:
            server = self._server
            thread = self._thread
            client = self._client
            self._server = None
            self._thread = None
            self._client = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        if client is not None:
            client.close()
