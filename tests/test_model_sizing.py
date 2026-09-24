"""aipotluck.installer.model_sizing -- the CUR-1965 follow-up that auto-picks ctx_size/parallel/
cache_type_k/cache_type_v whenever the active model changes.

Same "deliberately not mocked" posture as test_model_pull.py: probe_model_profile's subprocess
spawn, health polling, and log parsing are exercised against a real fake `llama-server` script,
not a mock that just returns what the test expects to see.
"""

from __future__ import annotations

import socket
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from aipotluck.installer import model_sizing
from aipotluck.installer.model_sizing import (
    ModelProfile,
    ModelSizingError,
    _kv_cache_bytes_per_token,
    _parse_int,
    _parse_int_or_list_max,
    compute_sizing,
    probe_model_profile,
)

FAKE_SERVER_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import argparse
    import sys
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    sys.stdout.reconfigure(line_buffering=True)  # piped stdout is block-buffered by default --
    # without this, print_info lines sit in the child's buffer and never reach the parent before
    # SIGTERM kills it, the same way a real C binary's unbuffered stderr wouldn't.

    parser = argparse.ArgumentParser()
    parser.add_argument("-hf")
    parser.add_argument("--model")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--ctx-size")
    parser.add_argument("--parallel")
    parser.add_argument("--gpu-layers")
    parser.add_argument("--fail", action="store_true")
    parser.add_argument("--hang", action="store_true")
    parser.add_argument("--omit-hparams", action="store_true")
    parser.add_argument("--n-ctx-train", default="131072")
    parser.add_argument("--n-layer", default="28")
    parser.add_argument("--n-embd-head-k", default="128")
    parser.add_argument("--n-embd-k-gqa", default="1024")
    parser.add_argument("--n-embd-v-gqa", default="1024")
    args = parser.parse_args()

    if args.fail:
        print("error: could not resolve repo/quant", file=sys.stderr)
        sys.exit(1)

    if args.hang:
        time.sleep(600)
        sys.exit(0)

    if not args.omit_hparams:
        print("print_info: n_ctx_train           = " + args.n_ctx_train)
        print("print_info: n_layer               = " + args.n_layer)
        print("print_info: n_layer_all           = " + args.n_layer)
        print("print_info: n_embd_head_k         = " + args.n_embd_head_k)
        print("print_info: n_embd_k_gqa          = " + args.n_embd_k_gqa)
        print("print_info: n_embd_v_gqa          = " + args.n_embd_v_gqa)

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


class TestParseHelpers:
    def test_parse_int_reads_a_plain_value(self):
        assert _parse_int("print_info: n_ctx_train           = 131072\n", "n_ctx_train") == 131072

    def test_parse_int_does_not_match_a_longer_key_sharing_the_same_prefix(self):
        # "n_layer_all" must never satisfy a lookup for "n_layer" -- the regex requires only
        # whitespace between the key and "=", and "_all" isn't whitespace.
        text = "print_info: n_layer_all           = 999\nprint_info: n_layer               = 28\n"
        assert _parse_int(text, "n_layer") == 28

    def test_parse_int_raises_when_key_absent(self):
        with pytest.raises(ModelSizingError, match="n_ctx_train"):
            _parse_int("nothing useful here", "n_ctx_train")

    def test_parse_int_or_list_max_reads_a_plain_value(self):
        assert _parse_int_or_list_max("n_embd_k_gqa          = 1024\n", "n_embd_k_gqa") == 1024

    def test_parse_int_or_list_max_takes_the_max_of_a_bracketed_list(self):
        # Heterogeneous (e.g. MoE) architectures print per-layer values as "[a, b, c]" --
        # taking the max is the safe-to-over-budget direction for a memory sizing decision.
        assert _parse_int_or_list_max("n_embd_k_gqa          = [512, 1024, 768]\n", "n_embd_k_gqa") == 1024

    def test_parse_int_or_list_max_raises_on_an_empty_list(self):
        with pytest.raises(ModelSizingError, match="empty list"):
            _parse_int_or_list_max("n_embd_k_gqa          = []\n", "n_embd_k_gqa")


class TestProbeModelProfile:
    def test_returns_a_profile_parsed_from_the_real_server_output(self, fake_server_binary, monkeypatch):
        monkeypatch.setattr(model_sizing, "_available_memory_gb", lambda: 3.5)
        profile = probe_model_profile(fake_server_binary, model_hf="org/model:Q4_K_M")
        assert profile == ModelProfile(
            n_ctx_train=131072, n_layer=28, n_embd_head_k=128, n_embd_k_gqa=1024, n_embd_v_gqa=1024,
            available_memory_gb=3.5,
        )

    def test_terminates_the_probe_process_rather_than_leaving_it_running(self, fake_server_binary, monkeypatch):
        monkeypatch.setattr(model_sizing, "_available_memory_gb", lambda: 3.5)
        port_holder: list[int] = []
        real_free_port = model_sizing._free_port

        def _capture_port(host):
            port = real_free_port(host)
            port_holder.append(port)
            return port

        monkeypatch.setattr(model_sizing, "_free_port", _capture_port)
        probe_model_profile(fake_server_binary, model_hf="org/model:Q4_K_M")
        assert port_holder, "the fake port-allocator was never called"
        assert not _port_is_open("127.0.0.1", port_holder[0]), "probe process was left running after profiling"

    def test_raises_when_the_server_exits_before_becoming_healthy(self, fake_server_binary, monkeypatch):
        # A bad repo/quant string is the realistic real-world trigger for this.
        real_popen = subprocess.Popen

        def _fake_popen(cmd, *args, **kwargs):
            return real_popen([*cmd, "--fail"], *args, **kwargs)

        monkeypatch.setattr(model_sizing.subprocess, "Popen", _fake_popen)
        with pytest.raises(ModelSizingError, match="exited"):
            probe_model_profile(fake_server_binary, model_hf="org/does-not-exist:Q4_K_M")

    def test_raises_on_timeout_rather_than_hanging_forever(self, fake_server_binary, monkeypatch):
        real_popen = subprocess.Popen

        def _fake_popen(cmd, *args, **kwargs):
            return real_popen([*cmd, "--hang"], *args, **kwargs)

        monkeypatch.setattr(model_sizing.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(model_sizing, "_HEALTH_POLL_INTERVAL_SECONDS", 0.1)
        with pytest.raises(ModelSizingError, match="Timed out"):
            probe_model_profile(fake_server_binary, model_hf="org/model:Q4_K_M", timeout=0.5)

    def test_raises_when_a_required_hparam_is_missing_from_the_output(self, tmp_path, monkeypatch):
        # A minimal, separate fake binary rather than reusing fake_server_binary with an extra
        # flag: probe_model_profile's own command builder has no passthrough for arbitrary flags
        # (deliberately -- it only knows the flags a real sizing probe needs), so the cleanest real
        # reproduction of "the server started but its log doesn't have what we need" is a fake
        # binary that never prints the hparam lines at all.
        script = tmp_path / "fake-llama-server-no-hparams"
        script.write_text(
            FAKE_SERVER_SCRIPT.replace('parser.add_argument("--omit-hparams", action="store_true")',
                                        'parser.add_argument("--omit-hparams", action="store_true", default=True)'),
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setattr(model_sizing, "_available_memory_gb", lambda: 3.5)
        with pytest.raises(ModelSizingError, match="n_ctx_train"):
            probe_model_profile(script, model_hf="org/model:Q4_K_M")


class TestKvCacheBytesPerToken:
    def test_matches_the_real_measured_llama_3_2_3b_figure(self):
        # Llama 3.2 3B: 28 layers, 8 KV-heads (GQA), 128 head-dim, f16 -- confirmed against the
        # real device this whole feature was built for (CUR-1965): ~112 KiB/token.
        profile = ModelProfile(
            n_ctx_train=131072, n_layer=28, n_embd_head_k=128, n_embd_k_gqa=1024, n_embd_v_gqa=1024,
            available_memory_gb=None,
        )
        bytes_per_token = _kv_cache_bytes_per_token(profile, "f16", "f16")
        assert bytes_per_token == 28 * (1024 * 2 + 1024 * 2)
        assert round(bytes_per_token / 1024, 1) == 112.0

    def test_q8_0_is_roughly_half_of_f16(self):
        profile = ModelProfile(
            n_ctx_train=8192, n_layer=28, n_embd_head_k=128, n_embd_k_gqa=1024, n_embd_v_gqa=1024,
            available_memory_gb=None,
        )
        f16 = _kv_cache_bytes_per_token(profile, "f16", "f16")
        q8_0 = _kv_cache_bytes_per_token(profile, "q8_0", "q8_0")
        assert 0.5 < q8_0 / f16 < 0.6


def _profile(available_memory_gb: float | None, *, n_ctx_train=8192, n_embd_head_k=128) -> ModelProfile:
    return ModelProfile(
        n_ctx_train=n_ctx_train, n_layer=28, n_embd_head_k=n_embd_head_k,
        n_embd_k_gqa=1024, n_embd_v_gqa=1024, available_memory_gb=available_memory_gb,
    )


class TestComputeSizing:
    def test_always_sizes_to_a_single_parallel_slot(self):
        result = compute_sizing(_profile(2.0))
        assert result.parallel == 1
        assert "parallel" in result.tuning

    def test_prefers_q8_0_when_it_reaches_the_full_trained_context(self):
        result = compute_sizing(_profile(2.0))
        assert result.cache_type_k == result.cache_type_v == "q8_0"
        assert result.ctx_size == 8192  # capped at n_ctx_train, not the much larger q8 max

    def test_degrades_to_q4_0_when_only_q4_0_reaches_the_full_trained_context(self):
        result = compute_sizing(_profile(0.45))
        assert result.cache_type_k == result.cache_type_v == "q4_0"
        assert result.ctx_size == 8192

    def test_sizes_to_the_largest_context_that_fits_when_neither_quant_reaches_full_context(self):
        result = compute_sizing(_profile(0.05))
        assert result.cache_type_k == result.cache_type_v == "q4_0"
        assert result.ctx_size == 1248
        assert result.ctx_size < 8192

    def test_falls_back_to_the_min_ctx_floor_rather_than_a_too_small_context(self):
        result = compute_sizing(_profile(0.001))
        assert result.ctx_size == model_sizing._MIN_CTX_SIZE
        assert "floor" in result.tuning["ctx_size"] or "512" in result.tuning["ctx_size"]

    def test_skips_kv_quantization_when_head_dim_does_not_divide_the_quant_block_size(self):
        # head_dim=80 -- 80 % 32 != 0, so llama.cpp's own startup validation would refuse a
        # quantized K cache for this architecture (confirmed against llama-context.cpp).
        result = compute_sizing(_profile(2.0, n_embd_head_k=80))
        assert result.cache_type_k is None
        assert result.cache_type_v is None
        assert "cache_type_k" in result.tuning

    def test_falls_back_to_trained_context_with_no_quantization_when_memory_cannot_be_measured(self):
        # Fail-open path: no memory reading (e.g. non-Linux today) must not silently pick an
        # unbounded or zero context -- it must fall back to the model's own trained size.
        result = compute_sizing(_profile(None, n_ctx_train=4096))
        assert result.ctx_size == 4096
        assert result.cache_type_k is None
        assert result.cache_type_v is None
        assert "could not measure" in result.tuning["ctx_size"]
