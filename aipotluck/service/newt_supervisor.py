"""Supervises the `newt` (Pangolin tunnel client) child process: start,
restart on crash with exponential backoff, and clean shutdown.

Cloned from llama_supervisor.py's shape (same rationale: the OS service
manager supervises this Python process, this class supervises the child
process it owns -- two tiers of self-healing). The one real difference:
newt has no local HTTP health endpoint of its own, so "healthy" here is
just "the process is currently running" -- there is no equivalent of
llama_supervisor's `/health` poll to wait on at startup or to watch for a
silent unhealthy-but-still-running state.

  systemd (Restart=on-failure) -> aipotluck_service.py -> NewtSupervisor -> newt
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("aipotluck.service.newt_supervisor")

BACKOFF_SCHEDULE = [1, 2, 5, 10, 20, 30, 60]
STABLE_UPTIME_RESET_SECONDS = 120
POLL_INTERVAL_SECONDS = 5


@dataclass
class NewtProcessInfo:
    pid: int | None = None
    running: bool = False
    restart_count: int = 0
    last_exit_code: int | None = None
    last_started_at: float | None = None
    last_error: str = ""


class NewtSupervisor:
    """Runs `newt` as a monitored child process in a background thread.
    Call start() once; stop() is safe to call multiple times."""

    def __init__(
        self,
        newt_binary: Path,
        *,
        tunnel_id: str,
        tunnel_secret: str,
        tunnel_endpoint: str,
        log_dir: Path | None = None,
        env: dict | None = None,
    ) -> None:
        self.newt_binary = newt_binary
        self.tunnel_id = tunnel_id
        self.tunnel_secret = tunnel_secret
        self.tunnel_endpoint = tunnel_endpoint
        self.log_dir = log_dir
        self.env = env

        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._info = NewtProcessInfo()
        self._log_file = None
        self._terminating = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            log.warning("Newt supervisor already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="newt-supervisor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop_event.set()
        self._terminate_process(timeout=timeout)
        if self._thread:
            self._thread.join(timeout=timeout + 5)
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def info(self) -> NewtProcessInfo:
        with self._lock:
            return NewtProcessInfo(**vars(self._info))

    # -- internals --

    def _run_loop(self) -> None:
        backoff_idx = 0
        while not self._stop_event.is_set():
            started_at = time.monotonic()
            try:
                self._spawn_process()
            except Exception as exc:  # binary missing, permissions, etc.
                log.error("Failed to spawn newt: %s", exc)
                with self._lock:
                    self._info.last_error = str(exc)
                if self._stop_event.wait(BACKOFF_SCHEDULE[backoff_idx]):
                    break
                backoff_idx = min(backoff_idx + 1, len(BACKOFF_SCHEDULE) - 1)
                continue

            exit_code = self._wait_for_exit()
            uptime = time.monotonic() - started_at

            if self._stop_event.is_set():
                break

            with self._lock:
                self._info.running = False
                self._info.last_exit_code = exit_code
                self._info.restart_count += 1

            log.warning(
                "newt exited (code=%s, uptime=%.1fs); restarting (attempt #%d)",
                exit_code, uptime, self._info.restart_count,
            )

            if uptime >= STABLE_UPTIME_RESET_SECONDS:
                backoff_idx = 0
            delay = BACKOFF_SCHEDULE[backoff_idx]
            log.info("Waiting %ss before restart", delay)
            if self._stop_event.wait(delay):
                break
            backoff_idx = min(backoff_idx + 1, len(BACKOFF_SCHEDULE) - 1)

        log.info("Newt supervisor loop exiting")

    def _spawn_process(self) -> None:
        cmd = [
            str(self.newt_binary),
            "--id", self.tunnel_id,
            "--secret", self.tunnel_secret,
            "--endpoint", self.tunnel_endpoint,
        ]
        # Never log the secret.
        log.info(
            "Starting newt: %s --id %s --secret *** --endpoint %s",
            self.newt_binary, self.tunnel_id, self.tunnel_endpoint,
        )

        stdout_target = subprocess.DEVNULL
        stderr_target = subprocess.DEVNULL
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.log_dir / "newt.log", "a", encoding="utf-8")
            stdout_target = self._log_file
            stderr_target = self._log_file

        proc = subprocess.Popen(
            cmd,
            stdout=stdout_target,
            stderr=stderr_target,
            env=self.env,
        )
        with self._lock:
            self._proc = proc
            self._info.pid = proc.pid
            self._info.running = True
            self._info.last_started_at = time.time()
            self._info.last_error = ""

    def _wait_for_exit(self) -> int | None:
        """Block until the process exits OR stop is requested (in which case we terminate it
        ourselves). No health check to poll -- "running" IS "healthy" here, unlike
        LlamaSupervisor, since newt exposes no local HTTP endpoint of its own."""
        while True:
            if self._proc is None:
                return None
            try:
                exit_code = self._proc.wait(timeout=POLL_INTERVAL_SECONDS)
                with self._lock:
                    self._proc = None
                return exit_code
            except subprocess.TimeoutExpired:
                pass

            if self._stop_event.is_set():
                self._terminate_process()
                return self._info.last_exit_code

    def _terminate_process(self, timeout: float = 15.0) -> None:
        with self._lock:
            proc = self._proc
            already_stopping = self._terminating
            self._terminating = True
        if proc is None or proc.poll() is not None:
            with self._lock:
                self._info.running = False
                self._terminating = False
            return
        if already_stopping:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
            return
        log.info("Terminating newt (pid=%s)", proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("newt did not exit in %ss; killing", timeout)
            proc.kill()
            proc.wait(timeout=5)
        with self._lock:
            self._info.running = False
            self._info.last_exit_code = proc.returncode
            self._proc = None
            self._terminating = False
