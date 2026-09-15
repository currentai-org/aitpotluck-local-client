# aipotluck-local-client

Cross-platform installer and local inference server wrapper. Wraps
[llama.cpp](https://github.com/ggml-org/llama.cpp) (pinned as a git
submodule at `vendor/llama.cpp` for reference/docs; the installer itself
downloads prebuilt release binaries rather than building from source) and
installs a small companion Python system service.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design rationale.

## Status

- Linux: implemented and tested (systemd --user service backend, live
  llama-server supervision with crash-restart, verified end-to-end with a
  real model download and inference request).
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
- Two-tier self-healing: systemd/launchd/Windows Service restarts the
  Python service process itself if it dies; the Python service's
  `LlamaSupervisor` restarts just `llama-server` if it crashes. Between the
  two, llama-server should stay running continuously.

`llama-server`'s own OpenAI-compatible API is reachable directly at
`http://127.0.0.1:8080` (or whatever `--server-host`/`--server-port` you set).

## Repo layout

```
vendor/llama.cpp/        git submodule, pinned commit (reference + docs)
llama_version.json        pinned release tag + per-platform asset checksums
installer/                installer package (detection, fetch, layout, service backends)
service/aipotluck_service.py   the blank companion Python service
packaging/                 placeholders for future native installers (.deb/.pkg/.msi)
ARCHITECTURE.md            full design doc
```
