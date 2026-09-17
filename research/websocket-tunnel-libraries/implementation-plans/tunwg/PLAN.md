# tunwg Implementation Plan

**Source verified against:** `git clone https://github.com/ntnj/tunwg` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/tunwg` (main branch, commit as of clone time).
File paths below are relative to that repo root.

Docs used: `README.md` (the entire project's documentation lives in one file — no `docs/` dir).
Source used (entire repo, it is small): `listener.go` (public `NewListener` API),
`tunwg/tunwg.go` (client CLI binary, `main()`), `tunwg/tunwgs.go` (server binary, `tunwgServer()`),
`internal/flags.go` (all env-var config), `internal/wireguard.go` (netstack/WireGuard device
setup), `internal/ip.go` (IP↔SNI subdomain encoding), `internal/api.go` (the `/add` peer-exchange
JSON schema), `internal/tls.go`, `internal/relay.go`, `internal/util.go`, `Dockerfile`.

**Confirmed vs. the task brief:** yes on every point. tunwg is WireGuard-userspace-based via
**gVisor netstack** over **plain UDP** — confirmed literally: `internal/wireguard.go` calls
`netstack.CreateNetTUN(...)` (the gVisor `wireguard-go` netstack package,
`golang.zx2c4.com/wireguard/tun/netstack`) and there is **no TUN device anywhere in the repo** —
`grep -r "tun.CreateTUN\|/dev/net/tun"` returns nothing; the whole WireGuard stack runs in-process
as a userspace network stack bound to a plain `net.ListenUDP`-style socket
(`conn.NewDefaultBind()`). It has a Go-embeddable `NewListener(name string) (net.Listener, error)`
API (`listener.go` line 107) usable directly from any Go program (`README.md` "Use directly in Go
programs": `listener, err := tunwg.NewListener("<name>"); http.Serve(listener, httpHandler)`). The
server side is configured **entirely through environment variables** — `TUNWG_RUN_SERVER`,
`TUNWG_API`, `TUNWG_IP`, `TUNWG_PORT`, `TUNWG_AUTH`, `TUNWG_SSL_EMAIL` (`internal/flags.go`,
`tunwg/tunwgs.go::tunwgServer()`) — there is no config-file format at all.

---

## Installation & Global VPS Configuration

tunwg is a single small Go module producing **one binary** that behaves as either client or
server depending on `TUNWG_RUN_SERVER` (`tunwg/tunwg.go::main()`, lines 45-60: `if
os.Getenv("TUNWG_RUN_SERVER") == "true" { tunwgServer(); return }`).

### Option 1 — `go install` (source build)

```bash
go install github.com/ntnj/tunwg/tunwg@latest
```
(README.md "Self hosting"). Requires Go toolchain on the VPS; produces `$GOPATH/bin/tunwg`.

### Option 2 — pre-built binaries (client only — no server releases mentioned)

README.md "Install" links Linux/Windows/macOS binaries from GitHub Releases, but these are
documented specifically for the **client** use case; the self-hosting section only shows `go
install` or Docker for the server, so for a VPS server deployment prefer Docker or `go install`.

### Option 3 — Docker (recommended for VPS)

```yaml
# docker-compose.yml
tunwgs:
  image: ghcr.io/ntnj/tunwg
  network_mode: host   # or explicit ports: 80, 443, 443/udp
  environment:
    TUNWG_RUN_SERVER: "true"
    TUNWG_PORT: "443"        # UDP port used for WireGuard connections
    TUNWG_IP: "a.b.c.d"      # public IP of this VPS
    TUNWG_API: "example.com" # all subdomains (*.example.com) resolve here
```
(quoted verbatim from README.md "Self hosting" → docker-compose example). Confirmed in
`tunwg/tunwg.go::main()` lines 50-57 that the image's entrypoint also special-cases
`tunwgs`/`/bin/tunwgs` as argv[0] to "reproduce the older docker environment" and defaults
`TUNWG_KEY=tunwgs` in that case — i.e. the published `ghcr.io/ntnj/tunwg` image can be invoked
either as `tunwg` (client) or `tunwgs` (server) depending on the command args, controlled by the
compose `command:`/`image` entrypoint convention, on top of the `TUNWG_RUN_SERVER` env-var switch.

**There is no config file anywhere in this project — 100% environment-variable driven**
(`internal/flags.go`, 8 total env vars, all read via plain `os.Getenv`):

| Env var | Read by | Purpose |
|---|---|---|
| `TUNWG_RUN_SERVER` | `tunwg.go` | `"true"` → run as server instead of client |
| `TUNWG_PORT` | `GetListenPort()` | UDP port for WireGuard traffic (server: fixed by convention 443; client: 0 = OS-assigned ephemeral) |
| `TUNWG_IP` | `ServerIp()` | Server's public IP, embedded in the `/add` handshake response as `endpoint` |
| `TUNWG_API` | `ApiDomain()` | Base domain for the tunnel URLs and the `/add`, `/relay` HTTPS API; default `l.tunwg.com` (the public instance) |
| `TUNWG_AUTH` | `AuthKey()` | Optional shared secret; if set, both server and every client must set the same value, else `/add` returns `403` |
| `TUNWG_KEY` | `getKeyName()` | Filename for the persisted WireGuard private key under `$TUNWG_PATH/keys/`; defaults to the binary's own name |
| `TUNWG_PATH` | `Keystorage()` | Override for the key-storage directory; default `os.UserConfigDir()/tunwg`, or `/data` in the Docker image |
| `TUNWG_SSL_EMAIL` | `SSLCertificateEmail()` | Contact email for the auto-issued Let's Encrypt cert; default `certs@tunwg.com` |
| `TUNWG_RELAY` | `UseRelay()` (client-side) | If set (any non-empty value), tunnel WireGuard-over-UDP inside a TCP/HTTPS "Upgrade: udp-relay" connection for firewalls that block UDP |
| `TUNWG_TEST_LOCALHOST` | dev-only | Redirects lookups to `127.0.0.1` for local testing |

### Ports (README.md "Self hosting", confirmed by `tunwgs.go::tunwgServer()`)

- **`:443/udp`** — WireGuard handshake/data traffic (`TUNWG_PORT`; the *public* `l.tunwg.com`
  instance deliberately uses UDP/443 "since it's less likely to be blocked by firewalls" — the
  brief's premise of "plain UDP" is thus a deliberate design choice, not an incidental detail).
- **`:443/tcp`** — HTTPS edge: `l443 := &tcpproxy.TargetListener{Address: "https"}`, served via
  `http.Serve(tls.NewListener(l443, internal.GetTLSConfig()), apiMux())` — this single port serves
  **both** the `/add`+`/relay` management API **and** all proxied tunnel traffic, demultiplexed by
  TLS SNI (`runSniProxy`, see Routing section) — `proxy.AddSNIRoute(":443", internal.ApiDomain(),
  l443)` routes SNI == the bare `TUNWG_API` domain to the API mux, and
  `proxy.AddSNIRouteFunc(":443", ...)` routes every *other* SNI (an encoded subdomain) to the
  matching client via WireGuard.
- **`:80/tcp`** — plain HTTP: redirects to HTTPS, and proxies ACME HTTP-01 challenges through the
  WireGuard tunnel to whichever client is completing the challenge (`sslRedirect()` in
  `tunwgs.go`).

### One-time global config
- **TLS**: fully automatic per-hostname Let's Encrypt (with ZeroSSL fallback on LE rate limits) —
  README.md "Automatic SSL certificates" — **the certificate is issued and terminated on the
  *client*, not the server** (README.md "Privacy": "The local instance running on your machine is
  responsible for generating an HTTPS certificate for you... your traffic is completely private to
  you"); the server is a pure TCP/SNI relay and never sees decrypted traffic. This is a materially
  different trust model than rustunnel/sish (both TLS-terminate at the server). Only
  `TUNWG_SSL_EMAIL` is a server-relevant TLS knob (used for its own `/add`/`/relay` API cert, per
  `internal/tls.go`, not read in full this pass — see Open Questions).
- **"Admin account"**: none — the closest equivalent is `TUNWG_AUTH`, a single shared secret string
  checked via plain `!=` comparison in `tunwg/tunwgs.go` line 94
  (`if authKey, reqKey := internal.AuthKey(), r.Header.Get("X-Authorization"); authKey != "" &&
  authKey != reqKey { w.WriteHeader(http.StatusForbidden) }`) — **not constant-time**, and it's a
  single global secret shared by every client, not per-client. No user/token database of any kind.
- **DB init**: **none — the server is explicitly "fully stateless and doesn't require any storage"**
  (README.md "Self hosting"). It optionally caches recent WireGuard peers + their last-known UDP
  endpoint to a flat JSON file (`persistPeers` in `tunwgs.go`, written every minute if dirty, to
  `$TUNWG_PATH/server/peers.json`) purely as an optimization "to enable instant reconnection of
  tunnels after server restart" — this is a cache, not a source of truth; losing it just means
  clients re-handshake via `/add` on their own 30s `backgroundMonitor()` retry loop
  (`listener.go` lines 143-161).

---

## Cloud-Side Control-Plane API (SvelteKit ↔ tunwg server)

**This is tunwg's biggest departure from rustunnel/sish: there is effectively no admin/control
REST API for SvelteKit to call at all.** The server exposes exactly two HTTP(S) endpoints, both
on the same `:443` listener multiplexed by SNI==`TUNWG_API` (`apiMux()` in `tunwg/tunwgs.go`):

- **`POST https://<TUNWG_API>/add`** — this is the WireGuard peer-registration handshake, called
  by *clients* (or embedding Go programs via `NewListener`), not by our backend. Request body
  (`internal.AddPeerReq`, `internal/api.go`): `{"Key": <32-byte client WireGuard pubkey, base64
  via Go's default JSON []byte encoding>}`. Optional `X-Authorization: <TUNWG_AUTH>` header if the
  server has a shared secret configured. Response (`internal.AddPeerResp`): `{"Key": <server's own
  WireGuard pubkey>, "Endpoint": "<TUNWG_IP>:<TUNWG_PORT>"}`. Server-side handler
  (`tunwgs.go::apiMux()` lines 93-131) just calls `allowUserKey(clientKey, "")` — i.e.
  **`wg set` a new allowed peer** — and returns its own key/endpoint so the client can complete
  the handshake. **This has no concept of "which tenant/user owns this device" — any caller who
  knows (or omits, if unset) `TUNWG_AUTH` can register a peer.**
- **`GET https://<TUNWG_API>/relay`** (`Upgrade: udp-relay`) — only used when `TUNWG_RELAY=true`
  on the client side, to tunnel the WireGuard UDP traffic inside a TLS/TCP connection for
  UDP-blocked networks. Not relevant to our control plane.

**There is no `/api/tunnels`, `/api/status`, `/api/tokens`, no tunnel history, no dashboard, no
OpenAPI spec, nothing to "list connected clients" over HTTP.** The entire "control plane" is: (a)
the one shared `TUNWG_AUTH` secret gating `/add`, and (b) whatever tunwg itself derives internally
from the WireGuard handshake state (peer list, last-handshake timestamps — see Status section,
readable only via the WireGuard device's own IPC interface, not exposed as HTTP).

**Practical consequence for SvelteKit integration:** SvelteKit cannot "create/register a tunnel or
service" via any tunwg API — a "tunnel" isn't a server-side resource at all, it's purely the
client's locally-chosen `NewListener(name)` call (see Client Linking + Routing sections). SvelteKit
also cannot issue **per-client** tokens — tunwg has exactly one shared secret
(`TUNWG_AUTH`) for the whole server, not a per-token/per-user system. If we need per-device
revocation, access control, or usage visibility, **100% of that must be built by us**, most likely
as:
1. A thin sidecar/shim service we write and run alongside `tunwgs` on the VPS, with access to the
   WireGuard device's IPC state (or the `peers.json` cache file) to expose a real "list connected
   peers" HTTP endpoint for SvelteKit to poll.
2. Our own per-device identity/authorization layer *external* to tunwg — e.g. running tunwg with
   `TUNWG_AUTH` set to a single platform-wide secret (shared by all our legitimate clients, not
   secret from an attacker's perspective once one client leaks it) and relying on a
   different-in-kind mechanism (mutual TLS, a wrapper API, or simply accepting that tunwg
   provides no meaningful multi-tenant access control) for anything beyond "is this basically our
   fleet."

---

## Client Linking Flow (access token exchange)

There is **no token/secret "exchange" in the pairing sense at all** — enrollment is fully
automatic and identity is **derived from a locally-generated WireGuard keypair**, not issued by
the server:

1. First run of the client (`tunwg -p 8080` or `tunwg --forward=http://localhost:8080`, or the
   embedded `tunwg.NewListener(name)` call) triggers `internal.Initialize()` →
   `generateKey(getKeyName())` (`internal/wireguard.go` lines 33-51): if no key file exists yet at
   `$TUNWG_PATH/keys/<TUNWG_KEY-or-binary-name>`, generate a fresh WireGuard private key and
   persist it (`0400` perms). **This key, not any server-issued token, is the device's permanent
   identity** — "the generated subdomain is derived from your wireguard key... it'll remain
   constant across process restarts" (README.md "Persistent URLs").
2. `NewListener()` calls `startListenersOnce()` → `addServerPeer()` (`listener.go` lines 53-105):
   POSTs the client's public key to `https://<TUNWG_API>/add`, optionally with
   `X-Authorization: <TUNWG_AUTH>` if that env var is set locally (must match the server's).
   **If `TUNWG_AUTH` is unset on both sides, literally any machine with network access to the
   server can register itself as a peer with zero credentials** — this is the "no configuration or
   database on server" design goal stated explicitly: "One of the primary goals for tunwg was to
   securely allow new clients to join without requiring any configuration or database on server"
   (README.md "Internal Details").
3. Server replies with its own WireGuard pubkey + `IP:port` endpoint; client does
   `internal.WgSetIpc(["replace_peers=true", "public_key=...", "endpoint=...", "allowed_ip=.../128",
   "persistent_keepalive_interval=25"])` to complete the handshake locally. The actual WireGuard
   protocol handshake (Noise-based, mutually authenticated by the exchanged public keys) then
   happens directly over UDP between client and server — **the real cryptographic trust
   establishment is WireGuard's own key exchange, not the plaintext `/add` HTTP call**, so even
   though `/add` itself is unauthenticated (absent `TUNWG_AUTH`), a party without the private key
   corresponding to a registered public key can never actually pass traffic.
4. Reconnect logic: `backgroundMonitor()` (`listener.go` lines 143-161) polls the WireGuard device
   every 30s; if `LastHandshakeTime` is stale (>150s), it calls `addServerPeer()` again
   automatically — no manual re-linking needed after a server restart or network blip.

**For our access-token-style install flow, we must invent the entire "pairing" layer ourselves**,
since tunwg provides none:
- **Option A (weakest, closest to tunwg's native design)**: set a single platform-wide `TUNWG_AUTH`
  value shared by every legitimate aipotluck-local-client install; this only distinguishes "our
  fleet" from "randoms on the internet" — it does **not** let SvelteKit issue/revoke *per-device*
  credentials, since it's one global secret baked into the server's environment at deploy time
  (changing it requires a server restart and pushes to every client).
- **Option B (recommended — build our own layer)**: don't rely on tunwg's `/add` auth at all for
  per-device identity. Instead: (i) SvelteKit issues *our own* opaque one-time pairing token via
  our own API when the user starts linking a device; (ii) the local-client installer exchanges
  that token with our SvelteKit backend over normal HTTPS (`POST /api/link-device
  {pairing_token}`), receiving back a stable `device_id`; (iii) the local-client daemon then runs
  tunwg with `TUNWG_KEY=<device_id>` (so its WireGuard keypair — and therefore its subdomain — is
  deterministic and namespaced per device, persisted at `$TUNWG_PATH/keys/<device_id>`) and the
  shared platform `TUNWG_AUTH`; (iv) SvelteKit records `device_id → wireguard pubkey → subdomain`
  in our own DB (the pubkey and derived subdomain can be computed client-side and reported back to
  our backend at registration time, or we derive it ourselves given the key — see Routing/`ip.go`
  below) purely for our own bookkeeping/UI/revocation-adjacent logic (revocation is not real —
  see next point).
- **Revocation caveat — genuinely does not exist server-side**: there is no `/remove-peer` or
  equivalent API. `allowUserKey()` in `tunwgs.go` only *adds* peers via `wg set`; nothing in the
  codebase ever removes one. Short of SSHing into the VPS and manually running `wg set wg0 peer
  <key> remove` (not even scripted anywhere in the repo) or restarting the server with a modified
  allow-list (there isn't one — it's dynamic, in-memory, rebuilt from `/add` calls and the
  `peers.json` cache), **we cannot cleanly revoke a single device's access to a self-hosted tunwg
  server without writing entirely new server-side code.** This is a material gap versus
  rustunnel's `DELETE /api/tokens/:id` and sish's `disconnectclient` API.

---

## Request Routing (public traffic → local client)

Routing is **subdomain-based**, but unlike rustunnel/sish the subdomain is not a
human/server-chosen string — it's a **deterministic base32 encoding of the client's internal
WireGuard IPv6 address + forwarded port**, computed independently by both client and server with
no registration/lookup table:

- `internal/ip.go::GetIPForKey(pubkey)`: the client's WireGuard IP is
  `sha256(pubkey)[:8]` appended to a fixed `/64`-ish private IPv6 prefix
  (`ipv6Subnet = "\xfd\xb0\x01\xad\x4d\x05\x81\x42"`, an RFC 4193 ULA range) — i.e. **the IP is a
  pure function of the public key**, needing no allocation or database on either side.
- `GetEncodedIPPort(addr)` (`ip.go` lines 28-34): takes that IPv6 address + a port number (the
  *local* TCP port the `Listener` is bound to inside the netstack, itself derived as
  `sha256(name)[:2]` in `NewListener` — i.e. **the "target name" you pass to `NewListener("name")`
  or `tunwg --forward=http://localhost:8080` also deterministically picks the internal listen
  port**, no coordination needed), and base32-encodes the last 8 bytes of the IP + the 2-byte port
  into the subdomain label. The resulting public URL is logged directly by the client:
  `slog.Info("listener started", ..., "url", fmt.Sprintf("https://%v.%v", tl.Addr(), internal.ApiDomain()))`
  → e.g. `https://abc123xyz.l.tunwg.com`.
- Server-side dispatch (`tunwgs.go::getIPForDomain`, lines 184-206, called from
  `runSniProxy`'s `AddSNIRouteFunc`): on every inbound TLS ClientHello, it reads the SNI, strips
  the `.{TUNWG_API}` suffix (or resolves a CNAME first for custom domains — see below), base32
  *decodes* the remaining label back into an `IP:port`, and dials that address **over the internal
  WireGuard netstack** (`DialContext: internal.DialWg`) with PROXY protocol v1 prepended so the
  client can recover the real remote IP. **No lookup table, no per-tunnel registration step, no
  "bind hostname to client" API call of any kind — the subdomain IS a self-describing address.**
- **Custom domains**: README.md "Custom domains" — just add a CNAME from your own domain (e.g.
  `test.example.com`) to the encoded subdomain (`xxxxxxxx.l.tunwg.com`); `getIPForDomain` falls
  back to `net.LookupCNAME` when the SNI doesn't end in `.{TUNWG_API}` directly, then decodes from
  the CNAME target. No server-side registration needed for this either — it's pure DNS.

**Practical implication for our SvelteKit backend**: there is **no "bind hostname to client ID"
API to call**, and no way to make a client's subdomain human-memorable or "fixed" beyond what its
own `NewListener(name)` argument deterministically yields (which is stable across restarts *for
that name*, exactly per README.md "Persistent URLs" — same wireguard key + same forwarded
`name`/address always yields the same subdomain). For `llama-server`, we'd call
`tunwg.NewListener("llama-server")` (or run the CLI as `tunwg -p <llama_port>`, which sets
`TUNWG_KEY=p<port>` internally — see `tunwg/tunwg.go` line 73-75 — meaning **the WireGuard key
filename itself changes if the port changes**, a subtlety to watch for if llama-server's port is
not fixed) once per device, record the resulting public URL (parsed from stdout/logs, since there
is no JSON/machine-readable output mode — unlike rustunnel's `--json`), and hand that URL to
SvelteKit as-is; it's stable indefinitely as long as the WireGuard key file and the forwarded
name/port stay the same.

---

## Status Polling / Health Checks

**There is no HTTP status/health API of any kind on the tunwg server.** Confirmed by exhaustive
review — `apiMux()` only registers `/add` and `/relay`; there is no `/status`, `/health`,
`/metrics`, `/peers`, or anything else. Any "is client X connected" signal must come from one of:

1. **Directly querying the WireGuard device's own IPC state** — `internal.GetWgDeviceInfo()`
   (`internal/wireguard.go` lines 116-181) parses the raw `wgDevice.IpcGet()` output into a
   `wgtypes.Device{Peers: [...]}` list, each peer exposing `PublicKey`, `LastHandshakeTime`,
   `ReceiveBytes`/`TransmitBytes`, `Endpoint`. This is used internally for
   `BackgroundLogger()` (debug logging every N seconds) and `persistPeers.writeToDisk()` (the
   `peers.json` cache, which only persists peers handshaked within the last 15 minutes) — **but
   none of this is exposed over HTTP**. To get this data into SvelteKit we would have to write and
   deploy a small sidecar that either (a) links against `internal.GetWgDeviceInfo()` directly (this
   package is `internal/`, i.e. **not importable from outside the `tunwg` module** per Go's
   internal-package visibility rules — we would need to fork/vendor this logic, or shell out to the
   standard `wg show` CLI against the same WireGuard interface if one is exposed, which it likely
   is not since this is a netstack-only in-process device with no OS-visible `wgN` interface), or
   (b) read the on-disk `peers.json` cache file (stale by design — up to a 15-minute freshness
   window and only written every 1 minute when dirty, per `backgroundWriter(time.Minute)`), or (c)
   patch the tunwg server source ourselves to add a real `/status` HTTP endpoint (the cleanest fix
   but means maintaining a fork).
2. **Passive/indirect end-to-end check**: since routing needs no server-side registration, the only
   "for sure it's up" signal externally available to SvelteKit is **actually hitting the device's
   public tunnel URL** (`https://<encoded-subdomain>.<domain>/health` or similar) and checking for
   a 2xx from llama-server itself. This exercises the full path (WireGuard handshake alive + local
   service alive) but tells us nothing about *why* it's down if it fails (DNS/CNAME issue vs. dead
   WireGuard peer vs. dead llama-server).
3. `tunwg`'s own logs (`slog`, JSON via `--json` flag on the CLI, or `TUNWG_KEY`-scoped) show
   handshake/reconnect activity (`backgroundMonitor()` in `listener.go` logs `"lost connection to
   server"` on `addServerPeer()` failure) — useful for local-machine-side debugging via the
   monitor service's own log tailing, not something SvelteKit can poll remotely without us shipping
   those logs somewhere (e.g. our own log-forwarding from the local-client daemon back to
   SvelteKit, which is exactly the kind of glue our monitor service would need to build regardless
   of tunneling tool).

**Bottom line: tunwg gives us zero server-side visibility into per-client connection status.** The
only genuinely reliable signal without forking the server is an end-to-end HTTPS health probe of
each device's own tunnel URL, run periodically from SvelteKit — same fallback strategy as the
other tools' "recommended combination," except here it is the *only* option, not a supplement.

---

## Open Questions / Assumptions

- **No per-client revocation exists.** This is the single biggest gap versus rustunnel/sish for our
  use case (SvelteKit "unlink this device" action has no tunwg-side effect without forking the
  server to add a peer-removal code path, or physically running `wg set` by hand). Needs an
  explicit decision: fork+patch tunwg to add `/remove` mirroring `/add`, or accept that
  "unlinking" only ever revokes trust on our own backend (the local daemon simply stops trying to
  serve if we tell it to stop, but a malicious/compromised device that already has the WireGuard
  key could keep reconnecting server-side indefinitely).
- **No per-client status API** (see above) — confirmed absent, not merely undocumented. Building
  visibility requires either forking the server or relying solely on our own external HTTP health
  probes against each device's tunnel URL.
- **`TUNWG_AUTH` is a single global secret, not workable as a genuine multi-tenant credential** —
  we did not find any way to scope it per-client; if compromised, every legitimate client and
  every attacker share the exact same bar to entry (whoever knows the string). Not verified whether
  there's a way to layer per-client auth at the HTTP-forwarding layer (`--limit` basic-auth flag
  exists, but that's for HTTP requests reaching the *local* forwarded service, i.e. protects
  llama-server's HTTP endpoint from the public internet — not client-to-server enrollment).
- **`internal/tls.go` and `internal/relay.go` were not read in full** during this pass — worth a
  follow-up read to confirm exactly how the client obtains/renews its own Let's Encrypt cert (ACME
  challenge type, whether it needs port 80 reachable on the *client* machine or routes the
  challenge through the tunnel per `tunwgs.go::sslRedirect`'s `/.well-known/acme-challenge/`
  proxy-through-WireGuard logic, which strongly suggests the ACME HTTP-01 challenge is proxied
  through the tunnel to the client rather than requiring the client to expose port 80 itself — this
  should be confirmed before relying on it for aipotluck-local-client's NAT'd deployment
  environment).
- **`internal` Go package visibility**: `tunwg.NewListener()` is the only public API surface; almost
  everything else (`internal/*.go`) is unexported from outside the `github.com/ntnj/tunwg` module
  per Go tooling rules, meaning any deeper integration (e.g. embedding the health-check/status
  logic in our own Go monitor service) would require either forking the repo or shelling out to
  the `tunwg`/`tunwgs` binary and scraping stdout/log lines — not a first-class SDK experience
  compared to rustunnel's structured `--json` NDJSON output.
- **Relay-over-HTTPS (`TUNWG_RELAY=true`) fallback** for UDP-blocked networks was read
  (`establishRelay()` in `listener.go`) but not deeply verified for production reliability/latency
  characteristics — README.md itself warns "performance will suffer in case of packet drops," worth
  load-testing if our target deployment environment (aipotluck-local-client's typical
  home/NAT/corporate-network install base) is likely to block outbound UDP/443 often enough that
  this fallback path becomes load-bearing rather than an edge case.

---

## Summary of custom glue code required

Substantial, and different in kind from rustunnel: tunwg supplies the actual tunneling mechanism
(WireGuard-over-UDP via gVisor netstack, automatic per-client TLS termination, deterministic
subdomain routing with zero registration) essentially for free and with genuinely minimal server
ops (no DB, no config file, single env-var-configured binary) — but it supplies **no multi-tenant
control plane whatsoever**: no per-client tokens, no revocation, no status API, no dashboard. Every
piece of "SvelteKit manages which clients are linked, sees who's connected, and can kick one off"
must be built by us, either as (a) an external wrapper that treats the shared `TUNWG_AUTH` as a
coarse fleet-wide gate and relies purely on end-to-end HTTPS health probing for status, or (b) a
maintained fork of tunwg's server that adds real per-peer enrollment/removal/status HTTP endpoints
— a meaningfully larger and more fragile commitment than adopting rustunnel's already-built
platform API.
