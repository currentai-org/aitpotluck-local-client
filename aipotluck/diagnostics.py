"""Live system/capability fingerprint, exposed by the service's GET /capabilities endpoint
(aipotluck/service/runner.py).

Reuses exactly the same detection primitives the installer itself uses to decide prebuilt vs.
custom-cache vs. source-build (aipotluck.installer.platform_detect/build_strategy/build_cache/
source_build), so this endpoint and the installer can never silently disagree about what a host is
capable of -- this is a read-only report of the same facts install.py acts on, not a second,
parallel implementation of them.

Every section is gathered independently and defensively: one probe failing (an unreadable
/proc file, a missing manifest, a raised UnsupportedPlatformError on some future OS) must never
take down the rest of the response. This endpoint's whole purpose is to answer "why did the
installer do what it did on this box" -- including boxes that are already in a broken or unusual
state -- so it has to stay useful precisely when something else is wrong.
"""

from __future__ import annotations

import ctypes.util
import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from aipotluck.installer import build_cache, build_strategy, fetch, source_build
from aipotluck.installer.platform_detect import (
    HostProfile,
    UnsupportedPlatformError,
    detect_arch,
    detect_cuda_compute_capability,
    detect_glibc_version,
    detect_gpu_backend,
    detect_os,
)

log = logging.getLogger("aipotluck.diagnostics")

REPO_ROOT = Path(__file__).resolve().parent.parent
FINGERPRINT_VERSION = 1


def _safe(section_name: str, fn, *args, **kwargs) -> Any:
    """Run one detection probe; on any exception, log it and report the failure inline rather than
    letting it take down the whole /capabilities response."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see module docstring
        log.warning("diagnostics: %s probe failed: %s", section_name, exc, exc_info=True)
        return {"error": str(exc)}


def _os_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
    }
    try:
        info["name"] = detect_os()
    except UnsupportedPlatformError as exc:
        info["name"] = None
        info["detect_error"] = str(exc)

    distro_path = Path("/etc/os-release")
    if distro_path.exists():
        distro: dict[str, str] = {}
        try:
            for line in distro_path.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    distro[key] = value.strip().strip('"')
        except OSError:
            distro = {}
        info["distro"] = distro or None
    else:
        info["distro"] = None
    return info


def _arch_info() -> dict[str, Any]:
    info: dict[str, Any] = {"raw_machine": platform.machine()}
    try:
        info["normalized"] = detect_arch()
    except UnsupportedPlatformError as exc:
        info["normalized"] = None
        info["detect_error"] = str(exc)
    return info


def _libc_info() -> dict[str, Any]:
    lib, version = platform.libc_ver()
    parsed = detect_glibc_version()
    return {
        "family": lib or None,
        "version": version or None,
        "version_tuple": list(parsed) if parsed else None,
    }


def _cpu_model() -> str | None:
    path = Path("/proc/cpuinfo")
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def _cpu_info() -> dict[str, Any]:
    return {
        "logical_cores": os.cpu_count(),
        "model": _cpu_model(),
        "available_memory_gb": source_build._available_memory_gb(),
        "total_memory_gb": _total_memory_gb(),
    }


def _total_memory_gb() -> float | None:
    path = Path("/proc/meminfo")
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / (1024**2)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _disk_info(reference_path: Path) -> dict[str, Any]:
    return {
        "free_gb_at": str(reference_path),
        "free_gb": source_build.check_free_disk_gb(reference_path),
        "min_required_for_source_build_gb": source_build.MIN_FREE_DISK_GB,
    }


def _gpu_info(os_name: str | None, arch: str | None) -> dict[str, Any]:
    detected_backend = None
    if os_name and arch:
        detected_backend = detect_gpu_backend(os_name, arch, "auto")
    return {
        "detected_backend": detected_backend,
        "has_nvidia_smi": shutil.which("nvidia-smi") is not None,
        "cuda_compute_capability": detect_cuda_compute_capability(),
        "has_rocminfo": shutil.which("rocminfo") is not None,
    }


def _build_tools_info(*, want_cuda: bool) -> dict[str, Any]:
    nvcc = source_build.find_nvcc()
    glslc = source_build.find_glslc()
    hipcc = source_build.find_hipcc()
    return {
        "cmake": shutil.which("cmake") is not None,
        "git": shutil.which("git") is not None,
        "cxx_compiler": source_build._find_cxx_compiler(),
        "openssl_headers": source_build._has_openssl_headers(),
        "libgomp": ctypes.util.find_library("gomp") is not None,
        "nvcc": str(nvcc) if nvcc else None,
        "glslc": glslc,
        "vulkan_headers": source_build._has_vulkan_headers(),
        "hipcc": hipcc,
        "missing_apt_packages": source_build.missing_apt_packages(),
    }


def _install_strategy_info(profile: HostProfile) -> dict[str, Any]:
    manifest_path = REPO_ROOT / "llama_version.json"
    if not manifest_path.exists():
        return {"error": f"{manifest_path} not found"}
    manifest = fetch.load_version_manifest(manifest_path)
    strategy = build_strategy.resolve_install_strategy(profile, manifest)

    custom_manifest = build_cache.load_custom_manifest(REPO_ROOT / "llama_custom_builds.json")
    custom_match = None
    if strategy.use_source_build:
        custom_match = build_cache.find_custom_build(profile, custom_manifest, cuda_arch=strategy.cuda_arch)

    return {
        "pinned_tag": manifest.get("tag"),
        "resolved_backend": profile.backend,
        "use_source_build": strategy.use_source_build,
        "reason": strategy.reason,
        "upstream_asset_key": strategy.asset_key,
        "cuda_arch": strategy.cuda_arch,
        "custom_cache_key": custom_match[0] if custom_match else None,
        "custom_cache_available": custom_match is not None,
        "recommended_build": (
            {
                "jobs": source_build.recommended_jobs(want_cuda=strategy.cuda_arch is not None),
                "cmake_cuda_architectures": strategy.cuda_arch,
            }
            if strategy.use_source_build and custom_match is None
            else None
        ),
    }


_RUNTIME_PARAM_FIELDS = (
    "ctx_size",
    "gpu_layers",
    "parallel",
    "cache_type_k",
    "cache_type_v",
    "host",
    "port",
    "model_hf",
    "model_path",
)


def runtime_params(runtime_config: dict[str, Any]) -> dict[str, Any]:
    """A clean, explicit view of the llama-server parameters actually in effect right now, plus
    -- for any of them that were computed rather than defaulted or user-specified -- WHY, via the
    optional `llama_cpp.tuning` map runtime.json can carry: {param_name: "reason string"}. A
    param's absence from `tuning` means it's a static default or an explicit user override, not
    that nothing is known about it.

    This is the traceability surface CLAUDE.md's "Runtime parameters" convention requires: any
    runtime parameter this project computes automatically (the auto-sizing feature this is
    groundwork for writes into `tuning`) must be visible here -- and only here. GET /status
    (`aipotluck/service/runner.py`, which imports this exact function rather than a copy) and
    `aipotluck-local-client status` both surface this dict as-is, and /capabilities' current_install
    section below embeds it too -- local and remote read the same source, never a second one that
    could drift from it.
    """
    llama_cfg = runtime_config.get("llama_cpp") or {}
    params = {field: llama_cfg.get(field) for field in _RUNTIME_PARAM_FIELDS}
    params["tuning"] = llama_cfg.get("tuning") or {}
    return params


def _current_install_info(runtime_config: dict[str, Any]) -> dict[str, Any]:
    llama_cfg = runtime_config.get("llama_cpp") or {}
    return {
        "tag": llama_cfg.get("tag"),
        "asset_key": llama_cfg.get("asset_key"),
        "built_from_source": llama_cfg.get("built_from_source"),
        "server_binary": llama_cfg.get("server_binary"),
        "server_binary_exists": Path(llama_cfg["server_binary"]).exists() if llama_cfg.get("server_binary") else None,
        "logged_in": bool(runtime_config.get("logged_in")),
        "runtime_params": runtime_params(runtime_config),
    }


def gather_fingerprint(*, config_dir: Path | None, runtime_config: dict[str, Any] | None) -> dict[str, Any]:
    """The full capability report. Every top-level section is independently guarded (see _safe) --
    a probe failing degrades that one section to {"error": "..."} rather than 500ing the endpoint.
    """
    runtime_config = runtime_config or {}
    os_info = _safe("os", _os_info)
    arch_info = _safe("arch", _arch_info)
    os_name = os_info.get("name") if isinstance(os_info, dict) else None
    arch = arch_info.get("normalized") if isinstance(arch_info, dict) else None

    profile = None
    if os_name and arch:
        backend = _safe("gpu_backend_for_strategy", detect_gpu_backend, os_name, arch, "auto")
        if isinstance(backend, str):
            profile = HostProfile(os_name=os_name, arch=arch, backend=backend)

    return {
        "service": "aipotluck",
        "fingerprint_version": FINGERPRINT_VERSION,
        "os": os_info,
        "arch": arch_info,
        "libc": _safe("libc", _libc_info),
        "cpu": _safe("cpu", _cpu_info),
        "disk": _safe("disk", _disk_info, config_dir or Path.home()),
        "gpu": _safe("gpu", _gpu_info, os_name, arch),
        "build_tools": _safe(
            "build_tools", _build_tools_info, want_cuda=bool(profile and profile.backend == "cuda")
        ),
        "install_strategy": (
            _safe("install_strategy", _install_strategy_info, profile)
            if profile is not None
            else {"error": "could not determine a host profile (unsupported os/arch)"}
        ),
        "current_install": _safe("current_install", _current_install_info, runtime_config),
    }
