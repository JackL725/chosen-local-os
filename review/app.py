"""Review console placeholder: answers /healthz so systemd and Tailscale can be tested.

It binds 127.0.0.1 inside WSL. Windows reaches it on localhost, and Tailscale on the
Windows host publishes it to the founders' devices only (deploy/README.md). It shows
no data in Phase 0.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from worker import logs

log = logs.get_logger("review")


class Handler(BaseHTTPRequestHandler):
    server_version = "cl-review"
    sys_version = ""

    def do_GET(self) -> None:
        if self.path == "/healthz":
            body = json.dumps({"ok": True, "service": "review-console", "phase": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = b"Chosen Local review console: nothing here yet (Phase 2c).\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Request lines can carry query strings; log nothing per request.
        return


def serve() -> None:
    logs.configure()
    host = os.environ.get("REVIEW_CONSOLE_HOST", "127.0.0.1")
    port = int(os.environ.get("REVIEW_CONSOLE_PORT", "8090"))
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("REVIEW_CONSOLE_HOST must be a loopback address; Tailscale publishes it")
    httpd = ThreadingHTTPServer((host, port), Handler)
    log.info("review console listening", extra={"port": port})
    httpd.serve_forever()
