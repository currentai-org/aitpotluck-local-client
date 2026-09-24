"""Shared fixtures for the aipotluck-local-client test suite.

Nothing here touches the real host: no real HOME, no real systemd/launchd, no real network. Tests
that need a "Layout" get one rooted under `tmp_path`; tests that need a service manager or fetch
call get a fake/mock instead of the real thing.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# scripts/ holds standalone tools (package_custom_build.py and, per its own docstring, more to
# come) that aren't part of the aipotluck package -- put the directory itself on sys.path so
# tests can `import package_custom_build` etc. directly, the same way install.py puts REPO_ROOT
# on sys.path for its own internal imports.
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pytest

from aipotluck.installer.layout import Layout
from aipotluck.installer.platform_detect import HostProfile


@pytest.fixture
def linux_profile() -> HostProfile:
    return HostProfile(os_name="linux", arch="x64", backend="cpu")


@pytest.fixture
def fake_layout(tmp_path: Path) -> Layout:
    """A real Layout, rooted entirely under tmp_path -- every directory a test writes into is
    disposable and never touches the real per-OS install locations."""
    root = tmp_path / "install"
    lay = Layout(
        install_root=root,
        config_dir=root / "config",
        log_dir=root / "logs",
        state_dir=root / "state",
    )
    lay.config_dir.mkdir(parents=True, exist_ok=True)
    lay.log_dir.mkdir(parents=True, exist_ok=True)
    lay.state_dir.mkdir(parents=True, exist_ok=True)
    return lay
