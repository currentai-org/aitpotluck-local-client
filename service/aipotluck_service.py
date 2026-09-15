#!/usr/bin/env python3
"""aipotluck-local-client service payload.

Owns the llama-server lifecycle via LlamaSupervisor: starts it on service
startup, restarts it on crash with backoff, and stops it cleanly on service
shutdown. This is the inner tier of the two-tier self-healing story:

    systemd/launchd/Windows Service (restarts this whole process)
        -> aipotluck_service.py (this file)
            -> LlamaSupervisor (restarts just llama-server)
                -> llama-server

Exposes:
    GET /healthz  -> 200 "ok" iff this service process is alive (does not
                     reflect llama-server health -- that's what /status is for)
    GET /status   -> JSON with runtime config + live llama-server supervisor info
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from service.llama_supervisor import LlamaSupervisor  # noqa: E402

SERVICE_NAME = "aipotluck"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765  # distinct from llama-server's default 8080

log = logging.getLogger("aipotluck.service")

_supervisor: LlamaSupervisor | None = None
_runtime_config: dict = {}


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
        log.warning("No runtime.json found at %s -- run the installer first", runtime_path)
        return {}
    try:
        return json.loads(runtime_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to read runtime.json: %s", exc)
        return {}


def build_llama_server_args(llama_cfg: dict) -> list[str]:
    args = ["--host", str(llama_cfg.get("host", "127.0.0.1")), "--port", str(llama_cfg.get("port", 8080))]

    model_path = llama_cfg.get("model_path")
    model_hf = llama_cfg.get("model_hf")
    if model_path:
        args += ["--model", str(model_path)]
    elif model_hf:
        args += ["-hf", str(model_hf)]
    else:
        log.warning("No model_path or model_hf configured; llama-server will fail to start without a model")

    ctx_size = llama_cfg.get("ctx_size")
    if ctx_size:
        args += ["--ctx-size", str(ctx_size)]

    gpu_layers = llama_cfg.get("gpu_layers")
    if gpu_layers is not None:
        args += ["--gpu-layers", str(gpu_layers)]

    return args


def build_supervisor(runtime_config: dict, log_dir: Path | None) -> LlamaSupervisor | None:
    llama_cfg = runtime_config.get("llama_cpp")
    if not llama_cfg:
        log.error("runtime.json has no llama_cpp section; cannot supervise llama-server")
        return None

    server_binary = Path(llama_cfg["server_binary"])
    if not server_binary.exists():
        log.error("llama-server binary not found at %s", server_binary)
        return None

    return LlamaSupervisor(
        server_binary=server_binary,
        args=build_llama_server_args(llama_cfg),
        host=llama_cfg.get("host", "127.0.0.1"),
        port=llama_cfg.get("port", 8080),
        log_dir=log_dir,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # quiet default stderr logging
        log.info("%s - %s", self.address_string(), format % args)

    def _write_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            llama_info = _supervisor.info().__dict__ if _supervisor else {"error": "supervisor not started"}
            self._write_json(
                {
                    "service": SERVICE_NAME,
                    "status": "running",
                    "runtime_config": _runtime_config,
                    "llama_server": llama_info,
                }
            )
            return

        self.send_response(404)
        self.end_headers()


def run(host: str, port: int, config_dir: Path, log_dir: Path | None) -> None:
    global _supervisor, _runtime_config
    _runtime_config = load_runtime_config(config_dir)

    _supervisor = build_supervisor(_runtime_config, log_dir)
    if _supervisor:
        log.info("Starting llama-server supervisor")
        _supervisor.start()
    else:
        log.error("llama-server supervisor could not be created; service will run without it")

    server = ThreadingHTTPServer((host, port), Handler)
    log.info("aipotluck service listening on http://%s:%s", host, port)

    def handle_signal(signum, _frame):
        log.info("Received signal %s, shutting down", signum)
        # server.shutdown() blocks until serve_forever()'s loop notices and
        # exits -- calling it directly from a signal handler running on the
        # same (main) thread that's inside serve_forever() would deadlock.
        # Run it from a short-lived helper thread instead.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        if _supervisor:
            _supervisor.stop()
        log.info("Service stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="aipotluck local service")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path(os.environ.get("AIPOTLUCK_CONFIG_DIR", Path.home() / ".config" / "aipotluck")),
    )
    parser.add_argument("--log-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    setup_logging(args.log_dir)
    run(args.host, args.port, args.config_dir, args.log_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
