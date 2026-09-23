# aipotluck-local-client

Cross-platform installer and local inference server wrapper. Wraps
[llama.cpp](https://github.com/ggml-org/llama.cpp) (pinned as a git
submodule at `vendor/llama.cpp` for reference/docs; the installer itself
downloads prebuilt release binaries rather than building from source) and
installs a small companion Python service that keeps `llama-server`
running at all times.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design rationale.

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
git clone --recurse-submodules <this-repo>
cd aipotluck-local-client
python3 -m aipotluck.installer.install --backend auto
```

Windows (no prerequisites, installs Python itself if missing):
```powershell
git clone --recurse-submodules <this-repo>
cd aipotluck-local-client
powershell -ExecutionPolicy Bypass -File packaging\windows\install.ps1
```

## One-line public installer (once this repo is hosted publicly)

`install.sh` (Linux/macOS) and `install.ps1` (Windows) at the repo root are
thin bootstrap wrappers meant to be posted publicly and piped straight into
a shell -- no manual `git clone` step required. **They currently ship with
a placeholder repo URL** (`REPLACE_ME`) and refuse to run until that's
replaced with the real hosted URL, or overridden via environment
variable/parameter. Once the repo has a public home (e.g. pushed to
GitHub), usage looks like:

```bash
# Linux / macOS -- standard practice for this kind of installer: no arguments needed.
curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash

# advanced use only -- installer flags, passed straight through:
curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash -s -- --backend cuda --model-hf "org/repo:Q4_K_M"

# pointing at a fork/branch instead of editing the script:
AIPOTLUCK_REPO_URL=https://github.com/<owner>/<repo>.git AIPOTLUCK_REF=main \
  curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/install.sh | bash
```

```powershell
# Windows, no arguments:
irm https://raw.githubusercontent.com/<owner>/<repo>/main/install.ps1 | iex

# Windows, with arguments (irm|iex can't take params directly, use this form):
$script = irm https://raw.githubusercontent.com/<owner>/<repo>/main/install.ps1
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

To actually publish this: push the repo to its public home, then replace
`REPLACE_ME` in both `install.sh` (`DEFAULT_REPO_URL`) and `install.ps1`
(`$DefaultRepoUrl`) with the real clone URL, and swap `<owner>/<repo>` in
the raw.githubusercontent.com URLs above.

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
-v / --verbose
```

After install, the service:

- Exposes a health check at `http://127.0.0.1:8765/healthz` and detailed
  status (including live llama-server supervisor state) at
  `http://127.0.0.1:8765/status`.
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
aipotluck-local-client login     # prompts for Tunnel ID / secret / endpoint, one at a time
aipotluck-local-client status    # asks the running service for its login/tunnel/llama-server state
aipotluck-local-client logout    # unpairs; llama-server and the tunnel stop until you log in again
```

Get the three values from **Settings -> Local Inference -> Add a managed
server** on aipotluck.org -- click "Pair", and it shows you what to paste in
(shown once; it isn't retrievable again after that). `login` also accepts
`--tunnel-id`/`--tunnel-secret`/`--tunnel-endpoint` directly (all three or
none) if you'd rather script it than be prompted -- given after the
subcommand (`login --tunnel-id ...`), not before (`--install-dir` and the
other shared flags below are the same way; see cli.py's own comment on why).

Logging in/out edits `runtime.json`'s `tunnel` section and `logged_in` flag,
downloads the pinned `newt` binary the first time (cached after that), and
restarts the already-installed service so it picks up the change -- it
never touches the llama.cpp install itself. If `aipotluck-local-client` isn't
on PATH yet (a fresh shell hasn't picked it up, or this was a `--no-service`
install, which skips the shim) fall back to
`python3 -m aipotluck.installer.cli login` from this checkout.

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

## Repo layout

```
vendor/llama.cpp/              git submodule, pinned commit (reference + docs)
llama_version.json             pinned release tag + per-platform asset checksums
newt_version.json               pinned newt (Pangolin tunnel client) release + per-platform checksums
install.sh                     public one-line installer entry point (Linux/macOS)
install.ps1                    public one-line installer entry point (Windows)
pyproject.toml                  packaging metadata -- console-script entries for a `pip install .` path only
aipotluck/                      the root package everything below lives under
  installer/
    install.py                    installer CLI entry point -- always produces a logged-out install
    cli.py                         login/logout/status CLI for an already-installed device
    cli_shim.py                    writes the `aipotluck-local-client` wrapper onto PATH (see "CLI on PATH")
    platform_detect.py             OS/arch/GPU-backend detection
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
ARCHITECTURE.md                  full design doc
```
