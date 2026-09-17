# frp Implementation Plan

**Source verified against:** `git clone --depth 1 https://github.com/fatedier/frp` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/frp` (default branch at clone time). File paths
below are relative to that repo root unless a full URL is given.

Key sources used: `README.md` (Table of Contents sections "Server Dashboard", "Client Admin UI",
"Dynamic Proxy Management (Store)", "Authenticating the Client", "Custom Subdomain Names", "Server
Manage Plugins"), `doc/server_plugin.md`, `conf/frps.toml`, `conf/frpc.toml`,
`conf/frps_full_example.toml`, `conf/frpc_full_example.toml`, `server/api_router.go`,
`server/http/controller.go`, `server/http/controller_v2.go`, `client/api_router.go`,
`pkg/util/http/server.go`.

---

## Installation & Global VPS Configuration

frp ships two static Go binaries, `frps` (server) and `frpc` (client), both built from this repo
(`make frps`, `make frpc`, `make build` per `AGENTS.md`). Three install paths, all real:

1. **Prebuilt binary** (recommended for a VPS): download a release tarball from
   `https://github.com/fatedier/frp/releases` (README's own install pointer), extract, and run
   `./frps -c /etc/frp/frps.toml`. No compiler needed.
2. **Build from source**: `make frps` produces a single `frps` binary (Go module, `go.mod` at repo
   root) — needed only if you want a custom build/patch.
3. **Docker**: no official Dockerfile is bundled in this repo, but images are published under
   `fatedier/frps`/`fatedier/frpc` on Docker Hub (referenced throughout community docs); a minimal
   wrapper is:
   ```bash
   docker run -d --name frps --net=host \
     -v /etc/frp/frps.toml:/etc/frp/frps.toml \
     fatedier/frps:latest -c /etc/frp/frps.toml
   ```

### Config file
frp is TOML-configured (legacy INI still parseable, see `conf/legacy/`). Minimal server config,
confirmed at `conf/frps.toml`:
```toml
bindPort = 7000
```
Production config, fields confirmed in `conf/frps_full_example.toml`:
```toml
bindAddr = "0.0.0.0"
bindPort = 7000                 # frpc<->frps control/data channel port

# Dashboard (this doubles as the REST admin API surface, see section 2)
webServer.addr = "0.0.0.0"
webServer.port = 7500
webServer.user = "admin"
webServer.password = "admin"
webServer.tls.certFile = "server.crt"     # optional HTTPS for dashboard/API
webServer.tls.keyFile  = "server.key"

# TLS for the frpc<->frps control channel itself
transport.tls.force = false     # set true to require TLS-encrypted frpc connections
# transport.tls.certFile = "server.crt"

# Shared-secret auth between frpc and frps
auth.token = "12345678"
# or: auth.tokenSource.type = "file"; auth.tokenSource.file.path = "/etc/frp/token"

# Wildcard-subdomain HTTP/HTTPS vhosting (see section 4)
subDomainHost = "frps.com"
```
(`README.md` "Server Dashboard" section and `conf/frps_full_example.toml` lines ~43-111.)

Corresponding minimal client config (`conf/frpc.toml`):
```toml
serverAddr = "127.0.0.1"
serverPort = 7000

[[proxies]]
name = "test-tcp"
type = "tcp"
localIP = "127.0.0.1"
localPort = 22
remotePort = 6000
```
Client also gets its own local admin UI (`README.md` "Client Admin UI"):
```toml
webServer.addr = "127.0.0.1"
webServer.port = 7400
webServer.user = "admin"
webServer.password = "admin"
```
and, if `[store] path = "./db.json"` is set, frpc persists dynamically-created proxies/visitors to
that JSON file and reloads them on restart (`README.md` "Dynamic Proxy Management (Store)").

### Ports
- `7000` (configurable `bindPort`): frpc→frps control/data connection (TCP). This is the only port
  that must be reachable *from every local client machine* to the VPS.
- `7500` (configurable `webServer.port`, server): dashboard/REST admin API — should be firewalled
  to the SvelteKit backend only, not public.
- `80`/`443`: only needed on the VPS if you use frp's HTTP/HTTPS vhosting proxy type (subdomain
  routing, section 4) — frps itself listens on whatever `vhostHTTPPort`/`vhostHTTPSPort` you
  configure (not shown in the minimal examples above but documented alongside `subDomainHost`).
- `7400` (client-side `webServer.port`): frpc's own local admin API, bound to `127.0.0.1` by
  default — not exposed externally, used for local health/reload only (section 5).

### Wrapping as a systemd service
frp has no bundled unit file in this repo; standard practice given a single static binary + TOML
config:
```ini
[Service]
ExecStart=/usr/local/bin/frps -c /etc/frp/frps.toml
Restart=always
User=frp
```
For docker-compose, a single `frps` service with the config bind-mounted and `restart: always` is
equivalent — no external DB or extra daemons are required (frps holds all state in memory, plus the
optional `store.path` JSON file on the **client** side only).

### One-time global config
- **TLS**: `transport.tls.force = true` + `transport.tls.certFile`/`keyFile` for encrypting the
  frpc↔frps channel; `webServer.tls.certFile`/`keyFile` separately for the dashboard/API HTTPS.
- **Admin account**: `webServer.user`/`webServer.password` (dashboard/API basic-auth credentials),
  set once in `frps.toml`.
- **Shared token**: `auth.token` (or `auth.tokenSource.file.path`) must match on both `frps.toml`
  and every `frpc.toml` — this is the *server-wide* frpc-authentication secret (not per-client; see
  section 3 for how we still get per-client identity).
- **No DB init required** — frps has no database; state is entirely in-memory (connected clients,
  bound proxies) except for the optional client-side `store.path` JSON file.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ frp server)

frps exposes a genuine, documented REST API on the dashboard port (`webServer.addr:port`), gated by
HTTP Basic Auth (`webServer.user`/`webServer.password`, wired in
`pkg/util/http/server.go:86` via `netpkg.NewHTTPAuthMiddleware(cfg.User, cfg.Password)`). Routes are
registered in `server/api_router.go:42-60`:

- `GET /api/serverinfo` — server-wide stats.
- `GET /api/proxy/{type}` and `GET /api/proxy/{type}/{name}` — list/detail proxies by type
  (`tcp`, `http`, etc.).
- `GET /api/proxies/{name}` — single proxy lookup by name.
- `GET /api/traffic/{name}` — per-proxy traffic stats.
- `GET /api/clients` and `GET /api/clients/{key}` — **list all connected frpc clients / get one by
  key** (`server/http/controller.go:132-147`, `APIClientDetail`); this is the read surface for
  "is client X connected" (see section 5).
- `DELETE /api/proxies` — bulk-delete proxies.
- Newer v2 envelope endpoints: `GET /api/v2/clients`, `GET /api/v2/clients/{key}` (URL-encoded key
  route via `v2EncodedPathRouter`, `server/api_router.go:55-57`), `GET /api/v2/proxies`,
  `GET /api/v2/proxies/{name}`, `GET /api/v2/proxies/{name}/traffic`, `GET /api/v2/system/info`,
  `POST /api/v2/system/prune`, `GET /api/v2/users` — confirmed by the actual `mux.Router` route
  table in `server/api_router.go` and exercised in `server/http/controller_v2_test.go` (e.g. test
  fetches `/api/v2/clients/alice.client-a` and asserts `Status.State == "online"`,
  `Status.CurConns`, `Status.ProxyCount` — `controller_v2_test.go:347-361`).

**Critically, this API is read-only for clients/proxies that frpc itself created** — there is no
`POST /api/clients` to "register a new client" from the server side; a client only appears here
after it has connected using the shared token (section 3). So SvelteKit's control-plane usage is:
- **Read/poll** `GET /api/v2/clients/{key}` or `/api/clients/{key}` for connection status
  (section 5).
- **Manage proxies dynamically** — not via the frps dashboard API, but via the **frpc-side** admin
  API and Store (see below), or via the **Server Manage Plugin** hook for admission control.

### Server Manage Plugins — the real "programmatic control" hook
For anything requiring frps to call *out* to SvelteKit before allowing a login/new-proxy/new-conn,
frp supports **HTTP plugins**, documented in full in `doc/server_plugin.md`:
```toml
# frps.toml
[[httpPlugins]]
name = "user-manager"
addr = "127.0.0.1:9000"
path = "/handler"
ops = ["Login"]
```
frps POSTs JSON `{"version","op","content"}` to `addr+path` (e.g.
`POST /handler?version=0.1.0&op=Login`) for any of the ops **`Login`, `NewProxy`, `CloseProxy`,
`Ping`, `NewWorkConn`, `NewUserConn`** (`doc/server_plugin.md` lines 71-215) and expects a JSON
response `{"reject": bool, "reject_reason": "...", "unchange": bool, "content": {...}}` — reject to
deny, allow-unchanged, or allow-with-modified-content. The `Login` payload includes
`user`, `run_id`, `privilege_key`, `metas` (arbitrary key/value metadata sent by frpc,
`doc/server_plugin.md:75-95, 241-266`) — this is how SvelteKit authorizes/denies a specific client's
connection attempt in real time, and can inject per-client metadata for later identification.

Auth for SvelteKit's *outbound HTTP calls into* this plugin endpoint is whatever SvelteKit's own
handler enforces (frps sends no bearer token in the plugin request itself beyond the JSON body and
an `X-Frp-Reqid` tracing header — `doc/server_plugin.md:34-35`); the plugin channel is
frps→SvelteKit only, not SvelteKit→frps, so protect it at the network layer (bind
`127.0.0.1:9000`/internal network) rather than expecting frp-side auth.

### frpc-side (client) dynamic proxy API — the actual "create a tunnel programmatically" surface
The **frpc** admin API (bound to `127.0.0.1:7400` by default, Basic-Auth protected, same
`webServer.user/password` mechanism) exposes, per `client/api_router.go:34-51`:
- `GET /api/status` — this frpc instance's proxy statuses.
- `GET /api/config` / `PUT /api/config` — read/hot-replace the entire running config.
- `GET /api/reload` — hot-reload from the config file/store.
- `POST /api/stop` — stop the client.
- If `store.path` configured: `GET/POST /api/store/proxies`, `GET/PUT/DELETE
  /api/store/proxies/{name}`, and the same CRUD set for `/api/store/visitors` — **this is the real
  "create a tunnel programmatically" REST surface**, confirmed at
  `client/api_router.go:41-49`. A new proxy is added with
  `POST http://127.0.0.1:7400/api/store/proxies` (JSON body matching the proxy config schema) with
  no frps restart needed; it persists to the `store.path` JSON file and frpc registers it with frps
  automatically.

**Implication for our SvelteKit architecture:** frps's dashboard/API is server-wide and read-mostly;
the piece that actually lets you *create/attach an individual client's tunnel* lives on the
**frpc side**, running on the local client machine itself — so "SvelteKit talks to frp" in practice
means (a) SvelteKit polls the frps dashboard for connectivity status, and (b) our own local-client
daemon (wrapping frpc) is what calls frpc's local `/api/store/proxies` to register the llama-server
port — SvelteKit does not reach frpc's admin port directly since it's bound to `127.0.0.1` on the
*local* machine, not the VPS.

---

## Client Linking Flow (access token exchange)

frp's built-in authentication is a **single shared secret for the whole server** (`auth.token`),
not a per-client token: "Make sure to specify the same `auth.token` in `frps.toml` and `frpc.toml`
for frpc to pass frps validation" (`README.md` "Token Authentication"). Alternatives are
`auth.tokenSource.type = "file"` (read token from a file path at startup, useful if pulled from a
secrets manager, `README.md` "Token Source") or full **OIDC Client Credentials Grant**
(`README.md` "OIDC Authentication"):
```toml
# frps.toml
auth.method = "oidc"
auth.oidc.issuer = "https://example-oidc-issuer.com/"
auth.oidc.audience = "https://oidc-audience.com/.default"
```
```toml
# frpc.toml
auth.method = "oidc"
auth.oidc.clientID = "98692467-37de-409a-9fac-bb2585826f18"
auth.oidc.clientSecret = "oidc_secret"
auth.oidc.audience = "https://oidc-audience.com/.default"
auth.oidc.tokenEndpointURL = "https://example-oidc-endpoint.com/oauth2/v2.0/token"
```
Neither mechanism natively distinguishes *individual* clients for connect/reject decisions — that
requires wiring the **`Login` server plugin hook** (section 2). frp's own `user` field (a free-text
client identifier configurable per-frpc, distinct from `auth.token`) plus `metas.*` are what let a
plugin (and therefore SvelteKit) tell clients apart.

**Concrete linking flow we must build on top of frp's primitives:**
1. Global secret: one `auth.token` (or OIDC client credentials) shared by every legitimate frpc
   instance — this is *not* per-device; it only proves "you're allowed to talk to our frps at all",
   analogous to an API base secret, not a device pairing token.
2. Per-device identity: at install time, SvelteKit issues the new local-client machine a
   **device ID + our own opaque pairing token** (frp has no concept of this — 100% custom). The
   local-client daemon writes both the shared `auth.token` and a unique `user = "<deviceID>"` plus
   `metadatas.pairing_token = "<our token>"` into its generated `frpc.toml`
   (`metadatas.*` confirmed real in `doc/server_plugin.md:251-266`: "Metadata will be sent to the
   server plugin in each RPC request... Global metadata entries will be sent in `Login` under the
   key `metas`").
3. On first frpc connect, our `Login`-op server plugin (registered via `[[httpPlugins]]`,
   `ops = ["Login"]`) receives `{"user":"<deviceID>", "metas":{"pairing_token":"..."}, ...}`, looks
   up `pairing_token` in SvelteKit's own DB, and returns `{"reject": false}` if valid or
   `{"reject": true, "reject_reason": "unknown device"}` otherwise. This is the actual "pairing"
   enforcement point — frp defers entirely to our plugin's verdict per `doc/server_plugin.md:38-49`.
4. Revocation: delete/invalidate the pairing token row in our DB; the next `Login` (frpc reconnects
   periodically / on any restart) is rejected. For *immediate* kill of an already-open session, frp
   provides no disconnect-by-key API in this codebase (only `DELETE /api/proxies` to remove
   proxies) — killing an active TCP connection early is not exposed via the dashboard REST API we
   found; would need a custom mechanism (e.g. plugin-driven forced restart) — see Open Questions.

---

## Request Routing (public traffic → local client)

frp supports true HTTP virtual-hosting via subdomains, driven by the `subDomainHost` server setting
and per-proxy `subdomain` field — confirmed verbatim in `README.md` "Custom Subdomain Names":
```toml
# frps.toml
subDomainHost = "frps.com"
```
"Resolve `*.frps.com` to the frps server's IP. This is usually called a Wildcard DNS record."
```toml
# frpc.toml
[[proxies]]
name = "web"
type = "http"
localPort = 80
subdomain = "test"
```
"Now you can visit your web service on `test.frps.com`."

For our llama.cpp use case:
- Assign each linked device a fixed `subdomain = "<deviceID>"` when we (SvelteKit) provision its
  `frpc.toml` at pairing time — since the subdomain is just a config field on the client's own
  proxy definition, **no server-side "bind hostname to client" API call is needed once
  `subDomainHost` is globally configured**; the client's own connection announces its subdomain when
  it registers the proxy (either via static `frpc.toml` or dynamically via the frpc `/api/store/
  proxies` endpoint in section 2, whose JSON body includes the `subdomain` field per the schema
  implied by `[[proxies]]` TOML keys).
- Public HTTP requests to `https://<deviceID>.frps.com/` are matched by frps's vhost router (Host
  header) against the registered proxy and streamed to the frpc client's declared `localPort`
  (e.g., the llama-server's port) over the already-open frpc↔frps control/data connection.
- **URL Routing** (`README.md` ToC entry "URL Routing", same family of features as subdomain) also
  supports path-based `locations` for one shared domain if we prefer `frps.com/device-x/...`
  instead of subdomains — same mechanism, just an additional `locations = ["/device-x"]` field on
  the proxy (see `doc/server_plugin.md:122-124` for the `locations` field appearing in the
  `NewProxy` plugin payload, confirming it's a first-class per-proxy attribute alongside
  `custom_domains`/`subdomain`).
- **SvelteKit's role in routing**: none at request time — routing is table-driven inside frps once
  the frpc client has registered its proxy with the chosen subdomain/locations. SvelteKit's only
  involvement is at provisioning time: deciding what subdomain to assign (and persisting that
  mapping so the frontend can show `https://<deviceID>.frps.com`), then either (a) baking it into
  the `frpc.toml` we generate for the device installer, or (b) instructing the already-connected
  frpc's local `/api/store/proxies` to create the proxy with that subdomain.

---

## Status Polling / Health Checks

Two real, complementary mechanisms:

1. **Server-side dashboard API** (section 2): `GET /api/v2/clients/{key}` (or legacy
   `GET /api/clients/{key}`) returns connection status for a specific client, keyed by its `user`
   value. The v2 response includes a `Status` object with `State` (`"online"` observed in test
   fixture, `server/http/controller_v2_test.go:359`: `detailResp.Data.Status.State != "online"`),
   `CurConns`, and `ProxyCount` — exactly the "is this client connected and how busy is it" signal.
   `GET /api/v2/clients` lists all connected clients server-wide, useful for a dashboard-style view
   without per-client polling.
2. **frp's built-in Service Health Check feature** (README ToC "Service Health Check") lets frpc
   itself probe the *local* backend (e.g. llama-server's `/health`) and only keep the proxy
   registered with frps while the local service is healthy — this is a frpc-side config knob
   (per-proxy `healthCheck.*` fields, standard in frp's proxy TOML schema) rather than an API we
   poll; it affects whether the proxy shows up at all in `/api/v2/clients/{key}`'s proxy count,
   giving an indirect end-to-end health signal for free without SvelteKit needing to separately
   probe the device's tunnel.
3. **frpc's own local admin API** (`GET http://127.0.0.1:7400/api/status`, section 2) is only
   reachable on the client machine itself, useful for the local-client daemon's own self-health
   reporting (e.g., our monitor service could surface this locally and separately push a heartbeat
   to SvelteKit), not for SvelteKit to call directly across the network.

**Recommended combination**: SvelteKit polls `GET /api/v2/clients/{key}` on the frps dashboard for
"is device X's frpc process currently connected" (cheap, push-free — no webhook/event mechanism was
found in this codebase, confirming poll-only), while relying on frp's native
`healthCheck` config on the client side to ensure a connected-but-stale proxy is dropped/hidden
automatically rather than showing false-positive connectivity.

---

## Open Questions / Assumptions

- **No native force-disconnect-by-client API found** in `server/api_router.go`/`controller*.go`
  beyond `DELETE /api/proxies` (bulk delete by name) — unlike some other tunnel tools there is no
  confirmed `/api/clients/{key}` DELETE verb in this codebase; immediate revocation of an
  already-connected frpc likely requires either (a) the `Login`/`Ping` plugin hook rejecting on the
  next heartbeat (`auth.additionalScopes = ["HeartBeats"]` re-validates auth every heartbeat per
  `README.md` "Authenticating the Client" ToC) or (b) killing the TCP socket at the network/firewall
  layer. Needs a source read of `server/service.go`'s control-connection lifecycle to confirm
  whether any exposed op can force-drop a live control connection.
- **Dynamic proxy creation ownership**: confirmed the frpc-side `/api/store/proxies` REST API
  exists and is real (`client/api_router.go`), but did not trace the exact JSON schema accepted by
  `CreateStoreProxy` in `client/http/*.go` — for a production implementation, read
  `client/http/controller.go` (not yet opened) to get the definitive request/response body shape
  before wiring the local-client daemon to call it.
- **Where per-device metadata is best carried**: `metas`/`metadatas.*` are confirmed to reach the
  `Login` server plugin, but whether they're also queryable later via the dashboard API (e.g. do
  `/api/v2/clients/{key}` responses echo back `metas`?) was not verified in
  `server/http/controller_v2.go` beyond the `User`/`ClientID`/`Status` fields seen in the test file
  — may need metas surfaced separately if SvelteKit needs to display device metadata alongside
  connection status.
- **OIDC vs plugin-Login interplay**: not verified whether `auth.method = "oidc"` and the
  `httpPlugins` `Login` hook can be combined (OIDC for the shared-secret layer, plugin for
  per-device authorization) — the README documents them as independent features; worth a source
  check of `pkg/config/v1/auth.go` (referenced but not opened) before assuming both apply
  simultaneously.
- **v1 vs v2 API and the "V2" refactor in progress**: the README ("Development Status") states frp
  is actively working on a not-yet-compatible "v2" architecture with a stated goal of
  "modernized... configuration management, permission verification, certificate management, and API
  management" — implying today's admin API surface described above may change; pin to a specific
  released version/tag rather than tracking `dev`/`master` HEAD for production.
