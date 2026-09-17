# chiSSL Implementation Plan

Sources used (all fetched/read directly, not summarized secondhand):
- Repo: `https://github.com/NextChapterSoftware/chissl` cloned to `/opt/data/aipotluck-local-client/.scratch/clones/chissl` (commit as of 2026-09-17)
- `README.md` (repo root)
- `server_installer.sh` (repo root)
- `docs/quickstart.md`, `docs/client.md`, `docs/database.md`, `docs/guide/api-auth.md`, `docs/guide/security.md`, `docs/guide/tunnels-https.md`, `docs/guide/listeners.md`
- Docs site: `https://unblocked.github.io/chissl/` and `https://unblocked.github.io/chissl/openapi-public.yaml`
- Source: `main.go`, `client/client_connect.go`, `client/config.go`, `server/server.go`, `server/server_handler.go`, `server/server_api.go`, `server/server_api_user.go`, `server/server_api_port_reservations.go`, `server/listener_manager.go`

## Installation & Global VPS Configuration

**Distribution channels found in the repo (no Dockerfile exists in this repo — Docker is NOT an officially supported install path for chiSSL as of this clone):**

1. **One-line installer script (Linux, recommended)** — from README.md and `server_installer.sh`:
   ```bash
   bash <(curl -fsSL https://raw.githubusercontent.com/unblocked/chissl/v2.0/server_installer.sh) FQDN_HERE [port] [admin_password]
   ```
   What it actually does (read directly from `server_installer.sh`):
   - Detects OS/arch (amd64, arm64, armv5/6/7, 386 unsupported for server) via `uname`.
   - Resolves the **latest GitHub release** asset via `https://api.github.com/repos/unblocked/chissl/releases/latest`, greps for a `chissl-server`+`linux`+`<arch>` binary.
   - Downloads it and installs to `/usr/local/bin/chissl` via `sudo install -m 0755`.
   - Writes a **systemd unit** at `/etc/systemd/system/chissl.service`:
     ```ini
     [Unit]
     Description=Chissl Service
     After=network.target

     [Service]
     ExecStart=/usr/local/bin/chissl server -v --port $PORT --tls-domain $FQDN --auth "$ADMIN_USER:$ADMIN_PASS" --dashboard
     Restart=always
     User=root

     [Install]
     WantedBy=multi-user.target
     ```
   - Runs `systemctl daemon-reload`, `enable`, `start`, then health-checks via `curl https://$FQDN:$PORT/health`.
   - Prints the generated admin username/password at the end — **capture this output**, there is no other way to retrieve the initial admin credential later.

2. **Manual binary download**: grab the release asset for your OS/arch from `https://github.com/unblocked/chissl/releases`, place at `/usr/local/bin/chissl`, `chmod +x`.

3. **Build from source** (Go 1.23+ per README): `go build ./...` from repo root. `main.go` builds a single binary handling both `server` and `client` subcommands (`main_server_enabled.go` / `main_server_stub.go` suggest server support can be compiled out for a client-only build).

4. **Docker**: not present in this repo — no `Dockerfile`/`docker-compose.yml` found anywhere in the tree. If containerization is required, you'd have to build your own image around the downloaded/self-built binary; there is no official one to base a `docker-compose.yml` service on. Treat systemd as the supported "24/7 service" path.

**Config file(s):**
- The **server** is configured entirely via CLI flags / env vars, not a config file — confirmed by `main.go`'s `serverHelp` text and flag definitions (`--host`, `--port`, `--auth`, `--authfile`, `--tls-domain`/`--tls-key`/`--tls-cert`, `--db-*`, `--auth0-*`, `--dashboard`). The one file-based option is `--authfile users.json` (optional, for multiple pre-shared user/pass + address-regex ACLs), auto-reloaded on change (per `main.go` comment on `--authfile`).
- The **client** optionally reads a YAML profile at `$HOME/.chissl/profile.yaml` (or `--profile <path>`), per `docs/client.md` and `client/config.go` (`NewClientConfig`, `parsePath`). Fields: `server`, `auth`, `keepalive`, `max-retry-count`, `max-retry-interval`, `remotes[]`, `headers`, `tls.*`, `proxy`, `verbose`.

**Ports:**
- Main listener port defaults to **8080** (env `PORT`, flag `--port`) — this is the single HTTP/WS port that also upgrades to the chisel SSH-over-WebSocket tunnel protocol and serves `/api/*`, `/dashboard`, and `/health`.
- When `--tls-domain` is used (automatic Let's Encrypt), **port 443 is required** (README/quickstart: "Setting --tls-domain requires port 443") and **port 80 must also be open** for the ACME HTTP-01 challenge (quickstart.md: "The server will automatically start an HTTP listener on port 80 for this purpose").
- Each tunnel/listener additionally opens its own port on the server as specified in the mapping (`local-port:local-host->remote-port:remote-host`), e.g. `chissl client ... "8080->80"` opens port 8080 on the server.

**Global (one-time, server-wide) configuration required:**
- **TLS/domain**: point the FQDN's A/AAAA record at the VPS; pass `--tls-domain your.fqdn` for automatic Let's Encrypt (cache at `$HOME/.cache/chisel`, override with `CHISEL_LE_CACHE`, disable with `CHISEL_LE_CACHE=-`, or supply your own cert/key via `--tls-key`/`--tls-cert` instead — mutually exclusive with `--tls-domain`, per `main.go` serverHelp).
- **Admin account creation**: there is no separate "create admin" step — the admin identity **is** whatever you pass via `--auth admin:PASSWORD` at startup (confirmed in `server/server_api_user.go`: `handleGetUserInfo` special-cases the CLI `--auth` user as `isCLIAdmin`, and `isUserAdmin()` treats a request as admin if the username matches `settings.ParseAuth(s.config.Auth)`). Additional non-CLI users/admins are created later via the dashboard or `POST /api/users` (see below), stored in the DB.
- **Database init**: none required manually — SQLite (`--db-file ./chissl.db`, default) is created automatically on first run. PostgreSQL requires a pre-existing DB/user you supply via `--db-type postgres --db-host --db-port --db-name --db-user --db-pass --db-ssl` (or `CHISSL_DB_*` env vars) — this is the only piece of "global config" that requires you to provision something outside chiSSL itself (`docs/database.md`).
- **Dashboard**: enabled via `--dashboard` flag, served at `--dashboard-path` (default `/dashboard`).
- Optional global config: `--auth0-enabled`/`--auth0-domain`/`--auth0-client-id`/`--auth0-client-secret`/`--auth0-audience` for SSO; `--authfile` for a static multi-user file; login-backoff / IP-rate-limit settings (`docs/guide/security.md`) configurable via dashboard/API, not flags.

## Cloud-Side Control-Plane API (SvelteKit ↔ chiSSL server)

Real endpoint list extracted directly from `https://unblocked.github.io/chissl/openapi-public.yaml` (the "public" OpenAPI spec — the dashboard's internal JS under `server/dashboard/static/js/views/*.js` calls a superset of this, but the following is the documented contract):

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/system` | Version/uptime/fingerprint | user |
| GET | `/api/stats` | total/active tunnel counts | user |
| GET | `/api/users` | list users | **admin** |
| POST | `/api/users` | create user (`username`, `password`, `is_admin`) | **admin** |
| PUT | `/api/users` | update user (`password`, `is_admin`) | **admin** |
| DELETE | `/api/users` | delete user | **admin** |
| GET | `/api/listeners` | list listeners (mock/proxy) | user |
| POST | `/api/listeners` | create listener (`name`, `mode`: proxy\|mock, `port`, `target_url`, `use_tls`) | user |
| GET/PUT/DELETE | `/api/listener/{id}` | get/update/delete a listener | user |
| GET | `/api/tunnels` | list tunnels | user |
| GET/DELETE | `/api/tunnels/{id}` | get/delete a tunnel | user |
| GET | `/api/sessions` | list active sessions | **admin** |

Source code (`server/server_handler.go`, `server/server_api.go`, `server/server_api_user.go`, `server/server_api_port_reservations.go`) shows additional undocumented-but-real endpoints beyond the published spec, e.g.:
- `GET/POST /api/users/{name}/tokens`, `DELETE /api/users/{name}/tokens/{id}` (per-user API token CRUD — see `handleListUserTokens`/`handleCreateUserToken`/`handleRevokeUserToken` in `server_api_user.go`)
- `GET /api/tunnels/active` (`handleGetActiveTunnels`)
- `GET /api/connections?tunnel_id=...` (`handleGetConnections`)
- `GET /api/capture/tunnels/{id}/stream` (SSE live capture), `GET /api/capture/tunnels/{id}/recent`
- `GET/POST /api/port-reservations`, `GET /api/port-reservations/mine` (admin reserves a port range per-user — `server_api_port_reservations.go`)
- `DELETE /api/tunnels/closed`, `DELETE /api/sessions/closed` (cleanup)

**Creating/registering a tunnel programmatically:** chiSSL's REST API does **not** expose a "create tunnel" endpoint that pre-provisions a route for a not-yet-connected client. Tunnels are created as a side effect of a client actually connecting with a `remote` mapping (`local-port->remote-port` over the SSH/WebSocket session) — the DB row appears once the client handshake succeeds. The closest thing to "pre-registering" a route is a **listener** (`POST /api/listeners`, mode `proxy`, with a `target_url`), which is server-side routing config independent of any specific tunnel connection, or a **port reservation** (`POST /api/port-reservations`) which pre-allocates a port range to a specific username so their client can claim it. There is no "register client / bind hostname to client ID" call in the spec.

**Auth for the SvelteKit backend to call this API:** per `docs/guide/api-auth.md` (verbatim):
```
Authorization: Basic ***     (admin, e.g. `curl -u admin:adminpass https://server/api/users`)
Authorization: Bearer ***    (per-user API token, e.g. `curl -H "Authorization: Bearer ***" https://server/api/listeners`)
```
Admin-only routes (users, sessions, port-reservations) require Basic auth as the `--auth` admin user (or a DB user with `is_admin=true`); user routes accept an API token issued via `POST /api/users/{name}/tokens`. For a SvelteKit backend acting as the control-plane owner, the practical setup is: keep the server's `--auth admin:PASSWORD` (or a DB admin account) credential in SvelteKit's server-side secrets, and use Basic auth for admin operations (create/list users, port reservations), or mint a long-lived admin API token via the dashboard/`/api/users/{admin}/tokens` and use `Authorization: Bearer`.

## Client Linking Flow (access token exchange)

Traced directly from `client/client_connect.go`, `client/config.go`, and `main.go`'s `client()` function — **there is no pairing/QR flow**. It is a **pre-shared username:password credential**, not a token issued through an API call:

1. An admin (or the SvelteKit backend via `POST /api/users` as admin) creates a chiSSL user account: `{"username": "...", "password": "...", "is_admin": false}`. This is a normal user/password pair stored in chiSSL's DB (bcrypt or similar, per `database.User`), not a rotating token.
2. The new local machine's client binary is invoked with that credential, either:
   - CLI: `chissl client --auth user:pass https://tunnel.your.domain "8080->80"` (`main.go`: `flags.StringVar(&config.Auth, "auth", "", "")`, falls back to `AUTH` env var), or
   - YAML profile at `$HOME/.chissl/profile.yaml` (`client/config.go: NewClientConfig`) containing `server:`, `auth: "user:pass"`, `remotes: [...]`.
3. On start, `client_connect.go: connectionOnce()` opens a WebSocket to the server (`d.DialContext(ctx, c.server, ...)`, subprotocol `chshare.ProtocolVersion`), then performs an **SSH handshake over that WebSocket connection** (`ssh.NewClientConn(conn, "", c.sshConfig)`) — the username/password is passed as SSH client auth credentials (chiSSL is built on chisel, which layers SSH auth over the WS tunnel). If auth fails the log shows "Authentication failed" and the connection is dropped.
4. After the SSH handshake succeeds, the client sends a `"config"` SSH request (`sshConn.SendRequest("config", true, settings.EncodeConfig(c.computed))`) describing the requested `remotes` (port mappings). The server validates the requested remote-port mapping against the user's permitted address regexes (from `--authfile` or DB) and either errors (`configerr`) or proceeds.
5. Once accepted, `c.tunnel.BindSSH(...)` binds the SSH connection for tunnel use and blocks — this is the live tunnel. The `connectionLoop()` wrapper auto-reconnects with exponential backoff (`jpillora/backoff`) on drop, up to `--max-retry-count` (default unlimited) with `--max-retry-interval`.
6. There's also `--fingerprint` for host-key pinning (client verifies server's SSH host key fingerprint to prevent MITM) — optional but recommended, separate from the user auth step.

**Bottom line:** "first-time linking" = admin creates a chiSSL user (username/password) once via dashboard or `POST /api/users`, distributes that credential to the local machine (e.g. baked into aipotluck-local-client's config or env var `AUTH`), and the client authenticates with it on every connect — it is a static shared secret, not a one-time pairing exchange or short-lived provisioning token. Per-user **API tokens** (`Bearer` tokens from `/api/users/{name}/tokens`) are a *separate* mechanism used only for calling the REST API, not for the client's SSH/tunnel auth.

## Request Routing (public traffic → local client)

- Routing is **port-based on the server side**, not subdomain-based by default. A client mapping like `"8080->80"` means: chiSSL server opens/uses port **8080** on itself, and any TCP/HTTP traffic hitting that port is tunneled through the SSH connection down to port 80 on the client's local machine. Public callers hit `https://tunnel.your.domain:8080` (or whatever port) directly — there's no automatic subdomain per tunnel.
- Optional per-mapping virtual host: the format supports `local-port->remote-port:remote-host`, e.g. `"8443->443:myapp.local"`, which sets the **remote** (client-side) target host — this is about which local service the client forwards to, not about which public hostname routes to it.
- **Listeners** (`mode: proxy`) are the feature that provides host/target-based HTTP routing/rewriting on the server: `POST /api/listeners {"name","mode":"proxy","port","target_url","use_tls"}` — this makes the server itself reverse-proxy public HTTP traffic on `port` to `target_url`. But `target_url` must already be reachable from the server (e.g., the *tunnel's* server-side port from a connected client, like `http://127.0.0.1:8080` if that's where the tunnel landed) — it is not "associate hostname with client ID" in one step; you'd chain a tunnel (client connects, claims port 8080 on server) + a listener (proxies from a friendlier port/host/TLS to `http://127.0.0.1:8080`).
- There is **no evidence in the source of subdomain-per-tunnel routing** (no vhost/SNI dispatch keyed by tunnel ID was found in `server_handler.go` or `listener_manager.go`); the model is "each tunnel/listener claims a distinct TCP port on the server." For a multi-client aipotluck-local-client fleet, the SvelteKit backend would need to track a **port-to-client mapping** itself (e.g. via `--authfile` regex restricting which remote-ports each chiSSL user may claim, or via `POST /api/port-reservations` to reserve a port range per username) and route/proxy by port, not by subdomain path. Getting a clean "one route = one client" story likely requires layering your own reverse proxy (e.g. Caddy/Traefik doing SNI/Host-based routing) in front of chiSSL's per-client ports, since chiSSL itself doesn't do that dispatch.

## Status Polling / Health Checks

- **Server-wide liveness**: `GET /health` → plain text `OK` (confirmed in `server/server_handler.go` line 58-60: `case strings.HasPrefix(path, "/health"): w.Write([]byte("OK\n"))`). This is what `server_installer.sh` itself curls post-install to verify the service started. No auth required.
- **Per-tunnel status**: `GET /api/tunnels` and `GET /api/tunnels/{id}` return tunnel objects with a `status` field (schema `Tunnel { id, username, connected_at, status }` per the OpenAPI spec) and `GET /api/tunnels/active` (`handleGetActiveTunnels` in `server_api.go`) returns only tunnels with `status == "active"`. Polling `/api/tunnels/{id}` (with the owning user's Bearer token or admin Basic auth) is the concrete way for SvelteKit/aipotluck-local-client's monitor to check "is this specific client connected."
- **Aggregate stats**: `GET /api/stats` returns `{total_tunnels, active_tunnels}` (plus, per `handleGetStats` in `server_api.go`, `total_listeners/active_listeners/total_users/active_sessions/uptime_seconds` when a DB is configured) — useful for a dashboard-level health check but not per-client.
- **Sessions** (`GET /api/sessions`, admin only) gives lower-level SSH session info (`s.sessions.Len()` when no DB), which is closer to "is a connection alive right now" than the tunnel table (which may lag on cleanup); the code comments in `server_api.go` note DB-backed sessions are "not implemented yet" in this snapshot, so `/api/sessions` may be less reliable than `/api/tunnels/{id}`.status for now.
- No CLI subcommand for status polling exists client-side (`chissl client --help` shows connect-only flags, no `chissl status`); polling must go through the server's REST API, not the client binary.

## Open Questions / Assumptions

1. **Q2 (control-plane API) — partially open**: the *published* OpenAPI spec (`openapi-public.yaml`) is intentionally minimal ("Public endpoints..."); the actual server exposes more routes (per-user tokens, port reservations, capture streaming, AI-mock) found only by reading `server_handler.go`'s switch statement, not documented in any single reference doc. Treat the OpenAPI spec as a *subset*, and expect to grep source for exact request/response shapes of the undocumented endpoints before wiring SvelteKit against them.
2. **Q4 (routing) — the most uncertain answer**: I could not find any subdomain- or Host-header-based dispatch to a specific tunnel/client in the source. The system fundamentally allocates **ports**, not hostnames/paths, per tunnel. If aipotluck-local-client's design assumes "SvelteKit calls an API to bind `client-123.aipotluck.app` → client 123," chiSSL as shipped does not support that natively — you'd need an extra reverse-proxy layer (Caddy/Traefik/nginx) in front of chiSSL doing SNI/Host routing to per-client ports, with the SvelteKit backend maintaining the port↔client mapping itself (e.g. via reserved port ranges). This needs validation with the chiSSL maintainers or by testing `--authfile` regex + reservations end-to-end before committing to chiSSL for this use case.
3. **Q1 (Docker)**: no Dockerfile/compose file exists in this repo despite the survey doc's mention of "self-hostable" — Docker packaging would be homegrown (wrap the downloaded binary + entrypoint yourself), not an out-of-the-box chiSSL artifact. Flagging this as a gap versus the original survey's characterization.
4. **Redoc API reference page** (`https://unblocked.github.io/chissl/api/`) renders client-side from the same `openapi-public.yaml` already inlined above — fetching it via `web_extract` returned only "Loading..." (JS-rendered), so no additional endpoints beyond the YAML were recoverable from that page; the YAML was the authoritative source used here.
5. **Q3 (client linking)** is answered with high confidence directly from source (`client_connect.go`, `main.go`), but I did not build the binary to confirm CLI `--help` output verbatim beyond what's embedded as Go string literals in `main.go` (`clientHelp`/`serverHelp` constants) — those constants were read directly and quoted above, so this should be accurate without a build step. Go toolchain build was not attempted in this pass (not required since help text is compile-time constant text visible in source).
6. **Per-user account provisioning cost**: creating one chiSSL "user" per local aipotluck-local-client install (as implied by the linking flow) means the SvelteKit backend must call `POST /api/users` as admin for every new machine and manage passwords/tokens per machine — this is a real operational scaling implication worth validating against the other 3 finalist tools before final selection.
