from __future__ import annotations

import mimetypes
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

from greedy_token.hub.api import handle_api, json_bytes

STATIC_DIR = Path(__file__).resolve().parent / "static"

# The hub binds to loopback and serves local telemetry (usage log path, spend,
# session data). Only echo CORS for browser Origins that are themselves local —
# never "*", which would let any visited website read this data cross-origin.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _origin_is_local(origin: str) -> bool:
    try:
        host = urlparse(origin).hostname
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


class HubHandler(BaseHTTPRequestHandler):
    static_dir: Path = STATIC_DIR

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"greedy-hub {self.address_string()} - {fmt % args}\n")

    def _cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin and _origin_is_local(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            status, payload = handle_api(self.path)
            status, body, ctype = json_bytes(status, payload)
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        rel = parsed.path.lstrip("/") or "index.html"
        file_path = self.static_dir / rel
        if not file_path.is_file() or self.static_dir not in file_path.resolve().parents:
            file_path = self.static_dir / "index.html"

        body = file_path.read_bytes()
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(*, host: str = "127.0.0.1", port: int = 8787) -> None:
    handler = partial(HubHandler)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"greedy-token hub → http://{host}:{port}", file=sys.stderr)
    print(f"  log: see /api/health", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ngreedy-token hub stopped", file=sys.stderr)
        server.server_close()


def static_root() -> Path:
    """Return packaged static dir (for tests)."""
    try:
        ref = resources.files("greedy_token.hub") / "static"
        return Path(str(ref))
    except (TypeError, ModuleNotFoundError):
        return STATIC_DIR
