"""Generic one-shot local OAuth callback listener, for use by the CLI.

The FastAPI web server already has something listening on localhost when a
provider's redirect comes back. A bare CLI invocation doesn't — this module
gives it a throwaway HTTP listener that catches exactly one callback request
and then shuts down, mirroring the trick `claude mcp add` uses for OAuth.
"""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


@dataclass
class CallbackResult:
    code: str
    state: str
    error: str | None


_RESPONSE_BODY = b"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;text-align:center;padding-top:4em;">
<p>Connected. You can close this tab.</p>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        server: "_LoopbackServer" = self.server  # type: ignore[assignment]
        parsed = urlparse(self.path)
        if parsed.path != server.callback_path:
            self.send_response(404)
            self.end_headers()
            return
        query = parse_qs(parsed.query)
        server.result = CallbackResult(
            code=query.get("code", [""])[0],
            state=query.get("state", [""])[0],
            error=query.get("error", [None])[0],
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(_RESPONSE_BODY)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - silence default stderr logging
        pass


class _LoopbackServer(HTTPServer):
    callback_path: str
    result: CallbackResult | None = None


class LoopbackListener:
    def __init__(self, port: int, path: str):
        self._port = port
        self._path = path
        self._server: _LoopbackServer | None = None

    def __enter__(self) -> "LoopbackListener":
        server = _LoopbackServer(("127.0.0.1", self._port), _Handler)
        server.callback_path = self._path
        server.result = None
        self._server = server
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.server_close()
            self._server = None

    def wait(self, timeout_seconds: float = 180) -> CallbackResult:
        assert self._server is not None, "LoopbackListener must be used as a context manager"
        self._server.timeout = timeout_seconds
        while self._server.result is None:
            self._server.handle_request()
            if self._server.result is None:
                raise TimeoutError(f"No OAuth callback received within {timeout_seconds}s.")
        return self._server.result


def start(port: int, path: str) -> LoopbackListener:
    return LoopbackListener(port, path)


def open_browser(url: str) -> None:
    webbrowser.open(url)
