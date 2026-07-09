"""Minimal Ollama HTTP stub for integration tests (no real inference)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

STUB_CHAT_RESPONSE = (
    '{"ok":true,"issues":[],"suggested_trim_lines":0,"keep_sections":["intro"]}'
)

_COMMON_SH = """#!/usr/bin/env bash
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-stub-model}"

ollama_chat() {
  local system="$1" prompt="$2"
  python - "$OLLAMA_URL" "$OLLAMA_MODEL" "$system" "$prompt" <<'PY'
import json, sys, urllib.request
url, model, system, prompt = sys.argv[1:5]
body = json.dumps({
  "model": model,
  "stream": False,
  "messages": [
    {"role": "system", "content": system},
    {"role": "user", "content": prompt},
  ],
}).encode()
req = urllib.request.Request(
  f"{url}/api/chat",
  data=body,
  headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=30) as r:
  print(json.load(r)["message"]["content"])
PY
}
"""

_AUDIT_SKILL_SH = """#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/ollama/_common.sh"
SKILL="${1:?usage: audit-skill.sh <SKILL.md>}"
SYSTEM='Audit SKILL.md. Reply JSON only.'
ollama_chat "$SYSTEM" "$(cat "$SKILL")"
"""


class _OllamaStubHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/tags":
            self._send_json(200, {"models": [{"name": "stub-model"}]})
            return
        if path == "/v1/models":
            self._send_json(200, {"data": [{"id": "stub-model"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/chat":
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self._send_json(200, {"message": {"content": STUB_CHAT_RESPONSE}})
            return
        if path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self._send_json(
                200,
                {
                    "choices": [{"message": {"content": STUB_CHAT_RESPONSE}}],
                    "usage": {"completion_tokens": 12},
                },
            )
            return
        self.send_error(404)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def install_ollama_scripts(workspace: Path) -> None:
    ollama_dir = workspace / "scripts" / "ollama"
    ollama_dir.mkdir(parents=True, exist_ok=True)
    common = ollama_dir / "_common.sh"
    audit = ollama_dir / "audit-skill.sh"
    common.write_text(_COMMON_SH, encoding="utf-8")
    audit.write_text(_AUDIT_SKILL_SH, encoding="utf-8")
    common.chmod(0o755)
    audit.chmod(0o755)


def clear_ollama_probe_cache() -> None:
    from greedy_token.cheap_llm import clear_cheap_llm_probe_cache

    clear_cheap_llm_probe_cache()


@contextmanager
def ollama_stub_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}"
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
