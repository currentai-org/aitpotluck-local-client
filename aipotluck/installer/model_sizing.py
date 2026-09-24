"""Automatic runtime-parameter sizing, run whenever the active model changes
(`aipotluck-local-client pull`, CUR-1965's follow-up).

Looks at two things and picks the largest context -- and cheapest safe KV-cache quantization --
that fits between them:

  1. The model's own trained context length and per-layer KV-cache footprint, probed directly
     from the model rather than guessed from its name or file size.
  2. The device's real memory headroom, measured with that exact model already loaded -- the same
     class of gap that caused CUR-1965 in the first place: a model silently running with 4x more
     KV-cache-eating parallel slots than a single-user box needs, invisible until a context-overflow
     error forced a manual /props query to explain it.

Every value this picks is written into runtime.json's llama_cpp.tuning map (see CLAUDE.md's
"Runtime parameters" convention) so it's traceable from both `status` and GET /capabilities --
never computed silently.

Why probe the running server rather than parse the GGUF file directly: this project already
resolves a `repo:quant` shorthand to the right cached file via llama-server's own `-hf`
downloader, and model_pull.py's own docstring explains why re-deriving that resolution logic a
second time is deliberately avoided. Probing the actual process llama-server starts keeps this in
lockstep with whatever file it really loaded, rather than an independently-guessed path into the
HF cache. The model is already downloaded by the time this runs (pull_model() runs first), so the
probe is a fast local load, not a second network fetch.
"""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from aipotluck.installer.source_build import _available_memory_gb

log = logging.getLogger("aipotluck.installer.model_sizing")

# Small enough to load fast (this is a metadata probe, not a real serving context) -- the model's
# own hparams (n_layer, n_embd_k_gqa, ...) are fixed at load time regardless of --ctx-size.
_PROBE_CTX_SIZE = 512
_PROBE_TIMEOUT_SECONDS = 120.0
_HEALTH_POLL_INTERVAL_SECONDS = 1.0

# Quantized K/V cache tensors are block-quantized (32 elements/block); a head dimension that
# doesn't divide evenly into that block size makes llama.cpp refuse to start at all -- confirmed
# against vendor/llama.cpp/src/llama-context.cpp's own startup validation, not assumed.
_KV_QUANT_BLOCK_SIZE = 32

# Bytes per KV-cache element for each supported cache type. f16 is exact (native element size);
# q8_0/q4_0 include the per-32-element fp16 scale factor llama.cpp's block quantization adds
# (q8_0: 32 int8 + 2-byte scale = 34/32; q4_0: 16 bytes of 4-bit data + 2-byte scale = 18/32).
_KV_BYTES_PER_ELEMENT = {"f16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}

# Reserve a share of the memory measured free (with the model already loaded) for the OS, other
# processes, and the compute/attention scratch buffers that scale with context but aren't captured
# by the KV-cache formula alone -- CUR-1965 found this gap empirically (~380MB/~325MB free vs. a
# ~1.4GB back-of-envelope KV-only estimate after a manual context-size fix). This is a deliberate
# fudge factor, not a precise accounting -- said plainly rather than implying more rigor than it has.
_MEMORY_SAFETY_FRACTION = 0.75

# Below this, auto-sizing gives up on being clever and just picks the floor rather than handing
# back something too small to be useful.
_MIN_CTX_SIZE = 512


class ModelSizingError(RuntimeError):
    pass


@dataclass
class ModelProfile:
    n_ctx_train: int
    n_layer: int
    n_embd_head_k: int
    n_embd_k_gqa: int
    n_embd_v_gqa: int
    available_memory_gb: float | None  # measured with THIS model already loaded, before it's freed


@dataclass
class SizingResult:
    ctx_size: int
    parallel: int
    cache_type_k: str | None
    cache_type_v: str | None
    tuning: dict[str, str]


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _check_health(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=3) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _drain(proc: subprocess.Popen, lines: list[str]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)


def _parse_int(text: str, key: str) -> int:
    match = re.search(rf"\b{re.escape(key)}\s*=\s*(\d+)", text)
    if not match:
        raise ModelSizingError(f"could not find {key!r} in llama-server's startup output")
    return int(match.group(1))


def _parse_int_or_list_max(text: str, key: str) -> int:
    """Handles llama.cpp's own print_info formatting: a uniform-per-layer value prints as a plain
    number, but a value that varies by layer (heterogeneous/MoE architectures) prints as
    `[a, b, c, ...]`. Taking the max across layers over-estimates memory for the smaller layers,
    which is the safe direction to be wrong in for a sizing budget."""
    match = re.search(rf"\b{re.escape(key)}\s*=\s*(\[[^\]]*\]|\d+)", text)
    if not match:
        raise ModelSizingError(f"could not find {key!r} in llama-server's startup output")
    raw = match.group(1)
    if raw.startswith("["):
        values = [int(v.strip()) for v in raw.strip("[]").split(",") if v.strip()]
        if not values:
            raise ModelSizingError(f"{key!r} parsed as an empty list in llama-server's startup output")
        return max(values)
    return int(raw)


def probe_model_profile(
    server_binary: Path,
    *,
    model_hf: str | None = None,
    model_path: Path | None = None,
    gpu_layers: str | int | None = None,
    host: str = "127.0.0.1",
    timeout: float = _PROBE_TIMEOUT_SECONDS,
) -> ModelProfile:
    """Spawns server_binary against the already-downloaded model at a small probe context size,
    waits for it to report healthy (loaded), reads its own startup log for the hparams a KV-cache
    sizing decision needs, measures available memory with the model still resident, then
    terminates it. Raises ModelSizingError on anything that stops this from producing a trustworthy
    profile -- callers should treat that as "skip auto-sizing this time," not fatal to the pull
    itself, per this project's fail-loud-but-not-catastrophic posture for a nice-to-have layer.
    """
    if not server_binary.exists():
        raise ModelSizingError(f"llama-server binary not found at {server_binary}")
    if not model_hf and not model_path:
        raise ModelSizingError("probe_model_profile needs model_hf or model_path")

    port = _free_port(host)
    # --parallel 1 here matches what auto-sizing itself is about to configure for real (see
    # compute_sizing) -- probing at the same slot count the real deployment will use makes the
    # memory measurement below representative of it, not of the (different) default.
    cmd = [str(server_binary), "--host", host, "--port", str(port), "--ctx-size", str(_PROBE_CTX_SIZE), "--parallel", "1"]
    if model_path:
        cmd += ["--model", str(model_path)]
    else:
        cmd += ["-hf", str(model_hf)]
    if gpu_layers is not None:
        cmd += ["--gpu-layers", str(gpu_layers)]

    log.info("Probing model metadata for %s", model_path or model_hf)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines: list[str] = []
    reader = threading.Thread(target=_drain, args=(proc, lines), daemon=True)
    reader.start()

    try:
        deadline = time.monotonic() + timeout
        healthy = False
        while time.monotonic() < deadline:
            exit_code = proc.poll()
            if exit_code is not None:
                output = "".join(lines)
                raise ModelSizingError(
                    f"llama-server exited (code={exit_code}) while probing model metadata:\n{output[-2000:]}"
                )
            if _check_health(host, port):
                healthy = True
                break
            time.sleep(_HEALTH_POLL_INTERVAL_SECONDS)

        if not healthy:
            raise ModelSizingError(f"Timed out after {timeout:.0f}s probing model metadata.")

        # Measured now, with the model resident -- this is what the real deployment will have
        # left over for KV cache + scratch buffers once loaded, not idle headroom before load.
        available_memory_gb = _available_memory_gb()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        reader.join(timeout=5)

    output = "".join(lines)
    return ModelProfile(
        n_ctx_train=_parse_int(output, "n_ctx_train"),
        n_layer=_parse_int(output, "n_layer"),
        n_embd_head_k=_parse_int(output, "n_embd_head_k"),
        n_embd_k_gqa=_parse_int_or_list_max(output, "n_embd_k_gqa"),
        n_embd_v_gqa=_parse_int_or_list_max(output, "n_embd_v_gqa"),
        available_memory_gb=available_memory_gb,
    )


def _kv_cache_bytes_per_token(profile: ModelProfile, cache_type_k: str, cache_type_v: str) -> float:
    return profile.n_layer * (
        profile.n_embd_k_gqa * _KV_BYTES_PER_ELEMENT[cache_type_k]
        + profile.n_embd_v_gqa * _KV_BYTES_PER_ELEMENT[cache_type_v]
    )


def compute_sizing(profile: ModelProfile) -> SizingResult:
    """Picks ctx_size, parallel, and KV cache type from a ModelProfile. Never raises -- if
    available_memory_gb couldn't be measured (non-Linux today; _available_memory_gb() only reads
    /proc/meminfo), falls back to the model's trained context with no quantization rather than
    guessing a memory budget that doesn't exist."""
    parallel = 1
    tuning = {
        "parallel": (
            "single-user local device -- 1 slot instead of llama-server's default 4 maximizes "
            "usable context for the same memory budget (CUR-1965)"
        )
    }

    if profile.available_memory_gb is None:
        tuning["ctx_size"] = (
            "could not measure available memory on this platform -- sized to the model's own "
            f"trained context ({profile.n_ctx_train}) with no memory-budget check"
        )
        return SizingResult(
            ctx_size=profile.n_ctx_train, parallel=parallel, cache_type_k=None, cache_type_v=None, tuning=tuning
        )

    budget_bytes = profile.available_memory_gb * (1024**3) * _MEMORY_SAFETY_FRACTION

    def max_ctx_for(cache_type_k: str, cache_type_v: str) -> int:
        bytes_per_token = _kv_cache_bytes_per_token(profile, cache_type_k, cache_type_v) * parallel
        if bytes_per_token <= 0:
            return 0
        return int(budget_bytes // bytes_per_token)

    quantizable = profile.n_embd_head_k > 0 and profile.n_embd_head_k % _KV_QUANT_BLOCK_SIZE == 0

    if not quantizable:
        cache_type_k = cache_type_v = None
        ctx = min(max_ctx_for("f16", "f16"), profile.n_ctx_train)
        tuning["cache_type_k"] = tuning["cache_type_v"] = (
            f"n_embd_head_k={profile.n_embd_head_k} is not a multiple of {_KV_QUANT_BLOCK_SIZE}, so this "
            "architecture can't use a quantized KV cache -- kept at llama-server's f16 default"
        )
    else:
        q8_ctx = max_ctx_for("q8_0", "q8_0")
        q4_ctx = max_ctx_for("q4_0", "q4_0")
        if q8_ctx >= profile.n_ctx_train:
            cache_type_k = cache_type_v = "q8_0"
            ctx = profile.n_ctx_train
            reason = (
                f"q8_0 KV cache reaches the model's full trained context ({profile.n_ctx_train}) "
                "within the available memory budget"
            )
        elif q4_ctx >= profile.n_ctx_train:
            cache_type_k = cache_type_v = "q4_0"
            ctx = profile.n_ctx_train
            reason = (
                "memory headroom is too tight for q8_0 to reach the full trained context "
                f"({profile.n_ctx_train}); q4_0 does"
            )
        elif q4_ctx >= q8_ctx:
            cache_type_k = cache_type_v = "q4_0"
            ctx = q4_ctx
            reason = (
                f"memory headroom doesn't cover the full trained context ({profile.n_ctx_train}) even "
                f"with quantized KV cache -- q4_0 fits more of it than q8_0 ({q4_ctx} vs {q8_ctx})"
            )
        else:
            cache_type_k = cache_type_v = "q8_0"
            ctx = q8_ctx
            reason = (
                f"memory headroom doesn't cover the full trained context ({profile.n_ctx_train}) even "
                f"with quantized KV cache -- sized to the largest context that fits ({ctx})"
            )
        tuning["cache_type_k"] = tuning["cache_type_v"] = reason

    if ctx < _MIN_CTX_SIZE:
        tuning["ctx_size"] = (
            f"memory budget only supports ~{ctx} tokens of context, below the {_MIN_CTX_SIZE} floor -- "
            f"using the floor anyway rather than handing back something too small to be useful "
            f"({profile.available_memory_gb:.2f}GB available, {int(_MEMORY_SAFETY_FRACTION * 100)}% budgeted)"
        )
        ctx = _MIN_CTX_SIZE
    else:
        tuning["ctx_size"] = (
            f"largest context fitting the available memory budget "
            f"({profile.available_memory_gb:.2f}GB available, {int(_MEMORY_SAFETY_FRACTION * 100)}% budgeted), "
            f"capped at the model's trained context ({profile.n_ctx_train})"
        )

    return SizingResult(ctx_size=ctx, parallel=parallel, cache_type_k=cache_type_k, cache_type_v=cache_type_v, tuning=tuning)
