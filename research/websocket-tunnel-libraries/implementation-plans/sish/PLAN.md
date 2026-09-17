# sish Implementation Plan

**Source verified against:** `git clone https://github.com/antoniomika/sish` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/sish` (main branch, commit as of clone time).
Files cited below are all relative to that repo root unless a full URL is given.

Key upstream docs used: `README.md`, `docs/posts/getting-started.md`, `docs/posts/cli.md`,
`docs/posts/advanced.md`, `docs/posts/forwarding-types.md`, `docs/posts/how-it-works.md`,
`docs/posts/cheatsheet.md`, `docs/posts/faq.md`; hosted at https://docs.ssi.sh/.
Source used: `utils/utils.go`, `utils/console.go`, `utils/authentication_key_request_test.go`,
`sshmuxer/httphandler.go`, `cmd/sish.go`, `config.example.yml`.

---

## Installation & Global VPS Configuration

sish is a single Go binary (`main.go` / built via `cmd/sish.go` with `spf13/cobra` + `spf13/viper`
for flags/config). Three install paths, all documented:

1. **Docker (single container)** — README.md lines 39-77 and `docs/posts/getting-started.md` lines 78-123:
   ```bash
   docker pull antoniomika/sish:latest
   mkdir -p ~/sish/ssl ~/sish/keys ~/sish/pubkeys
   cp ~/.ssh/id_ed25519.pub ~/sish/pubkeys
   docker run -itd --name sish \
     -v ~/sish/ssl:/ssl -v ~/sish/keys:/keys -v ~/sish/pubkeys:/pubkeys \
     --net=host antoniomika/sish:latest \
     --ssh-address=:2222 --http-address=:80 --https-address=:443 \
     --https=true --https-certificate-directory=/ssl \
     --authentication-keys-directory=/pubkeys \
     --private-keys-directory=/keys \
     --bind-random-ports=false --domain=example.com
   ```
   Docker Hub image name confirmed: `antoniomika/sish` (https://hub.docker.com/r/antoniomika/sish/tags).

2. **Docker Compose** — `docs/posts/getting-started.md` lines 26-70, using the repo's own
   `sish/deploy` folder (referenced at
   `https://github.com/antoniomika/sish/tree/main/deploy`), which bundles
   `adferrand/dnsrobocert` for automated wildcard Let's Encrypt DNS-01 certs. Workflow: clone repo,
   `cp -R sish/deploy ~/sish`, edit `docker-compose.yml`/`le-config.yml` with domain + DNS provider
   auth, symlink `/etc/letsencrypt/live/<domain>/{fullchain,privkey}.pem` into `deploy/ssl/<domain>.{crt,key}`,
   then `docker-compose -f deploy/docker-compose.yml up -d`.

3. **Build from source** — README.md "Local Development" section:
   ```bash
   git clone git@github.com:antoniomika/sish.git && cd sish
   go run main.go --http-address localhost:3000 --domain testing.ssi.sh
   # or: make dev
   ```
   For a real VPS deploy this becomes `go build -o sish main.go` (or use a released binary from
   https://github.com/antoniomika/sish/releases per FAQ "Where can I find latest releases?") and
   run under systemd.

### Config file vs CLI flags
`-c/--config` (default `config.yml`) is loaded by viper; every CLI flag in `docs/posts/cli.md` has
a matching YAML key, confirmed by `config.example.yml` in the repo root (e.g. `authentication: true`,
`authentication-keys-directory: deploy/pubkeys/`). CLI flags override the config file. There is no
separate "global config" object beyond this single flat flag/YAML namespace — sish has one process,
one config, applying server-wide to all tunnels (per-tunnel behavior is instead controlled via SSH
command-line options passed after the `-R` target, e.g. `tcp-alias=true`, `deadline=15m`,
`tcp-aliases-allowed-users=...` — see `docs/posts/cheatsheet.md`).

Required/important flags for a managed VPS deployment (all quoted from `docs/posts/cli.md`):
- `--ssh-address` (default `localhost:2222`), `--http-address` (default `localhost:80`),
  `--https-address` (default `localhost:443`) — the three listener ports.
- `--domain` (default `ssi.sh`) — "The root domain for HTTP(S) multiplexing that will be appended
  to subdomains". Requires a wildcard DNS `A` record (`*.example.com` → VPS IP) per
  `docs/posts/getting-started.md` "DNS" section.
- `--private-keys-directory` (default `deploy/keys`) — sish auto-generates its own SSH host key(s)
  here on first run if none exist (confirmed by `utils/authentication_key_request_test.go` which
  passes an empty temp dir and relies on sish to generate one); `--private-key-passphrase` encrypts it.
- `--https=true` + `--https-certificate-directory` (default `deploy/ssl/`) for static
  `name.crt`/`name.key` pairs, **or** `--https-ondemand-certificate` +
  `--https-ondemand-certificate-accept-terms` (+ optional `--https-ondemand-certificate-email`) to
  fetch certs from Let's Encrypt automatically per-hostname on demand (this is an alternative to the
  dnsrobocert wildcard-cert compose setup, useful for `--bind-any-host`/custom-domain scenarios —
  see `docs/posts/advanced.md` "Custom domains").
- Authentication method selection (global, applies to the whole SSH listener):
  `--authentication` (bool, default `true`) turns auth on/off; then exactly one/combination of:
  - `--authentication-password` (static shared password) and/or
  - `--authentication-password-request-url` (HTTP callback for password validation), and/or
  - `--authentication-keys-directory` (default `deploy/pubkeys/`) — file-based authorized-keys style,
    directory is watched (poll interval `--authentication-keys-directory-watch-interval`, default
    `200ms`) and hot-reloaded, and/or
  - `--authentication-key-request-url` — HTTP callback for public-key validation (see section 2/3 below).
  These are not mutually exclusive; sish tries the static password/key list first, then falls back
  to the configured callback URL(s) (confirmed in `utils/utils.go` `GetSSHConfig()`, lines 480-520).
- Bind-policy flags to make it "restrictive" for a single-tenant/managed use case:
  `--bind-random-subdomains` (default `true`, forces random subdomain unless disabled),
  `--bind-random-ports` (default `true`), `--force-requested-subdomains`, `--banned-subdomains`,
  `--bind-hosts`.
- `--admin-console` + `-j/--admin-console-token` for the HTTP admin API (see next section).

### Wrapping as a service
- **Docker**: the `docker run ... --net=host` command above, or Docker Compose as documented, is
  already the "service" — wrap with `docker run --restart=always` or the provided compose file (which
  Compose keeps running via its own restart policy).
- **systemd** (not shipped by upstream, but trivial given a single static binary + flat flag/YAML
  config): a unit like
  ```ini
  [Service]
  ExecStart=/usr/local/bin/sish --config=/etc/sish/config.yml
  Restart=always
  User=sish
  ```
  is standard practice; nothing about sish requires anything more exotic (no local DB, no extra
  daemons) beyond the filesystem directories for keys/certs/pubkeys.

**What sish provides vs what we build:** sish provides the entire server binary, TLS termination
(including Let's Encrypt automation), SSH host-key generation, and the pubkey-directory-watcher
loop. We only need to build: the systemd unit / process supervisor (or use the Docker image as-is),
provision the wildcard DNS record, and choose+wire one of the two pubkey authorization mechanisms
described in the next two sections.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ sish server)

**There is a genuine, if narrow, HTTP admin API.** It is NOT documented in `docs/posts/cli.md`
beyond the one-line flag description, but source in `utils/console.go` confirms three concrete
JSON endpoints, all gated by `--admin-console=true` and a shared bearer-style token
(`-j/--admin-console-token`), passed as `?x-authorization=<token>` query param or `x-authorization`
header (`utils/console.go` lines 52-58):

- `GET /_sish/api/clients` (must be requested against the **root domain**, `hostIsRoot` check in
  `console.go` line 84) — returns JSON of every currently-connected SSH client: remote address, SSH
  username, SSH client version, session ID, **public key and SHA256 fingerprint**
  (`sshConn.SSHConn.Permissions.Extensions["pubKey"/"pubKeyFingerprint"]`), and all of that client's
  active listeners (HTTP subdomains, TCP ports, TCP aliases). This is the closest thing sish has to
  "list connected clients" (`utils/console.go` `HandleClients`, lines 186-313).
- `POST /_sish/api/disconnectclient/<clientName>` — force-disconnects a given SSH connection by its
  internal client name key (`HandleDisconnectClient`, lines 128-147).
- `POST /_sish/api/disconnectroute/<base64 route>` — closes a specific forwarded listener/route
  (`HandleDisconnectRoute`, lines 149-184).

There is also a separate, per-tunnel **service console** (`--service-console`, described in
`docs/posts/forwarding-types.md` "HTTP" section and CLI flag `-m/--service-console-token`) which is
a live request/response inspector web UI + websocket feed for one forwarded route, authenticated
per-route with its own token echoed back to the SSH client in the connection banner
(`sshmuxer/httphandler.go` lines 80-121, `"...console can be accessed here: %s/_sish/console?x-authorization=%s"`).
This is for debugging traffic, not for managing auth — not directly useful to our control plane.

**Critically, there is NO admin API for managing/adding/revoking authorized public keys.** The three
endpoints above only *read/disconnect* live SSH sessions; they do not touch the authorization list.
Authorization is controlled exclusively by one of:
1. the file-based `--authentication-keys-directory` watcher, or
2. the `--authentication-key-request-url` HTTP callback (sish calls out to *us*, not the reverse).

So SvelteKit's "API" into sish is a hybrid:
- **Read path (list/status of connected clients)**: real HTTP GET to `/_sish/api/clients` on the
  admin console port, using the shared admin token. This is the one part of the integration that is
  genuinely "call an HTTP API", no glue needed beyond an HTTP client and token config.
- **Write path (authorize a new client key)**: no HTTP API exists. Two implementable approaches,
  both requiring glue code we build:
  - **(A) Directory-watcher approach**: SvelteKit's backend writes an `authorized_keys`-style file
    (one OpenSSH public-key line, or multiple newline-separated) into the directory pointed to by
    `--authentication-keys-directory` (docs confirm: "Files in this directory can either be single
    key per file, or multiple keys per file separated by newlines, similar to `authorized_keys`" —
    `docs/posts/getting-started.md` lines 143-144). sish's own filesystem watcher
    (`authentication-keys-directory-watch-interval`, default 200ms, implemented via the
    `certHolder`/`holderLock` reload loop in `utils/utils.go` around line 440) picks up the new file
    automatically — "regenerated on directory modification, so removed public keys will also
    automatically be removed" (same doc). This requires our backend to have filesystem/SSH/SFTP/
    shared-volume access to the sish host (e.g. mounted volume if co-located, or an SSH/SCP write, or
    a small sidecar HTTP-to-filesystem shim we write ourselves — sish provides none of this).
  - **(B) Callback approach (recommended)**: set `--authentication-key-request-url=https://<our
    backend>/api/sish/authorize-key`. On every SSH connection attempt, sish itself POSTs
    `{"auth_key": "<openssh-authorized-key-line>", "user": "<ssh-username>", "remote_addr": "<ip:port>"}`
    to that URL (confirmed exactly in `utils/utils.go` lines 501-517 and the JSON shape tested in
    `utils/authentication_key_request_test.go` `AuthRequestBody` struct) and treats HTTP 200 as
    approval, anything else as rejection (`checkAuthenticationKeyRequest`, `utils/utils.go` line
    535 onward, checks `res.StatusCode != http.StatusOK`). This is a **synchronous, per-connection**
    call with a configurable timeout (`--authentication-key-request-timeout`, default 5s) — so our
    SvelteKit endpoint must respond fast and be highly available (if it's down, no client can
    connect at all). We build this endpoint entirely ourselves: it queries our own DB of
    linked/authorized local-client keys (keyed by fingerprint or raw key) and returns 200/401.
    This is the cleanest model because "authorizing a new client" becomes a plain row-insert in our
    own database — no filesystem coupling to the sish host at all.

**Bottom line: sish's admin console gives us read-only client listing + forced disconnect over real
HTTP; it gives us zero programmatic control over authorization state — that's 100% our glue code**,
best implemented as option (B), the `authentication-key-request-url` callback, backed by our own
DB-driven authorize/revoke logic.

---

## Client Linking Flow (access token exchange)

sish itself has **no concept of "access tokens", enrollment, or self-registration** — its
"pairing flow" is bare SSH public-key auth, full stop. Quoting the actual documented mechanism
(`docs/posts/getting-started.md` "Authentication" section, lines 134-155):

> "If you want to use this service privately, it supports both public key and password
> authentication. To enable authentication, set `--authentication=true` as one of your CLI options
> and be sure to configure `--authentication-password` or `--authentication-keys-directory` to your
> liking. The directory provided by `--authentication-keys-directory` is watched for changes and
> will reload the authorized keys automatically... Files in this directory can either be single key
> per file, or multiple keys per file separated by newlines, similar to `authorized_keys`."

And the author's own recommended enrollment pattern is literally fetching a public GitHub keys URL:

> ```
> sish@sish0:~/sish/pubkeys# curl https://github.com/antoniomika.keys > antoniomika
> ```
> "This will load my public keys from GitHub, place them in the directory that sish is watching,
> and then load the pubkey. As soon as this command is run, I can SSH normally and it will authorize me."

So out of the box: **an admin manually places each authorized public key file (or a callback
service decides per-connection)** — there is no built-in self-service enrollment, no token exchange,
no OAuth-like flow, nothing resembling `ngrok`'s auth-token-in-config-file UX.

**For our access-token-style install flow, we must build the entire thing as glue code on top of
option (B) above:**
1. Local-client installer (`aipotluck-local-client` on the user's machine) generates a fresh SSH
   keypair locally (e.g. `ssh-keygen -t ed25519 -f ~/.aipotluck/id_ed25519 -N ""`) — sish requires
   nothing special about key type/format beyond standard OpenSSH-parseable keys
   (`ssh.ParseAuthorizedKey` is used server-side, confirmed in
   `utils/authentication_key_request_test.go` `PubKeyHttpHandler`).
2. During install, the user pastes/enters a **one-time access/pairing token** issued by the
   SvelteKit backend (this token is entirely our own invention — sish has no notion of it).
3. The local client calls a SvelteKit REST endpoint (our own, not sish's) — e.g.
   `POST /api/link-device {token, public_key}` — over normal HTTPS, authenticating with the
   pairing token. The backend validates the token, associates the given SSH public key /
   fingerprint with that user/device in our DB, and marks it "authorized".
4. From then on, whenever the local client's `ssh -R ...` connection reaches sish, sish calls our
   `--authentication-key-request-url` callback with that exact public key; our backend looks it up
   in the DB row created in step 3 and returns 200.
5. (Only if we chose approach A instead of B): step 3's backend action would instead be writing/
   SCPing a file containing that public key into the `authentication-keys-directory` on the sish
   host, keyed by a filename like the device ID, so it can also be cleanly deleted on revoke.

Either way, **100% of the "access token exchange" and "pairing" UX is glue code we write**; sish's
only contribution is: (a) accepting the eventual SSH public-key auth attempt, and (b) optionally
calling into our validation endpoint per attempt.

Revocation is symmetric: delete the DB row (approach B, next callback call returns non-200 and
future connections fail) or delete the key file (approach A, next directory-watch cycle drops it) —
sish also exposes `/_sish/api/disconnectclient/<name>` to kill an already-established session
immediately (from the admin console API in section 2) if we need instant revocation rather than
waiting for the client's own reconnect/ping-timeout cycle.

---

## Request Routing (public traffic → local client)

sish's HTTP routing is subdomain-based virtual hosting, assigned per-`ssh -R` invocation, not
per-SSH-key:

- Client runs `ssh -R <subdomain>:80:localhost:<local-port> <sish-host>` (or `:443` for HTTPS).
  Quoting `docs/posts/forwarding-types.md`: "To make use of HTTP forwarding, ports `[80, 443]` are
  used to tell sish that a HTTP connection is being forwarded... `ssh -R hereiam:80:localhost:8080
  tuns.sh` ... share the link `https://hereiam.tuns.sh`."
- **Subdomain choice is per-connection/user-supplied, not derived from the SSH key fingerprint.**
  The user picks the subdomain string in the `-R` argument itself (`subdomain:80:...`). If omitted,
  or if the requested one is taken, sish assigns a random one (`GetOpenHost`, `utils/utils.go` line
  884 onward: "GetOpenHost returns an open host or a random host if that one is unavailable"), whose
  length is controlled by `--bind-random-subdomains-length` (default 3 chars). Server operators can
  force this behavior with `--bind-random-subdomains=true` (default `true`!) to prevent users
  choosing arbitrary subdomains, or `--force-requested-subdomains=true` to require the exact
  requested one succeed or the bind fails.
- Internally, once bound, sish stores an `HTTPHolder` (`sshmuxer/httphandler.go` lines 51-60) keyed
  by the full host URL, and adds the specific SSH connection as a backend server in an
  `oxy/roundrobin` load balancer (so if `--http-load-balancer` is enabled, *multiple* SSH clients can
  share the same subdomain and get round-robin'd, per `docs/posts/advanced.md` "Load balancing").
  Public HTTP(S) requests to `https://<subdomain>.<domain>` are matched by Host header against this
  map and proxied over the corresponding still-open SSH channel to the local client's `localhost:<port>`.
- **For our use case (fixed backend calling a fixed local client), the practical design is:** assign
  each linked device a stable, predictable subdomain at pairing time (step 3 in section 3) — e.g.
  `device-<uuid or short-id>.tunnels.ourapp.com` — and have the local-client daemon always invoke
  `ssh -R <that-fixed-subdomain>:80:localhost:<llama-cpp-port> <sish-host> -o ServerAliveInterval=...`
  with `--force-requested-subdomains=true` server-side so the daemon can rely on that exact hostname
  every time it reconnects (otherwise a race/collision could hand it a random one). Our SvelteKit
  backend then just issues plain HTTPS requests to `https://device-<id>.tunnels.ourapp.com/...`,
  which sish transparently forwards over the (currently-connected) SSH tunnel to the local
  llama.cpp server. No sish-side "mapping API" is needed once the subdomain convention is fixed by
  us — routing is pure DNS + Host-header dispatch inside sish, automatic and free once the tunnel is up.
- Custom/BYO domains are also supported (`docs/posts/advanced.md` "Custom domains") via TXT record
  `_sish.customdomain` containing the SSH key fingerprint, but this is unnecessary complexity for
  our single-operator-domain scenario — the fixed-subdomain-per-device convention above is simpler
  and needs no DNS changes per device.

---

## Status Polling / Health Checks

sish provides exactly one usable mechanism for "is this client's tunnel currently up": the admin
console's `GET /_sish/api/clients` endpoint (section 2, `utils/console.go` `HandleClients`). For
each live SSH connection it returns `remoteAddr`, `user`, `pubKeyFingerprint`, and the full set of
`listeners`/`routeListeners` (bound subdomains/ports/aliases) for that connection — so our backend
can poll this endpoint, match on `pubKeyFingerprint` (which we already store from the linking flow
in section 3), and derive "connected: true/false" plus which hostname is currently live for it.
There is no push/webhook notification of connect/disconnect events — this is poll-only.

Complementary mechanisms, all confirmed by CLI flags in `docs/posts/cli.md`:
- `--ping-client` (default `true`) + `--ping-client-interval` (default 5s) + `--ping-client-timeout`
  (default 5s): sish actively pings the underlying SSH connection and will drop it if the client
  doesn't respond — this is what keeps the `/_sish/api/clients` list accurate/pruned promptly rather
  than showing stale half-dead connections.
- `--cleanup-unauthed`/`--cleanup-unbound` (+ timeouts): drop connections that authenticate but never
  bind a forward, again keeping the client list clean.
- Process-level introspection: `--debug` + `--debug-interval` prints internal state to sish's own
  logs (`--log-to-file`/`--log-to-stdout`), useful for operator troubleshooting but not something
  our SvelteKit backend can consume as structured data without log scraping — not recommended as our
  primary signal.
- We could also just directly HTTP-probe the device's own fixed subdomain
  (`https://device-<id>.tunnels.ourapp.com/health`) as an end-to-end liveness check (this exercises
  the full path: sish routing + SSH tunnel + local llama.cpp health endpoint) — this is arguably more
  meaningful than the admin API's "is the SSH session open" signal, since a session can be open with
  a stuck/dead local service behind it. We'd build this ourselves as a periodic job in SvelteKit; sish
  provides no built-in end-to-end health probing feature.

**Recommended combination:** poll `/_sish/api/clients` on an interval (say every 15-30s) as the
"cheap" connectivity signal for dashboard/status display, and additionally do our own HTTP health
probe of the device's exposed subdomain when we need to confirm the local llama.cpp server itself is
actually responsive (not just that the SSH tunnel exists).

---

## Open Questions / Assumptions

- **Admin console exposure surface**: `/_sish/api/clients` requires `hostIsRoot` (i.e., must be
  requested against the bare root domain, not a subdomain) — need to confirm exactly how "root
  domain" is resolved when `--proxy-ssl-termination`/reverse-proxy setups are used in front of sish;
  may need the admin console reachable on a dedicated internal port/network rather than the public
  edge, to avoid exposing `/_sish/api/disconnectclient` publicly even behind a token (token is a
  single shared secret across all admin operations — no scoping/RBAC).
- **`authentication-key-request-url` availability requirement**: since this callback is
  synchronous per SSH-connection-attempt with a 5s default timeout, our SvelteKit authorize-key
  endpoint becomes a hard dependency for *every* tunnel (re)connect, not just initial pairing. Need
  to size this endpoint for high availability/low latency (small DB lookup only), and decide the
  failure mode (timeout ⇒ connection rejected) is acceptable, or consider layering a local cache/
  fallback (e.g. also write to the directory-watcher approach as a redundant path) — not verified
  whether sish supports configuring multiple auth mechanisms as true OR-fallback beyond the
  static list-then-callback order already observed in `utils/utils.go`.
- **No SDK/client library**: sish's "client" is just the system `ssh` binary; our local-client
  daemon will need to shell out to `ssh` (or embed a Go/other SSH client library) and manage
  reconnect/backoff itself — sish provides `--ping-client` for keepalive detection server-side but no
  equivalent auto-reconnect logic lives in the client since there is no bespoke client binary.
- **Per-device fixed subdomain race**: relying on `--force-requested-subdomains=true` prevents silent
  fallback to a random subdomain, but means a reconnect attempt will hard-fail (rather than get a
  substitute hostname) if for any reason the subdomain is still marked bound by a stale/zombie
  connection — needs to be paired with reasonably aggressive `--ping-client-timeout`/
  `--cleanup-unbound-timeout` tuning so stale binds are freed quickly.
- **Version drift**: all flag names/behavior above were verified against the current `main` branch
  at clone time; since sish is actively developed (frequent `cli.md`/`main.go` changes per its own
  CI docs-publish workflow, `.github/workflows/docs.yml`), pin to a specific Docker tag/commit for
  production rather than tracking `:latest`, and re-verify flags against that pinned version before
  building the glue code above.
- **Load balancer mode not exercised**: this plan assumes one SSH connection per device/subdomain
  (no `--http-load-balancer`); if we ever need multiple redundant local-client processes behind one
  logical device endpoint, sish's load-balancer flags would need separate evaluation (round-robin
  only, no health-aware routing beyond the ping/cleanup timeouts already discussed).

---

## Summary of custom glue code required

sish supplies: the SSH server, TLS termination, subdomain routing/proxying, an admin HTTP API for
*listing*/*disconnecting* live SSH sessions, and a synchronous HTTP *callback* hook for pubkey
authorization decisions. It supplies **no** enrollment/pairing UX, **no** access-token concept, and
**no** API to add/revoke authorized keys. Everything in sections 3 (pairing/token exchange), most of
section 2 (write-path "authorize a key" logic and its backing DB), the fixed-subdomain convention in
section 4, and the end-to-end health probing in section 5 must be built by us as SvelteKit backend
code plus a local-client daemon that shells out to standard `ssh`.
