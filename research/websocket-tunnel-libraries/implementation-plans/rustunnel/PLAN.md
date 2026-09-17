# rustunnel Implementation Plan

**Source verified against:** `git clone https://github.com/joaoh82/rustunnel` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/rustunnel` (main branch, commit as of clone
time). File paths below are relative to that repo root unless a full URL is given.

Key upstream docs used: `README.md` (production deployment §, Docker deployment §, client
configuration §, port reference §, config file reference §, REST API §), `docs/api-reference.md`
(full REST API spec), `docs/client-guide.md`, `docs/architecture.md`, `docs/docker-deployment.md`,
`docs/database.md`, `docs/agent-integration.md`.
Source used: `crates/rustunnel-server/src/auth.rs`, `crates/rustunnel-server/src/control/server.rs`,
`crates/rustunnel-server/src/control/session.rs`, `crates/rustunnel-server/migrations/pg/0006_platform_phase1.sql`,
`crates/rustunnel-server/migrations/pg/0007_platform_auth_tokens.sql`, `crates/rustunnel-client/src/health.rs`,
`deploy/server.toml`, `deploy/rustunnel.service`, `deploy/Dockerfile`, `deploy/docker-compose.yml`.

**Confirmed vs. the task brief:** yes — rustunnel has a genuine, documented REST/OpenAPI
control-plane (`GET /api/openapi.json`, README.md "REST API" §) with Bearer-token auth (admin
token or per-tenant API token), token issuance (`POST /api/tokens`), tunnel listing/history
(`GET /api/tunnels`, `GET /api/history`), and a TypeScript/Next.js dashboard (`dashboard-ui/`,
a Next.js app using TS + React, e.g. `dashboard-ui/hooks/useTunnels.ts`, `useTokens.ts`,
`useTunnelHistory.ts`, `TokensPanel.tsx`, `TunnelHistoryPanel.tsx`). This is by far the most
"platform-like" of the tools surveyed — it already has its own users/plans/billing schema
(`migrations/pg/0006_platform_phase1.sql`), i.e. rustunnel itself is evolving into a
multi-tenant SaaS, which is both a feature (rich API) and a caveat (real API surface has more
moving parts — billing/plan gating — than we strictly need).

---

## Installation & Global VPS Configuration

rustunnel is a Rust workspace producing two relevant server-side binaries and one client binary,
all built via `cargo build --release` (Rust 1.76+, `README.md` "Requirements"):
- `rustunnel-server` — the tunnel server / control-plane / dashboard-API binary.
- `rustunnel` — the CLI client (also usable as `rustunnel-mcp` for agent integration, not needed
  for our use case).

### Option 1 — systemd on bare-metal Ubuntu (README.md "Production deployment (Ubuntu / systemd)")

Exact steps quoted/paraphrased from README.md:

1. Install deps: `apt install -y pkg-config libssl-dev curl git certbot python3-certbot-dns-cloudflare`,
   then Rust via rustup.
2. Build: `git clone https://github.com/joaoh82/rustunnel.git && cd rustunnel && cargo build --release -p rustunnel-server -p rustunnel-client`
   → binaries at `target/release/rustunnel-server` and `target/release/rustunnel`.
3. Create a system user + dirs:
   ```bash
   useradd --system --no-create-home --shell /usr/sbin/nologin rustunnel
   mkdir -p /etc/rustunnel /var/lib/rustunnel
   chown rustunnel:rustunnel /var/lib/rustunnel && chmod 750 /var/lib/rustunnel
   ```
4. Install the binary: `install -Dm755 target/release/rustunnel-server /usr/local/bin/rustunnel-server`
   (or `sudo make deploy`, which wraps build+install+systemd setup per the Makefile).
5. **PostgreSQL is a hard requirement** ("rustunnel requires PostgreSQL for shared state (tokens,
   tunnel history, audit log)" — README.md §5): `apt install postgresql postgresql-contrib`, then
   `CREATE USER rustunnel WITH PASSWORD ...; CREATE DATABASE rustunnel OWNER rustunnel;`. Managed
   Postgres (RDS/DO/Supabase) is explicitly supported — just point `database.url` at it.
   **Schema migrations run automatically on server startup — no manual SQL beyond DB/user
   creation** (confirmed by README.md §5 "Schema migrations run automatically when the server
   starts" and the presence of 14 migration files under
   `crates/rustunnel-server/migrations/pg/*.sql`, which sqlx runs at boot).
6. **Config file** `/etc/rustunnel/server.toml` (full example is in README.md §6 and mirrored at
   `deploy/server.toml`) — key sections:
   - `[server]`: `domain`, `http_port=80`, `https_port=443`, `control_port=4040`,
     `dashboard_port=8443`, `dashboard_origin` (CORS allow-origin for the SvelteKit/Next.js
     frontend).
   - `[tls]`: `cert_path`/`key_path` (Certbot-issued PEM), or `acme_enabled=true` +
     `cloudflare_api_token`/`cloudflare_zone_id` for rustunnel's own built-in ACME/DNS-01 flow
     (`crates/rustunnel-server/src/tls/acme.rs`).
   - `[auth]`: `admin_token` (master secret — **this is the one-time global admin credential**;
     generate with `openssl rand -hex 32`), `require_auth=true`, `max_failed_auth_per_minute=10`.
   - `[database]`: `url` (Postgres DSN), `captured_path` (per-region SQLite file for captured HTTP
     bodies — `/var/lib/rustunnel/captured.db`).
   - `[logging]`: `level`, `format`, optional `audit_log_path` (JSON-lines audit trail of
     auth/registration/token events).
   - `[limits]`: `max_tunnels_per_session`, `max_connections_per_tunnel`, `rate_limit_rps`,
     `ip_rate_limit_rps`, `request_body_max_bytes`, `tcp_port_range`/`udp_port_range` (each tunnel
     consumes one port from these ranges — size the range for however many concurrent
     llama-server clients you expect if you ever use TCP tunnels instead of HTTP subdomains).
   Secure it: `chown root:rustunnel /etc/rustunnel/server.toml && chmod 640 ...`.
7. TLS via Certbot + Cloudflare DNS-01 (needed because HTTP subdomain tunnels require a wildcard
   cert): `certbot certonly --dns-cloudflare ... -d "edge.rustunnel.com" -d "*.edge.rustunnel.com"`.
   Add a renewal deploy-hook that does `systemctl restart rustunnel.service` (rustunnel reads certs
   from disk at startup only, so it must restart after renewal — README.md §7).
8. systemd unit is shipped in-repo at `deploy/rustunnel.service` — just
   `install -Dm644 deploy/rustunnel.service /etc/systemd/system/rustunnel.service && systemctl enable --now rustunnel.service`.
9. Firewall: open `80/tcp`, `443/tcp`, `4040/tcp` (control plane), `8443/tcp` (dashboard+API),
   `9090/tcp` (Prometheus), plus the configured `tcp_port_range`/`udp_port_range` (README.md §9).
10. Verify: `curl http://localhost:8443/api/status` (unauthenticated health JSON), `ss -tlnp | grep rustunnel-serve`, `journalctl -u rustunnel.service -f`.

### Option 2 — Docker / Docker Compose (`docs/docker-deployment.md`, README.md "Docker deployment")

Pre-built multi-arch images are published to GHCR — **no build step required**:
```bash
docker pull ghcr.io/joaoh82/rustunnel-server:latest
docker run --rm -p 80:80 -p 443:443 -p 4040:4040 -p 8443:8443 \
  -v "$PWD/deploy/server.toml:/etc/rustunnel/server.toml:ro" \
  ghcr.io/joaoh82/rustunnel-server:latest
```
`deploy/docker-compose.yml` is the production compose file (also referenced by `make docker-run`);
`deploy/docker-compose.local.yml` is a self-signed/no-auth local dev variant
(`docker compose -f deploy/docker-compose.local.yml up`); `deploy/docker-compose.dev-deps.yml`
starts a Postgres 16 dev container. `make docker-run-monitoring` layers on Prometheus + Grafana
(default Grafana password `changeme` — override via `GRAFANA_PASSWORD` env var before going to
production, per README.md "Monitoring" warning). This is the recommended path for our VPS: one
`docker-compose.yml` wraps the server, mounts `server.toml`, and Compose's own restart policy
gives us the "managed service" behavior for free (no separate systemd unit needed, though systemd
+ the bare binary is equally viable per Option 1).

### Port reference (README.md "Port reference")

| Port | Purpose |
|---|---|
| 80 | HTTP edge (redirect to HTTPS + ACME HTTP-01) |
| 443 | HTTPS edge — actual tunnel ingress |
| 4040 | Control-plane WebSocket — **clients connect here** |
| 8443 | Dashboard UI + REST API — **SvelteKit talks here** |
| 9090 | Prometheus `/metrics` |
| 20000–20099 (TCP), 20100–20199 (UDP) | Configurable tunnel port ranges, one port per active TCP/UDP tunnel |

### One-time global config summary
- TLS: Certbot+Cloudflare (external) or built-in ACME (`tls.acme_enabled=true` + Cloudflare
  creds) — either way it's a one-time cert/DNS setup per domain.
- "Admin account": there's no interactive admin user — the single `auth.admin_token` string in
  `server.toml` **is** the root credential; it is compared in constant time
  (`auth.rs::secret_eq`, uses SHA-256 + `subtle::ConstantTimeEq` specifically to defeat timing
  attacks) and throttled by `AuthFailureLimiter` (default 10 failed attempts/IP/minute → 429).
- DB init: automatic sqlx migrations on first boot against the Postgres URL you provide — no
  manual schema work beyond `CREATE DATABASE`/`CREATE USER`.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ rustunnel server)

The dashboard port (`8443` prod / `4041` local-dev per `deploy/local/server.toml`) exposes a full
JSON REST API, documented exhaustively in `docs/api-reference.md` and additionally served
machine-readably at `GET /api/openapi.json` (no auth) — genuinely OpenAPI 3.0, so SvelteKit could
codegen a typed client from it if desired.

**Auth** (docs/api-reference.md "Authentication"): every endpoint except `GET /api/status` requires
`Authorization: Bearer <token>`. Two token kinds are accepted:
- **Admin token** = the `auth.admin_token` value from `server.toml` — full visibility across every
  tunnel/tenant.
- **API token** = a raw UUID returned once at creation time via `POST /api/tokens` — scoped to
  whichever `user_id` (if any) owns it; tunnels/groups outside scope return `404`, not `403`
  (existence not leaked).

**Endpoints SvelteKit will call directly** (all from `docs/api-reference.md`, quick-reference table
duplicated in README.md "REST API"):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/status` | Unauthenticated health check → `{ "ok": true, "active_sessions": N, "active_tunnels": N }` |
| `POST` | `/api/tokens` | **Issue a per-client token.** Body `{"label": "device-<id>", "scope": null}` → response `{"id": "...", "label": "...", "token": "<raw-uuid-shown-once>"}` |
| `GET` | `/api/tokens` | List tokens + usage counts (hashed, not raw) |
| `DELETE` | `/api/tokens/:id` | Revoke a token — any client currently connected with it is disconnected on its next auth check |
| `GET` | `/api/tunnels` | List active tunnels (scoped to caller); each entry has `tunnel_id`, `protocol`, `label` (subdomain or TCP port), `public_url`, `connected_since`, `request_count`, `client_addr` |
| `GET` | `/api/tunnels/:id` | Single tunnel lookup |
| `DELETE` | `/api/tunnels/:id` | Force-close a tunnel |
| `GET` | `/api/tunnels/:id/requests` | Recent captured HTTP requests through that tunnel (ring buffer, last 500, or SQLite once closed) |
| `GET` | `/api/history` | Paginated log of **all** past+active tunnel registrations, filterable by `protocol`, with `limit`/`offset` |

**Recommended SvelteKit auth model:** the backend holds the single `admin_token` as a server-side
secret (never shipped to the browser) and calls `POST /api/tokens` server-side to mint one scoped
API token per linked device (label = our device ID). This maps naturally onto "per-client tokens":
`{"label": "device-<uuid>"}` → store the returned raw `token` (shown only once) in our own DB
against that device row, then hand it to the local client during linking (see next section).
Admin-token-scoped calls (`GET /api/tunnels` etc. with `Authorization: Bearer <admin_token>`) give
SvelteKit visibility into every device's live tunnel state platform-wide, which is what you'd use
for a fleet dashboard.

**Rate/abuse handling relevant to a backend integration:** failed bearer checks are throttled per
source IP (`auth.max_failed_auth_per_minute`, default 10/min) → `429 {"error": "too many failed
auth attempts"}`; make sure SvelteKit's server-side calls use a stable/allow-listed source IP and
never leak the admin token to a context where wrong-token retries could trip this.

---

## Client Linking Flow (access token exchange)

This is the cleanest of the surveyed tools for our exact "SvelteKit issues a per-client token, then
the local machine authenticates with it" model — rustunnel already has this concept baked in
natively, no glue code needed for the token mechanism itself:

1. **SvelteKit (server-side) calls `POST /api/tokens`** with the platform admin token, body
   `{"label": "device-<our-device-id>"}` (`docs/api-reference.md` "POST /api/tokens"). Response:
   `{"id": "tok-uuid", "label": "device-<id>", "token": "977ebf87-88f3-4af0-9ead-0426a3d00ecd"}`.
   **The raw token is shown only once** — SvelteKit must persist it now (e.g. in its own DB keyed
   by device ID) since the server only stores a SHA-256 hash from then on (confirmed:
   `docs/api-reference.md` "GET /api/tokens" response shows `token_hash`, never the raw value).
2. SvelteKit's device-linking/pairing UI hands that raw token to the user (or to our
   `aipotluck-local-client` installer directly via our own pairing API — same pattern used for
   chiSSL/Pangolin/wstunnel/sish plans: the *token exchange itself* between the browser/user and
   our backend is our own invented pairing UX; what's new here is that the token being exchanged
   IS the real rustunnel-native auth_token, not a synthetic wrapper).
3. On the local machine, the aipotluck-local-client daemon writes (or the interactive
   `rustunnel setup` wizard writes) `~/.rustunnel/config.yml`:
   ```yaml
   server: eu.edge.rustunnel.com:4040     # or self-hosted host:control_port
   auth_token: 977ebf87-88f3-4af0-9ead-0426a3d00ecd
   region: auto
   tunnels:
     llama:
       proto: http
       local_port: 8080          # llama-server's port
       subdomain: device-<id>    # see Routing section below
   ```
   (Config schema and defaults from README.md "Config file" / `docs/client-guide.md` §"Config file
   reference".) Programmatically, our daemon can skip the wizard and just template this YAML file
   itself, then invoke `rustunnel start --config <path>` or `rustunnel start` (default path
   `~/.rustunnel/config.yml`).
4. On connect, the client opens the control-plane WebSocket to `:4040`
   (`crates/rustunnel-server/src/control/server.rs::run_control_plane`, path `/_control`, TLS +
   WS upgrade) and sends a `ControlFrame::Auth{ token, ... }` frame
   (`crates/rustunnel-server/src/control/session.rs::handle_session`, "Auth handshake (5s
   timeout)"). The server validates it against the DB-hashed token (or the constant-time-compared
   admin token), replies `AuthOk{ session_id, ... }` or `AuthError{ message }`, and (per
   `session.rs` lines ~590-660) looks up the owning plan's `max_tunnels`/`allow_custom_subdomains`
   to gate the subsequent tunnel-registration frame.
5. Revocation: SvelteKit calls `DELETE /api/tokens/:id` — "Any client currently connected with this
   token will be disconnected on the next auth check" (`docs/api-reference.md`). No local-client
   config change needed on our backend side; the daemon will simply fail future reconnects with
   `auth failed`, which `docs/client-guide.md` documents as the CLI's `error{code:"auth"}` NDJSON
   event — easy to detect/alert on in our monitor service.

**No separate "pairing"/OAuth dance exists beyond this** — the token IS both the enrollment
credential and the ongoing bearer credential (same value used for the WS control-plane Auth frame
and, if reused, the REST API). This matches ngrok-style UX closely and requires zero bespoke
callback/webhook glue on the rustunnel side, unlike sish.

---

## Request Routing (public traffic → local client)

HTTP tunnels are **subdomain-based**, chosen at tunnel-registration time inside the WebSocket
control-plane protocol (not a separate SvelteKit-facing "bind hostname to client" API call):

- The client's `~/.rustunnel/config.yml` (or `rustunnel http <port> --subdomain <name>`) specifies
  the desired `subdomain`. If omitted, the server auto-assigns one (README.md "Quick start"
  example: `✓ tunnel open https://abc123.eu.edge.rustunnel.com`).
- Server-side, custom/fixed subdomains are **plan-gated**: `session.rs` (~line 631-639) shows a
  `allow_custom_subdomains` check pulled from the token's plan — "custom subdomains require a paid
  plan" is returned as an `AuthError`/tunnel-registration error if the caller's plan doesn't allow
  it. **This means our fixed-per-device-subdomain design requires either (a) the hosted paid tier,
  or (b) self-hosting and controlling the `plans` table ourselves** (self-hosted deployments seed
  `free`/`payg` plans via `migrations/pg/0006_platform_phase1.sql`'s `INSERT INTO plans` — we could
  add our own unrestricted plan row, or simply leave tokens with `user_id IS NULL` — legacy/agent
  tokens — which the migration comment says "limit checks are skipped when user_id IS NULL").
- Once registered, the public URL is `https://<subdomain>.<domain>` — the HTTPS edge (`:443`)
  reads the TLS SNI/Host header, matches it to the registered tunnel, and relays traffic over a
  yamux-multiplexed stream inside the client's already-open control-plane WebSocket connection
  (`docs/architecture.md` diagram: "HTTPS edge → yamux stream → client"). There is no separate
  "SvelteKit must call an API to bind hostname→client" step — binding happens implicitly the
  moment the client's own tunnel-registration frame is accepted, driven entirely by the config
  file's `subdomain` value.
- **Recommended design for us**: assign `subdomain: device-<uuid>` deterministically at pairing
  time (mirrors the sish design), write it into the templated `config.yml` alongside the issued
  token, and (self-hosted) ensure the token's plan allows custom subdomains so reconnects always
  land on the same hostname rather than a server-assigned random one. SvelteKit then just issues
  plain HTTPS requests to `https://device-<id>.<domain>/...`.
- TCP tunnels instead get an auto-picked port from `limits.tcp_port_range` (labelled by port
  number rather than subdomain in `GET /api/tunnels`) — not relevant for llama-server's HTTP API
  unless we specifically want a non-HTTP protocol tunnel.

---

## Status Polling / Health Checks

Two independent, well-documented mechanisms:

1. **`GET /api/tunnels`** (scoped by caller's token) — for each active tunnel returns
   `tunnel_id`, `protocol`, `label` (the subdomain), `public_url`, `connected_since`,
   `request_count`, `client_addr`. SvelteKit can poll this (or `GET /api/tunnels/:id` for one
   device) and treat "present in the list" as "currently connected." `GET /api/history` gives the
   complementary point-in-time picture — every registration ever, with `registered_at` and
   `unregistered_at` (`null` while still active), useful for uptime/SLA reporting rather than
   live-status polling.
2. **Built-in client-side health probing** (`crates/rustunnel-client/src/health.rs`, "TUNNEL-7
   Phase 4") — the *client* can be configured with a `health_check` spec (TCP or HTTP probe against
   the local service itself, e.g. our llama-server's own `/health`); on `max_failed` consecutive
   failures it emits a `ControlFrame::TunnelUnhealthy` frame to the server, and recovers with a
   `TunnelHealthy`-equivalent edge-triggered frame when probes pass again. This is specifically
   about **local-service** health (is llama-server itself alive), not tunnel connectivity, and it's
   surfaced through the load-balancing/groups feature (`docs/load-balancing.md`,
   `dashboard-ui/hooks/useRegionHealth.ts`, `HealthTimeline.tsx`) rather than a plain single-tunnel
   REST field — if we're not using tunnel "groups", the simplest signal remains "is the tunnel_id
   present in `GET /api/tunnels`".
3. **`GET /api/status`** (no auth) — cheap process-wide liveness (`active_sessions`,
   `active_tunnels` counts) — good for basic uptime monitoring of the rustunnel server itself, not
   per-device.
4. Prometheus `:9090/metrics` exposes `rustunnel_active_sessions`,
   `rustunnel_active_tunnels_http`, `rustunnel_active_tunnels_tcp` gauges — aggregate only, not
   per-client, but useful for the ops/Grafana side of monitoring alongside the per-device REST
   polling above.

**Recommended combination:** poll `GET /api/tunnels` (admin-token-scoped) every 15-30s from
SvelteKit, match by `tunnel_id`/`label` == our stored device subdomain, to derive per-device
connected/disconnected. For a stronger "is llama-server itself alive" signal, optionally configure
the client's built-in `health_check` (health.rs) so a dead local service surfaces distinctly from a
dead tunnel — or just issue our own HTTPS probe against `https://device-<id>.<domain>/health`,
identical in spirit to the sish plan's recommendation.

---

## Open Questions / Assumptions

- **Custom-subdomain plan gating on self-hosted instances**: `session.rs` ties
  `allow_custom_subdomains` to the token's `plans` row. We have NOT traced the exact default value
  for tokens with `user_id IS NULL` (the "legacy/admin-issued" bucket the migration comment
  describes as skipping limit checks) — need to confirm empirically (spin up the local dev server
  with `deploy/local/server.toml`, mint a token via `POST /api/tokens`, and attempt a
  `--subdomain` request) whether self-hosted-and-not-going-through-the-web-signup tokens get
  unrestricted custom subdomains by default, or whether we must manually adjust the `plans` table.
- **Dashboard-UI (Next.js, not SvelteKit) is bundled but replaceable**: `dashboard-ui/` is a
  Next.js/TypeScript app, not SvelteKit — we are not using rustunnel's shipped dashboard-ui at all;
  our own SvelteKit backend talks to the same REST API that `dashboard-ui/lib/api.ts` uses, so the
  dashboard-ui code is a useful reference implementation but not something we deploy.
  `dashboard-ui/lib/api.ts` was not read in depth in this pass — worth a follow-up read if we want
  to mirror its exact fetch/error-handling conventions.
  Its Next.js UI is a working reference of the same REST calls above, useful for double-checking
  edge cases in polling frequency / auth-header conventions if SvelteKit integration hits snags.
- **Billing/plan machinery is more than we need**: migrations 0006-0014 (billing status, Stripe
  fields, `usage_events`, PAYG minimums) show rustunnel is building toward a metered SaaS. If
  self-hosting, we likely want to just seed a single "unlimited" plan and use admin-token or
  `user_id IS NULL` tokens throughout to sidestep this complexity entirely — not fully verified how
  cleanly that bypasses every plan-gated code path (only the subdomain gate was directly inspected
  above).
- **`--insecure` / self-signed cert workflows** are for local dev only (README explicitly warns
  "Never use this flag against a production server") — production must have real Let's Encrypt
  wildcard certs, adding a DNS-01/Cloudflare dependency (or ACME HTTP-01 via built-in
  `tls.acme_enabled`) to the install checklist; this is a real operational cost compared to
  simpler tools.
- **AGPLv3 license** — self-hosting rustunnel means our fork/any modifications are subject to
  AGPL's network-copyleft clause; worth flagging to whoever owns the licensing decision for
  aipotluck-local-client, since AGPL has stronger obligations than the sish/wstunnel-style
  permissive licenses evaluated for the other finalists.

---

## Summary of custom glue code required

Very little, compared to the other finalists: rustunnel already ships (a) a full multi-tenant
token-issuance REST API (`POST /api/tokens`), (b) native per-tunnel subdomain assignment driven
purely by client-side config, (c) tunnel listing/history/status REST endpoints suitable for
polling, and (d) an OpenAPI spec SvelteKit can codegen against. What we build ourselves is: the
device-ID ↔ rustunnel-token mapping table in our own DB, the templated `~/.rustunnel/config.yml`
generation step in the local-client installer, the fixed-subdomain convention (`device-<id>`), and
(if self-hosting) making sure our token/plan setup actually permits custom subdomains and
unrestricted tunnel counts for every linked device.
