"""aipotluck.installer.platform_detect -- OS/arch/GPU-backend detection.

Every test monkeypatches the underlying `platform`/subprocess probes rather than trusting whatever
happens to be true of the machine running the suite, so these pass identically in CI and on any
dev laptop.
"""

from __future__ import annotations

import pytest

from aipotluck.installer import platform_detect as pd


class TestDetectOs:
    @pytest.mark.parametrize(
        ("system_value", "expected"),
        [("Linux", "linux"), ("Darwin", "macos"), ("Windows", "windows")],
    )
    def test_maps_known_platforms(self, monkeypatch, system_value, expected):
        monkeypatch.setattr(pd.platform, "system", lambda: system_value)
        assert pd.detect_os() == expected

    def test_unknown_platform_raises(self, monkeypatch):
        monkeypatch.setattr(pd.platform, "system", lambda: "OS/2")
        with pytest.raises(pd.UnsupportedPlatformError):
            pd.detect_os()


class TestDetectArch:
    @pytest.mark.parametrize(
        ("machine_value", "expected"),
        [("x86_64", "x64"), ("AMD64", "x64"), ("arm64", "arm64"), ("aarch64", "arm64")],
    )
    def test_maps_known_arches(self, monkeypatch, machine_value, expected):
        monkeypatch.setattr(pd.platform, "machine", lambda: machine_value)
        assert pd.detect_arch() == expected

    def test_unknown_arch_raises(self, monkeypatch):
        monkeypatch.setattr(pd.platform, "machine", lambda: "riscv64")
        with pytest.raises(pd.UnsupportedPlatformError):
            pd.detect_arch()


class TestDetectGpuBackend:
    def test_explicit_request_passes_through_unprobed(self, monkeypatch):
        # An explicit override must never be second-guessed by probing -- even if nvidia-smi is
        # absent, "cuda" should come back as "cuda" so the caller's asset-manifest lookup fails
        # loudly on a bad manifest key rather than silently falling back to a different backend.
        monkeypatch.setattr(pd, "_has_cmd", lambda name: False)
        assert pd.detect_gpu_backend("linux", "x64", "cuda") == "cuda"

    def test_macos_is_always_cpu(self):
        # Metal is baked into the macos asset itself -- no separate GPU variant to detect.
        assert pd.detect_gpu_backend("macos", "arm64", "auto") == "cpu"

    def test_windows_is_always_cpu_for_now(self):
        assert pd.detect_gpu_backend("windows", "x64", "auto") == "cpu"

    def test_linux_detects_nvidia(self, monkeypatch):
        monkeypatch.setattr(pd, "_has_cmd", lambda name: name == "nvidia-smi")
        monkeypatch.setattr(pd, "_cmd_ok", lambda args: args[0] == "nvidia-smi")
        assert pd.detect_gpu_backend("linux", "x64", "auto") == "cuda"

    def test_linux_detects_amd_when_no_nvidia(self, monkeypatch):
        monkeypatch.setattr(pd, "_has_cmd", lambda name: name == "rocminfo")
        monkeypatch.setattr(pd, "_cmd_ok", lambda args: args[0] == "rocminfo")
        assert pd.detect_gpu_backend("linux", "x64", "auto") == "rocm"

    def test_linux_falls_back_to_cpu_when_neither_present(self, monkeypatch):
        monkeypatch.setattr(pd, "_has_cmd", lambda name: False)
        monkeypatch.setattr(pd, "_cmd_ok", lambda args: False)
        assert pd.detect_gpu_backend("linux", "x64", "auto") == "cpu"

    def test_linux_nvidia_present_but_not_working_falls_back(self, monkeypatch):
        # nvidia-smi on PATH but failing (e.g. driver/container mismatch) must not be treated as
        # "cuda available" -- a wrong positive here would hand a cuda asset to a host that can't
        # run it.
        monkeypatch.setattr(pd, "_has_cmd", lambda name: name == "nvidia-smi")
        monkeypatch.setattr(pd, "_cmd_ok", lambda args: False)
        assert pd.detect_gpu_backend("linux", "x64", "auto") == "cpu"


class TestDetectHostProfile:
    def test_combines_os_arch_backend(self, monkeypatch):
        monkeypatch.setattr(pd, "detect_os", lambda: "linux")
        monkeypatch.setattr(pd, "detect_arch", lambda: "x64")
        monkeypatch.setattr(pd, "detect_gpu_backend", lambda os_name, arch, requested: "cpu")
        profile = pd.detect_host_profile("auto")
        assert profile == pd.HostProfile(os_name="linux", arch="x64", backend="cpu")

    def test_asset_key_property(self):
        profile = pd.HostProfile(os_name="linux", arch="arm64", backend="cuda")
        assert profile.asset_key == "linux-arm64-cuda"
