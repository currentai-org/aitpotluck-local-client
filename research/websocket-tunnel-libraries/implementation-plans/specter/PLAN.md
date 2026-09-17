# specter Implementation Plan

**Source verified against:** `git clone https://github.com/zllovesuki/specter` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/specter` (main branch, commit as of clone time).
Files cited below are relative to that repo root unless a full URL is given.

Key docs used: `README.md`, `OPERATOR.md`, `docs/lightweight-tunnels.md`, `docs/lightweight-compatibility.md`,
`gateway/endpoints.md`, `docs/frontend-spec.md`, `PRODUCT.md`, `DESIGN.md`.
Source used: `cmd/server/server.go`, `cmd/client/{tunnel,expose,serve,token,lightweight,listen,connect}.go`,
`tun/client/{server.go,delegation.go,lightweight.go,lightweight_token.go,config.go}`,
`gateway/{apex.go,http.go,gateway.go}`, `packaging/linux/*.service`, `compose-server.yaml`, `compose-client.yaml`.

---

## Installation & Global VPS Configuration

specter is a single Go binary (`main.go` → `cmd/specter/app.go`) with subcommands `server`, `client`,
`dns`, `pki ca`. Three install paths are documented and packaged:

1. **Native OS packages (recommended for a managed VPS)** — `OPERATOR.md` "Installation → Packaged
   Installs": `.deb`/`.rpm` packages install:
   - Binary: `/usr/bin/specter`
   - Launchers: `/usr/libexec/specter/{server-launch,dns-launch,client-launch}`
   - Configs: `/etc/specter/*.env`, `/etc/specter/client.yaml`
   - Certs: `/etc/specter/cert`
   - RPC socket dir: `/run/specter`
   - systemd units: `specter-server.service`, `specter-dns.service`, `specter-client.service`
     (confirmed present at `packaging/linux/specter-{server,dns,client}.service`)
   - Dedicated `specter` service user; pre/post install scripts at
     `packaging/linux/{preinstall,postinstall,postremove}.sh`.

2. **Docker / Docker Compose** — `Dockerfile` in repo root builds the binary; `compose-server.yaml`
   and `compose-client.yaml` are working dev examples. `OPERATOR.md`: "ensure that the data directory
   you pass via `--data-dir` and your certificate directory are mounted as persistent volumes."
   Example server invocation from `compose-server.yaml` line 66:
   ```
   command: --verbose server --data /app/data --cert /certs --listen :1113 \
     --advertise seed:1113 --listen-http 1112 --apex dev.con.nect.sh --virtual 3 \
     --acme acme://admin@example.com@hostedacme.com --listen-rpc tcp://:1280
   ```
   Ports used in this compose file: `1113` (TCP+UDP QUIC/TLS mux listener), `1112` (plain HTTP
   redirect/CONNECT listener, mapped internally, default port 80), `1280` (internal RPC, must stay
   private). Env vars set alongside: `INTERNAL_USER`, `INTERNAL_PASS`, `ACME_CA`, `KV_PROVIDER=sqlite`.

3. **Build from source**: `go build ./...` per `README.md` "Development"; requires Go 1.27.x,
   Node 22.12+/npm for `make ui` (embeds the operator + client web UIs), Docker+buildx for the dev
   cluster, `protoc` only if regenerating Twirp/protobuf.

### One-time global config (from `OPERATOR.md` "Quick Start")
- **CA/cert bundle** (mutual TLS between nodes and clients): a directory with `ca.crt`, `client-ca.crt`,
  `client-ca.key`, `node.crt`, `node.key`. specter's mux transport authenticates both nodes and clients
  via mTLS — there is no username/password admin account concept at the gateway level (contrast with
  sish); identity is a certificate.
- **Seed node bootstrap**: first node starts *without* `--join`:
  ```sh
  specter --verbose server --data /var/lib/specter --cert /etc/specter/cert \
    --apex example.com --acme acme://ops@example.net@acme-hosted.net \
    --listen-rpc unix:///run/specter/rpc.sock
  ```
- **ACME DNS solver** (separate process, needed for automated Let's Encrypt certs on the apex):
  ```sh
  specter dns --acme acme://ops@example.net@acme-hosted.net \
    --acme-ns ns1.acme-hosted.net/203.0.113.10 --rpc unix:///run/specter/rpc.sock
  ```
  Requires delegating `_acme-challenge.<apex>` via CNAME to the managed ACME zone, and NS/A records
  for the `--acme-ns` hostname (`OPERATOR.md` "Quick Start" steps 3–4).
- **Admin credentials for `/_internal`**: set `INTERNAL_USER` / `INTERNAL_PASS` env vars (or
  `--auth_user`/`--auth_pass`) — *"Without these credentials, the `/_internal` routes are disabled"*
  (`gateway/endpoints.md` line 9, `OPERATOR.md` line 226). This is the closest thing to a "global admin
  account" and it's HTTP Basic Auth, not a DB-backed user table.
- **KV/DB init**: no separate DB init step — `--data-dir` (default via `--kv-provider`, default `aof`)
  is created and self-initializes on first run; alternative `sqlite` backend uses `<data-dir>/cache`
  (`OPERATOR.md` "Storage and Durability"). `--data-dir` "holds tunnel routing mappings, client tokens
  and hostname bindings, custom-host validation state, and ACME-related state" — this must be persisted
  and backed up.
- **Listener ports** summary (`OPERATOR.md` "Addresses and Listeners" + "Gateway Behavior"): one mux
  port for TLS+QUIC (HTTP/2, HTTP/3 via QUIC, and raw tunnel traffic) via `--listen-addr`/
  `--advertise-addr`, plus a small dedicated HTTP listener (default port 80) used *strictly* for
  HTTP/1.1 CONNECT and HTTPS redirects — "It never serves tunnels or internal endpoints directly."
  `--listen-rpc` (loopback or Unix socket only — "lacks authentication, always bind this to a loopback
  address or a secure Unix socket").
- **systemd wrapping**: the packaged install already ships `specter-server.service` /
  `specter-dns.service` / `specter-client.service`, configured via `/etc/specter/{server,dns,client}.env`
  shell files read by the launcher scripts (`packaging/templates/{server,dns,client}-launch.sh`); enable
  with:
  ```sh
  systemctl daemon-reload
  systemctl enable --now specter-server
  systemctl enable --now specter-dns
  ```

---

## Cloud-Side Control-Plane API (SvelteKit ↔ specter server)

specter's "control plane" surface splits into two very different kinds of API, and neither is a
classic operator-facing admin REST API for provisioning new tenants — it's built for the *client*
to manage its own tunnels, plus a read-only operator introspection tree:

1. **`/_internal/*` on the gateway (server-operator facing, read/manage-cluster only)** — documented
   exhaustively in `gateway/endpoints.md`. HTTP Basic Auth via `INTERNAL_USER`/`INTERNAL_PASS`. Relevant
   endpoints for a control plane:
   - `GET /_internal/tun/` (HTML) and `GET /_internal/api/tun/` (JSON) — list of connected tunnel
     clients (node identity, client identifier, version, observation timestamp).
   - `GET /_internal/tun/{id}/{address}` (HTML) and `GET /_internal/api/tun/{id}/{address}` (JSON) —
     per-client hostname→target mappings, both "configured" and "registered" states.
   - `GET /_internal/overview.json` — local node/ring health.
   - ACME cert management (`/_internal/acme/*`), Chord ring stats (`/_internal/chord/*`) — cluster ops,
     not tenant management.
   - **There is no `/_internal` endpoint to create a tenant, issue a token, or register a new hostname
     on behalf of a client** — this tree is observation + cluster/cert maintenance only.

2. **The tunnel client's own local HTTP API (`tun/client/server.go`), which is the actual "management
   API" the task description refers to.** Confirmed by source: when the CLI is started with
   `--server [host]:[port]` (README.md "API" section), the specter *client process* itself stands up a
   local `chi` router (`tun/client/server.go` line 320 `r.Mount("/api", api)`) exposing:
   - `GET /api/ls` — list currently registered hostnames (`tun/client/server.go` line 311).
   - `POST /api/publish/{hostname}` / `POST /api/unpublish/{hostname}` / `POST /api/release/{hostname}`
     (nearby in the same file) — publish/unpublish/release a tunnel target.
   - `GET /api/acme/{hostname}` and `GET /api/validate/{hostname}` — custom-domain ACME instruction and
     validation trigger (delegates to `c.GetAcmeInstruction`/`c.RequestAcmeValidation`).
   - `POST /api/reload` — reload client config from disk (confirmed via `tun/client/outcomes_test.go`
     line 80 `httptest.NewRequest(http.MethodPost, "/api/reload", nil)`).
   - **Domain-token management** (`docs/lightweight-tunnels.md` "While the owner runs" table, backed by
     `tun/client/delegation_test.go`):
     | Method | Path | Purpose |
     |---|---|---|
     | `GET` | `/api/tokens` | list grant metadata (no bearer tokens returned) |
     | `POST` | `/api/tokens` | body `{"hostname":"app.example.com"}` (+ optional `expiresAt` RFC3339); returns grant + token |
     | `POST` | `/api/tokens/<id>/revoke` | revoke a grant; returns `{revoked, indexError?}` |

   This confirms the task's premise: specter has a genuine, documented **client-side management API**
   (`--server host:port`) with an `/api/tokens` CRUD surface for minting/revoking per-hostname bearer
   tokens — this is the "pattern that could be extended into a control-plane."

**Critical architecture implication for our use case:** this management API lives on the *owner
client* process (the specter client that holds the full YAML config + registered hostname), **not on
the specter gateway/server**. So "SvelteKit talks to specter" cannot mean "SvelteKit calls the specter
server's REST API to provision a new tenant" — specter's server has no such API. Instead, the pattern is:
- Run one persistent "owner" specter client process *next to SvelteKit* (e.g. in the same container/
  host as the SvelteKit backend, or as a co-located sidecar), started with `specter client tunnel
  --server 127.0.0.1:9090 --config owner.yaml`, pre-registering one or more hostnames.
- SvelteKit's backend calls this **local owner client's** `/api/tokens` HTTP API (loopback, not
  public) to mint a fresh bearer token for each new local-client (aipotluck-local-client) linking
  event: `POST http://127.0.0.1:9090/api/tokens {"hostname": "device-<id>.tunnels.ourapp.com"}`.
  The response contains the token; SvelteKit hands it to the local client during pairing (see next
  section) and stores the returned grant `id` for later revoke calls.
- Alternatively (offline/CLI path used for initial owner bootstrap or automation without a running
  owner process): `specter client token mint --config owner.yaml <hostname>` /
  `token list` / `token revoke <id>` — same underlying RPCs (`MintDelegation`/`ListDelegations`/
  `RevokeDelegation`, `cmd/client/token.go`), callable by SvelteKit via shelling out if it prefers a
  stopped-owner batch flow instead of a long-running API sidecar.
- No further "auth" is needed for SvelteKit to talk to this loopback API beyond network isolation
  (bind `--server 127.0.0.1:<port>`, never expose publicly) — there is no separate API key documented
  for the client's own `/api` tree.

---

## Client Linking Flow (access token exchange)

specter's **lightweight token client** (`specter client serve`, `docs/lightweight-tunnels.md`) is
exactly the "pre-shared token in config" pairing model requested:

1. **Owner side (SvelteKit / owner client, done once per hostname):** an "owner" full client must have
   already registered the target hostname (e.g. `device-<id>.tunnels.ourapp.com`) via
   `specter client tunnel` with a YAML config. With that owner client running (or stopped, using
   `token mint` against its config file), mint a delegation:
   ```sh
   specter client token mint --config owner.yaml device-<id>.tunnels.ourapp.com
   ```
   or the HTTP equivalent `POST /api/tokens {"hostname": "device-<id>.tunnels.ourapp.com"}` against the
   owner client's local management API (see previous section). The response is JSON containing a
   `token` value — *"Save the token value in `tunnel.token`; it cannot be retrieved later"*
   (`docs/lightweight-tunnels.md` line 31). Grants have no expiry by default; `--expires-at`/`expiresAt`
   sets one.
2. **Token handoff to the new local-client machine (this is our "access token exchange"):** SvelteKit
   delivers this bearer token to the aipotluck-local-client installer/monitor during the linking step
   (e.g. displayed once during pairing, or delivered over an authenticated SvelteKit API call — this
   part, the actual transport of the token to the new device, is our own glue code; specter itself only
   defines what the token looks like and how it's consumed, not how it's transmitted to the device).
3. **First connection from the new machine:**
   ```sh
   specter client serve --apex tunnels.example.com --token-file tunnel.token http://127.0.0.1:8080
   ```
   or set `SPECTER_TUNNEL_TOKEN` env var and omit `--token-file` (`cmd/client/serve.go`,
   `cmd/client/lightweight.go` `loadTunnelToken`). The client keeps a fresh in-memory key/certificate
   (no on-disk client config needed — this is the "config-free client mode" referenced in the task) and
   authenticates the token over RPC; **"Token clients discover peers from the apex and maintain up to
   three server connections"** (`docs/lightweight-tunnels.md` line 41) — built-in HA without operator
   config.
4. **Ongoing validation, not just initial handshake:** *"Token sessions check grants and ownership
   every 30 seconds"* (same doc, "Connections and limits") — so revocation propagates automatically
   within ~30s–2min (temporary storage-failure grace period), without needing to forcibly kill the
   connection.
5. **Revocation:** `specter client token revoke --config owner.yaml GRANT_ID` (CLI) or
   `POST /api/tokens/<id>/revoke` (HTTP, on the owner's local API) — *"Revocation or expiry closes
   active streams; reconnecting requires fresh validation"*.
6. **Limits to design around:** an owner can have at most **128 grants** (including expired/incomplete)
   per hostname-owning client, and the server admits at most **1,024 lightweight sessions** cluster-wide
   including in-flight setup/cleanup (`docs/lightweight-tunnels.md` "Connections and limits").

This is a materially different (and better-fitting) model than sish/Punchmole: **the token is scoped
to a specific hostname grant, minted and revoked through a real API, with the specter binary itself
handling reconnect/discovery/expiry enforcement** — very little custom glue code is needed beyond (a)
running the owner client as a sidecar process and (b) transporting the minted token to the new device
during our own pairing UX.

---

## Request Routing (public traffic → local client)

Routing is **subdomain-based**, similar in spirit to sish, but the hostname binding is a first-class
registration object (not implied by an SSH `-R` argument):

- Each tunnel target is bound to a specific `hostname` at registration time. For a full client, this
  is either a random generated hostname (assigned by the server on first connect, then persisted back
  into the client's YAML config — `README.md` "Client Configuration" shows the config being rewritten
  with `hostname: overnight-graph-caboose-list-boney` after initial connection) or a validated custom
  domain (via the ACME flow: `specter client acme <domain>` → publish `_acme-challenge` CNAME →
  `specter client validate <domain>`, `OPERATOR.md` "Custom Domains").
- For our fixed-per-device design, the **domain-token model is the natural fit**: the owner client
  pre-registers one real hostname per device at pairing time (`device-<id>.tunnels.ourapp.com`) — since
  each token grant is minted *for a specific hostname* (`MintDelegationRequest{Hostname: ...}`,
  `tun/client/delegation.go` line 74-84), the mapping "hostname ↔ device" is established the moment we
  call `POST /api/tokens`, with no separate "bind route to client ID" API call needed afterward: the
  hostname *is* baked into the token.
- Traffic reaching the specter gateway is dispatched to whichever backend (full owner client, or one
  of up to 3 redundant lightweight-token connections) is currently registered for that hostname —
  the gateway's TLS/QUIC mux (`gateway/gateway.go`, `gateway/apex.go`) does the Host/SNI-based dispatch
  and proxies over the tunnel transport, same conceptual mechanism as sish's Host-header dispatch but
  over specter's own QUIC/TLS multiplexed transport instead of SSH channels.
- SvelteKit's role here is exactly one API call (already covered above): mint the token bound to the
  hostname during linking. No further per-request "bind hostname to client" call from SvelteKit is
  needed — the local-client daemon itself owns the connection lifecycle once it has the token.
- HTTP(S) targets are reached directly by public HTTPS to the hostname; TCP/Unix-socket/named-pipe
  targets (not our llama.cpp HTTP case, but noted for completeness) require either
  `specter client connect <hostname>` (encrypted, stdin/stdout tunnel, usable as `ssh -o ProxyCommand`)
  or the unencrypted HTTP CONNECT fallback via the dedicated port-80 listener (`README.md` "Connecting
  to Tunnel (CLI)").

---

## Status Polling / Health Checks

Two independent, real mechanisms exist:

1. **Operator/cluster-wide introspection (HTTP Basic Auth, via SvelteKit or an ops dashboard):**
   `GET /_internal/tun/` (HTML) or `GET /_internal/api/tun/` (JSON) lists all currently connected
   tunnel clients cluster-wide, and `GET /_internal/tun/{id}/{address}` /
   `GET /_internal/api/tun/{id}/{address}` gives per-client hostname→target mappings with
   **both "configured" and "registered" states shown separately** (`gateway/endpoints.md` lines
   96-118) — i.e., you can see if a hostname is configured on the client but not currently registered
   with the gateway (a stale/disconnected state), a nuance sish's flatter model doesn't expose.
   Caveat: *"Local connection list; no per-client Chord lookups"* — this reflects only what the queried
   node itself observes, not a guaranteed cluster-wide truth (specter is a DHT of multiple gateway
   nodes; a client might be connected to a different node than the one queried). Querying via
   `x-internal-proxy-node-address` header lets one node proxy the query to another node in the ring if
   needed.
2. **The owner/token client's own local API**: `GET /api/ls` returns currently registered hostnames
   for that specific client process (`tun/client/server.go` line 311) — useful if SvelteKit is polling
   the sidecar owner-client process rather than the gateway directly. `GET /api/tokens` additionally
   shows grant metadata (no liveness field documented beyond grant existence/expiry — connectivity
   itself is best confirmed via `/_internal/api/tun/` on the gateway side, or an end-to-end HTTP probe).
3. **End-to-end health check** (not provided by specter, our own glue): probe
   `https://device-<id>.tunnels.ourapp.com/health` directly — exercises gateway routing + specter
   transport + the actual llama.cpp server, the same "belt and suspenders" pattern recommended in the
   sish plan.

No push/webhook notification of connect/disconnect is documented; this is poll-only, same as sish.

---

## Open Questions / Assumptions

- **Exact JSON schema of `/_internal/api/tun/` and `/_internal/api/tun/{id}/{address}`** was not
  fully dumped from source in this pass (only confirmed the paths and prose description from
  `gateway/endpoints.md` — `overview_test.go` line 47 confirms the JSON path exists and returns
  "client data"). Before building a poller, read `gateway/http.go`/`gateway/apex.go` handler code
  directly for the exact field names.
- **Owner client sidecar operational model** is an inference from the documented API shape, not an
  explicit "how to run this as a service for SvelteKit" tutorial in the docs — specter's docs assume a
  human running `specter client tunnel --server host:port` interactively/as a service for their own
  use, not "one owner client per SaaS backend, minting tokens for many downstream tenants." This should
  work (nothing in the source restricts it), but running the owner client itself as a systemd-managed
  sidecar next to SvelteKit, with its `--server` bound to loopback only, is a design decision we're
  making, not something upstream explicitly recommends.
- **128-grants-per-owner and 1,024-total-lightweight-sessions limits**: confirmed from
  `docs/lightweight-tunnels.md`, but not stress-tested here; if aipotluck-local-client scales past 128
  linked devices per registered hostname, multiple owner-hostname pools would be needed — needs
  capacity planning against expected fleet size.
- **mTLS client-CA bundle provisioning for the *owner* client** (as opposed to lightweight token
  clients, which need no local config/cert) was not traced end-to-end — the owner client used to mint
  tokens presumably needs its own `client.yaml` with the full apex/cert setup from `README.md`
  "Client Configuration"; exact one-time setup steps for that owner identity (is it also mTLS-based,
  or just the config file?) need a closer read of `tun/client/config.go` before implementation.
- **specter's project status**: `README.md` "Status" table marks Tunnel Core/Gateway/Server/Client as
  **Beta**, not GA — treat production hardening (especially the newer domain-token/lightweight-client
  feature set, which reads as the most recently added capability given the dedicated docs file) as
  requiring your own soak testing before relying on the 30s grant-recheck/reconnect timing guarantees
  at scale.
