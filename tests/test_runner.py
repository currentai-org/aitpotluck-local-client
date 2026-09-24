"""aipotluck.service.runner -- the portable service body.

The two things this file exists to pin: (1) both supervisors are gated on runtime.json's
"logged_in" flag, and (2) /status never echoes the tunnel secret. Real LlamaSupervisor/
NewtSupervisor are swapped for lightweight fakes throughout -- nothing here spawns a real
subprocess.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path

import pytest

from aipotluck.service import runner


class _FakeSupervisor:
    """Records start()/stop() calls; stands in for LlamaSupervisor/NewtSupervisor so tests never
    spawn a real llama-server or newt process."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout: float = 20.0):
        self.stopped = True


class TestLoadRuntimeConfig:
    def test_missing_file_returns_empty_dict(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="aipotluck.service"):
            result = runner.load_runtime_config(tmp_path)
        assert result == {}
        assert any("run the installer first" in rec.message for rec in caplog.records)

    def test_valid_json_is_parsed(self, tmp_path):
        (tmp_path / "runtime.json").write_text(json.dumps({"logged_in": True}), encoding="utf-8")
        assert runner.load_runtime_config(tmp_path) == {"logged_in": True}

    def test_invalid_json_returns_empty_dict_not_raises(self, tmp_path, caplog):
        (tmp_path / "runtime.json").write_text("{not valid json", encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger="aipotluck.service"):
            result = runner.load_runtime_config(tmp_path)
        assert result == {}
        assert any("Failed to read runtime.json" in rec.message for rec in caplog.records)


class TestBuildLlamaServerArgs:
    def test_prefers_model_path_over_model_hf(self):
        args = runner.build_llama_server_args(
            {"host": "127.0.0.1", "port": 8080, "model_path": "/models/x.gguf", "model_hf": "org/repo"}
        )
        assert "--model" in args
        assert "/models/x.gguf" in args
        assert "-hf" not in args

    def test_falls_back_to_model_hf(self):
        args = runner.build_llama_server_args({"host": "127.0.0.1", "port": 8080, "model_hf": "org/repo:Q4"})
        assert "-hf" in args
        assert "org/repo:Q4" in args

    def test_warns_when_neither_model_given(self, caplog):
        with caplog.at_level(logging.WARNING, logger="aipotluck.service"):
            runner.build_llama_server_args({"host": "127.0.0.1", "port": 8080})
        assert any("No model_path or model_hf" in rec.message for rec in caplog.records)

    def test_ctx_size_and_gpu_layers_passed_through(self):
        args = runner.build_llama_server_args(
            {"host": "127.0.0.1", "port": 8080, "model_hf": "x", "ctx_size": 4096, "gpu_layers": "auto"}
        )
        assert "--ctx-size" in args and "4096" in args
        assert "--gpu-layers" in args and "auto" in args

    def test_parallel_passed_through_when_set(self):
        args = runner.build_llama_server_args(
            {"host": "127.0.0.1", "port": 8080, "model_hf": "x", "parallel": 1}
        )
        assert "--parallel" in args and "1" in args

    def test_parallel_omitted_when_unset_leaves_llama_servers_own_default(self):
        # No --parallel flag at all -- llama-server picks its own default (4 today), unchanged
        # behavior for any runtime.json predating this field.
        args = runner.build_llama_server_args({"host": "127.0.0.1", "port": 8080, "model_hf": "x"})
        assert "--parallel" not in args

    def test_parallel_zero_is_also_omitted(self):
        # 0 slots isn't a meaningful value to pass through; falsy-and-unset should behave the same.
        args = runner.build_llama_server_args(
            {"host": "127.0.0.1", "port": 8080, "model_hf": "x", "parallel": 0}
        )
        assert "--parallel" not in args


class TestBuildSupervisor:
    def test_returns_none_without_llama_cpp_section(self, caplog):
        with caplog.at_level(logging.ERROR, logger="aipotluck.service"):
            result = runner.build_supervisor({}, None)
        assert result is None
        assert any("no llama_cpp section" in rec.message for rec in caplog.records)

    def test_returns_none_when_binary_missing(self, tmp_path, caplog):
        config = {"llama_cpp": {"server_binary": str(tmp_path / "does-not-exist")}}
        with caplog.at_level(logging.ERROR, logger="aipotluck.service"):
            result = runner.build_supervisor(config, None)
        assert result is None
        assert any("binary not found" in rec.message for rec in caplog.records)

    def test_builds_supervisor_when_binary_exists(self, tmp_path, monkeypatch):
        binary = tmp_path / "llama-server"
        binary.write_text("#!/bin/sh\n")
        monkeypatch.setattr(runner, "LlamaSupervisor", _FakeSupervisor)

        config = {"llama_cpp": {"server_binary": str(binary), "host": "127.0.0.1", "port": 8080, "model_hf": "x"}}
        result = runner.build_supervisor(config, None)

        assert isinstance(result, _FakeSupervisor)


class TestBuildNewtSupervisor:
    def test_returns_none_without_tunnel_section(self):
        assert runner.build_newt_supervisor({}, None) is None

    def test_returns_none_when_binary_missing(self, tmp_path, caplog):
        config = {"tunnel": {"binary": str(tmp_path / "missing-newt")}}
        with caplog.at_level(logging.ERROR, logger="aipotluck.service"):
            result = runner.build_newt_supervisor(config, None)
        assert result is None
        assert any("newt binary not found" in rec.message for rec in caplog.records)

    def test_builds_supervisor_when_binary_exists(self, tmp_path, monkeypatch):
        binary = tmp_path / "newt"
        binary.write_text("#!/bin/sh\n")
        monkeypatch.setattr(runner, "NewtSupervisor", _FakeSupervisor)

        config = {"tunnel": {"binary": str(binary), "id": "i", "secret": "s", "endpoint": "e"}}
        result = runner.build_newt_supervisor(config, None)

        assert isinstance(result, _FakeSupervisor)


class TestRedactedRuntimeConfig:
    def test_redacts_tunnel_secret(self):
        config = {"tunnel": {"id": "i", "secret": "super-secret", "endpoint": "e"}, "logged_in": True}
        redacted = runner._StatusHandler._redacted_runtime_config(config)
        assert redacted["tunnel"]["secret"] == "<redacted>"
        assert redacted["tunnel"]["id"] == "i"
        # original must be untouched -- a caller holding onto `config` should never see it mutated
        assert config["tunnel"]["secret"] == "super-secret"

    def test_passthrough_when_no_tunnel_section(self):
        config = {"logged_in": False}
        assert runner._StatusHandler._redacted_runtime_config(config) == config


class TestCapabilitiesEndpoint:
    """A real HTTP GET against a real running server -- diagnostics.gather_fingerprint itself is
    faked (it's covered for real in test_diagnostics.py); what this pins is that the route is
    actually wired up and returns exactly what gather_fingerprint produced, as real JSON over the
    wire."""

    def test_returns_the_fingerprint_as_json(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "runtime.json").write_text(json.dumps({"logged_in": False}), encoding="utf-8")
        fake_fingerprint = {"service": "aipotluck", "fingerprint_version": 1, "os": {"name": "linux"}}
        monkeypatch.setattr(runner.diagnostics, "gather_fingerprint", lambda **kwargs: fake_fingerprint)

        svc = runner.AipotluckServiceRunner(host="127.0.0.1", port=0, config_dir=config_dir, log_dir=None)
        try:
            svc.start()
            port = svc._server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/capabilities", timeout=5) as resp:
                assert resp.status == 200
                assert resp.headers["Content-Type"] == "application/json"
                body = json.loads(resp.read())
            assert body == fake_fingerprint
        finally:
            svc.stop(timeout=2)

    def test_passes_config_dir_and_runtime_config_through(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "runtime.json").write_text(json.dumps({"logged_in": True}), encoding="utf-8")
        captured = {}

        def _fake_gather(*, config_dir, runtime_config):
            captured["config_dir"] = config_dir
            captured["runtime_config"] = runtime_config
            return {}

        monkeypatch.setattr(runner.diagnostics, "gather_fingerprint", _fake_gather)

        svc = runner.AipotluckServiceRunner(host="127.0.0.1", port=0, config_dir=config_dir, log_dir=None)
        try:
            svc.start()
            port = svc._server.server_address[1]
            urllib.request.urlopen(f"http://127.0.0.1:{port}/capabilities", timeout=5).read()
        finally:
            svc.stop(timeout=2)

        assert captured["config_dir"] == config_dir
        assert captured["runtime_config"]["logged_in"] is True


class TestAipotluckServiceRunnerStartStop:
    """Exercises the real start()/stop() methods end to end, including the real HTTP server --
    the only things swapped out are LlamaSupervisor/NewtSupervisor themselves."""

    def test_logged_out_start_binds_http_server_but_no_supervisors(self, tmp_path, monkeypatch, caplog):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "runtime.json").write_text(json.dumps({"logged_in": False}), encoding="utf-8")

        svc = runner.AipotluckServiceRunner(host="127.0.0.1", port=0, config_dir=config_dir, log_dir=None)
        try:
            with caplog.at_level(logging.INFO, logger="aipotluck.service"):
                svc.start()
            assert svc.supervisor is None
            assert svc.newt_supervisor is None
            assert svc._server is not None
            assert any("logged out" in rec.message for rec in caplog.records)
        finally:
            svc.stop(timeout=2)

    def test_logged_in_start_builds_and_starts_both_supervisors(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        binary = tmp_path / "llama-server"
        binary.write_text("#!/bin/sh\n")
        newt_binary = tmp_path / "newt"
        newt_binary.write_text("#!/bin/sh\n")
        (config_dir / "runtime.json").write_text(
            json.dumps(
                {
                    "logged_in": True,
                    "llama_cpp": {"server_binary": str(binary), "host": "127.0.0.1", "port": 8080, "model_hf": "x"},
                    "tunnel": {"binary": str(newt_binary), "id": "i", "secret": "s", "endpoint": "e"},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(runner, "LlamaSupervisor", _FakeSupervisor)
        monkeypatch.setattr(runner, "NewtSupervisor", _FakeSupervisor)

        svc = runner.AipotluckServiceRunner(host="127.0.0.1", port=0, config_dir=config_dir, log_dir=None)
        try:
            svc.start()
            assert isinstance(svc.supervisor, _FakeSupervisor)
            assert svc.supervisor.started
            assert isinstance(svc.newt_supervisor, _FakeSupervisor)
            assert svc.newt_supervisor.started
        finally:
            svc.stop(timeout=2)

    def test_stop_is_idempotent(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "runtime.json").write_text(json.dumps({"logged_in": False}), encoding="utf-8")

        svc = runner.AipotluckServiceRunner(host="127.0.0.1", port=0, config_dir=config_dir, log_dir=None)
        svc.start()
        svc.stop(timeout=2)
        svc.stop(timeout=2)  # must not raise the second time
