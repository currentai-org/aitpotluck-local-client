# Punchmole Implementation Plan

**Source verified against:** `git clone https://github.com/Degola/punchmole` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/punchmole` (main branch, commit as of clone time,
`package.json` version `1.2.0`).

Files cited: `README.md`, `package.json`, `.env.example`, `Dockerfile`, `kubernetes.manifest.yaml`,
`app.js`, `PunchmoleServer.js`, `PunchmoleClient.js`, `server.js`, `client.js`, `.gitlab-ci.yml`.

---

## Installation & Global VPS Configuration

Punchmole is a **pure Node.js ESM package** (`"type": "module"`, no compiled binary, `engines.node
>=20.6.0`), with exactly one runtime dependency: `ws` (`package.json` line 27-29, `^8.14.2`). It has no
database, no TLS termination of its own, and no admin-account concept — install options:

1. **npm global install (`README.md` "Installation")**:
   ```bash
   npm install -g punchmole
   ```
   `package.json` `bin` maps two CLI entrypoints (line 7-10):
   ```json
   "bin": { "punchmole": "client.js", "punchmole-server": "server.js" }
   ```
   Run the server:
   ```bash
   PORT=10000 \
   API_KEYS=api-key1,api-key2,random-string-nobody-can-guess \
   PUNCHMOLE_ENDPOINT_URL_PATH=/_punchmole \
   punchmole-server
   ```
2. **Docker** (`README.md` "Run on docker", and repo's own `Dockerfile`):
   ```bash
   docker build -t punchmole .
   docker run -e API_KEYS=api-key1,api-key2,random-string-nobody-can-guess punchmole
   ```
   `Dockerfile` is a two-stage `node:24-alpine` build, runs as non-root `www-data` (uid 82),
   `ENV PORT 10000`, `EXPOSE 10000`, `CMD ["npm", "run", "server"]` → `server.js`.
3. **Kubernetes** (repo ships its own `kubernetes.manifest.yaml`, used by the author in production —
   README: *"I deployed the punchmole server on Kubernetes with 20 replicas with thousands of requests
   per second"*): a `Secret` holding `API_KEYS`, a `Deployment` running the built image with `PORT=10000`
   from that secret, a `Service` on port `10000`, and an `Ingress` with
   `nginx.ingress.kubernetes.io/ssl-passthrough: "true"` for wildcard host `*.${PUNCHMOLE_DOMAIN}` —
   confirming TLS termination for HTTPS tunnels is expected to happen **in front of** punchmole (nginx
   ingress / reverse proxy), not inside the Node process itself. For a single-VPS deployment this maps
   to: run punchmole-server on a private port, put nginx/Caddy/Traefik in front doing TLS + wildcard
   vhost pass-through or termination, forwarding plain HTTP/WS to punchmole's `PORT`.
4. **Source**: `git clone`, `npm install`, `npm run server` / `npm run client` (or `server-dev`/
   `client-dev` variants using `node --watch --env-file=.env`, per `package.json` "scripts").

### Config: ENV vars only (no config file format at all)
Server (`README.md` "Environment variables" table, `.env.example`):
| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `10000` | HTTP port the server listens on |
| `API_KEYS` | none (required) | comma-separated list of accepted API keys |
| `PUNCHMOLE_ENDPOINT_URL_PATH` | `/_punchmole` | WS path used to distinguish tunnel-client connections from public requests |

`server.js` (the CLI entrypoint) hard-fails with a usage message and `process.exit(1)` if `API_KEYS`
is empty (lines 8-14) — this is the entire "global config" step; there's no TLS setup, no admin
account, no DB init inside punchmole itself.

### One-time global config (what actually needs doing on a VPS)
- **TLS**: punchmole has no TLS support at all; a reverse proxy (nginx/Caddy/Traefik) must terminate
  TLS in front of it and forward WebSocket upgrades. This must support wildcard subdomains for the
  routing model in section 4 (the k8s manifest's `ssl-passthrough`+wildcard-host Ingress is the
  reference example).
- **Admin/API-key provisioning**: `API_KEYS` is a **flat shared-secret list**, not a user/account
  system — provisioning a "device" is just appending one more string to this comma-separated env var
  and restarting/reloading the process (there is no hot-reload; changing `API_KEYS` requires a
  restart since it's read once at process start in `server.js` line 5).
- **No DB init**: state (`domainsToConnections`, `openRequests`, `openWebsocketConnections`) is
  **entirely in-process memory** (`PunchmoleServer.js` lines 88-90) — restarting the server drops all
  registrations; clients must reconnect (the bundled `client.js` already loops with a 500ms restart
  delay on close/error, lines 41-49, so this self-heals from the client side).
- **systemd wrapping** (not shipped upstream, standard unit for a global npm install):
  ```ini
  [Service]
  Environment=PORT=10000
  Environment=API_KEYS=%i
  ExecStart=/usr/bin/punchmole-server
  Restart=always
  User=punchmole
  ```
- **docker-compose wrapping** (not shipped upstream but trivial, following the k8s manifest's shape):
  ```yaml
  services:
    punchmole-server:
      build: .
      restart: unless-stopped
      environment:
        - PORT=10000
        - API_KEYS=${PUNCHMOLE_API_KEYS}
      ports:
        - "127.0.0.1:10000:10000"   # keep private; front with nginx/Caddy for TLS+wildcard
  ```

---

## Cloud-Side Control-Plane API (SvelteKit ↔ Punchmole server)

**Punchmole has no separate REST/HTTP admin API distinct from its embeddable JS function** — the
entire "control plane" is the `PunchmoleServer()` function call itself plus the `API_KEYS` array
passed to it, confirmed directly in source (`app.js` re-exports `PunchmoleServer`/`PunchmoleClient`
from `PunchmoleServer.js`/`PunchmoleClient.js`; `README.md` "Usage in own Node code" for the server):
```javascript
import { PunchmoleServer } from "punchmole";

PunchmoleServer(
    PORT,                        // port number to listen on
    API_KEYS,                    // array of api keys (random strings)
    PUNCHMOLE_ENDPOINT_URL_PATH, // /_punchmole is the default path
    console                      // logger: console or {info,debug,error} shaped object
)
```
Confirmed exact signature in `PunchmoleServer.js` line 24-29:
```javascript
export async function PunchmoleServer(port, apiKeys, endpointUrlPath = '/_punchmole', log = console)
```
It throws synchronously if `apiKeys` has no non-empty entries (line 30-32), and returns
`{ server, wss }` (the raw Node `http.Server` and `ws` `WebSocketServer` instances) once
`server.listen(port, ...)` has been called (lines 240-247) — giving the embedding app direct access to
the underlying HTTP server object (e.g. to attach additional routes or graceful-shutdown hooks).

**There is no "register a tunnel" or "issue a per-client token" call** — "provisioning" a tunnel client
is 100% the act of putting its API key string into the `apiKeys` array/`API_KEYS` env var *before* the
client connects; the client itself declares which `domain` it wants when it connects (see next
section). There is no admin endpoint to list active domains/connections either (confirmed by reading
the full `PunchmoleServer.js` — the only externally observable state is via the `wss`/`server` objects
returned, which are raw `ws`/`http` primitives, not a punchmole-provided API).

### Could `PunchmoleServer()` run embedded directly inside the SvelteKit backend process?

**Yes, both scenarios are real options, and this is one of Punchmole's actual design goals** (README:
*"Implemented in Node with the goal to have as much flexibility and as little dependencies and code as
possible"* + explicit "Usage in own Node code" section for exactly this purpose):

- **Scenario A — embedded directly in the SvelteKit Node server process.** SvelteKit's `adapter-node`
  (or any long-running Node deployment, not Vercel/Cloudflare serverless) runs a single persistent
  Node process. Since `PunchmoleServer()` is just an `async function` that calls `http.createServer(...)`
  and `server.listen(port)` (`PunchmoleServer.js` lines 33-247), it can be `await`-called once from a
  SvelteKit server-side module (e.g. a `hooks.server.js` top-level side effect, or a custom Node server
  wrapping SvelteKit's `handler` middleware) to open a **second HTTP listener on its own port** in the
  same OS process as SvelteKit. This works cleanly because `PunchmoleServer()` creates its own
  `http.Server` rather than trying to attach to SvelteKit's — it does not need or want to share a port
  with SvelteKit's own HTTP listener (they're bound to different ports; a front-end reverse proxy would
  route the tunnel's wildcard-subdomain traffic to punchmole's port and normal app traffic to
  SvelteKit's port). This is architecturally sound and is exactly what the README's code sample
  demonstrates: `import { PunchmoleServer } from "punchmole"` used as a regular library call, not a
  standalone process. Practical caveat: SvelteKit's Node adapter process would now own two concerns
  (web app + tunnel gateway) in one process/one failure domain; a crash or OOM in one takes down both,
  and `API_KEYS` provisioning (adding a device) would need an in-process, no-restart-required
  mechanism, which `PunchmoleServer()` does **not** provide (the accepted-keys array is closed over at
  call time — see gap noted in Open Questions).
- **Scenario B — separate process (any Node/Docker deployment, not just serverless).** Run
  `PunchmoleServer()` in its own dedicated Node process/container (via `punchmole-server` CLI, Docker,
  or a small custom `server.js`), and have SvelteKit talk to it purely as an external system: SvelteKit
  never imports punchmole at all, it just knows the wildcard hostname pattern and (as established
  above) has no live API to call — its only real "integration" point is deciding what to write into
  `API_KEYS` at deploy/restart time. This is the safer separation-of-concerns choice, and matches how
  the `kubernetes.manifest.yaml` example deploys it (as its own `Deployment`/`Service`/`Ingress`,
  independent of any app backend).

**Given punchmole has no dynamic reconfiguration API, Scenario B (separate process) is the more
practical choice for aipotluck-local-client** even though Scenario A is technically supported — because
either way, "linking a new device" requires **restarting the punchmole server process to pick up a new
API key** (see Open Questions), and you don't want a device-linking event to restart your entire
SvelteKit web app. If Scenario A is used anyway (e.g. to avoid running a second process), the
mitigation is the same custom API-key-validation shim discussed below, which sidesteps the restart
requirement by validating keys dynamically instead of via the static `apiKeys` array.

---

## Client Linking Flow (access token exchange)

There is **no separate "pairing"/enrollment protocol** — the "token exchange" is the plain
`register` WebSocket message sent on first connect, confirmed exactly in `PunchmoleClient.js` lines
62-65:
```javascript
ws.on('open', () => {
    ws.send(JSON.stringify({'type': 'register', 'domain': domain, 'apiKey': apiKey}));
    ws.isAlive = true;
});
```
and validated server-side in `PunchmoleServer.js` lines 111-125:
```javascript
case 'register':
    if (apiKeys.includes(message.apiKey)) {
        domainsToConnections[message.domain] = { status: 'alive', socket: socket };
        socket.domain = message.domain;
        socket.send(JSON.stringify({type: 'registered', domain: message.domain}));
    } else {
        socket.send(JSON.stringify({type: 'error', message: 'invalid api key'}));
        socket.close();
    }
```

**End-to-end flow for a brand-new machine, matching the "pre-shared token in config" pattern exactly:**
1. SvelteKit backend (our own glue) decides on a `domain` string for the new device (e.g.
   `device-<id>.tunnels.ourapp.com`) and an `apiKey` (any random string it generates itself — punchmole
   places no format/entropy constraints on it, it's compared with plain `Array.includes`).
2. SvelteKit **adds that `apiKey` to the punchmole server's `API_KEYS` list** — this is the crux
   limitation: since `apiKeys` is passed once as a constructor argument/env var and closed over by the
   `register` handler (`PunchmoleServer.js` line 30, 112), there is **no live API to add a key without
   restarting** the punchmole-server process (see Open Questions for mitigation options).
3. SvelteKit delivers `{apiKey, domain, endpointUrl}` to the local-client installer/monitor (our own
   transport — e.g. embedded in a one-time install command or config file the user copies down), which
   sets it as **environment variables** for the client (`README.md` "Client → Environment variables"):
   ```bash
   PUNCHMOLE_ENDPOINT_URL=wss://tunnel.ourapp.com/_punchmole \
   TARGET_URL=http://127.0.0.1:8081 \
   PUNCHMOLE_API_KEY=<issued-key> \
   DOMAIN=device-<id>.tunnels.ourapp.com \
   punchmole
   ```
   or, embedded directly in the aipotluck-local-client monitor's own Node process:
   ```javascript
   import { PunchmoleClient } from "punchmole";
   const events = PunchmoleClient(apiKey, domain, "http://127.0.0.1:8081", endpointUrl, console);
   events.addListener("registered", (result) => console.log("linked!", result));
   events.addListener("error", (err) => console.error("link failed", err));
   ```
4. On connect, the client immediately sends the `register` message (step above); server replies
   `{type:"registered", domain}` on success, or `{type:"error", message:"invalid api key"}` followed by
   `socket.close()` on failure — this is the entire authentication handshake, no further exchange.
5. **Built-in resilience**: the CLI wrapper `client.js` (lines 41-49) auto-restarts the
   `PunchmoleClient()` connection every 500ms on close/error in an infinite loop — reconnect/backoff is
   handled for us if we use the CLI; if embedding `PunchmoleClient()` directly in our own monitor
   process, we must replicate this reconnect loop ourselves (it is not internal to the library
   function, only to the `client.js` CLI wrapper).
6. **Revocation**: remove the key from `API_KEYS` and restart the server — the same one-way,
   restart-required limitation as provisioning. There is no `disconnect-by-domain` or
   `revoke-by-key` API; an already-`alive` connection using a since-removed key keeps working until it
   disconnects (the key is only checked at `register` time, not per-request).

---

## Request Routing (public traffic → local client)

Routing is **Host-header based, one domain string per registered connection**, extremely simple:

- `PunchmoleServer.js` lines 33-40: on every incoming HTTP request, the server extracts the domain
  from the `Host` header (`req.headers.host?.match(/^(.*?)(:[0-9]{1,}|)$/)?.[1]`), looks it up in the
  in-memory `domainsToConnections` map, and if `status === 'alive'`, forwards the full request
  (method/url/headers, then streamed body via `request-start`/`request-data`/`request-data-end`
  messages) over that domain's WebSocket connection to the tunnel client. If no match, `503`.
- **This is subdomain/hostname-based routing, not path-based** — the domain is whatever string the
  client registered with `DOMAIN=...` (an actual DNS hostname is expected, e.g.
  `device-<id>.tunnels.ourapp.com`, matched exactly against the incoming `Host` header).
- **DNS + reverse proxy work needed (our responsibility, not punchmole's)**: a wildcard DNS record
  (`*.tunnels.ourapp.com`) must point at the VPS, and the front-facing reverse proxy (nginx/Caddy) must
  pass the `Host` header through unmodified to punchmole's port so its lookup works — punchmole does
  not do any DNS/wildcard-cert management itself (contrast with specter's built-in ACME, or sish's
  `--https-ondemand-certificate`). The k8s manifest's Ingress `host: "*.${PUNCHMOLE_DOMAIN}"` rule is
  the concrete template for this.
- **Does SvelteKit need to call an API to bind a hostname to a client ID?** No separate binding call —
  as established in section 3, the hostname *is* the `domain` value baked into the `register` message
  the client itself sends; there's no punchmole-side "route table" API distinct from that
  registration. The "binding" SvelteKit performs is entirely upstream of punchmole: deciding what
  domain string to hand to the new device and ensuring DNS/reverse-proxy config covers that wildcard.
- WebSocket proxying is also supported end-to-end (base64-encoded frame relay preserving binary vs.
  text opcode and subprotocols — `PunchmoleServer.js` lines 188-236, `PunchmoleClient.js` lines
  234-297), which matters if the target service (llama-server) exposes any WS endpoints (e.g.
  streaming completions over WS, if ever used) in addition to plain HTTP.

---

## Status Polling / Health Checks

**Punchmole ships no status/health API or CLI at all.** Confirmed by reading the complete
`PunchmoleServer.js` source: the only externally-observable state is the return value
`{ server, wss }` — raw Node `http.Server`/`ws.WebSocketServer` instances — with no punchmole-provided
method to query "is domain X currently connected." Concretely, the only options are:

1. **Build it ourselves by wrapping the embed API**: since `PunchmoleServer()` returns the real `wss`
   object, an app embedding punchmole directly (Scenario A above, or a lightly-forked Scenario B where
   we maintain our own thin wrapper around `PunchmoleServer.js`) *could* inspect `wss.clients` (the
   standard `ws` library's live client `Set`) and cross-reference `socket.domain` (set in the
   `register` handler, `PunchmoleServer.js` line 118) to enumerate currently-registered domains and
   their `readyState`. This is not exposed by punchmole's public API/README, but is possible because
   JS module internals (`domainsToConnections`, closed over inside `PunchmoleServer()`) are not
   accessible from outside — only `wss.clients` (unfiltered by domain) is reachable via the returned
   object, so we'd need to either (a) patch/fork `PunchmoleServer.js` to also return
   `domainsToConnections`, or (b) iterate `wss.clients`, checking each socket's `.domain` property
   (set as a side effect on success) ourselves.
2. **Client-side events**: `PunchmoleClient()` returns an `EventEmitter` with `registered`, `request`,
   `request-end`, `close`, `error` events (`README.md` "Usage in own Node code", confirmed in
   `PunchmoleClient.js` lines 58-303) — if the local-client (aipotluck-local-client monitor) embeds
   `PunchmoleClient()` directly rather than shelling out to the CLI, it can report its **own**
   connection status (`registered`/`close`/`error`) back to SvelteKit via a normal application-level
   heartbeat/webhook — this is the most reliable and simplest option, entirely under our control, and
   doesn't require patching punchmole at all.
3. **End-to-end HTTP probe** (same pattern recommended for the other tools): SvelteKit periodically
   requests `https://device-<id>.tunnels.ourapp.com/health`; a `503` with punchmole's literal body
   `"no registration for domain and/or remote service not available"` (`PunchmoleServer.js` line 76)
   means either the client isn't registered or the domain lookup failed; a `200` (or whatever the
   local llama-server health endpoint returns) confirms the full path is alive. This requires no
   punchmole internals at all and is the recommended primary signal given punchmole's total lack of a
   built-in status API.
4. **Ping/pong keepalive exists but is not exposed as status**: the client sends a WS `ping` every 10s
   (`PunchmoleClient.js` lines 67-73) and the server relies on standard `ws` library ping/pong for
   liveness at the transport level, but this is purely internal keepalive — punchmole does not surface
   ping/pong success/failure as an API-visible signal.

**Bottom line: option 2 (self-reported client-side events, pushed to SvelteKit by our own code) is the
most robust choice, backed up by option 3 (end-to-end HTTP probe) for genuine service-health
confirmation** — punchmole itself provides no server-side polling API.

---

## Open Questions / Assumptions

- **No live add/remove of API keys is the single biggest operational gap.** `API_KEYS` is baked in at
  `PunchmoleServer(port, apiKeys, ...)` call time (`PunchmoleServer.js` line 24-32) and never
  re-read. Confirmed nothing in source watches an env var or file for changes (unlike sish's
  `authentication-keys-directory` watcher). **Mitigations to evaluate before build:**
  (a) fork/wrap `PunchmoleServer.js` to accept a mutable `Set`/async lookup function instead of a
  frozen array — since it's ~250 lines of plain JS with no external state beyond `ws`/`http`, this is
  a small, maintainable patch, or (b) run one punchmole-server process **per device** (heavier, avoids
  touching upstream code, but loses the "single shared server" simplicity and the load-balanced
  20-replica deployment model the author describes), or (c) restart the shared server process on every
  new device link (simplest, but causes a brief outage for *all* already-connected devices on every
  new registration — likely unacceptable at any meaningful device count).
- **No revoke-in-place / disconnect-by-key API**: same root cause as above — revocation requires
  restarting the server (dropping all connections) since there's no "kick this domain" call analogous
  to sish's `POST /_sish/api/disconnectclient/<name>`. A fork exposing `wss.clients` filtering + manual
  `socket.close()` could add this cheaply, but it's not upstream today.
- **No TLS, no wildcard cert automation, no built-in reverse proxy** — unlike specter (built-in ACME)
  or sish (`--https-ondemand-certificate`), punchmole assumes you already have a reverse proxy with a
  wildcard cert in front of it; this is explicit in its own Kubernetes manifest
  (`nginx.ingress.kubernetes.io/ssl-passthrough`) and not something the task can skip when planning the
  VPS deployment.
- **No horizontal-scaling coordination for the in-memory registration map**: the k8s manifest example
  runs with `replicas: 1` for the punchmole-server Deployment in `kubernetes.manifest.yaml` (line 22),
  even though the README claims a 20-replica production deployment elsewhere — since
  `domainsToConnections` is per-process memory with no shared state (no Redis/DB), running >1 replica
  behind a load balancer would require **sticky routing per domain** (the LB must always send a given
  domain's public traffic *and* its client's WS registration to the same replica) — this is not solved
  by punchmole itself and isn't detailed in the shipped manifest (which pins `replicas: 1`). Treat
  horizontal scaling as an open design problem, not a solved one, despite the README's throughput claim.
- **Confirmed exactly**: both `PunchmoleServer(port, apiKeys, endpointUrlPath, log)` and
  `PunchmoleClient(apiKey, domain, targetUrl, endpointUrl, log)` are directly importable, plain async/
  sync JS functions with no class instantiation, no hidden config file, and trivially embeddable in any
  Node process (including a SvelteKit `adapter-node` backend) — this part of the task's premise is
  fully confirmed by source, not just README claims.
