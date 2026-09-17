# boringproxy Implementation Plan

**Source verified against:** `git clone https://github.com/boringproxy/boringproxy` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/boringproxy` (master branch, commit as of clone
time). Files cited: `README.md`, `boringproxy.go`, `api.go`, `auth.go`, `client.go`, `database.go`,
`tunnel_manager.go`, `cmd/boringproxy/main.go`, `docker-compose.yml`, `docker/server/README.md`,
`docker/client/*`, `systemd/boringproxy-server.service`, `systemd/boringproxy-client.service`,
`systemd/README.md`, `docs/systemd.md`. boringproxy is a single Go binary with `server` and
`client` subcommands (`cmd/boringproxy/main.go` lines 42-149) plus an embedded HTTP JSON API
(`api.go`) that both the bundled web UI (`ui_handler.go`) and the `client` subcommand
(`client.go`) consume — this confirms the prior research claim that a real REST API exists behind
the UI.

---

## Installation & Global VPS Configuration

### Binary / source build
No prebuilt release binary was found in-repo (releases are cut via `.github/workflows/release.yml`
+ `.goreleaser.yml`, downloadable from GitHub Releases). Build-from-source steps
(`README.md` lines 29-68):
```bash
git clone https://github.com/boringproxy/boringproxy
cd boringproxy
./install_go.sh && source $HOME/.bashrc     # only if Go isn't already installed
./scripts/generate_logo.sh                   # bakes a PNG favicon into the binary at build time
cd cmd/boringproxy
go build -ldflags "-X main.Version=$(git describe --tags)"
sudo setcap cap_net_bind_service=+ep boringproxy   # allow binding 80/443 as non-root
```

### Docker
`docker-compose.yml` (repo root, 8 lines) builds from the local Dockerfile and publishes 80/443:
```yaml
services:
  boringproxy:
    build: ./
    image: boringproxy
    ports:
      - "80:80"
      - "443:443"
```
`docker/server/README.md` documents two compose overlay files for the server specifically:
```bash
docker-compose -f docker-compose.yml -f docker/server/source.yml up -d      # build from source
docker-compose -f docker-compose.yml -f docker/server/prebuild.yml up -d    # pull prebuilt image
```
(edit the `-admin-domain`/`-acme-email` command args in the compose file first). A parallel
`docker/client/{source,prebuild}.yml` pair exists for running the **client** side in Docker too
(`docker/client/README.md`), including example integrations for Home Assistant and nginx
(`docker/client/examples/`).

### Server CLI flags / config
There is **no YAML/JSON config file for server startup flags** — everything is a CLI flag parsed
via stdlib `flag` in `cmd/boringproxy/main.go` → `boringproxy.Listen()` (`boringproxy.go` lines
45-64):
- `-admin-domain` (required on first run) — the hostname the admin web UI/API is served from.
- `-ssh-server-port` (default `22`) — the SSH port clients tunnel through.
- `-db-dir` (default `""` → CWD) — directory containing `boringproxy_db.json` (see Database below).
- `-cert-dir` — TLS cert storage dir for CertMagic (Let's Encrypt client library).
- `-http-port` (default `80`), `-https-port` (default `443`) — the two required listeners; if
  either is non-default, **automatic Let's Encrypt cert management is silently disabled**
  (`boringproxy.go` lines 96-100: "WARNING: LetsEncrypt only supports HTTP/HTTPS ports 80/443...
  Disabling automatic certificate management").
- `-allow-http` — serve plaintext HTTP instead of a 301 redirect to HTTPS.
- `-acme-email`, `-acme-use-staging`, `-accept-ca-terms`, `-acme-certificate-authority` — standard
  ACME/Let's Encrypt bootstrap flags (`boringproxy.go` lines 57-60, 108-123); `-accept-ca-terms` is
  what lets Docker Compose run non-interactively.
- `-public-ip`, `-behind-proxy`, `-print-login` (prints/QR-codes the admin login URL).

### One-time global config (DB init, TLS, admin account)
- **Database**: `NewDatabase(dbDir)` (`database.go` lines 65-106) reads/creates a single flat JSON
  file, `<dbDir>/boringproxy_db.json`, containing `admin_domain`, `tokens` (map of token→
  `{owner, client}`), `tunnels` (map of domain→`Tunnel`), and `users` (map of username→
  `{is_admin, clients}`). This is the **entire persistence layer** — no external DB required, just
  a JSON file the process owns exclusively (guarded by an in-process `sync.Mutex`, not
  multi-process safe).
- **Admin domain**: if not passed via `-admin-domain` and not already stored in the DB, the server
  prompts interactively on first run (`setAdminDomain`, `boringproxy.go` lines 404-436) — either
  manual entry or a TakingNames.io-brokered DNS bootstrap flow (QR code + OAuth-like link). For a
  headless/systemd deploy you must pass `-admin-domain` on the very first `ExecStart` so it never
  hits this interactive prompt — confirmed by `docs/systemd.md` "Manual start (once off only)"
  section explicitly instructing operators to run the binary manually once first to answer the
  email prompt, then switch to the systemd unit.
- **TLS**: automatic via `github.com/caddyserver/certmagic` (`boringproxy.go` line 125,
  `certmagic.NewDefault()`); `certConfig.ManageSync(ctx, []string{adminDomain})` is called at
  startup (line 141) to fetch/renew the admin domain's cert, and again per-tunnel-domain inside
  `TunnelManager` (see Routing section) whenever `TlsTermination` is `"server"`/`"server-tls"`
  (`tunnel_manager.go` lines 36-46, 66-73).
- **Admin account bootstrap**: fully automatic and unconditional — `boringproxy.go` lines 149-158:
  ```go
  users := db.GetUsers()
  if len(users) == 0 {
      db.AddUser("admin", true)
      _, err := db.AddToken("admin", "")
      ...
  }
  ```
  On the very first run with an empty DB, boringproxy creates a user literally named `admin`
  (`IsAdmin: true`) and mints a bearer token scoped to that user with no client restriction
  (`TokenData{Owner: "admin", Client: ""}`). With `-print-login`, this token is printed as a full
  login URL + terminal QR code (`printLoginInfo`, lines 445-454): `https://<admin-domain>/login?access_token=<token>`.
  **This token is the master credential for the entire instance** — whoever holds it can create
  users, tunnels, tokens, and clients via the API (see next section).

### Ports
- `80` — plaintext HTTP (redirects to HTTPS unless `-allow-http`); also used for ACME HTTP
  challenges implicitly via CertMagic defaults.
- `443` — HTTPS listener for both the admin UI/API (on `-admin-domain`) and every tunneled domain
  (SNI-multiplexed — see Routing section); boringproxy runs its own raw TCP listener + manual TLS
  ClientHello peeking (`boringproxy.go` lines 332-374, `handleConnection`/`peekClientHello`) rather
  than a plain `http.ListenAndServeTLS`, specifically so it can support TLS-passthrough tunnels.
- `22` (configurable via `-ssh-server-port`) — **the host's own OpenSSH server**, not a bundled SSH
  daemon; boringproxy authorizes tunnels by writing restricted entries into the **operating-system
  user's real `~/.ssh/authorized_keys` file** (see Routing/Client-Linking sections) — this is a
  significant operational fact: the server process must run as an OS user with a real home
  directory and SSH access, and `docs/systemd.md`/`systemd/README.md` explicitly call out creating
  `~/.ssh` for that user beforehand ("BoringProxy assumes the folder already exists... program will
  fail to add tunnels" — `systemd/README.md` lines 39-43).

### systemd wrapping (shipped by upstream)
`systemd/boringproxy-server.service`:
```ini
[Service]
User=boringproxy
Group=boringproxy
WorkingDirectory=/home/boringproxy/
ExecStart=/usr/local/bin/boringproxy server -admin-domain bp.example.com
[Install]
WantedBy=multi-user.target
```
`systemd/boringproxy-client.service`:
```ini
[Service]
User=boringproxy
WorkingDirectory=/home/boringproxy/
ExecStart=/usr/local/bin/boringproxy client -server bp.example.com -token your-bp-server-token -acme-email your-email-address
[Install]
WantedBy=multi-user.target
```
`docs/systemd.md` additionally documents a **templated** client unit
(`boringproxy-client@.service`) so multiple named client instances can run on one machine, enabled
per-name via `systemctl enable --now boringproxy-client@default.service` (the `%i`/`%I` argument
becomes the `-client-name`). Both docs stress running as a dedicated non-root `boringproxy` system
user (`useradd --system --shell /bin/false boringproxy`), with `~/.ssh` pre-created and the DB file
(`boringproxy_db.json`) chowned to that user if migrating from a root-run instance.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ boringproxy server)

**Confirmed: `api.go` implements a real REST-ish JSON/form API**, mounted under `/api/` on the
admin domain (`boringproxy.go` line 292-293: `if strings.HasPrefix(r.URL.Path, "/api/") {
http.StripPrefix("/api", api).ServeHTTP(w, r) }`). Four resource groups, each requiring a bearer
`access_token` (`extractToken("access_token", r)`, `utils.go` — accepted as either an
`Authorization: bearer <token>` header, per `client.go` line 134, or presumably a query
param/form value given the shared `extractToken` helper name):

- **`GET/POST/DELETE /api/tunnels`** (`api.go` `handleTunnels`, lines 40-134):
  - `GET /api/tunnels[?client-name=X]` — list tunnels visible to the token's owner (admins see
    all, non-admins see only their own; a client-scoped token is further filtered to only that
    client's tunnels, lines 62-88). Response is JSON `map[string]Tunnel`, and includes an `ETag`
    response header (MD5 hash of the body, lines 97-100) used by the polling client for
    change-detection.
  - `POST /api/tunnels` (form-encoded body) — **this is the "register a tunnel/service"
    call**. `CreateTunnel` (`api.go` lines 317-424) accepts form params: `domain` (required),
    `owner` (required — only admins may set an owner different from themselves, line 330-335),
    `client-name`, `client-port`, `client-addr` (default `127.0.0.1`), `tunnel-port` (or omitted/
    `"Random"` for server-assigned), `allow-external-tcp` (`"on"`), `password-protect` (`"on"`,
    with `username`/`password`), `tls-termination` (must be one of `server`, `client`,
    `passthrough`, `client-tls`, `server-tls`), `ssh-server-addr`/`ssh-server-port` (override
    defaults). Delegates to `TunnelManager.RequestCreateTunnel` (see Routing section) which
    allocates the port, writes an `authorized_keys` entry, and generates a fresh SSH keypair for
    the tunnel.
  - `DELETE /api/tunnels` (form `domain`) — tears down a tunnel (owner or admin only).
- **`GET/POST /api/users`** (`handleUsers`, lines 136-175) — admin-only in practice
  (`CreateUser` line 542-564 hard-requires `user.IsAdmin`). `POST` body: `username` (≥6 chars),
  `is-admin=on`. This is how SvelteKit would provision a new logical "account" if we wanted
  multi-tenant separation (not strictly required for our single-operator use case, but the primary
  admin token can be reused for everything since it's already `IsAdmin: true`).
- **`GET/POST /api/tokens`** (`handleTokens`, lines 177-215) — **this is the "issue a per-client
  token" call**. `POST /api/tokens` (form `owner`, `client`): creates a new bearer token scoped to
  `owner` and, if `client != "any"`, restricted to that one client name (`CreateToken`, lines
  450-487; the referenced client must already exist under that owner's `Clients` map — see next
  bullet). Non-admins may only mint tokens for themselves. Response body is the raw token string
  (`io.WriteString(w, token)`, line 210) — not JSON-wrapped.
- **`POST/DELETE /api/clients`** (`handleClients`, lines 217-275) — registers/deregisters a
  **client name** under a user (`SetClient`/`DeleteClient`, lines 594-625, which just insert/delete
  a key in `User.Clients map[string]DbClient{}` — `DbClient` is presently an empty struct, i.e.
  this is purely a name registry, not connection state). This is also the exact call the
  `boringproxy client` binary itself makes on every startup (`client.go` `Run()`, lines 122-150):
  ```
  POST https://<server>/api/clients/?client-name=<name>[&user=<user>]
  Authorization: bearer <token>
  ```
  confirming `client-name` registration is idempotent/self-service as long as the caller's token is
  valid for that user.

**Auth model for SvelteKit**: a single **bearer access token** per call
(`Authorization: bearer <token>`, `client.go` line 134), validated by `Auth.Authorized`/
`db.GetTokenData` (`auth.go` lines 25-33) — tokens are flat 32-byte random strings
(`genRandomCode(32)`, `database.go` line 159) stored server-side with no expiry field and no
scope beyond `{Owner, Client}`. **There is no OAuth, no JWT, no session cookie required for
API calls** — SvelteKit just needs one long-lived admin (or per-user) token stored as a secret and
sent as a bearer header on every API request. The bundled web UI additionally supports a cookie-
based login (`/login?access_token=...` sets a session, `ui_handler.go`), but that's irrelevant to a
server-to-server SvelteKit integration, which should use the raw `/api/*` endpoints directly with
the bearer header exactly as the `client` binary does.

---

## Client Linking Flow (access token exchange)

This is the cleanest of the two tools for our exact scenario — boringproxy's own `client`
subcommand **is** the token-exchange flow we want to replicate:

1. **Admin issues a token** ahead of time via the API or web UI: `POST /api/tokens` with
   `owner=<username>&client=any` (or a specific pre-registered client name) using the admin's own
   bearer token. Response body is the new token string (`api.go` line 210). This is exactly the
   `-token fKFIjefKDFLEFijKDFJKELJF` value shown in `README.md` line 81's client example. In
   practice for our flow, SvelteKit's backend does this server-side and hands the resulting token
   to the local-client installer (e.g. embedded in a one-time setup command/QR/config download) —
   this **is** our "access token exchange."
2. **New machine registers itself as a client** by starting the boringproxy client binary with
   that token:
   ```bash
   ./boringproxy client -server bp.example.com -token <token> -client-name my-new-machine -user demo-user
   ```
   (`README.md` line 81; flags fully enumerated in `cmd/boringproxy/main.go` lines 88-99). On
   startup, `Client.Run()` (`client.go` lines 122-150) immediately calls
   `POST /api/clients/?client-name=my-new-machine&user=demo-user` with `Authorization: bearer
   <token>` — this **is** the self-registration/pairing call; if it 200s, the client-name now exists
   under that user (`SetClient`, `api.go` line 259, `database.go` User.Clients map) and the token
   remains valid for that scope going forward. If the client subcommand is invoked with a token
   that is **not** already client-scoped (`TokenData.Client == ""`), `handleClients` (`api.go` lines
   235-244) lets it self-assign the `client-name` on first call — i.e. **the very first connection
   with a fresh admin-issued "any-client" token is what actually creates the client identity**, no
   separate manual admin step is required to pre-declare the client name.
3. **No local pre-shared config file is required beyond the CLI flags/token** — boringproxy's
   client has no persistent local state file of its own (no `~/.config/boringproxy/client.json`
   found in source); the token, server address, client name, and user are supplied fresh each
   process start (via CLI flags or, for our integration, wrapped in the aipotluck-local-client
   monitor service's own config that stores these 4 values after the one-time link step). The
   `ClientConfig` struct (`client.go` lines 35-47) is exactly this: `ServerAddr`, `Token`,
   `ClientName`, `User`, plus TLS/DNS/behind-proxy tuning knobs — this is precisely the shape our
   monitor service should persist locally after linking.
4. **After registration, the client immediately begins polling** `GET
   /api/tunnels?client-name=<name>` on an interval (`PollInterval`, default `2000`ms, CLI flag
   `-poll-interval-ms`, `main.go` line 99) using the same bearer token, comparing the response's
   `ETag` header to detect changes (`client.go` lines 180-224) and spinning up/tearing down SSH
   tunnels (`BoreTunnel`) to match whatever `Tunnel` records the server currently holds for it. This
   means **tunnel creation is server/SvelteKit-driven**: SvelteKit calls `POST /api/tunnels` (as
   admin) with `client-name=<the linked device>`, and the already-connected/polling client daemon
   picks it up on its next poll and dials out — no additional handshake beyond the original token.
5. **Revocation**: `DELETE /api/tokens` (not shown fully wired to an HTTP verb in `handleTokens`'s
   switch, but `Api.DeleteToken` exists at `api.go` lines 489-511 and is reachable — needs
   confirming which route wires it, likely intended as `DELETE /api/tokens?token=<t>`) removes the
   token from `db.Tokens`; the next `PollTunnels`/`SetClient` call from that device will 403 (token
   no longer resolves in `GetTokenData`). There is no live "kick" of an already-open SSH tunnel
   process analogous to sish's disconnect endpoint — revocation only prevents *future* API calls
   and future tunnel creation; an already-dialed SSH connection using a boringproxy-generated
   keypair (see Routing section) would need the corresponding `authorized_keys` line removed via
   `DELETE /api/tunnels` (which does call `TunnelManager.DeleteTunnel` → strips the
   `authorized_keys` entry, `tunnel_manager.go` lines 110-152) to actually cut off connectivity.

---

## Request Routing (public traffic → local client)

Routing is **domain-based** (one full domain/subdomain per tunnel, not path-based), and — critically
— **each tunnel gets its own dedicated SSH keypair and remote-forward port, generated and managed
entirely by the boringproxy server itself**, not by the operator's own SSH keys:

1. When SvelteKit calls `POST /api/tunnels` (domain, client-name, client-port, etc.),
   `TunnelManager.RequestCreateTunnel` (`tunnel_manager.go` lines 56-108):
   - Allocates a `TunnelPort` if not explicitly given (`randomOpenPort()`, line 80).
   - Calls `addToAuthorizedKeys(domain, port, allowExternalTcp)` (lines 164-218), which **generates
     a brand-new 1024-bit RSA keypair** (`MakeSSHKeyPair`, lines 224-247) and appends a **restricted**
     line to the boringproxy OS user's `~/.ssh/authorized_keys`:
     ```
     command="echo This key permits tunnels only",permitopen="fakehost:1",permitlisten="<bindAddr>:<port>" <pubkey> boringproxy-<domain>-<port>
     ```
     (`tunnel_manager.go` line 196-200) — the `permitlisten` OpenSSH option is what restricts this
     specific keypair to *only* opening a remote listener on that one port, nothing else; `command=`
     neuters interactive shell access entirely. This is a materially different (and more scoped)
     security model than sish or plain SirTunnel, which reuse the operator's own general-purpose
     SSH keys/server auth.
   - Stores the tunnel record (including the **private key**, `TunnelPrivateKey`) in the DB, keyed
     by `domain` (`db.SetTunnel`).
2. The **client** receives this full `Tunnel` object (including the private key!) via its
   `GET /api/tunnels?client-name=...` poll response, parses the private key
   (`ssh.ParsePrivateKey`, `client.go` line 283), dials the SSH server itself
   (`ssh.Dial("tcp", "<ServerAddress>:<ServerPort>", ...)`, line 300), and opens a remote listener
   with `client.Listen("tcp", "<bindAddr>:<TunnelPort>")` (line 311) — i.e. **the client, not the
   admin's shell, drives the actual SSH remote-forward**, using a key the server handed it over the
   REST API, not a key it generated for itself. `TlsTermination` mode (`server`/`client`/
   `passthrough`/`client-tls`/`server-tls`) determines whether the client or server terminates
   TLS and where certs are managed (CertMagic on whichever side terminates).
3. **On the public-traffic side**, the main HTTP/TLS listener (`boringproxy.go`
   `handleConnection`/the `http.HandleFunc("/", ...)` block, lines 201-374) dispatches by
   `Host` header (or SNI `ServerName` for passthrough modes):
   - If `hostDomain == db.GetAdminDomain()` → routed to `/api/*` or the web UI (line 291-296).
   - Else, `db.GetTunnel(hostDomain)` looks up the `Tunnel` record for that exact domain, and
     `proxyRequest(w, r, tunnel, httpClient, "localhost", tunnel.TunnelPort, behindProxy)` (line
     307) reverse-proxies to `127.0.0.1:<TunnelPort>` **on the boringproxy server itself** — which
     is the SSH remote-forward endpoint the client is listening on. TLS-passthrough/client-tls
     tunnels instead go through `handleConnection`'s raw-TCP `peekClientHello`/SNI-routing path
     (lines 350-374) straight to the tunnel port without terminating TLS on the server at all.
4. **So yes — SvelteKit must call `POST /api/tunnels` to bind a hostname to a client**: the
   `domain` and `client-name` params in that call are exactly the "bind hostname to client ID"
   operation the task asked about. There is no separate DNS-registration step needed beyond
   ensuring `domain` already resolves (via A/AAAA record, or boringproxy's optional TakingNames.io
   integration, `namedrop.Client` in `boringproxy.go`, which is out of scope for a fixed-domain
   operator setup).
5. **For our use case**: SvelteKit would call `POST /api/tunnels` with
   `domain=device-<id>.tunnels.ourapp.com&owner=<user>&client-name=<linked-client-name>&client-port=<llama-cpp-port>&client-addr=127.0.0.1&tls-termination=server`
   once at link time (or whenever the local llama-server port is (re)configured); the already-
   polling client picks it up automatically and dials out — no manual per-tunnel SSH command needed
   on the client side at all, unlike SirTunnel/sish.

---

## Status Polling / Health Checks

There is **no dedicated "is client X connected" status field** in the DB/API responses examined —
`Tunnel` and `TokenData`/`User` structs carry no `connected`/`last_seen`/`online` field
(`database.go` lines 24-63). The practical signals available:

1. **Indirect via `GET /api/tunnels?client-name=<name>`**: this tells you a tunnel record *exists*
   for that client and its full config (domain, ports, tls mode), but not whether the client
   process is actually currently online or has successfully dialed the SSH connection — it's
   configuration state, not connection state. The `ETag` header exists purely for the client's own
   change-detection polling optimization (`api.go` lines 97-100), not as a health signal for
   external consumers.
2. **No admin API for listing active SSH sessions** (unlike sish's `/_sish/api/clients`) —
   boringproxy's SSH usage is just the standard OS `sshd`, so the *only* way to see active
   connections from the OS level would be inspecting `sshd`'s own process list / `who`/`ss`
   output on the VPS, which is not something boringproxy exposes over HTTP at all.
3. **Practical recommendation, same as the other tools researched**: do our own **end-to-end HTTP
   health probe** of the tunnel's public domain (`https://device-<id>.tunnels.ourapp.com/health`)
   from SvelteKit on an interval — a reachable 200 proves the full path (public HTTPS → boringproxy
   → SSH remote-forward → local llama.cpp) is healthy; a 502/connection-refused indicates either the
   local client process is down, the SSH tunnel dropped, or llama.cpp itself is unresponsive. This
   is the *only* reliable path-verified signal boringproxy's architecture supports without extra
   glue.
4. We could optionally **build our own heartbeat** by having the local-client daemon call a
   SvelteKit-side `/api/heartbeat` endpoint on the same interval it already polls
   `/api/tunnels` (`-poll-interval-ms`, default 2000ms) — this would require patching/wrapping the
   `boringproxy client` binary (or replicating its polling loop in our own Go/other-language SSH
   client, since `client.go`'s logic is compact and copyable) to also emit a liveness ping,
   something upstream does not do today.
5. Server logs (`log.Println("SyncTunnels")`, `client.go` line 227, and various `log.Print` calls
   in `boringproxy.go`) are stdout-only, consumable via `journalctl -u boringproxy-client` under
   the shipped systemd units, but not structured/API-accessible — useful for manual troubleshooting
   only.

---

## Open Questions / Assumptions

- **`extractToken` implementation not located**: referenced throughout `api.go`/`client.go`
  (`extractToken("access_token", r)`) but its definition wasn't found in the files read (likely in
  `utils.go`, which exists in the repo listing but wasn't opened) — need to confirm whether it also
  accepts the token as a query/form param (`?access_token=`) in addition to the `Authorization:
  bearer` header confirmed in `client.go` line 134, since the web UI's `/login?access_token=...`
  pattern strongly implies query-param support too. This affects exactly how SvelteKit should send
  the token on API calls.
- **`DELETE /api/tokens` routing**: `Api.DeleteToken` exists (`api.go` lines 489-511) but
  `handleTokens`'s method switch (lines 198-215) only wires `GET`/`POST`, not `DELETE` — need to
  verify (by testing against a running instance, or reading `utils.go`/router setup more closely)
  whether token deletion is actually reachable over HTTP or only used internally (e.g. via
  `DeleteUser`'s cascade at `api.go` lines 585-589). If unreachable, revoking a single token without
  deleting the whole user may require direct DB-file editing — a meaningful gap for our
  "revoke one device" requirement.
- **1024-bit RSA tunnel keys**: `MakeSSHKeyPair` (`tunnel_manager.go` line 225) hardcodes
  `rsa.GenerateKey(rand.Reader, 1024)` — a notably weak key size by modern standards (RSA-1024 is
  considered breakable and deprecated by NIST). Worth checking the CHANGELOG/issue tracker for
  whether this was since strengthened in a later release, or budgeting to patch it ourselves if we
  self-host from source.
- **DB is a single flat JSON file with in-process-only locking** (`database.go` `sync.Mutex`) — not
  safe for multiple boringproxy server processes/replicas; only one server instance can safely run
  per DB file. Fine for our single-VPS scenario but worth flagging as a scaling ceiling.
- **No confirmed rate limiting / brute-force protection** on the token-protected API endpoints —
  not examined in `utils.go` (not read); given tokens are the sole auth factor and have no
  expiry, treat them with the same operational care as API keys (rotate on suspected leak via the
  token endpoints, assuming DELETE is reachable per the open question above).
- **TakingNames.io / namedrop integration** (`boringproxy.go`'s `namedropClient`) is an optional
  DNS-automation feature we do not need for a fixed-operator-domain setup and was not investigated
  in depth — safe to ignore by always supplying `-admin-domain` explicitly and pre-provisioning our
  own DNS records for tunnel subdomains.
- **Confidently answered**: all 5 questions have concrete, source-verified answers — this tool
  genuinely ships the token/API/routing model our architecture needs, unlike SirTunnel. The main
  residual risk is the two "open questions" above (exact token-extraction surface, and whether
  token deletion is live) rather than any structural gap in boringproxy's design.

---

## Summary of custom glue code required

Relative to SirTunnel and sish, boringproxy requires the **least** amount of custom glue: it
already ships (a) a REST API for creating tunnels/tokens/clients (`api.go`), (b) a token-based
self-registration flow that its own `client` binary performs out of the box (`client.go`
`Run()`), (c) server-generated, per-tunnel-scoped SSH keys with `permitlisten`-restricted
`authorized_keys` entries (`tunnel_manager.go`) so we never need our own SSH-key-provisioning
logic, and (d) domain-based routing driven directly by the same `POST /api/tunnels` call. The
remaining work for our SvelteKit integration is thin: (1) wrap `/api/tokens` + `/api/clients` +
`/api/tunnels` calls behind our own onboarding UX (mapping our own concept of "device link" onto
boringproxy's `client-name`/`token`/`tunnel` records), and (2) build our own end-to-end HTTP health
probe, since boringproxy has no connection-status API of its own.
