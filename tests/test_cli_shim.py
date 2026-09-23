"""aipotluck.installer.cli_shim -- writes the `aipotluck-local-client` wrapper onto PATH.

install_cli_shim() itself is only ever exercised against a tmp_path-redirected bin dir (via
monkeypatching `_bin_dir`) -- never the real `~/.local/bin` or `/usr/local/bin`, so running this
suite never touches or requires write access to the actual host.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import pytest

from aipotluck.installer import cli_shim
from aipotluck.installer.platform_detect import HostProfile


class TestBinDir:
    def test_linux_user_scope_is_local_bin(self):
        profile = HostProfile(os_name="linux", arch="x64", backend="cpu")
        assert cli_shim._bin_dir(profile, system_scope=False) == Path.home() / ".local" / "bin"

    def test_linux_system_scope_is_usr_local_bin(self):
        profile = HostProfile(os_name="linux", arch="x64", backend="cpu")
        assert cli_shim._bin_dir(profile, system_scope=True) == Path("/usr/local/bin")

    def test_macos_matches_linux(self):
        profile = HostProfile(os_name="macos", arch="arm64", backend="cpu")
        assert cli_shim._bin_dir(profile, system_scope=False) == Path.home() / ".local" / "bin"
        assert cli_shim._bin_dir(profile, system_scope=True) == Path("/usr/local/bin")

    def test_windows_user_scope_uses_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
        profile = HostProfile(os_name="windows", arch="x64", backend="cpu")
        assert cli_shim._bin_dir(profile, system_scope=False) == tmp_path / "AppData" / "Local" / "aipotluck" / "bin"

    def test_windows_system_scope_uses_program_files(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
        profile = HostProfile(os_name="windows", arch="x64", backend="cpu")
        assert cli_shim._bin_dir(profile, system_scope=True) == tmp_path / "Program Files" / "aipotluck" / "bin"


class TestShimText:
    def test_posix_shim_execs_the_given_python_and_cli_module(self):
        text = cli_shim._posix_shim_text(Path("/usr/bin/python3"))
        assert text.startswith("#!/usr/bin/env bash\n")
        assert f'exec "/usr/bin/python3" "{cli_shim.CLI_MODULE_PATH}" "$@"' in text

    def test_windows_shim_calls_the_given_python_and_cli_module(self):
        text = cli_shim._windows_shim_text(Path(r"C:\Python312\python.exe"))
        assert text.startswith("@echo off\r\n")
        assert f'"C:\\Python312\\python.exe" "{cli_shim.CLI_MODULE_PATH}" %*' in text
        assert "\r\n" in text  # real Windows line endings, not bare \n


class TestEnsureOnPathPosix:
    def test_warns_when_bin_dir_missing_from_path(self, monkeypatch, caplog, tmp_path):
        missing_dir = tmp_path / "not-on-path"
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        with caplog.at_level(logging.WARNING, logger="aipotluck.installer.cli_shim"):
            cli_shim._ensure_on_path_posix(missing_dir)
        assert any("not on your PATH" in rec.message for rec in caplog.records)

    def test_silent_when_bin_dir_already_on_path(self, monkeypatch, caplog, tmp_path):
        present_dir = tmp_path / "already-there"
        monkeypatch.setenv("PATH", f"/usr/bin:{present_dir}:/bin")
        with caplog.at_level(logging.WARNING, logger="aipotluck.installer.cli_shim"):
            cli_shim._ensure_on_path_posix(present_dir)
        assert caplog.records == []


class TestInstallCliShimPosix:
    def _redirect_bin_dir(self, monkeypatch, tmp_path):
        target = tmp_path / "bin"
        monkeypatch.setattr(cli_shim, "_bin_dir", lambda profile, system_scope: target)
        return target

    def test_writes_executable_file_with_correct_content(self, monkeypatch, tmp_path):
        bin_dir = self._redirect_bin_dir(monkeypatch, tmp_path)
        profile = HostProfile(os_name="linux", arch="x64", backend="cpu")
        python_exe = Path("/usr/bin/python3")

        shim_path = cli_shim.install_cli_shim(profile, python_exe, system_scope=False)

        assert shim_path == bin_dir / cli_shim.CLI_SHIM_NAME
        assert shim_path.exists()
        content = shim_path.read_text()
        assert str(python_exe) in content
        assert str(cli_shim.CLI_MODULE_PATH) in content

        mode = shim_path.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH

    def test_is_idempotent_and_fully_overwrites(self, monkeypatch, tmp_path):
        bin_dir = self._redirect_bin_dir(monkeypatch, tmp_path)
        profile = HostProfile(os_name="linux", arch="x64", backend="cpu")

        cli_shim.install_cli_shim(profile, Path("/usr/bin/python3.9"), system_scope=False)
        shim_path = cli_shim.install_cli_shim(profile, Path("/usr/bin/python3.12"), system_scope=False)

        content = shim_path.read_text()
        assert "/usr/bin/python3.12" in content
        assert "/usr/bin/python3.9" not in content

    def test_creates_bin_dir_if_missing(self, monkeypatch, tmp_path):
        bin_dir = tmp_path / "does" / "not" / "exist" / "yet"
        monkeypatch.setattr(cli_shim, "_bin_dir", lambda profile, system_scope: bin_dir)
        profile = HostProfile(os_name="linux", arch="x64", backend="cpu")

        cli_shim.install_cli_shim(profile, Path("/usr/bin/python3"), system_scope=False)

        assert bin_dir.is_dir()


class TestInstallCliShimWindows:
    def _redirect_bin_dir(self, monkeypatch, tmp_path):
        target = tmp_path / "bin"
        monkeypatch.setattr(cli_shim, "_bin_dir", lambda profile, system_scope: target)
        return target

    def test_writes_cmd_file_with_crlf(self, monkeypatch, tmp_path):
        bin_dir = self._redirect_bin_dir(monkeypatch, tmp_path)
        profile = HostProfile(os_name="windows", arch="x64", backend="cpu")

        shim_path = cli_shim.install_cli_shim(profile, Path(r"C:\Python\python.exe"), system_scope=False)

        assert shim_path == bin_dir / f"{cli_shim.CLI_SHIM_NAME}.cmd"
        raw = shim_path.read_bytes()
        assert b"\r\n" in raw

    def test_system_scope_never_attempts_a_registry_write(self, monkeypatch, tmp_path, caplog):
        # System-scope PATH edits need elevation this process may not have -- install_cli_shim must
        # print the manual instructions rather than attempt (and fail/crash on) a registry write.
        self._redirect_bin_dir(monkeypatch, tmp_path)
        called = []
        monkeypatch.setattr(cli_shim, "_add_to_user_path_windows", lambda bin_dir: called.append(bin_dir) or True)
        profile = HostProfile(os_name="windows", arch="x64", backend="cpu")

        with caplog.at_level(logging.WARNING, logger="aipotluck.installer.cli_shim"):
            cli_shim.install_cli_shim(profile, Path(r"C:\Python\python.exe"), system_scope=True)

        assert called == []
        assert any("machine-wide PATH" in rec.message for rec in caplog.records)

    def test_user_scope_calls_add_to_user_path(self, monkeypatch, tmp_path):
        self._redirect_bin_dir(monkeypatch, tmp_path)
        called = []
        monkeypatch.setattr(cli_shim, "_add_to_user_path_windows", lambda bin_dir: called.append(bin_dir) or True)
        profile = HostProfile(os_name="windows", arch="x64", backend="cpu")

        cli_shim.install_cli_shim(profile, Path(r"C:\Python\python.exe"), system_scope=False)

        assert len(called) == 1

    def test_gracefully_handles_no_winreg_available(self, monkeypatch, tmp_path, caplog):
        # This suite runs on Linux, so the real `_add_to_user_path_windows` hits its own
        # `except ImportError` branch for real (no mocking) -- proving the Windows shim path
        # doesn't crash when winreg genuinely isn't importable, not just when it's mocked away.
        self._redirect_bin_dir(monkeypatch, tmp_path)
        profile = HostProfile(os_name="windows", arch="x64", backend="cpu")

        with caplog.at_level(logging.WARNING, logger="aipotluck.installer.cli_shim"):
            shim_path = cli_shim.install_cli_shim(profile, Path(r"C:\Python\python.exe"), system_scope=False)

        assert shim_path.exists()
        assert any("could not be added automatically" in rec.message for rec in caplog.records)
