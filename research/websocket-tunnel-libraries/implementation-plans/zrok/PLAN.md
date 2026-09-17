# zrok Implementation Plan

**Source verified against:** `git clone --depth 1 https://github.com/openziti/zrok` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/zrok` (default branch at clone time). Paths are
relative to that repo root unless a full URL is given.

Key sources used: `README.md`; `specs/src/*.yml` (hand-authored OpenAPI/Swagger source fragments
that generate `rest_server_zrok`/`rest_client_zrok`/`rest_model_zrok`, per `openapitools.json` and
the generated Python/Go/Node SDK docs under `sdk/*`); `website/docs/self-hosting/deployment/*.mdx`;
`docker/compose/zrok2-instance/*`; `sdk/golang/sdk/environment.go`, `sdk/golang/sdk/share.go`;
`cmd/zrok2/enable.go`. The underlying transport is **OpenZiti** (https://docs.openziti.io), a
zero-trust network overlay — confirmed in `README.md` "How it works": "zrok is built on OpenZiti...
No inbound connectivity required... Peer-to-peer connections... Identity-based access".

---

## Installation & Global VPS Configuration

zrok self-hosting ("zrok2") requires **three cooperating components**, confirmed in
`website/docs/self-hosting/deployment/docker.mdx`: an **OpenZiti overlay** (control plane +
router/data-plane), the **zrok2 controller** (the account/share/API server), and the **zrok2
frontend** (the public-facing HTTP(S) reverse proxy that serves shares), plus **PostgreSQL** (or
sqlite3) as the controller's datastore.

### Docker Compose install (documented, reproducible)
From `website/docs/self-hosting/deployment/docker.mdx`:
```bash
curl -sSfL https://get.openziti.io/zrok2-instance/fetch.bash | bash
cd zrok2-instance
# or, to build from source instead of pulling images:
git clone --depth 1 https://github.com/openziti/zrok.git
cd zrok/docker/compose/zrok2-instance
```
```bash
cp .env.example .env
# required vars:
#   ZROK2_DNS_ZONE=share.example.com        (wildcard *.share.example.com -> VPS IP)
#   ZROK2_ADMIN_TOKEN=<>=32 char secret>     (zrok2 admin API token)
#   ZITI_PWD=<OpenZiti controller admin pw>
docker compose up -d
docker compose logs -f zrok2-init   # first-run bootstrap of the OpenZiti overlay + zrok2 stack
docker compose ps
```
Files present in-repo confirming this: `docker/compose/zrok2-instance/compose.yml`,
`compose.build.yml` (build-from-source variant), `compose.caddy.yml` (TLS overlay),
`entrypoint-init.bash` (the bootstrap script), `.env.example`, `Caddyfile`, `fetch.bash`.

### Ports (from `docker.mdx` "Prerequisites")
- **3022** — OpenZiti router data plane; always required, direct TLS, cannot be proxied.
- **1280** — OpenZiti controller control plane; required if routers aren't co-located, mTLS,
  "Caddy cannot proxy this port".
- **443** — HTTPS for zrok2 controller + frontend via the optional Caddy overlay (recommended for
  production).
- **18080** — zrok2 controller API (`ZROK2_CTRL_PORT`, local-testing/insecure by default, binds
  `127.0.0.1` via `ZROK2_INSECURE_INTERFACE`).
- **8080** — zrok2 frontend (`ZROK2_FRONTEND_PORT`), same insecure-by-default binding.

### One-time global config (confirmed exact commands)
- **Admin account / DB init**: `ZROK2_ADMIN_TOKEN` env var bootstraps the admin API secret; the
  controller's Postgres schema is initialized automatically by `zrok2-init` on first
  `docker compose up -d` (no separate migration step documented).
- **First user account**:
  ```bash
  docker compose exec zrok2-controller zrok2 admin create account you@example.com yourpassword
  ```
  "The command prints an **enable token**. Save it — you'll need it to enable your environment."
  (`docker.mdx` Step 5). This is also exposed as the REST endpoint `POST /account`
  (`specs/src/admin.yml` `createAccount`, response `{"accountToken": "..."}`) and
  `POST /invite`/`POST /register` (`specs/src/account.yml`) for the self-service invite→register
  flow used on the public zrok.io service.
- **TLS**: optional Caddy overlay acquires a wildcard cert via DNS-01 challenge:
  ```bash
  # .env: CADDY_DNS_PLUGIN=cloudflare; CADDY_DNS_PLUGIN_TOKEN=your-api-token
  COMPOSE_FILE=compose.yml:compose.caddy.yml docker compose up -d
  ```
  Caddy then terminates TLS for `https://zrok2.share.example.com` (controller API) and the wildcard
  share frontend on 443, routing by subdomain (`docker.mdx` "Optional: Enable TLS with Caddy").
- **Verification**:
  ```bash
  curl -sf -H 'Accept: application/zrok.v1+json' http://127.0.0.1:18080/api/v2/versions
  ```

### Systemd / non-Docker alternative
`website/docs/self-hosting/deployment/linux.mdx` documents the same three-component stack
(OpenZiti + controller + frontend + Postgres) installed as native systemd units directly on a Linux
host, using the same `ZROK2_*` / `ZITI_*` environment variable names as the Docker Compose guide —
so the config surface (env vars) is identical whether wrapped in Docker or systemd.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ zrok server)

The zrok2 controller exposes a full REST API, generated from `specs/src/*.yml` into
`rest_server_zrok`/`rest_client_zrok` (Go), plus published Node.js, Python, and Go SDKs
(`sdk/nodejs`, `sdk/python`, `sdk/golang`) — this is the most complete programmatic surface of the
tools surveyed so far. Every operation requires the `X-TOKEN` header/API-key security scheme
(`security: - key: []` on nearly every path in `specs/src/*.yml`), confirmed concretely in
`sdk/golang/sdk/environment.go:15`: `httptransport.APIKeyAuth("X-TOKEN", "header",
root.Environment().AccountToken)`.

Relevant endpoint groups SvelteKit would call, all confirmed by path/operationId in the specs:

**Admin API** (`specs/src/admin.yml`, needs the admin token, i.e. `ZROK2_ADMIN_TOKEN`):
- `POST /account` (`createAccount`) — create a user/account, returns `accountToken`.
- `POST /identity` (`createIdentity`) — provision a raw OpenZiti identity + config, returns
  `{"identity": "<zId>", "cfg": "<ziti config JSON as string>"}`.
- `POST /frontend` (`createFrontend`) / `GET /frontends` — manage public-facing frontend records
  (`urlTemplate`, `permissionMode: open|closed`, `dynamic: bool`).
- `POST /invite/token/generate` — pre-mint invite tokens for the self-service register flow.
- `POST /namespace`, `/namespace/grant`, `/namespace/frontend/mapping` — multi-tenant routing
  namespaces (used for named shares behind a shared frontend, see section 4).

**Agent API** (`specs/src/agent.yml`) — **this is the standout feature for our use case**: it lets a
*controller-side* caller remotely drive an already-enrolled zrok client (the "agent") without
shelling into that machine:
- `POST /agent/enroll {envZId}` → `{"token": "..."}` — enroll a specific OpenZiti identity as a
  controllable agent.
- `POST /agent/share {envZId, shareMode, target, backendMode, accessGrants, ...}` → `{"token":
  "...", "frontendEndpoints": [...]}` — **remotely tell a specific client's agent to start sharing
  a local target** (e.g. `http://127.0.0.1:<llama-port>`), returning the public frontend URL(s).
  `backendMode` enum includes `proxy, web, tcpTunnel, udpTunnel, caddy, drive, socks`.
- `POST /agent/unshare {envZId, token}` — tear down a remote share.
- `POST /agent/access {envZId, token, bindAddress, autoMode, ...}` → `{"frontendToken": "..."}` —
  remotely bind a private share for consumption.
- `POST /agent/status {envZId}` → `{shares: [...], accesses: [...]}` — full remote status dump
  (see section 5).
- `POST /agent/ping {envZId}` → `{"version": "..."}` — liveness check; **502 "bad gateway; agent not
  reachable"** is the documented failure mode when the client's agent isn't connected
  (`specs/src/agent.yml:44-47, 100-103`), which is exactly the signal SvelteKit needs (section 5).
- `POST /agent/unenroll` — deprovision.

**Share API** (`specs/src/share.yml`, used when the client itself — not the controller — initiates
the share, i.e. the `zrok share` CLI/SDK path):
- `POST /share` (`shareRequest`/`shareResponse` schemas in `specs/src/definitions.yml`) — create a
  share from the client side.
- `POST /access` → `{"frontendToken": "...", "backendMode": "..."}` — bind a private share.
- `POST /share/name`, `PATCH /share/name`, `/share/names`, `/share/namespaces` — reserved/named
  shares (stable public names instead of random tokens — directly useful for a per-device stable
  hostname, section 4).
- `DELETE /unshare`, `DELETE /unaccess` — teardown.

**Environment API** (`specs/src/environment.yml`):
- `POST /enable {description, host}` → `{"identity": "<zId>", "cfg": "<ziti identity config>"}` —
  the actual client enrollment call (see section 3).
- `POST /disable {identity}` — deprovision an environment/client identity.

**What SvelteKit needs to authenticate**: the `X-TOKEN` header is an **account token** (for
account-scoped share/environment ops) or the **admin token** (for `admin.yml` ops, i.e. account/
frontend/namespace provisioning) — both are opaque bearer strings issued by the controller (account
token from `POST /account`/`POST /register`; admin token is the operator-configured
`ZROK2_ADMIN_TOKEN`). No OAuth/JWT — flat API-key auth throughout, confirmed by the uniform
`security: - key: []` clause across every spec file.

---

## Client Linking Flow (access token exchange)

zrok's linking flow is a genuine two-step **account-token → environment-identity** exchange, fully
traceable through both the CLI and the Go SDK:

1. **Account provisioning** (done once by SvelteKit/admin, not per-device): either
   `docker compose exec zrok2-controller zrok2 admin create account <email> <password>` (prints an
   **enable token**) or the self-service `POST /invite` → email link → `POST /register
   {registerToken, password}` → `{"accountToken": "..."}` (`specs/src/account.yml` `register`
   operation). The **account token** (or "enable token") is the pre-shared secret that gets handed
   to a new device.
2. **Environment enable (the actual pairing step)** — confirmed in
   `sdk/golang/sdk/environment.go:10-33`, function `EnableEnvironment(root, request)`:
   ```go
   auth := httptransport.APIKeyAuth("X-TOKEN", "header", root.Environment().AccountToken)
   ```
   which calls `POST /enable` (`specs/src/environment.yml`) with the account token as `X-TOKEN`.
   The controller responds `201` with `{"identity": "<OpenZiti zId>", "cfg": "<ziti identity JSON
   config>"}` — this **is** the pairing: the account token proves "this device belongs to this
   account", and in exchange the controller mints a brand-new OpenZiti identity (a private key +
   enrollment cert bound to the zrok/OpenZiti network) unique to that device.
   CLI equivalent, confirmed in `cmd/zrok2/enable.go:110-111`:
   ```go
   apiEndpoint, _ := env.ApiEndpoint()
   env.SetEnvironment(&env_core.Environment{
       AccountToken: token, ZitiIdentity: resp.Payload.Identity, ApiEndpoint: apiEndpoint,
   })
   ```
   i.e., after enable, the local environment config on disk stores the **account token +
   the assigned OpenZiti identity**, not just a flat shared secret — every subsequent API call
   authenticates with that same account token, and every subsequent **data-plane** connection
   authenticates via the newly minted OpenZiti identity (mTLS-based, since OpenZiti is a
   zero-trust overlay — no plaintext shared secret ever crosses the data plane).
   CLI usage matching README quick-start: `zrok invite` (get account) → `zrok enable <token>`
   (pair this machine).
3. **Per-device uniqueness**: because step 2 mints a *new* OpenZiti identity per `enable` call, each
   physical/local-client machine that runs `zrok2 enable <token>` gets its own distinct `zId` even
   though they may share one account token — this `zId` (`envZId` in the Agent API, section 2) is
   the durable per-device identifier SvelteKit should store and use for all subsequent agent/status
   calls.
4. **Revocation**: `POST /disable {identity}` (or CLI `zrok disable`) — the controller invalidates
   that specific OpenZiti identity; `POST /agent/unenroll {envZId}` similarly de-registers the agent
   control channel for that device. Either is a clean per-device revoke without touching the shared
   account token or other devices.

**For our aipotluck use case**, the natural mapping is:
- SvelteKit backend holds one zrok admin/account token server-side.
- On new-device install, SvelteKit calls `POST /enable` (or admin-side `POST /identity` for a raw
  OpenZiti identity without the account-scoped wrapper) **on behalf of** the new device and ships
  the resulting `{identity, cfg}` down to the local-client installer over our own authenticated
  channel — the installer never needs to see the account token directly, only the resulting
  device-scoped `cfg`. This mirrors the "SvelteKit does the pairing centrally, device receives only
  a scoped credential" pattern we want.
- Then SvelteKit calls `POST /agent/enroll {envZId}` to make that identity remotely controllable,
  and subsequently `POST /agent/share` to point it at the local llama-server port — all from the
  SvelteKit backend, no manual action needed on the device beyond running the zrok agent process
  with the provisioned identity config.

---

## Request Routing (public traffic → local client)

zrok/OpenZiti routing is **name/token-based via the frontend**, not raw subdomain-per-client by
default — but named shares give an equivalent stable-hostname experience:

- A **share** (`POST /share` or `POST /agent/share`) gets an auto-generated random `token`
  (`shareResponse` in `specs/src/definitions.yml`) unless a **reserved name** is used
  (`POST /share/name {namespaceToken, name}`, `specs/src/share.yml:127-155`) — reserved names give a
  stable, predictable public identifier instead of a rotating random token, exactly analogous to
  choosing a fixed subdomain per device.
- The **frontend** (`admin.yml` `createFrontend`, `urlTemplate` field) defines the URL pattern that
  maps a share token/name to a public URL — e.g. the Docker guide's own end-to-end test:
  ```bash
  zrok2 create name mytest
  zrok2 share public http://127.0.0.1:8080 --name-selection public:mytest
  curl -sf http://mytest.share.example.com:8080/
  ```
  (`docker.mdx` Step 7) — confirms the actual routing is **subdomain-based** in the default
  frontend configuration (`mytest.share.example.com`), driven by the reserved name, with the
  frontend dynamically dispatching by Host header to whichever OpenZiti identity currently holds
  that name's live share — see `website/docs/self-hosting/frontends/dynamic-proxy.md` (dynamic
  frontend routing) confirming this is a first-class "dynamic proxy" frontend mode, not a static
  per-share nginx config.
- **Does SvelteKit need to call an API to bind a hostname to a client?** Yes, exactly once at
  provisioning time (not per-request): `POST /share/name` reserves the name in a namespace, then
  the device's own `zrok share public <target> --name-selection <namespace>:<name>` (or the
  controller-driven `POST /agent/share` with a `nameSelections` array field, confirmed present in
  `specs/src/agent.yml:130-133`) attaches that reserved name to the live share. After that, all
  public HTTP traffic to `https://<name>.<ZROK2_DNS_ZONE>` is routed automatically by the frontend
  over the OpenZiti mesh to the specific device — no additional API call needed once the share is
  live, since the identity-to-name binding persists until unshared.
- Underlying transport: unlike simple reverse-tunnel tools, the actual bytes travel over an OpenZiti
  overlay circuit (mutually authenticated, end-to-end encrypted, "peer-to-peer connections between
  users when possible" per README) — the frontend is one edge of that circuit, the device's zrok
  agent/share process is the other; there is no raw TCP/SSH tunnel exposed anywhere.

---

## Status Polling / Health Checks

Multiple concrete, complementary mechanisms, all confirmed in specs:

1. **`POST /agent/status {envZId}`** (`specs/src/agent.yml:213-297`) — the single richest health
   endpoint: returns `shares: [{token, shareMode, backendMode, frontendEndpoints, backendEndpoint,
   open, status, failure: {id, count, lastError, nextRetry}}]` and `accesses: [{frontendToken,
   token, bindAddress, status, failure: {...}}]`. This gives **per-share health with retry/backoff
   state**, not just a boolean — ideal for a device-status dashboard (e.g. surfacing "share is
   flapping, 3 consecutive failures, next retry at T").
2. **`POST /agent/ping {envZId}`** — lightweight liveness probe; returns `{"version": "..."}` on
   `200`, and explicitly documents **`502 bad gateway; agent not reachable`** as the failure mode
   (`specs/src/agent.yml:100-103`) — this is the cleanest binary "is this specific client's zrok
   agent process currently connected" signal, directly analogous to what we need for
   connect/disconnect polling.
3. **`POST /agent/share/http-healthcheck`** (`specs/src/agent.yml:173-211`) — remotely instructs the
   agent to perform an **end-to-end HTTP health check against its own local backend** (the
   llama-server) and returns `{"healthy": bool, "error": "..."}`. This is a native, built-in
   equivalent of "probe the local service through the tunnel" that other tools in this survey had to
   build as custom glue — zrok provides it as a first-class agent RPC.
4. **CLI equivalent**: `zrok status` (implied by the `remoteStatus` operationId and general zrok CLI
   command surface) shows the same share/access list locally on the device for operator
   troubleshooting.

**Recommended combination**: SvelteKit polls `POST /agent/status {envZId}` per device on an
interval for the rich status view (including failure/retry detail for alerting), and can invoke
`POST /agent/share/http-healthcheck` on-demand (e.g. before routing a user request, or for an
active "test this device now" button) to confirm the actual llama-server is responsive end-to-end,
not just that the OpenZiti circuit is up. `POST /agent/ping` is the cheapest option for a simple
green/red dot.

---

## Open Questions / Assumptions

- **Agent process deployment model not fully traced**: the Agent API (`specs/src/agent.yml`)
  clearly implies a persistent "zrok agent" process running on the device that the controller can
  remotely command, but I did not locate the agent's own daemon source (likely under `agent/` or
  `canary/enabler.go`/`controller/enable.go`, which appeared in the initial file search) to confirm
  exactly how the agent process is installed/started on a brand-new machine (systemd unit? does
  `zrok2 enable` also start the agent automatically, or is `zrok agent enable`/`zrok agent start` a
  separate step?). This matters for our local-client daemon integration — need a follow-up source
  read of `cmd/zrok2/*agent*.go` before finalizing the local-client wrapper design.
- **`envZId` vs `token` field overload**: the Agent API mixes `envZId` (environment/OpenZiti
  identity ID) and `token` (share token) parameters across calls in ways that weren't fully cross-
  checked against `rest_model_zrok` Go structs — recommend generating/vendoring the official Go
  client (`rest_client_zrok`) rather than hand-rolling HTTP calls from SvelteKit, to avoid subtle
  schema mismatches.
- **Namespace/frontend model complexity**: the admin API's namespace/frontend-mapping system
  (`specs/src/admin.yml` `/namespace`, `/namespace/frontend/mapping`, `/frontend/namespace/mapping`)
  is clearly built for multi-tenant SaaS-style deployments (matching zrok.io's own public service)
  and is more complex than we likely need for a single-operator deployment; the docker-compose quick
  start's `zrok2 create name mytest` path may be sufficient without touching namespaces directly —
  needs a design decision on whether to adopt namespaces or use the simpler single-default-frontend
  path.
- **No explicit "force disconnect a specific device" endpoint found** distinct from `/agent/unshare`
  (kills one share) and `/disable`/`/agent/unenroll` (kills the whole identity) — for our use case
  this is probably fine (unenroll/disable is the natural "revoke this device" action), but worth
  confirming there's no lighter-weight "pause without deprovisioning" verb.
- **Version/branch drift risk**: this analysis targeted what the repo calls "zrok2" (compose service
  names `zrok2-controller`, CLI binary `zrok2`, env vars `ZROK2_*`) which appears to be a newer
  generation alongside legacy `zrok`/`ZROK_*` naming still present elsewhere in the tree (`cmd/`
  contains both `zrok` overlap and `zrok2`-specific files like `cmd/zrok2/enable.go`) — confirm which
  generation is the current stable/recommended one before committing to the zrok2-specific API
  surface documented here, since docs versioning (`website/zrok_versioned_docs/version-1.0` vs
  `version-1.1`) suggests active in-flight changes between versions.
