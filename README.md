# aipotluck-local-client

Cross-platform installer and local inference server wrapper. Wraps
[llama.cpp](https://github.com/ggml-org/llama.cpp) (pinned as a git
submodule at `vendor/llama.cpp`) and installs a small companion Python
service that keeps `llama-server` running at all times.

The installer prefers a prebuilt release binary, but not every host has one it
can actually run: `aipotluck/installer/build_strategy.py` decides this per
host (no published asset for the detected backend/arch, e.g. every
Jetson-class arm64+CUDA board; or the best available asset needs a newer
glibc than the host has), and `aipotluck/installer/source_build.py` builds
`llama-server` from the same pinned `vendor/llama.cpp` tag when it's needed.
See "Confirmed hardware" below for what's actually been run where.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design rationale.

## Confirmed hardware

| Host | OS | Backend | Install path | Status |
| --- | --- | --- | --- | --- |
| x86_64 laptop | Ubuntu 24.04 | CPU | prebuilt `linux-x64-cpu` asset | confirmed |
| NVIDIA Jetson Orin Nano 8GB (Developer Kit Super) | JetPack 6.2.3 (Ubuntu 22.04, glibc 2.35) | CUDA (SM 8.7) | source build (no `linux-arm64-cuda` asset exists, and the `linux-arm64-cpu` asset needs glibc 2.38, which JetPack doesn't have) | confirmed -- real build, `--list-devices` reports `CUDA0: Orin`, a real `-hf` download and chat completion both verified |

Everything else in "Status" just below is implemented against documented OS
conventions but not run on real hardware yet.

## Status

- Linux: implemented and tested (systemd --user service backend, live
  llama-server supervision with crash-restart, verified end-to-end with a
  real model download and inference request).
- macOS: implemented per Apple's documented launchd/plist conventions,
  reusing the identical, already-tested service payload. **Untested** (no
  macOS host available in this environment). See
  `aipotluck/installer/service/launchd.py`, `packaging/macos/README.md`.
- Windows: implemented per documented winget/schtasks/pywin32 conventions,
  including a PowerShell bootstrapper (`packaging/windows/install.ps1`)
  that installs Python itself via winget if none is found, plus a real
  pywin32 Windows Service host for the `--system` path. **Untested** (no
  Windows host available). See `aipotluck/installer/service/windows_service.py`,
  `aipotluck/service/windows_service_host.py`, `aipotluck/installer/python_bootstrap.py`,
  `packaging/windows/README.md`.

All three OS backends drive the exact same portable service payload
(`aipotluck/service/runner.py` -> `aipotluck/service/llama_supervisor.py`) --
only the OS-native "keep this process running" registration differs
(systemd unit / launchd plist / Scheduled Task or Windows Service).

## Quick start

Linux / macOS:
```bash
git clone --recurse-submodules https://github.com/currentai-org/aitpotluck-local-client.git
cd aitpotluck-local-client
python3 -m aipotluck.installer.install --backend auto
```

Windows (no prerequisites, installs Python itself if missing):
```powershell
git clone --recurse-submodules https://github.com/currentai-org/aitpotluck-local-client.git
cd aitpotluck-local-client
powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1
```

## One-line public installer

`install.sh` (Linux/macOS) and `install.ps1` (Windows) at the repo root are
thin bootstrap wrappers meant to be posted publicly and piped straight into
a shell -- no manual `git clone` step required:

```bash
# Linux / macOS -- standard practice for this kind of installer: no arguments needed.
curl -fsSL https://raw.githubusercontent.com/currentai-org/aitpotluck-local-client/main/install.sh | bash

# advanced use only -- installer flags, passed straight through:
curl -fsSL https://raw.githubusercontent.com/currentai-org/aitpotluck-local-client/main/install.sh | bash -s -- --backend cuda --model-hf "org/repo:Q4_K_M"

# pointing at a fork/branch instead of the default:
AIPOTLUCK_REPO_URL=https://github.com/<owner>/<fork>.git AIPOTLUCK_REF=main \
  curl -fsSL https://raw.githubusercontent.com/currentai-org/aitpotluck-local-client/main/install.sh | bash
```

```powershell
# Windows, no arguments:
irm https://raw.githubusercontent.com/currentai-org/aitpotluck-local-client/main/install.ps1 | iex

# Windows, with arguments (irm|iex can't take params directly, use this form):
$script = irm https://raw.githubusercontent.com/currentai-org/aitpotluck-local-client/main/install.ps1
Invoke-Expression "& { $script } -Backend cuda -System"
```

Both scripts assume nothing is installed yet and do their best to fix that
themselves: `install.sh` checks for `git`/`python3` and, if either is
missing, attempts a non-interactive install via whatever package manager it
finds (`apt-get`/`dnf`/`yum`/`pacman`/`apk`/`zypper`/`brew`) before falling
back to a clear, actionable error; `install.ps1` installs `git` via winget
first on Windows if it's missing. Both then clone the repo (into
`~/.aipotluck/src` / `%LOCALAPPDATA%\aipotluck\src`) and hand off
immediately to the already-implemented, already-tested installer
(`aipotluck/installer/install.py` on Linux/macOS, `packaging/windows/install.ps1`
on Windows, which itself finds-or-installs Python before calling
`aipotluck.installer.install`). No installer logic is duplicated in the
one-liners themselves. Re-running either script updates the existing
checkout (`git fetch` + `reset --hard`) rather than re-cloning.

**The install always finishes in a "logged out" state** -- no Pangolin
credentials, and the service holds `llama-server` and the tunnel back until
you pair the device. See "Pairing" below.

## Installer options

The Python installer's flags (the PowerShell wrapper exposes the equivalent
`-System`/`-Backend`/`-ModelHf`/`-Tag`/`-NoStart`/`-Verbose` flags):

```
--tag TAG             Override the pinned llama.cpp release tag
--backend {auto,cpu,cuda,vulkan,rocm}
--install-dir PATH    Override the default per-OS install root
--system              Install machine-wide (requires elevation)
--no-start            Install the service but don't start it
--no-service          Only fetch/extract llama.cpp, skip the Python service
--model-hf REPO:QUANT Hugging Face repo[:quant] for llama-server's own -hf
                       downloader (default: a small Qwen2.5-0.5B placeholder)
--model-path PATH      Use a local GGUF file instead
--ctx-size N           llama-server context size (default: 4096)
--gpu-layers VALUE     llama-server -ngl value: int, 'auto', or 'all'
--server-host / --server-port  llama-server bind address (default 127.0.0.1:8080)
--no-source-build      Fail instead of building llama-server from source when
                       no prebuilt asset is viable for this host
--jobs N               Parallel build jobs for a from-source build (default:
                       auto, capped by available RAM)
--build-timeout SECS   Wall-clock ceiling for a from-source build (default: 5400)
--allow-apt-install    Consent up front to installing missing build deps via
                       'sudo apt-get install' (skips the interactive y/N prompt)
--no-apt-install       Never offer to auto-install build deps, even interactively
-v / --verbose
```

### Building from source (and our own binary cache)

Not every host has a prebuilt release binary it can actually run --
`build_strategy.py` checks this (not just "does an asset exist for this
os/arch/backend", but "does the *specific* asset resolve to something this
glibc can run"). When it doesn't, two things are tried in order before
anything gets compiled:

1. **Our own cache of prebuilt binaries** (`build_cache.py`,
   `llama_custom_builds.json`) -- once we've built a binary for a given
   host/backend/GPU-architecture combination once, there's no reason to pay
   a 30-90 minute compile again on every matching device. If a cached entry
   matches, it's downloaded (cached, checksum-verified, same as any other
   asset) and compilation is skipped entirely.
2. **`source_build.py`** builds `llama-server` from the pinned
   `vendor/llama.cpp` tag when neither an upstream asset nor a cached custom
   one matches.

This is all automatic; you'll see it happen if the installer logs "No
viable upstream asset for this host... but a cached custom build exists" (no
compile) or "No viable prebuilt asset for this host... building llama-server
from source" (a real compile) instead of "Resolved asset:".

Two concrete cases this covers today:

- **No prebuilt asset for the detected backend at all** -- every
  Jetson-class arm64+CUDA board: `nvidia-smi` genuinely works there and CUDA
  is real, but llama.cpp's release CI has never published a
  `linux-arm64-cuda` asset.
- **The best available asset needs a newer glibc than the host has** --
  llama.cpp's release CI builds its Linux arm64 assets against a newer base
  image than most arm64 boards actually run. Confirmed live: the pinned
  `linux-arm64-cpu` asset needs glibc 2.38; JetPack 6.2.3 (Ubuntu 22.04)
  ships 2.35, so even the CPU-only fallback refuses to start there.

A source build needs `cmake`, `git`, a C++17 compiler, OpenSSL dev headers,
and the OpenMP runtime -- plus, per backend, a GPU vendor toolchain: `nvcc`
for CUDA, `glslc` + Vulkan headers for Vulkan, `hipcc` for ROCm. This list
isn't guessed from one host's missing package -- it's cross-referenced
against every Linux job in llama.cpp's own release CI (cpu/cuda/vulkan/rocm),
intersected with what a native build of just the `llama-server` target
actually needs (their CI installs several things -- `ninja-build`,
`python3-venv`, `git-lfs`, `libjpeg-dev` -- for its own portable-build/test
concerns that don't apply here; confirmed `libjpeg-dev` isn't even a real
llama.cpp dependency, since image loading goes through the header-only
`stb_image` instead of libjpeg). See `source_build.py`'s module docstring for
the full evidence trail.

For everything except a GPU vendor toolchain, the installer can install the
gap itself via `sudo apt-get install` -- but only with your explicit
consent: pass `--allow-apt-install` up front, or answer yes to the
interactive prompt it shows otherwise (that prompt never appears, and nothing
is auto-installed, without a real terminal to ask through -- e.g. the public
`curl | bash` one-liner, which has no stdin a person could answer through).
`sudo`'s own password prompt is untouched either way; this installer only
ever supplies the package list, never a password. `--no-apt-install` turns
this off entirely and goes back to just printing the exact `apt-get install`
line for whatever's missing. A GPU vendor toolchain is never auto-installed
under any of these flags -- each is a multi-GB, distro/vendor-specific
install (on Jetson/JetPack, CUDA specifically is the vendor-managed OS image,
not something this installer should touch); a missing `nvcc`/`glslc`/`hipcc`
always stays an instruction.

**Two easy-to-miss gaps if you install these yourself ahead of time, both the
same shape:** a plain, non-`REQUIRED` `find_package(...)` in llama.cpp's own
CMake that degrades **silently** -- configure still succeeds -- rather than
failing loud.

- HTTPS support (used by `-hf` and `--cache-list` -- i.e. this project's own
  `pull`/`list` commands) needs OpenSSL dev headers (`libssl-dev` on
  Debian/Ubuntu). Missing them doesn't fail the build; it only fails later,
  at runtime, the first time something tries to download a model.
- OpenMP multi-threading on the CPU backend needs the OpenMP runtime
  (`libgomp1` on Debian/Ubuntu). Missing it doesn't fail the build either --
  just a `message(WARNING "OpenMP not found")` buried in build output, and
  the binary silently falls back to single-threaded CPU inference, no error,
  just much slower than it should be.

The installer's preflight check catches both before ever starting a build
that can otherwise take well over an hour on a low-power board.

A from-source build is slow and memory-hungry (`--jobs` defaults to a
conservative estimate based on available RAM, since a naive `-j$(nproc)` can
OOM or swap-thrash a low-memory board) and needs real disk space (a few GB
for the build; the installer refuses up front if less than 6GB is free
rather than fail 40 minutes in from `ENOSPC`). Re-running the installer
after a successful source build is fast -- it's keyed to the exact
tag+backend combination and skips straight to reusing the binary already
built.

#### Custom binary cache (`llama_custom_builds.json`)

This is a separate, optional manifest alongside `llama_version.json`, for
binaries **we've** built and hosted ourselves -- not upstream releases. Same
shape (`release_base_url` + `tag` + `{key: {file, sha256}}`), same
download/verify/extract code path (`fetch.py`), just a different source and
a different key format: `{os}-{arch}-{backend}`, plus `-sm{N}` for CUDA
(e.g. `linux-arm64-cuda-sm87` for a Jetson Orin) -- since different
Jetson-class generations (Xavier SM 7.2, Orin SM 8.7, Thor SM 10.x) are not
interchangeable; a binary built for one `CMAKE_CUDA_ARCHITECTURES` value
will not run correctly targeting a different one.

The file is entirely optional -- a checkout without one (or with an empty
`assets` map) just falls through to a real source build every time, exactly
as if this feature didn't exist.

To add an entry after building on a new host:

1. Run the installer there (`--no-service` is fine for this) and let it
   finish a real source build.
2. Package the *contents* of the build's `bin/` directory into a flat
   top-level `llama-<tag>/` directory, `tar.gz`'d -- this matches upstream's
   own release archive layout exactly (`llama-b10989/llama-server`,
   `llama-b10989/libggml-cuda.so.0`, etc., no nested `bin/`), so
   `fetch.find_binary`'s recursive search works unchanged either way:
   ```bash
   cd /path/to/llama.cpp-build
   mkdir /tmp/llama-<tag> && cp -a bin/. /tmp/llama-<tag>/
   tar -czf llama-<tag>-bin-<key>.tar.gz -C /tmp llama-<tag>
   sha256sum llama-<tag>-bin-<key>.tar.gz
   ```
   This only works because the build sets `CMAKE_INSTALL_RPATH=$ORIGIN` (see
   above) -- without it, the packaged binary would only work from the exact
   path it was originally built at.
3. Upload the archive as an asset on a GitHub release of this repo (a
   `custom-builds` tag, reused across entries as more platforms are added).
4. Add an entry to `llama_custom_builds.json` under that key, with the file
   name and the sha256 from step 2.

After install, the service:

- Exposes a health check at `http://127.0.0.1:8765/healthz`, detailed
  status (including live llama-server supervisor state) at
  `http://127.0.0.1:8765/status`, and a full system/capability fingerprint
  at `http://127.0.0.1:8765/capabilities` -- OS/distro, arch, glibc
  version, CPU/memory/disk, GPU backend + CUDA compute capability, every
  build tool `source_build.py` checks for, and the actual install strategy
  this device would resolve to right now (upstream asset vs. our own binary
  cache vs. a real source build, and why) alongside what's *currently*
  installed. Built from the exact same detection code the installer itself
  uses (`aipotluck/diagnostics.py`), so this is a live, remotely-queryable
  answer to "what would happen if I reinstalled this right now" -- useful
  for diagnosing a device that's already in a broken state, since every
  section degrades independently (`{"error": ...}`) rather than the whole
  endpoint failing if one probe does.
- **Actively supervises `llama-server`**: starts it on service startup,
  polls `/health` every 5s, and restarts it automatically on crash with
  exponential backoff (1s, 2s, 5s, 10s, 20s, 30s, 60s -- resets if the
  process stayed up 2+ minutes). On service shutdown, llama-server is
  terminated cleanly (SIGTERM, then SIGKILL after 15s if unresponsive).
- Two-tier self-healing: systemd/launchd/Scheduled Task/Windows Service
  restarts the Python service process itself if it dies; the Python
  service's `LlamaSupervisor` restarts just `llama-server` if it crashes.
  Between the two, llama-server should stay running continuously.

`llama-server`'s own OpenAI-compatible API is reachable directly at
`http://127.0.0.1:8080` (or whatever `--server-host`/`--server-port` you set).

## Pairing: login / logout / status

The installer never asks for Pangolin credentials -- every install finishes
logged out, and the service (`aipotluck/service/runner.py`) won't start
`llama-server` or the tunnel (`newt`) until you log in. The installer drops
a small `aipotluck-local-client` wrapper onto your PATH (see "CLI on PATH"
below) that runs the thin login/logout/status CLI
(`aipotluck/installer/cli.py`) against whatever device is installed here:

```bash
aipotluck-local-client login     # prompts for the pairing JSON -- paste it, press Enter
aipotluck-local-client status    # asks the running service for its login/tunnel/llama-server state
aipotluck-local-client logout    # unpairs; llama-server and the tunnel stop until you log in again
```

Get the pairing JSON from **Settings -> Local Inference -> Add a managed server** on
aipotluck.org -- click "Pair", then its **Copy** button puts
`{"tunnelId": "...", "tunnelSecret": "...", "tunnelEndpoint": "..."}` on your clipboard (shown
once; it isn't retrievable again after that). Paste that straight into the `login` prompt. Two
alternatives to pasting: `login --credentials-file creds.json` reads the same JSON shape from a
file, and `login --tunnel-id X --tunnel-secret Y --tunnel-endpoint Z` accepts the three values as
separate flags for scripting (all three or none). All three forms are mutually exclusive, and
every flag here is given after the subcommand (`login --tunnel-id ...`), not before
(`--install-dir` and the other shared flags below are the same way; see cli.py's own comment on
why).

Logging in/out edits `runtime.json`'s `tunnel` section and `logged_in` flag,
downloads the pinned `newt` binary the first time (cached after that), and
restarts the already-installed service so it picks up the change -- it
never touches the llama.cpp install itself. If `aipotluck-local-client` isn't
on PATH yet (a fresh shell hasn't picked it up, or this was a `--no-service`
install, which skips the shim) fall back to
`python3 -m aipotluck.installer.cli login` from this checkout.

## Models: pull / list

Neither the installer nor the service ever downloads model weights on their own -- the installer
only fetches the llama.cpp *binaries*, and `llama-server` itself lazily downloads whatever
`-hf`/`--model` it's configured with the first time it actually starts. `pull` and `list` (in
`aipotluck/installer/model_pull.py`) exist to trigger and inspect that ahead of time:

```bash
aipotluck-local-client pull bartowski/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M   # download + activate
aipotluck-local-client list                                              # what's cached locally
```

`pull` accepts any Hugging Face `repo` or `repo:quant` target -- the exact same shorthand
`--model-hf` already takes -- and hands it straight to `llama-server`'s own `-hf` downloader
rather than re-implementing Hugging Face's GGUF-resolution logic (matching a quant string to the
right file, split-GGUF handling, etc). It blocks until the model has actually finished
downloading *and* loading successfully (a real `/health` check on a throwaway port, not just "the
download finished"), then sets it as `runtime.json`'s active model and restarts the
already-installed service, the same "edit config, restart the service" shape `login`/`logout`
use. Independent of login state -- pulling a model doesn't need pairing.

`list` reads the same on-disk cache `-hf` writes into and `pull` reads from -- a real Hugging
Face Hub cache layout (`$LLAMA_CACHE` / `$HF_HUB_CACHE` / `$HUGGINGFACE_HUB_CACHE` /
`$HF_HOME/hub` / `$XDG_CACHE_HOME/huggingface/hub` / `~/.cache/huggingface/hub`, in that order --
see `vendor/llama.cpp/common/hf-cache.cpp`) -- via `llama-server`'s own `--cache-list` flag, for
the same "don't re-implement it" reason `pull` reuses `-hf`. The currently active model (whatever
`runtime.json`'s `llama_cpp.model_hf` is set to) is marked `(active)`.

## CLI on PATH

`aipotluck-local-client` (`aipotluck/installer/cli_shim.py`) is written
during install to `~/.local/bin/aipotluck-local-client` (`--system`:
`/usr/local/bin`) on Linux/macOS, or `%LOCALAPPDATA%\aipotluck\bin\aipotluck-local-client.cmd`
(`--system`: `%ProgramFiles%\aipotluck\bin`) on Windows -- a small wrapper
that execs the Python interpreter the installer resolved against
`cli.py`'s own file path directly, so it works regardless of what
`python3` means in whatever shell you're in, and regardless of CWD (no
`-m`/PYTHONPATH dependency). Not a `pip install`-based console script:
this project has no build step anywhere else either, and downloads
prebuilt binaries rather than building from source (same reasoning as
llama.cpp/newt).

On Linux/macOS, the per-user target (`~/.local/bin`) is only added to your
PATH automatically on the installer's best-effort check -- if it warns that
it isn't there, add it to your shell profile once:
```bash
export PATH="$HOME/.local/bin:$PATH"
```
On Windows the installer edits your user `PATH` in the registry directly
(no elevation needed) and broadcasts the change; a brand new terminal
always sees it even if the current one doesn't. `--system` on any OS
writes to a machine-wide location that's normally already on PATH
(`/usr/local/bin`) or prints the manual step to add it (Windows, since
that needs elevation this process may not have).

## Tests

```bash
pip install -e ".[dev]"    # or: pip install --user pytest
pytest
```

No real network and no real systemd/launchd. Every OS/network/service-manager boundary
(`fetch`/`newt_fetch`, `get_service_manager`, `LlamaSupervisor`/`NewtSupervisor`,
`urllib.request.urlopen`) is mocked or swapped for a lightweight fake; only real filesystem
writes happen, always rooted under pytest's own `tmp_path` -- the suite never touches the actual
per-OS install locations (`~/.local/bin`, `~/.config/aipotluck`, a real systemd unit, etc.). One
deliberate exception: `test_model_pull.py` and `test_source_build.py` run a real subprocess (a
tiny stand-in script playing the part of `llama-server`/`cmake`) and, for the latter, a real
spawned grandchild process to prove a build timeout actually kills the whole process tree --
spawn/health-poll/terminate (or configure/build/kill) orchestration is exactly the kind of thing a
mock can make *look* correct while testing nothing, so those modules are exercised for real
instead. Covers `platform_detect.py` (including glibc/CUDA-compute-capability detection),
`build_strategy.py`'s prebuilt-vs-source-build decision, `build_cache.py`'s custom-binary-cache
lookup, `layout.py`, `cli.py` (including the argparse regression -- see `test_cli_argparse.py`'s
docstring), `cli_shim.py`, `model_pull.py`'s `pull`/`list` orchestration, `install.py`'s
`--no-service`/source-build/binary-cache/real install paths, `service/runner.py`'s login-gating,
`/status` secret redaction and the real-HTTP `/capabilities` route, and `diagnostics.py`'s
per-section failure isolation.

Not covered: the OS-native `ServiceManager` backends themselves (`systemd.py`/`launchd.py`/
`windows_service.py` — installing a real unit/plist/Scheduled Task), and the real download+
checksum+extract path in `fetch.py`/`newt_fetch.py`. Both need a real OS service manager or real
network access respectively to verify meaningfully; see this repo's own `ARCHITECTURE.md` for how
those were verified by hand instead (real installs, real `systemctl`/`launchctl`, a real model
download and inference request).

## Repo layout

```
vendor/llama.cpp/              git submodule, pinned commit (reference + docs)
llama_version.json             pinned release tag + per-platform asset checksums
llama_custom_builds.json        our own cached prebuilt binaries (see "Building from source")
newt_version.json               pinned newt (Pangolin tunnel client) release + per-platform checksums
install.sh                     public one-line installer entry point (Linux/macOS)
install.ps1                    public one-line installer entry point (Windows)
pyproject.toml                  packaging metadata -- console-script entries for a `pip install .` path only
aipotluck/                      the root package everything below lives under
  diagnostics.py                 GET /capabilities fingerprint -- reuses installer/ detection code
  installer/
    install.py                    installer CLI entry point -- always produces a logged-out install
    cli.py                         login/logout/status CLI for an already-installed device
    cli_shim.py                    writes the `aipotluck-local-client` wrapper onto PATH (see "CLI on PATH")
    platform_detect.py             OS/arch/GPU-backend/glibc/CUDA-compute-capability detection
    build_strategy.py              decide prebuilt-asset vs. build-from-source per host
    source_build.py                configure+build llama-server from vendor/llama.cpp
    build_cache.py                  check our own prebuilt-binary cache before compiling
    fetch.py                       download+checksum+extract llama.cpp releases
    newt_fetch.py                  download+checksum-verify the pinned newt binary
    layout.py                      per-OS install paths
    python_bootstrap.py            find/install a suitable Python (Windows: via winget)
    service/
      base.py                       ServiceManager ABC
      systemd.py                    Linux (tested)
      launchd.py                    macOS (stub, untested)
      windows_service.py            Windows: schtasks (no-admin) + pywin32 (--system)
  service/
    runner.py                       portable AipotluckServiceRunner (HTTP status + both supervisors,
                                     gated on runtime.json's "logged_in" flag)
    llama_supervisor.py             LlamaSupervisor: starts/restarts/health-polls llama-server
    newt_supervisor.py              NewtSupervisor: starts/restarts the newt tunnel client
    aipotluck_service.py            CLI entry point (systemd/launchd/schtasks invoke this directly)
    windows_service_host.py         pywin32 ServiceFramework wrapping the same runner
packaging/
  windows/install.ps1             Windows bootstrapper (installs Python if missing, then installs)
  windows|macos|linux/README.md   per-OS implementation notes
tests/                            unit tests -- see "Tests" above
ARCHITECTURE.md                  full design doc
```
