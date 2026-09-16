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
