# aipotluck-local-client

Cross-platform installer and local inference server wrapper. Wraps
[llama.cpp](https://github.com/ggml-org/llama.cpp) (pinned as a git
submodule at `vendor/llama.cpp` for reference/docs; the installer itself
downloads prebuilt release binaries rather than building from source) and
installs a small companion Python system service.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design rationale.

## Status

- Linux: implemented and tested (systemd --user service backend).
- macOS: stubbed, implemented per Apple docs, **untested** (no macOS host
  available in this environment). See `installer/service/launchd.py`.
- Windows: stubbed, implemented per documented schtasks/pywin32 conventions,
  **untested** (no Windows host available). See
  `installer/service/windows_service.py`.

## Quick start (Linux)

```bash
git clone --recurse-submodules <this-repo>
cd aipotluck-local-client
python3 -m installer.install --backend auto
```

Options:

```
--tag TAG            Override the pinned llama.cpp release tag
--backend {auto,cpu,cuda,vulkan,rocm}
--install-dir PATH   Override the default per-OS install root
--system              Install machine-wide (requires elevation)
--no-start            Install the service but don't start it
--no-service          Only fetch/extract llama.cpp, skip the Python service
-v / --verbose
```

After install, the blank companion service exposes a health check at
`http://127.0.0.1:8765/healthz` and a status endpoint at
`http://127.0.0.1:8765/status` (shows the resolved llama-server path). The
`llama-server` binary itself is not started automatically yet -- run it
directly from the path printed in the install summary / `runtime.json`.

## Repo layout

```
vendor/llama.cpp/        git submodule, pinned commit (reference + docs)
llama_version.json        pinned release tag + per-platform asset checksums
installer/                installer package (detection, fetch, layout, service backends)
service/aipotluck_service.py   the blank companion Python service
packaging/                 placeholders for future native installers (.deb/.pkg/.msi)
ARCHITECTURE.md            full design doc
```
