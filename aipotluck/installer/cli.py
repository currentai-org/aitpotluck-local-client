#!/usr/bin/env python3
"""Thin management CLI for an already-installed aipotluck-local-client device.

The public one-line installer (install.sh -> aipotluck.installer.install) always produces a
"logged out" install: no Pangolin credentials, and the service (aipotluck/service/runner.py) holds
both llama-server and newt back until this CLI's `login` command supplies them. This file is the
only place that edits runtime.json's `tunnel` section and `logged_in` flag after install time -- it
never touches the llama.cpp install or the OS service registration itself, it only flips that state
and restarts the already-installed service so it picks the change up.

The installer also drops a small `aipotluck-local-client` wrapper onto PATH that runs this exact
file (see cli_shim.py) -- that's the command to actually type; `python -m aipotluck.installer.cli`
below is the fallback for a `--no-service` install (which skips the shim) or a checkout PATH
doesn't reach yet.

Usage:
    aipotluck-local-client login    # prompts for tunnel id/secret/endpoint, one at a time
    aipotluck-local-client logout   # unpairs; llama-server and the tunnel stop until you log in again
    aipotluck-local-client status   # asks the running service for its login/tunnel/llama-server state
    aipotluck-local-client pull <hf-repo[:quant]>   # download + activate a model ahead of time
    aipotluck-local-client list     # list models already downloaded locally
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aipotluck.installer import layout, newt_fetch  # noqa: E402
from aipotluck.installer.cli_shim import CLI_SHIM_NAME  # noqa: E402
from aipotluck.installer.model_pull import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    ModelPullError,
    list_cached_models,
    pull_model,
)
from aipotluck.installer.platform_detect import HostProfile, detect_host_profile  # noqa: E402
from aipotluck.installer.service.base import get_service_manager  # noqa: E402
from aipotluck.service.runner import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402

log = logging.getLogger("aipotluck.cli")

SERVICE_NAME = "aipotluck"


def _common_args_parser() -> argparse.ArgumentParser:
    """Shared flags, added as a `parents=[...]` base to EACH SUBCOMMAND only -- never to the top
    parser too. argparse's subparsers action re-parses the chosen subcommand's tokens into a fresh
    namespace and then unconditionally overwrites the outer one with it (`_SubParsersAction.
    __call__` passes `None`, not the existing namespace, to the subparser's own parse_known_args) --
    so a flag declared on BOTH the top parser and a subparser silently reverts to the subparser's
    own default the moment it's given before the subcommand name, with no error. Confirmed live:
    `aipotluck-local-client --install-dir X login` silently ignored X and wrote to the default
    install dir instead; `aipotluck-local-client login --install-dir X` (this design) fails loudly
    on an unrecognized argument if given in the wrong position rather than acting on the wrong
    directory. Keep these flags after the subcommand name; don't hoist them onto the top parser to
    "support both orders" -- that's exactly the change that reintroduces the silent bug."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--install-dir", type=Path, default=None,
        help="Override the default per-OS install root -- must match what the installer used",
    )
    common.add_argument(
        "--system", action="store_true",
        help="Target a machine-wide (--system) install rather than the default per-user one",
    )
    common.add_argument("-v", "--verbose", action="store_true")
    return common


def build_arg_parser() -> argparse.ArgumentParser:
    common = _common_args_parser()
    parser = argparse.ArgumentParser(
        prog="aipotluck-local-client",
        description="Log in/out and check status for an already-installed aipotluck-local-client device.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", parents=[common], help="Pair this device with a managed tunnel endpoint")
    login.add_argument(
        "--tunnel-id", default=None,
        help="Skip the interactive prompt (scripting only); must be given with the other two --tunnel-* flags",
    )
    login.add_argument("--tunnel-secret", default=None, help="Skip the interactive prompt (scripting only)")
    login.add_argument("--tunnel-endpoint", default=None, help="Skip the interactive prompt (scripting only)")

    sub.add_parser(
        "logout", parents=[common],
        help="Unpair this device; llama-server and the tunnel stop until you log in again",
    )
    sub.add_parser("status", parents=[common], help="Query the running service's login/tunnel/llama-server state")

    pull = sub.add_parser(
        "pull", parents=[common],
        help="Download a Hugging Face model ahead of time and make it the active model",
    )
    pull.add_argument(
        "model",
        help="Hugging Face repo[:quant], e.g. bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M "
             "-- passed straight through to llama-server's own -hf downloader",
    )
    pull.add_argument(
        "--timeout", type=float, default=None,
        help=f"Seconds to wait for the download+load to finish before giving up "
             f"(default: {int(DEFAULT_TIMEOUT_SECONDS)})",
    )

    sub.add_parser(
        "list", parents=[common],
        help="List models already downloaded locally (via llama-server's own --cache-list)",
    )

    return parser


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )


def _profile_and_layout(args: argparse.Namespace) -> tuple[HostProfile, layout.Layout]:
    profile = detect_host_profile()
    lay = layout.get_layout(profile.os_name, system_scope=args.system, override_root=args.install_dir)
    return profile, lay


def _runtime_path(lay: layout.Layout) -> Path:
    return lay.config_dir / "runtime.json"


def _load_runtime(runtime_path: Path) -> dict:
    if not runtime_path.exists():
        log.error(
            "No runtime.json found at %s -- run the installer first (see README.md's Quick start).",
            runtime_path,
        )
        raise SystemExit(1)
    try:
        return json.loads(runtime_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to read %s: %s", runtime_path, exc)
        raise SystemExit(1) from exc


def _save_runtime(runtime_path: Path, runtime_config: dict) -> None:
    runtime_path.write_text(json.dumps(runtime_config, indent=2), encoding="utf-8")


def _restart_service(os_name: str, system_scope: bool) -> None:
    """Best-effort: a `--no-service`/dry-run install has nothing registered to restart, and that's
    not this CLI's problem to fix -- just tell the person plainly rather than raising."""
    try:
        service_mgr = get_service_manager(os_name)
        service_mgr.stop(SERVICE_NAME, system_scope=system_scope)
        service_mgr.start(SERVICE_NAME, system_scope=system_scope)
        log.info("Restarted the %s service to pick up the change.", SERVICE_NAME)
    except Exception as exc:
        log.warning(
            "Could not restart the %s service (%s). If you're running it manually, restart it yourself.",
            SERVICE_NAME, exc,
        )


def _prompt(field: str, *, secret: bool = False) -> str:
    while True:
        raw = getpass.getpass(f"{field}: ") if secret else input(f"{field}: ")
        value = raw.strip()
        if value:
            return value
        print("  (required, try again)")


def run_login(args: argparse.Namespace) -> int:
    given = [args.tunnel_id, args.tunnel_secret, args.tunnel_endpoint]
    if any(given) and not all(given):
        log.error("Pass all three of --tunnel-id/--tunnel-secret/--tunnel-endpoint, or none to be prompted.")
        return 1

    if all(given):
        tunnel_id, tunnel_secret, tunnel_endpoint = args.tunnel_id, args.tunnel_secret, args.tunnel_endpoint
    else:
        print("Pair this device with a managed tunnel endpoint.")
        print("Paste the three values from Settings -> Local Inference -> Add a managed server, one at a time.")
        print()
        tunnel_id = _prompt("Tunnel ID")
        tunnel_secret = _prompt("Tunnel secret", secret=True)
        tunnel_endpoint = _prompt("Tunnel endpoint URL")

    profile, lay = _profile_and_layout(args)
    runtime_path = _runtime_path(lay)
    runtime_config = _load_runtime(runtime_path)

    newt_manifest = newt_fetch.load_newt_manifest(REPO_ROOT / "newt_version.json")
    newt_asset_key, newt_asset_entry = newt_fetch.resolve_newt_asset(newt_manifest, profile)
    log.info("Resolved newt asset: %s (%s)", newt_asset_key, newt_asset_entry["file"])
    newt_binary = newt_fetch.download_newt_binary(
        newt_manifest, newt_asset_entry, lay.install_root / "newt" / newt_manifest["tag"]
    )
    log.info("newt binary: %s", newt_binary)

    runtime_config["tunnel"] = {
        "provider": "pangolin",
        "binary": str(newt_binary),
        "id": tunnel_id,
        "secret": tunnel_secret,
        "endpoint": tunnel_endpoint,
    }
    runtime_config["logged_in"] = True
    _save_runtime(runtime_path, runtime_config)
    log.info("Wrote pairing to %s", runtime_path)

    _restart_service(profile.os_name, args.system)
    print()
    print("Logged in. llama-server and the tunnel are starting -- check with:")
    print(f"    {CLI_SHIM_NAME} status")
    return 0


def run_logout(args: argparse.Namespace) -> int:
    profile, lay = _profile_and_layout(args)
    runtime_path = _runtime_path(lay)
    runtime_config = _load_runtime(runtime_path)

    if not runtime_config.get("logged_in") and "tunnel" not in runtime_config:
        log.info("Already logged out.")
        return 0

    runtime_config.pop("tunnel", None)
    runtime_config["logged_in"] = False
    _save_runtime(runtime_path, runtime_config)
    log.info("Removed pairing from %s", runtime_path)

    _restart_service(profile.os_name, args.system)
    print()
    print("Logged out. llama-server and the tunnel have stopped.")
    return 0


def run_status(_args: argparse.Namespace) -> int:
    url = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/status"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        log.error(
            "Could not reach the aipotluck service at %s (%s). Is it installed and running?",
            url, exc,
        )
        return 1

    logged_in = bool(payload.get("logged_in"))
    print(f"Login:        {'logged in' if logged_in else 'logged out'}")

    llama = payload.get("llama_server")
    if llama and not llama.get("error"):
        print(f"llama-server: {llama}")
    elif logged_in:
        print(f"llama-server: not running ({llama.get('error') if llama else 'unknown'})")
    else:
        print("llama-server: stopped (logged out)")

    tunnel = payload.get("tunnel")
    if tunnel:
        print(f"Tunnel:       {tunnel}")
    else:
        print("Tunnel:       " + ("not running (unexpected while logged in)" if logged_in else "stopped (logged out)"))
    return 0


def run_pull_model(args: argparse.Namespace) -> int:
    """Downloads `args.model` via llama-server's own -hf downloader (see model_pull.py), then
    makes it the configured model for this device -- same "edit runtime.json, restart the
    already-installed service" shape as login/logout, so a running service picks it up without a
    separate step. Independent of login state: pulling a model doesn't need (or touch) pairing."""
    profile, lay = _profile_and_layout(args)
    runtime_path = _runtime_path(lay)
    runtime_config = _load_runtime(runtime_path)

    llama_cfg = runtime_config.get("llama_cpp")
    if not llama_cfg or not llama_cfg.get("server_binary"):
        log.error("No llama.cpp install found at %s -- run the installer first.", runtime_path)
        return 1

    print(f"Pulling {args.model} -- this can take a while for a large quant.")
    try:
        pull_model(
            Path(llama_cfg["server_binary"]),
            args.model,
            ctx_size=llama_cfg.get("ctx_size"),
            gpu_layers=llama_cfg.get("gpu_layers"),
            timeout=args.timeout if args.timeout is not None else DEFAULT_TIMEOUT_SECONDS,
        )
    except ModelPullError as exc:
        log.error("%s", exc)
        return 1

    llama_cfg["model_hf"] = args.model
    llama_cfg["model_path"] = None
    _save_runtime(runtime_path, runtime_config)
    log.info("Set %s as the active model in %s", args.model, runtime_path)

    _restart_service(profile.os_name, args.system)
    print()
    print(f"{args.model} is downloaded and set as the active model.")
    return 0


def run_list_models(args: argparse.Namespace) -> int:
    """Lists every model already cached locally (see model_pull.list_cached_models). Doesn't need
    the device logged in or the service running -- this only reads the local HF-hub-compatible
    cache directory via llama-server's own --cache-list, independent of pairing/service state."""
    _profile, lay = _profile_and_layout(args)
    runtime_path = _runtime_path(lay)
    runtime_config = _load_runtime(runtime_path)

    llama_cfg = runtime_config.get("llama_cpp")
    if not llama_cfg or not llama_cfg.get("server_binary"):
        log.error("No llama.cpp install found at %s -- run the installer first.", runtime_path)
        return 1

    try:
        models = list_cached_models(Path(llama_cfg["server_binary"]))
    except ModelPullError as exc:
        log.error("%s", exc)
        return 1

    if not models:
        print("No models cached locally yet -- pull one with `aipotluck-local-client pull <repo[:quant]>`.")
        return 0

    active = llama_cfg.get("model_hf")
    print(f"{len(models)} model(s) cached locally:")
    for target in models:
        marker = "  (active)" if target == active else ""
        print(f"  {target}{marker}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    try:
        if args.command == "login":
            return run_login(args)
        if args.command == "logout":
            return run_logout(args)
        if args.command == "status":
            return run_status(args)
        if args.command == "pull":
            return run_pull_model(args)
        if args.command == "list":
            return run_list_models(args)
    except SystemExit:
        raise
    except Exception as exc:  # top-level guard: always report clearly
        log.error("%s failed: %s", args.command, exc, exc_info=args.verbose)
        return 1
    return 1  # unreachable: argparse's `required=True` on the subparser rules this out


if __name__ == "__main__":
    raise SystemExit(main())
