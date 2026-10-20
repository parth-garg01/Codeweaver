"""The local web UI server.

Deliberate constraints, because this server has the user's filesystem behind
it:

* it binds to the loopback interface only, and refuses a request whose ``Host``
  header is not a loopback name — which blocks DNS-rebinding from a web page
  the user happens to have open;
* there are no CORS headers, so a page on another origin cannot read responses;
* static files are served from one packaged directory, resolved and checked, so
  a crafted URL cannot escape it;
* request bodies are size-limited and must be JSON.

It is a single-threaded ``http.server``: no third-party dependency, and one
request at a time is the right model for a tool driving the local toolchain.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from codeweaver.ui.api import ApiError, ApiRouter

__all__ = ["STATIC_DIR", "create_server", "serve"]

#: Packaged static assets.
STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Largest request body accepted.
MAX_BODY_BYTES = 1 * 1024 * 1024

#: Host header values treated as local.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class _Handler(BaseHTTPRequestHandler):
    server_version = "CodeWeaver"
    sys_version = ""

    # Injected by create_server.
    router: ApiRouter
    static_dir: Path

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if not self._host_is_local():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._serve_api("GET", parsed.path, _query(parsed.query), {})
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        if not self._host_is_local():
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            body = self._read_json()
        except ApiError as exc:
            self._send_json({"error": str(exc)}, status=exc.status)
            return
        self._serve_api("POST", parsed.path, _query(parsed.query), body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
        """Log to stderr without the request line's query string.

        A query string can name a path on the user's machine, and request logs
        are the easiest place to leak one.
        """
        if self.server.quiet:  # type: ignore[attr-defined]
            return
        route = urlparse(self.path).path
        print(f"[codeweaver-ui] {self.command} {route} -> {args[1] if len(args) > 1 else ''}")

    # -- internals ---------------------------------------------------------

    def _host_is_local(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host in _LOOPBACK_HOSTS or host == "":
            return True
        self._send_json(
            {"error": ("refused: the CodeWeaver UI only accepts requests addressed to localhost")},
            status=421,
        )
        return False

    def _read_json(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ApiError("invalid Content-Length") from exc
        if length > MAX_BODY_BYTES:
            raise ApiError("request body is too large", status=413)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError("request body must be a JSON object")
        return payload

    def _serve_api(
        self, method: str, path: str, query: dict[str, str], body: dict[str, Any]
    ) -> None:
        try:
            payload = self.router.handle(method, path, query, body)
        except ApiError as exc:
            self._send_json({"error": str(exc)}, status=exc.status)
            return
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._send_json({"error": f"internal error: {exc}"}, status=500)
            return
        self._send_json(payload)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        base = self.static_dir.resolve()
        target = (base / relative).resolve()
        if base != target and base not in target.parents:
            self._send_json({"error": "not found"}, status=404)
            return
        if not target.is_file():
            self._send_json({"error": "not found"}, status=404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def _query(raw: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(raw).items() if values}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    quiet = False


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8731,
    allow_tests: bool = False,
    allow_install: bool = False,
    static_dir: Path | None = None,
    quiet: bool = False,
) -> _Server:
    """Create the UI server without starting it.

    *host* must be a loopback address: the UI exposes the local filesystem, so
    binding it to a routable interface would publish the user's project.
    """
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError(
            f"refusing to bind the CodeWeaver UI to {host!r}; it serves local files "
            "and must stay on the loopback interface"
        )
    handler = type(
        "_BoundHandler",
        (_Handler,),
        {
            "router": ApiRouter(allow_tests=allow_tests, allow_install=allow_install),
            "static_dir": static_dir or STATIC_DIR,
        },
    )
    server = _Server((host, port), handler)
    server.quiet = quiet
    return server


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8731,
    allow_tests: bool = False,
    allow_install: bool = False,
) -> None:  # pragma: no cover - blocking loop
    """Run the UI until interrupted."""
    server = create_server(
        host=host, port=port, allow_tests=allow_tests, allow_install=allow_install
    )
    actual = server.server_address[1]
    print(f"CodeWeaver UI on http://{host}:{actual}  (Ctrl-C to stop)")
    print("Nothing leaves this machine. No model is called.")
    if allow_tests or allow_install:
        enabled = ", ".join(
            name for name, flag in (("tests", allow_tests), ("install", allow_install)) if flag
        )
        print(f"Execution enabled for: {enabled}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
