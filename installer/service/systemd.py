"""Linux service backend: systemd user unit by default, --system for a
machine-wide unit under /etc/systemd/system (requires sudo)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from installer.service.base import ServiceManager, ServiceState, ServiceStatus

log = logging.getLogger("aipotluck.installer.service.systemd")

USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
SYSTEM_UNIT_DIR = Path("/etc/systemd/system")

UNIT_TEMPLATE = """[Unit]
Description=aipotluck local inference client service
After=network.target

[Service]
Type=simple
ExecStart={exec_line}
Restart=on-failure
RestartSec=3
{working_dir_line}{log_lines}
[Install]
WantedBy={wanted_by}
"""


def _unit_path(name: str, system_scope: bool) -> Path:
    unit_dir = SYSTEM_UNIT_DIR if system_scope else USER_UNIT_DIR
    return unit_dir / f"{name}.service"


def _systemctl(args: list[str], system_scope: bool) -> subprocess.CompletedProcess:
    if shutil.which("systemctl") is None:
        log.warning("systemctl not found on PATH; skipping: systemctl %s", " ".join(args))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="systemctl not available")
    base = ["systemctl"] if system_scope else ["systemctl", "--user"]
    return subprocess.run(base + args, capture_output=True, text=True)


class SystemdServiceManager(ServiceManager):
    def install(
        self,
        name: str,
        exec_path: Path,
        args: list[str],
        *,
        system_scope: bool = False,
        working_dir: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        unit_path = _unit_path(name, system_scope)
        unit_path.parent.mkdir(parents=True, exist_ok=True)

        exec_line = " ".join([str(exec_path)] + [str(a) for a in args])
        working_dir_line = f"WorkingDirectory={working_dir}\n" if working_dir else ""
        log_lines = ""
        if log_dir:
            log_lines = (
                f"StandardOutput=append:{log_dir}/{name}.out.log\n"
                f"StandardError=append:{log_dir}/{name}.err.log\n"
            )
        wanted_by = "multi-user.target" if system_scope else "default.target"

        unit_content = UNIT_TEMPLATE.format(
            exec_line=exec_line,
            working_dir_line=working_dir_line,
            log_lines=log_lines,
            wanted_by=wanted_by,
        )
        unit_path.write_text(unit_content, encoding="utf-8")
        log.info("Wrote unit file: %s", unit_path)

        self._daemon_reload(system_scope)
        _systemctl(["enable", f"{name}.service"], system_scope)

        if not system_scope:
            log.info(
                "Installed as a systemd --user unit. It starts at login. "
                "To start at boot without login, run: "
                "loginctl enable-linger %s",
                __import__("getpass").getuser(),
            )

    def uninstall(self, name: str, *, system_scope: bool = False) -> None:
        _systemctl(["disable", "--now", f"{name}.service"], system_scope)
        unit_path = _unit_path(name, system_scope)
        if unit_path.exists():
            unit_path.unlink()
        self._daemon_reload(system_scope)

    def start(self, name: str, *, system_scope: bool = False) -> None:
        result = _systemctl(["start", f"{name}.service"], system_scope)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start {name}: {result.stderr.strip()}")

    def stop(self, name: str, *, system_scope: bool = False) -> None:
        _systemctl(["stop", f"{name}.service"], system_scope)

    def status(self, name: str, *, system_scope: bool = False) -> ServiceStatus:
        unit_path = _unit_path(name, system_scope)
        if not unit_path.exists():
            return ServiceStatus(ServiceState.NOT_INSTALLED, detail=str(unit_path))

        result = _systemctl(["is-active", f"{name}.service"], system_scope)
        active = result.stdout.strip()
        if active == "active":
            return ServiceStatus(ServiceState.RUNNING, detail=active)
        if active in ("inactive", "failed", "activating", "deactivating"):
            return ServiceStatus(ServiceState.STOPPED, detail=active)
        return ServiceStatus(ServiceState.UNKNOWN, detail=active or result.stderr.strip())

    @staticmethod
    def _daemon_reload(system_scope: bool) -> None:
        if shutil.which("systemctl") is None:
            log.warning("systemctl not found on PATH; skipping daemon-reload")
            return
        _systemctl(["daemon-reload"], system_scope)
