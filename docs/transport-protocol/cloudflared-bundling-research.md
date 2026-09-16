# Prior Art: Bundling `cloudflared` Without a System Service

Research question: is there established precedent for shipping the `cloudflared`
binary alongside an installed application and driving it as a plain subprocess
(spawn/monitor/parse stdout) — as opposed to registering it as a systemd
unit / launchd daemon / Windows service?

**Short answer: yes, this is a well-established, common pattern** — both
Cloudflare's own tooling and multiple third-party wrappers do exactly this, in
several languages. No one bundling `cloudflared` for a "quick tunnel" use case
installs it as an OS service; the service-registration path is reserved for
persistent, DNS-backed named tunnels operated by IT/ops teams.

## 1. Two very different `cloudflared` deployment models exist

`cloudflared` itself supports two entirely different tunnel modes, and this
matters a lot for "should I install a service":

- **Named tunnels** (`cloudflared tunnel create` + a DNS record + a persistent
  `config.yml`/credentials file) — the model Cloudflare's own docs assume when
  they show `cloudflared service install`. This is the only mode where
  `cloudflared` ships an actual OS-service installer subcommand
  (`cloudflared service install|uninstall`, Windows service management
  built into the CLI).
- **Quick Tunnels** (`cloudflared tunnel --url http://localhost:PORT`, aka
  TryCloudflare) — no account, no DNS record, no config file. `cloudflared`
  connects out, gets handed a random `*.trycloudflare.com` hostname, prints it
  to stdout, and proxies traffic for as long as the process runs. This is
  explicitly meant to be driven as a foreground/background **child process**,
  not a service — Cloudflare's own docs describe it as "launch a process." [Quick Tunnels docs]

Every piece of prior art found below assumes the **Quick Tunnel model**, i.e.
run cloudflared as a subprocess for as long as your app needs a tunnel, never
touching the OS service layer. Quick Tunnels do have two real constraints worth
noting: a 200 concurrent in-flight request cap, and **no Server-Sent Events (SSE)
support** — directly relevant to us, since llama-server's streaming responses
are SSE (see SPEC.md). This pushes us toward the **named tunnel** mode for
production, but the subprocess-driving pattern is identical either way — only the
CLI invocation and whether a one-time `cloudflared tunnel login` happened differ.

## 2. Cloudflare's own JS tooling does exactly this

- **`cloudflare/binary-install`** (official Cloudflare repo) is a generic
  "install a `.tar.gz` binary via npm" library. Its entire design is
  `.install()` in your package's `postinstall`, then `.run()` execs the binary
  directly — no service registration anywhere in the library. It's the same
  mechanism Cloudflare uses to ship `wrangler` itself.
- **`cloudflared` npm package** (`JacobLinCool/node-cloudflared`, 155+ published
  dependents) is the most directly relevant prior art. It:
  - Downloads the platform-correct `cloudflared` binary on first use (mirrors
    exactly what our `installer/fetch.py` already does for llama.cpp).
  - Exposes a `Tunnel` class (an `EventEmitter`) — `Tunnel.quick()` spawns
    `cloudflared tunnel --hello-world`-style quick tunnels, emits `url` and
    `connected` events parsed straight from the subprocess's stdout, and a
    plain `.stop()` kills it. No service, no daemon registration.
  - Separately exposes a `service` namespace purely to *detect* whether the
    user already has a real named-tunnel Windows/systemd service installed
    elsewhere — it explicitly does not create one itself.

## 3. Python-ecosystem equivalents (closest match to our own installer)

- **`pycloudflared`** (PyPI, `Bing-su/pycloudflared`) is essentially a Python
  port of the same idea: "Cloudflare binaries will be downloaded the first time
  you run it," then a single call `try_cloudflare(port=7860)` spawns
  `cloudflared` as a subprocess and returns the assigned URL. Popular in the
  ML-notebook/Gradio ecosystem specifically because it lets a Colab/Jupyter
  session expose itself publicly without any admin rights or service manager —
  the same unprivileged-user constraint we already designed around for the
  llama.cpp installer itself.
- **`s4rrar/cloudflary`** is a more elaborate but still service-free wrapper:
  `subprocess.Popen(["cloudflared", "tunnel", "--url", url])`, a background
  thread reading merged stdout/stderr, regex-matching
  `https://[A-Za-z0-9.-]+\.trycloudflare\.com` out of the log lines to detect
  the assigned URL, an auto-restart loop with configurable backoff on crash,
  and a health-check thread that polls the URL and forces a restart if it goes
  unreachable. This is structurally almost identical to our own
  `service/llama_supervisor.py` (spawn → poll health → exponential-backoff
  restart → clean SIGTERM/SIGKILL shutdown) — strong validation that the
  "one more supervised child process" pattern we already use for llama-server
  extends cleanly to cloudflared with no new architecture needed.

## 4. Practical implementation detail confirmed by real-world use

- The community Cloudflare forum thread on automated `cloudflared` downloads
  confirms the exact asset-fetch pattern we'd reuse: GitHub Releases at
  `github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-<os>-<arch>.tgz`
  (or a bare binary on some platforms), no separate installer package required
  — this is architecturally identical to what `installer/fetch.py` already
  does for llama.cpp binaries, just a different GitHub repo/asset naming
  scheme. One caution raised in that thread: verify checksums yourself since
  Cloudflare doesn't consistently publish signed hashes for every asset (same
  situation we already hit and solved for llama.cpp in `llama_version.json`).
- No prior-art project reviewed uses `cloudflared service install` for a
  bundled/embedded use case — that subcommand only appears in Cloudflare's own
  docs for administrator-driven named-tunnel deployments, never in any of the
  "bundle it inside my app" wrappers surveyed.

## 5. Recommendation for aipotluck-local-client

This validates a straightforward extension of the existing installer
architecture rather than a new subsystem:

1. **Fetch**: extend `installer/fetch.py`'s existing GitHub-release-asset
   pattern to also resolve/download/checksum-verify `cloudflared` binaries per
   OS/arch, cached the same way llama.cpp releases already are.
2. **Run**: add a `CloudflaredSupervisor` sibling to `LlamaSupervisor` — same
   spawn/health-check/backoff-restart/clean-shutdown shape, no OS service
   registration required. It becomes a second child process the
   `aipotluck_service.py` runner manages, not a new layer of installer
   complexity.
3. **Mode**: use named tunnels (not Quick Tunnels) for the real product, purely
   because Quick Tunnels don't support SSE and llama-server's chat streaming
   endpoint is SSE — but the subprocess-driving code is identical either way,
   so this is a config-time decision (`--url` vs. a persistent tunnel token),
   not an architecture change.
4. No changes needed to APLC-1 (SPEC.md / PROPOSAL.md) — cloudflared would sit
   *underneath* our WebSocket transport as one possible NAT-traversal carrier
   (already scored 5/5 in `research/nat-traversal/nat-traversal-report.md`),
   not replace it.

## Sources

1. Cloudflare — Quick Tunnels docs: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
2. `cloudflare/binary-install` (official Cloudflare repo): https://github.com/cloudflare/binary-install
3. `cloudflared` npm package (JacobLinCool/node-cloudflared): https://www.npmjs.com/package/cloudflared
4. `pycloudflared` (Bing-su, PyPI): https://github.com/Bing-su/pycloudflared / https://pypi.org/project/pycloudflared/
5. `s4rrar/cloudflary` — resilient Python subprocess wrapper: https://github.com/s4rrar/cloudflary
6. Cloudflare Community — "Cloudflared automated download" (manual GitHub Release asset-fetch pattern, checksum caveat): https://community.cloudflare.com/t/cloudflared-automated-download/418848
