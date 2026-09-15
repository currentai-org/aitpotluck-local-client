# aipotluck-local-client — Architecture Plan

Status: PLANNING (not yet implemented). This doc captures findings from
examining llama.cpp's install methods and proposes how we wrap it.

## 1. What llama.cpp actually gives us to wrap

llama.cpp has **no unified installer script**. `docs/install.md` lists four
paths, none of which is "run this script":

| Method      | Win | Mac | Linux | Notes |
|-------------|-----|-----|-------|-------|
| conda-forge | Y   | Y   | Y     | needs conda/mamba/pixi already present |
| winget      | Y   |     |       | Windows only |
| Homebrew    |     | Y   | Y (linuxbrew) | needs brew already present |
| Nix         |     | Y   | Y     | niche |
| build from source | Y | Y | Y | `cmake -B build && cmake --build build --config Release` |

All of the package-manager paths ultimately resolve to the same artifact:
GitHub Releases at `ggml-org/llama.cpp`, tagged `bNNNNN` (build number, not
semver). Each release publishes a flat archive per platform/backend, e.g.:

```
llama-b10989-bin-macos-arm64.tar.gz
llama-b10989-bin-macos-x64.tar.gz
llama-b10989-bin-ubuntu-x64.tar.gz
llama-b10989-bin-ubuntu-arm64.tar.gz
llama-b10989-bin-ubuntu-cuda-12.8-x64.tar.gz
llama-b10989-bin-ubuntu-vulkan-x64.tar.gz
llama-b10989-bin-ubuntu-rocm-10.0-x64.tar.gz
llama-b10989-bin-win-cpu-x64.zip
llama-b10989-bin-win-cuda-13.4-x64.zip
llama-b10989-bin-win-vulkan-x64.zip
llama-b10989-bin-win-cpu-arm64.zip
... (sycl, openvino, s390x, android, xcframework, cudart runtime bundles, ui)
```

Confirmed by extracting `llama-b10989-bin-ubuntu-x64.tar.gz`: it's a flat
directory — `llama-server`, `llama-cli`, `llama-quantize`, `llama-bench`, a
pile of `libggml-cpu-<arch feature level>.so` variants (dynamic CPU dispatch),
`libllama*.so`, `libmtmd.so`, `LICENSE`. No install script, no manifest. You
place it somewhere and run the binary directly with `LD_LIBRARY_PATH`
pointing at the same dir (or keep binaries+libs colocated, which works
out-of-the-box on Linux/macOS via rpath, and via same-dir DLL search on
Windows).

**Conclusion:** "the llama.cpp installer" we stick close to is really just:
*download the right release asset for this OS/arch/backend, verify it,
extract it, keep it out of the way of user data*. That's exactly what brew's
formula and the winget manifest do under the hood. We replicate that logic
ourselves rather than shelling out to brew/winget, because:

- brew/winget/conda are not guaranteed present (chicken-and-egg for a
  cross-platform *installer*), and their presence/version drift is outside
  our control.
- Building from source requires a C++ toolchain we can't assume end users
  have — bad for "reduce friction."
- The GitHub Release binaries are the one path guaranteed on all three OSes
  with zero prerequisites beyond network access.

We keep an **escape hatch**: if the user already has `brew`/`winget`
available and prefers system-managed installs, offer `--via brew` /
`--via winget` flags that just shell out. Default path is our own
download+verify+extract, because it's the one we can make deterministic,
pinned, and offline-repeatable (cache the archive).

## 2. Version pinning strategy

- Pin a specific release tag (e.g. `b10989`) in a config file
  (`llama_version.json` at repo root), not "latest" — reproducibility.
- `scripts/update_llama_version.py` (later) bumps the pin deliberately.
- Submodule `vendor/llama.cpp` tracks source at that same tag for reference /
  future custom builds, but the installer does **not** build from the
  submodule by default — it downloads the matching prebuilt release. The
  submodule exists so we can (a) read server CLI flags/docs programmatically,
  (b) fall back to source build when no prebuilt asset matches the host
  (e.g. exotic Linux arch), (c) later carry patches if we ever need a custom
  build.

## 3. Installer wrapper design

```
aipotluck-local-client/
  vendor/llama.cpp/            # git submodule, pinned commit
  llama_version.json           # {"tag": "b10989", "assets": {...sha256s...}}
  installer/
    __init__.py
    platform_detect.py         # os, arch, gpu-backend probing
    fetch.py                   # download+checksum+extract release asset
    layout.py                  # where things live on disk per-OS
    install.py                 # CLI entry: `python -m installer.install`
    service/
      __init__.py
      base.py                  # ServiceManager ABC: install/uninstall/start/stop/status
      systemd.py                # Linux
      launchd.py                # macOS
      windows_service.py        # Windows
  service/
    aipotluck_service.py       # the "blank" python service payload (for now: no-op / health loop)
  packaging/
    windows/                   # future: Inno Setup / MSI wrapper
    macos/                     # future: .pkg / notarization
    linux/                     # future: .deb/.rpm/AppImage
  ARCHITECTURE.md
```

### 3.1 Platform/arch/backend detection (`platform_detect.py`)
- `platform.system()` → `Linux` / `Darwin` / `Windows`
- `platform.machine()` → normalize to `x64` / `arm64`
- GPU backend probe (best-effort, non-fatal if inconclusive):
  - NVIDIA: `nvidia-smi` present → cuda asset
  - AMD: `rocminfo` / `/dev/kfd` → rocm asset (Linux only for now)
  - Apple Silicon: Metal is built into the `macos-arm64` asset already (no
    separate variant) — nothing to detect, just pick macos-arm64.
  - Vulkan: generic fallback for non-NVIDIA/AMD GPU Linux/Windows if desired.
  - Default/no GPU detected → plain CPU asset (`ubuntu-x64`, `win-cpu-x64`,
    `macos-*`). CPU asset ships multiple `libggml-cpu-<isa>.so` variants and
    llama.cpp dynamically dispatches to the best one at runtime — so plain
    CPU asset is always a safe baseline even when GPU detection is wrong.
- Asset-name mapping table lives in `llama_version.json` so it's data, not
  code, and survives across version bumps without touching logic.

### 3.2 Fetch + verify (`fetch.py`)
- Download from `https://github.com/ggml-org/llama.cpp/releases/download/<tag>/<asset>`.
- Verify sha256 against `llama_version.json` (we compute and pin these
  ourselves at version-bump time — llama.cpp releases don't publish
  checksums, so we generate our own manifest and treat first-download-per-tag
  as trust-on-first-use, recorded for future audits).
- Extract to a versioned dir so multiple versions can coexist:
  `<install_root>/llama.cpp/<tag>/<platform-asset>/`.
- Idempotent: re-running with the same tag/asset is a no-op if already
  extracted and checksum matches.

### 3.3 Install locations (`layout.py`), per OS, no-admin-by-default
- Linux: `~/.local/share/aipotluck/` (binaries+libs), config in
  `~/.config/aipotluck/`, logs in `~/.local/state/aipotluck/logs/`
  (XDG dirs).
- macOS: `~/Library/Application Support/aipotluck/`, logs in
  `~/Library/Logs/aipotluck/`.
- Windows: `%LOCALAPPDATA%\aipotluck\` for binaries/config,
  `%LOCALAPPDATA%\aipotluck\logs\`.
- A `--system` flag can later target machine-wide install paths
  (`/opt/aipotluck`, `/Library/Application Support`,
  `%ProgramData%\aipotluck`) for the real "system service" case, requiring
  elevation. Default stays per-user/no-admin to minimize friction, matching
  llama.cpp's own brew/winget UX (no admin prompt).

### 3.4 Top-level installer flow (`install.py`)
1. Detect platform/arch/backend.
2. Resolve asset name from `llama_version.json`.
3. Fetch+verify+extract llama.cpp release into versioned install dir.
4. Write/refresh a small `runtime.json` pointing at the resolved
   `llama-server` binary path + lib dir for this OS.
5. Install our Python service (section 4) pointed at that `runtime.json`.
6. Optionally start the service immediately (`--start` flag, default on).
7. Print a summary: install dir, service status, server URL
   (`http://127.0.0.1:8080` by default, matching llama.cpp's own default so
   we don't invent new conventions).

CLI surface (argparse, no exotic deps):
```
python -m installer.install [--tag b10989] [--backend auto|cpu|cuda|vulkan|rocm]
                             [--install-dir PATH] [--system] [--no-start]
                             [--via auto|direct|brew|winget]
```

## 4. Python service injection design

"Tack on our bare python service" after the llama.cpp install finishes.
Scope for now: **blank/no-op service** — just proves out the cross-platform
service lifecycle (install, autostart, start/stop/status, logs) so the real
transport layer has a place to live later. It should NOT try to manage
llama-server itself yet beyond optionally shelling out to start it — keep
concerns separated: llama.cpp does inference serving, our service is future
transport/orchestration surface.

### 4.1 Service abstraction
`installer/service/base.py` defines:
```python
class ServiceManager(ABC):
    def install(self, exec_path: str, args: list[str], name: str, user_scope: bool) -> None: ...
    def uninstall(self, name: str) -> None: ...
    def start(self, name: str) -> None: ...
    def stop(self, name: str) -> None: ...
    def status(self, name: str) -> ServiceStatus: ...
```
`install.py` picks the concrete implementation via `platform.system()`.

### 4.2 Linux — systemd user unit (default), system unit optional
- Default: `~/.config/systemd/user/aipotluck.service`, enabled with
  `systemctl --user enable --now aipotluck.service`. No root needed. Starts
  at user login (or with lingering enabled via
  `loginctl enable-linger $USER` for boot-time start without login — call
  this out explicitly, don't silently enable linger).
- `--system` flag: writes `/etc/systemd/system/aipotluck.service`, needs
  sudo, runs at boot regardless of login — this is the "real" system service
  case the user mentioned.
- Unit just runs `python3 <path>/aipotluck_service.py`; `Restart=on-failure`.

### 4.3 macOS — launchd
- Default (user/agent scope): plist at
  `~/Library/LaunchAgents/com.aipotluck.service.plist`, loaded via
  `launchctl load -w`. Starts at login.
- `--system` flag: `LaunchDaemons` in `/Library/LaunchDaemons/`, needs sudo,
  starts at boot without login — the daemon-scope equivalent of a system
  service.
- `ProgramArguments` invokes the same python entrypoint; `KeepAlive` +
  `StandardOutPath`/`StandardErrorPath` for logs.

### 4.4 Windows — real Windows Service via `pywin32`, with a no-admin fallback
- True Windows Services need admin + a service wrapper. Use `pywin32`
  (`win32serviceutil`) — it's the standard, well-trodden path, pure Python,
  no extra native installer needed beyond the pip package.
- `pip install pywin32` as a dependency of the service component only
  (Linux/macOS installs don't need it).
- Default (no admin available / user declines UAC): fall back to a
  **Scheduled Task** (`schtasks /create /sc onlogon ...`) running the same
  script — no admin required, starts at logon, good enough for the "blank"
  phase.
- `--system` flag (requires elevation): registers as an actual Windows
  Service (`python aipotluck_service.py install` via pywin32's
  `win32serviceutil.HandleCommandLine`, then `sc config ... start= auto`) —
  runs at boot, no login required. This is the true system-service parity
  with systemd `--system` / launchd `LaunchDaemons`.

### 4.5 The "blank" service payload itself
`service/aipotluck_service.py`: minimal, dependency-free (stdlib only) for
now:
- A tiny loop / `http.server` health endpoint (`GET /healthz` → `200 ok`) so
  we have something externally observable and testable across all three
  service backends without needing the transport layer yet.
- Reads `runtime.json` written by the installer so it already knows where
  llama-server lives, in preparation for later orchestration duties, but
  does not start/stop llama-server yet in this phase — that wiring comes
  with the transport layer.
- Structured logging to the per-OS log dir from section 3.3.

## 5. Open questions before implementation

1. Default install scope: **per-user (no admin), autostart at login** vs
   requiring `--system` explicitly for boot-time/no-login start? (Recommend
   per-user default, `--system` opt-in — matches "reduce friction.")
2. Should the installer also launch `llama-server` directly as a foreground
   process the first time (quick "does it work" smoke test), separate from
   the persistent Python service?
3. GPU backend default: auto-detect and pick best (cuda/rocm/vulkan/cpu), or
   always default to plain CPU asset and require an explicit `--backend`
   flag for GPU builds? (Recommend auto-detect with CPU as safe fallback.)
4. Language/tooling for the installer itself: plain stdlib Python (most
   portable, matches "blank python service" ask) vs. a compiled Go/Rust
   binary wrapper for a nicer double-click UX later? (Recommend stdlib
   Python now; packaging/ dir above is where a future native wrapper would
   slot in without touching this logic.)
5. Default model: none bundled — first run requires user to point at a GGUF
   or we offer an optional `-hf <repo>:<quant>` passthrough to
   `llama-server`'s own Hugging Face fetcher (it already supports this
   natively, so we don't need to reimplement model discovery).

## 6. Non-goals for this phase

- No custom transport layer yet (explicitly deferred per instructions).
- No GUI installer wizard yet (packaging/ is a placeholder).
- No auto-update mechanism yet.
- No support for building llama.cpp from source by default (submodule is
  reference/fallback only, not the default install path).
