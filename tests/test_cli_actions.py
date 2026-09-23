"""aipotluck.installer.cli's run_login / run_logout / run_status.

Every OS/network/service-manager boundary is mocked: detect_host_profile (no real GPU probing),
newt_fetch (no real download), get_service_manager (no real systemd/launchd), and
urllib.request.urlopen (no real HTTP). Only runtime.json's actual read/write path is real,
rooted under tmp_path via `--install-dir`.
"""

from __future__ import annotations

import json
import logging
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipotluck.installer import cli
from aipotluck.installer.platform_detect import HostProfile


def make_args(install_dir: Path, **overrides) -> Namespace:
    defaults = dict(
        install_dir=install_dir,
        system=False,
        verbose=False,
        tunnel_id=None,
        tunnel_secret=None,
        tunnel_endpoint=None,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def write_runtime(install_dir: Path, config: dict) -> Path:
    config_dir = install_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "runtime.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _stable_profile(monkeypatch):
    """Every test in this file targets a deterministic linux profile -- nobody should be probing
    the real GPU/OS of whatever machine runs the suite."""
    monkeypatch.setattr(cli, "detect_host_profile", lambda: HostProfile(os_name="linux", arch="x64", backend="cpu"))


@pytest.fixture
def fake_service_manager(monkeypatch):
    mgr = MagicMock()
    monkeypatch.setattr(cli, "get_service_manager", lambda os_name: mgr)
    return mgr


@pytest.fixture
def fake_newt_fetch(monkeypatch, tmp_path):
    """Stubs the three newt_fetch calls run_login makes, in the shape it expects -- no network,
    no real binary written to disk (nothing downstream of run_login checks it exists)."""
    monkeypatch.setattr(cli.newt_fetch, "load_newt_manifest", lambda path: {"tag": "1.17.0", "assets": {}})
    monkeypatch.setattr(
        cli.newt_fetch, "resolve_newt_asset",
        lambda manifest, profile: ("linux-x64", {"file": "newt_linux_amd64"}),
    )
    fake_binary = tmp_path / "newt_linux_amd64"
    monkeypatch.setattr(cli.newt_fetch, "download_newt_binary", lambda manifest, entry, dest_dir: fake_binary)
    return fake_binary


class TestRunLogin:
    def test_with_all_three_flags_writes_pairing_and_logs_in(
        self, tmp_path, fake_service_manager, fake_newt_fetch, capsys
    ):
        install_dir = tmp_path / "install"
        runtime_path = write_runtime(install_dir, {"llama_cpp": {}, "service": {}, "logged_in": False})
        args = make_args(install_dir, tunnel_id="tid", tunnel_secret="tsecret", tunnel_endpoint="http://x")

        rc = cli.run_login(args)

        assert rc == 0
        saved = json.loads(runtime_path.read_text())
        assert saved["logged_in"] is True
        assert saved["tunnel"] == {
            "provider": "pangolin",
            "binary": str(fake_newt_fetch),
            "id": "tid",
            "secret": "tsecret",
            "endpoint": "http://x",
        }
        assert "Logged in." in capsys.readouterr().out

    def test_restarts_the_service(self, tmp_path, fake_service_manager, fake_newt_fetch):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"llama_cpp": {}, "service": {}, "logged_in": False})
        args = make_args(install_dir, tunnel_id="tid", tunnel_secret="tsecret", tunnel_endpoint="http://x")

        cli.run_login(args)

        fake_service_manager.stop.assert_called_once()
        fake_service_manager.start.assert_called_once()

    def test_partial_tunnel_flags_is_rejected_before_touching_anything(self, tmp_path, fake_service_manager):
        # No runtime.json exists at this install_dir at all -- if run_login proceeded past its own
        # validation it would blow up on a missing file, which would also make this test fail, just
        # for the wrong reason. Asserting the return code (and that the service was never touched)
        # is what actually pins "reject early", not merely "doesn't crash".
        args = make_args(tmp_path / "install", tunnel_id="tid", tunnel_secret=None, tunnel_endpoint="http://x")

        rc = cli.run_login(args)

        assert rc == 1
        fake_service_manager.stop.assert_not_called()
        fake_service_manager.start.assert_not_called()

    def test_missing_runtime_json_exits_loudly(self, tmp_path, fake_service_manager, fake_newt_fetch):
        args = make_args(tmp_path / "never-installed", tunnel_id="tid", tunnel_secret="s", tunnel_endpoint="e")
        with pytest.raises(SystemExit):
            cli.run_login(args)

    def test_service_restart_failure_does_not_fail_the_login(self, tmp_path, monkeypatch, fake_newt_fetch):
        # A --no-service install has nothing registered to restart -- run_login must still report
        # success (the pairing itself was written correctly) rather than treating that as fatal.
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"llama_cpp": {}, "service": {}, "logged_in": False})

        def _raise_no_service_manager(os_name):
            raise RuntimeError("no service")

        monkeypatch.setattr(cli, "get_service_manager", _raise_no_service_manager)
        args = make_args(install_dir, tunnel_id="tid", tunnel_secret="s", tunnel_endpoint="e")

        rc = cli.run_login(args)

        assert rc == 0

    def test_prompts_interactively_when_no_flags_given(self, tmp_path, fake_service_manager, fake_newt_fetch, monkeypatch):
        install_dir = tmp_path / "install"
        runtime_path = write_runtime(install_dir, {"llama_cpp": {}, "service": {}, "logged_in": False})
        prompted_fields = []

        def fake_prompt(field, *, secret=False):
            prompted_fields.append((field, secret))
            return {"Tunnel ID": "prompted-id", "Tunnel secret": "prompted-secret", "Tunnel endpoint URL": "prompted-url"}[field]

        monkeypatch.setattr(cli, "_prompt", fake_prompt)
        args = make_args(install_dir)  # all three tunnel_* left at their None default

        rc = cli.run_login(args)

        assert rc == 0
        assert prompted_fields == [("Tunnel ID", False), ("Tunnel secret", True), ("Tunnel endpoint URL", False)]
        saved = json.loads(runtime_path.read_text())
        assert saved["tunnel"]["id"] == "prompted-id"
        assert saved["tunnel"]["secret"] == "prompted-secret"
        assert saved["tunnel"]["endpoint"] == "prompted-url"


class TestPrompt:
    def test_retries_on_empty_input(self, monkeypatch, capsys):
        answers = iter(["", "   ", "real-value"])
        monkeypatch.setattr(cli, "input", lambda _prompt_text: next(answers), raising=False)

        result = cli._prompt("Some field")

        assert result == "real-value"
        assert capsys.readouterr().out.count("(required, try again)") == 2

    def test_secret_uses_getpass(self, monkeypatch):
        monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt_text: "hidden-value")
        assert cli._prompt("Secret field", secret=True) == "hidden-value"


class TestRunLogout:
    def test_removes_tunnel_and_flips_logged_in(self, tmp_path, fake_service_manager):
        install_dir = tmp_path / "install"
        runtime_path = write_runtime(
            install_dir,
            {"llama_cpp": {}, "service": {}, "logged_in": True, "tunnel": {"id": "x", "secret": "y", "endpoint": "z"}},
        )
        args = make_args(install_dir)

        rc = cli.run_logout(args)

        assert rc == 0
        saved = json.loads(runtime_path.read_text())
        assert saved["logged_in"] is False
        assert "tunnel" not in saved
        fake_service_manager.stop.assert_called_once()
        fake_service_manager.start.assert_called_once()

    def test_already_logged_out_is_a_noop(self, tmp_path, fake_service_manager, caplog):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"llama_cpp": {}, "service": {}, "logged_in": False})
        args = make_args(install_dir)

        with caplog.at_level(logging.INFO, logger="aipotluck.cli"):
            rc = cli.run_logout(args)

        assert rc == 0
        assert any("Already logged out" in rec.message for rec in caplog.records)
        fake_service_manager.stop.assert_not_called()
        fake_service_manager.start.assert_not_called()

    def test_missing_runtime_json_exits_loudly(self, tmp_path, fake_service_manager):
        args = make_args(tmp_path / "never-installed")
        with pytest.raises(SystemExit):
            cli.run_logout(args)


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


class TestRunStatus:
    def test_reports_logged_in_state_and_process_info(self, monkeypatch, capsys):
        payload = {
            "logged_in": True,
            "llama_server": {"pid": 123, "running": True},
            "tunnel": {"pid": 456, "running": True},
        }
        monkeypatch.setattr(cli.urllib.request, "urlopen", lambda url, timeout: _FakeHttpResponse(payload))

        rc = cli.run_status(None)

        out = capsys.readouterr().out
        assert rc == 0
        assert "Login:        logged in" in out
        assert "123" in out
        assert "456" in out

    def test_reports_logged_out_state_without_error_noise(self, monkeypatch, capsys):
        payload = {"logged_in": False, "llama_server": None, "tunnel": None}
        monkeypatch.setattr(cli.urllib.request, "urlopen", lambda url, timeout: _FakeHttpResponse(payload))

        rc = cli.run_status(None)

        out = capsys.readouterr().out
        assert rc == 0
        assert "Login:        logged out" in out
        assert "stopped (logged out)" in out

    def test_unreachable_service_returns_error(self, monkeypatch, caplog):
        import urllib.error

        def raise_it(url, timeout):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(cli.urllib.request, "urlopen", raise_it)

        with caplog.at_level(logging.ERROR, logger="aipotluck.cli"):
            rc = cli.run_status(None)

        assert rc == 1
        assert any("Could not reach" in rec.message for rec in caplog.records)

    def test_logged_in_but_llama_supervisor_errored_is_surfaced(self, monkeypatch, capsys):
        payload = {
            "logged_in": True,
            "llama_server": {"error": "supervisor not started"},
            "tunnel": {"pid": 1, "running": True},
        }
        monkeypatch.setattr(cli.urllib.request, "urlopen", lambda url, timeout: _FakeHttpResponse(payload))

        cli.run_status(None)

        out = capsys.readouterr().out
        assert "not running (supervisor not started)" in out


def make_pull_args(install_dir: Path, model: str = "org/repo:Q4_K_M", timeout=None, **overrides) -> Namespace:
    defaults = dict(install_dir=install_dir, system=False, verbose=False, model=model, timeout=timeout)
    defaults.update(overrides)
    return Namespace(**defaults)


class TestRunPullModel:
    def test_success_activates_the_pulled_model_and_restarts_the_service(
        self, tmp_path, fake_service_manager, monkeypatch
    ):
        install_dir = tmp_path / "install"
        runtime_path = write_runtime(
            install_dir,
            {
                "llama_cpp": {"server_binary": "/fake/llama-server", "model_hf": "old/model", "model_path": None},
                "service": {},
                "logged_in": True,
            },
        )
        pull_calls = []
        monkeypatch.setattr(cli, "pull_model", lambda *a, **kw: pull_calls.append((a, kw)))
        args = make_pull_args(install_dir, model="new/model:Q8_0")

        rc = cli.run_pull_model(args)

        assert rc == 0
        assert len(pull_calls) == 1
        saved = json.loads(runtime_path.read_text())
        assert saved["llama_cpp"]["model_hf"] == "new/model:Q8_0"
        assert saved["llama_cpp"]["model_path"] is None
        fake_service_manager.stop.assert_called_once()
        fake_service_manager.start.assert_called_once()

    def test_missing_llama_cpp_install_is_rejected_before_pulling(self, tmp_path, fake_service_manager, monkeypatch):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"service": {}, "logged_in": False})  # no llama_cpp section at all
        pull_calls = []
        monkeypatch.setattr(cli, "pull_model", lambda *a, **kw: pull_calls.append((a, kw)))
        args = make_pull_args(install_dir)

        rc = cli.run_pull_model(args)

        assert rc == 1
        assert pull_calls == []
        fake_service_manager.stop.assert_not_called()

    def test_pull_failure_does_not_change_runtime_json(self, tmp_path, fake_service_manager, monkeypatch):
        install_dir = tmp_path / "install"
        runtime_path = write_runtime(
            install_dir,
            {"llama_cpp": {"server_binary": "/fake/llama-server", "model_hf": "old/model"}, "service": {}},
        )
        original = runtime_path.read_text()

        def raise_pull_error(*a, **kw):
            raise cli.ModelPullError("bad repo/quant")

        monkeypatch.setattr(cli, "pull_model", raise_pull_error)
        args = make_pull_args(install_dir, model="bad/repo")

        rc = cli.run_pull_model(args)

        assert rc == 1
        assert runtime_path.read_text() == original  # unchanged -- a failed pull activates nothing
        fake_service_manager.stop.assert_not_called()

    def test_default_timeout_used_when_not_given(self, tmp_path, fake_service_manager, monkeypatch):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"llama_cpp": {"server_binary": "/fake/llama-server"}, "service": {}})
        seen_kwargs = {}
        monkeypatch.setattr(cli, "pull_model", lambda *a, **kw: seen_kwargs.update(kw))
        args = make_pull_args(install_dir, timeout=None)

        cli.run_pull_model(args)

        assert seen_kwargs["timeout"] == cli.DEFAULT_TIMEOUT_SECONDS

    def test_custom_timeout_passed_through(self, tmp_path, fake_service_manager, monkeypatch):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"llama_cpp": {"server_binary": "/fake/llama-server"}, "service": {}})
        seen_kwargs = {}
        monkeypatch.setattr(cli, "pull_model", lambda *a, **kw: seen_kwargs.update(kw))
        args = make_pull_args(install_dir, timeout=45.0)

        cli.run_pull_model(args)

        assert seen_kwargs["timeout"] == 45.0

    def test_ctx_size_and_gpu_layers_forwarded_from_existing_config(self, tmp_path, fake_service_manager, monkeypatch):
        install_dir = tmp_path / "install"
        write_runtime(
            install_dir,
            {
                "llama_cpp": {"server_binary": "/fake/llama-server", "ctx_size": 8192, "gpu_layers": "all"},
                "service": {},
            },
        )
        seen_kwargs = {}
        monkeypatch.setattr(cli, "pull_model", lambda *a, **kw: seen_kwargs.update(kw))
        args = make_pull_args(install_dir)

        cli.run_pull_model(args)

        assert seen_kwargs["ctx_size"] == 8192
        assert seen_kwargs["gpu_layers"] == "all"


class TestRunListModels:
    def test_lists_cached_models_and_marks_the_active_one(self, tmp_path, monkeypatch, capsys):
        install_dir = tmp_path / "install"
        write_runtime(
            install_dir,
            {"llama_cpp": {"server_binary": "/fake/llama-server", "model_hf": "org/repo:Q4_K_M"}, "service": {}},
        )
        monkeypatch.setattr(cli, "list_cached_models", lambda binary: ["org/repo:Q4_K_M", "org/other:Q8_0"])
        args = make_pull_args(install_dir)

        rc = cli.run_list_models(args)

        out = capsys.readouterr().out
        assert rc == 0
        assert "2 model(s) cached locally" in out
        assert "org/repo:Q4_K_M  (active)" in out
        assert "org/other:Q8_0" in out
        assert "org/other:Q8_0  (active)" not in out

    def test_no_cached_models_suggests_pull(self, tmp_path, monkeypatch, capsys):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"llama_cpp": {"server_binary": "/fake/llama-server"}, "service": {}})
        monkeypatch.setattr(cli, "list_cached_models", lambda binary: [])
        args = make_pull_args(install_dir)

        rc = cli.run_list_models(args)

        out = capsys.readouterr().out
        assert rc == 0
        assert "aipotluck-local-client pull" in out

    def test_missing_llama_cpp_install_is_rejected(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"service": {}})  # no llama_cpp section
        calls = []
        monkeypatch.setattr(cli, "list_cached_models", lambda binary: calls.append(binary))
        args = make_pull_args(install_dir)

        rc = cli.run_list_models(args)

        assert rc == 1
        assert calls == []

    def test_list_error_is_surfaced_and_returns_1(self, tmp_path, monkeypatch, caplog):
        install_dir = tmp_path / "install"
        write_runtime(install_dir, {"llama_cpp": {"server_binary": "/fake/llama-server"}, "service": {}})

        def raise_error(binary):
            raise cli.ModelPullError("--cache-list not supported by this build")

        monkeypatch.setattr(cli, "list_cached_models", raise_error)
        args = make_pull_args(install_dir)

        with caplog.at_level(logging.ERROR, logger="aipotluck.cli"):
            rc = cli.run_list_models(args)

        assert rc == 1
        assert any("not supported" in rec.message for rec in caplog.records)
