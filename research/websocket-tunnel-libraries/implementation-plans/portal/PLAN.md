# Portal (gosuda/portal-tunnel) Implementation Plan

**Source verified against:** `git clone https://github.com/gosuda/portal-tunnel` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/portal` (main branch, v2 line, at clone time).
Key files/docs cited: `README.md`, `docs/src/routes/deployment/+page.md`,
`docs/src/routes/self-hosting/+page.md`, `docs/src/routes/configuration/+page.md`,
`docs/src/routes/portal-agent/+page.md`, `docs/src/routes/api-reference/+page.md`,
`docs/src/routes/siwe-authentication/+page.md`, `docs/src/routes/security-model/+page.md`,
`docker-compose.yml`, `cmd/portal-tunnel/docker-compose.yml`, `cmd/portal-tunnel/README.md`,
`types/api.go`, `types/paths.go`, `sdk/api_client.go`, `sdk/expose.go`,
`portal/identity/register_challenge.go`.

Portal is architecturally very different from sish/wstunnel/sish-style SSH tunnels: it is a
purpose-built relay+SDK with **wallet-style (SIWE/secp256k1) identity instead of usernames or
static tokens**, a documented JSON control-plane under `/sdk/*` and `/api/*`, and a Go SDK
(`sdk/`) rather than a raw CLI-only client. This is the strongest-fit finalist so far for "real
programmatic control-plane" among the four second-tier tools evaluated.

---

## Installation & Global VPS Configuration

Portal ships **one Go binary/image that is both the relay (cloud) and the tunnel client**, built
from the same repo with two different `cmd/` entrypoints: `cmd/relay-server` (the cloud relay) and
`cmd/portal-tunnel` (the local `portal` CLI/agent). Only the relay matters for "installation on a
cloud VPS as a managed service."

**1. Docker (recommended, documented in both `docs/src/routes/self-hosting/+page.md` and
`docs/src/routes/deployment/+page.md`):**

```bash
mkdir -p ./relay-data
docker run -d \
  --name portal-relay \
  --restart unless-stopped \
  --cap-add NET_BIND_SERVICE \
  -p 443:443 -p 53:53/tcp -p 53:53/udp \
  -e PORTAL_URL=https://relay.example.com \
  -e IDENTITY_PATH=/portal-certs \
  -e ADMIN_TOKEN="$(openssl rand -hex 32)" \
  -v $(pwd)/relay-data:/portal-certs \
  ghcr.io/gosuda/portal:2
```
(`docs/src/routes/self-hosting/+page.md` "Quick Start"). `NET_BIND_SERVICE` is required because the
relay binds privileged ports 443/53 as non-root inside the container.

**2. Docker Compose (production-grade, from the repo root `docker-compose.yml`):**
```bash
git clone https://github.com/gosuda/portal-tunnel
cd portal-tunnel && cp .env.example .env   # edit PORTAL_URL, ADMIN_TOKEN, etc.
docker compose up -d
```
The bundled `docker-compose.yml` publishes exactly `443/tcp` (HTTPS + SPA + `/sdk/*`/`/api/*` APIs
+ SNI tunnel ingress) and `53/tcp`+`53/udp` (embedded authoritative DNS) — confirmed table in
`docs/src/routes/deployment/+page.md` "Compose stack publishes." Port `80/tcp` is optional
(`HTTP_REDIRECT_ENABLED`). The internal admin/API listener defaults to `4017/tcp`
(`API_PORT`) and is **not published**; root-host traffic reaches it internally through Portal's own
SNI router (`docs/src/routes/deployment/+page.md` "Production Topology").

**3. Build from source:** `go build` the module (root `go.mod`), or `docker compose up -d --build
--force-recreate portal` (deployment doc "Deploy" section) for a local source build. No separate
build-from-source path is emphasized in docs beyond Docker; Docker/Compose is the documented
default for the relay.

**One-time global config — `.env` (deployment doc "Configuration" section):**
```dotenv
PORTAL_URL=https://portal.example.com
ADMIN_TOKEN=replace-with-a-long-random-value
LANDING_PAGE_ENABLED=false
DISCOVERY=false
BOOTSTRAPS=
IVNP_CONFIG=
ACME_DNS_PROVIDER=
EMBEDDED_DNS_PORT=53
```
- **"Admin account"**: there is no admin *user* concept, only a single shared bearer secret,
  `ADMIN_TOKEN` (env var / `--admin-token` flag), used for all `/api/admin/*` and `/api/policy/*`
  calls (`docs/src/routes/configuration/+page.md` "Admin" table; `types/api.go`
  `AdminAuthLoginRequest`/`AdminAuthLoginResponse`). This is effectively a single-tenant root
  password, not a multi-admin RBAC system.
- **TLS**: fully automatic. Default `ACME_DNS_PROVIDER=embedded` runs an authoritative DNS server
  *inside* the relay process; you do a one-time NS delegation (`NS portal.example.com ->
  ns.portal.example.com` + glue `A` record) and Portal self-issues ACME DNS-01 certs for the root
  host and every tunnel subdomain with no per-tunnel DNS work
  (`docs/src/routes/deployment/+page.md` "Prerequisites"/"Embedded DNS" in
  `docs/src/routes/configuration/+page.md`). External DNS providers (`cloudflare`, `gcloud`,
  `route53`, `hetzner`, `njalla`, `vultr`) are first-class alternatives if you don't want to
  delegate a zone to Portal's embedded DNS. Manual certs (`fullchain.pem`/`privatekey.pem` dropped
  into `IDENTITY_PATH`) are also supported as an override.
- **"DB init"**: there is no separate database. All relay state (identity keypair, ACME account,
  DNSSEC signing key, policy settings) lives as flat files under `IDENTITY_PATH` (default
  `./.portal-certs`): `identity.json`, `policy.json`, `dnssec-csk.json`, `acme-account.key`, TLS
  material. This directory **must be persisted/backed up as a volume** — losing it changes the
  relay's identity and DNSSEC chain (`docs/src/routes/deployment/+page.md` "Configuration").

**systemd wrapping**: not natively documented (Portal's own docs assume Docker/Compose), but since
it's a single static Go binary you can trivially wrap the relay binary:
```ini
[Service]
ExecStart=/usr/local/bin/relay-server --portal-url https://portal.example.com --admin-token ${ADMIN_TOKEN}
Restart=always
```
`relay-server config` (run inside the container: `docker compose run --rm -T portal config`) prints
every effective env var, its source, and validation errors — useful for a systemd-based deploy to
sanity-check config before going live (`docs/src/routes/configuration/+page.md` "Checking Relay
Configuration").

**Reverse-proxy caveat**: if 443 is already owned by another service on the VPS, Portal documents a
full nginx-stream-passthrough topology (`docs/src/routes/deployment/+page.md` "Running Behind an
Existing Reverse Proxy") — SNI-based `stream{}` passthrough, not HTTP proxying, because terminating
tenant TLS in front of Portal breaks the MITM self-probe and ECH. This is a real, documented,
tested config (`docs/static/examples/reverse-proxy/`), not a guess.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ Portal server)

Portal exposes a genuine, documented, versioned JSON API — this is the standout feature versus the
sish/wstunnel-tier tools. Full canonical reference:
`docs/src/routes/api-reference/+page.md`. All matched JSON endpoints return one envelope:
```json
{ "ok": true, "data": { } }
```
or on error `{ "ok": false, "error": { "code": "...", "message": "..." } }`.

**Auth schemes** (`docs/src/routes/api-reference/+page.md` "Auth Schemes" table):

| Name | Used by | Header/body |
|---|---|---|
| Admin bearer | admin/policy API | `Authorization: Bearer <ADMIN_TOKEN>` |
| Lease token header | keyless signer, datagram backhaul | `X-Portal-Access-Token: <access_token>` |
| Reverse capability header | reverse stream | `X-Portal-Reverse-Capability: <capability>` |
| Lease token body | renew/unregister | JSON field `access_token` |
| SIWE signature | lease registration | body fields on `/sdk/register` |

**What SvelteKit would actually call, and with what auth:**

1. **Read the relay's public/leased-tunnel state** (no auth): `GET /api/state` →
   `PublicStateResponse{ leases: Lease[], landing_page_enabled }`. Each `Lease` has `name`,
   `expires_at`, `first_seen_at`, `last_seen_at`, `hostname`, `udp_enabled`, `tcp_enabled`,
   `tcp_addr`, `metadata`, `ready` (a numeric readiness flag). This is the primary "list
   tunnels/services" read endpoint (`types/api.go` `Lease`, `PublicStateResponse`).
2. **Admin/policy control** (needs `Authorization: Bearer <ADMIN_TOKEN>`):
   - `GET /api/policy/state` → `PolicyStateResponse{ policy: PolicySettings, leases:
     PolicyLease[] }`. `PolicyLease` extends `Lease` with `identity_key`, `address`, `bps`,
     `client_ip`, `reported_ip`, `is_approved`, `is_banned`, `is_denied`, `is_ip_banned` — this is
     the richest per-client status view and is exactly what SvelteKit's admin dashboard would poll.
   - `POST /api/policy/leases` with `LeasePolicyUpdate{ identity_key, is_banned?, is_approved?,
     is_denied?, bps? }` — approve/ban/rate-limit a specific client by its `identity_key` (a
     normalized `name:address` string).
   - `POST /api/policy/ips` with `IPPolicyUpdate{ ip, is_banned }` — IP-level banning.
   - `GET`/`POST /api/policy` for global `PolicySettings{ approval_mode: "auto"|"manual",
     landing_page_enabled, udp: PolicyPortSettings, tcp_port: PolicyPortSettings }`. Setting
     `approval_mode: "manual"` means a newly-registering lease/client is **not** routable until an
     admin (or our SvelteKit backend acting as admin) calls `POST /api/policy/leases` with
     `is_approved: true` — this is the closest thing Portal has to a first-class
     "approve-this-new-client" API call.
3. **Admin login/session** (if SvelteKit wants a short-lived session instead of embedding the raw
   admin token everywhere): `POST /api/admin/auth/login` with `AdminAuthLoginRequest`(token) →
   `AdminAuthLoginResponse`; `GET /api/admin/auth/status`; `POST /api/admin/auth/logout`
   (`types/paths.go` `PathAdminAuthLogin`/`PathAdminAuthStatus`/`PathAdminLogout`). Not required —
   SvelteKit's backend can just always send `Authorization: Bearer <ADMIN_TOKEN>` directly since it
   is itself a trusted server-side actor, not a browser.

**Crucially, SvelteKit does NOT register tunnels on the client's behalf.** Registration
(`/sdk/register/challenge` + `/sdk/register`) is performed **by the local Portal SDK/CLI itself**
using its own locally-generated identity key — see Client Linking Flow below. SvelteKit's role is
purely: (a) read lease/policy state to show dashboards, (b) set global policy
(`approval_mode`) and per-lease approve/ban/rate-limit decisions, (c) optionally require our own
separate access-token gate before ever letting a new local-client identity register at all (since
Portal itself has no accounts and will register *any* self-generated wallet identity by default
under `approval_mode: "auto"`).

---

## Client Linking Flow (access token exchange)

This is Portal's most distinctive design point: **there are no shared pre-issued tokens for
tunnel registration at all — the local client generates its own secp256k1 keypair and
self-signs a SIWE (Sign-In-With-Ethereum) message to register.** Confirmed by
`docs/src/routes/siwe-authentication/+page.md`, `portal/identity/register_challenge.go`, and
`sdk/api_client.go`:

1. On first run, `portal expose` or `portal agent` loads/creates `identity.json`
   (`docs/src/routes/configuration/+page.md` "`identity.json`"). It contains either a raw
   secp256k1 `private_key` or a BIP-39 `mnemonic` + `derivation_path` (default
   `m/44'/60'/0'/0/0`), from which an EVM-style `address` and `public_key` are derived. This file
   is generated **entirely locally, with no network call** — Portal has no registration/pairing
   endpoint that hands out an identity.
2. The client's SDK calls `POST /sdk/register/challenge` with `RegisterChallengeRequest{ identity:
   {name, address}, metadata, ttl, cache, overlay, udp_enabled, tcp_enabled, ... }` (no auth
   needed — `types/paths.go` `PathSDKRegisterChallenge`). The relay responds with
   `RegisterChallengeResponse{ challenge_id, expires_at, siwe_message }`, where `siwe_message` is a
   fully formatted SIWE text challenge with statement `"Register a portal lease"`, a random nonce,
   and the challenge ID embedded as the SIWE `Request ID` field
   (`portal/identity/register_challenge.go` `NewRegisterChallenge`).
3. The SDK signs that exact SIWE message text with its local secp256k1 private key
   (`authority.SignEthereumPersonalMessage(challenge.SIWEMessage)` in `sdk/api_client.go`
   `register()`) — this is a standard Ethereum personal-sign over the SIWE message, no browser
   wallet or MetaMask involved; it's just secp256k1 ECDSA done in-process.
4. The SDK calls `POST /sdk/register` with `RegisterRequest{ challenge_id, siwe_message,
   siwe_signature, reported_ip }`. The relay verifies the signature against the address in the
   original challenge (`RegisterChallenge.Verify()`, `identity/register_challenge.go`) and, on
   success, returns `RegisterResponse{ identity, expires_at, access_token, reverse_endpoint:
   {url, capability, expires_at, overlay}, sni_port, udp_addr/tcp_addr if enabled }`.
   `access_token` is an **ES256K (secp256k1-signed) lease-scoped bearer token**
   (`docs/src/routes/security-model/+page.md` "Identity" — "the relay then issues a lease-scoped
   ES256K access token used by renew, unregister, keyless signing, and QUIC datagram
   authentication, plus a separate reverse-only capability for reverse streams"). This
   `access_token` is what subsequently authorizes `POST /sdk/renew`, `POST /sdk/unregister`, and
   `POST /v1/sign` (keyless TLS signer) via the "lease token body"/"lease token header" auth
   schemes.
5. Renewal loop: `POST /sdk/renew` with `RenewRequest{access_token,...}` before lease expiry to
   keep the lease/hostname alive; `POST /sdk/reverse` with `ReverseEndpointRequest{access_token,
   failed_url}` to get a fresh reverse-connection endpoint if the current one fails.

**So "pairing" in Portal's native model is unauthenticated-by-identity, not token-gated**: any
process that can reach the relay's public HTTPS endpoint can generate a wallet identity and
self-register a lease, subject only to the relay's global `approval_mode` policy (`auto` allows it
immediately; `manual` requires our SvelteKit backend to subsequently call `POST /api/policy/leases
{identity_key, is_approved:true}` via the admin API in section 2 before that lease actually
routes traffic — `types/api.go` `LeasePolicyUpdate`, `PolicySettings.approval_mode`).

**Adapting this to our access-token pairing UX** (this part is glue code we build, mirroring the
sish-tier pattern used for the other finalists):
1. Local-client installer runs `portal agent` for the first time; it auto-generates
   `identity.json` and thus a stable Ethereum-style address for that machine
   (`docs/src/routes/portal-agent/+page.md` "Identity Layout").
2. During onboarding, the user pastes a **one-time access/pairing token issued by our SvelteKit
   backend** (our own invention, same pattern as the other finalists) into the installer.
3. The installer POSTs `{pairing_token, portal_identity_address}` to our own SvelteKit endpoint
   (not a Portal API). SvelteKit validates the pairing token, records the mapping
   `device_id -> portal_identity_address` in our DB, and — if the relay's global policy is
   `manual` — calls Portal's admin API `POST /api/policy/leases {identity_key: "<name>:<address>",
   is_approved: true}` to whitelist that specific identity before the client's `portal expose`/
   `portal agent run` call is even allowed to route.
4. From then on the local client just runs `portal agent run --config config.toml` normally — no
   further use of our pairing token; Portal's own SIWE lease/renewal cycle handles ongoing
   reconnection authentication automatically.
5. Revocation: SvelteKit calls `POST /api/policy/leases {identity_key, is_denied: true}` (which
   "also revokes approval" per `types/api.go` `LeasePolicyUpdate` doc comment) or `is_banned:
   true`, immediately blocking that identity from routing on future connects/renewals.

**Local Control API (loopback-only), a second, separate auth surface worth noting:** `portal
agent` also runs a **local, loopback-only** control API (`127.0.0.1:4018` by default,
`docs/src/routes/configuration/+page.md` "Local Control API" in the agent doc) with its own random
bearer token stored in `<state_dir>/agent-endpoint.json`. This is used by `portal agent
dashboard`/CLI to manage the local agent process (add/update/delete tunnels, connect/disconnect
relays) and is **not reachable from SvelteKit remotely** — it's local-machine-only IPC, useful only
if our monitor service itself drives the local `portal agent` process programmatically
(`POST /agent/tunnels`, `PATCH /agent/tunnels/{id}`, etc. — see `docs/src/routes/portal-agent/
+page.md` "Local Control API" table) rather than editing `config.toml` directly.

---

## Request Routing (public traffic → local client)

Routing is **subdomain-based** and is driven by TLS SNI at the relay, not any REST call from
SvelteKit:

- The `name` field chosen at registration time (CLI `--name`, or `config.toml` `[[tunnels]] name
  = "myapp"`) becomes the DNS label: `https://myapp.portal.example.com`. If omitted, Portal
  auto-generates one (`docs/src/routes/configuration/+page.md` "Lease" flags table, `--name`
  "auto-generated when omitted").
- Once registered, the relay's embedded authoritative DNS server (or the configured external
  provider) synthesizes the A record for `<name>.portal.example.com` automatically — **no
  wildcard DNS record is needed and no separate DNS API call from SvelteKit is required**
  (`docs/src/routes/self-hosting/+page.md` "DNS Configuration": "No wildcard record is needed: the
  relay synthesizes answers for every tunnel hostname").
- At the network level (`docs/src/routes/security-model/+page.md`, `docs/src/routes/deployment/
  +page.md` "Production Topology"): the relay's `:443` SNI router reads only the TLS ClientHello's
  SNI, and for a registered lease hostname bridges the raw encrypted bytes over the already-open
  reverse session (opened by the client via `GET /sdk/connect` using the `X-Portal-Reverse-
  Capability` header capability minted at registration) straight through to the SDK's local tenant
  TLS terminator, which decrypts locally and forwards plaintext to the local service (e.g.
  `llama-server` on `127.0.0.1:8080`).
- **Routed HTTP mode** lets one lease/hostname multiplex several local upstreams by path prefix,
  configured entirely client-side, with no relay-side routing API call:
  ```bash
  portal expose --name myapp \
    --http-route /api=http://127.0.0.1:3001 \
    --http-route /=http://127.0.0.1:5173
  ```
  or in `config.toml`:
  ```toml
  [[tunnels.http_routes]]
  prefix = "/api"
  upstream = "http://127.0.0.1:3001"
  ```
  For our use case, `target = "127.0.0.1:<llama-server-port>"` (a single plain TCP target) is
  simpler than `http_routes` unless we need path-based multiplexing under one hostname.
- **SvelteKit never calls an API to "bind a hostname to a client ID."** The hostname is chosen
  and owned entirely by the local client's own registration call (`name` field); SvelteKit's only
  lever is approve/deny/ban that identity via the policy API (section 2), not hostname assignment.
  For a predictable per-device hostname convention, we would fix `name = "device-<uuid>"` in each
  device's generated `config.toml` at pairing time (glue code, not a Portal feature).
- TCP/UDP raw ports are also supported (`--tcp`, `--udp`) allocated from a configured
  `MIN_PORT`-`MAX_PORT` range if we ever need raw (non-HTTP) exposure of `llama-server`'s port —
  not needed for a standard HTTP inference endpoint.

---

## Status Polling / Health Checks

Two real, documented signals, both via the same `/api/*` control-plane already covered:

1. **`GET /api/state`** (no auth) → `PublicStateResponse.leases[]`; each `Lease` includes
   `first_seen_at`, `last_seen_at`, and `ready` (numeric readiness). A lease with a recent
   `last_seen_at` and `ready > 0` is a connected, healthy client; a stale `last_seen_at` or an
   absent lease (removed after expiry) means disconnected. `hide: true` in a lease's metadata
   would remove it from this public listing though (`LeaseMetadata.hide`), so an admin-side check
   is more reliable for internal dashboards.
2. **`GET /api/policy/state`** (admin bearer) → `PolicyStateResponse.leases[]` as `PolicyLease`,
   which is strictly richer: adds `identity_key`, `address`, `bps` (current rate limit),
   `client_ip`, `reported_ip`, `is_approved`, `is_banned`, `is_denied`, `is_ip_banned`. This is the
   right endpoint for SvelteKit's per-device status dashboard: filter the returned array by
   `identity_key` (which we stored at pairing time) to get that exact device's live connection and
   policy state in one call.
3. **`GET /sdk/domain`** (no auth) returns `DomainResponse{protocol_version, release_version,
   cache limits}` — a relay liveness/compatibility probe (used internally by the SDK's own
   bootstrap, `sdk/api_client.go` `initHTTPTransport`), and `GET /api/healthz` → `{"status":"ok"}`
   is the plain relay-process health check (`docs/src/routes/api-reference/+page.md` "Public"
   table) — useful for our own uptime monitoring of the relay itself, not per-device status.
4. There is **no webhook/push notification** of connect/disconnect events documented anywhere in
   the API reference or source — this is poll-only, same conclusion as the sish plan. Recommended
   polling interval: the built-in `portal agent dashboard` polls its own local control API "every
   two seconds" (`docs/src/routes/portal-agent/+page.md` "Dashboard") as a reference cadence; for
   our relay-side polling, 15-30s against `/api/policy/state` is reasonable given it's a single
   JSON GET with admin bearer auth and no per-request relay-side cost beyond serving current state.
5. **End-to-end health**: as with the other finalists, the most meaningful signal is our own
   periodic `GET https://device-<id>.portal.example.com/health` HTTP probe through the live
   tunnel, since `ready`/`is_approved` on the relay only proves the reverse session exists, not
   that `llama-server` behind it is actually serving. Portal provides no built-in equivalent.

---

## Open Questions / Assumptions

- **Approval-mode default is `auto`.** Out of the box any self-generated identity can register and
  immediately route without any admin action (`PolicySettings.approval_mode` defaults are not
  explicitly stated as `manual`, and self-hosting docs never mention setting it). For our
  access-token-gated linking flow we must explicitly set `approval_mode: "manual"` via `POST
  /api/policy` at relay setup time and always pre-approve identities ourselves in the pairing flow
  — otherwise **any third party could register a rogue lease on our relay** using nothing but a
  freshly generated wallet keypair. This needs to be a hard requirement in the SvelteKit
  integration, not optional hardening.
- **Rate limiting / DoS surface**: since registration requires no credential beyond a
  self-generated keypair (SIWE challenge/response is free to request repeatedly), an attacker could
  spam `/sdk/register/challenge` — not evaluated whether Portal has built-in registration-rate
  limiting distinct from `PolicyPortSettings.max_leases` (which caps concurrent UDP/TCP port
  leases, not HTTP lease count generally). Should be checked against relay logs/behavior before
  production use.
- **Policy update propagation latency**: not explicitly documented whether `is_denied`/`is_banned`
  via `POST /api/policy/leases` takes effect on the *current* open reverse session immediately or
  only on the next renew/reconnect cycle — assume "next renew" unless verified otherwise; if
  instant revocation is required, this needs direct testing against a running relay.
  (Contrast with sish's explicit `/_sish/api/disconnectclient/<name>` for a similar operation —
  Portal's API reference doesn't document an equivalent forced-disconnect endpoint.)
- **`v2.4.0`-style exact protocol version pinning**: relay and client/SDK must be on matching
  protocol versions or registration is rejected outright (`docs/src/routes/deployment/
  +page.md` "Upgrading" — "v2.3.x clients are rejected by v2.4.0+ relays"). Our monitor-service SDK
  dependency and the deployed relay image tag must be upgraded in lockstep, which is an
  operational constraint worth flagging for CI/CD.
- **x402 payment machinery** (Sui/Casper USDC gating on routes) is a real, well-documented feature
  but entirely irrelevant to our use case (no paid API monetization needed for an internal
  llama.cpp tunnel) — noted only to confirm it doesn't need to be wired up.
- **IVNP overlay transport** (`--overlay`, `DISCOVERY=true`) is optional multi-hop I2P-style
  routing for censorship resistance; not evaluated in depth since our threat model (VPS-hosted
  relay we control) doesn't need it — direct reverse transport (the default) is sufficient.
- **We did not run a live relay+client pair end-to-end** in this research pass (no network access
  to spin up a public relay+DNS delegation); all API shapes and flows above are traced from source
  and docs but not empirically exercised. Before committing to Portal, a smoke test (`docker
  compose up`, `portal expose 3000`, `curl /api/state`, `POST /api/policy/leases`) is recommended
  to confirm the documented behavior matches the current release exactly.
