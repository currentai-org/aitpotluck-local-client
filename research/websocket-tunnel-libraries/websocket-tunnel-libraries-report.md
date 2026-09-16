# Open-Source WebSocket/Reverse-Tunnel Libraries — Survey vs. APLC-1

## Objective

Wide, open-source-only survey of existing reverse-proxy-over-WebSocket (and
adjacent reverse-tunnel) tools, to check whether any could replace or reduce
scope in our custom APLC-1 transport (a WebSocket-based reverse tunnel that
multiplexes HTTP requests from our SvelteKit/Node cloud backend down to a
local llama-server behind an arbitrary firewall/NAT). Restricted to
open-source projects only, sourced primarily from
`anderspitman/awesome-tunneling` plus targeted npm/GitHub searches for
purpose-built embeddable libraries.

**Revision note (infra constraint reversed):** our SvelteKit backend runs on
serverless infrastructure with no long-running Node process to embed a
tunnel server into. This inverts the original priority order: "embeds
directly in our existing process" is no longer achievable by *any* option
surveyed (there is no persistent process to embed into), so a **separate
server binary/process is now an accepted, expected requirement** rather than
a penalty. **Client-side leanness remains the top priority, unchanged and
re-emphasized** — no admin/root, no network interface management, and now
additionally weighted toward the *lightest possible runtime footprint*
(a static binary or an OS-bundled tool beats requiring a full language
runtime like Node.js). The local wrapper is not required to stay any
particular language, so client-side scoring is about install/runtime
weight, not language match to the server.

## Methodology

The same 17 candidates from the original survey were re-scored 1-5 against
the revised priorities:

1. **Popularity and maturity** — unchanged: stars, commit recency,
   maintainer count, red flags (abandonment, license ambiguity, stalled
   hosted services).
2. **Server deployment burden** — now scored assuming a separate always-on
   process/binary is required regardless of choice. Differentiators become:
   is it a single static binary (lowest ops burden), does it need a full
   language runtime (Node, .NET) provisioned separately, and are there
   licensing constraints (e.g. AGPL) that create legal exposure for a
   commercial SaaS deployment.
3. **Client-side leanness** — the un-changed top priority. Ranked from
   lightest to heaviest: (a) an OS-bundled tool needing zero install (plain
   SSH), (b) a single static compiled binary (Rust/Go/NativeAOT .NET) with
   no runtime dependency, (c) a package requiring an interpreter/runtime
   already assumed present (Node.js, Python), in that order. No admin/root
   or network-interface management at any tier.
4. **Lines of code / administrative burden** — rough integration LOC on
   both sides plus ongoing operational load, now assuming a second service
   is unavoidable for every candidate.

Every candidate was checked against its own GitHub/npm page directly during
the original research pass; this revision only re-weights the existing
findings against the corrected infrastructure constraint — no new sources
were needed. URLs are listed in Sources.

## At a glance (re-scored for serverless backend)

| Tool | Score | Language | Server deployment | Client weight |
|---|---|---|---|---|
| **sish** | 5/5 | Go (server) | Separate SSH server binary (accepted) | Lightest possible — plain OS-bundled OpenSSH, zero install |
| **wstunnel** | 5/5 | Rust | Separate compiled binary (accepted) | Very light — single static binary, no runtime dependency |
| h2tunnel | 3/5 | TypeScript (zero-dep) | Separate Node service (no longer embeddable) | Node.js runtime required |
| pipenet | 3/5 | TypeScript | Separate Node service (no longer embeddable) | Node.js runtime required |
| cactus-tunnel | 3/5 | TypeScript | Separate Node service (no longer embeddable) | Node.js ≥22 runtime required |
| tunnelite | 3/5 | C#/.NET | Separate .NET/SignalR stack (heaviest server runtime) | Light — NativeAOT single binary |
| go-http-tunnel | 2/5 | Go | Separate binary; **AGPL-3.0 licensing risk** | Compiled binary, but must bundle/cross-compile |
| localtunnel | 2/5 | JS (client) / stale | Separate, unmaintained server; wildcard DNS + port pool | Node.js runtime required |
| Tunnelmole | 2/5 | TypeScript | Separate service (hosted or self-run) | Node.js runtime required |
| primus (npm) | 2/5 | JS | N/A — not a tunnel tool, just a transport primitive | N/A |
| progrium/localtunnel | 1/5 | Go | No server implementation exists at all | No prebuilt binaries; needs Go toolchain |
| jprq | 1/5 | Go | Separate binary; hosted service gated in 2023 | Compiled binary, unclear license |
| koding/tunnel | 1/5 | Go (library) | Go-only, abandoned since 2017 | Compiled binary, needs Go toolchain to build |
| http-proxy (npm) | 1/5 | JS | N/A — generic proxy, not a NAT tunnel at all | N/A |
| yamux (npm) | 1/5 | JS | N/A — dead package, unrelated to real Yamux protocol | N/A |

*(localtunnel/tunnelmole were checked twice in the original pass — once as
CLI tools, once specifically for an embeddable npm server library — both
converged on the same conclusion, restated here under the new weighting.)*

## Top tier: sish and wstunnel

### sish — Viability: 5/5

**What it is.** A standalone Go SSH server (`main.go`, `cmd/`, `sshmuxer/`,
`httpmuxer/` packages) that powers a public managed tunnel service (tuns.sh),
sponsored by pico.sh.[5]

**Popularity and maturity.** The most popular tool in the entire survey — 4.7k
stars, 335 forks, 54 watchers — with a healthy commit cadence, CI
(golangci-lint), Docker image publishing, and binary releases via
goreleaser.[5] No abandonment or maintenance red flags.[5]

**Server deployment.** Now an accepted cost rather than a penalty: sish runs
as its own long-lived service (or Docker container) listening on an SSH port
(e.g. 2222) plus HTTP(S)/WS(S) ports for tunnel traffic, with its own TLS cert
management, key-based auth, and routing config.[5] Since our SvelteKit backend
is serverless anyway, standing up sish as its own deployed service (alongside,
not inside, the serverless functions) is architecturally no different from
deploying any other backing service our serverless functions call out to.[5]

**Client-side leanness — the standout result.** Best-in-class of every option
surveyed: the client is a standard OpenSSH client, already present on
virtually every OS by default (`ssh -R 80:localhost:8080 tuns.sh`).[5] Zero
custom binary to install, zero admin/root, zero network-interface management,
zero language runtime dependency of any kind — this is the lightest possible
client footprint achievable, short of literally requiring nothing at all.[5]

**LOC / admin burden.** Near-zero client integration LOC: shell out to `ssh`,
or use an SSH library (Go's `golang.org/x/crypto/ssh`, Node's `ssh2`, or
Python's `paramiko`) to establish `-R` forwarding programmatically — roughly
50-150 LOC for a managed client wrapper in any language.[5] Server-side:
deploying/operating sish is the real, now-accepted cost — a new
binary/container, systemd unit or k8s deployment, SSH host-key management,
authorized_keys or dynamic key issuance per user, TLS cert automation, and
monitoring a second service.[5]

**Bottom line.** With a separate server process now unavoidable regardless of
choice, sish's unmatched client simplicity (reusing a tool every OS already
ships) makes it the strongest candidate for the client-leanness priority
specifically.[5] The server-side ops burden is real but no worse than any
other option in this tier — it's simply the cost of the infrastructure
constraint, not a differentiator against wstunnel or the Node-based
alternatives.[5]

### wstunnel — Viability: 5/5

**What it is.** A Rust-based (rewritten from an earlier Haskell version)
reverse-tunnel-over-WebSocket tool with a dedicated docs site
(wstunnel.erebe.eu), a public demo server, and commercial sponsorship (Service
Planet).[1]

**Popularity and maturity.** ~7.0k stars, 567 forks, 56 watchers — the second
most popular tool surveyed.[1] Active development history: the author fully
rewrote the project from Haskell to Rust for maintainability, itself a strong
maturity signal, and it continues to ship releases.[1] Single-maintainer risk
exists, but the project has run for years with steady releases, static
binaries, CI, and docs.[1]

**Server deployment.** A separate compiled Rust binary, run as its own OS
process (systemd service or sidecar container), with its own listening port(s)
and TLS cert handling (or its self-signed/auto-reload option).[1] This is now
an accepted architecture rather than a disqualifier, on par with any other
option requiring a dedicated service.[1] One real caveat carried forward from
the original research: the project explicitly documents that it can be finicky
behind Nginx/Cloudflare/HAProxy — worth testing early against whatever
actually fronts our serverless SvelteKit deployment.[1]

**Client-side leanness.** Very close to sish's result: a single static binary,
no interpreter or runtime needed, unprivileged (`wstunnel client ...` as a
normal user process).[1] It does not create virtual network interfaces and
only opens local TCP listeners/forwards on ports the user is allowed to bind —
no admin/root required.[1] The one difference from sish: it's a foreign
compiled binary the user must download/trust and we must bundle/distribute
per-OS/per-arch, rather than reusing something already installed everywhere
(SSH).[1] Still meaningfully lighter than any option requiring a full language
runtime.[1]

**LOC / admin burden.** Low integration LOC (spawn subprocess) but real
operational weight: packaging and distributing a third-party binary for our
client, running/monitoring/restarting a separate server binary in our cloud
infra, managing TLS certs and reverse-proxy compatibility, and diagnosing
issues in a codebase we don't own.[1] Roughly 1-2 days of integration/
deployment work, plus ongoing binary-update and incident-response burden.[1]

**Bottom line.** The most mature and popular tool surveyed, with a
best-in-class *compiled-binary* client (no runtime dependency of any kind)
losing only to sish's zero-install SSH reuse.[1] With a separate server
process now expected architecture, wstunnel is a top recommendation alongside
sish.[1]

## Mid-tier: demoted now that in-process embedding is impossible

These four were the prior top picks specifically because their server
logic could embed inside our own long-running Node process — an advantage
that no longer applies on serverless infrastructure. All four now require a
separately deployed service exactly like every other option, and their
client-side footprint requires a full language runtime rather than a static
binary or OS-bundled tool, placing them below sish and wstunnel on the
re-emphasized client-leanness priority.[1][5]

### h2tunnel — Viability: 3/5

Still the smallest, cleanest, most actively maintained of the Node-based
options (< 500 LOC, zero runtime dependencies, active CI and dependency
maintenance).[7] Its core advantage in the original research — embedding
`TunnelServer` directly inside our own Node process with ~20-50 lines of glue
code — is no longer achievable, since there is no persistent process to embed
into on serverless infrastructure.[7] It must now run as its own deployed Node
service like any other option here.[7] Its client also requires a Node.js
runtime rather than a lean static binary or an already-installed tool like SSH
— heavier than sish or wstunnel on the client-leanness axis.[7] Still worth
considering given its small, auditable codebase if a Node-based server stack
is otherwise preferred.[7]

### pipenet — Viability: 3/5

Demoted for the same structural reason: its purpose-built single-shared-port
cloud-deployment design and in-process embedding capability were its standout
features, and the latter no longer applies.[11] It now needs its own deployed
service like any other tool surveyed.[11] Client requires Node.js, heavier
than a static binary.[11] Still a reasonable option given its design intent
maps onto containerized/serverless-adjacent deployment patterns generally, but
no longer a standout versus sish/wstunnel.[11]

### cactus-tunnel — Viability: 3/5

Same demotion logic: the in-process-embedding advantage is moot on serverless
infrastructure, and it must now run as a standalone deployed service.[3]
Client requires Node.js ≥22, heavier than a static binary.[3] Smallest
community of any option surveyed (58 stars, single maintainer), raising
long-term maintenance risk relative to sish or wstunnel.[3]

### tunnelite — Viability: 3/5

The one candidate that *improves* under the revised weighting: a separate
server process is no longer a disqualifier, and its client is a lean
NativeAOT-compiled single binary — comparable in weight to wstunnel's
static-binary client.[4] Distributed via NuGet with an ASP.NET Core middleware
and first-class .NET SDK integration (not directly relevant to our stack), and
shows unusually strong CI/test discipline and recent commit velocity for its
size.[4] Still the heaviest *server* stack of the realistic options: a full
C#/.NET + SignalR/ASP.NET Core runtime, distinct from anything else in our
infrastructure, with a mandatory wildcard cert/DNS requirement and a reference
deployment pinned to Azure App Service.[4] A real ops cost even though the
client itself is genuinely lean.[4]

## Ruled out — unchanged conclusions from the original research

The revised infrastructure constraint does not change the conclusions for
the remaining candidates; they were already disqualified on maturity,
licensing, or "not actually a tunnel" grounds independent of the
embedding question.

- **go-http-tunnel** (2/5) — solid HTTP/2-multiplexing design and decent
  adoption (3.3k stars), but its **AGPL-3.0 license would legally obligate
  us to release our integration source** (or purchase the author's
  enterprise license) since we'd be offering it as part of a network
  service — unchanged regardless of deployment architecture.[8][17]
  Maintenance has also slowed to occasional dependency bumps.[8][17]
- **localtunnel** (2/5) — the npm client hasn't shipped in ~5 years, the
  public hosted `localtunnel.me` server is known to be unreliable.[9] The
  server-side code needs wildcard DNS and dynamic port allocation
  regardless of whether it's embedded or standalone.[18] Its client also
  requires a Node.js runtime, and no Python or lighter-weight client
  exists.[9][18]
- **Tunnelmole** (2/5) — more actively maintained than localtunnel, but its
  reusable/embeddable piece is the *client* (Node-only), and the actual
  tunnel server (`tunnelmole-service`) remains a separate, non-embeddable
  self-hosted service regardless of our infra shift.[10][14]
- **jprq** (1/5) — no LICENSE file at all, and its hosted service (`jprq.io`)
  pivoted to a paid, members-only model in October 2023 with no clear activity
  since — an abandonment signal independent of deployment architecture.[2]
- **progrium/localtunnel** (1/5) — the author explicitly declares it
  unmaintained/legacy, the current v3 rewrite has no public server
  implementation at all, and there are no prebuilt cross-platform client
  binaries — zero direct integration value under any infrastructure model.[12]
- **koding/tunnel** (1/5) — a Go library with zero commits since 2017 and
  an explicit "vendor it, use at your own risk" warning in its own
  README — abandoned regardless of embedding requirements.[6][16]
- **http-proxy / node-http-proxy** (1/5) — does not implement the
  reverse-tunnel pattern at all; it's a generic forward reverse-proxy
  assuming direct network access to a known target, unrelated to the
  serverless/embedding question.[21][22]
- **primus** (2/5) — a real-time transport abstraction (like Socket.IO), not a
  reverse-tunnel mechanism; would still require building essentially all of
  APLC-1's request-multiplexing/routing logic on top of it.[23]
- **yamux (npm)** (1/5) — a 13-year-old dead package requiring Node v0.10.x,
  unrelated to HashiCorp's actual Yamux protocol despite the name.[20]

## Recommendation

**sish and wstunnel are now the joint top recommendations**, both scoring
5/5 under the revised weighting — the exact inverse of the prior
recommendation, because the infrastructure constraint that favored
in-process-embeddable libraries (pipenet, cactus-tunnel, h2tunnel) no longer
applies on serverless infrastructure. Choosing between them:

- **sish** wins outright on client-leanness: it requires literally nothing
  beyond the OpenSSH client already present on virtually every OS — no
  binary to bundle, no runtime dependency, no admin/root. If minimizing
  local install friction is the deciding factor, this is the strongest
  option in the entire 17-candidate survey.
- **wstunnel** is a close second: its client is a single static Rust binary
  with no runtime dependency, meaningfully leaner than any Node/`.NET`-based
  option, though it does mean bundling/distributing a compiled binary rather
  than reusing an already-installed tool. It also has a purpose-built
  WebSocket-focused design (versus sish's SSH-tunneling model), which may
  align more directly with the "reverse proxy over WebSockets" framing of
  APLC-1 if that protocol-level similarity matters for future extensibility.
- Both require deploying and operating a separate server process/binary as
  a new piece of infrastructure alongside the serverless SvelteKit backend
  — an unavoidable cost now, not a differentiator between them.
- **tunnelite** (3/5) is worth a second look if a lean, NativeAOT-compiled
  client with first-class SDK ergonomics for a specific target language
  becomes attractive, but its full .NET/SignalR server stack is real
  additional infrastructure surface distinct from anything else in the
  deployment.
- The previously-top-ranked Node-based libraries (h2tunnel, pipenet,
  cactus-tunnel) remain viable candidates if a Node-based server deployment
  is otherwise preferred for other reasons, but they no longer hold a
  structural advantage over sish/wstunnel now that none of the options can
  embed in-process — and their client footprint is heavier (a full Node.js
  runtime) than sish's zero-install or wstunnel's static-binary approach.

**Net effect of the infra pivot:** the calculus flips from "avoid a separate
binary at nearly any cost" to "a separate binary is now unavoidable, so
optimize purely for client leanness and server maturity" — which is exactly
what sish and wstunnel deliver, and exactly what the Node-embeddable options
were never optimized for in the first place.

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
[14] https://www.npmjs.com/package/tunnelmole — tunnelmole npm
[16] https://github.com/koding/tunnel/commits/master
[17] https://github.com/mmatczuk/go-http-tunnel/commits/master
[18] https://github.com/localtunnel/server
[20] https://www.npmjs.com/package/yamux
[21] https://www.npmjs.com/package/http-proxy
[22] https://www.npmjs.com/package/node-http-proxy
[23] https://www.npmjs.com/package/primus
