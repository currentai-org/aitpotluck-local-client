# piko Implementation Plan

**Source verified against:** `git clone https://github.com/andydunstall/piko` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/piko` (main branch at clone time).
Docs used: repo `README.md`, and the project's GitHub Wiki (https://github.com/andydunstall/piko/wiki):
`Server`, `Server-Configuration`, `Authentication`, `Agent`, `Go-SDK`, `How-Piko-Works`, `Install`,
`Getting-Started`. Source files used: `pkg/auth/jwtverifier.go` (JWT claims/verification),
`server/status/client/{client,upstream,cluster,gossip}.go` (status API client), `cli/server/status/*.go`
(CLI wrapping the status client), `operations/helm/piko/templates/configmap.yaml` (k8s config shape).

---

## Installation & Global VPS Configuration

Piko is a single Go binary serving both roles: `piko server` and `piko agent` (wiki `Install` page).

1. **Binary release**: download from https://github.com/andydunstall/piko/releases, place on `PATH`.
   Verify with `piko -h`.
2. **Build from source** (wiki `Install`): requires Go ≥1.22.
   ```bash
   git clone https://github.com/andydunstall/piko.git
   cd piko
   make piko      # outputs bin/piko
   make image     # builds local Docker image piko:latest
   ```
3. **Docker** (wiki `Install`): official images at `ghcr.io/andydunstall/piko`:
   ```bash
   docker pull ghcr.io/andydunstall/piko:latest
   # or pin: ghcr.io/andydunstall/piko:v0.6.0
   docker run ghcr.io/andydunstall/piko:latest
   ```
4. **docker-compose demo cluster** (wiki `Getting-Started`): repo ships `demo/docker-compose.yml`
   standing up 3 server nodes + an NGINX load balancer + Prometheus/Grafana:
   ```bash
   cd piko/demo
   docker compose up
   ```

### Ports (per node — wiki `Server` "Ports" section, four independent ports)
- **Proxy port** (default `:8000`, `proxy.bind_addr`) — receives downstream/public traffic to be
  routed to an endpoint; exposed to the Internet in our scenario (SvelteKit and/or public users hit
  this to reach a device's llama-server).
- **Upstream port** (default `:8001`, `upstream.bind_addr`) — accepts the outbound WebSocket
  connections from local-client agents; "usually exposed to the Internet so upstream listeners can
  connect from external networks" — must be reachable from every device behind NAT.
- **Admin port** (default `:8002`, `admin.bind_addr`) — health/ready, Prometheus `/metrics`, and the
  **status API** used by `piko server status`. Wiki: "The admin port should not be exposed to the
  Internet." — keep this bound to an internal network/VPN only, or in front of an internal-only LB
  path, since SvelteKit will need to reach it (see Status Polling section).
- **Gossip port** (default `:8003`, `cluster.gossip.bind_addr`) — inter-node cluster gossip only,
  never public.

### Config file
`--config.path <file>` loads YAML (server and agent both support this + CLI flags override YAML,
wiki `Server-Configuration`/`Agent`). Full annotated server YAML schema (from
`Server-Configuration` wiki page) — relevant excerpt:
```yaml
proxy:
  bind_addr: ":8000"
  auth:
    hmac_secret_key: ""
    audience: ""
    issuer: ""
  tls: {enabled: false, cert: "", key: ""}
upstream:
  bind_addr: ":8001"
  auth: {hmac_secret_key: "", ...}
  tls: {enabled: false, cert: "", key: ""}
admin:
  bind_addr: ":8002"
cluster:
  node_id_prefix: ""
  join: []          # other nodes' addresses to gossip-join
  gossip: {bind_addr: ":8003"}
```
Each of `proxy`, `upstream`, and `admin` has **independently configurable auth/TLS** — e.g. you can
require JWT auth on the `upstream` port (agents) while leaving `proxy` open, or vice versa.

### One-time global config
- **TLS**: per-port `tls.enabled/cert/key` (PEM files) — no single global TLS object; must be set on
  each of `proxy`/`upstream`/`admin` individually if all three need HTTPS/WSS.
- **Admin account**: there is no separate "admin user" concept — the `admin` port's `auth:` block uses
  the same JWT mechanism as the other ports (HMAC/RSA/ECDSA secret+claims, wiki `Authentication`); if
  left unset, the admin port is **unauthenticated**, hence the "don't expose it publicly" warning.
- **DB init**: none — piko keeps all cluster/routing state in-memory, propagated via gossip; no
  external database to provision. Cluster membership bootstrap is via `cluster.join` (list of peer
  addresses or a resolvable DNS name, e.g. a Kubernetes headless service).

### Wrapping as a service
No systemd unit shipped; standard pattern:
```ini
[Service]
ExecStart=/usr/local/bin/piko server --config.path /etc/piko/server.yaml
Restart=always
User=piko
```
For a single-VPS deployment (not a full HA cluster), run one `piko server` node — clustering
(`cluster.join`) is optional and only needed for horizontal scale/fault tolerance (wiki `Server`
"Cluster" — "should typically be hosted behind a HTTP(S) load balancer" for multi-node; a single-node
deployment simply skips `cluster.join` and the load balancer).

---

## Cloud-Side Control-Plane API (SvelteKit ↔ piko server)

**Piko has no dynamic "create a tunnel/service" REST endpoint** — endpoints are **not
pre-registered server-side at all**. Quoting the README: "No static configuration is required to
configure endpoints, upstreams can listen on any endpoint they choose." The upstream (our local
client, via the agent or Go SDK) simply connects and declares the endpoint ID itself
(`piko agent http my-endpoint 3000`). This is the single biggest structural difference from gost
(which requires a server-side Ingress entry) and from sish's admin console.

Consequently SvelteKit's "control-plane API" role is narrower than for gost:
- **No endpoint/tunnel registration call is needed or possible** — there's nothing to `POST` to
  create an endpoint in advance.
- **What SvelteKit *does* need to do programmatically: mint per-device JWTs.** Piko's whole
  authorization model (wiki `Authentication`) is: the server verifies a signed JWT presented by the
  connecting agent/client, optionally scoped to specific endpoint IDs via a custom claim:
  ```python
  import jwt
  token = jwt.encode({"piko": {"endpoints": ["device-<id>"]}}, "my-secret")  # HS256 by default
  ```
  This minting happens **entirely on our own SvelteKit/backend side** — piko exposes no "issue me a
  token" HTTP endpoint; it only *verifies* tokens minted by whoever holds the shared secret
  (HMAC) or private key (RSA/ECDSA). SvelteKit must therefore hold the `hmac_secret_key` (or an
  RSA/ECDSA private key, keeping only the public key configured on the piko server) and run the
  equivalent of the Python `jwt.encode` snippet above using any JWT library (e.g. Node's
  `jsonwebtoken`) — confirmed field names from `pkg/auth/jwtverifier.go`:
  ```go
  type PikoClaims struct { Endpoints []string `json:"endpoints"` }
  type JWTClaims struct { jwt.RegisteredClaims; Piko PikoClaims `json:"piko"` }
  ```
  i.e. the claim is nested under a top-level `"piko"` object with key `"endpoints"`, alongside
  standard registered claims (`exp`, `iat`, `aud`, `iss`) which piko will verify if present
  (`jwtverifier.go` `Verify()`, using `jwt.WithAudience`/`jwt.WithIssuer` parser options when
  `audience`/`issuer` are configured server-side).
- **Auth scoping is per-port**: configure JWT verification independently on `upstream.auth` (governs
  which endpoint IDs an agent/local-client may *listen on*) and `proxy.auth` (governs which endpoint
  IDs a downstream/public caller may *address*) — wiki `Authentication`: "Each server port has
  independent configuration, such as you may authenticate upstream listeners but not clients
  connecting to the proxy port." For our scenario we'd enable `upstream.auth` (so only linked devices
  can register their own endpoint ID) and likely leave `proxy.auth` unset if SvelteKit's own backend
  is the only thing calling the proxy port (network-level trust) — or enable it too if the proxy port
  is reachable by less-trusted callers.
- **Algorithms supported**: HS256/384/512, RS256/384/512, ES256/384/512 (`jwtverifier.go` `methods`
  list, matches wiki `Authentication` "Piko supports HMAC, RSA, and ECDSA JWT algorithms").

**Bottom line**: SvelteKit's integration with piko is "mint a scoped JWT with our secret/private key,
hand it to the client" — no HTTP call to piko itself is required to authorize a new device. This is
simpler than gost (no Ingress-entry POST call needed) but pushes more responsibility onto us to
correctly implement JWT scoping and secret management, since piko performs no server-side
registration/bookkeeping of "which device owns which endpoint" — that mapping exists only implicitly,
per-connection, in the live gossip state.

---

## Client Linking Flow (access token exchange)

This is piko's most token-native flow of the four-plus-two tools surveyed — it is **literally a JWT
in a config field**, no custom protocol needed:

1. At link time, SvelteKit's backend **mints a JWT** scoped to that device's chosen endpoint ID, e.g.
   `{"piko": {"endpoints": ["device-<uuid>"]}}`, signed with the shared `upstream.auth.hmac_secret_key`
   (or RSA/ECDSA private key held only by SvelteKit, if using asymmetric — wiki recommends this for
   not needing to share a secret with anything that could leak it). Optionally set `exp`/`iat` for
   token expiry — piko verifies `exp`/`iat` automatically if present (`jwtverifier.go`).
2. SvelteKit delivers that JWT + the piko server's upstream URL to the local-client installer during
   linking (our own delivery channel — e.g. shown in an install UI or fetched via our own
   authenticated device-config endpoint; piko has no enrollment/pairing endpoint of its own).
3. The local-client (aipotluck-local-client's monitor service) runs the **agent** with that token:
   ```bash
   piko agent http device-<uuid> 8080 --connect.url http://piko-host:8001 --connect.token <jwt>
   ```
   (`--connect.token` per wiki `Authentication` "Agent" section; note the URL must point at the
   server's **upstream port**, confirmed in `Agent` wiki YAML: "The Piko server URL to connect to.
   Note this must be configured to use the Piko server 'upstream' port.") `8080` here is the local
   llama-server port.
   Or, embedding directly (no external `piko` binary) via the **Go SDK** in our own Go monitor
   process:
   ```go
   upstream := &piko.Upstream{ /* token, server URL, TLS config, etc. */ }
   ln, _ := upstream.Listen(context.Background(), "device-<uuid>")
   http.Serve(ln, ourLocalProxyHandler)   // ln is a standard net.Listener
   ```
   (from wiki `Go-SDK`, package `github.com/andydunstall/piko/client`, `pkg.go.dev` reference:
   https://pkg.go.dev/github.com/andydunstall/piko/client — this is the natural integration point if
   aipotluck-local-client's monitor is itself Go, avoiding a subprocess entirely.)
4. On connect, piko's `upstream.auth` verifier checks the JWT signature + the `piko.endpoints` claim
   permits `device-<uuid>` before accepting the registration (`jwtverifier.go`: if `piko.endpoints` is
   present, it restricts which endpoint IDs that connection may use; if absent, "the client can access
   any endpoint" per wiki `Authentication` — so for genuine per-device isolation the claim must always
   be set, otherwise any valid token could squat any device's endpoint ID).
5. **Reconnect behavior is automatic and stateless**: "If an upstream is disconnected it will
   automatically reconnect and resume listening on the endpoint" (wiki `How-Piko-Works`) — the
   local-client daemon does not need custom reconnect/backoff logic; the agent/SDK handles it
   internally.

**Revocation**: since there's no server-side registration record, revocation means either (a) letting
the JWT's `exp` claim lapse (short-lived tokens, refreshed periodically by our own backend — adds
complexity), or (b) rotating/versioning the shared secret so previously-issued tokens fail
verification server-wide (blunt instrument, invalidates *all* devices), or (c) if using
RSA/ECDSA-per-device keys (not the documented default but architecturally possible since the server
config only holds one public key per algorithm slot) — not a clean single-device-revoke story out of
the box; **this is the weakest part of piko's model for our multi-tenant device scenario** and is
called out as an open question below.

---

## Request Routing (public traffic → local client)

Two addressing schemes, per README "Endpoints"/"HTTP(S)" section and wiki `Getting-Started`:

- **Host-header based**: if piko is hosted behind a wildcard domain (`*.piko.example.com`), the first
  label of the `Host` header is taken as the endpoint ID directly — "if your hosting Piko with a
  wildcard domain at `*.piko.example.com`, sending a request to `foo.piko.example.com` will be routed
  to an upstream listening on endpoint `foo`." This requires no server-side hostname↔endpoint mapping
  call at all — the endpoint ID *is* the subdomain, chosen by whoever holds a valid upstream JWT.
- **Explicit header based** (no wildcard DNS needed): `x-piko-endpoint: <endpoint-id>` header on the
  request, sent to piko's bare proxy-port address — confirmed in wiki `Getting-Started` "Request":
  ```bash
  curl http://localhost:8000 -H "x-piko-endpoint: my-endpoint"
  ```
  Also supports `Authorization: Bearer <token>` or (preferred, to avoid clashing with upstream's own
  `Authorization` use) `x-piko-authorization: Bearer <token>` for the proxy-port JWT if `proxy.auth`
  is enabled.
- **No server-side hostname/route "bind" call is needed or exists** — unlike gost's Ingress object,
  there's no piece of piko state that maps `device-x.tunnels.ourapp.com` → an endpoint ID that
  SvelteKit must create. The endpoint ID is just a string chosen at agent-start time (constrained
  only by the JWT's `piko.endpoints` claim, if enforced) and requests target it directly via
  `x-piko-endpoint` or Host-header subdomain-matching. **Practical design**: use the `x-piko-endpoint`
  header approach (`x-piko-endpoint: device-<uuid>`) from SvelteKit's own backend calls to avoid
  needing wildcard DNS at all — simpler than gost's ingress-hostname convention, at the cost of not
  getting a clean public per-device HTTPS URL for free (would need our own reverse-proxy layer in
  front of piko's proxy port if we want `https://device-x.ourapp.com` URLs that translate to the
  header internally).
- **TCP routing** requires `piko forward` (a local TCP-listener-to-endpoint bridge: `piko forward
  3000 my-endpoint` per README) or the Go SDK's `Dialer.Dial(ctx, "my-endpoint")`, since raw TCP has
  no header to carry the endpoint ID — not needed for our HTTP-based llama-server use case but
  relevant if we ever need raw TCP (e.g. exposing something non-HTTP).
- **Cluster-aware routing**: if piko runs as a multi-node cluster, any node can receive the request;
  gossip propagates "which node has which endpoint connected" so a node without a local match forwards
  internally to the node that does (wiki `How-Piko-Works` "Cluster" section) — transparent to
  SvelteKit, which just talks to the cluster's load balancer.

---

## Status Polling / Health Checks

Piko has a real, source-confirmed **status API** served on the admin port, with a matching CLI and Go
client package (`server/status/client/`):

- `GET /status/cluster/nodes` — list all cluster nodes with `endpoints`/`upstreams` counts
  (`server/status/client/cluster.go` `Nodes()`); `GET /status/cluster/nodes/{id}` for one node's
  detail (`Node(nodeID)`).
- `GET /status/upstream/endpoints` — **the key one for us**: returns
  `map[string]int` of endpoint-ID → connected-upstream-count for the queried node
  (`server/status/client/upstream.go` `Endpoints()` — decodes directly into `map[string]int`). CLI
  wrapper: `piko server status upstream endpoints`, example output (wiki `Getting-Started`):
  ```
  endpoints:
    my-endpoint:
    - 172.18.0.7:39084
  ```
  (Note: the wiki's example output actually shows a list of upstream addresses per endpoint, while the
  Go client type is `map[string]int` — i.e. the CLI's pretty-printer renders per-connection detail
  that the minimal typed client here simplifies to a count; the endpoint's presence with a non-zero
  count is what indicates "device is connected".) To check one specific device: poll this endpoint,
  look up key `device-<uuid>`; presence + non-zero count = at least one live upstream connection for
  that endpoint. Forwarded to a specific node with `--forward <nodeID>` (`server/status/client/client.go`
  `SetForward`, appended as a `?forward=<id>` query param) or `piko server status cluster nodes
  --forward piko-3-p3wnt2z`.
- `GET /status/gossip/nodes` / `/status/gossip/nodes/{id}` — raw gossip state (`gossip.go`), useful
  for deep cluster debugging, not needed for per-device liveness.
- **Health/ready endpoints** exist on the admin port per wiki `Server`: "The admin port exposes a
  health/ready status, metrics, and a status API" — exact paths (likely `/health`/`/ready`) weren't
  captured verbatim from the crawled wiki text; treat as a coarse "is the *server* itself up" signal,
  separate from per-endpoint upstream status.
- **Prometheus metrics** at `/metrics` on the admin port (confirmed same section) — likely exposes
  gauges for connected-upstreams-per-endpoint suitable for alerting/dashboards, but exact metric names
  weren't captured in this pass.
- **No push/webhook notifications** of connect/disconnect were found in the docs reviewed — this is
  poll-only, same caveat as the other tools surveyed.
- **Recommended for SvelteKit**: call `GET http://piko-admin-host:8002/status/upstream/endpoints`
  (JSON) directly — no CLI needed, it's a plain HTTP GET matching the same route the CLI's Go client
  hits — check for the device's endpoint ID key. Combine with our own end-to-end HTTP health probe
  through the proxy port (`x-piko-endpoint: device-<id>`, hitting the local llama-server's own
  `/health`) for a true liveness check of the whole path, exactly as recommended for the other tools.

---

## Open Questions / Assumptions

- **No clean single-device token revocation**: flagged above — piko's stateless-JWT model has no
  per-device revoke-by-ID primitive analogous to sish's `disconnectclient` or gost's ingress-rule
  deletion. Needs either short-lived+refreshed tokens, a denylist layer we build ourselves (checking
  JTI/claims against our DB before minting is fine, but doesn't stop an already-issued token from
  working until expiry), or accepting the operational cost of secret rotation for hard revokes. This
  is the single biggest architectural gap vs. gost/sish for our access-control requirements.
- **Any client can claim any unclaimed endpoint ID if `piko.endpoints` claim is omitted**: since
  endpoint IDs are arbitrary strings chosen by the connecting agent, correct enforcement absolutely
  depends on us always setting `piko.endpoints: ["device-<uuid>"]` in every minted JWT — an
  operational discipline requirement, not enforced by any server-side registry.
- **Exact admin-port health/ready and Prometheus metric paths/names** weren't verbatim-captured from
  the wiki text extracted (the `Server-Observability` wiki page was not crawled in this pass) — would
  need a follow-up fetch of `https://github.com/andydunstall/piko/wiki/Server-Observability` before
  wiring dashboards.
- **Whether the status API itself requires/accepts the same JWT auth as the admin port's `auth:`
  block** was not independently confirmed — `server/status/client/client.go`'s `Request()` sends a
  bare `http.NewRequest` with no auth header logic visible in the excerpt read, implying either the
  admin port is expected to be unauthenticated/network-isolated (consistent with the wiki's "should
  not be exposed to the Internet" guidance) or auth is added elsewhere not captured in this file —
  worth re-checking `server/status/handler.go` before assuming SvelteKit can hit it with no
  credentials.
- **Cluster vs. single-node for our VPS scenario**: this plan assumes a single `piko server` node
  (simplest for one VPS); if we ever need HA, `cluster.join`/gossip config was reviewed but not
  exercised — the gossip port must then also be reachable between nodes (private network only).
- **Version drift**: JWT claim shape (`pkg/auth/jwtverifier.go`) and config schema were read directly
  from source/wiki at clone time; re-verify against the specific release tag chosen for production
  (wiki examples reference `v0.6.0` as of the Install page).

---

## Summary of custom glue code required

Piko supplies: the proxy/upstream/admin/gossip server, WebSocket+yamux-multiplexed outbound-only
upstream connections with automatic reconnect, Host-header/`x-piko-endpoint` routing with zero
server-side registration step, JWT-based auth with endpoint-scoping claims, and a real status API +
CLI for per-endpoint connection counts. We must build: the JWT-minting service in SvelteKit (using our
own HMAC/RSA/ECDSA secret — piko never issues tokens itself), the device-linking delivery UX (token +
server URL handoff, entirely our own), the endpoint-ID naming convention enforced via the
`piko.endpoints` claim, any token-revocation strategy beyond plain expiry, and (optionally) a
reverse-proxy/DNS layer in front of piko's proxy port if we want clean per-device public hostnames
rather than relying on the `x-piko-endpoint` header directly.
