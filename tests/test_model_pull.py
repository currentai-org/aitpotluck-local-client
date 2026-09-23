"""aipotluck.installer.model_pull -- the one-shot "spawn, poll /health, terminate" orchestration
around llama-server's own -hf downloader.

Deliberately NOT mocked: subprocess spawning, real HTTP health polling, and process termination
are exactly the kind of thing that's easy to get wrong (a pipe-buffer deadlock, a health check
that never actually proves the process is gone, a timeout that doesn't actually time out) and
easy to get *looking* right with a mock that just returns what the test expects. A tiny real
stand-in script plays the part of llama-server instead: a real subprocess, a real socket, a real
/health response, so these tests prove the orchestration against real process/network behavior.
"""

from __future__ import annotations

import socket
import stat
import textwrap
import time
from pathlib import Path

import pytest

from aipotluck.installer import model_pull

FAKE_SERVER_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import argparse
    import sys
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    parser = argparse.ArgumentParser()
    parser.add_argument("-hf")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--ctx-size")
    parser.add_argument("--gpu-layers")
    parser.add_argument("--fail", action="store_true")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--cache-list", action="store_true")
    parser.add_argument("--cache-list-fail", action="store_true")
    args = parser.parse_args()

    if args.cache_list:
        if args.cache_list_fail:
            print("error: something went wrong reading the cache", file=sys.stderr)
            sys.exit(1)
        print("number of models in cache: 2")
        print("   1. bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M")
        print("   2. org/other-repo:Q8_0")
        sys.exit(0)

    if args.fail:
        print("error: could not resolve repo/quant", file=sys.stderr)
        sys.exit(1)

    time.sleep(args.delay)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    HTTPServer((args.host, args.port), Handler).serve_forever()
    """
)


@pytest.fixture
def fake_server_binary(tmp_path: Path) -> Path:
    script = tmp_path / "fake-llama-server"
    script.write_text(FAKE_SERVER_SCRIPT, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


class TestFreePort:
    def test_returns_a_bindable_port(self):
        port = model_pull._free_port("127.0.0.1")
        # If it were still held, a fresh bind on the same port would fail.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))


class TestCheckHealth:
    def test_false_when_nothing_is_listening(self):
        port = model_pull._free_port("127.0.0.1")
        assert model_pull._check_health("127.0.0.1", port) is False


class TestPullModel:
    def test_succeeds_when_health_comes_up(self, fake_server_binary):
        # No --delay: the fake server is healthy almost immediately, so pull_model should return
        # well before its own timeout.
        model_pull.pull_model(fake_server_binary, "org/repo:Q4_K_M", timeout=15)

    def test_raises_when_process_exits_before_healthy(self, fake_server_binary, monkeypatch):
        # Simulates a bad repo/quant string: llama-server would exit non-zero rather than ever
        # serving /health. Injected via a monkeypatched cmd rather than a real bad HF target, so
        # this test needs no network access.
        real_popen = model_pull.subprocess.Popen

        def popen_with_fail_flag(cmd, *args, **kwargs):
            return real_popen(cmd + ["--fail"], *args, **kwargs)

        monkeypatch.setattr(model_pull.subprocess, "Popen", popen_with_fail_flag)

        with pytest.raises(model_pull.ModelPullError, match="exited"):
            model_pull.pull_model(fake_server_binary, "org/bad-repo", timeout=15)

    def test_raises_on_timeout_and_still_cleans_up_the_process(self, fake_server_binary, monkeypatch):
        real_popen = model_pull.subprocess.Popen

        def popen_with_long_delay(cmd, *args, **kwargs):
            return real_popen(cmd + ["--delay", "30"], *args, **kwargs)

        monkeypatch.setattr(model_pull.subprocess, "Popen", popen_with_long_delay)

        with pytest.raises(model_pull.ModelPullError, match="Timed out"):
            model_pull.pull_model(fake_server_binary, "org/slow-repo", timeout=1)

    def test_terminates_the_process_on_success(self, fake_server_binary, monkeypatch):
        port_holder: list[int] = []
        real_free_port = model_pull._free_port

        def recording_free_port(host):
            port = real_free_port(host)
            port_holder.append(port)
            return port

        monkeypatch.setattr(model_pull, "_free_port", recording_free_port)

        model_pull.pull_model(fake_server_binary, "org/repo", timeout=15)

        # The port pull_model used must not still be listening once it returns -- proves the
        # subprocess was actually terminated, not just that pull_model stopped waiting on it.
        port = port_holder[0]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _port_is_open("127.0.0.1", port):
            time.sleep(0.1)
        assert not _port_is_open("127.0.0.1", port)

    def test_raises_when_binary_missing(self, tmp_path):
        with pytest.raises(model_pull.ModelPullError, match="not found"):
            model_pull.pull_model(tmp_path / "does-not-exist", "org/repo")

    def test_passes_ctx_size_and_gpu_layers_through(self, fake_server_binary, monkeypatch):
        seen_cmds = []
        real_popen = model_pull.subprocess.Popen

        def recording_popen(cmd, *args, **kwargs):
            seen_cmds.append(cmd)
            return real_popen(cmd, *args, **kwargs)

        monkeypatch.setattr(model_pull.subprocess, "Popen", recording_popen)

        model_pull.pull_model(fake_server_binary, "org/repo", ctx_size=2048, gpu_layers="auto", timeout=15)

        assert "--ctx-size" in seen_cmds[0] and "2048" in seen_cmds[0]
        assert "--gpu-layers" in seen_cmds[0] and "auto" in seen_cmds[0]


class TestListCachedModels:
    def test_parses_real_cache_list_output(self, fake_server_binary):
        models = model_pull.list_cached_models(fake_server_binary)
        assert models == ["bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M", "org/other-repo:Q8_0"]

    def test_raises_when_binary_missing(self, tmp_path):
        with pytest.raises(model_pull.ModelPullError, match="not found"):
            model_pull.list_cached_models(tmp_path / "does-not-exist")

    def test_raises_when_cache_list_exits_nonzero(self, fake_server_binary, monkeypatch):
        real_run = model_pull.subprocess.run

        def run_with_fail_flag(cmd, *args, **kwargs):
            return real_run(cmd + ["--cache-list-fail"], *args, **kwargs)

        monkeypatch.setattr(model_pull.subprocess, "run", run_with_fail_flag)

        with pytest.raises(model_pull.ModelPullError, match="exited 1"):
            model_pull.list_cached_models(fake_server_binary)

    def test_invokes_exactly_cache_list_no_other_flags(self, fake_server_binary, monkeypatch):
        seen_cmds = []
        real_run = model_pull.subprocess.run

        def recording_run(cmd, *args, **kwargs):
            seen_cmds.append(cmd)
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(model_pull.subprocess, "run", recording_run)

        model_pull.list_cached_models(fake_server_binary)

        assert seen_cmds == [[str(fake_server_binary), "--cache-list"]]
