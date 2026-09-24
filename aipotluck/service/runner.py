"""Portable service runtime shared by every OS entry point.

This module contains zero OS-specific code (no signal handling, no service
framework imports) so the exact same logic runs under:

- Linux:   systemd unit invokes aipotluck/service/aipotluck_service.py directly
- macOS:   launchd invokes aipotluck/service/aipotluck_service.py directly
- Windows: schtasks (no-admin) invokes aipotluck/service/aipotluck_service.py directly;
           the --system pywin32 Windows Service
           (aipotluck/service/windows_service_host.py) imports AipotluckServiceRunner
           and drives start()/stop() from SvcDoRun/SvcStop instead of a
           signal handler.

AipotluckServiceRunner owns:
  - the HTTP status server (GET /healthz, GET /status)
  - the LlamaSupervisor lifecycle (start on run, stop on shutdown)
  - the NewtSupervisor lifecycle (same as above)

Both supervisors are gated on runtime.json's "logged_in" flag: every fresh install starts logged
out (no Pangolin credentials), and neither llama-server nor the tunnel is started until
`aipotluck-local-client login` sets logged_in true and restarts the service. `logout` reverses it.
See aipotluck/installer/cli.py.

It exposes start() / stop() / wait() so callers control the lifecycle
without needing to know how each OS delivers a "please stop" signal.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aipotluck import diagnostics  # noqa: E402
from aipotluck.service.llama_supervisor import LlamaSupervisor  # noqa: E402
from aipotluck.service.newt_supervisor import NewtSupervisor  # noqa: E402

SERVICE_NAME = "aipotluck"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8069  # distinct from llama-server's own default port 8080

log = logging.getLogger("aipotluck.service")


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

    # llama-server defaults to 4 parallel slots with no flag of our own controlling it -- and
    # --ctx-size sets context PER SLOT, not divided across them (confirmed live: "--ctx-size 4096"
    # with the default slot count logs "n_slots = 4, n_ctx_slot = 4096"), so total KV cache memory
    # scales as slots * ctx_size. On a single-user local device, cutting slots to 1 recovers up to
    # 4x the usable per-request context for the same memory budget -- the real lever behind "the
    # model supports more context than my request allows," confirmed on a real device (CUR-1965).
    parallel = llama_cfg.get("parallel")
    if parallel:
        args += ["--parallel", str(parallel)]

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


def build_newt_supervisor(runtime_config: dict, log_dir: Path | None) -> NewtSupervisor | None:
    """`None` when this install has no `tunnel` section -- true for every device until it's paired
    via `aipotluck-local-client login`, and again after `logout` drops the section."""
    tunnel_cfg = runtime_config.get("tunnel")
    if not tunnel_cfg:
        return None

    newt_binary = Path(tunnel_cfg["binary"])
    if not newt_binary.exists():
        log.error("newt binary not found at %s", newt_binary)
        return None

    return NewtSupervisor(
        newt_binary=newt_binary,
        tunnel_id=tunnel_cfg["id"],
        tunnel_secret=tunnel_cfg["secret"],
        tunnel_endpoint=tunnel_cfg["endpoint"],
        log_dir=log_dir,
    )


class _StatusHandler(BaseHTTPRequestHandler):
    """Bound to a running AipotluckServiceRunner via class attributes set
    at server construction time (http.server handlers are instantiated
    per-request, so state has to live on the class or be injected via a
    handler factory -- we use a factory, see AipotluckServiceRunner._make_handler)."""

    runner: "AipotluckServiceRunner"

    def log_message(self, format: str, *args) -> None:  # quiet default stderr logging
        log.info("%s - %s", self.address_string(), format % args)

    def _write_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _redacted_runtime_config(runtime_config: dict) -> dict:
        """/status is unauthenticated on localhost -- never echo the tunnel secret back out."""
        if "tunnel" not in runtime_config:
            return runtime_config
        sanitized = dict(runtime_config)
        sanitized["tunnel"] = {**runtime_config["tunnel"], "secret": "<redacted>"}
        return sanitized

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
            logged_in = bool(self.runner.runtime_config.get("logged_in"))
            supervisor = self.runner.supervisor
            if supervisor:
                llama_info = supervisor.info().__dict__
            elif logged_in:
                llama_info = {"error": "supervisor not started"}
            else:
                llama_info = None
            newt_supervisor = self.runner.newt_supervisor
            tunnel_info = newt_supervisor.info().__dict__ if newt_supervisor else None
            self._write_json(
                {
                    "service": SERVICE_NAME,
                    "status": "running",
                    "logged_in": logged_in,
                    "runtime_config": self._redacted_runtime_config(self.runner.runtime_config),
                    "llama_server": llama_info,
                    "tunnel": tunnel_info,
                }
            )
            return

        if self.path == "/capabilities":
            # No secrets in here (unlike runtime_config's tunnel section, see _redacted_runtime_config
            # above) -- everything reported is host hardware/software facts and the installer's own
            # public decision logic, so this is unauthenticated on localhost the same as /status.
            fingerprint = diagnostics.gather_fingerprint(
                config_dir=self.runner.config_dir, runtime_config=self.runner.runtime_config
            )
            self._write_json(fingerprint)
            return

        self.send_response(404)
        self.end_headers()


class AipotluckServiceRunner:
    """OS-agnostic service body: HTTP status server + LlamaSupervisor.

    Usage:
        runner = AipotluckServiceRunner(host, port, config_dir, log_dir)
        runner.start()   # non-blocking: spawns the HTTP server + supervisor
        runner.wait()    # blocking: returns once stop() is called elsewhere
        runner.stop()    # idempotent; call from a signal handler or an
                          # SCM stop callback (pywin32 SvcStop)
    """

    def __init__(self, host: str, port: int, config_dir: Path, log_dir: Path | None) -> None:
        self.host = host
        self.port = port
        self.config_dir = config_dir
        self.log_dir = log_dir

        self.runtime_config: dict = {}
        self.supervisor: LlamaSupervisor | None = None
        self.newt_supervisor: NewtSupervisor | None = None
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        self.runtime_config = load_runtime_config(self.config_dir)

        # Every fresh install is logged out (aipotluck.installer.install writes "logged_in": false and no
        # "tunnel" section) -- hold both llama-server and newt back entirely until `python -m
        # `aipotluck-local-client login` flips this and restarts the service. This is what lets the public
        # one-line installer take zero arguments: it never needs credentials in hand to finish.
        if self.runtime_config.get("logged_in"):
            self.supervisor = build_supervisor(self.runtime_config, self.log_dir)
            if self.supervisor:
                log.info("Starting llama-server supervisor")
                self.supervisor.start()
            else:
                log.error("llama-server supervisor could not be created; service will run without it")

            self.newt_supervisor = build_newt_supervisor(self.runtime_config, self.log_dir)
            if self.newt_supervisor:
                log.info("Starting newt supervisor")
                self.newt_supervisor.start()
        else:
            log.info(
                "Device is logged out -- llama-server and the tunnel will not start until you run "
                "`aipotluck-local-client login`."
            )

        runner = self

        class BoundHandler(_StatusHandler):
            pass

        BoundHandler.runner = runner

        self._server = ThreadingHTTPServer((self.host, self.port), BoundHandler)
        log.info("aipotluck service listening on http://%s:%s", self.host, self.port)

        self._server_thread = threading.Thread(
            target=self._server.serve_forever, name="aipotluck-http", daemon=True
        )
        self._server_thread.start()

    def wait(self) -> None:
        """Block the calling thread until stop() has fully completed."""
        self._stopped.wait()

    def stop(self, timeout: float = 20.0) -> None:
        if self._stopped.is_set():
            return
        log.info("Stopping aipotluck service")
        if self._server:
            self._server.shutdown()
            if self._server_thread:
                self._server_thread.join(timeout=5)
            self._server.server_close()
            self._server = None
        if self.supervisor:
            self.supervisor.stop(timeout=timeout)
        if self.newt_supervisor:
            self.newt_supervisor.stop(timeout=timeout)
        log.info("aipotluck service stopped")
        self._stopped.set()
