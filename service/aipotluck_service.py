#!/usr/bin/env python3
"""aipotluck-local-client blank service payload.

Stdlib-only. For this phase it does not manage llama-server -- it exists to
prove out the cross-platform service lifecycle (install/start/stop/status,
autostart, logging) so the future transport layer has a place to live.

Exposes GET /healthz -> 200 "ok" so external tooling (and our own installer
smoke test) has something to poll regardless of which OS service backend is
running it.

Reads runtime.json (written by the installer) to know where llama-server
lives, in preparation for later orchestration -- but takes no action on it
yet.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE_NAME = "aipotluck"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765  # distinct from llama-server's default 8080

log = logging.getLogger("aipotluck.service")


def setup_logging(log_dir: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{SERVICE_NAME}.log", maxBytes=5 * 1024 * 1024, backupCount=3
        )
        handlers.append(file_handler)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def load_runtime_config(config_dir: Path) -> dict:
    runtime_path = config_dir / "runtime.json"
    if not runtime_path.exists():
        log.warning("No runtime.json found at %s -- llama.cpp location unknown yet", runtime_path)
        return {}
    try:
        return json.loads(runtime_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to read runtime.json: %s", exc)
        return {}


class HealthHandler(BaseHTTPRequestHandler):
    runtime_config: dict = {}

    def log_message(self, format: str, *args) -> None:  # quiet default stderr logging
        log.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/status":
            payload = json.dumps(
                {
                    "service": SERVICE_NAME,
                    "status": "running",
                    "runtime_config": self.runtime_config,
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


def run(host: str, port: int, config_dir: Path) -> None:
    runtime_config = load_runtime_config(config_dir)
    HealthHandler.runtime_config = runtime_config

    server = ThreadingHTTPServer((host, port), HealthHandler)
    log.info("aipotluck blank service listening on http://%s:%s", host, port)
    log.info("runtime config: %s", runtime_config or "(none yet)")

    def handle_signal(signum, _frame):
        log.info("Received signal %s, shutting down", signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    server.serve_forever()
    server.server_close()
    log.info("Service stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="aipotluck blank local service")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path(os.environ.get("AIPOTLUCK_CONFIG_DIR", Path.home() / ".config" / "aipotluck")),
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
    )
    args = parser.parse_args(argv)

    setup_logging(args.log_dir)
    run(args.host, args.port, args.config_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
