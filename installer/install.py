#!/usr/bin/env python3
"""aipotluck-local-client installer.

Usage:
    python -m installer.install [options]

Flow:
    1. Detect platform/arch/GPU-backend.
    2. Resolve the pinned llama.cpp release asset from llama_version.json.
    3. Download (cached, checksum-verified) + extract it.
    4. Write runtime.json pointing at the resolved llama-server binary.
    5. Install our blank Python service via the OS-native service manager.
    6. Optionally start the service (default: yes).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from installer import fetch, layout, newt_fetch
from installer.platform_detect import HostProfile, detect_host_profile
from installer.python_bootstrap import PythonNotFoundError, ensure_python
from installer.service.base import ServiceState, get_service_manager

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

    # CUR-1266: managed (tunnel) pairing -- the three flags the Settings UI's "Add a managed
    # server" install command hands the user, verbatim. All three or none; the installer validates
    # this rather than silently ignoring a partial set.
    parser.add_argument("--tunnel-id", default=None, help="Pangolin newt device id (managed pairing)")
    parser.add_argument("--tunnel-secret", default=None, help="Pangolin newt device secret (managed pairing)")
    parser.add_argument("--tunnel-endpoint", default=None, help="Pangolin tunnel endpoint URL (managed pairing)")
    parser.add_argument(
        "--remove-tunnel", action="store_true",
        help="Remove this device's tunnel pairing (drops runtime.json's 'tunnel' section, restarts "
             "the service) without touching the llama.cpp install. Mutually exclusive with --tunnel-*.",
    )

    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )


def _tunnel_flags_given(args: argparse.Namespace) -> list[str]:
    return [
        name for name, value in (
            ("--tunnel-id", args.tunnel_id),
            ("--tunnel-secret", args.tunnel_secret),
            ("--tunnel-endpoint", args.tunnel_endpoint),
        )
        if value
    ]


def run_remove_tunnel(args: argparse.Namespace) -> int:
    """CUR-1266: the client-side half of Settings' "Remove" button -- the server only revokes its
    own Pangolin site/resource on delete (see web/'s DELETE route); this is what actually stops the
    local newt process from retrying with the now-invalid credential."""
    profile: HostProfile = detect_host_profile(args.backend)
    lay = layout.get_layout(profile.os_name, system_scope=args.system, override_root=args.install_dir)
    runtime_path = lay.config_dir / "runtime.json"
    if not runtime_path.exists():
        log.error("No runtime.json found at %s -- nothing to unpair", runtime_path)
        return 1

    runtime_config = json.loads(runtime_path.read_text(encoding="utf-8"))
    if "tunnel" not in runtime_config:
        log.info("No tunnel section present; already unpaired.")
        return 0

    del runtime_config["tunnel"]
    runtime_path.write_text(json.dumps(runtime_config, indent=2), encoding="utf-8")
    log.info("Removed tunnel pairing from %s", runtime_path)

    if not args.no_service:
        service_mgr = get_service_manager(profile.os_name)
        service_mgr.stop(SERVICE_NAME, system_scope=args.system)
        service_mgr.start(SERVICE_NAME, system_scope=args.system)
        log.info("Restarted %s service", SERVICE_NAME)
    return 0


def run_install(args: argparse.Namespace) -> int:
    if args.remove_tunnel:
        if _tunnel_flags_given(args):
            log.error("--remove-tunnel cannot be combined with --tunnel-id/--tunnel-secret/--tunnel-endpoint")
            return 1
        return run_remove_tunnel(args)

    given = _tunnel_flags_given(args)
    if given and len(given) != 3:
        missing = sorted({"--tunnel-id", "--tunnel-secret", "--tunnel-endpoint"} - set(given))
        log.error("Managed pairing needs all three tunnel flags; missing: %s", ", ".join(missing))
        return 1

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

    asset_key, asset_entry = fetch.resolve_asset(manifest, profile)
    log.info("Resolved asset: %s (%s)", asset_key, asset_entry["file"])

    archive_path = fetch.download_asset(manifest, asset_entry, lay.state_dir / "downloads")
    llama_dir = fetch.extract_archive(archive_path, lay.install_root / "llama.cpp", manifest["tag"], asset_key)

    server_bin = fetch.find_binary(llama_dir, "llama-server")
    log.info("llama-server binary: %s", server_bin)

    tunnel_config: dict | None = None
    if given:
        newt_manifest_path = REPO_ROOT / "newt_version.json"
        newt_manifest = newt_fetch.load_newt_manifest(newt_manifest_path)
        newt_asset_key, newt_asset_entry = newt_fetch.resolve_newt_asset(newt_manifest, profile)
        log.info("Resolved newt asset: %s (%s)", newt_asset_key, newt_asset_entry["file"])
        newt_binary = newt_fetch.download_newt_binary(
            newt_manifest, newt_asset_entry, lay.install_root / "newt" / newt_manifest["tag"]
        )
        log.info("newt binary: %s", newt_binary)
        tunnel_config = {
            "provider": "pangolin",
            "binary": str(newt_binary),
            "id": args.tunnel_id,
            "secret": args.tunnel_secret,
            "endpoint": args.tunnel_endpoint,
        }

    runtime_config = {
        "llama_cpp": {
            "tag": manifest["tag"],
            "asset_key": asset_key,
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
    }
    if tunnel_config:
        runtime_config["tunnel"] = tunnel_config
    runtime_path = lay.config_dir / "runtime.json"
    runtime_path.write_text(json.dumps(runtime_config, indent=2), encoding="utf-8")
    log.info("Wrote runtime config: %s", runtime_path)

    if args.no_service:
        log.info("--no-service set; skipping Python service install.")
        _print_summary(profile, lay, runtime_config, service_status=None)
        return 0

    service_mgr = get_service_manager(profile.os_name)
    service_script = REPO_ROOT / "service" / "aipotluck_service.py"
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

    if not args.no_start:
        service_mgr.start(SERVICE_NAME, system_scope=args.system)
        log.info("Started %s service", SERVICE_NAME)

    status = service_mgr.status(SERVICE_NAME, system_scope=args.system)
    _print_summary(profile, lay, runtime_config, service_status=status)

    if status.state not in (ServiceState.RUNNING, ServiceState.STOPPED):
        log.warning("Service status is ambiguous: %s (%s)", status.state, status.detail)

    return 0


def _print_summary(profile: HostProfile, lay: layout.Layout, runtime_config: dict, service_status) -> None:
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
        print(f"  llama-server:    http://{llama['host']}:{llama['port']}/health (managed by the service)")
    else:
        print("Service:        not installed (--no-service)")
    if "tunnel" in runtime_config:
        print(f"Tunnel:         paired ({runtime_config['tunnel']['provider']}) -> {runtime_config['tunnel']['endpoint']}")
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
