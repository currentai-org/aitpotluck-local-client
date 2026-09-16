# Proposal: WebSocket Transport for the AI Potluck Local Inference Client

Status: PROPOSED (not yet implemented)
Depends on: `research/nat-traversal/nat-traversal-report.md` (technique survey and axis analysis)
Companion doc: [SPEC.md](SPEC.md) — the wire protocol technical specification

## 1. Problem statement

`aipotluck-local-client` installs llama.cpp + a supervising Python service on an
end user's PC. Today that service only talks to `localhost`. To let the AI
Potluck cloud product route inference requests to a specific user's machine,
the machine must become reachable from the cloud even though it sits behind
an arbitrary, unconfigured home or corporate firewall/NAT — no port
forwarding, no router admin access, possibly CGNAT.

The NAT-traversal research report surveyed 15 techniques across five
categories and scored each against four project-specific axes: **wire
format** (must transparently carry the full OpenAI-compatible chat API
surface, not a narrow hand-picked subset), **infrastructure lift** (fewest
additional components the cloud team has to run), **reliability** (little
extra state/logic to keep a device "connected"), and **simplicity** (an
open-source project other developers can read and trust). Two techniques
tied for the top score: **Cloudflare Tunnel** (deployment-level, zero-cost
outbound relay) and a **persistent outbound WebSocket** (application-level
transport). They are complementary, not competing — Cloudflare Tunnel can
carry the same WebSocket as a corporate-firewall escape hatch.

This proposal is about the second half: **what actually rides inside that
WebSocket.** The follow-up addendum in the research report concluded the
transport should not invent a custom chat-message schema. It should
transparently forward raw HTTP request/response bytes between the cloud and
the device's local `llama-server`, multiplexed so multiple HTTP
exchanges (including the concurrent `/v1/chat/completions/control` call
llama-server itself defines) can be in flight over the one connection.

## 2. What this proposal adds

A concrete, versioned wire protocol — internally called **APLC-1** (AI
Potluck Local Client, protocol version 1) — layered on top of a single
outbound `wss://` connection from the device's Python service to the cloud.
Full byte-level detail is in [SPEC.md](SPEC.md); this document covers the
rationale, scope, and rollout.

### 2.1 Design summary

- **Transport:** one WebSocket connection per device, dialed outbound by the
  device, binary frames only.
- **Framing:** each WebSocket message carries one small, fixed-layout binary
  frame header (type + stream ID) followed by a payload. Control payloads
  (request line, headers, errors) are UTF-8 JSON; data payloads are raw
  bytes. This keeps both ends' parsers trivial — a JSON decoder plus five
  fixed-offset integer reads — and readable by any contributor.
- **Multiplexing:** the cloud is always the stream *initiator* (it is
  proxying an inbound HTTP request from an external caller to the device);
  the device only ever responds on a stream the cloud opened. This is a
  deliberate simplification over general-purpose muxers like yamux, which
  must handle either side opening a stream — this project's request/response
  shape never requires the device to originate a stream, so that entire
  class of protocol complexity (ID-parity collision avoidance, etc.) does
  not exist here.
- **HTTP passthrough, not reinterpretation:** the frame carrying a request
  contains method, path, headers, and query string; body and response both
  ride as opaque byte chunks. The transport does not parse, validate, or
  understand OpenAI/llama.cpp JSON schemas at all — that is deliberately
  llama-server's job, unchanged. Every current and future llama-server
  endpoint (multimodal fields, new `response_format` shapes, new endpoints
  entirely) is automatically supported with zero transport changes.
- **One connection, one liveness concern:** the only "is this device still
  reachable" state is the single WebSocket's own keepalive/reconnect logic.
  Individual in-flight HTTP exchanges are not separately tracked for
  liveness — they live and die with the connection.

### 2.2 What this explicitly does not do (v1 scope)

- **No stream resumption.** If the WebSocket drops mid-request, the request
  fails; the cloud returns an error to its caller. No attempt is made to
  resume a partially-streamed completion across a reconnect. This is a
  conscious simplicity/reliability tradeoff (see SPEC §10) — most failures
  worth architecting around are already covered by the *service* layer this
  project already ships (llama-server crash-restart supervision), not by the
  transport.
- **No flow control / backpressure protocol.** Bounded only by WebSocket's
  own TCP-level backpressure and a configurable max-concurrent-streams cap.
  Documented as a known head-of-line-blocking risk with a clear v2 path
  (SPEC §10.3) rather than solved prematurely.
- **No device-initiated streams.** The device cannot push arbitrary events
  to the cloud outside of responding to a stream the cloud opened. If a
  future feature needs device-initiated push (e.g. proactive health
  telemetry), it is a deliberate, separately-versioned protocol addition,
  not part of APLC-1.
- **No protocol-level authentication scheme.** Device identity and auth
  travel as a bearer token in the WebSocket upgrade request's `Authorization`
  header (ordinary HTTP auth, TLS-protected) — not a custom in-band
  handshake. mTLS or token rotation are noted as v2 hardening options, not
  required for a working v1.

## 3. Why this shape, concretely

| Axis | How APLC-1 addresses it |
|---|---|
| Wire format | Raw HTTP passthrough means the OpenAI-compatible surface (and its future growth) needs zero transport-layer awareness — see SPEC §2 and §7.1. |
| Infra lift | Cloud side is one WebSocket endpoint attached to the existing Node/SvelteKit HTTP server (the `ws` library) — no broker, no relay VPS, no second protocol server. |
| Reliability | A single reconnect-with-backoff loop on one connection is the entire liveness story; no per-request session state to reconcile. |
| Simplicity | ~10 frame types, JSON control payloads, no custom binary framing beyond a 5-byte header — implementable and auditable by any contributor in an afternoon; reference pseudocode for both sides is in SPEC §16. |

## 4. Rollout plan

1. **Land the spec** (this doc + SPEC.md) for review — no code yet.
2. **Implement the client half** in `service/` as a new module
   (`service/transport/`), wired into `AipotluckServiceRunner` alongside the
   existing `LlamaSupervisor` (see `service/runner.py`). The transport
   forwards to `http://127.0.0.1:<llama_port>` exactly as `LlamaSupervisor`
   already knows it — no new discovery mechanism needed.
3. **Implement the cloud half** as a small, independent Node module the
   SvelteKit backend imports (WebSocket endpoint + per-device connection
   registry + an outbound-facing HTTP handler that opens a stream per
   incoming request). This can be developed and tested against the Python
   client entirely independently of the rest of the cloud product.
4. **Smoke test** end-to-end against a real `llama-server` instance for: a
   non-streaming completion, a streaming (SSE) completion, a concurrent
   `/v1/chat/completions/control` call during an active stream, and a
   mid-stream device disconnect (verify the cloud returns a clean error
   rather than hanging).
5. **Optional hardening pass** before wider rollout: path allowlisting on
   the device (SPEC §12), max-concurrent-streams enforcement, structured
   logging of stream lifecycle events for observability.

## 5. Open questions for review

- **Token issuance and storage.** Where does the per-device bearer token
  come from (minted at install time via a cloud API call, analogous to the
  Cloudflare Tunnel per-user-token pattern from the base research), and
  where does the Python service persist it (`runtime.json` plaintext vs. OS
  keychain)? Not blocking for a first implementation, but should be decided
  before any real device token has real value.
- **Max concurrent streams default.** SPEC proposes a default cap
  (`max_concurrent_streams`) advertised by the server in `HELLO_ACK`;
  the right default depends on expected concurrency per device (likely low
  — one active completion plus at most one concurrent control call) and is
  cheap to tune later since it is a negotiated value, not hardcoded.
- **Whether to require Cloudflare Tunnel as well, or ship the raw
  WebSocket as the sole default path.** The base research recommends
  layering (WebSocket primary, Cloudflare Tunnel fallback) but that fallback
  is not part of this protocol proposal — it is a deployment-level decision
  about how the WebSocket's TCP connection reaches the cloud, orthogonal to
  what rides inside it.


## 6. Sanity check: Hugging Face InferenceClient

Verified directly against `huggingface_hub`'s source
(`src/huggingface_hub/inference/_client.py`) and its docs. Short answer:
**no, it would not be any different, and no changes to APLC-1 are needed.**

### 6.1 What InferenceClient actually is

`InferenceClient` is a Python SDK convenience wrapper, not a network
protocol. Its own source comments state the design philosophy plainly:
*"let's make it as easy as possible to use it, even if less optimized"* —
it accepts inputs as bytes/file paths/URLs, guesses content types, and
picks recommended models, but underneath it is making plain HTTPS calls via
`requests`/`huggingface_hub`'s HTTP session helper. There is no WebSocket,
no custom binary framing, no persistent connection of any kind. Every
`InferenceClient` method (`chat_completion`, `audio_classification`,
`text_to_image`, etc.) resolves to one `POST` (or `GET`) to some resolved
`base_url + path`, with either a synchronous JSON response or an SSE stream
for chat completions specifically.

### 6.2 Where it overlaps with "the OpenAI-compatible API"

For `chat_completion()` specifically, when routed through HF's "Inference
Providers" router (`https://router.huggingface.co/v1/chat/completions`),
HF's own docs describe it as a "drop-in replacement for the OpenAI chat
completions API" and show it being called with the literal `openai` Python
SDK pointed at HF's base URL. Text Generation Inference (the HF-maintained
local/self-hosted inference server, llama-server's rough sibling in this
space) implements the identical `/v1/chat/completions` shape for the same
reason. **For the chat-completion case, "encapsulate InferenceClient" and
"encapsulate the OpenAI-compatible API" are the same wire format** — HF
built its chat endpoint to be OpenAI-shaped on purpose, and `InferenceClient`
is just one more HTTP caller of that same shape, no different in kind from
the `openai` SDK itself or from `curl`.

Since APLC-1 never parses the HTTP body or knows anything about OpenAI's
JSON schema (§2, §8), this case is already fully covered with zero
transport changes — the same as it's already covered for the `openai` SDK,
llama.cpp's Python bindings, or a raw `curl` call.

### 6.3 Where InferenceClient goes beyond chat (and beyond this project's current scope)

`InferenceClient` is a *multi-task* client: beyond chat completion, it also
covers `text_to_image`, `automatic_speech_recognition`,
`document_question_answering`, `zero_shot_image_classification`, and
roughly two dozen other HF-specific task types, each hitting its own
task-specific endpoint shape (not the `/v1/chat/completions` shape) on
HF's Inference Providers infrastructure. `llama-server` does not implement
any of these HF-specific task endpoints — it implements the
OpenAI-compatible surface plus llama.cpp-specific endpoints (`/completion`,
`/tokenize`, `/embedding`, `/infill`, etc., see the parent repo's
`vendor/llama.cpp/tools/server/README.md`).

This is **not a transport-protocol gap** — APLC-1's raw-HTTP-passthrough
design would carry any of these task-specific request/response shapes
exactly as transparently as it carries chat completions, since it still
never inspects the body. It is a **backend-capability gap**: if
`aipotluck-local-client` ever wanted to serve HF-style multi-task requests
(image classification, ASR, etc.) from a user's local machine, that would
require running a different/additional local backend that implements those
endpoints (llama-server does not), which is a product-scope decision
entirely orthogonal to this transport spec.

### 6.4 Conclusion

No changes to PROPOSAL.md or SPEC.md are warranted by this comparison.
APLC-1's core design choice — carry raw HTTP bytes and never encode
assumptions about the JSON schema riding inside them — is exactly what
makes it already correct for InferenceClient's chat-completion traffic
(identical to OpenAI's shape) and already extensible, with zero protocol
changes, to InferenceClient's other task types **if** a future local
backend implementing those task endpoints is ever added alongside
llama-server. The transport layer was never the constraint; the set of
HTTP endpoints the local backend exposes is.


## 7. Off-the-shelf transport audit: can we buy instead of build?

Widened the search past "is there a library for HTTP-over-WebSocket" to the actual
category of tool: reverse-proxy tunneling clients for exposing a local HTTP server
through NAT, since that's what we're really building. Evaluated against the same
four axes as §1-4.

### Candidates surveyed

| Project | Language/license | Model | Would it fit? |
|---|---|---|---|
| **frp** (`fatedier/frp`) | Go, Apache-2.0, 109k★ | Client dials out to `frps`; supports TCP/UDP/HTTP/HTTPS proxies, TCP stream multiplexing, custom subdomains, URL routing, health checks, TLS, connection pooling | Closest match — does almost exactly what APLC-1 proposes, already battle-tested |
| **rathole** | Rust, Apache-2.0, 14k★ | Same shape as frp, lighter (≈500KB binary), Noise-protocol encryption built in | Good fit, smaller footprint than frp, less mature ecosystem |
| **chisel** (`jpillora/chisel`) | Go, MIT | SSH protocol tunneled inside a single WebSocket/HTTP connection; multiplexes many local/remote port-forwards over it | Structurally the closest to APLC-1's own design — SSH-over-WS is one further layer of protocol on top of what we already spec'd |
| **wstunnel** | Rust, BSD | Wraps arbitrary TCP/UDP inside WebSocket or QUIC, designed to look like ordinary HTTPS traffic to firewalls/DPI | Good raw building block but no HTTP-request-awareness — leaves the request/response framing to us anyway |
| **Pangolin** (`fosrl/pangolin`) | TS/Next.js, dual AGPL-3/commercial | Full identity-aware access platform (SSO, dashboards, ZTNA) built on WireGuard, not just a tunnel | Overkill — an entire product, not a library; AGPL-3 also complicates embedding in a permissively-licensed OSS client |
| **sshuttle** | Python, LGPL | Transparent VPN-like proxy over plain SSH, no custom client protocol at all | Elegant but requires SSH server-side, not a good match for a Node/SvelteKit cloud backend |
| **ngrok / localtunnel / bore / gost / tunnelto** | various | Same reverse-tunnel-as-a-service family, mostly TCP/HTTP port forwarding to a public URL | Already covered in the original NAT-traversal report; same trade-offs apply (ngrok = managed-service lock-in; others = smaller/less maintained) |

Source: `anderspitman/awesome-tunneling` (curated list, cross-checked against
each project's own README) plus each project's README directly.

### Verdict: don't adopt one wholesale, but borrow the pattern

None of these projects are HTTP-request-aware in the way APLC-1 needs (they proxy
*ports*, not our specific requirement of one multiplexed request/response stream
per llama-server call including the concurrent control-plane call — see SPEC.md
§7). Adopting frp or rathole wholesale would mean:

- **Infra lift goes up, not down.** They require running their own standalone
  server process (`frps`/rathole server) as a *separate* deployment artifact next
  to the SvelteKit/Node app — exactly the "additional software component" axis 2
  was designed to minimize. Our own WebSocket endpoint living inside the existing
  Node process has zero extra deployables.
- **They solve a superset of our problem.** Generic TCP/UDP port-forwarding,
  custom-domain routing, and load balancing are unneeded complexity for "one
  local client, one cloud endpoint, one HTTP API."
- **License/maintenance friction.** Pangolin's AGPL-3 core, and the general
  pattern of "the OSS repo is a shell around a hosted paid service" (ngrok,
  Pangolin Cloud, several list entries) don't fit an open-source client that
  should work standalone with no vendor dependency.

Where they *do* earn their place in the design: **chisel is effectively a smaller,
production-proven version of what APLC-1 already specifies** (multiplex several
logical connections down one persistent outbound WebSocket/SSH tunnel). That's
independent validation the core approach — not a novel design — is sound and
widely deployed at scale. No changes to SPEC.md are needed; if anything, this
audit reinforces the existing design rather than replacing it.

**Recommendation: keep the custom, minimal APLC-1 transport as specified.** The
closest off-the-shelf option (frp) would cost us infra simplicity to gain features
we don't need. Revisit only if the transport needs to grow beyond
"one HTTP API, one connection" (e.g., multi-service exposure, TCP passthrough for
non-HTTP payloads) — at that point frp/rathole become worth reconsidering.
