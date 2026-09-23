"""ServiceManager abstraction: install/uninstall/start/stop/status across OSes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ServiceState(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    detail: str = ""


class ServiceManager(ABC):
    """Concrete subclasses implement OS-native service registration.

    All methods operate on `name` (a stable service identifier, e.g.
    "aipotluck") and are expected to be idempotent: calling install() twice
    should not error, uninstall() on a missing service should not error.
    """

    @abstractmethod
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
        ...

    @abstractmethod
    def uninstall(self, name: str, *, system_scope: bool = False) -> None:
        ...

    @abstractmethod
    def start(self, name: str, *, system_scope: bool = False) -> None:
        ...

    @abstractmethod
    def stop(self, name: str, *, system_scope: bool = False) -> None:
        ...

    @abstractmethod
    def status(self, name: str, *, system_scope: bool = False) -> ServiceStatus:
        ...


def get_service_manager(os_name: str) -> ServiceManager:
    if os_name == "linux":
        from aipotluck.installer.service.systemd import SystemdServiceManager
        return SystemdServiceManager()
    if os_name == "macos":
        from aipotluck.installer.service.launchd import LaunchdServiceManager
        return LaunchdServiceManager()
    if os_name == "windows":
        from aipotluck.installer.service.windows_service import WindowsServiceManager
        return WindowsServiceManager()
    raise ValueError(f"No service manager for os_name={os_name!r}")
