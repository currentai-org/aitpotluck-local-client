# gost (GO Simple Tunnel) Implementation Plan

**Source verified against:** `git clone https://github.com/go-gost/gost` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/gost` (main/master branch, `cmd/gost/version.go` reports `3.3.0`).
Docs site: https://gost.run (Chinese-primary, `README.md`/`README_en.md` in repo link out to it).
Key pages used: `getting-started/quick-start/`, `tutorials/api/overview/`, `tutorials/api/config/`,
`tutorials/reverse-proxy-tunnel/`, `concepts/ingress/`. Source files used: `gost.yml` (full annotated
example config in repo root), `install.sh`, `cmd/gost/*.go` (via `CLAUDE.md` in the cloned repo, which
documents the CLI entry point/flags/lifecycle).

---

## Installation & Global VPS Configuration

gost ships as a single static Go binary; the repo's `README.md` "下载安装" (download/install) section
documents three install paths, plus a fourth (`install.sh`) discovered in the repo root:

1. **Official install script** (repo root `install.sh`, root-required):
   ```bash
   bash <(curl -fsSL https://github.com/go-gost/gost/raw/master/install.sh) --install
   ```
   This script (verified by reading `install.sh`) detects OS/arch (`uname -s`/`uname -m`, mapping e.g.
   `x86_64`→`amd64`, `aarch64`/`arm64`→`arm64`) and pulls the matching release asset from
   `https://api.github.com/repos/go-gost/gost/releases`. Run without `--install` to interactively choose
   a version.
2. **Binary release** — download directly from
   https://github.com/go-gost/gost/releases and place on `PATH` (no installer needed).
3. **Build from source** — README:
   ```bash
   git clone https://github.com/go-gost/gost.git
   cd gost/cmd/gost
   go build
   ```
4. **Docker** — README: `docker run --rm gogost/gost -V` (image `gogost/gost` on Docker Hub).

### Config file
`-C <path>` loads a YAML (or JSON) config file; the repo ships a fully-annotated example at
`gost.yml` (repo root) showing the full object model: top-level `services`, `chains`, `hops`, plus
(per the API docs) `auther`, `bypass`, `admission`, `resolver`, `hosts`, `limiter`, `ingresses` as
separate top-level dynamic-config collections. A service definition looks like (from `gost.yml`):
```yaml
services:
- name: service-0
  addr: ":8080"
  handler:
    type: http
    auth: {username: user, password: pass}
  listener:
    type: tcp
    tls: {certFile: cert.pem, keyFile: key.pem, caFile: ca.pem}
```
Equivalent CLI form uses repeatable `-L` (listen) and `-F` (forward-chain node) flags, e.g.
`gost -L "http://:8080" -F "socks5://:1080"` (`gost.run/getting-started/quick-start/`).

### Ports for our scenario
For the reverse-tunnel use case (`gost.run/tutorials/reverse-proxy-tunnel/`), the relevant listener is
the **tunnel service**, e.g.:
```bash
gost -L "tunnel://:8443?entrypoint=:80&tunnel=.example.com:4d21094e-b74c-4916-86c1-d9fa36ea677b"
```
- `:8443` — the port local-client agents dial into (the "tunnel" listener/service port; can be TLS —
  use `tunnel+wss://` or add `tls:` under `listener` for TLS termination).
- `entrypoint=:80` — the public HTTP entrypoint port that public traffic hits, forwarded via the
  tunnel to whichever client owns the matching hostname (per `ingress` rules).
- **API port**: a separate `-api :18080` (or `api:` block in YAML) is the admin/control-plane HTTP
  API port (see next section) — this should be bound to a private/internal interface, not the public
  Internet, per `tutorials/api/overview/` (auth is only HTTP Basic, no TLS-specific hardening beyond
  standard reverse-proxy/firewalling).

### One-time global config
- **TLS**: set on the specific listener/service (`listener.tls.certFile/keyFile/caFile`, per `gost.yml`),
  or use `tunnel+wss://` scheme for WSS-based tunnels. No separate global TLS object — TLS is
  per-listener.
- **Admin account**: the `-api` flag / `api:` YAML block supports `user:pass@:18080` HTTP Basic Auth
  inline, or a full object:
  ```yaml
  api:
    addr: :18080
    pathPrefix: /api
    accesslog: true
    auth: {username: user, password: pass}
    auther: auther-0   # optional: reference a dynamically-configurable Auther object instead
  ```
  (`gost.run/tutorials/api/overview/`, "身份认证" section: "认证采用HTTP Basic Auth方式... 如果设置了
  `auther`选项，`auth`选项则会被忽略" — Basic Auth; if `auther` is set it overrides the static
  username/password.)
- **DB init**: gost has **no built-in database** — all dynamic objects (services, chains, authers,
  ingresses, quotas) live in-process in memory, mutated via the Web API, and optionally persisted back
  to `gost.json`/`gost.yaml` via `POST /config?format=yaml` (`tutorials/api/config/` "保存配置": "保存当前
  的配置到`gost.json`或`gost.yaml`文件"). There is no external DB dependency to provision.

### Wrapping as a service
No systemd unit is shipped upstream; standard pattern given a static binary + flat YAML config:
```ini
[Service]
ExecStart=/usr/local/bin/gost -C /etc/gost/gost.yml -api :18080
Restart=always
User=gost
```
Or `docker-compose.yml` with `restart: always` wrapping `gogost/gost -C /etc/gost/gost.yml`.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ gost server)

gost has a genuine, documented, dynamic **REST + Swagger admin API** (this is the strongest
differentiator vs. sish/piko for our use case) — this is exactly the "Web API" feature the README
lists: `[x] 动态配置 (Dynamic Configuration)` / `[x] Web API`.

- Enable via `-api :18080` (global mode) or as a normal service with `handler.type: api` (per-service
  mode, `tutorials/api/overview/` "普通服务"). Global mode means config-reload via the API never
  disrupts already-running services.
- **Swagger/OpenAPI spec is built into the binary and hosted online**: interactive docs at
  https://api.gost.run/swagger-ui/?url=/docs/swagger.yaml, and if you run your own instance with the
  API enabled on port 18080, you can browse the same UI against
  `https://api.gost.run/swagger-ui/?url=http://localhost:18080/docs/swagger.yaml` (docs explicitly
  say to switch the swagger scheme to HTTP for local testing).
- **Auth**: HTTP Basic (`user:pass@:18080` inline, or `auth:`/`auther:` object, see above). SvelteKit
  needs only a stored Basic-Auth credential (or ideally a dedicated `auther` allow-listing a
  service-account credential) to call the API — no OAuth/JWT flow on this side.
- **Dynamic object CRUD** (`tutorials/api/config/`, all under path prefix if `pathPrefix` configured,
  default none):
  - `GET /config` — full merged current config (`?format=json|yaml`).
  - `POST /config` — persist current in-memory config to `gost.json`/`gost.yaml` on disk.
  - `GET /config/services`, `GET /config/services/{name}` — list/inspect services.
  - `POST /config/services` — create a new service; **takes effect immediately, no restart**:
    ```bash
    curl http://user:pass@localhost:18080/config/services -d \
      '{"name":"service-0","addr":":8080","handler":{"type":"http"},"listener":{"type":"tcp"}}'
    ```
  - `PUT /config/services/{name}` — replace/update a service (**causes that service to restart**).
  - `DELETE /config/services/{name}` — stop and remove immediately.
  - Same CRUD pattern applies to `chains`, `authers`, `bypasses`, `admissions`, `resolvers`, `hosts`,
    `limiters`, and (3.3.0+) `quotas` (traffic-quota objects with `limit`, `startsAt`/`expiresAt`,
    `direction`, and `POST /config/quotas/{name}/reset` to zero usage — useful if we ever want to cap a
    device's bandwidth).
  - Objects support **forward references**: `tutorials/api/config/` "前向引用" — you can create a
    service referencing a chain name that doesn't exist yet, and it activates once that chain is
    later created. All objects are treated as immutable — updates replace the whole instance.
- **For our tunnel scenario specifically**, the object SvelteKit manages via this API is the
  **Ingress** (`gost.run/concepts/ingress/`), which maps hostname → tunnel-ID (endpoint), and which
  itself supports dynamic configuration through the same Web API (`Ingress支持通过Web API进行动态配置`).
  Concretely: SvelteKit would `POST`/`PUT` the ingress object (`/config/ingresses` — same CRUD pattern
  as services, not spelled out verbatim on the ingress page but implied by "通过Web API进行动态配置" +
  the general Config API doc covering "支持动态配置的对象有：服务，转发链，认证器，分流器，准入控制器，
  域名解析器，域名IP映射器，限速器，配额" — the ingress doc doesn't literally re-list the curl form for
  ingresses but confirms it's one of the dynamically configurable object kinds).
- **Issuing per-client tokens**: gost's "token" equivalent is the **tunnel ID** (a UUID) bound to a
  hostname in the Ingress. There's no separate bearer-token/secret object — knowledge of the tunnel
  UUID plus (optionally) an `auther`-validated username/password on the tunnel listener itself is what
  authorizes a client to bind. See next section.

---

## Client Linking Flow (access token exchange)

There is no "enrollment"/pairing endpoint in gost — the "identity" a local client presents is a
**tunnel ID (UUID)** it's configured with, passed as connector metadata, plus optional
listener/handler-level username+password auth (`auther`, dynamically configurable via the same Web
API as services).

Concrete flow, sourced from `gost.run/tutorials/reverse-proxy-tunnel/`:

1. **SvelteKit generates a UUID** for the new device (this is the "access token"/tunnel ID — plain
   UUID, no special crypto). Store it associated with the device/user row in our own DB.
2. **SvelteKit calls the gost admin API** to register that UUID as an Ingress rule mapping a hostname
   to the endpoint:
   ```bash
   curl http://user:pass@gost-host:18080/config/ingresses -d \
     '{"name":"ingress-0","rules":[{"hostname":"device-<id>.tunnels.ourapp.com",
        "endpoint":"<generated-uuid>"}]}'
   ```
   (Per the docs' warning: "隧道ID分配: 如果使用了Ingress，隧道将通过(虚拟)主机名进行路由，隧道的ID应当
   由服务端提前分配并记录在Ingress中。如果客户端使用了一个未在Ingress中注册的隧道ID，则流量无法路由到此
   客户端" — the tunnel ID *must* be pre-registered server-side or the client's connection, while
   accepted, is unroutable.)
3. **The local-client installer receives that UUID + the gost tunnel-service address** (e.g.
   `gost-host:8443`) from SvelteKit during the linking step (our own delivery mechanism — e.g. shown
   once in a "copy this config" UI, or fetched via our own authenticated `GET /api/device/config`
   endpoint — gost has no concept of this delivery step, it's 100% our glue).
4. **The client connects** using that UUID as `tunnel.id` connector metadata:
   ```bash
   gost -L rtcp://:0/127.0.0.1:8080 -F "tunnel://gost-host:8443?tunnel.id=<generated-uuid>&tunnel.weight=1"
   ```
   or YAML equivalent with `connector.type: tunnel`, `connector.metadata.tunnel.id: <uuid>`. `rtcp`
   here maps the local llama-server's port (e.g. `127.0.0.1:8080`) to be forwarded once traffic
   arrives via the tunnel.
5. **Optional stronger auth**: because the tunnel listener is a normal gost `service`, it can also
   carry `listener.auth`/`listener.auther` (HTTP Basic-style username/password, dynamically manageable
   via `POST /config/authers`) so a client must also present valid credentials in addition to knowing
   the UUID — recommended for production so the UUID alone (which travels over the wire in the
   connector query string) isn't the sole secret.
6. **High-availability tunnels**: multiple client processes can register with the *same* tunnel UUID
   for redundancy — "为了提高单个隧道的可用性，可以运行多个客户端，这些客户端使用相同的隧道ID... 采用
   加权随机方式选择一个客户端连接" (weighted-random selection among connections sharing a tunnel ID,
   up to 3 retries) — not directly relevant to 1-client-per-device but notable if we ever run HA
   local-client instances.

**Revocation**: `DELETE /config/ingresses/{name}` (or PUT to remove that specific rule) immediately
makes the hostname unroutable; `DELETE /config/authers/{name}` (or removing the specific user from
an auther's user list) revokes credential-based access. There's no separate "kill the live
connection" endpoint documented (unlike sish's `disconnectclient`) — removing the routing/auth entry
prevents *new* routing, but gost doesn't document a forced-disconnect API for already-established
tunnel connections in the pages reviewed.

---

## Request Routing (public traffic → local client)

Routing is **Ingress-hostname-based**, resolved per-request against the public entrypoint, per
`gost.run/tutorials/reverse-proxy-tunnel/`:

- The gost server runs a **tunnel service** (`tunnel://:8443?entrypoint=:80&...`) which owns two
  ports: the tunnel listener (8443, where clients dial in) and the `entrypoint` (80, where the public
  browses to). "本例中当流量进入公共入口点(服务端的80端口)后会嗅探流量信息获取所要访问的主机名，再通过
  主机名在Ingress中找到匹配的规则，获取对应的服务端点(endpoint即隧道ID)，最后在隧道的连接池中获取一个
  有效连接将流量通过此连接发送到客户端" — i.e. gost **sniffs the Host header (HTTP) or SNI (TLS)** on
  the entrypoint, looks up the hostname in the configured Ingress to get a tunnel/endpoint UUID, then
  picks a live connection from that tunnel's connection pool and forwards.
- Constraint: "公共入口点仅支持接收HTTP和带有SNI信息的TLS流量" — the public entrypoint only
  understands HTTP and SNI-bearing TLS; it cannot host raw-TCP-only protocols without a hostname
  signal (irrelevant for our llama-server-over-HTTP case).
- Ingress hostnames support wildcards (`.example.org` matches any subdomain), with a well-defined
  match-priority: exact match → wildcard on that host → progressively higher-level wildcard
  (`concepts/ingress/` "域名通配符" section).
- **Practical design for our scenario**: assign each device a fixed hostname convention
  (`device-<id>.tunnels.ourapp.com`) at link time, `POST` (or dynamically manage via file/redis/HTTP
  ingress data sources — see below) an Ingress rule mapping that hostname to the device's tunnel
  UUID. SvelteKit's job per new device is exactly one Ingress-rule write via the Web API — **yes,
  SvelteKit must call an API to bind hostname↔client**, and this is a first-class, clean, documented
  operation (unlike sish/piko where hostname-binding is either free-form client-chosen or done at the
  agent/Host-header level with no server-side "assign" step).
- **Ingress data source options** (`concepts/ingress/` "数据源"), beyond direct Web-API CRUD: inline
  YAML, flat file (hot-reloadable via `reload: 10s`), Redis hash/set, or an **HTTP plugin** where gost
  itself calls out to our backend per lookup: `GET http://ourapp/ingress?host=device-x.tunnels...` →
  `{"endpoint":"<uuid>"}`. This HTTP-plugin mode is an alternative to push-based Web-API writes: instead
  of SvelteKit writing routing rules into gost, gost pulls them from SvelteKit on demand. Either
  direction is legitimate; push-via-Web-API is simpler to reason about for our token-issuance flow
  since it keeps gost's Ingress state authoritative and explicit.

---

## Status Polling / Health Checks

The pages reviewed (`tutorials/api/overview/`, `tutorials/api/config/`) document the **config CRUD**
surface thoroughly but the specific "list currently connected tunnel clients / their live-connection
count" endpoint was **not found verbatim** in the crawled doc pages — the closest confirmed
primitives are:

- `GET /config` / `GET /config/services/{name}` — returns the **static configuration** of a service
  (whether an ingress/tunnel service is *defined*), not live connection/session state.
- The Prometheus metrics endpoint (`[x] Prometheus监控指标` in the README feature list, docs at
  `gost.run/tutorials/metrics/`, not fully crawled in this pass) is the most likely place gost exposes
  live per-tunnel connection counts/gauges — **not verified in detail**, flagged as an open question
  below.
- Quota objects (`tutorials/api/config/` "配额(Quota)") do expose live usage state via
  `GET /config/quotas/{name}` → `"status":{"used":..., "limit":..., "active":true, "blocked":false}` —
  if we attach a quota object per device, this gives us at least a traffic-based liveness/usage signal,
  but it's not a direct "is this SSH/tunnel-session open right now" boolean.
- **End-to-end health check** (same pattern as recommended for sish): have SvelteKit periodically
  `curl https://device-<id>.tunnels.ourapp.com/health` through the public entrypoint — if gost's
  ingress lookup + live tunnel connection + local llama-server all work, this succeeds; if the tunnel
  client is disconnected, gost will fail to find a live connection in the pool and the request will
  error/timeout. This is buildable today with 100% confidence regardless of the metrics-endpoint gap.

**This is the weakest-verified section of this plan** — I could not confirm a documented
"list connected tunnel clients + their liveness" REST endpoint equivalent to sish's
`/_sish/api/clients`; the Prometheus metrics page and the Swagger spec itself
(https://api.gost.run/swagger-ui/?url=/docs/swagger.yaml) were not deep-dived and likely contain the
authoritative answer (gost's Web API almost certainly has more read endpoints than the two doc pages
crawled here show — the docs explicitly point at the Swagger UI as the canonical reference, implying
the prose docs are not exhaustive).

---

## Open Questions / Assumptions

- **No live-connection/health list endpoint confirmed**: see Status Polling section — needs a
  follow-up pass directly against `https://api.gost.run/docs/swagger.yaml` (or a locally-run
  `gost -api :18080` instance's own `/docs/swagger.yaml`) to enumerate every route the API exposes,
  rather than relying on the two prose tutorial pages crawled here.
- **No forced-disconnect API confirmed**: unlike sish's `disconnectclient`, no gost endpoint to kill
  an already-open tunnel connection was found in the crawled docs — revocation as designed here is
  "remove the routing rule," which stops *new* traffic but may leave an existing tunnel session
  technically still connected until it naturally drops.
- **Ingress CRUD path unconfirmed verbatim for the `POST /config/ingresses` example** — the Ingress
  concept page confirms ingresses are dynamically configurable via the Web API in prose ("Ingress支持
  通过Web API进行动态配置") but doesn't show the literal curl example the way it does for services/
  chains/quotas; the URL pattern used above (`/config/ingresses`) is inferred by strict analogy to the
  documented `/config/services` / `/config/chains` pattern and should be confirmed against the Swagger
  spec before implementation.
- **TLS on the tunnel listener**: `tunnel+wss://` scheme exists (seen in the Wisper public-tunnel
  example: `gost -L rtcp://:0/... -F tunnel+wss://wisper.gost.run:443`), confirming WSS-wrapped tunnel
  transport is supported — good for our NAT/firewall-traversal requirement (works over 443 like a
  normal HTTPS websocket, no special client-side firewall allowances needed) — but a from-scratch
  server-side TLS cert wiring example for a *self-hosted* `tunnel+wss` listener (vs. the public Wisper
  demo instance) wasn't explicitly captured; use standard `listener.tls.certFile/keyFile` as shown in
  `gost.yml`, likely sufficient by extension of the general listener config, not individually verified
  for the tunnel handler type specifically.
- **Auther object shape** (username/password list format for `auther-0`) referenced by name throughout
  the docs but its own POST/PUT body schema wasn't independently pulled — needed before wiring
  per-device credential issuance if we choose to layer Basic-Auth on top of the tunnel-UUID scheme.
- **Version drift**: gost is under active development (3.3.0 confirmed via `cmd/gost/version.go`);
  Ingress/Quota features are explicitly marked with a "3.1.0"/"3.3.0" version-introduced badge in the
  docs, so pin to a specific release tag for production and re-verify the Swagger spec against that
  exact tag.

---

## Summary of custom glue code required

gost supplies: the tunnel server + entrypoint, hostname-based routing via a first-class dynamically
configurable **Ingress** object, and a genuinely complete **REST + Swagger Web API** for managing
services/chains/authers/ingresses/quotas at runtime with no restart. This is meaningfully more
"API-native" than sish or (per the piko plan) piko for our SvelteKit-driven control-plane use case:
issuing a new device link is a plain `POST /config/ingresses` call, not filesystem/callback glue.
What we still build ourselves: the actual "pairing UX" (UUID generation + delivery to the installer,
since gost has no enrollment concept of its own), the hostname-naming convention, and — pending the
Swagger-spec follow-up — likely a metrics/status polling integration for true live-connection health
beyond our own end-to-end HTTP health probe.
