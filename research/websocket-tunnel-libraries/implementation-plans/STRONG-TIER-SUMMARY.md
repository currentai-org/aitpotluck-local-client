# Strong Tier Implementation Scenario Summary (frp, gost, zrok, piko, SirTunnel, boringproxy, rustunnel, tunwg, Portal, gt, specter, Punchmole)

## Scenario recap (unchanged)

Same as the finalist-tier exercise: server as a managed VPS service reachable
from SvelteKit over an API/control-plane; local client library inside
aipotluck-local-client's monitor service; access-token exchange during
install; SvelteKit configures the reverse-proxy server to accept the
client's connection and route requests to it. Full individual plans (cloned
repos, real source/docs) are in `implementation-plans/{tool}/PLAN.md` for
each of the 12 tools below.

## At a glance

| Tool | Real writable control-plane API? | Client linking model | Routing model | Status/health API | Notable catch |
|---|---|---|---|---|---|
| **gost** | **Yes** — REST+Swagger, full CRUD on services/chains/authers/**ingresses** | Server mints a tunnel-ID (UUID) via `POST /config/ingresses`; client connects with `tunnel.id=<uuid>` | Ingress object binds hostname → tunnel-ID directly | Config-CRUD confirmed; connected-client list unconfirmed (buried in Swagger, not crawled) | Most "control-plane native" design of the whole 78-tool survey |
| **zrok** | **Yes** — OpenZiti-backed, `POST /enable` account→identity exchange, plus a remarkable **Agent API** | Account token → identity enrollment; agent-driven share creation | Reserved names bound to a dynamic frontend = stable per-device hostname | `/agent/status`, `/agent/ping`, `/agent/share/http-healthcheck` — genuinely end-to-end | Heaviest architecture of the 12 (full OpenZiti zero-trust overlay under the hood) |
| **rustunnel** | **Yes** — documented REST/OpenAPI, Bearer auth, `POST /api/tokens`, `/api/tunnels`, `/api/history` | Per-tenant UUID token, admin-issued | Custom subdomains **plan-gated** in schema (`allow_custom_subdomains`) | `/api/tunnels` + `/api/history` | **AGPLv3** — real licensing exposure for a commercial SaaS, same issue as go-http-tunnel |
| **Portal** | **Yes**, but via **SIWE** (crypto-wallet-style signed message auth), not a shared token | Client generates a local secp256k1 identity, signs a server-issued challenge, gets an ES256K lease-scoped token | Automated ACME + embedded DNS/DNSSEC | `/sdk/*` control-plane, but instant-revocation-of-live-session unverified | Unusual auth model (blockchain-style signing) — powerful but a real integration curveball vs. a plain bearer token |
| **boringproxy** | **Yes** — real REST (`/api/tunnels`, `/api/tokens`, `/api/clients`, `/api/users`), bearer-token auth | Client self-registers with a token, polls for assigned tunnels; **server generates the SSH keypair itself** | `POST /api/tunnels` binds domain → client-name | None dedicated — no confirmed connection-status endpoint | Clean fit for the scenario overall; `DELETE /api/tokens` HTTP-reachability unconfirmed |
| **gt** | Partial — Gin-backed admin Web UI/REST (`/api/login`, `/config/save`, `/connection/list`) plus an **authAPI HTTP callback** (mirrors sish's model) | Static id+secret; authAPI callback lets our backend approve connections without touching gt's files | `hostPrefix` subdomain, defaults to client `id` | `/connection/list` | Whether `/config/save` hot-reloads or needs a restart is unverified — recommend the authAPI callback path instead to sidestep this |
| **specter** | Partial/indirect — real Twirp RPCs (`MintDelegation`/`ListDelegations`/`RevokeDelegation`) but **only exposed on an "owner" client process**, not the gateway server itself | "Lightweight token client" mode is a genuine pre-shared-token pairing pattern, with built-in reconnect + 30s grant re-validation | Config-free client modes; routing via the gateway once a delegation is minted | `/api/ls` on the owner-client sidecar | Architecturally unusual: SvelteKit would need to run a persistent "owner-client" sidecar process just to call the mint/revoke API — an extra moving part none of the other tools require |
| **frp** | Partial — rich admin API exists but is **read-only status**, not tunnel-creation; per-client pairing only via a custom `Login` **server-plugin HTTP callback** | 100% custom glue via the Login plugin — frp itself has no native per-device token concept, just one shared server-wide token | Clean subdomain/path vhosting, configured per-proxy ahead of time, no API call needed once set | `GET /api/v2/clients/{key}` (read-only) | Extremely mature/popular (109.5k★) but the actual "register a device" flow is entirely something we build via the plugin hook |
| **piko** | No registration endpoint at all — pure JWT minting by us | SvelteKit signs a JWT (`{"piko":{"endpoints":[...]}}`) with a shared key; client presents it via `--connect.token` | `x-piko-endpoint` header or Host-subdomain — no bind-API call needed | `/status/upstream/endpoints` (confirmed in source) | **No clean single-device revoke primitive** — a real architectural gap, not just unresearched |
| **SirTunnel** | **None** | None — it's an SSH remote-command wrapper around Caddy's local admin API; whoever can SSH in can add any route | Caddy route add/delete, keyed by whatever hostname the SSH command specifies | **None** — no way to check "is this tunnel alive" short of inspecting Caddy's config directly | The starkest "we build 100% of everything" result of the whole 78-tool survey; confirmed by reading all 5 files in full, not a research gap |
| **tunwg** | Effectively none — 2 HTTP endpoints (`/add`, `/relay`), one shared secret | One shared `TUNWG_AUTH` env-var secret for the whole server — **no per-client tokens, no revocation mechanism at all** | Automatic via WireGuard-over-UDP netstack, no TUN device (confirmed) | None | Clean transport primitive, but the least control-plane of any 4/5-scored tool once you actually design the scenario around it |
| **Punchmole** | **None** — `API_KEYS` is a closed-over array set once at server startup | No dynamic add/revoke; changing authorized clients requires a full process restart | Host-header lookup, domain baked directly into the client's `register` message — no bind-API needed | None | Genuinely embeddable Node.js library (confirmed both `PunchmoleServer()`/`PunchmoleClient()` work in-process), but zero dynamic control-plane; also no built-in multi-replica/shared-state story for horizontal scaling |

## Key finding: the Strong Tier splits into the same two camps as the finalists

**Real, writable, per-device control-plane APIs**: **gost**, **zrok**,
**rustunnel**, **Portal**, and **boringproxy** all give SvelteKit a genuine
way to register/mint/track a device programmatically — this is the same
"Pangolin/chiSSL" camp from the finalist round, just five more members of
it. Of these, **gost's ingress-object model is the cleanest architectural
match** to the scenario (hostname bound directly to a server-issued tunnel
ID), rivaling Pangolin's site/resource/target graph; **zrok's Agent API**
is the most complete *health/status* story of anything surveyed across
both tiers — it can literally end-to-end health-check a share through the
agent itself. **rustunnel** and **Portal** are close seconds with real
caveats: rustunnel's AGPLv3 license is the same legal exposure already
flagged for go-http-tunnel, and Portal's SIWE-based auth is unusually
sophisticated (secp256k1 identities and signed challenges) rather than a
plain bearer token — powerful, but more integration surface than a typical
REST API key.

**No real control-plane — mostly or entirely custom glue**: **frp**
(despite being the single most popular tool in the entire 78-tool survey),
**piko**, **specter**, **tunwg**, **SirTunnel**, and **Punchmole** all fall
into the "wstunnel/sish" camp — some have partial primitives (frp's
Login-plugin callback, specter's owner-client Twirp API, gt's authAPI
callback) but none give SvelteKit a first-class "register this device, get
back a routable hostname, poll its status" flow without significant custom
backend work. **SirTunnel and Punchmole are the starkest examples**:
SirTunnel provides literally nothing beyond an SSH-triggered Caddy API
call, and Punchmole's entire authorization model requires a full process
restart to change — surprising given Punchmole's very clean embeddable
Node.js API shape, which otherwise looked like the best "share our
JS/TS runtime" candidate of the batch.

## Updated recommendation across all 16 tools examined so far (4 finalists + 12 strong-tier)

**gost now joins Pangolin and chiSSL as a top-tier real recommendation.**
Its ingress-based hostname-to-tunnel-ID binding is arguably even more
directly matched to the scenario than chiSSL's port-only model, and its
Swagger-documented REST API is comprehensive. **zrok is the strongest pick
specifically for the status-polling requirement** — nothing else surveyed
(including the 4 finalists) has as complete an agent-driven health-check
story. Between these two and the original **Pangolin**/**chiSSL** pair,
the practical shortlist for aipotluck-local-client's actual build is now:
**Pangolin** (best overall object model, heaviest deployment), **gost**
(closest single-binary rival to Pangolin's routing cleanliness),
**chiSSL** (leanest deployment with a real API), and **zrok** (best health
story, heaviest underlying architecture via OpenZiti) — with rustunnel and
Portal as viable but license/auth-model-complicated alternates. Every other
tool examined across both tiers, including some very mature/popular ones
like frp and wstunnel, requires building most or all of the actual product
(device registry, token issuance, routing table, health polling) as custom
SvelteKit backend code, regardless of how good the underlying tunnel
transport itself is.
