"""Platform, architecture, and GPU-backend detection.

Everything here is best-effort and non-fatal: if a probe fails or is
inconclusive, we fall back to a safe default (plain CPU asset for the
detected OS/arch). Detection never raises for a supported OS; unsupported
OSes raise UnsupportedPlatformError with a clear message.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass


class UnsupportedPlatformError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostProfile:
    os_name: str      # "linux" | "macos" | "windows"
    arch: str         # "x64" | "arm64"
    backend: str      # "cpu" | "cuda" | "vulkan" | "rocm"

    @property
    def asset_key(self) -> str:
        return f"{self.os_name}-{self.arch}-{self.backend}"


def detect_os() -> str:
    system = platform.system()
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    raise UnsupportedPlatformError(f"Unsupported OS: {system!r}")


def detect_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    raise UnsupportedPlatformError(f"Unsupported architecture: {machine!r}")


def _has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def _cmd_ok(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_gpu_backend(os_name: str, arch: str, requested: str = "auto") -> str:
    """Return one of "cpu" | "cuda" | "vulkan" | "rocm".

    requested: "auto" to probe, or an explicit override which is returned
    as-is (validated later against the asset manifest).
    """
    if requested != "auto":
        return requested

    if os_name == "macos":
        # Metal is baked into the standard macOS asset; nothing to pick.
        return "cpu"

    if os_name == "windows":
        # GPU asset variants for Windows are not wired into the manifest
        # yet (stubbed intentionally per project phase) -- default to CPU.
        return "cpu"

    if os_name == "linux":
        if _has_cmd("nvidia-smi") and _cmd_ok(["nvidia-smi"]):
            return "cuda"
        if _has_cmd("rocminfo") and _cmd_ok(["rocminfo"]):
            return "rocm"
        # Vulkan is a reasonable generic GPU fallback but a working Vulkan
        # loader doesn't guarantee a usable inference backend, and picking
        # it wrong is worse than CPU (which always works). Only opt in to
        # vulkan when explicitly requested by the caller.
        return "cpu"

    return "cpu"


def detect_host_profile(requested_backend: str = "auto") -> HostProfile:
    os_name = detect_os()
    arch = detect_arch()
    backend = detect_gpu_backend(os_name, arch, requested_backend)
    return HostProfile(os_name=os_name, arch=arch, backend=backend)
