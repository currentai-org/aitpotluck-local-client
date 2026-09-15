"""macOS service backend: launchd. STUBBED -- not exercised in this
environment (no macOS host to test against). Implementation follows the
documented launchctl/plist conventions but has not been run.

Default (user/agent scope): ~/Library/LaunchAgents/<label>.plist, loaded via
`launchctl load -w`. Starts at login.

--system scope: /Library/LaunchDaemons/<label>.plist, requires sudo, starts
at boot without login.
"""

from __future__ import annotations

import logging
import plistlib
import subprocess
from pathlib import Path

from installer.service.base import ServiceManager, ServiceState, ServiceStatus

log = logging.getLogger("aipotluck.installer.service.launchd")

LABEL_PREFIX = "com.aipotluck"

USER_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
SYSTEM_DAEMON_DIR = Path("/Library/LaunchDaemons")


def _label(name: str) -> str:
    return f"{LABEL_PREFIX}.{name}"


def _plist_path(name: str, system_scope: bool) -> Path:
    plist_dir = SYSTEM_DAEMON_DIR if system_scope else USER_AGENT_DIR
    return plist_dir / f"{_label(name)}.plist"


class LaunchdServiceManager(ServiceManager):
    """STUB: implemented per Apple docs, not tested on real macOS hardware."""

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
        plist_path = _plist_path(name, system_scope)
        plist_path.parent.mkdir(parents=True, exist_ok=True)

        program_args = [str(exec_path)] + [str(a) for a in args]
        plist_dict = {
            "Label": _label(name),
            "ProgramArguments": program_args,
            "RunAtLoad": True,
            "KeepAlive": True,
        }
        if working_dir:
            plist_dict["WorkingDirectory"] = str(working_dir)
        if log_dir:
            plist_dict["StandardOutPath"] = str(log_dir / f"{name}.out.log")
            plist_dict["StandardErrorPath"] = str(log_dir / f"{name}.err.log")

        with open(plist_path, "wb") as fh:
            plistlib.dump(plist_dict, fh)
        log.info("Wrote launchd plist: %s (STUB, untested)", plist_path)

        self._launchctl(["load", "-w", str(plist_path)])

    def uninstall(self, name: str, *, system_scope: bool = False) -> None:
        plist_path = _plist_path(name, system_scope)
        if plist_path.exists():
            self._launchctl(["unload", "-w", str(plist_path)])
            plist_path.unlink()

    def start(self, name: str, *, system_scope: bool = False) -> None:
        self._launchctl(["start", _label(name)])

    def stop(self, name: str, *, system_scope: bool = False) -> None:
        self._launchctl(["stop", _label(name)])

    def status(self, name: str, *, system_scope: bool = False) -> ServiceStatus:
        plist_path = _plist_path(name, system_scope)
        if not plist_path.exists():
            return ServiceStatus(ServiceState.NOT_INSTALLED, detail=str(plist_path))
        result = self._launchctl(["list", _label(name)])
        if result.returncode == 0:
            return ServiceStatus(ServiceState.RUNNING, detail="launchctl list succeeded")
        return ServiceStatus(ServiceState.STOPPED, detail=result.stderr.strip())

    @staticmethod
    def _launchctl(args: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["launchctl"] + args, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise RuntimeError("launchctl not found -- not running on macOS?") from exc
