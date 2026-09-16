# NAT Traversal Techniques for aipotluck: Connecting Local llama.cpp Servers to the Cloud

## Objective

`aipotluck-local-client` installs a local llama.cpp inference server plus a lightweight
Python background service on an end user's PC via a one-line installer. The goal explored
here: let a cloud control plane (fronted by a SvelteKit/Node.js web app) route inference
requests to *any* user's local `llama-server` instance, even when that PC sits behind an
arbitrary, unconfigured home or corporate firewall/NAT -- no port forwarding, no router
admin access, possibly behind carrier-grade NAT (CGNAT). This document is pure research: no
implementation has been built yet. It surveys 15 distinct techniques across five categories,
evaluates each for this specific use case, and ends with a recommendation.

## Methodology

Five parallel research passes (one per category below) used `web_search` + `web_extract`
against primary sources (RFCs, official protocol docs, vendor engineering blogs, and
maintainers' own benchmark data) rather than search-snippet summaries. Every technique was
scored 1-5 on viability for this specific use case -- an end user's arbitrary,
unconfigured network, reached from a cloud service, at a scale of potentially thousands of
concurrent machines -- and evaluated for any "for free" synergy with an existing
SvelteKit/Node.js cloud stack, since that is the project's actual planned backend.

## At a glance

| Technique | Category | Viability | One-line verdict |
|---|---|---|---|
| Cloudflare Tunnel (cloudflared) | Tunnel-as-a-service | 5/5 | Free, outbound-only, native WebSocket, per-user tokens via API -- best overall fit |
| Persistent outbound WebSocket (reverse WebSocket tunnel) | Web-native (JS stack) | 5/5 | Best fit for LLM prompt/token streaming; near-zero infra on a Node/SvelteKit stack |
| STUN + TURN + ICE (WebRTC NAT traversal, RFC 8489/8656/8445) | Classical protocol | 4/5 | Robust, standardized, but heavier than needed since the cloud side is already public |
| Tailscale / Headscale | Mesh VPN | 4/5 | >90% direct connect, DERP relay fallback, single binary -- but requires admin/root for a TUN adapter |
| QUIC/HTTP3-based tunneling (MASQUE / reverse tunnel over QUIC) | Emerging transport | 4/5 | Architecturally excellent reverse-tunnel primitive, but MASQUE tooling is still immature -- mostly custom-build |
| Generic UDP hole punching | Classical protocol | 3/5 | Fine for home users, not a fit for the arbitrary-firewall requirement |
| ZeroTier | Mesh VPN | 3/5 | Similar to Tailscale but self-hosted relay path is now deprecated/enterprise-gated |
| ngrok (commercial hosted tunnel service) | Tunnel-as-a-service | 3/5 | Best turnkey UX but self-serve pricing is prohibitive at thousands of always-on tunnels |
| Self-hosted reverse tunnel (frp/rathole) | Tunnel-as-a-service | 3/5 | Cheapest at scale in dollars, but you own the entire relay fabric and its ops burden |
| Server-Sent Events (SSE) + HTTP POST for the return channel | Web-native (JS stack) | 3/5 | Best firewall pass-rate, but a poor fit for streaming tokens back (needs POST hack) |
| MQTT (pub/sub broker, TCP/TLS or WebSocket transport) | Web-native (JS stack) | 3/5 | Proven at IoT scale but a broker is a whole new system, and pub/sub is an awkward fit for request/response streaming |
| libp2p (AutoNAT + circuit relay v2 + DCUtR/hole punching + identify) | P2P framework | 3/5 | Battle-tested at IPFS/Ethereum scale but heavyweight; Python client (py-libp2p) still immature |
| TCP hole punching / simultaneous TCP open | Classical protocol | 2/5 | Least reliable (~64% NATs); superseded by a plain outbound tunnel |
| WireGuard-based NAT traversal / UDP hole punching | Mesh VPN | 2/5 | Reinvents Tailscale's hard parts from scratch; not shippable to end users as-is |
| UPnP IGD / NAT-PMP / PCP (router-assisted port mapping) | Router-assisted | 2/5 | Zero infra cost but too unreliable (off by default, blocked on CGNAT/corporate) to depend on |

---

## Classical NAT-traversal protocols

### STUN + TURN + ICE (WebRTC NAT traversal, RFC 8489/8656/8445) -- Viability: 4/5

**How it works.** STUN (RFC 8489) is a lightweight request/response protocol a client uses to learn its NAT-assigned public (server-reflexive) IP:port and to probe connectivity.[1][2] TURN (RFC 8656) extends STUN so a client can allocate a relayed transport address on a public server that relays packets when a direct path fails.[1][2] ICE (RFC 8445) orchestrates both: each endpoint gathers host, server-reflexive (STUN) and relayed (TURN) candidates, exchanges them over a signaling channel, runs STUN-based connectivity checks, and nominates the best working pair, preferring direct paths and falling back to TURN only when necessary.[1][2]

**Infrastructure needed.** A public STUN server (stateless and trivial to run, e.g.[4][5] coturn in STUN-only mode or a public server like stun.l.google.com), a public TURN relay server (e.g.[4][5] coturn) with high bandwidth since it carries all relayed traffic (RFC 8656 explicitly notes the provider cost), plus a separate signaling/rendezvous channel to exchange ICE candidates, which the protocols deliberately leave undefined.[4][5] STUN/TURN listen on public IPs with open UDP (and TCP/TLS for TURN-over-TCP) ports 3478/5349.[4][5]

**Success rate & caveats.** Highest reliability of the three: because TURN relays over a client-initiated connection, it works even behind symmetric NAT and most corporate firewalls (TURN can run over TCP/TLS on port 443 when UDP is blocked), giving essentially ~100% reachability whenever any TURN path is permitted; ICE's job is to avoid TURN for the majority of cases.[1][2] Direct hole-punching paths fail with symmetric NAT (endpoint-dependent mapping) and when both peers are behind hard NATs.[1][2] Tailscale estimates direct traversal succeeds in >90% of real-world cases, with relays covering the residual long tail.[1][2] Symmetric NATs are a minority, most common on corporate/office and cloud NAT gateways rather than home routers.[1][2]

**Client dependencies (Python background service).** Python: aiortc (full WebRTC/ICE/STUN/TURN client with DTLS/SCTP data channels, asyncio, pip-installable) or the lower-level aioice (ICE only), or libnice (C library with GObject bindings).[8][9] No custom native binaries beyond Python dependencies, but the DTLS/SCTP stack is heavier than the use case needs.[8][9] Server side additionally requires deploying coturn[6] or using a managed TURN provider, per RFC 8445's ICE specification.[3] aiortc itself is the same client library referenced above.[7]

**SvelteKit/Node.js stack synergy.** Partial.[1][2] The ICE signaling/rendezvous channel can be the existing SvelteKit/Node backend using its native WebSocket/SSE support, so that piece is essentially free.[1][2] But Node provides no STUN or TURN server, so you must deploy coturn (or use public STUN servers) regardless, and Node has no native ICE/WebRTC data-channel stack (requires node-datachannel/wrtc native addons, or terminating ICE in Python and just tunneling bytes).[1][2] Transport infra is net-new even though signaling is free.[1][2]

**Viability notes.** The most robust and standardized option, and the only one of the three (STUN/TURN/ICE, generic UDP hole punching, TCP hole punching) that genuinely covers symmetric NAT, CGNAT, and UDP-blocking corporate firewalls via TURN-over-TCP. Not a 5 for two reasons: (1) it is overkill for this topology - the cloud control plane is itself a public server, so the client can simply hold an outbound connection open and the cloud routes requests down it (a reverse tunnel), achieving reachability with no ICE candidate dance; (2) TURN relay bandwidth is a real operating cost for LLM token streams, and the Python WebRTC stack (aiortc/SCTP) is heavier than required. Still the safest choice if the requirement is 'must never fail for any user'.[unverified]


### Generic UDP hole punching -- Viability: 3/5

**How it works.** Both peers first send an outbound UDP packet to a public rendezvous server, which records each peer's NAT-mapped public IP:port and shares them.[8][9] Each peer then sends UDP packets to the other's public endpoint; each outbound packet creates a NAT state entry ('punches a hole'), so the peer's subsequent packets are accepted as return traffic.[8][9] Periodic keep-alive packets renew the binding, since UDP NAT state typically expires in tens of seconds to a few minutes.[8][9]

**Infrastructure needed.** A public rendezvous/coordination server that observes each client's public endpoint and exchanges them (can double as de-facto STUN).[2][5] The technique itself includes no relay - a separate public UDP relay must be added as fallback for cases where punching fails.[2][5]

**Success rate & caveats.** Fails with symmetric NAT (endpoint-dependent mapping), most common on corporate gateways.[8][9] Also fails when outbound UDP is blocked, and can degrade under CGNAT port-allocation pressure.[8][9] Bryan Ford et al.[8][9] measured ~82% of tested NATs supporting UDP hole punching; Tailscale estimates direct UDP traversal succeeds in >90% of real-world cases with STUN plus simultaneous transmission, with both-sides-hard-NAT being the residual failure.[8][9]

**Client dependencies (Python background service).** Python standard-library sockets are sufficient (UDP socket plus rendezvous exchange and keep-alive timers); optionally pystun3 for public-endpoint discovery.[2][5] No native binaries required, fully cross-platform via asyncio/socket.[2][5]

**SvelteKit/Node.js stack synergy.** Strong.[8][9] The Node/SvelteKit backend is already a public rendezvous server: a WebSocket connection from the client both coordinates the endpoint exchange and reveals the client's public IP:port (acting as STUN), so coordination infra is essentially free.[8][9] Still need a UDP relay fallback (small Node/Python process) that Node does not provide natively.[8][9]

**Viability notes.** Works well for home networks (~82-90%+ direct success) but is solving a peer-to-peer problem this use case largely does not have: the cloud is a public server, so an outbound connection from the client already makes it reachable without any hole punching. Provides no relay fallback on its own, fails on symmetric NAT and UDP-blocking corporate networks (a target segment here). Score 3 reflects 'workable for home users, weak against the arbitrary-firewall goal.'[unverified]


### TCP hole punching / simultaneous TCP open -- Viability: 2/5

**How it works.** Two peers each initiate an outbound TCP connection to the other's NAT-mapped public endpoint at roughly the same time (simultaneous open).[10] Each outgoing SYN creates a NAT mapping, and when the two SYNs cross on the wire, each NAT accepts the incoming SYN as a response to its own outgoing SYN, forming a direct TCP stream.[10] Requires reusing a single local port for both listening and outgoing connects (SO_REUSEADDR/SO_REUSEPORT) and often port prediction when the NAT does not preserve port numbers.[10]

**Infrastructure needed.** A public rendezvous/coordination server to exchange the peers' public (and predicted) endpoints; the Node backend can fill this role.[5][9] No relay is inherent to the technique.[5][9]

**Success rate & caveats.** Least reliable of the three.[10] Many NATs drop or RST unsolicited inbound SYNs, break simultaneous open, or use unpredictable port allocation that defeats port prediction; CGNAT makes sequential port allocation impractical.[10] Ford et al.[10] measured only ~64% of tested NATs supporting TCP hole punching (vs ~82% for UDP), on data now dated - modern strict stateful firewalls and CGNAT likely reduce this further.[10]

**Client dependencies (Python background service).** Python standard library can do it but with difficulty: binding multiple sockets to one port with SO_REUSEADDR, non-blocking simultaneous connect()/listen(), port prediction, retry/auth logic.[5][9] Fragile, low-level socket code with real cross-platform differences; no mainstream well-maintained Python library implements this out of the box.[5][9]

**SvelteKit/Node.js stack synergy.** Weak-to-partial.[10] Rendezvous/exchange can run on the Node WebSocket backend (free), but Node provides no help for the actual simultaneous-open mechanics, which depend entirely on OS socket behavior.[10] No relay fallback included.[10]

**Viability notes.** Rarely justified today. ~64% (and likely declining) success rate, dependence on fragile OS-specific SO_REUSEADDR/simultaneous-open behavior, port-prediction requirements, and RST-emitting NATs make it the worst fit for an 'arbitrary unconfigured firewall' guarantee. The same goal is achieved far more reliably with a plain outbound TCP/WebSocket tunnel from the client (outbound TCP is almost never blocked).[unverified]


## Mesh VPN / overlay network platforms

### WireGuard-based NAT traversal / UDP hole punching -- Viability: 2/5

**How it works.** Raw WireGuard is a UDP-only cryptokey-routing tunnel with no built-in NAT traversal, signaling, or relay.[11] To connect two peers behind NAT you add an out-of-band coordination/rendezvous server: each peer registers its public endpoint, the server exchanges those ip:ports, then both peers simultaneously send outbound UDP packets to each other's public endpoints so each NAT creates a state-table mapping.[11] Once the first authenticated packet gets through, WireGuard's 'cryptokey endpoint roaming' auto-updates the peer's ip:port, and PersistentKeepalive (25s) keeps the NAT mapping from expiring.[11]

**Infrastructure needed.** Everything must be assembled by you: (1) a public coordination/rendezvous server with a stable IP; (2) a public STUN server (or equivalent) so peers discover their own mapped ip:port and NAT type; (3) a public relay/bounce server for symmetric-NAT / both-behind-CGNAT fallback, since WireGuard itself offers none.[12] No managed option for the traversal layer itself; self-hosting is mandatory for the control plane.[12]

**Success rate & caveats.** No published % for raw-WireGuard hole punching; success depends entirely on NAT type.[11] Works across Full-Cone, Address-Restricted and Port-Restricted Cone NATs, fails against Symmetric NAT and CGNAT (docs call hole punching 'impossible' there).[11] Corporate firewalls that block UDP, and double-NAT, also defeat it.[11] Unlike Tailscale/ZeroTier there is no automatic relay fallback -- the connection simply does not form unless you build and operate a relay yourself.[11]

**Client dependencies (Python background service).** Heavy.[12] Needs a WireGuard implementation (wg-quick, requires root/TUN interface, or userspace wireguard-go, MIT, still needs a TUN device requiring admin on Win/Mac) plus custom coordination-client logic (no mature first-class Python library exists for the traversal itself; wg-punch is a young project) plus a STUN client.[12] Admin/root privilege to create the network interface is a hard requirement on all three OSes.[12]

**SvelteKit/Node.js stack synergy.** Mixed.[11] The coordination/signaling server can genuinely live inside SvelteKit/Node (WebSocket rendezvous endpoint), some free overlap there.[11] But the relay/bounce server and STUN infrastructure cannot be a SvelteKit web app and must be separate always-on public hosts regardless of web stack.[11]

**Viability notes.** Raw WireGuard gives none of the hard part -- you would be re-implementing Tailscale's STUN + hole-punching + DERP-relay machinery from scratch against a protocol deliberately designed without it. High engineering risk, no published success-rate floor, no relay fallback unless built, and a Python client that must shell out to a root/admin TUN interface on every user machine. Not a good fit for a 'one-line installer' product shipped to non-technical end users.[unverified]


### Tailscale / Headscale -- Viability: 4/5

**How it works.** Tailscale is a mesh VPN that wraps WireGuard with a full control plane.[5][13] Every device runs a tailscaled daemon that connects to a coordination server, which exchanges public keys, IP:port candidates and DERP server info between peers.[5][13] Every connection initially flows through a DERP relay (encrypted TCP/HTTPS on port 443), and in parallel each side uses STUN-discovered endpoints plus UDP hole punching (including 'birthday' attacks for hard NATs) to establish a direct WireGuard path; if that succeeds traffic silently upgrades to direct P2P, otherwise it stays on DERP as fallback.[5][13]

**Infrastructure needed.** Managed: Tailscale's SaaS control plane plus Tailscale-operated DERP relay fleet (free tier, then paid).[15][16] Self-hosted: Headscale (open-source, BSD-3, Go) replaces the control server, and you run your own DERP relays via the open-source derper binary.[15][16] DERP traffic rides over TCP/443 HTTPS so it passes most corporate egress proxies.[15][16]

**Success rate & caveats.** Tailscale publishes explicit metrics: direct NAT traversal succeeds 'well north of 90%' of connections in typical conditions.[5][13] Failure modes forcing DERP relay: symmetric/hard NAT on both sides, double/multi-layer NAT, CGNAT (short port timeouts + symmetric mapping), firewalls/IDPs that block UDP or classify P2P.[5][13] DERP fallback adds latency and lower throughput and consumes relay bandwidth -- docs call using a relay when direct would work 'one of the most common causes of performance issues'.[5][13]

**Client dependencies (Python background service).** Ship the single tailscaled/tailscale binary (open source, BSD-3-Clause) inside the installer, run `tailscale up --auth-key=<ephemeral-key>` (or point at self-hosted Headscale).[15][16] The daemon exposes a local HTTP API, so the Python service can drive/observe it via localhost HTTP.[15][16] Requires admin/root for the network adapter on all three OSes (may be blocked by corporate MDM/IT policy).[15][16] Client and Headscale are BSD-3; Tailscale's SaaS adds their ToS and free-tier device limits.[15][16]

**SvelteKit/Node.js stack synergy.** None -- entirely separate infrastructure regardless of web stack.[5][13] Headscale (Go) and derper (Go) are standalone binaries/services deployed alongside the SvelteKit app; the Node layer only calls their HTTP APIs (device list, auth-key issuance, ACLs) and reaches llama-server over the tailnet IP.[5][13]

**Viability notes.** The most proven off-the-shelf path for exactly this problem: a single binary shipped with the installer, a published >90% direct-connect rate, automatic DERP relay over TCP/443 as fallback that survives most corporate firewalls, and a mature self-hosted control plane (Headscale) so you aren't locked to Tailscale's SaaS. Deductions: (1) installing the virtual network adapter forces admin/root on every user machine; (2) corporate MDM/AV can block or flag a VPN agent; (3) self-hosting means operating Headscale + DERP yourself. For a cloud product that must reach arbitrary home/corporate PCs this is the highest-confidence choice.[unverified]


### ZeroTier -- Viability: 3/5

**How it works.** ZeroTier is a software-defined virtual LAN using its own protocol (not WireGuard): a cryptographically-addressed P2P transport layer (VL1) under an Ethernet-emulation layer (VL2).[18][19] Nodes start with links only 'up' to always-on root servers (the global 'planet', or user-defined 'moons'), send first packets upstream, and receive rendezvous hints; both endpoints then attempt UDP hole punching (plus LAN discovery, symmetric-NAT port prediction, and uPnP/NAT-PMP) to form a direct link.[18][19] If a direct path cannot be established, traffic continues through root/relay servers ('free but slow'), retrying direct connection forever in the background.[18][19]

**Infrastructure needed.** Managed: ZeroTier Central (SaaS network controller, free up to 25 devices) plus ZeroTier Inc.'s global root servers (free, also relay fallback).[20][21] Self-hosted: the controller code lives in the 'nonfree' (source-available, not OSI open-source) part of the repo, and private 'moons' (self-hosted relay/root servers) are now deprecated and unsupported under SLA, pushing self-hosted root infra to paid enterprise sales.[20][21]

**Success rate & caveats.** No published % figure; docs state only that 'most traffic flows peer to peer' with relaying as fallback.[18][19] Stronger than raw WireGuard on hard NATs (explicit port prediction for symmetric IPv4 NAT and uPnP/NAT-PMP), but symmetric NAT behind CGNAT remains the common failure case that drops to the 'slow' relay.[18][19] Corporate firewalls blocking UDP/9993 also push traffic to relay.[18][19] The deprecated-moons change means the classic self-hosted-relay escape hatch is now discouraged.[18][19]

**Client dependencies (Python background service).** Ship the zerotier-one binary + zerotier-cli, run `zerotier-cli join <network-id>` and authorize via the controller (local JSON/HTTP control API available).[20][21] Requires admin/root to install (virtual NIC/kernel driver) -- same UAC/sudo and corporate-MDM caveats as Tailscale.[20][21] Core client is MPL-2.0, but the self-hosted network controller is 'nonfree'/'source available' -- licensing friction if you want to own the control plane.[20][21]

**SvelteKit/Node.js stack synergy.** None -- entirely separate infrastructure regardless of web stack.[18][19] The controller (SaaS or nonfree self-hosted) and roots/moons are separate services; the SvelteKit/Node app would only call the Central/controller HTTP API to authorize nodes.[18][19]

**Viability notes.** Technically solves the problem with an easy single-binary client, built-in rendezvous + hole punching (including symmetric-NAT port prediction), and a working (if slow) relay fallback -- and the free Central tier makes a managed deployment trivial to prototype. Score pulled down by the self-hosting story: moons/self-hosted relays are deprecated, and the self-hosted controller is nonfree, pushing a product wanting to own its control plane toward enterprise sales rather than an open route. For a managed MVP on ZeroTier Central it could be a 4; for a self-hosted product it is a 3.[unverified]


## Tunnel-as-a-service / reverse proxy

### ngrok (commercial hosted tunnel service) -- Viability: 3/5

**How it works.** ngrok runs a small local agent binary that authenticates to ngrok's cloud control plane with an authtoken and opens a long-lived, TLS-encrypted OUTBOUND connection (typically port 443) to ngrok's globally distributed edge.[22][23] The cloud assigns a public URL; inbound requests to that URL hit the nearest ngrok edge, which forwards them over the already-open outbound connection to the agent, which proxies them to a local port (e.g.[22][23] llama-server on 127.0.0.1:8080).[22][23] Because the connection is initiated from inside the network, no inbound firewall rule, port forward, or router admin access is ever required, and it works through NAT and CGNAT.[22][23]

**Infrastructure needed.** Fully managed SaaS: ngrok hosts the edge/relay, DNS, and TLS (automatic HTTPS certs) -- zero server-side infrastructure run by you, only the agent binary plus an ngrok account/authtoken.[25][26] Free tier limited to one assigned *.ngrok-free.app dev domain; custom/static/reserved domains require paid plans.[25][26]

**Success rate & caveats.** Free tier is unusable for production (1 GB + 20k HTTP requests/month, max 3 online endpoints, click-through interstitial page on browser traffic).[22][23] Pay-as-you-go is metered (~$0.10/GB, ~$1/100k HTTP requests, $0.02/active-endpoint-hour), which at thousands of always-on endpoints reaches tens of thousands of dollars/month.[22][23] Every request adds an extra hop through an ngrok PoP.[22][23] The agent can dial through an HTTP/SOCKS5 proxy, so corporate proxies are usually workable, but a proxy that blocks/MITMs unknown outbound 443 destinations can still break the tunnel.[22][23]

**Client dependencies (Python background service).** A single static ngrok agent binary (no runtime deps), run as a background service (`ngrok service install`).[25][26] Ships for Windows (winget/Scoop/MS Store or standalone .exe), macOS, and Linux (including ARM) -- equal cross-platform availability.[25][26] Closed-source but free to download; usage billed against the account's plan.[25][26]

**SvelteKit/Node.js stack synergy.** Partial.[22][23] The SvelteKit/Node control plane does NOT host the relay -- it just makes plain HTTPS/WebSocket requests to each agent's public ngrok URL, which any Node HTTP client does natively.[22][23] But tunnel lifecycle, auth, TLS, and DNS all live in ngrok's proprietary cloud, managed via ngrok's REST API -- a separate system to integrate with, not free.[22][23]

**Viability notes.** Technically the best turnkey fit (works through arbitrary NAT/CGNAT and corporate proxies, no infra to operate), but self-serve pricing is prohibitive at thousands of concurrent always-on tunnels: $0.02/hr is about $14.40/endpoint/month plus $0.10/GB egress scales into tens of thousands of dollars/month, and the free tier caps at 3 endpoints. ngrok offers enterprise per-device pricing for exactly this use case, but that is a custom contract, not self-serve. Viable for a small beta or with a negotiated enterprise deal; poor default at scale.[unverified]


### Cloudflare Tunnel (cloudflared) -- Viability: 5/5

**How it works.** A lightweight daemon (cloudflared) installed on the user's machine authenticates to Cloudflare and opens outbound-only, encrypted connections to Cloudflare's edge (four long-lived connections to two data centers per tunnel).[28][29] Cloudflare maps public hostnames (DNS pointing at <tunnel-id>.cfargotunnel.com) to the local service; requests hit Cloudflare's edge and are forwarded over the already-open outbound connection to cloudflared, which proxies to the local service.[28][29] No inbound ports, port forwarding, public IP, or router access are ever needed.[28][29]

**Infrastructure needed.** Managed SaaS edge: Cloudflare hosts the relay, DNS, TLS (automatic certs), and DDoS/WAF.[30][31] The only prerequisite is a domain whose DNS is managed by Cloudflare.[30][31] Tunnels are created and credentialed (per-tunnel JSON token) via the Cloudflare dashboard or API.[30][31] Available on all Cloudflare plans including free.[30][31]

**Success rate & caveats.** Hard prerequisite: the product's domain must be on Cloudflare DNS.[28][29] Requires outbound connectivity to Cloudflare's edge (QUIC/HTTP2, port 7844 or 443); corporate firewalls/proxies that restrict outbound destinations or TLS-intercept can block it.[28][29] Relies entirely on Cloudflare as a single third party (availability + ToS); verify current Service-Specific Terms before serving large model downloads (streaming JSON/text inference responses are fine).[28][29]

**Client dependencies (Python background service).** A single static cloudflared binary, run as a background service.[30][31] Ships first-class for Windows (.exe/.msi), macOS (Homebrew or tarballs), and Linux (deb/rpm/arm), plus Docker -- equal cross-platform availability.[30][31] Apache-2.0 open source and free.[30][31] Installer must obtain a per-user tunnel token via Cloudflare API and configure ingress hostname -> http://localhost:<llama-port>.[30][31]

**SvelteKit/Node.js stack synergy.** Strong.[28][29] Zero additional cloud-side software: Cloudflare's edge IS the relay and it speaks plain HTTP/HTTPS/WebSocket, which the SvelteKit/Node backend already handles natively -- route requests to https://<user-subdomain>.yourdomain.com (TLS terminated by Cloudflare, WebSocket passes through).[28][29] Main integration is calling Cloudflare's REST API (or Node SDK) to programmatically create tunnels and mint per-user tokens at install time -- a modest, well-documented API integration.[28][29]

**Viability notes.** Best fit for this use case at scale: free (no per-endpoint, per-GB, or per-request metering on the tunnel itself), fully managed, outbound-only and CGNAT-safe, native WebSocket for token streaming, and per-user tunnel tokens mintable via API so a one-line installer can provision connectivity automatically. Real costs are (a) moving/managing the product's DNS on Cloudflare and (b) dependence on Cloudflare's ToS/availability; no marginal per-tunnel fee, so thousands of concurrent tunnels are economically viable. Residual risk: locked-down corporate networks that block Cloudflare destinations.[unverified]


### Self-hosted reverse tunnel (frp/rathole) -- Viability: 3/5

**How it works.** Both frp (Go) and rathole (Rust) are self-hosted reverse proxies: a server component runs on a VPS with a public IP, a client component runs on the NAT'd device.[33][34] The client makes an OUTBOUND connection to the server, authenticates with a per-service token, and registers the service; the server then accepts public traffic and forwards it back over that outbound connection to the local service.[33][34] Because the connection originates from inside the NAT, no inbound port forwarding or router access is needed.[33][34] rathole forwards raw TCP/UDP only; frp adds HTTP/HTTPS virtual-host routing, a P2P hole-punching mode, and a status dashboard.[33][34]

**Infrastructure needed.** Fully self-hosted -- you operate the relay: one or more VPS with a public IP (multiple PoPs for scale) running frps or the rathole server, plus DNS and TLS termination you provision.[35][36] No managed SaaS and no vendor domain requirement.[35][36] You are responsible for capacity, HA, security patching, and DDoS protection.[35][36]

**Success rate & caveats.** Your relay server is a single point of failure unless you build HA/geo-redundancy yourself (single-node by default).[33][34] You bear all egress-bandwidth cost.[33][34] Corporate proxies can block the client's outbound connection if the destination/port is not allowlisted.[33][34] No managed TLS or abuse handling -- safely exposing thousands of endpoints (auth, rate limiting, anti-abuse) is entirely your responsibility to build.[33][34]

**Client dependencies (Python background service).** A single static binary: frpc (Apache-2.0) ships prebuilt for Windows/macOS/Linux (amd64/arm64/arm/386/mips) and more; rathole (Apache-2.0) ships binaries as small as ~500 KiB with optional Noise/TLS encryption.[35][36] Both free and open source, supervised by the existing Python background service.[35][36]

**SvelteKit/Node.js stack synergy.** Weak-to-moderate.[33][34] The cloud side is a totally separate relay service (frps/rathole server) you deploy and operate on your own VPS -- not already installed and does not run inside the SvelteKit/Node app, though it speaks plain HTTP/TCP so Node can trivially reverse-proxy to it once running.[33][34] TLS certs, DNS, and the relay itself are all new infrastructure you own.[33][34]

**Viability notes.** Zero per-tunnel licensing cost and full control make it cheap in marginal dollars (VPS + bandwidth only), proven at scale by frp's large user base. But for thousands of end-user machines you must build and operate the entire relay fabric yourself -- public-IP servers, HA, TLS, DNS, token issuance, monitoring, DDoS/abuse handling -- a significant ongoing engineering/ops burden and single point of failure if not engineered around. Better for a self-hosted/on-prem product or small fleet than a zero-ops SaaS.[unverified]


## Web-native / already-in-the-JS-stack protocols

### Persistent outbound WebSocket (reverse WebSocket tunnel) -- Viability: 5/5

**How it works.** The Python background service acts as the WebSocket client: it dials out over TCP/TLS to a public wss:// endpoint on the cloud and performs the HTTP Upgrade handshake (the client always initiates the handshake per RFC 6455).[37][38] Once upgraded, the single persistent TCP socket carries full-duplex frames in both directions, so the cloud can push a prompt down the same socket the client opened and receive token chunks back up it.[37][38] Because every byte flows over one client-initiated outbound connection, the NAT/firewall only ever sees ordinary outbound HTTPS and never needs an inbound port-forward rule.[37][38]

**Infrastructure needed.** A publicly reachable WebSocket server (ideally on 443 with TLS).[39][40] In Node this is the `ws` library (npm, ~22.8k stars, passes the Autobahn test suite) -- a few lines creates a WebSocketServer, attachable to the same HTTP server that serves the SvelteKit app (via adapter-node's custom server pattern).[39][40] The only real additional piece is an in-memory registry mapping device-id -> socket.[39][40] For thousands of live connections, add horizontal scaling (sticky routing / a pub-sub bus such as Redis) since each socket pins to one Node instance.[39][40]

**Success rate & caveats.** Highest rate of firewalling of the three JS-native options: enterprise firewalls with packet inspection (Sophos, WatchGuard, Trellix Web Gateway) can break or block WebSocket upgrades, and some corporate MITM/HTTP proxies strip the Upgrade header -- mitigated by using wss:// on 443 so traffic is indistinguishable from HTTPS.[37][38] Raw WebSocket has no built-in reconnect, so the client must implement reconnect with exponential backoff.[37][38] Long-lived idle sockets are silently killed by NAT timers and load balancers, so both sides need ping/pong keepalive.[37][38]

**Client dependencies (Python background service).** Python: `websockets` (v17, pure-Python, very mature, asyncio/threading/trio backends, built-in handshake and ping/pong) is the de-facto standard; `websocket-client` is the synchronous alternative.[39][40] Reconnect/backoff logic still must be written by the app.[39][40]

**SvelteKit/Node.js stack synergy.** Strong on the server side but not fully 'free' inside SvelteKit routing: Node natively has http/networking, and `ws` is one npm install that attaches to the same Node HTTP server SvelteKit's adapter-node already runs.[37][38] Crucially, SvelteKit has no native WebSocket handler (GitHub issue #1491 is still open) -- the socket layer must live outside SvelteKit's route handlers, wired in through the Node adapter.[37][38] The browser side is free (native WebSocket API).[37][38] Not free: connection lifecycle, reconnect, heartbeats, and cross-node routing of prompts to whichever server holds a given device's socket.[37][38]

**Viability notes.** Best fit for the LLM request/response pattern: the prompt (server->client) and the streamed tokens (client->server) both ride the same already-open socket with no per-message HTTP handshake, so round-trips are minimal and both directions stream natively (full-duplex, binary and text, with backpressure). This is the classic control-plane + streaming-tunnel architecture. The only real downside is the small-but-real population of enterprise networks that break WebSocket; mitigate with wss:// on 443 and an SSE/POST or long-poll fallback. Infra cost on a Node/SvelteKit stack is near-zero to start and scales with standard techniques.[unverified]


### Server-Sent Events (SSE) + HTTP POST for the return channel -- Viability: 3/5

**How it works.** The client opens a plain outbound HTTP GET to a cloud /events endpoint; the server replies with Content-Type: text/event-stream and keeps the response open, streaming named events (prompts) down as they occur.[42][43] Because the client initiated that GET, the NAT mapping is established by an outbound request.[42][43] For the client->server direction the client makes ordinary outbound HTTP POSTs -- also client-initiated, so that direction is likewise NAT-traversal-free.[42][43] Both channels are outbound-only, even though one is a long-lived server-push stream.[42][43]

**Infrastructure needed.** Only an HTTP endpoint that can hold a long-lived streaming response -- no separate service and no special server software.[40][44] On a long-running Node/SvelteKit server (adapter-node) this is a +server.ts route returning a ReadableStream with text/event-stream content type, plus an app-level registry mapping device-id -> writable stream.[40][44] Caveat: SSE genuinely needs a long-running Node process rather than a serverless adapter (serverless/edge platforms buffer, truncate, or time out long-lived responses).[40][44]

**Success rate & caveats.** Best firewall compatibility of the three JS-native options -- it's plain HTTP, no trouble with corporate packet inspection.[42][43] Failure modes instead: proxies/load balancers that buffer the response body (defeats streaming) or idle-timeout long-lived HTTP responses -- addressed by periodic keep-alive comment lines.[42][43] It is one-way, so the return channel is a separate POST per message: token streaming client->server becomes chunked-encoding uploads or many small POSTs (added latency/overhead).[42][43] No binary (UTF-8 text only).[42][43] A Python client must implement reconnect + resume manually.[42][43]

**Client dependencies (Python background service).** Python: no heavyweight dependency -- httpx (or requests) with streaming reads consumes SSE; sseclient-py exists but is far less battle-tested than WebSocket/MQTT libraries.[40][44] Client must hand-roll reconnect, keepalive detection, and Last-Event-ID resume, plus separately implement the POST return channel.[40][44]

**SvelteKit/Node.js stack synergy.** Highest native synergy: SSE is nothing more than a standard Response/ReadableStream returned from a SvelteKit server route, using the exact fetch/Response web APIs SvelteKit already builds on -- zero new libraries, zero new services, browser side is native EventSource.[42][43] What is not free: SvelteKit gives nothing for holding thousands of open streams, routing a prompt to the right stream, or the client->server token channel, which must be POSTs built by hand.[42][43]

**Viability notes.** Traverses NAT correctly and has the strongest firewall pass-rate, but it is a poor fit for the LLM token stream specifically: server->client prompt push is clean, yet client->server streaming over POST adds per-chunk overhead/latency and complexity, and there's no native bidirectional streaming or backpressure the way WebSocket provides. More moving parts and higher per-message cost for the direction that matters most (token generation). Well-suited as the fallback path when WebSocket is blocked, not as the primary transport.[unverified]


### MQTT (pub/sub broker, TCP/TLS or WebSocket transport) -- Viability: 3/5

**How it works.** The Python service acts as an MQTT client and makes an outbound CONNECT to a broker on a public address; the broker replies CONNACK and keeps the connection open so messages flow in both directions over that client-initiated socket (clients behind NAT routers have no difficulty precisely because the client initiates the connection).[45][46] The cloud control plane publishes a prompt to a per-device topic (e.g.[45][46] devices/<id>/in) and subscribes to devices/<id>/out where the device publishes token chunks; the broker routes messages by topic, fully decoupling the two endpoints.[45][46]

**Infrastructure needed.** A broker -- Mosquitto, EMQX, HiveMQ, or a managed MQTT service -- which is a wholly separate service/process alongside the Node app; a SvelteKit/Node backend cannot host an MQTT broker natively at production scale (aedes exists as an embeddable Node broker for small cases only).[47][48] The broker provides auth, TLS, persistent sessions, QoS, and is built to hold millions of concurrent connections.[47][48] For firewall-friendliness the broker should also listen over MQTT-over-WebSocket on 443.[47][48]

**Success rate & caveats.** On the default MQTT ports (1883/8883) non-80/443 outbound traffic is frequently blocked by corporate firewalls/DPI -- mitigation is running MQTT-over-WebSocket on 443.[45][46] MQTT has built-in KeepAlive + PINGREQ; shortening the keepalive interval prevents NAT gateways from dropping idle connections.[45][46] Still needed: reconnect handling and re-subscription (unless persistent sessions used); broker availability/scale-out is a separate operational concern.[45][46] Messages passing through the broker hop add small added latency vs a direct WebSocket.[45][46]

**Client dependencies (Python background service).** Python: paho-mqtt (Eclipse Paho, ~2.4k stars) -- the canonical, battle-tested client implementing MQTT 5.0/3.1.1/3.1 with a threading-based network loop, callbacks, and auto-reconnect options.[47][48] Most drop-in of the three for a background service (no asyncio rewrite required).[47][48]

**SvelteKit/Node.js stack synergy.** Lowest synergy of the three JS-native options.[45][46] The broker is genuinely new infrastructure to deploy, secure, and scale as a separate system; SvelteKit/Node contributes nothing toward it -- Node merely becomes an MQTT client via mqtt.js.[45][46] For a product whose backend is otherwise a single Node/SvelteKit app, MQTT adds a second always-on distributed system with its own auth, TLS, topics, and ops burden.[45][46] The payoff -- built-in QoS, retained messages, persistent sessions, proven scale -- is real but more than a 'route inference to this PC' relay needs.[45][46]

**Viability notes.** Extremely battle-tested for exactly 'thousands of devices behind NAT' (mqtt.org markets scaling to millions of things), and solves reconnect/keepalive/session semantics for free where WebSocket makes you build them. But for the LLM request/response streaming pattern it's an awkward fit: it's a pub/sub messaging fabric, not a request/response stream -- publishing one message per token chunk incurs per-message overhead and a broker hop each time, and ordering/backpressure and prompt<->completion correlation are bolted on via topic pairs. Combined with the separate broker's infra cost, it scores lower than WebSocket for this specific use.[unverified]


## P2P frameworks & router-assisted / emerging transports

### libp2p (AutoNAT + circuit relay v2 + DCUtR/hole punching + identify) -- Viability: 3/5

**How it works.** libp2p's NAT traversal is built from four cooperating protocols.[50][51] Identify lets peers exchange their observed external addresses (STUN-like); AutoNAT asks random peers to dial back to determine reachability; circuit relay v2 provides TURN-style relay reservations so a private node can be dialed through a public relay; DCUtR (Direct Connection Upgrade through Relay) coordinates a near-simultaneous dial from both ends to punch a hole and upgrade the relayed connection to a direct TCP or QUIC connection.[50][51]

**Infrastructure needed.** Requires at least one publicly reachable libp2p node acting as bootstrap/relay (circuit relay v2, AutoNAT server, DCUtR enabled).[53][54] For a private controlled network you would run your own relay (go-libp2p or rust-libp2p) and hardcode its multiaddr as bootstrap peer.[53][54] Since the cloud endpoint is always public, hole punching here is one-sided, simplifying coordination.[53][54] This relay is separate infrastructure from the SvelteKit app, though a js-libp2p relay could run in-process inside Node.js.[53][54]

**Success rate & caveats.** Direct hole punching fails against symmetric NATs, double-NAT and CGNAT, falling back to the relayed path.[50][51] The Protocol Labs DINPS 2022 paper measured 86% TCP and 93% QUIC hole-punch success in a ~45-volunteer residential test, with ~52% of IPFS nodes found behind a NAT; retrying more than 3 times did not improve success.[50][51] Strict corporate firewalls may block the relay's port or QUIC/UDP, forcing a TCP-only relay path.[50][51]

**Client dependencies (Python background service).** py-libp2p is the official Python implementation; its README still labels it 'under development' (625 GitHub stars, vs.[53][54] mature go-libp2p/rust-libp2p).[53][54] Feature matrix marks circuit-relay-v2, autonat, hole-punching and identify as done, but cross-implementation interop with a go-libp2p relay remains a maturity risk.[53][54] Lower-risk path is a compiled go-libp2p or rust-libp2p daemon sidecar bundled by the installer.[53][54]

**SvelteKit/Node.js stack synergy.** Minimal.[50][51] Node.js can run js-libp2p, so a SvelteKit/Node backend could host the relay node in-process instead of a separate Go binary -- the only real synergy.[50][51] But libp2p's discovery/bootstrap/DHT machinery is net-new infrastructure and overkill for a single client-to-cloud relationship.[50][51]

**Viability notes.** Battle-tested at massive scale (IPFS, Ethereum) and handles arbitrary NATs via the relay fallback, so it would work. But it is heavyweight for a point-to-point client-to-cloud need -- you would deploy a private relay plus libp2p plumbing where a simple reverse tunnel suffices. The deciding factor is Python client maturity -- py-libp2p is the least-developed libp2p implementation, so the installer would likely need a Go/Rust sidecar, adding complexity.[unverified]


### UPnP IGD / NAT-PMP / PCP (router-assisted port mapping) -- Viability: 2/5

**How it works.** The client discovers the local gateway via SSDP multicast and then invokes SOAP actions on the router's Internet Gateway Device (IGD) service, principally AddPortMapping, to map an external port to the client's internal IP:port.[57][58] NAT-PMP (UDP 5351) and its successor PCP are lighter binary alternatives, with PCP adding IPv6 support.[57][58] Once mapped, the router forwards inbound traffic to the client, which reports its public IP:port to the cloud so it can be dialed directly -- no relay involved.[57][58]

**Infrastructure needed.** None server-side.[59][60] Entirely client-to-router; the cloud only needs to receive and store the client's self-reported public endpoint.[59][60] No relay, TURN, or proxy infrastructure required.[59][60]

**Success rate & caveats.** UPnP is disabled by default on many consumer routers and almost universally disabled in corporate/enterprise networks for security reasons (unauthenticated LAN hosts opening ports; exploited by malware such as Conficker).[57][58] Fails entirely behind CGNAT, where the reported public IP is itself behind the ISP's NAT.[57][58] Widespread IGDv1/v2 interop bugs exist; NAT-PMP/PCP adoption is low (only Apple routers, AVM, OpenWrt, OPNsense and pfSense known to support PCP).[57][58] Cannot be relied upon for arbitrary, unconfigured networks.[57][58]

**Client dependencies (Python background service).** miniupnpc: a mature, tiny (<50 KB) pure-C UPnP IGD client library for Windows/macOS/Linux with Python bindings (also on PyPI, a documented dependency of py-libp2p).[59][60] libnatpmp is the equivalent for NAT-PMP/PCP.[59][60] Both very production-ready (used by Transmission and other BitTorrent clients and games).[59][60] Lightest possible dependency.[59][60]

**SvelteKit/Node.js stack synergy.** None needed -- there is no server-side component at all.[57][58] The only integration point is recording the client's self-reported public endpoint, trivial in any backend.[57][58]

**Viability notes.** Trivial to implement and zero infrastructure cost, but far too unreliable to be the mechanism that guarantees reachability: corporate networks block it, CGNAT breaks it, many home routers ship with it off, and interop bugs abound. Only viable as a best-effort direct-connect fast path layered on top of a guaranteed reverse tunnel, never as the sole strategy.[unverified]


### QUIC/HTTP3-based tunneling (MASQUE / reverse tunnel over QUIC) -- Viability: 4/5

**How it works.** The client opens an outbound QUIC connection to a cloud relay and keeps it alive; because it is outbound, it passes through almost any NAT/firewall and punches the NAT mapping.[62][63] Data is multiplexed over QUIC streams and DATAGRAM frames.[62][63] MASQUE standardizes HTTP-based tunneling (RFC 9298 CONNECT-UDP, RFC 9484 connect-ip); in the reverse-tunnel orientation used here, the client tunnels to the server and the server forwards inbound inference requests down the persistent tunnel.[62][63] QUIC adds connection migration (survives client IP/network changes), multiplexing and 0-RTT.[62][63]

**Infrastructure needed.** A cloud-side QUIC/HTTP3-speaking relay with a public IP and UDP 443 open, built on aioquic (Python), quic-go/quiche (Go/Rust), or a managed service such as Cloudflare Tunnel.[64][65] Separate infrastructure from the SvelteKit app but can run behind the same domain.[64][65] A TCP fallback path (WebSocket/gRPC over 443) is advisable for UDP-blocked networks.[64][65]

**Success rate & caveats.** QUIC runs over UDP, which is occasionally blocked or throttled by strict firewalls/corporate networks that permit only TCP 80/443, so a TCP fallback is recommended.[62][63] A pure P2P direct path still fails behind symmetric NATs, but a relay-based reverse tunnel sidesteps that entirely because the connection is always client-initiated and persistent.[62][63] MASQUE itself is early-stage: HTTP/3 + extended CONNECT + DATAGRAM server support is not yet ubiquitous, so off-the-shelf MASQUE proxy endpoints are rare and the tunnel protocol may need to be implemented by hand.[62][63]

**Client dependencies (Python background service).** aioquic: a mature, production-grade Python QUIC/HTTP3 library (2.0k GitHub stars; used by dnspython, hypercorn, mitmproxy; QUIC v1/v2, HTTP/3, DATAGRAM per RFC 9297, connection migration).[64][65] Does not ship a ready-made MASQUE connect-udp/connect-ip proxy endpoint, but all QUIC primitives are present for a custom reverse tunnel.[64][65] Alternative: bundle a managed tunnel client (cloudflared) at the cost of an external binary/account dependency.[64][65]

**SvelteKit/Node.js stack synergy.** Partial.[62][63] SvelteKit/Node gives nothing for free (Node has no built-in QUIC/HTTP3 server), but the reverse tunnel integrates cleanly with a web control plane -- the relay can live behind the same domain.[62][63] If Cloudflare Tunnel/edge is used the synergy is stronger since Cloudflare already fronts many SvelteKit deployments; otherwise the QUIC relay is a modest separate service regardless of web stack.[62][63]

**Viability notes.** Best architectural fit of the three P2P/router-assisted alternatives: a client-initiated, persistent reverse tunnel is exactly the primitive that makes a NAT'd PC reachable, works through any NAT/CGNAT that allows outbound UDP 443, needs no router configuration, and survives network changes via connection migration. Tradeoffs -- running/building a relay and handling UDP-blocked networks with a TCP fallback -- are real but bounded. Scored 4 rather than 5 because aioquic-based tunneling is custom work and MASQUE is immature; a mature managed tunnel (Cloudflare) would be a 5 but adds an external dependency.[unverified]


## Notes on scope and confidence

- **This is a survey, not a decision.** No code has been written; nothing here has been
  prototyped against a real corporate firewall or CGNAT deployment. Viability scores are
  the research subagents' informed synthesis of the cited sources, not measured data from
  this project.[unverified]
- **Success-rate figures vary by source and age.** The oft-cited "64% TCP / 82% UDP hole
  punch success" figures trace back to Ford, Srisuresh & Kegel's 2005 measurement study[9],
  which predates widespread CGNAT deployment and modern stateful corporate firewalls --
  actual numbers today are very plausibly lower for the raw hole-punching techniques.[unverified]
  Tailscale's >90% figure[5] and Protocol Labs' 86%/93% figure[56] are more recent
  (2020s) but come from each project's own infrastructure and user base, not an
  independent third-party study.[unverified]
- **"Viability for this use case" is not "technical soundness."** Several techniques
  scored low here (UPnP, raw WireGuard, TCP hole punching) are technically sound and
  widely used elsewhere -- they score low specifically because this project's requirement
  (works on *any* unconfigured network, no user configuration, shippable via a one-line
  installer) is unusually strict.[unverified]

## Recommendation (for discussion, not a final decision)

Two techniques tie for the top viability score (5/5), and they are complementary rather
than competing:

1. **Cloudflare Tunnel** as the transport-agnostic reachability layer. It is free at any
   scale (no per-tunnel metering), fully managed (Cloudflare operates the relay/edge, DNS,
   and TLS), works through arbitrary NAT/CGNAT/corporate-firewall configurations because it
   is a pure outbound connection, and a per-user tunnel token can be minted via Cloudflare's
   API at install time -- fitting a one-line installer well. The only structural
   requirement is moving the product's DNS to Cloudflare.
2. **A persistent outbound WebSocket** as the actual application-level protocol riding
   inside that tunnel (or directly, for users not needing Cloudflare's help). It is the
   best fit for the LLM request/response pattern specifically -- prompt down, tokens
   streamed back, full-duplex, low overhead -- and a SvelteKit/Node backend already has
   native WebSocket support via the `ws` library, with no separate broker or relay
   service of its own to run.

[unverified] A layered fallback strategy following the same "always have an outbound-only path" theme:

- **Primary:** persistent outbound WebSocket (`wss://`) directly to the cloud's Node
  service, on port 443, indistinguishable from ordinary HTTPS traffic to most firewalls.
- **If corporate DPI/proxies break the WebSocket upgrade:** fall back to Cloudflare Tunnel
  (cloudflared), which has its own independent path through Cloudflare's edge and is
  documented to survive networks that a raw WebSocket cannot.
- **If neither works (very locked-down corporate network):** SSE + POST as a last-resort
  degraded mode (accepting the added latency/complexity on the client-to-server
  direction), since it has the best plain-HTTP compatibility of everything surveyed.

Techniques explicitly **not recommended** as the primary mechanism, with reasons:
- Raw WireGuard, UPnP, and plain TCP/UDP hole punching all require infrastructure this
  project would have to build from scratch (rendezvous, STUN, relay) to reach a
  reliability level that Cloudflare Tunnel or Tailscale already provide off the shelf,
  for equal or less engineering cost.
- Tailscale/Headscale is a strong second-place architecture but requires installing a
  virtual network adapter with admin/root on every end-user machine, which is a heavier
  install footprint than a single outbound WebSocket connection and a plausible blocker
  in corporate/MDM-managed environments.
- MQTT and libp2p bring real IoT/P2P-scale credentials but add an entire additional
  distributed system (broker or bootstrap/relay network) that a single Node/SvelteKit
  cloud backend does not need for a one-device-to-one-cloud-endpoint relationship.

**Suggested next research step**, if this direction is pursued: a small spike prototyping
the WebSocket-primary / Cloudflare-Tunnel-fallback pattern against 2-3 real corporate
network configurations (a locked-down office Wi-Fi, a CGNAT'd home ISP connection, and a
network with an explicit HTTPS-inspecting proxy) to validate the paper reachability
claims made by the cited sources against this project's actual traffic pattern.

## Addendum: Wire Format, Infra Lift, Reliability, and Simplicity

Follow-up analysis against four specific axes the user flagged, informed by
examining llama-server's actual OpenAI-compatible surface and by prior art for
tunneling arbitrary HTTP traffic over a single outbound connection.

### What "encapsulate the OpenAI-compatible API" actually requires

llama-server's `/v1/chat/completions` endpoint is not a narrow JSON RPC call.
Inspecting its own README shows it accepts, in addition to plain text
messages: base64-encoded or locally-pathed images/audio/video inside
`messages[i].content[j]`, arbitrary JSON `response_format` schemas, and either
a single JSON response or a Server-Sent-Events stream depending on the
`stream` flag[73]. It also exposes `/v1/chat/completions/control` as a
**second, concurrent** HTTP call a client sends *while a stream from the first
call is still open*, to steer or terminate an in-flight generation[73][74].

This has one clear design implication: whatever transport is chosen needs to
carry **arbitrary, unmodified HTTP semantics** -- multiple content types
(JSON, base64 binary, SSE), multiple concurrent in-flight requests over one
connection, arbitrary headers -- not just "send a chat message, get tokens
back." Building a narrow custom message schema (e.g. a bespoke JSON-RPC method
per llama-server endpoint) means re-deriving OpenAI-API compatibility by hand
and re-doing it every time llama-server's surface grows. The lower-maintenance
path is to make the transport dumb: **carry raw HTTP request/response bytes
end to end, and let llama-server's own HTTP server (which already fully
implements the API) do all the interpretation.** This is exactly the "thin
wrapper around an HTTP REST API" framing from the prompt.

### Prior art: this exact problem is already solved

Tunneling arbitrary HTTP over a single persistent outbound connection is a
well-trodden pattern with multiple mature reference implementations:

- **wstunnel**[67]: tunnels arbitrary TCP/UDP traffic (including raw HTTP)
  over a WebSocket or HTTP/2 connection specifically to bypass firewalls/DPI,
  with static binaries for all three OSes. Its entire purpose is "make
  arbitrary traffic look like ordinary WebSocket/HTTPS traffic to a firewall."
- **chisel**[66]: a single Go binary implementing a client-initiated tunnel
  (secured via SSH framing) that multiplexes many logical TCP streams over
  one outbound HTTP connection, explicitly built "for passing through
  firewalls." Notably it already handles reconnection with exponential
  backoff and detects silently-dead connections via keepalive pings[66] --
  solving axis 3 (reliability) as a side effect of its transport design.
- **localtunnel**[72] and **smee.io/smee-client** are simpler, JS-native
  versions of the same idea (client dials out, server proxies public HTTP
  requests back down the tunnel) -- smee-client in particular is exactly a
  Node.js package receiving webhook-shaped HTTP payloads and forwarding them
  to a local HTTP server, a close structural cousin of "receive an inference
  request, forward to local llama-server."
- **yamux**[70] and **muxado**[68] are the underlying primitive both chisel
  and many other tools build on: a generic stream-multiplexing protocol over
  any single reliable byte connection (TCP, or a WebSocket's byte stream).
  Both explicitly call out NAT traversal and server-initiated streams as a
  design goal[68][70] -- the cloud can open a new logical "stream" toward the
  client down the same connection the client dialed out with, without the
  client ever listening on a port.
- **Chrome DevTools Protocol (CDP)**[69] is a real-world example of a
  request/response + streamed-event protocol running over a single
  WebSocket, using a JSON-RPC-shaped envelope (`id`, `method`, `params` /
  `result`) to correlate concurrent in-flight calls -- the same
  request-correlation problem this project has with `/v1/chat/completions`
  running concurrently with `/v1/chat/completions/control`. JSON-RPC 2.0
  itself[71] is the formal spec CDP's envelope loosely follows, and is a
  reasonable model for wrapping the raw HTTP passthrough with a lightweight
  envelope only where correlation is needed.

### Recommendation: yamux-style multiplexed HTTP-over-WebSocket

Combining these axes, the design that scores best across all four:

**One outbound `wss://` WebSocket connection, multiplexed with a yamux-style
framing, where the cloud opens a new logical stream per inbound inference
request and each stream carries a literal, byte-for-byte HTTP/1.1
request/response (or just the JSON/SSE body, if terminating HTTP framing
cloud-side).** Concretely:

1. **Wire format (axis 1).** Don't invent a schema for chat messages. Forward
   the client's raw HTTP request bytes (method, path, headers, body) to
   llama-server's local port over one multiplexed stream, and forward
   llama-server's raw HTTP response bytes (including SSE chunks as they
   arrive) back over the same stream. This means base64 images, `stream:
   true` SSE, arbitrary `response_format` schemas, and any future
   llama-server/OpenAI-API additions pass through with **zero transport-layer
   changes ever required** -- the transport doesn't know or care what's
   inside the HTTP body. A concurrent `/v1/chat/completions/control` call
   just opens a second logical stream on the same WebSocket while the first
   is still flushing SSE chunks; yamux-style multiplexing is designed
   exactly for this (bidirectional streams openable by either side, useful
   for NAT traversal, per hashicorp's own description[70]).
2. **Infrastructure lift (axis 2).** The cloud side needs exactly one
   component: a WebSocket endpoint on the existing Node/SvelteKit HTTP
   server (the `ws` library, as already covered in the prior report[38]).
   No broker, no relay VPS, no additional protocol server -- the
   multiplexing and HTTP-forwarding logic is application code inside the one
   process already running. This is the fewest-additional-components answer
   among every option surveyed in the base report.
3. **Reliability (axis 3).** All the "stay connected" complexity is
   contained in exactly one place: the WebSocket connection's own
   liveness. There is no separate session/heartbeat state to track per
   in-flight request -- individual inference requests are just streams
   inside the one connection, so they live and die with requests, not with
   connection health. The client only needs one piece of logic:
   reconnect-with-backoff on the single outer WebSocket (as chisel and
   Tailscale both already do as their whole job[5][66]). No request-level
   session resumption logic is needed since HTTP requests/responses are
   inherently short-lived compared to the outer connection's lifetime.
4. **Simplicity for an open-source project (axis 4).** This is a widely
   understood pattern with multiple readable open-source reference
   implementations to point contributors at (chisel and wstunnel are both
   single-purpose, single-binary, well-documented tools whose entire
   READMEs are "here's how HTTP-over-WebSocket tunneling works")[66][67].
   The Python client side needs only the `websockets` library plus a small,
   auditable amount of code: read HTTP request bytes off a multiplexed
   stream, `httpx`/`requests` them to `localhost:8080`, write the response
   bytes back. No custom binary framing has to be invented from scratch --
   yamux's frame format is a published, implementable spec[70], or the
   project can start even simpler (one WebSocket message per HTTP
   request/response pair, forgoing true multiplexing until concurrency
   demands it) and add multiplexing later without changing the wire
   format's fundamental shape.

This narrows the base report's tied 5/5 recommendation (Cloudflare Tunnel +
persistent WebSocket) into a single concrete wire-level design: the
persistent WebSocket **is** the transport, and what rides inside it is raw
HTTP passthrough with yamux-style multiplexing for concurrent requests --
not a hand-rolled chat-message protocol. Cloudflare Tunnel remains a viable
*deployment-level* fallback (it can carry this same WebSocket, so nothing
about this design conflicts with also offering it as a corporate-firewall
escape hatch from the base report).

## Sources

[1] https://www.rfc-editor.org/rfc/rfc8489 — RFC 8489: Session Traversal Utilities for NAT (STUN)
[2] https://www.rfc-editor.org/rfc/rfc8656 — RFC 8656: Traversal Using Relays around NAT (TURN)
[3] https://www.rfc-editor.org/rfc/rfc8445 — RFC 8445: Interactive Connectivity Establishment (ICE)
[4] https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Protocols — Introduction to WebRTC protocols - MDN
[5] https://tailscale.com/blog/how-nat-traversal-works — How NAT traversal works - Tailscale
[6] https://github.com/coturn/coturn — coturn TURN server project (GitHub)
[7] https://github.com/aiortc/aiortc — aiortc: WebRTC and ORTC implementation for Python (GitHub)
[8] https://en.wikipedia.org/wiki/UDP_hole_punching — UDP hole punching - Wikipedia
[9] https://bford.info/pub/net/p2pnat — Peer-to-Peer Communication Across Network Address Translators (Ford, Srisuresh, Kegel)
[10] https://en.wikipedia.org/wiki/TCP_hole_punching — TCP hole punching - Wikipedia
[11] https://deepwiki.com/pirate/wireguard-docs/4.2-nat-traversal — NAT Traversal | pirate/wireguard-docs | DeepWiki
[12] https://blogs.meshwg.com/blog/wireguard-nat-traversal-behind-cgnat-2026 — WireGuard NAT Traversal: Connecting Peers Behind CGNAT & Firewalls (2026) - MeshWG Blog
[13] https://tailscale.com/blog/nat-traversal-improvements-pt-1 — How Tailscale is improving NAT traversal (part 1)
[15] https://tailscale.com/kb/1257/connection-types — Connection types - Tailscale Docs
[16] https://headscale.net — Headscale - open source, self-hosted Tailscale control server
[18] https://docs.zerotier.com/protocol — The Protocol | ZeroTier Documentation
[19] https://docs.zerotier.com/roots — ZeroTier Documentation - Roots & Moons
[20] https://github.com/zerotier/ZeroTierOne — GitHub - zerotier/ZeroTierOne
[21] https://docs.zerotier.com/start — ZeroTier Documentation - Getting Started / ZeroTier Central
[22] https://ngrok.com/docs/share-localhost/tunnels — How Sharing Localhost Works - ngrok documentation
[23] https://ngrok.com/docs/pricing-limits/free-plan-limits — Free Plan Limits - ngrok documentation
[25] https://martinuke0.github.io/posts/2025-12-17-how-ngrok-works-a-deep-technical-walkthrough — How ngrok Works - A Deep Technical Walkthrough
[26] https://ngrok.com/pricing — ngrok Pricing
[28] https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel — Cloudflare Tunnel - Cloudflare One docs
[29] https://developers.cloudflare.com/tunnel — Cloudflare Tunnel - Cloudflare Docs
[30] https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads — Downloads - Cloudflare One docs
[31] https://homelabstarter.com/homelab-cloudflare-tunnel — Cloudflare Tunnel: Expose Home Lab Services Without Port Forwarding
[33] https://github.com/fatedier/frp — GitHub - fatedier/frp: A fast reverse proxy to expose a local server behind NAT
[34] https://github.com/fatedier/frp/releases — Releases - fatedier/frp
[35] https://github.com/rathole-org/rathole — GitHub - rathole-org/rathole
[36] https://fastnat.com/compare/rathole-vs-frp — rathole vs frp (2026): which self-hosted tunnel is faster? - FastNAT
[37] https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API/Writing_WebSocket_servers — Writing WebSocket servers - Web APIs | MDN
[38] https://github.com/websockets/ws — websockets/ws: WebSocket client and server for Node.js
[39] https://websockets.readthedocs.io/en/stable — websockets 17.0.1 documentation (Python)
[40] https://ably.com/blog/websockets-vs-sse — WebSockets vs Server-Sent Events: Key differences and which to use (Ably)
[42] https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events — Using server-sent events - Web APIs | MDN
[43] https://developer.mozilla.org/en-US/docs/Web/API/EventSource — EventSource - Web APIs | MDN
[44] https://ably.com/topic/server-sent-events — Server-Sent Events: A WebSockets alternative ready for another look (Ably)
[45] https://mqtt.org — MQTT - The Standard for IoT Messaging
[46] https://www.hivemq.com/blog/mqtt-essentials-part2-publish-subscribe — MQTT Publish/Subscribe Architecture (Pub/Sub) - MQTT Essentials: Part 2 | HiveMQ
[47] https://www.hivemq.com/blog/mqtt-essentials-part-3-client-broker-connection-establishment — MQTT Client, Broker, and Server Connection Establishment Explained - MQTT Essentials: Part 3 | HiveMQ
[48] https://github.com/eclipse-paho/paho.mqtt.python — eclipse-paho/paho.mqtt.python
[50] https://docs.libp2p.io/concepts/nat — What are NATs | libp2p documentation
[51] https://docs.libp2p.io/concepts/nat/hole-punching — Hole Punching | libp2p documentation
[53] https://blog.ipfs.tech/2022-01-20-libp2p-hole-punching — Hole punching in libp2p - Overcoming Firewalls | IPFS Blog
[54] https://github.com/libp2p/py-libp2p — libp2p/py-libp2p
[56] https://research.protocol.ai/publications/decentralized-hole-punching — Decentralized Hole Punching (DINPS 2022, Seemann, Inden, Vyzovitis)
[57] https://en.wikipedia.org/wiki/Universal_Plug_and_Play — Universal Plug and Play - Wikipedia
[58] https://en.wikipedia.org/wiki/Internet_Gateway_Device_Protocol — Internet Gateway Device Protocol - Wikipedia
[59] https://en.wikipedia.org/wiki/NAT_Port_Mapping_Protocol — NAT Port Mapping Protocol - Wikipedia
[60] https://en.wikipedia.org/wiki/Port_Control_Protocol — Port Control Protocol - Wikipedia
[62] https://www.rfc-editor.org/rfc/rfc9298.txt — RFC 9298 - Proxying UDP in HTTP (CONNECT-UDP)
[63] https://www.rfc-editor.org/rfc/rfc9484.txt — RFC 9484 - Proxying IP in HTTP (connect-ip)
[64] https://blog.cloudflare.com/unlocking-quic-proxying-potential — Unlocking QUIC's proxying potential with MASQUE | Cloudflare Blog
[65] https://github.com/aiortc/aioquic — aiortc/aioquic: QUIC and HTTP/3 implementation in Python
[66] https://github.com/jpillora/chisel — GitHub - jpillora/chisel: A fast TCP/UDP tunnel over HTTP
[67] https://github.com/erebe/wstunnel — GitHub - erebe/wstunnel: Tunnel all your traffic over Websocket or HTTP2
[68] https://github.com/inconshreveable/muxado — GitHub - inconshreveable/muxado: Stream multiplexing for Go
[69] https://chromedevtools.github.io/devtools-protocol — Chrome DevTools Protocol Viewer
[70] https://github.com/hashicorp/yamux — GitHub - hashicorp/yamux: Golang connection multiplexing library
[71] https://www.jsonrpc.org/specification — JSON-RPC 2.0 Specification
[72] https://github.com/localtunnel/localtunnel — GitHub - localtunnel/localtunnel: expose yourself
[73] https://platform.openai.com/docs/api-reference/chat/streaming — API Overview - OpenAI API Reference
[74] https://platform.openai.com/docs/guides/realtime — Getting started with the Realtime API - OpenAI API
