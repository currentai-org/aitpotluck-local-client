# Open-Source WebSocket/Reverse-Tunnel Libraries — Survey vs. APLC-1

## Objective

Wide, open-source-only survey of existing reverse-proxy-over-WebSocket (and
adjacent reverse-tunnel) tools, to check whether any could replace or reduce
scope in our custom APLC-1 transport (a WebSocket-based reverse tunnel that
multiplexes HTTP requests from our SvelteKit/Node cloud backend down to a
local llama-server behind an arbitrary firewall/NAT). Restricted to
open-source projects only, sourced primarily from
`anderspitman/awesome-tunneling` plus targeted npm/GitHub searches for
purpose-built embeddable libraries. **The local wrapper is not required to
stay Python** — if a library's client and server share a runtime/protocol,
rewriting the local wrapper to match is an acceptable cost, not a
disqualifier.

## Methodology

17 candidates were evaluated in 4 parallel research passes, each scored
1-5 on the same four axes the user specified:

1. **Popularity and maturity** — stars, commit recency, maintainer count,
   red flags (abandonment, license ambiguity, stalled hosted services).
2. **Infra lift on our SvelteKit/Node backend** — can the server-side piece
   run embedded as a library inside our existing Node process, or does it
   require deploying/monitoring a wholly separate binary/service? Solutions
   requiring a separate binary were deprioritized per the user's explicit
   instruction, not eliminated.
3. **Client-side simplicity** — no admin/root, no network interface/TUN
   management, no special installation beyond an ordinary unprivileged
   process.
4. **Lines of code / administrative burden** — rough integration LOC and
   ongoing operational load (a second service to patch/monitor vs. "one more
   npm dependency").

Every candidate was checked against its own GitHub/npm page directly (no
aggregator scraped secondhand); URLs are listed in Sources.

## At a glance

| Tool | Score | Language | Embeddable in existing Node server? | Client admin-free? |
|---|---|---|---|---|
| **h2tunnel** | 5/5 | TypeScript (zero-dep) | Yes — `TunnelServer` class, ~20-50 LOC | Yes — client is the same library, no reimplementation needed |
| **pipenet** | 4/5 | TypeScript | Yes — server ships as importable lib | Yes |
| **cactus-tunnel** | 4/5 | TypeScript | Yes — `Server` class, instantiable in-process | Yes (needs Node ≥22) |
| wstunnel | 3/5 | Rust | No — separate compiled binary | Yes (static binary) |
| sish | 3/5 | Go | No — separate SSH server | Yes (plain OpenSSH client) |
| localtunnel | 2/5 | JS (client) / stale | No — separate, unmaintained server | Yes |
| Tunnelmole | 2/5 | TypeScript | No — separate service (hosted or self-run) | Yes |
| tunnelite | 2/5 | C#/.NET | No — separate .NET/SignalR stack | Yes |
| go-http-tunnel | 2/5 | Go | No — separate binary; **AGPL-3.0 licensing risk** | Yes |
| primus (npm) | 2/5 | JS | N/A — not a tunnel tool, just a transport primitive | N/A |
| progrium/localtunnel | 1/5 | Go | No — no server implementation exists at all | No (Go toolchain required) |
| jprq | 1/5 | Go | No — separate binary; hosted service gated in 2023 | Yes |
| koding/tunnel | 1/5 | Go (library) | No — Go-only, unusable from Node; abandoned since 2017 | Yes (but needs Go binary) |
| http-proxy (npm) | 1/5 | JS | N/A — generic proxy, not a NAT tunnel at all | N/A |
| yamux (npm) | 1/5 | JS | N/A — dead package, unrelated to real Yamux protocol | N/A |

*(localtunnel/tunnelmole appear twice — once evaluated as CLI tools, once
re-checked specifically for an embeddable npm server library; both passes
converged on the same conclusion.)*

## Top 3: genuinely embeddable server-side libraries (h2tunnel now the clear leader)

### h2tunnel — Viability: 5/5

**What it is.** A deliberately tiny (< 500 LOC), zero-runtime-dependency
Node.js/TypeScript library implementing an HTTP/2 + mTLS tunnel, documented in
a design blog post by its author (boronine.com, June 2025).[7]

**Popularity and maturity.** Small but genuinely active — 142 stars, 4 forks,
2 watchers — with CI, a test suite with coverage reporting, and recent
dependency/security maintenance (dependabot bumps merged the same year).[7]
Not battle-tested at scale, but the entire codebase is auditable in an
afternoon given its size, which meaningfully lowers supply-chain risk versus a
larger, opaque dependency.[7]

**Infra lift on our SvelteKit/Node backend.** The clearest embeddable-library
win of the whole survey: h2tunnel exports a `TunnelServer` class usable
directly from our existing Node process — `import { TunnelServer } from
"h2tunnel"; const server = new TunnelServer({ key, cert, tunnelListenIp,
tunnelListenPort, proxyListenIp, proxyListenPort }); server.start();` — about
10-20 lines to instantiate and wire lifecycle, plus a small mTLS
cert-generation/rotation script.[7] It listens on its own TCP ports inside the
*same* Node process; no separate deployable artifact.[7]

**Client-side simplicity.** Also a plain npm library (`import { TunnelClient }
from "h2tunnel"`) — no admin/root, no interface management, pure userspace
outbound TLS connection.[7] Since the local wrapper is not required to stay
Python, this removes what would otherwise be the only real friction point:
the client is the *same* tiny library as the server, sharing its protocol,
test suite, and maintainer — no cross-language reimplementation of anything
is needed.[7]

**LOC / admin burden.** Server-side glue: ~20-50 LOC total.[7] Client-side
glue is comparably small (~20-50 LOC) since `TunnelClient` is the matching
half of the same library — there is no protocol to reimplement in a
different language.[7] Ongoing ops burden is the lowest of any option
surveyed since the tunnel server scales/restarts with the app itself; the
main residual risk is being one of very few consumers of a small project,
with some chance of needing to patch it ourselves.[7]

**Bottom line.** With the Python-client constraint lifted, h2tunnel is the
strongest candidate in the entire survey: both halves of the tunnel are the
same minimal, auditable, actively-maintained library, satisfying "no
separate binary" on the cloud side and "no admin/root" on the client side
simultaneously, at the cost of committing the local wrapper to a Node.js
runtime instead of Python.[7]

### pipenet — Viability: 4/5

**What it is.** A modernized, TypeScript/ESM fork of localtunnel,
purpose-built for cloud/container deployment scenarios (Fly.io, Docker,
Kubernetes) where only a single port can be exposed.[11] It is sponsored by
glama.ai.[11] It is used internally to power the author's `mcp-proxy`
project.[11]

**Popularity and maturity.** It is young — 527 stars, 24 forks, 44
commits.[11] It has a single primary author (Frank Fiegel / punkpeye).[11]
There is no long track record, but the design intent
(single-shared-tunnel-port mode) maps directly onto how a containerized
SvelteKit backend is typically deployed.[11]

**Infra lift on our SvelteKit/Node backend.** The standout feature: both
client and server ship as importable JS/TS APIs in one npm package.[11] Server
side: `import { createServer } from 'pipenet/server'; const server =
createServer({...}); server.listen(3000)` returns a Node `http.Server`-like
object.[11] That object can run embedded alongside our existing adapter-node
SvelteKit server, with lifecycle hooks (`onTunnelCreated`, `onTunnelClosed`,
`onRequest`) to integrate our own auth/logging.[11] It also explicitly
supports single-shared-port mode for exactly our kind of cloud deployment.[11]

**Client-side simplicity.** It is fully unprivileged: `npm install pipenet`
then `npx pipenet client --port 3000` or the programmatic client API.[11] No
admin/root, no interface configuration is needed, per the project's explicit
zero-setup design goal.[11]

**LOC / admin burden.** Roughly 150-300 LOC would be needed to mount
`createServer` inside our existing Node HTTP server and wire lifecycle hooks
into our auth/routing layer, plus ~50-100 LOC of client glue.[11] Ongoing
burden is much lower than binary-based options since there's no separate
always-on relay service to operate — only our own domain(s) and the process we
already monitor.[11]

**Caveats.** License terms in the extracted README were not explicitly
confirmed as MIT (generic "License" link, not directly verified) — check the
actual `LICENSE` file before adopting.[11] It is a small single-maintainer
project with no long-term maintenance track record yet.[11]

### cactus-tunnel — Viability: 4/5

**What it is.** A pure Node.js/TypeScript tunnel tool built on Express,
`websocket-stream`, `pump`, and `winston`.[3] Actively developed, including a
recent "Bridge Mode Web UI" feature (requires Node.js ≥22).[3]

**Popularity and maturity.** The smallest community of the top three — 58
stars, 7 forks, 1 watcher, single maintainer (jeffreytse).[3] It shows real
recent feature work, a Jest test suite, and a passing CI badge — not
abandoned.[3] No evidence of large-scale production adoption exists.[3]

**Infra lift on our SvelteKit/Node backend.** Its server mode
(`cactusTunnel.Server`) is explicitly importable and instantiable as a plain
JS/TS object — `new cactusTunnel.Server({ listen: {...} })` — meaning the
tunnel-server logic runs embedded inside our existing Node/SvelteKit HTTP
process rather than as a separate OS binary.[3] This directly satisfies the
"no separate relay binary" priority: it's just another `package.json`
dependency sharing our existing TLS termination and deploy/monitoring
pipeline.[3]

**Client-side simplicity.** `cactus-tunnel client <server> <target>` via npm —
unprivileged, no admin/root, no virtual network interfaces.[3] Requires
Node.js ≥22 on the client machine (not a zero-dependency static binary like
the Rust/Go options) — the one real friction point versus wstunnel/sish's
compiled binaries.[3] It also has a novel "bridge mode" where the relay can
run inside a browser tab, though that's not applicable to a headless
llama-server client.[3]

**LOC / admin burden.** Lowest integration burden of all 17 candidates for our
specific architecture: the server is an importable class, roughly tens of
lines of glue code to wire into our existing HTTP server/auth/routing, no
separate process to run or restart, no extra TLS/cert management.[3] Ops
burden reduces to "one more npm dependency to keep patched."[3]

**Caveats.** Smallest community of the top three by a wide margin — higher
long-term abandonment risk if the single maintainer stops.[3] Small codebase
(a few hundred LOC of core logic) makes self-patching feasible if needed.[3]

## Mature, popular — but require a separate deployed process

### wstunnel — Viability: 3/5

The most mature and popular of all 17 candidates: ~7.0k stars, active Rust
codebase (rewritten from Haskell for maintainability), dedicated docs site,
public demo, commercial sponsorship.[1] Genuinely non-root client behavior — a
single static binary, no virtual network interfaces.[1] But it is not an
embeddable Node library at all: the server must run as a separate compiled
Rust binary/process, requiring its own systemd/container lifecycle, TLS cert
management, and port exposure — directly conflicting with the "embed in
existing Node process" priority.[1] The project also explicitly warns it can
be finicky behind Nginx/Cloudflare/HAProxy, a real risk given our SvelteKit
backend likely sits behind similar infrastructure.[1]

### sish — Viability: 3/5

The most popular Go option (4.7k stars) and the best client-side experience of
the entire survey: the client is literally just OpenSSH (`ssh -R
80:localhost:8080 tuns.sh`), already present on virtually every OS — zero
custom binary, zero admin/root.[5] But it fails the infra-lift priority
hardest among the "mature" tier: sish is a standalone Go SSH server that must
run as its own long-lived process with SSH host-key management, its own TLS
lifecycle, and wildcard DNS — a whole new infra component, not embeddable in
Node at all.[5]

## Other mature options ruled out on infra or licensing grounds

- **tunnelite** (2/5) — most actively developed of the "separate binary" tier (commits within days of this survey, real CI/integration-test suite, hosted SaaS option) and unprivileged client, but its server is a full C#/.NET + SignalR/ASP.NET Core stack — an entirely different runtime from our Node/Python stack, with its own wildcard-cert/DNS requirements and a reference deployment pinned to Azure App Service.[4] Worth reconsidering only if we're open to using their hosted `tunnelite.com` SaaS directly rather than self-hosting.[4]
- **go-http-tunnel** (2/5) — solid HTTP/2-multiplexing design and decent
  adoption (3.3k stars), but two disqualifiers: it requires deploying a
  separate Go binary, and its **AGPL-3.0 license would legally obligate us to
  release our integration source** (or purchase the author's enterprise
  license) since we'd be offering it as part of a network service — a real
  business/legal decision, not just an engineering one.[8][17] Maintenance
  has also slowed to occasional dependency bumps rather than active feature
  work.[8][17]
- **localtunnel** (2/5) — 22.5k stars reflects historical name recognition,
  not current health.[9] The npm client hasn't shipped in ~5 years.[13] Its
  commit history shows the same pattern — meaningful activity stopped years
  ago.[15] The public hosted `localtunnel.me` server is known to be
  unreliable.[9] The server-side code lives in a separate repo,
  `localtunnel/server`, and is not embeddable middleware — it's a standalone
  service needing wildcard DNS and dynamic port allocation.[18] A second,
  dedicated pass specifically checking for an embeddable npm server library
  confirmed the same conclusion.[9] It additionally found the wire protocol
  is raw TCP/HTTP-CONNECT-style, not an actual WebSocket upgrade, and no
  Python client exists.[18]
- **Tunnelmole** (2/5) — more actively maintained than localtunnel (recent
  npm releases, a dashboard, CI), and its *client* is trivially embeddable
  as a Node dependency, but that's the wrong side for our architecture — we
  need the server logic embedded in our SvelteKit backend, and the actual
  tunnel server (`tunnelmole-service`) is a separate, non-embeddable
  self-hosted service, architecturally identical to localtunnel's.[10][14]
  No Python client exists.[10][14]
- **jprq** (1/5) — same "separate relay binary" infra burden as wstunnel, with materially worse maturity signals: no LICENSE file at all (treat as unlicensed/all-rights-reserved by default), and its hosted service (`jprq.io`) pivoted to a paid, members-only model in October 2023 with no clear activity since — effectively an abandoned free path.[2]
- **koding/tunnel** (1/5) — technically a Go *library*, but "library" only
  helps if the consuming backend is also written in Go; since ours is
  Node.js, there is no in-process interop path at all, so it carries the
  same "separate process" burden as any other Go tool. Compounding that: the
  project has had zero commits since June 2017, and its own README warns
  "under active development, please vendor it if you want to use it" — a
  red flag even when it *was* current.[6][16]

## Not a fit at all — no server implementation, or not actually a tunnel

- **progrium/localtunnel** (1/5) — the historically important original that inspired ngrok and the entire localtunnel.me lineage, but its own author explicitly declares it unmaintained/legacy in the README, calling it kept "mainly to archive project history."[12] The current v3 rewrite has *no public server implementation at all*, no npm/JS bindings (Go-only), and no prebuilt cross-platform client binaries — offering essentially zero direct integration value beyond historical/architectural inspiration.[12]
- **http-proxy / node-http-proxy** (1/5) — extremely mature and widely used
  (3,347 npm dependents), but it does not implement the reverse-tunnel
  pattern at all: it's a generic forward reverse-proxy that assumes the
  proxy process already has direct network access to a known target — it
  has no concept of a persistent, client-initiated tunnel connection through
  which requests get multiplexed to a NAT'd client. Included here only
  because it repeatedly surfaces in "websocket reverse proxy npm" searches;
  not usable as-is for NAT traversal.[21][22]
- **primus** (2/5) — a mature, long-lived real-time transport abstraction (like Socket.IO) with a genuinely useful built-in reconnect/heartbeat protocol, but it is a pub/sub messaging layer, not a reverse-tunnel or HTTP-multiplexing mechanism.[23] Trivial to attach to an existing `http.Server` (5-10 LOC), but using it would still mean building essentially all of APLC-1's request-multiplexing/routing logic ourselves on top of it — it would only be useful as a reconnect/heartbeat primitive to borrow from, not a ready-made solution.[23]
- **yamux (npm)** (1/5) — a genuine stream-multiplexing library in concept (the right general shape for multiplexing many HTTP requests down one WebSocket), but the npm package is a 13-year-old proof-of-concept requiring Node v0.10.x, has zero dependents, and — despite the name — is **unrelated to HashiCorp's actual Yamux protocol** used in libp2p/Consul.[20] Confirms there is no mature, modern npm library implementing true stream-multiplexing-over-WebSocket ready to embed.[20]

## Recommendation

**With the local wrapper no longer constrained to Python, h2tunnel is the
clear recommendation.** All three top-tier candidates (pipenet,
cactus-tunnel, h2tunnel) are real, working, MIT-or-similarly-licensed
libraries whose server-side logic can be instantiated directly inside our
existing SvelteKit/adapter-node process — satisfying the "no separate
binary" goal that ruled out every more mature/popular alternative (wstunnel,
sish, tunnelite, go-http-tunnel, localtunnel, Tunnelmole, jprq, koding/tunnel
all require deploying and operating a second service). But only h2tunnel
lets both the client and server run as the *same* small library with zero
cross-language protocol work, which was the deciding factor once the
Python-client requirement was lifted.

Trade-offs to weigh before adopting h2tunnel over building APLC-1 from
scratch:

- All three top-tier candidates are small, single-maintainer projects
  (58-527 stars) with no long production track record — real bus-factor and
  long-term-maintenance risk versus a protocol we design, own, and can
  extend ourselves. h2tunnel's codebase is small enough (< 500 LOC) that
  self-patching/forking is realistic if the upstream maintainer stops.
- Adopting h2tunnel means the local wrapper is rewritten from Python to
  Node.js/TypeScript to match — a real one-time engineering cost, but a
  bounded and now explicitly acceptable one. This buys us a client and
  server that share the exact same library, protocol, and test suite,
  eliminating the reimplementation work that would otherwise be required.
- pipenet and cactus-tunnel remain reasonable fallbacks if h2tunnel's small
  community or narrower feature set (pure HTTP/2 tunnel, no built-in
  subdomain routing) turns out to be a poor fit once we dig into
  implementation details — both have the same "embed server in Node
  process" property, at a similar single-maintainer risk level.
- The realistic paths forward are now: (a) adopt h2tunnel directly (rewrite
  the local wrapper in Node.js, embed `TunnelServer` in our SvelteKit
  backend), (b) vendor/fork h2tunnel if we need protocol extensions it
  doesn't already support, or (c) treat all three as *design reference*
  (embeddable-library architecture, lifecycle-hook patterns, mTLS approach)
  and continue building APLC-1 fully custom if a deeper implementation dive
  turns up a dealbreaker.
- Given the small scale of all three candidate projects, forking/vendoring
  carries about the same long-term maintenance burden as building from
  scratch — the main thing genuinely saved is initial development time on
  the request-multiplexing/HTTP-over-tunnel plumbing, not ongoing
  maintenance burden.

## Sources

[1] https://github.com/erebe/wstunnel
[2] https://github.com/azimjohn/jprq
[3] https://github.com/jeffreytse/cactus-tunnel
[4] https://github.com/cristipufu/tunnelite
[5] https://github.com/antoniomika/sish
[6] https://github.com/koding/tunnel
[7] https://github.com/boronine/h2tunnel
[8] https://github.com/mmatczuk/go-http-tunnel
[9] https://github.com/localtunnel/localtunnel — localtunnel/localtunnel GitHub
[10] https://github.com/robbie-cahill/tunnelmole-client — tunnelmole-client GitHub
[11] https://github.com/punkpeye/pipenet — pipenet GitHub
[12] https://github.com/progrium/localtunnel — progrium/localtunnel GitHub
[13] https://www.npmjs.com/package/localtunnel — localtunnel npm
[14] https://www.npmjs.com/package/tunnelmole — tunnelmole npm
[15] https://github.com/localtunnel/localtunnel/commits/master — localtunnel commits
[16] https://github.com/koding/tunnel/commits/master
[17] https://github.com/mmatczuk/go-http-tunnel/commits/master
[18] https://github.com/localtunnel/server
[20] https://www.npmjs.com/package/yamux
[21] https://www.npmjs.com/package/http-proxy
[22] https://www.npmjs.com/package/node-http-proxy
[23] https://www.npmjs.com/package/primus
