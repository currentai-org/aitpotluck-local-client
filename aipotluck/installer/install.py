#!/usr/bin/env python3
"""aipotluck-local-client installer.

Usage:
    python -m aipotluck.installer.install [options]

Flow:
    1. Detect platform/arch/GPU-backend.
    2. Decide whether a prebuilt llama.cpp release asset is actually viable for this host, or
       whether it needs to be built from source instead (build_strategy.py) -- e.g. every
       Jetson-class arm64+CUDA board: nvidia-smi is real there, but no linux-arm64-cuda asset is
       ever published upstream, and even the linux-arm64-cpu asset needs a newer glibc than
       JetPack ships. See build_strategy.py's module docstring for the confirmed evidence.
    3a. Prebuilt path: download (cached, checksum-verified) + extract it.
    3b. Source-build path: check the host actually has what a build needs (never silently `sudo
        apt-get install`), then configure + build llama-server from vendor/llama.cpp
        (source_build.py).
    4. Write runtime.json pointing at the resolved llama-server binary.
    5. Install our blank Python service via the OS-native service manager.
    6. Install the `aipotluck-local-client` CLI shim onto PATH (see install_cli_shim below).
    7. Optionally start the service (default: yes).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aipotluck.installer import build_strategy, fetch, layout, source_build
from aipotluck.installer.platform_detect import HostProfile, detect_host_profile
from aipotluck.installer.python_bootstrap import PythonNotFoundError, ensure_python
from aipotluck.installer.service.base import ServiceState, get_service_manager
from aipotluck.installer.cli_shim import CLI_SHIM_NAME, install_cli_shim

log = logging.getLogger("aipotluck.installer")

SERVICE_NAME = "aipotluck"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8080
DEFAULT_MODEL_HF = "bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aipotluck-installer",
        description="Install llama.cpp + the aipotluck local service.",
    )
    parser.add_argument("--tag", default=None, help="Override the pinned llama.cpp release tag")
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "cpu", "cuda", "vulkan", "rocm"],
        help="GPU backend to use (default: auto-detect, falls back to cpu)",
    )
    parser.add_argument(
        "--install-dir", type=Path, default=None,
        help="Override the default per-OS install root",
    )
    parser.add_argument(
        "--system", action="store_true",
        help="Install machine-wide (requires elevation); default is per-user, no admin",
    )
    parser.add_argument(
        "--no-start", action="store_true",
        help="Install the service but do not start it",
    )
    parser.add_argument(
        "--no-service", action="store_true",
        help="Only fetch/extract llama.cpp; skip installing the Python service "
             "(useful for CI / dry runs on a host with no service manager)",
    )
    parser.add_argument(
        "--via", default="direct", choices=["direct"],
        help="Install method. Only 'direct' (our own download+verify) is "
             "implemented; 'brew'/'winget' passthroughs are future work.",
    )
    model_group = parser.add_mutually_exclusive_group()
    model_group.add_argument(
        "--model-hf", default=DEFAULT_MODEL_HF,
        help="Hugging Face repo[:quant] passed straight to llama-server's "
             "own -hf downloader, e.g. 'bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M' "
             f"(default: {DEFAULT_MODEL_HF} -- a small placeholder model so the "
             "service has something to supervise out of the box; override for real use)",
    )
    model_group.add_argument(
        "--model-path", type=Path, default=None,
        help="Path to a local GGUF file, instead of fetching from Hugging Face",
    )
    parser.add_argument("--ctx-size", type=int, default=4096, help="llama-server context size (-c)")
    parser.add_argument(
        "--gpu-layers", default="auto",
        help="llama-server -ngl value: an integer, 'auto', or 'all' (default: auto)",
    )
    parser.add_argument("--server-host", default=DEFAULT_SERVER_HOST, help="llama-server bind host")
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT, help="llama-server bind port")

    parser.add_argument(
        "--no-source-build", action="store_true",
        help="Fail instead of building llama-server from source when no prebuilt release asset is "
             "viable for this host (e.g. Jetson-class arm64+CUDA boards, or an old-glibc Linux host) "
             "-- useful in CI/dry-run contexts that want to know about the gap rather than sit "
             "through a build that can take well over an hour",
    )
    parser.add_argument(
        "--jobs", type=int, default=None,
        help="Parallel build jobs for a from-source build (default: auto, capped by available RAM "
             "-- a naive -j$(nproc) can OOM a low-memory board like a Jetson Orin Nano)",
    )
    parser.add_argument(
        "--build-timeout", type=float, default=source_build.DEFAULT_BUILD_TIMEOUT_SECONDS,
        help=f"Wall-clock ceiling in seconds for a from-source build (default: "
             f"{source_build.DEFAULT_BUILD_TIMEOUT_SECONDS:.0f})",
    )

    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )


def run_install(args: argparse.Namespace) -> int:
    profile: HostProfile = detect_host_profile(args.backend)
    log.info("Detected host profile: os=%s arch=%s backend=%s", profile.os_name, profile.arch, profile.backend)

    lay = layout.get_layout(profile.os_name, system_scope=args.system, override_root=args.install_dir)
    layout.ensure_layout_dirs(lay)
    log.info("Install root: %s", lay.install_root)
    log.info("Config dir:   %s", lay.config_dir)
    log.info("Log dir:      %s", lay.log_dir)

    manifest_path = REPO_ROOT / "llama_version.json"
    manifest = fetch.load_version_manifest(manifest_path)
    if args.tag:
        manifest["tag"] = args.tag
        log.info("Overriding pinned tag -> %s", args.tag)

    strategy = build_strategy.resolve_install_strategy(profile, manifest)
    if strategy.use_source_build:
        log.info("No viable prebuilt asset for this host (%s) -- building llama-server from source", strategy.reason)
        if args.no_source_build:
            log.error(
                "--no-source-build set; refusing to build (%s). Drop that flag to let the "
                "installer build llama-server from source instead.", strategy.reason,
            )
            return 1
        if profile.backend == "cuda" and strategy.cuda_arch is None:
            log.error(
                "CUDA backend requested/detected but the GPU's compute capability could not be "
                "determined (checked `nvidia-smi --query-gpu=compute_cap`) -- can't safely pick a "
                "CMAKE_CUDA_ARCHITECTURES value. Check nvidia-smi works, or pass --backend cpu."
            )
            return 1

        problems = source_build.check_build_prerequisites(want_cuda=strategy.cuda_arch is not None)
        if problems:
            log.error("Can't build llama-server from source -- missing prerequisites:")
            for problem in problems:
                log.error("  - %s", problem)
            log.error("Install the above, then re-run this installer.")
            return 1

        free_gb = source_build.check_free_disk_gb(lay.install_root)
        if free_gb is not None and free_gb < source_build.MIN_FREE_DISK_GB:
            log.error(
                "Only %.1fGB free at %s; a from-source build needs roughly %dGB. Free up space "
                "and re-run.", free_gb, lay.install_root, source_build.MIN_FREE_DISK_GB,
            )
            return 1

        build_root = lay.install_root / "llama.cpp-build"
        log.info(
            "Building llama-server (tag=%s, cuda_arch=%s) -- this can take well over an hour on a "
            "low-power board; output follows live", manifest["tag"], strategy.cuda_arch,
        )
        server_bin = source_build.ensure_llama_server_built(
            REPO_ROOT, build_root, manifest["tag"],
            cuda_arch=strategy.cuda_arch, jobs=args.jobs, timeout=args.build_timeout,
        )
        asset_key = f"source-build:{profile.asset_key}"
        llama_dir = server_bin.parent.parent
    else:
        asset_key, asset_entry = strategy.asset_key, strategy.asset_entry
        log.info("Resolved asset: %s (%s)", asset_key, asset_entry["file"])

        archive_path = fetch.download_asset(manifest, asset_entry, lay.state_dir / "downloads")
        llama_dir = fetch.extract_archive(archive_path, lay.install_root / "llama.cpp", manifest["tag"], asset_key)

        server_bin = fetch.find_binary(llama_dir, "llama-server")

    log.info("llama-server binary: %s", server_bin)

    runtime_config = {
        "llama_cpp": {
            "tag": manifest["tag"],
            "asset_key": asset_key,
            "built_from_source": strategy.use_source_build,
            "install_dir": str(llama_dir),
            "server_binary": str(server_bin),
            "lib_dir": str(server_bin.parent),
            "host": args.server_host,
            "port": args.server_port,
            "ctx_size": args.ctx_size,
            "gpu_layers": args.gpu_layers,
            "model_hf": args.model_hf if args.model_path is None else None,
            "model_path": str(args.model_path) if args.model_path else None,
        },
        "service": {
            "name": SERVICE_NAME,
            "config_dir": str(lay.config_dir),
            "log_dir": str(lay.log_dir),
        },
        # Every install starts logged out -- no Pangolin credentials, so the service (runner.py)
        # holds both llama-server and newt back until `aipotluck-local-client login` sets this
        # true. This is what lets the public one-line installer (install.sh) take zero arguments:
        # it never needs a tunnel id/secret/endpoint in hand to finish installing.
        "logged_in": False,
    }
    runtime_path = lay.config_dir / "runtime.json"
    runtime_path.write_text(json.dumps(runtime_config, indent=2), encoding="utf-8")
    log.info("Wrote runtime config: %s", runtime_path)

    if args.no_service:
        log.info("--no-service set; skipping Python service install and the CLI PATH shim.")
        _print_summary(profile, lay, runtime_config, service_status=None, shim_path=None)
        return 0

    service_mgr = get_service_manager(profile.os_name)
    service_script = REPO_ROOT / "aipotluck" / "service" / "aipotluck_service.py"
    service_args = [
        "--config-dir", str(lay.config_dir),
        "--log-dir", str(lay.log_dir),
    ]
    try:
        python_exe = ensure_python()
    except PythonNotFoundError as exc:
        log.error("%s", exc)
        raise
    log.info("Using Python interpreter for service: %s", python_exe)
    service_mgr.install(
        SERVICE_NAME,
        exec_path=python_exe,
        args=[str(service_script)] + service_args,
        system_scope=args.system,
        working_dir=REPO_ROOT,
        log_dir=lay.log_dir,
    )
    log.info("Installed %s service (system_scope=%s)", SERVICE_NAME, args.system)

    shim_path = install_cli_shim(profile, python_exe, args.system)

    if not args.no_start:
        service_mgr.start(SERVICE_NAME, system_scope=args.system)
        log.info("Started %s service", SERVICE_NAME)

    status = service_mgr.status(SERVICE_NAME, system_scope=args.system)
    _print_summary(profile, lay, runtime_config, service_status=status, shim_path=shim_path)

    if status.state not in (ServiceState.RUNNING, ServiceState.STOPPED):
        log.warning("Service status is ambiguous: %s (%s)", status.state, status.detail)

    return 0


def _print_summary(
    profile: HostProfile, lay: layout.Layout, runtime_config: dict, service_status, shim_path: Path | None
) -> None:
    print()
    print("=" * 60)
    print("aipotluck-local-client install summary")
    print("=" * 60)
    print(f"Platform:       {profile.os_name}/{profile.arch} (backend={profile.backend})")
    print(f"llama.cpp:      {runtime_config['llama_cpp']['tag']} [{runtime_config['llama_cpp']['asset_key']}]")
    print(f"  server binary: {runtime_config['llama_cpp']['server_binary']}")
    print(f"Install root:   {lay.install_root}")
    print(f"Config:         {lay.config_dir / 'runtime.json'}")
    print(f"Logs:           {lay.log_dir}")
    if service_status is not None:
        print(f"Service:        {service_status.state.value} ({service_status.detail})")
        print(f"  service health:  http://127.0.0.1:8765/healthz")
        print(f"  service status:  http://127.0.0.1:8765/status")
        llama = runtime_config["llama_cpp"]
        print(f"  llama-server:    http://{llama['host']}:{llama['port']}/health (managed by the service, once logged in)")
    else:
        print("Service:        not installed (--no-service)")
    if shim_path is not None:
        print(f"CLI:            {shim_path}")
    print("=" * 60)
    print("You're logged out -- llama-server and the tunnel are held back until you pair this")
    print("device. Get a tunnel id/secret/endpoint from Settings -> Local Inference -> Add a")
    print("managed server on aipotluck.org, then run:")
    print()
    if shim_path is not None:
        print(f"    {CLI_SHIM_NAME} login")
        print()
        print(f"(open a new terminal first if this is the first time {CLI_SHIM_NAME} has been installed)")
    else:
        print("    python3 -m aipotluck.installer.cli login")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run_install(args)
    except Exception as exc:  # top-level guard: always report clearly
        log.error("Install failed: %s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
