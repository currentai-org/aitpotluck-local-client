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

## 6. Quick Tunnel vs Named Tunnel: wrapper support and API interaction, in detail

Direct answers to the two follow-up questions.

### Q1: Do we lose wrapper support with a named tunnel?

**No.** The subprocess-wrapping pattern (spawn, capture stdout, health-check,
supervise/restart) is *identical* for both modes — none of the prior art in
§1-5 changes shape based on tunnel type. What changes is only the **command
line invoked** and **what's needed before you can invoke it**:

| | Quick Tunnel | Named Tunnel (remotely-managed) |
|---|---|---|
| Command | `cloudflared tunnel --url http://localhost:8080` | `cloudflared tunnel run --token <TOKEN>` |
| Config file needed? | No | No (token-only mode — see below) |
| Account needed? | No | Yes |
| Output to parse | Random `https://xxxx.trycloudflare.com` URL on stdout | Nothing to parse — hostname is fixed/known ahead of time |
| Process lifecycle management | Same: `subprocess.Popen`, monitor exit code, restart on crash | Identical |
| Supervisor code | `CloudflaredSupervisor` (as already proposed) | Same class, different argv |

Critically: Cloudflare added a **third, token-based invocation** specifically to
avoid forcing a config file (`config.yml` + `credentials-file` JSON) onto every
deployment. `cloudflared tunnel run --token <TUNNEL_TOKEN>` is a single opaque
JWT-like string that fully authenticates and configures the tunnel — no
YAML file, no `cloudflared tunnel login` browser flow, no local credentials
JSON on disk at all. This is exactly the "remotely-managed tunnel" mode and is
what `cloudflared service install <TOKEN>` uses under the hood for the
service-based deployments — but nothing stops us from feeding that same token
to a plain subprocess invocation instead of the service installer. This is the
mode that keeps our design closest to the existing wrapper pattern: one token,
one `Popen` call, no additional local state.

So: **we don't lose anything from the wrapper's perspective.** The
`CloudflaredSupervisor` class doesn't need to know or care whether it's running
`--url` or `run --token ...` — it's the same spawn/monitor/restart loop either
way. The only real complexity shift is *upstream* of the subprocess call: who
creates the tunnel and gets the token in the first place.

### Q2: Will it require additional interaction with the Cloudflare API?

**Yes — but it's a one-time, cloud-side operation, not something the local
client (or its wrapper) needs to do repeatedly.** Concretely, to run a named
tunnel you need, once per install:

1. **A Cloudflare API token** with `Cloudflare Tunnel Write` (or the newer
   equivalent `Cloudflare One Connector: cloudflared Write`) permission — this
   is our cloud backend's credential, not the end user's.
2. **One `POST` to create the tunnel**:
   `POST /accounts/{account_id}/cfd_tunnel` with `{"name": "...", "config_src": "cloudflare"}`
   → returns a tunnel `id` and a `token` in the same response body.
3. **One `PUT` to set the ingress rule** (which local port maps to which public
   hostname): `PUT /accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations`.
4. **One `POST` to create the DNS record** pointing at
   `<tunnel_id>.cfargotunnel.com` (needs a zone already onboarded to Cloudflare
   — this is the one hard prerequisite: the *cloud* side needs a Cloudflare
   account + domain, not the *client*).

Steps 2-4 happen entirely on **our cloud/control-plane side**, driven by
whatever backend the aipotluck cloud service already runs — this is a natural
fit for the SvelteKit/Node cloud stack from the original transport research,
not new infrastructure. The local installer never talks to the Cloudflare API
directly; it only ever receives a `TUNNEL_TOKEN` string (handed to it by our
own cloud API, analogous to how APLC-1 already issues a bearer token — see
SPEC.md §4) and passes that straight through to `cloudflared tunnel run
--token`.

So the practical shape becomes:

```
[user installs aipotluck client]
        |
        v
[client calls OUR cloud API: "register this device"]
        |
        v
[our cloud API calls Cloudflare API: create tunnel + ingress + DNS record]
(one-time, done by us, standard REST calls, no cloudflared involved at this step)
        |
        v
[our cloud API returns: TUNNEL_TOKEN + APLC-1 bearer token to the client]
        |
        v
[client's CloudflaredSupervisor spawns: cloudflared tunnel run --token <TUNNEL_TOKEN>]
[client's own aipotluck_service.py opens the APLC-1 WebSocket, authenticated
 with its own bearer token, same as if cloudflared weren't involved]
```

This means:

- **The wrapper itself (`CloudflaredSupervisor`) never touches the Cloudflare
  API.** It only ever receives a token string and an argv list — same
  complexity as the Quick Tunnel path, just no stdout URL-parsing needed since
  the hostname is already known.
- **The Cloudflare API calls are all server-side**, made once per device
  registration by our own cloud backend using Cloudflare's official REST API
  (`cloudflare-typescript`/`cloudflare-python` SDKs both exist and would fit a
  Node/SvelteKit or Python backend equally well) — not a new operational
  burden per se, but it is a new integration our cloud service needs to own
  (an API token with tunnel-write scope, tunnel lifecycle bookkeeping —
  create on registration, presumably delete/rotate on deregistration).
- **Token rotation is a real operational concern** worth flagging: Cloudflare
  recommends rotating tunnel tokens regularly, and after rotation `cloudflared`
  needs to be restarted with the new token. Our `CloudflaredSupervisor` should
  support a "reload token and reconnect" signal (mirroring how
  `LlamaSupervisor` already handles config-driven restarts) rather than
  requiring a full reinstall.

### Net effect on scope

- **No new local dependency or install-time complexity** beyond what's already
  planned (fetch cloudflared binary → supervise as a subprocess).
- **One new integration point on the cloud side**: our backend must call
  Cloudflare's Tunnel API to provision a tunnel + ingress + DNS record per
  registered device, and hand the resulting token down through whatever
  device-registration flow the client already uses to fetch its APLC-1 bearer
  token. This is additive cloud-side work, not local-client work, and reuses
  infrastructure (an outbound HTTPS API call from our own backend) the
  SvelteKit/Node stack already has by definition.
- **Quick Tunnels remain useful for local development/testing only** (no
  account needed, but no SSE support and a 200-connection cap) — not a
  candidate for production given llama-server's SSE streaming requirement
  already noted.

## 8. Local firewall risk: Windows Firewall blocking cloudflared <-> local service

Direct question: what's the risk of Windows Firewall blocking the *local*
loopback communication between `cloudflared` and our own service (llama-server
via the aipotluck wrapper)? Short answer up front: **that specific risk is low
and not what shows up in real-world reports.** The documented, recurring
problems are two different things: (1) antivirus/Defender flagging the
`cloudflared` binary itself, and (2) *outbound* firewall/network blocking
between `cloudflared` and Cloudflare's edge (not local loopback at all). Both
are real and worth designing around; the loopback-specific fear is not well
supported by what's actually reported.

### Why local loopback traffic itself is low-risk

Windows Firewall's default inbound/outbound rules do not filter traffic to
`127.0.0.1`/`::1` for ordinary desktop applications — loopback filtering is a
deliberate opt-in restriction that mostly applies to UWP/AppContainer-sandboxed
apps (`CheckNetIsolation LoopbackExempt`), not to plain Win32 processes like
`cloudflared.exe` and `llama-server.exe` running as normal user processes. Two
ordinary same-machine processes talking over `127.0.0.1:<port>` is exactly the
traffic pattern Windows Firewall is least likely to interfere with. No GitHub
issue or community thread found in this research described "cloudflared can't
reach my local origin because Windows Firewall blocked loopback" as the root
cause of a real bug report.

### What actually gets reported: two different, real problems

**(A) Antivirus / Windows Defender false-positive detections of the binary
itself.** This is the single most common `cloudflared`+Windows complaint found,
and it's recent and recurring:

- `cloudflare/cloudflared#1535` (Sept 2025): Windows Defender flagged
  `cloudflared 2025.9.1` as `Trojan:Win32/Kepavll!rfn` on both consumer and
  corporate Defender installs, reproduced "across different computers."
  Community workaround at the time: temporarily disable real-time protection,
  install the previous version (2025.8.1) via the `.msi`, then re-enable
  protection.
- Cloudflare Community — "Trojan on Tunnel download" (Oct 2025): a separate
  detection, `Trojan:Win32/Vigorf.A`, on the installer itself. A Cloudflare
  staff member (`ncano`) confirmed: *"this is a false positive and we are
  working on signing `cloudflared.exe` binary and its `.msi` installer."*
- Cloudflare Community — "Cloudflared Tunnel Daemon False Positive in Windows
  Defender" (earlier thread, same root cause pattern) and a parallel Reddit
  report about Cloudflare WARP triggering `Trojan.Filecoder.Win32` detections —
  same category of problem across Cloudflare's client tooling generally, not a
  one-off.

The pattern across all of these: Cloudflare's Windows binaries get
intermittently caught by Defender's heuristic/ML detection (not
signature-based), Cloudflare acknowledges it as a false positive each time, and
as of the most recent report (Oct 2025) they were actively working on
**code-signing the binary and installer**, which should reduce — though not
eliminate — future false positives. **This is a real risk for us**: if we
silently drop a `cloudflared.exe` onto a user's machine as part of our
installer, a Defender false-positive quarantine event could delete/block the
binary with no visible error from *our* code — it just silently fails to start
or vanishes from disk between runs.

**(B) Outbound connectivity to Cloudflare's edge being blocked/degraded** —
this is a real firewall interaction, but it's about `cloudflared`'s *outbound*
connection to Cloudflare (QUIC on UDP, falling back to HTTP/2 on TCP 7844),
not the local loopback link to our service:

- `cloudflare/cloudflared#1534` (Sept 2025, still open): when a network
  disruption blocks QUIC (UDP), `cloudflared` correctly falls back to HTTP/2 —
  but in an environment where **only QUIC is allowed through the firewall**
  (a real reported corporate configuration) and HTTP/2 is blocked, it never
  retries QUIC again once it has fallen back, leaving the tunnel stuck retrying
  a protocol the firewall will never allow. Real log evidence in the issue
  shows `dial tcp ...:7844: i/o timeout` retried indefinitely after the QUIC
  path recovers.
- `cloudflare/cloudflared#1575` ("NEED HELP - Firewall - Docker - Allow
  cloudflared only connect to Cloudflare and block everything else") —
  a user explicitly trying to firewall-restrict `cloudflared` to only talk to
  Cloudflare's IP ranges, confirming this outbound-allowlisting pattern is
  something real deployments attempt and can get wrong.
- `cloudflare/cloudflared#1362` ("Publish IP addresses/ranges of edges?",
  open) — a standing, unresolved ask from users who need to write outbound
  firewall/allowlist rules for `cloudflared` and don't have a definitive
  published IP range to allowlist against, precisely because Cloudflare's edge
  IPs aren't static/documented for this purpose.

### Net risk assessment for aipotluck-local-client

| Risk | Real-world evidence | Applies to us? |
|---|---|---|
| Windows Firewall blocking `cloudflared` ↔ our local service (loopback) | None found in the wild | Low — not a documented failure mode |
| Antivirus/Defender flagging the bundled `cloudflared.exe` as malware | Recurring, recent, multiple confirmed false positives | **Yes — directly applicable, needs handling** |
| Outbound firewall/corporate-proxy blocking `cloudflared`'s connection to Cloudflare's edge | Real, documented, an open unresolved bug in the fallback logic | Yes, but affects reaching Cloudflare, not our local service pairing |
| Missing published edge IP ranges for allowlisting | Open standing complaint | Relevant if a user's IT department wants to allowlist us explicitly |

### Recommendations to add to the installer design

1. **Expect and handle AV quarantine, don't just assume the binary stays put.**
   `CloudflaredSupervisor` (or the installer's fetch step) should verify the
   binary still exists and still passes a checksum check before each spawn
   attempt, and surface a clear, specific error message ("Windows security
   software may have removed the cloudflared binary — see [known issue]") if
   it's missing post-install, rather than a generic crash/restart loop. This
   mirrors the checksum-verification approach already used for llama.cpp
   binaries in `installer/fetch.py`.
2. **Recommend/document the Microsoft Defender allow-listing step** for users
   who hit this, pointing at Cloudflare's own confirmed-false-positive threads
   as evidence, rather than leaving users to independently discover and
   research the same false-positive reports found here.
3. **Don't rely on `cloudflared`'s automatic QUIC→HTTP2 fallback recovering on
   its own** in restrictive network environments — given the open, unresolved
   #1534 bug. If our own `CloudflaredSupervisor`'s health check detects a
   tunnel stuck retrying (connection never reaches "healthy" within a
   reasonable window), a full process restart resets `cloudflared`'s protocol
   selection state, working around the bug rather than waiting on upstream.
4. **No special-case loopback firewall handling needed** — treat
   `cloudflared` ↔ local llama-server exactly like `aipotluck_service.py` ↔
   `llama-server` today (nothing in the existing installer adds a Windows
   Firewall rule for that pairing, and there's no evidence one is needed).

## Sources

1. Cloudflare — Quick Tunnels docs: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
2. `cloudflare/binary-install` (official Cloudflare repo): https://github.com/cloudflare/binary-install
3. `cloudflared` npm package (JacobLinCool/node-cloudflared): https://www.npmjs.com/package/cloudflared
4. `pycloudflared` (Bing-su, PyPI): https://github.com/Bing-su/pycloudflared / https://pypi.org/project/pycloudflared/
5. `s4rrar/cloudflary` — resilient Python subprocess wrapper: https://github.com/s4rrar/cloudflary
6. Cloudflare Community — "Cloudflared automated download" (manual GitHub Release asset-fetch pattern, checksum caveat): https://community.cloudflare.com/t/cloudflared-automated-download/418848
7. Cloudflare — Tunnel tokens: https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/
8. Cloudflare — Create a tunnel (API): https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/
9. Cloudflare — Create a locally-managed tunnel: https://developers.cloudflare.com/tunnel/advanced/local-management/create-local-tunnel/
10. Cloudflare API reference — Zero Trust Tunnels: https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/
11. `cloudflare/cloudflared#1535` — Windows Defender false-positive (Kepavll!rfn): https://github.com/cloudflare/cloudflared/issues/1535
12. Cloudflare Community — "Trojan on Tunnel download" (Vigorf.A false positive, Cloudflare-confirmed): https://community.cloudflare.com/t/trojan-on-tunnel-download/848473
13. Cloudflare Community — "Cloudflared Tunnel Daemon False Positive in Windows Defender": https://community.cloudflare.com/t/cloudflared-tunnel-daemon-false-positive-in-windows-defender/323388
14. `cloudflare/cloudflared#1534` — QUIC fallback never retried after firewall recovery (open): https://github.com/cloudflare/cloudflared/issues/1534
15. `cloudflare/cloudflared#1575` — firewall-restricting cloudflared to Cloudflare-only egress: https://github.com/cloudflare/cloudflared/issues/1575
16. `cloudflare/cloudflared#1362` — no published edge IP ranges for firewall allowlisting (open): https://github.com/cloudflare/cloudflared/issues/1362
