"""aipotluck.installer.layout -- per-OS install locations.

Env vars and Path.home() are monkeypatched throughout so these tests never read or create
anything under the real user's home directory, and pass identically regardless of what account
the suite happens to run as.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aipotluck.installer import layout


class TestLinuxLayout:
    def test_per_user_uses_xdg_dirs(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        lay = layout.get_layout("linux", system_scope=False)

        assert lay.install_root == tmp_path / "data" / "aipotluck"
        assert lay.config_dir == tmp_path / "config" / "aipotluck"
        assert lay.log_dir == tmp_path / "state" / "aipotluck" / "logs"
        assert lay.state_dir == tmp_path / "state" / "aipotluck"

    def test_per_user_falls_back_to_dotfile_dirs_without_xdg(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(layout.Path, "home", classmethod(lambda cls: tmp_path))

        lay = layout.get_layout("linux", system_scope=False)

        assert lay.install_root == tmp_path / ".local" / "share" / "aipotluck"
        assert lay.config_dir == tmp_path / ".config" / "aipotluck"

    def test_system_scope_is_fixed_unix_paths(self):
        lay = layout.get_layout("linux", system_scope=True)
        assert lay.install_root == Path("/opt/aipotluck")
        assert lay.config_dir == Path("/etc/aipotluck")
        assert lay.log_dir == Path("/var/log/aipotluck")
        assert lay.state_dir == Path("/var/lib/aipotluck")


class TestMacosLayout:
    def test_per_user_uses_application_support(self, monkeypatch, tmp_path):
        monkeypatch.setattr(layout.Path, "home", classmethod(lambda cls: tmp_path))

        lay = layout.get_layout("macos", system_scope=False)

        assert lay.install_root == tmp_path / "Library" / "Application Support" / "aipotluck"
        assert lay.log_dir == tmp_path / "Library" / "Logs" / "aipotluck"

    def test_system_scope_is_fixed_paths(self):
        lay = layout.get_layout("macos", system_scope=True)
        assert lay.install_root == Path("/Library/Application Support/aipotluck")
        assert lay.log_dir == Path("/Library/Logs/aipotluck")


class TestWindowsLayout:
    def test_per_user_uses_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

        lay = layout.get_layout("windows", system_scope=False)

        assert lay.install_root == tmp_path / "AppData" / "Local" / "aipotluck"
        assert lay.config_dir == tmp_path / "AppData" / "Local" / "aipotluck" / "config"

    def test_system_scope_uses_programdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ProgramData", str(tmp_path / "ProgramData"))

        lay = layout.get_layout("windows", system_scope=True)

        assert lay.install_root == tmp_path / "ProgramData" / "aipotluck"


class TestOverrideRoot:
    @pytest.mark.parametrize("os_name", ["linux", "macos", "windows"])
    def test_override_root_wins_regardless_of_os(self, os_name, tmp_path, monkeypatch):
        # Windows needs LOCALAPPDATA resolvable even though override_root should make its value
        # irrelevant -- this is exactly the case that would catch override_root being ignored.
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "unused"))
        override = tmp_path / "custom-install"

        lay = layout.get_layout(os_name, system_scope=False, override_root=override)

        assert lay.install_root == override
        assert lay.config_dir == override / "config"
        assert lay.log_dir == override / "logs"
        assert lay.state_dir == override / "state"

    def test_override_root_wins_over_system_scope_too(self, tmp_path):
        override = tmp_path / "custom-install"
        lay = layout.get_layout("linux", system_scope=True, override_root=override)
        assert lay.install_root == override


class TestUnknownOs:
    def test_raises_value_error(self):
        with pytest.raises(ValueError, match="No layout defined"):
            layout.get_layout("plan9")


class TestEnsureLayoutDirs:
    def test_creates_all_four_directories(self, tmp_path):
        root = tmp_path / "fresh-install"
        lay = layout.Layout(
            install_root=root,
            config_dir=root / "config",
            log_dir=root / "logs",
            state_dir=root / "state",
        )
        assert not root.exists()

        layout.ensure_layout_dirs(lay)

        assert lay.install_root.is_dir()
        assert lay.config_dir.is_dir()
        assert lay.log_dir.is_dir()
        assert lay.state_dir.is_dir()

    def test_idempotent_on_already_existing_dirs(self, fake_layout):
        # fake_layout's own fixture already created these -- calling again must not raise.
        layout.ensure_layout_dirs(fake_layout)
        layout.ensure_layout_dirs(fake_layout)
