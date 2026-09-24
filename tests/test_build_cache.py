"""aipotluck.installer.build_cache -- our own cache of prebuilt binaries for hosts upstream
doesn't publish for (e.g. every Jetson-class arm64+CUDA board).
"""

from __future__ import annotations

from pathlib import Path

from aipotluck.installer import build_cache as bc
from aipotluck.installer.platform_detect import HostProfile


class TestCustomAssetKey:
    def test_cpu_key_has_no_arch_suffix(self):
        profile = HostProfile(os_name="linux", arch="arm64", backend="cpu")
        assert bc.custom_asset_key(profile, cuda_arch=None) == "linux-arm64-cpu"

    def test_cuda_key_includes_sm_suffix(self):
        profile = HostProfile(os_name="linux", arch="arm64", backend="cuda")
        assert bc.custom_asset_key(profile, cuda_arch="87") == "linux-arm64-cuda-sm87"

    def test_cuda_key_with_no_arch_falls_back_to_plain_key(self):
        # Shouldn't be looked up in this state (find_custom_build refuses it outright), but the
        # key function itself must not crash on it.
        profile = HostProfile(os_name="linux", arch="arm64", backend="cuda")
        assert bc.custom_asset_key(profile, cuda_arch=None) == "linux-arm64-cuda"


class TestLoadCustomManifest:
    def test_missing_file_is_an_empty_manifest_not_an_error(self, tmp_path):
        assert bc.load_custom_manifest(tmp_path / "nope.json") == {"assets": {}}

    def test_real_file_is_parsed(self, tmp_path):
        path = tmp_path / "custom.json"
        path.write_text('{"tag": "custom-builds", "assets": {"x": {"file": "f.tar.gz"}}}', encoding="utf-8")
        manifest = bc.load_custom_manifest(path)
        assert manifest["tag"] == "custom-builds"
        assert manifest["assets"]["x"]["file"] == "f.tar.gz"


MANIFEST = {
    "assets": {
        "linux-arm64-cuda-sm87": {"file": "llama-b10989-bin-linux-arm64-cuda-sm87.tar.gz", "sha256": "abc"},
        "linux-arm64-cuda-sm87-oldglibc": {
            "file": "f.tar.gz", "sha256": "abc", "min_glibc": "2.99",
        },
    }
}


class TestFindCustomBuild:
    def test_matching_key_is_found(self, monkeypatch):
        monkeypatch.setattr(bc, "detect_glibc_version", lambda: (2, 35))
        profile = HostProfile(os_name="linux", arch="arm64", backend="cuda")
        result = bc.find_custom_build(profile, MANIFEST, cuda_arch="87")
        assert result is not None
        key, entry = result
        assert key == "linux-arm64-cuda-sm87"
        assert entry["file"] == "llama-b10989-bin-linux-arm64-cuda-sm87.tar.gz"

    def test_no_matching_key_is_none(self, monkeypatch):
        monkeypatch.setattr(bc, "detect_glibc_version", lambda: (2, 35))
        profile = HostProfile(os_name="linux", arch="arm64", backend="cuda")
        assert bc.find_custom_build(profile, MANIFEST, cuda_arch="72") is None

    def test_different_backend_is_none(self, monkeypatch):
        monkeypatch.setattr(bc, "detect_glibc_version", lambda: (2, 35))
        profile = HostProfile(os_name="linux", arch="arm64", backend="cpu")
        assert bc.find_custom_build(profile, MANIFEST, cuda_arch=None) is None

    def test_cuda_backend_with_undetectable_arch_never_matches(self, monkeypatch):
        # There is no sensible key to look up -- must not guess or fall back to a bare
        # "linux-arm64-cuda" entry that might not even exist / might not be the right SM target.
        monkeypatch.setattr(bc, "detect_glibc_version", lambda: (2, 35))
        profile = HostProfile(os_name="linux", arch="arm64", backend="cuda")
        assert bc.find_custom_build(profile, MANIFEST, cuda_arch=None) is None

    def test_host_glibc_too_old_for_the_cached_build_is_none(self, monkeypatch):
        monkeypatch.setattr(bc, "detect_glibc_version", lambda: (2, 35))
        profile = HostProfile(os_name="linux", arch="arm64", backend="cuda")
        # This entry's key would need cuda_arch="87-oldglibc" to be looked up directly; instead
        # exercise the glibc gate through a manifest entry keyed to match a real lookup.
        manifest = {
            "assets": {
                "linux-arm64-cuda-sm87": {"file": "f.tar.gz", "sha256": "abc", "min_glibc": "2.99"},
            }
        }
        assert bc.find_custom_build(profile, manifest, cuda_arch="87") is None

    def test_unknown_host_glibc_is_not_blocked(self, monkeypatch):
        monkeypatch.setattr(bc, "detect_glibc_version", lambda: None)
        profile = HostProfile(os_name="linux", arch="arm64", backend="cuda")
        manifest = {
            "assets": {
                "linux-arm64-cuda-sm87": {"file": "f.tar.gz", "sha256": "abc", "min_glibc": "2.99"},
            }
        }
        result = bc.find_custom_build(profile, manifest, cuda_arch="87")
        assert result is not None
