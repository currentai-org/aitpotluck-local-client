"""Build llama-server from source when build_strategy decides no prebuilt asset is viable.

Confirmed viable against a real Jetson Orin Nano (JetPack 6.2.3): a manually-built llama-server
there already links libggml-cuda.so against the JetPack-bundled CUDA 12.6 toolkit at
CMAKE_CUDA_ARCHITECTURES=87 and serves real inference. This module automates exactly that path:
check the host actually has what a build needs (never silently `sudo apt-get install` on the
caller's behalf -- many hosts, including that Jetson, require an interactive sudo password, so a
non-interactive installer would just hang or fail confusingly), check out the pinned tag into the
vendor/llama.cpp submodule, configure + build with resource-aware parallelism, and locate the
resulting binary the same way fetch.py locates one inside an extracted release archive.

The one build-time gotcha worth a full explanation (see check_build_prerequisites): llama.cpp's
HTTPS support -- which `-hf`/`--cache-list`, and therefore this project's own pull/list commands,
depend on -- is compiled in via a plain, non-REQUIRED find_package(OpenSSL). Configure without
libssl-dev installed SUCCEEDS, silently, and the gap only shows up later as a runtime error the
first time something tries to download a model. Preflight checks for the header file before ever
starting cmake specifically to avoid burning a 30-90 minute build on a binary with this missing.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from aipotluck.installer.fetch import find_binary

log = logging.getLogger("aipotluck.installer.source_build")

DEFAULT_BUILD_TIMEOUT_SECONDS = 5400  # 90 min -- a cold CUDA build on an 8GB Jetson-class board is slow.
MIN_FREE_DISK_GB = 6
_OPENSSL_HEADER_CANDIDATES = (
    Path("/usr/include/openssl/ssl.h"),
    Path("/usr/local/include/openssl/ssl.h"),
)


class BuildError(RuntimeError):
    pass


def _find_cxx_compiler() -> str | None:
    return shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")


def find_nvcc() -> Path | None:
    """nvcc isn't reliably on PATH even when the CUDA toolkit is genuinely installed -- confirmed
    live on the reference Jetson: `which nvcc` came back empty while
    /usr/local/cuda-12.6/bin/nvcc worked fine and reported a real version. Prefer PATH when it's
    there, then fall back to the standard /usr/local/cuda*/bin layout (checked newest-first, since
    a host can have several toolkit versions installed side by side)."""
    on_path = shutil.which("nvcc")
    if on_path:
        return Path(on_path)
    for candidate in sorted(Path("/usr/local").glob("cuda*/bin/nvcc"), reverse=True):
        if candidate.is_file():
            return candidate
    return None


def _has_openssl_headers() -> bool:
    if any(p.exists() for p in _OPENSSL_HEADER_CANDIDATES):
        return True
    if shutil.which("pkg-config"):
        try:
            return subprocess.run(
                ["pkg-config", "--exists", "openssl"], timeout=5,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


def check_build_prerequisites(*, want_cuda: bool) -> list[str]:
    """Return a list of human-readable problems; empty means the host is ready to build. Every
    entry names both the gap and the exact fix (never just "cmake missing") -- the caller must
    print these and stop, not attempt to resolve them itself, since fixing most of them needs
    `sudo apt-get install`, and this installer never invokes sudo on the caller's behalf."""
    problems: list[str] = []
    if shutil.which("cmake") is None:
        problems.append("cmake not found (sudo apt-get install -y cmake)")
    if shutil.which("git") is None:
        problems.append("git not found (sudo apt-get install -y git)")
    if _find_cxx_compiler() is None:
        problems.append("no C++ compiler found (sudo apt-get install -y build-essential)")
    if not _has_openssl_headers():
        problems.append(
            "OpenSSL development headers not found -- llama-server's -hf downloader and "
            "--cache-list (this CLI's pull/list commands) compile out silently without them, with "
            "no build error, only a later runtime one (sudo apt-get install -y libssl-dev)"
        )
    if want_cuda and find_nvcc() is None:
        problems.append(
            "nvcc not found -- CUDA was detected (nvidia-smi works) but the CUDA toolkit compiler "
            "isn't on PATH or under /usr/local/cuda*/bin. On JetPack this ships with the OS image; "
            "check `ls /usr/local/cuda*/bin/nvcc` and add its directory to PATH."
        )
    return problems


def check_free_disk_gb(path: Path, *, minimum_gb: float = MIN_FREE_DISK_GB) -> float | None:
    """Free space (in GiB) at `path`, or None if it can't be determined. Doesn't raise on its
    own -- callers decide what "not enough" means; this only exists because a from-source build
    was confirmed to run on a Jetson with only 14GB free system-wide, and a build that dies
    two-thirds through from ENOSPC is a much worse experience than refusing up front."""
    try:
        return shutil.disk_usage(path).free / (1024**3)
    except OSError:
        return None


def recommended_jobs(*, want_cuda: bool) -> int:
    """A conservative -j value. nvcc is memory-hungry enough that the naive `-j$(nproc)` default
    can OOM (or thrash into swap, which is slower than fewer parallel jobs) on an 8GB board like
    the Jetson Orin Nano this was built against -- 6 cores but only ~5.6GB available in practice.
    Budgets more RAM per job for a CUDA build (nvcc) than a plain C++ compile."""
    cpu_count = os.cpu_count() or 1
    available_gb = _available_memory_gb()
    if available_gb is None:
        return cpu_count
    budget_per_job = 2.0 if want_cuda else 1.0
    return max(1, min(cpu_count, int(available_gb // budget_per_job)))


def _available_memory_gb() -> float | None:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / (1024**2)
    except (OSError, ValueError, IndexError):
        return None
    return None


def ensure_source_checked_out(repo_root: Path, tag: str) -> Path:
    """Point vendor/llama.cpp's submodule checkout at the pinned release tag, shallowly. Requires
    the submodule to already be initialized (README.md's documented `git clone --recurse-submodules`
    does this); a bare/uninitialized vendor/llama.cpp gets a clear, actionable error instead of a
    confusing git failure."""
    vendor_dir = repo_root / "vendor" / "llama.cpp"
    if not (vendor_dir / ".git").exists():
        raise BuildError(
            f"{vendor_dir} is not a populated git checkout (no .git). Run "
            f"'git -C {repo_root} submodule update --init --recursive' first."
        )
    log.info("Checking out llama.cpp %s into %s for source build", tag, vendor_dir)
    _run(["git", "-C", str(vendor_dir), "fetch", "--depth", "1", "origin", f"refs/tags/{tag}"], timeout=120)
    _run(["git", "-C", str(vendor_dir), "checkout", "--detach", "FETCH_HEAD"], timeout=30)
    return vendor_dir


def _git_commit(vendor_dir: Path) -> str:
    result = _run(["git", "-C", str(vendor_dir), "rev-parse", "HEAD"], timeout=15)
    return result.stdout.strip()


def _run(args: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except OSError as exc:
        raise BuildError(f"Could not run {' '.join(args)}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"{' '.join(args)} did not finish within {timeout:.0f}s") from exc
    if result.returncode != 0:
        raise BuildError(f"{' '.join(args)} failed (exit {result.returncode}):\n{result.stderr.strip()}")
    return result


def _configure(source_dir: Path, build_dir: Path, *, cuda_arch: str | None, cmake_binary: str) -> None:
    args = [
        cmake_binary, "-S", str(source_dir), "-B", str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DLLAMA_BUILD_SERVER=ON",
        "-DLLAMA_BUILD_TESTS=OFF",
        "-DLLAMA_BUILD_EXAMPLES=OFF",
        "-DLLAMA_BUILD_TOOLS=OFF",
        "-DLLAMA_OPENSSL=ON",
    ]
    if cuda_arch:
        args += ["-DGGML_CUDA=ON", f"-DCMAKE_CUDA_ARCHITECTURES={cuda_arch}"]
        # cmake's own CUDA-language detection only searches PATH -- confirmed live on the
        # reference Jetson: nvcc is genuinely installed but not on PATH there (it's under
        # /usr/local/cuda-12.6/bin), find_nvcc()'s glob fallback locates it fine, but without this
        # flag cmake still fails with CMAKE_CUDA_COMPILER-NOTFOUND. Always pass it when we have it
        # rather than only as a fallback -- it's strictly more robust than letting cmake redo a
        # weaker version of the same search.
        nvcc = find_nvcc()
        if nvcc:
            args += [f"-DCMAKE_CUDA_COMPILER={nvcc}"]
    log.info("Configuring: %s", " ".join(args))
    _run(args, timeout=300)


def _build(build_dir: Path, *, jobs: int, timeout: float, cmake_binary: str) -> None:
    args = [cmake_binary, "--build", str(build_dir), "--target", "llama-server", "-j", str(jobs)]
    log.info("Building llama-server with -j%d (this can take a long time -- output follows live)", jobs)
    # A build process tree (cmake -> make/ninja -> cc1/nvcc/ld) can be deep; start a new session so
    # a timeout can kill the *whole tree* via its process group, not just cmake's direct child,
    # which would otherwise leave orphaned compiler workers running. Output is left uncaptured
    # (inherits this process's stdout/stderr) for the same reason model_pull.py leaves llama-server's
    # own download progress uncaptured: a silent 30-90 minute wait reads as a hang.
    proc = subprocess.Popen(args, start_new_session=True)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        raise BuildError(f"Build did not finish within {timeout:.0f}s (killed)")
    if proc.returncode != 0:
        raise BuildError(f"Build failed (exit {proc.returncode}) -- see output above")


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
        if proc.poll() is None:
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    proc.wait(timeout=10)


def _cleanup_intermediate_objects(build_dir: Path) -> None:
    """Reclaim disk by deleting CMake's per-target object directories once the binary we actually
    want is built and located -- real motivation, not theoretical: the reference Jetson build this
    was validated against runs with only 14GB free system-wide, and a from-source CUDA build's
    object files are the single biggest thing this tool ever writes to disk (the hand-built
    reference on that device: 659MB). This forfeits `cmake --build`'s own incremental-rebuild
    cache; that's an acceptable trade because our own idempotency (the fingerprint marker) already
    treats "flags changed" as "rebuild from scratch," never as "relink.\""""
    for cmake_files_dir in build_dir.rglob("CMakeFiles"):
        if cmake_files_dir.is_dir():
            shutil.rmtree(cmake_files_dir, ignore_errors=True)


def build_llama_server(
    source_dir: Path,
    build_dir: Path,
    *,
    fingerprint: str,
    cuda_arch: str | None = None,
    jobs: int | None = None,
    timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
    cmake_binary: str = "cmake",
) -> Path:
    """Configure + build llama-server from `source_dir` into `build_dir`, returning the built
    binary's path. Idempotent across calls with the same `fingerprint` (e.g. "<git-sha>:<cuda_arch>")
    -- a matching marker plus an existing binary skips straight to returning it, so re-running the
    installer doesn't repeat a 90-minute build for no reason."""
    marker = build_dir / ".build_fingerprint"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == fingerprint:
        try:
            return find_binary(build_dir, "llama-server")
        except FileNotFoundError:
            log.warning("Build marker present but binary missing under %s; rebuilding", build_dir)

    jobs = jobs if jobs is not None else recommended_jobs(want_cuda=cuda_arch is not None)
    build_dir.mkdir(parents=True, exist_ok=True)
    _configure(source_dir, build_dir, cuda_arch=cuda_arch, cmake_binary=cmake_binary)
    _build(build_dir, jobs=jobs, timeout=timeout, cmake_binary=cmake_binary)

    binary = find_binary(build_dir, "llama-server")
    marker.write_text(fingerprint, encoding="utf-8")
    _cleanup_intermediate_objects(build_dir)
    log.info("Built llama-server: %s", binary)
    return binary


def ensure_llama_server_built(
    repo_root: Path,
    build_root: Path,
    tag: str,
    *,
    cuda_arch: str | None = None,
    jobs: int | None = None,
    timeout: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
) -> Path:
    """Top-level entry point install.py calls: check out the pinned tag, then build. build_root is
    a persistent directory (e.g. under the install layout's install_root) distinct from the
    vendor/llama.cpp checkout itself, so multiple installs/backends don't clobber one build."""
    source_dir = ensure_source_checked_out(repo_root, tag)
    commit = _git_commit(source_dir)
    fingerprint = f"{commit}:{cuda_arch or 'cpu'}"
    return build_llama_server(
        source_dir, build_root, fingerprint=fingerprint, cuda_arch=cuda_arch, jobs=jobs, timeout=timeout,
    )
