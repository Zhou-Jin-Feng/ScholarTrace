"""Loopback HTTP peer for production-adapter integration tests."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx


@contextmanager
def production_http_peer(
    respond: Callable[[httpx.Request], httpx.Response],
) -> Iterator[Callable[[], httpx.AsyncBaseTransport]]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            pass

        def handle_request(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            content = self.rfile.read(length)
            original = self.headers["x-synthetic-original-url"]
            request = httpx.Request(
                self.command,
                original,
                content=content,
                headers={"content-type": self.headers.get("content-type", "")},
            )
            try:
                response = respond(request)
            except httpx.TimeoutException:
                self.close_connection = True
                return
            body = response.content
            self.send_response(response.status_code)
            for name, value in response.headers.items():
                if name not in {"content-length", "transfer-encoding", "connection"}:
                    self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    class Transport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            content = await request.aread()
            forwarded = httpx.Request(
                request.method,
                origin + "/",
                content=content,
                headers={
                    "x-synthetic-original-url": str(request.url),
                    "content-type": request.headers.get("content-type", ""),
                },
            )
            return await self.inner.handle_async_request(forwarded)

        async def aclose(self) -> None:
            await self.inner.aclose()

    try:
        yield Transport
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        if worker.is_alive():
            raise RuntimeError("synthetic HTTP peer did not stop")
