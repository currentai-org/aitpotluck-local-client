"""Download + checksum-verify a pinned `newt` (Pangolin tunnel client) release binary.

Deliberately NOT a reuse of `fetch.py`'s resolve_asset/extract_archive: newt's GitHub releases ship
a single raw per-OS/arch binary (no tar.gz/zip, confirmed against fosrl/newt's real release assets),
and newt has no GPU-backend concept at all -- so both the archive-extraction step and the
backend-aware asset-key lookup `fetch.py` builds for llama.cpp genuinely don't apply here. The
download+checksum-verify+cache LOGIC is the same idea as `fetch.download_asset`, just adapted for a
binary with no archive around it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import urllib.request
from pathlib import Path
from typing import Any

from installer.platform_detect import HostProfile

log = logging.getLogger("aipotluck.installer.newt_fetch")

CHUNK_SIZE = 1024 * 1024

# HostProfile.os_name -> newt's own release-asset OS naming.
_OS_NAME_MAP = {"linux": "linux", "macos": "macos", "windows": "windows"}
# HostProfile.arch -> the manifest's arch key (matches newt's asset naming pattern; the *file* name
# itself is stored in the manifest per-key, e.g. "newt_linux_amd64").
_ARCH_MAP = {"x64": "x64", "arm64": "arm64"}


class NewtVersionManifestError(RuntimeError):
    pass


class NewtChecksumMismatchError(RuntimeError):
    pass


def load_newt_manifest(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def resolve_newt_asset(manifest: dict[str, Any], profile: HostProfile) -> tuple[str, dict[str, Any]]:
    """Return (asset_key, asset_entry) for this host. No backend fallback -- newt has no backend
    variants, unlike llama.cpp."""
    os_name = _OS_NAME_MAP.get(profile.os_name)
    arch = _ARCH_MAP.get(profile.arch)
    if os_name is None or arch is None:
        raise NewtVersionManifestError(f"Unsupported host for newt: os={profile.os_name} arch={profile.arch}")

    key = f"{os_name}-{arch}"
    assets = manifest["assets"]
    if key not in assets:
        raise NewtVersionManifestError(f"No newt release asset for {key!r}. Known keys: {sorted(assets)}")

    entry = assets[key]
    if entry.get("sha256", "").startswith("UNVERIFIED"):
        raise NewtVersionManifestError(
            f"newt_version.json's {key!r} entry has never been downloaded/verified on this "
            "platform -- see newt_version.json's own header comment before relying on it."
        )
    return key, entry


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def download_newt_binary(manifest: dict[str, Any], asset_entry: dict[str, Any], dest_dir: Path) -> Path:
    """Download the raw newt binary into dest_dir, verifying sha256, and mark it executable.
    Skips re-download if a valid cached copy already exists (same idempotency shape as
    fetch.download_asset)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = asset_entry["file"]
    expected_sha = asset_entry["sha256"]
    dest_path = dest_dir / filename

    if dest_path.exists() and _sha256_file(dest_path) == expected_sha:
        log.info("Using cached newt binary: %s", dest_path)
        return dest_path
    if dest_path.exists():
        log.warning("Cached newt binary checksum mismatch, re-downloading: %s", dest_path)
        dest_path.unlink()

    url = f"{manifest['release_base_url']}/{manifest['tag']}/{filename}"
    log.info("Downloading %s", url)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    with urllib.request.urlopen(url) as resp, open(tmp_path, "wb") as out:
        shutil.copyfileobj(resp, out, length=CHUNK_SIZE)

    actual = _sha256_file(tmp_path)
    if actual != expected_sha:
        tmp_path.unlink(missing_ok=True)
        raise NewtChecksumMismatchError(
            f"Checksum mismatch for {filename}: expected {expected_sha}, got {actual}"
        )
    tmp_path.rename(dest_path)

    if os.name != "nt":
        dest_path.chmod(dest_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    log.info("Downloaded and verified newt binary: %s", dest_path)
    return dest_path
