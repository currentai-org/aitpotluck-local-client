# wstunnel Implementation Plan

Source material used: `wstunnel` repo cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/wstunnel` (commit `73cd268ee0285187eeeb0cfdca3672ed03d0c7c1`),
its `README.md` (790 lines, CLI `--help` output embedded), `restrictions.yaml` (128 lines, the YAML restriction
rule format), `docs/using_mtls.md`, and https://wstunnel.erebe.eu/ + https://wstunnel.erebe.eu/docs.html (full text
cached at `/opt/data/cache/web/wstunnel.erebe.eu-07b073f6a3.md`).

**Bottom line up front**: wstunnel is a single static Rust binary that does exactly two things — run a WebSocket/HTTP2/
WebTransport server that terminates TLS and forwards bytes, and run a client that opens local ports/proxies and
tunnels their traffic to that server. It has **no REST API, no database, no admin dashboard, no concept of "users" or
"devices"**. Every one of the 5 questions below therefore splits into "what wstunnel's CLI/config actually does" vs.
"what our SvelteKit app has to build from scratch to get a usable product."

---

## Installation & Global VPS Configuration

**What wstunnel provides:**

- Static binaries per release: `https://github.com/erebe/wstunnel/releases` (README.md L45, L422; docs.html "Install").
  Pattern confirmed in README/docs: `wstunnel_*_linux_amd64.tar.gz` etc. for Linux/macOS/Windows/Android.
  ```bash
  tar -xzf wstunnel_*_linux_amd64.tar.gz
  chmod +x wstunnel
  sudo mv wstunnel /usr/local/bin/
  wstunnel --version
  ```
  Docker image also published: `docker pull ghcr.io/erebe/wstunnel:latest` (README.md L427, package page
  `github.com/erebe/wstunnel/pkgs/container/wstunnel`).

- Server invocation (README.md L291-418, docs.html "Server options"):
  ```
  wstunnel server [OPTIONS] <ws[s]|wts://0.0.0.0[:port]>
  ```
  Minimal real command for our use case:
  ```bash
  wstunnel server wss://[::]:8443 \
    --restrict-config /etc/wstunnel/restrictions.yaml \
    --tls-certificate /etc/wstunnel/certs/fullchain.pem \
    --tls-private-key /etc/wstunnel/certs/privkey.pem
  ```
  - **Listening ports**: exactly one TCP port (the address you pass, e.g. `8443`), which serves WebSocket *and*
    HTTP/2 simultaneously — "The server is capable of detecting by itself if the request is websocket or http2"
    (README.md L300). If `--enable-webtransport` or the `wts://` scheme is used, the *same port number* also binds
    UDP for HTTP/3/QUIC (README.md L306-313). There is no separate admin/metrics port — nothing to expose besides
    this one tunnel port.
  - **TLS**: `--tls-certificate` / `--tls-private-key` (PEM), auto-reloaded on file change, otherwise wstunnel serves
    an embedded self-signed cert shared by every install worldwide — explicitly called out as fingerprintable and
    unsuitable for anything but throwaway testing (README.md L511, docs.html "Caveats"). For production we must
    supply our own cert (e.g. Let's Encrypt via certbot) — wstunnel does not obtain/renew certs itself.
  - **mTLS** (optional, `docs/using_mtls.md`): `--tls-client-ca-certs ca.cert.pem` turns on client-cert auth; certs
    are reloaded automatically on change but *provisioning/signing/revoking* client certs is entirely our own CA
    tooling (the doc shows raw `openssl ca` commands, no wstunnel involvement).
  - **Server-wide restriction flags** (README.md L365-380, docs.html "Access control") — these are the ONLY built-in
    access control wstunnel has, and they are global/static per server process, reloaded from disk, not per-connection
    via any API:
    - `--restrict-to <HOST:PORT>` — allow-list of destinations the server will forward to at all (e.g.
      `--restrict-to 127.0.0.1:8080`). Repeatable but static.
    - `--restrict-http-upgrade-path-prefix <PREFIX>` — a single shared-secret path string(s) required on every
      client's WebSocket upgrade request. Repeatable, but it is one flat list, not a per-user secret store.
    - `--restrict-config <FILE>` — path to a YAML file (format = `restrictions.yaml` in repo root, 128 lines) with
      richer rules: match on `!PathPrefix` regex or `!Authorization` bearer-token regex, then `allow: [!Tunnel, ...]`
      listing permitted protocol/port/host/CIDR, plus a separate `!ReverseTunnel` block with `port_mapping` (maps a
      client-requested reverse port to a different actual bind port on the server). **This file is hot-reloaded when
      it changes on disk** (confirmed both in CLI help and docs.html) — this is wstunnel's only "dynamic
      reconfiguration without restart" mechanism, and it is a file write, not an API call.

- **systemd unit** (glue code, not shipped by wstunnel — no unit file exists in the repo):
  ```ini
  # /etc/systemd/system/wstunnel-server.service
  [Unit]
  Description=wstunnel server
  After=network-online.target
  Wants=network-online.target

  [Service]
  ExecStart=/usr/local/bin/wstunnel server wss://[::]:8443 \
    --restrict-config /etc/wstunnel/restrictions.yaml \
    --tls-certificate /etc/wstunnel/certs/fullchain.pem \
    --tls-private-key /etc/wstunnel/certs/privkey.pem \
    --log-lvl INFO
  Restart=always
  RestartSec=2
  User=wstunnel
  AmbientCapabilities=CAP_NET_BIND_SERVICE
  NoNewPrivileges=true

  [Install]
  WantedBy=multi-user.target
  ```
  `systemctl daemon-reload && systemctl enable --now wstunnel-server`. Because the restriction YAML and TLS files
  auto-reload, updating access rules does **not** require restarting/reloading the systemd unit at all — just
  rewriting the file on disk (this is the one place wstunnel gives us something close to "dynamic config").

**What we must build ourselves:** cert issuance/renewal automation (certbot + a reload-safe file write), the systemd
unit itself, the restriction YAML file *and the code that writes it* (see next section), log shipping/monitoring,
and the VPS provisioning/hardening around it. wstunnel gives us the binary and the flags; everything "managed
service" about it is our own ops work.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ wstunnel server)

**Critical fact**: wstunnel exposes **zero network API**. There is no port to `curl`, no gRPC/HTTP admin socket, no
way to ask it "list connected clients" or "register client X." Its only two "control" surfaces are (a) CLI flags at
process start, and (b) the two config files that hot-reload (`--restrict-config` YAML, and the TLS cert files). Any
per-client authorization has to be encoded through the file-based restriction mechanism plus wstunnel's own
per-connection auth primitives on the upgrade request:

- `--restrict-http-upgrade-path-prefix` (simple shared-secret path string, README.md L370-376).
- The `!Authorization` matcher in `restrictions.yaml` (L13-15): "This match applies only if it succeeds to match the
  Authorization Header with the given regex... `!Authorization \"^[Bb]earer +actual_bearer_token_to_match$\"`".
  This is the closest thing wstunnel has to a "JWT auth mechanism" — **it is a static regex compared against the raw
  `Authorization: Bearer <token>` header the client sends during the WS upgrade** (client side sets this with
  `--http-upgrade-credentials` for basic auth, or generically via `-H "Authorization: Bearer <token>"`, README.md
  L235-256). wstunnel does **not** parse, verify, or interpret JWT claims/signatures — it does a plain regex string
  match. All the "JWT" semantics (signing, expiry, per-device claims) have to be implemented by us; wstunnel only
  sees the opaque bearer string and regex-matches it.

**Proposed glue architecture:**

1. SvelteKit backend owns a Postgres/SQLite table `devices(id, user_id, secret_token, created_at, revoked_at)`.
   When a user registers a local client ("link a device"), SvelteKit generates a long random token (or a signed JWT
   — signing is our own code, e.g. `jsonwebtoken`/`jose` in Node; wstunnel plays no part in generation or
   verification) and stores it associated with that device's row.
2. SvelteKit maintains one `restrictions.yaml` restriction entry *per device* (or per fixed pool of destination
   ports — see Routing section), of the shape:
   ```yaml
   - name: "device-<uuid>"
     match:
       - !Authorization "^Bearer <device-token>$"
     allow:
       - !ReverseTunnel
         protocol: [Tcp]
         port: [20000]              # this device's fixed remote-to-local port, see Routing section
         port_mapping: ["<assigned-server-port>:20000"]
   ```
3. A small SvelteKit server-side job (Node process or a cron-triggered script) regenerates the full YAML file from
   the `devices` table whenever a device is added/revoked, writes it atomically (`write tmp file + rename`) to
   `/etc/wstunnel/restrictions.yaml` on the VPS, and relies on wstunnel's built-in hot-reload (confirmed: "Restriction
   file is automatically reloaded if it changes", README.md L379-380) to pick it up — **no wstunnel restart, no
   wstunnel API call, just a file write**. In practice this means SvelteKit needs SSH/SFTP or a small filesystem-sync
   agent (e.g. a tiny local writer process co-located with wstunnel, polling our own internal admin API for the
   current device list) since SvelteKit itself is not colocated with the VPS filesystem.
4. Revoking a device = remove its `!Authorization` rule from the generated YAML (or flip an `!Any`→deny-all rule
   order) and rewrite the file; the currently-open wstunnel connection for that device is **not** forcibly
   disconnected by a restriction-file change** (nothing in the docs says restriction reload closes existing sessions,
   only that new upgrade requests are checked against it) — so true "kill this session now" requires killing the
   underlying TCP connection at the OS level (see Open Questions).

**In short: SvelteKit is the entire control plane.** wstunnel supplies one static string-match/regex-match primitive
on an HTTP header at connection time; SvelteKit has to build the database, token issuance, YAML templating, safe
file distribution to the VPS, and audit/revocation logic completely from scratch.

---

## Client Linking Flow (access token exchange)

wstunnel's actual client CLI syntax (README.md L92-116, L226-256):

```bash
wstunnel client [OPTIONS] <ws[s]|http[s]|wts://wstunnel.server.com[:port]>
```

Concrete invocation for our use case — local client forwards its llama-server (say `127.0.0.1:8080`) up to the VPS
as a reverse tunnel, authenticated with the per-device bearer token minted by SvelteKit:

```bash
wstunnel client \
  -H "Authorization: Bearer <device-token-issued-by-sveltekit>" \
  -R 'tcp://[::]:20000:localhost:8080' \
  wss://tunnel.aipotluck.example:8443
```

- `-H, --http-headers <NAME: VALUE>` (README.md L249-252) is how the token rides along: it's sent as a normal HTTP
  header on the WebSocket upgrade request, which is exactly what the server-side `!Authorization` restriction regex
  inspects. There is also `--http-upgrade-path-prefix` (`-P`) if we prefer embedding the secret in the path instead
  of a header (README.md L226-233, "the path prefix act as a secret to authenticate clients").
- `-R 'tcp://[::]:20000:localhost:8080'` means: "server listens on port 20000, and forwards inbound connections back
  down the tunnel to `localhost:8080` on the client machine" (README.md L142-149, docs.html "Reverse tunneling").
  20000 must be one of the ports allow-listed for this device's token in `restrictions.yaml`.

**Onboarding flow we'd design:**
1. User clicks "Link this machine" in the SvelteKit UI.
2. SvelteKit generates `device_id`, `bearer_token`, assigns a free server-side port (or maps to a shared port — see
   Routing), writes the row to its own DB, and regenerates/pushes the restriction YAML (as above).
3. SvelteKit displays a one-line command (or a small install script wrapping it) with the token and port baked in,
   e.g. the exact `wstunnel client -H "Authorization: Bearer ..." -R ... wss://...` line above, for the user to run
   next to their local llama-server / our wrapper daemon.
4. The local client wrapper we write (not wstunnel) should supervise the wstunnel client process (respawn on crash,
   `--connection-retry-max-backoff` tuning, `--log-lvl` capture) since wstunnel itself has no persistence/retry-UI
   beyond the one reconnect backoff flag.

---

## Request Routing (public traffic → local client)

wstunnel's forwarding model is strictly **one static/dynamic proxy binding per `-L`/`-R` flag**, each mapping a
listen-address to a single forward-destination (or, for `socks5`/`http`, a dynamic per-request destination chosen by
the client program using the proxy — not by us). There is no path-based HTTP multiplexing built into wstunnel's own
protocol layer; the WS/H2 "envelope" only carries raw bytes for whatever single tunnel it belongs to (README.md
L118-150, docs.html "Tunnel syntax": `scheme://[BIND:]PORT:HOST:PORT`).

Given that, to let SvelteKit's HTTP requests reach a specific user's llama-server behind NAT, we have exactly two
wstunnel-native options, both requiring us to build the muxing/lookup layer ourselves:

1. **One dedicated server-side port per user** (what the `-R`/reverse-tunnel + `restrictions.yaml`
   `port_mapping` field is meant for, restrictions.yaml L54-58: "Maps ports on the server side from X to Y... For
   example with `10001:8080` configured and a client which connects using `-R tcp://10001:localhost:80` the server
   will listen on port 8080 instead of 10001"). Concretely:
   - Each device gets an assigned port in a reserved range on the VPS (e.g. 20000-20999), recorded in our `devices`
     table.
   - Client runs `-R 'tcp://[::]:20000:localhost:8080'`; server-side restriction YAML pins that device's token to
     that exact port via `port_mapping`.
   - SvelteKit (or an nginx/HAProxy layer we add in front of it) keeps its own routing table
     `device_id -> 127.0.0.1:20000` and proxies `POST /api/devices/<id>/infer` → `http://127.0.0.1:20000/...` on the
     VPS. This reverse-proxy hop is **our** code (e.g. a small Node/undici fetch or an nginx `map` block generated
     from the same device table we already maintain for the restriction YAML) — wstunnel does not do HTTP routing,
     it only opens the raw TCP listener on 20000 and pipes bytes to the user's `localhost:8080`.
   - Scaling limit: one TCP port per active device on the VPS; with hundreds/thousands of users this means either a
     large open port range (and matching restriction-file entries) or a periodic pool/recycling scheme we manage.

2. **Shared port + SOCKS5/HTTP dynamic proxy** (`-R socks5://...` or `-R http://...`, README.md L147-148): the server
   listens once and the *local* client machine decides the final destination per request — but this is designed for
   the client's LAN to reach many exit destinations, not for the cloud to address many different concurrent
   *clients* through one inbound port. It does not solve "SvelteKit picks user #4821's llama-server specifically";
   it only helps if there is a single tunnel client that then further internally multiplexes, which is not our
   topology. So for our use case (many independent local clients, cloud backend addressing one specific client per
   request) **option 1 (per-device port + our own reverse-proxy/lookup layer) is the only wstunnel-native fit.**

**What we must build:** the device→port allocation table, the restriction-YAML `port_mapping` generation, and an
HTTP reverse-proxy/router in front of the per-port TCP sockets that maps incoming public API requests
(`/api/devices/<id>/...`) to `127.0.0.1:<assigned-port>` on the VPS. wstunnel supplies only the raw TCP pipe per
port; all "route by user/device ID" logic is ours.

---

## Status Polling / Health Checks

wstunnel exposes **no status/introspection API** — confirmed absent from both the CLI `--help` output (README.md
L291-418) and the full docs.html feature/options tables: no `--status`, no metrics endpoint, no `wstunnel list`
subcommand. The only two ways to determine "is device X's tunnel alive" are both things we build:

1. **Server-host process/socket introspection** (glue code, not wstunnel):
   - `ss -tnp | grep :20000` or reading `/proc/net/tcp` on the VPS to see if the reverse-tunnel's mapped port
     (`20000` in the example above) currently has an established connection — this tells us the wstunnel *client*
     is connected to the wstunnel *server* and the reverse listener is bound, but not that llama-server behind it is
     healthy.
   - Alternatively, tail wstunnel's own logs (`--log-lvl INFO`/`DEBUG`, journalctl for the systemd unit) for
     connect/disconnect lines and parse them into our own DB — again, our own log-scraping code, not an API.

2. **End-to-end HTTP health check through the tunnel itself** (the more reliable option, still 100% our code): since
   the reverse tunnel forwards TCP to `localhost:8080` on the local client where llama-server runs, SvelteKit (or
   the reverse-proxy layer from the Routing section) can periodically `curl http://127.0.0.1:<assigned-port>/health`
   *on the VPS* — if llama-server responds, both the wstunnel tunnel AND the local server are confirmed alive in one
   check. This requires llama-server to expose a `/health` endpoint (it does, per prior project research) and for us
   to write a scheduler (cron/queue job) that pings every assigned port and records success/failure + latency into
   our own `devices` status table, which the SvelteKit UI then reads.

**Recommendation**: build health checking exclusively via method 2 (HTTP `/health` through the tunnel) as the source
of truth for "is this device usable," and use method 1 (`ss`/journalctl) only as a secondary diagnostic signal when
a device is reported down, to distinguish "tunnel dropped" from "local llama-server crashed but tunnel is still up."
Both are entirely our own polling infrastructure — wstunnel contributes nothing here beyond being the pipe the
health check happens to travel through.

---

## Open Questions / Assumptions

- **No forced-disconnect API**: nothing in the README/docs indicates that rewriting `restrictions.yaml` terminates
  an already-established session for a now-revoked token — it only appears to gate *new* upgrade requests. If
  hard/instant revocation is a requirement, we may need to `SIGHUP`/restart the wstunnel server process (defeats the
  "no restart" hot-reload benefit) or track and kill the specific TCP socket via `ss`/`iptables` on revocation,
  which needs verification against actual wstunnel server behavior (not observed directly, only inferred from docs
  silence on this point).
- **Port-range exhaustion / re-use**: with per-device dedicated reverse-tunnel ports (20000-20999 in the example),
  we assumed ~1000 concurrent devices as a rough ceiling before needing a bigger range or a port-recycling policy;
  this number needs to be sized against real expected user counts.
- **Authorization header format assumption**: we assumed the regex matcher (`!Authorization "^[Bb]earer +TOKEN$"`)
  is checked once at WebSocket-upgrade time and not re-validated per subsequent tunneled byte — this matches how
  HTTP upgrade requests work generally and how the docs describe it, but we did not find explicit text confirming
  there's no periodic re-check.
- **File-distribution mechanism to the VPS**: we assumed SvelteKit is not colocated with the wstunnel VPS and needs
  some sync mechanism (SSH/SFTP push, or a tiny sidecar API we write ourselves) to update `restrictions.yaml` and
  certs; if SvelteKit and wstunnel actually run on the same host this simplifies to a local file write, but this
  should be confirmed against actual infra plans.
- **HTTP/2 and WebTransport transports were not evaluated for our topology** — we assumed plain `wss://` WebSocket
  transport throughout since it's the documented default/most reliable option and our clients aren't expected to be
  behind CDN buffering or UDP-hostile networks; WebTransport in particular requires UDP end-to-end and is likely
  unnecessary complexity for this use case (docs.html "WebTransport" caveats).
- **mTLS was not adopted in the primary design** (bearer-token/path-prefix restriction was used instead) because
  mTLS requires us to run our own CA and distribute/rotate client certs per device, which is materially more glue
  code than generating a token string; this is a design choice, not a wstunnel limitation, and could be revisited if
  token-in-header auth is judged too weak.
