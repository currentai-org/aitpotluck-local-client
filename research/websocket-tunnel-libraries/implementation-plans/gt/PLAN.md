# gt (ao-space/gt) Implementation Plan

**Source verified against:** `git clone https://github.com/ao-space/gt` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/gt` (main branch at clone time).
Key files/docs cited: `README.md`/`README.en.md` (top-level, Chinese project with English
translation), `libcs/README.md` (the actual engine, "GT" — the top-level repo is a thin CGO/Rust
wrapper (`bin/`) around the Go engine in `libcs/`), `libcs/example/config/server.yaml`,
`libcs/example/config/client.yaml`, `libcs/server/config.go`, `libcs/client/config.go`,
`libcs/server/server.go`, `libcs/server/api/server.go`, `libcs/server/web/web.go`,
`libcs/server/web/api/api.go`, `libcs/web/front/src/api/modules/*.ts`, `entrypoint-server.sh`,
`Dockerfile-server`, `Dockerfile-client`.

**Important structural finding**: the top-level `ao-space/gt` repo (`bin/`, `Cargo.toml`) is a Rust
CGO wrapper/build harness around a vendored Go engine at `libcs/` (originally the separate
`isrc-cas/gt` project, Go module `github.com/isrc-cas/gt`). All of the actual server/client/API
logic lives in `libcs/`, so this plan cites `libcs/*` paths throughout — that is genuinely "the gt
binary." The task brief's expectation of a "simpler Rust-ish binary with a web config UI" matches:
the CLI/binary distribution is what ships, and it embeds an admin Vue.js SPA (`libcs/web/front/`)
served over gin-backed REST (`libcs/server/web/`).

---

## Installation & Global VPS Configuration

**1. Prebuilt binary (documented primary path, `libcs/README.md` "Run"):**
```bash
# download from GitHub Releases, e.g.:
./release/linux-amd64-server -addr 80 -id id1 -secret secret1
```
Or with a config file:
```bash
./release/linux-amd64-server -config server.yaml
```
Releases are per-arch static binaries (`linux-amd64-server`, etc.) confirmed by benchmark command
lines throughout `README.md`/`README.en.md`.

**2. Docker (documented, `Dockerfile-server` + `entrypoint-server.sh`):** the shipped server image
generates its own YAML config from environment variables at container start, then execs the
binary:
```bash
docker run -d --name gt-server \
  -p 80:80 -p 443:443 -p 4443:4443 -p 81:81 -p 3478:3478/udp \
  -e NETWORK_ADDR=80 -e NETWORK_TLSADDR=443 -e NETWORK_SNIADDR=4443 \
  -e NETWORK_API_ADDR=0.0.0.0:81 \
  -v $(pwd)/certs:/opt/crt \
  ghcr.io/ao-space/gt:server-dev   # or a pinned server-<tag>
```
`entrypoint-server.sh` shows the exact generated config:
```yaml
version: 1.0
options:
  addr: ${NETWORK_ADDR:-80}
  tlsAddr: ${NETWORK_TLSADDR:-443}
  sniAddr: ${NETWORK_SNIADDR:-4443}
  certFile: /opt/crt/tls.crt
  keyFile: /opt/crt/tls.key
  logLevel: ${NETWORK_LOGLEVEL:-info}
  apiAddr: ${NETWORK_API_ADDR:-0.0.0.0:81}
  sentryDSN: ${NETWORK_SENTRYDSN}
  authAPI: ${NETWORK_AUTHAPI}
  timeout: ${NETWORK_TIMEOUT:-90s}
  httpMUXHeader: EID
  stunAddr: ${NETWORK_STUNADDR:-3478}
  reconnectTimes: ${NETWORK_RECONNECTTIMES:-3}
```
then `exec /usr/bin/gt server -c /opt/aonetwork-server.yml`. Docker images published at
`ghcr.io/ao-space/gt` (README.md "Docker 容器": `docker pull ghcr.io/ao-space/gt:server-dev`).
**No `docker-compose.yml` is shipped in the repo** — Compose wrapping would be our own thin
addition (a single service block around the `docker run` above); nothing in the repo suggests any
special multi-container orchestration is needed since gt is a single self-contained binary/process
per server or client.

**3. Build from source** (`libcs/Makefile`, `BuildOnWindows.md`): standard `go build`, but the
top-level repo additionally requires building a Rust/CGO WebRTC layer (`bin/`, `Cargo.toml`,
`libcs/client/webrtc/*.cpp`) if P2P/WebRTC support is wanted — heavyweight (needs a WebRTC native
lib per `libcs/dep/patch/`). For a pure relay-mode VPS deployment (no WebRTC/P2P), only the Go
`libcs` engine needs building; this is by far the simplest of the three paths for our scenario.

**Config file** (`libcs/example/config/server.yaml`, full field list in `libcs/server/config.go`
`Options` struct):
```yaml
version: 1.0
options:
  addr: 80          # HTTP listener
  sniAddr: 443       # raw TLS SNI-passthrough listener (no cert needed on gt server for this mode)
  # tlsAddr: 4443    # TLS listener if terminating TLS at gt itself (needs certFile/keyFile)
  # certFile: /opt/crt/tls.crt
  # keyFile: /opt/crt/tls.key
  logLevel: info
  timeout: 90s
  stunAddr: 3478     # STUN service for WebRTC P2P NAT traversal (optional)
  apiAddr: 0.0.0.0:81   # internal status API (see section 5)
  webAddr: 127.0.0.1:8000  # admin Web UI/API (gin), see section 2
users:
  id-should-be-overwritten:
    secret: secret-should-be-overwritten
    tcp:
      - range: 10000-15000
      - range: 20000-25000
```
**Ports summary**: `addr` (plain HTTP multiplexing on Host header), `sniAddr` (raw TLS SNI
passthrough — recommended for HTTPS local services since gt never sees plaintext), optional
`tlsAddr`+`certFile`/`keyFile` (gt terminates TLS itself), `apiAddr` (internal health/status API,
should stay private/loopback or firewalled), `webAddr` (the admin Web UI + REST API — should be
bound to `127.0.0.1` and reverse-proxied or SSH-tunneled for real production use since it is not
documented as safe to expose directly), `stunAddr` (only needed for the optional WebRTC P2P mode,
irrelevant to a pure server-relay use case), optional `quicAddr` for QUIC transport between gt
client and gt server.

**One-time global config**:
- **TLS**: either (a) put gt behind `sniAddr` SNI-passthrough mode and let each backend service
  terminate its own TLS (no cert needed on gt at all — the same pass-through pattern Portal
  documents, and privacy-preserving since gt "只定位获取第一个数据包的 HTTP 协议头" — only reads the
  first packet's routing target, never terminates), or (b) supply `certFile`/`keyFile` for
  `tlsAddr` if gt itself should terminate TLS for plain-HTTP local services exposed over HTTPS.
- **"Admin account"**: the Web UI has `admin`/`password` fields (`Options.Admin`,
  `Options.Password` in `libcs/server/config.go`, flags `-admin`/`-password`) plus a mandatory
  `-signingKey`/`SigningKey` used to sign JWTs for Web UI sessions
  (`libcs/server/web/server/util/jwt.go`, `api.Login()` in `libcs/server/web/api/api.go` calls
  `util.GenerateToken(s.Config().SigningKey, predef.DefaultTokenDuration, "gt-server", loginReq)`).
  This is a genuine username/password + JWT admin login, distinct from the tunnel `id`/`secret`
  client-auth pairs.
- **"DB init"**: none. All config/state is flat YAML files (`server.yaml`/`users.yaml`), no
  database process to initialize.
- **User/client auth setup (functional equivalent of "which clients may connect")**: four
  layered mechanisms, checked in priority order (`libcs/README.md` "Server User Configuration"):
  1. CLI flags: `-id id1 -secret secret1` (repeatable, positionally paired).
  2. A separate `users.yaml` (`Options.Users` path) — `id: {secret: ...}` map, hot-loadable
     without touching the main config.
  3. Inline `users:` block in the main config YAML (as in `server.yaml` above).
  4. `-allowAnyClient`: no pre-configured users at all; first-connect-wins on a given `id`, its
     `secret` locked in from then on (`libcs/server/server.go` `authUserOrCreateUser`).
  A fifth, **API-based** mechanism also exists — see next section; it supersedes the static
  YAML-based approaches when configured.

Confirmed **CI-free systemd wrapping** (not shipped, but trivial given a single static binary +
flat YAML config), same pattern as sish/wstunnel:
```ini
[Service]
ExecStart=/usr/local/bin/gt server -c /etc/gt/server.yaml
Restart=always
```

---

## Cloud-Side Control-Plane API (SvelteKit ↔ gt server)

gt has **two structurally different HTTP surfaces on the server side**, and it's important not to
conflate them:

1. **`apiAddr` — internal lightweight status API** (`libcs/server/api/server.go`,
   `NewServer(addr, ...)`, mounted with plain `net/http`, no auth):
   - `GET /status` — runs a **live synthetic end-to-end test**: the server spins up an in-process
     gt client with a freshly random id/secret (`s.randomIDSecret()`), connects it to itself over
     the configured remote schema, and does a real proxied HTTP round-trip to confirm the whole
     tunnel path (auth → connect → HTTP forward) works, returning `{"status":"ok"/"failed",
     "version":"..."}`. This is a genuine synthetic-canary health check of the whole gt server, not
     of any specific client.
   - `GET /statusResp` — the trivial echo endpoint the synthetic client above hits internally;
     not meant to be called externally.
   - This API is intentionally minimal and **has no endpoints for creating tunnels, listing
     clients, or per-client tokens** — it's a liveness probe only.

2. **`webAddr` — the full admin Web UI + REST API** (`libcs/server/web/web.go`, Gin-based, backs
   the bundled Vue.js SPA in `libcs/web/front/`). This is the real management-plane API and the one
   SvelteKit would integrate with. Routes confirmed in `libcs/server/web/web.go`:

   | Method | Path | Auth | Purpose |
   |---|---|---|---|
   | `POST` | `/api/login` | none (username+password body) | `api.Login()` → returns `{token: <JWT>}` signed with `SigningKey` |
   | `GET` | `/api/health` | none | plain health check |
   | `GET` | `/api/verify?key=` | none | exchange a short-lived temp key for the actual JWT (`VerifyTempKey`, `TokenManager.GetActualToken`) |
   | `POST` | `/user/change` | JWT | change admin username/password |
   | `GET` | `/user/info` | JWT | get admin user info |
   | `GET` | `/config/running` | JWT | `GetRunningConfig` — read the live in-memory server config as JSON |
   | `GET` | `/config/file` | JWT | `GetConfigFromFile` — read config from disk (falls back to running config) |
   | `POST` | `/config/save` | JWT | `SaveConfigToFile` — **write a new server config to disk** (this is the closest thing to "create/register a tunnel/service programmatically": editing `users`/TCP ranges/etc. via this endpoint) |
   | `GET` | `/server/info` | JWT | OS/CPU/memory/disk info (`GetServerInfo`) |
   | `GET` | `/connection/list` | JWT | `GetConnectionInfo` → `{serverPool: SimplifiedConnectionWithID[], external: SimplifiedConnection[]}` — **live connected-client list**, see section 5 |
   | `GET` | `/permission/menu` | JWT | Vue admin-menu structure (irrelevant to backend integration) |
   | `/debug/pprof/*` | JWT (optional, `EnablePprof`) | Go pprof, ops-only |

   All JWT-protected routes require `Authorization: Bearer <token>` (standard Gin JWT middleware,
   `libcs/web/server/middleware/jwt.go`), where the token is obtained from `POST /api/login` with
   the configured `admin`/`password` and is signed with the server's `signingKey`.

**Bottom line for SvelteKit integration**: gt's programmatic surface is a **generic
config-management REST API** (login → get/save whole YAML config → connection list), not a
purpose-built "register a tunnel" resource-oriented API like Portal's `/sdk/*`. To "add a new
client" programmatically, SvelteKit would:
1. `POST /api/login {username, password}` → get JWT.
2. `GET /config/running` (or `/config/file`) → fetch current config JSON.
3. Mutate the `Users` map in that JSON (add `{id: {secret: <generated>}}`), or generate the
   `id`/`secret` on our own DB side and let the local client present them directly (see next
   section — gt does not require the *server* to be told about a client until that client's first
   connection attempt when using `-allowAnyClient`).
4. `POST /config/save` with the updated config to persist it — **this rewrites gt's on-disk
   config file; it is not clear from source whether it hot-reloads the running listener state
   without a restart/reload signal** (see Open Questions). A safer alternative is the `-authAPI`
   callback mechanism below, which needs no config mutation at all.

**Better alternative — the `authAPI` callback (recommended, avoids config-file mutation
entirely):** `Options.AuthAPI` (`-authAPI` flag / `authAPI` YAML key,
`libcs/server/config.go` line 52) makes the **gt server call out to us** on every client connect
attempt, instead of consulting local YAML/users.yaml at all
(`libcs/server/server.go` `authWithAPI`, lines ~595-635):
```go
POST <authAPI-url>
Content-Type: application/json
Request-Id: <unix-timestamp>

{"networkClientId": "<id>", "networkSecretKey": "<secret>", "appletTokens": [...]}
```
Our endpoint must respond `200 OK` with a JSON body containing `{"result": true, "appletTokens":
["<hostPrefix1>", ...]}` (`jsonparser.GetBoolean(r, "result")`,
`jsonparser.ArrayEach(r, ..., "appletTokens")`); `result: true` authorizes the connection and the
returned `appletTokens` array becomes the set of host-prefixes that client id is allowed to bind
(plus the `id` itself is always implicitly allowed as a host prefix). This is functionally
identical to sish's `authentication-key-request-url` callback pattern: **our own DB becomes the
source of truth for "who can connect," with zero gt-side config file mutation needed.** When
`AuthAPI` is set, gt disables its own static-list/`allowAnyClient` auth paths entirely
(`server.go` `Start()`: `if len(s.config.AuthAPI) > 0 { s.authUser = nil; ... }`).

---

## Client Linking Flow (access token exchange)

gt's native model is a **static, pre-shared `id`+`secret` pair** — there is no self-registration,
challenge/response, or wallet-style identity like Portal. Confirmed by `libcs/client/config.go`
`Options.ID`/`Options.Secret` ("The unique id used to connect to server. Now it's the prefix of the
domain." / "The secret used to verify the id") and every usage example in the README
(`./client -local http://localhost:8080 -remote tcp://localhost:7001 -id pi -threads 3`).

The exact flow for a brand-new machine, mapped onto our access-token pairing UX:

1. **Server-side, at pairing time**: our SvelteKit backend (or the local-client installer talking
   to SvelteKit) generates a fresh `id` (which will literally become the tunnel's DNS
   host-prefix, e.g. `device-<uuid>`) and a `secret` (random string, subject to gt's length
   bounds: `predef.MinIDSize`/`MaxIDSize`, `MinSecretSize`/`MaxSecretSize` — confirmed constraints
   checked in `libcs/server/config.go` `users.verify()`).
2. **Authorization on the server**: either
   - (A) write `{id: {secret}}` into the server's `users.yaml`/main config `users:` block via the
     `/config/save` Web API call from step 3 of the previous section, or
   - (B) do nothing server-config-side and instead rely on our own DB + the `authAPI` callback
     (recommended — see previous section) to approve this exact `id`/`secret` pair the moment the
     new client's first connection attempt arrives.
3. **Client-side, first run**: the installer writes the local client config
   (`libcs/example/config/client.yaml` shape):
   ```yaml
   version: 1.0
   services:
     - local: http://127.0.0.1:8080      # our llama-server
       hostPrefix: web                    # or omit to default to using `id` as the host prefix
   options:
     id: device-<uuid>
     secret: <the pre-shared secret from step 1>
     remote: tcp://relay.example.com:80   # or tls://...:443, or quic://...:443
     remoteConnections: 5
     logLevel: info
   ```
   This `id`+`secret` pair **is the entire "pairing token"** — there is no separate
   enrollment/handshake step in gt itself; the pair is simply presented as auth on every TCP
   connection the client pool makes to the server (`libcs/client/client.go`,
   `libcs/server/server.go` `authUser*` family). Our access-token exchange from SvelteKit is
   therefore: *the "access token" IS the `id:secret` pair*, generated and stored by us, and handed
   to the local-client installer once (e.g. embedded in the same one-time pairing flow that
   downloads/configures the monitor service), then written verbatim into that machine's
   `config.yml` for gt.
4. **No token rotation/expiry is built in.** The `secret` is a long-lived static credential from
   gt's point of view (unless we build our own periodic-rotation logic through the `authAPI`
   callback, e.g. having our DB reject a previously-valid secret after N days and requiring the
   client to re-pair). This is materially weaker than Portal's self-signed, per-lease
   ES256K-token model — gt's revocation story is exactly "our DB stops saying yes" (if using
   `authAPI`) or "delete the user from `users.yaml`+ reload/restart" (if using static config).
5. **Multiple relay/server addresses**: `Options.Remote` is a `config.Slice[string]`, i.e.
   **repeatable/comma-separated** — a client can be configured to fail over across multiple `gt
   server` instances by listing several `remote:` URLs, useful for redundancy but not relevant to
   basic single-relay pairing.

---

## Request Routing (public traffic → local client)

Routing is **subdomain/host-prefix-based**, similar in spirit to sish/Portal but resolved via an
explicit `hostPrefix` field rather than automatic subdomain synthesis:

- Each local service block in the client config declares a `hostPrefix` (`libcs/example/config/
  client.yaml`: `hostPrefix: blog`, `hostPrefix: web`, `hostPrefix: www`). The server recognizes
  `<hostPrefix>.<base-domain>` (or, per `libcs/client/config.go` `Options.HostPrefix` doc string,
  "The server will recognize this host prefix and forward data to local") and routes matching
  inbound `Host`/SNI to that client's declared `local:` target.
- If a service's `hostPrefix` is omitted, gt defaults it to the client's own `id`
  (`libcs/client/client.go` lines ~819-837: `usedIDASHostPrefix` logic — "if no hostPrefix is
  given, the id itself becomes the host prefix, and only one un-prefixed service per client is
  allowed"). This means **the simplest deployment needs zero extra "routing" configuration at
  all**: set `id: device-<uuid>` and a single `local: http://127.0.0.1:8080` service with no
  `hostPrefix`, and the tunnel is automatically reachable at `https://device-<uuid>.relay.example.
  com` (SNI-passthrough) or `http://device-<uuid>.relay.example.com` (plain HTTP multiplexing) —
  the base domain itself is whatever DNS you point at the gt server's public IP (gt does **not**
  run its own DNS server the way Portal does; a real wildcard `*.relay.example.com A <server-ip>`
  DNS record is required, set up manually/externally).
- Three local-service transport modes, chosen by the `local:` URL scheme
  (`libcs/example/config/client.yaml` comments): `http://` (plain HTTP forwarding, first-packet
  Host-header routing only — gt reads only the HTTP header, then forwards raw), `https://` (SNI
  passthrough — gt reads only the TLS ClientHello SNI, never decrypts, matching Portal's tenant-TLS
  privacy model), and `tcp://` with `remoteTCPPort`/`remoteTCPRandom` (a dedicated raw TCP port on
  the server forwarded straight to a local TCP service — for non-HTTP protocols like SSH/SMB/DB).
  For our llama-server-over-HTTP case, plain `http://` local target under a per-device `hostPrefix`
  (or the default id-as-prefix) is the natural fit.
- **SvelteKit does not need to call any API to "bind a hostname to a client ID."** Exactly like
  Portal, the hostname binding is entirely declared by the local client's own config (`hostPrefix`
  or the id-default), not pushed from the server/control-plane side. The one thing SvelteKit's
  `authAPI` callback *can* additionally restrict is which `hostPrefix` values (`appletTokens` in
  the callback response) a given `id` is allowed to claim — so if we want centralized enforcement
  of "device X may only claim hostname device-X," we return `appletTokens: ["device-<uuid>"]` from
  our `authAPI` response and gt will reject any other prefix that client's config tries to bind.

---

## Status Polling / Health Checks

Two genuinely separate mechanisms, at two different levels:

1. **`GET /connection/list`** on the admin Web API (`webAddr`, JWT-protected,
   `libcs/server/web/api/api.go` `GetConnectionInfo`, backed by `Server.GetConnectionInfo()` in
   `libcs/server/server.go` line 1106) → `{serverPool: SimplifiedConnectionWithID[], external:
   SimplifiedConnection[]}`. `serverPool` is the live list of gt-client TCP-pool connections
   currently held open on the server, each tagged with an `id` (`SimplifiedConnectionWithID.ID`)
   plus local/remote address and connection status
   (`libcs/web/server/model/request/connection.go`). **This is the direct per-client
   "is device X currently connected" signal**: poll this endpoint and check whether the target
   device's `id` appears with an active `Status`. This is the closest gt equivalent to sish's
   `/_sish/api/clients` or Portal's `/api/policy/state`.
2. **`GET /status`** on the lightweight `apiAddr` internal API (`libcs/server/api/server.go`) is a
   **synthetic whole-server canary**, not a per-client check — it spins up a throwaway client and
   round-trips real traffic through the server to prove the server's own tunnel/HTTP-forwarding
   path is functioning, independent of any specific real client. Useful as our own
   relay-process-liveness probe (e.g. wired into an uptime monitor), but it cannot answer "is
   device X connected."
3. **No push/webhook notifications** of connect/disconnect exist in the codebase — this is
   poll-only, same conclusion as every other tool surveyed so far.
4. **CLI-side signal control** (`gt -s reload|restart|stop`, top-level `--signal` flag in the
   `Usage:` block) sends OS signals to a running `gt` process for config reload/restart/stop; not
   a status-check mechanism, but relevant operationally (e.g. after using `/config/save` to change
   `users.yaml`, a `reload` signal may be needed to pick it up — see Open Questions).
5. **End-to-end health**: as with Portal/sish, directly HTTP-probing
   `https://device-<uuid>.relay.example.com/health` through the live tunnel remains the most
   meaningful check of whether the actual `llama-server` behind the tunnel is responsive, since
   `/connection/list` only proves the TCP pool to the gt server is alive.

---

## Open Questions / Assumptions

- **Does `/config/save` hot-reload, or does it require a `reload`/`restart` signal?** Source
  shows `SaveConfigToFile` writes to disk (`libcs/server/web/api/api.go` calls into
  `service.SaveConfigToFile`, not fully traced in this pass) but it is not confirmed whether the
  running `users` map (`s.users` `sync.Map` in `server.go`) is updated in-process immediately or
  only after a `gt -s reload` / process restart. **If config-file mutation requires a
  reload/restart to take effect, the `authAPI` callback path (no file mutation, evaluated live on
  every connect) is strictly the better integration for SvelteKit** — this plan recommends
  `authAPI` for that reason, but the hot-reload behavior of `/config/save` should be verified
  against a running instance before ruling it out.
- **`webAddr` admin API exposure**: nothing in the docs discusses TLS/CORS/rate-limiting for the
  `webAddr` Gin server itself (`WebCertFile`/`WebKeyFile` options exist — `libcs/server/config.go`
  — implying it *can* run its own TLS, but the default/documented posture is unencrypted loopback).
  For SvelteKit to call this remotely, it should be placed behind our own reverse proxy/VPN and
  never exposed directly to the public Internet; the JWT itself doesn't grant network-level
  protection.
- **Token/session lifetime**: `predef.DefaultTokenDuration` is used for the login JWT but its
  actual value was not located in this pass (`libcs/predef/const.go`/`predef/version.go` not
  fully inspected) — worth confirming so SvelteKit knows the JWT refresh cadence needed for
  long-running automation.
- **`allowAnyClient` vs `authAPI` interplay in a multi-tenant setting**: `Start()` treats these as
  mutually exclusive auth strategies (`AuthAPI` set ⇒ static/allowAnyClient paths are disabled
  entirely) — good for our case (we want one authoritative source of truth: our own DB via
  `authAPI`), but it means we cannot layer `authAPI` as merely a *fallback* alongside static
  `users.yaml` entries; it's one or the other globally per server process.
- **No SIWE/wallet-style identity, no per-lease scoped tokens, no built-in DNS automation, no ACME
  automation** — gt is a materially simpler, more "roll your own ops" tool than Portal. TLS
  termination/ACME (if wanted) and wildcard DNS are both entirely our own responsibility to set up
  around gt (e.g. terraform/certbot + a `*.relay.example.com A <ip>` record), whereas Portal
  automates both. This is the single biggest architecture cost difference between the two
  finalists.
- **No admin API to force-disconnect a specific client** was found (unlike sish's
  `/_sish/api/disconnectclient/<name>` or an equivalent) — revocation via `authAPI` only prevents
  *future* reconnections; an already-open TCP pool connection may persist until the client's own
  keepalive/idle timeout (`Options.Timeout`, default 90s) or a full server restart. Not confirmed
  whether any other undocumented signal exists to kill a specific live connection.
- **We did not build or run gt** in this research pass (no network access to spin up a real
  server+client pair); all API shapes and flows above are traced directly from Go source
  (`libcs/server/`, `libcs/client/`, `libcs/web/`) and the example YAML configs, but not
  empirically exercised end-to-end. A smoke test (`gt server`, `gt client`, hit `/api/login`,
  `/connection/list`, and the `authAPI` callback with a local stub) is recommended before
  committing to gt for production.
