"""Per-OS install locations (no-admin/per-user by default; --system opt-in).

Follows XDG conventions on Linux, standard Application Support/Logs dirs on
macOS, and LOCALAPPDATA / ProgramData on Windows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "aipotluck"


@dataclass(frozen=True)
class Layout:
    install_root: Path   # where llama.cpp release archives are extracted
    config_dir: Path     # runtime.json, service config
    log_dir: Path        # service + install logs
    state_dir: Path      # cache dir for downloaded archives etc.


def _linux_layout(system_scope: bool) -> Layout:
    if system_scope:
        return Layout(
            install_root=Path("/opt/aipotluck"),
            config_dir=Path("/etc/aipotluck"),
            log_dir=Path("/var/log/aipotluck"),
            state_dir=Path("/var/lib/aipotluck"),
        )
    home = Path.home()
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    xdg_state = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state"))
    return Layout(
        install_root=xdg_data / APP_NAME,
        config_dir=xdg_config / APP_NAME,
        log_dir=xdg_state / APP_NAME / "logs",
        state_dir=xdg_state / APP_NAME,
    )


def _macos_layout(system_scope: bool) -> Layout:
    if system_scope:
        return Layout(
            install_root=Path("/Library/Application Support") / APP_NAME,
            config_dir=Path("/Library/Application Support") / APP_NAME / "config",
            log_dir=Path("/Library/Logs") / APP_NAME,
            state_dir=Path("/Library/Application Support") / APP_NAME / "state",
        )
    home = Path.home()
    return Layout(
        install_root=home / "Library" / "Application Support" / APP_NAME,
        config_dir=home / "Library" / "Application Support" / APP_NAME / "config",
        log_dir=home / "Library" / "Logs" / APP_NAME,
        state_dir=home / "Library" / "Application Support" / APP_NAME / "state",
    )


def _windows_layout(system_scope: bool) -> Layout:
    if system_scope:
        program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        return Layout(
            install_root=program_data / APP_NAME,
            config_dir=program_data / APP_NAME / "config",
            log_dir=program_data / APP_NAME / "logs",
            state_dir=program_data / APP_NAME / "state",
        )
    local_appdata = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return Layout(
        install_root=local_appdata / APP_NAME,
        config_dir=local_appdata / APP_NAME / "config",
        log_dir=local_appdata / APP_NAME / "logs",
        state_dir=local_appdata / APP_NAME / "state",
    )


_LAYOUT_FACTORIES = {
    "linux": _linux_layout,
    "macos": _macos_layout,
    "windows": _windows_layout,
}


def get_layout(os_name: str, system_scope: bool = False, override_root: Path | None = None) -> Layout:
    try:
        factory = _LAYOUT_FACTORIES[os_name]
    except KeyError as exc:
        raise ValueError(f"No layout defined for os_name={os_name!r}") from exc
    layout = factory(system_scope)
    if override_root is not None:
        layout = Layout(
            install_root=override_root,
            config_dir=override_root / "config",
            log_dir=override_root / "logs",
            state_dir=override_root / "state",
        )
    return layout


def ensure_layout_dirs(layout: Layout) -> None:
    for path in (layout.install_root, layout.config_dir, layout.log_dir, layout.state_dir):
        path.mkdir(parents=True, exist_ok=True)
