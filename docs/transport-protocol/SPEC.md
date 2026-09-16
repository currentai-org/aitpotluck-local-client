# APLC-1: AI Potluck Local Client Transport Protocol — Technical Specification

**Version:** 1.0 (draft)
**Status:** Proposed, unimplemented
**Audience:** Engineers implementing either the cloud-side (server) or
device-side (client) half of this protocol.

---

## 1. Scope and goals

APLC-1 lets a cloud service ("the Server") send arbitrary HTTP requests to
an OpenAI-compatible HTTP API ("llama-server") running on a private machine
("the Client") that initiated an outbound connection to the Server, without
requiring any inbound network configuration on the Client's network.

APLC-1 is a **binary, message-oriented, HTTP-passthrough multiplexing
protocol** carried over a single WebSocket ([RFC 6455]) connection per
Client. It has one design goal above all others: **never require a change
to this protocol when llama-server's HTTP API changes.** It achieves this by
never parsing HTTP bodies, and by carrying HTTP request/response metadata
(method, path, headers) in a generic envelope rather than a
purpose-specific schema.

### 1.1 Non-goals

- APLC-1 is not a general-purpose RPC protocol. It has exactly one
  supported operation shape: Server opens a stream representing one HTTP
  request; Client responds with one HTTP response (which may itself be
  chunked, e.g. Server-Sent Events).
- APLC-1 does not define authentication, authorization, or device identity
  beyond a single bearer token presented at connection time (§4). Token
  issuance/rotation is out of scope for this document.
- APLC-1 does not define discovery of which port/host `llama-server` runs
  on — that is Client-local configuration (already solved by this project's
  existing `runtime.json`, see the parent proposal §4.2).
- APLC-1 v1 does not support the Client opening a stream toward the Server.
  All streams are Server-initiated.

---

## 2. Why HTTP passthrough (rationale, non-normative)

llama-server's OpenAI-compatible surface accepts, depending on endpoint and
request: plain JSON bodies, base64-encoded binary media embedded in JSON
fields, arbitrary caller-supplied JSON schemas (`response_format`), and
either a single JSON response or a `text/event-stream` (SSE) streamed
response depending on the `stream` request parameter. It additionally
exposes at least one endpoint
(`POST /v1/chat/completions/control`) that a caller sends **concurrently**
with an in-flight streaming request on a different logical exchange, to
steer or cancel the first request.

Any protocol that tries to model "the chat message the user sent" as a
typed field will need to be revised every time llama-server's request or
response shape changes, and cannot losslessly represent arbitrary/future
JSON bodies without becoming, in effect, a full HTTP reimplementation
anyway. APLC-1 instead treats the payload as opaque bytes and forwards
HTTP's own metadata (method, path, headers, status, body chunks)
losslessly. The Client's only protocol-aware code is the framing layer in
this document; everything else is "read some bytes from a stream, write
them to a local TCP HTTP connection, and vice versa."

---

## 3. Transport layer

### 3.1 Connection establishment

The Client is the one and only WebSocket **client** in the WebSocket
sense (it always dials out). The Server is the WebSocket **server**,
exposed at a URL such as:

```
wss://relay.aipotluck.example/v1/device-link
```

The Client MUST connect using TLS (`wss://`, never plaintext `ws://`) in
any deployment reachable over the public internet. The upgrade request
MUST include:

```
GET /v1/device-link HTTP/1.1
Host: relay.aipotluck.example
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Version: 13
Sec-WebSocket-Key: <standard RFC 6455 key>
Authorization: Bearer <device_token>
X-APLC-Device-Id: <device_id>
X-APLC-Protocol-Version: 1
```

- `device_token`: an opaque bearer credential identifying this specific
  Client installation. Issuance is out of scope for this spec (see parent
  PROPOSAL.md §5).
- `device_id`: a stable UUID (v4) generated once at install time and
  persisted locally (e.g. alongside `runtime.json`). Used by the Server for
  logging/routing; MUST NOT be treated as an authentication credential on
  its own (the bearer token is the credential).
- `X-APLC-Protocol-Version`: integer, currently `1`. Servers MUST reject
  (HTTP 400, before the WebSocket upgrade completes) any value they do not
  support, allowing future breaking protocol changes to fail fast and
  visibly rather than silently misbehaving.

If the Server rejects the upgrade (bad token, unsupported version,
disabled device), it responds with a normal HTTP error status
(401/403/400) and no WebSocket upgrade occurs. The Client MUST treat this
as a **fatal** connection attempt (see §9.2 reconnection backoff) rather
than retrying immediately.

### 3.2 Frame encoding: WebSocket message = one APLC-1 frame

Every WebSocket **binary** message on this connection contains exactly one
APLC-1 frame, defined in §5. APLC-1 never uses WebSocket **text** messages
and never spans an APLC-1 frame across multiple WebSocket messages or packs
multiple APLC-1 frames into one WebSocket message. This 1:1 mapping is a
deliberate simplicity choice: implementers can rely entirely on the
WebSocket library's own message boundaries and never write message
reassembly logic.

### 3.3 Keepalive

The Server MUST send a WebSocket ping frame (RFC 6455 §5.5.2) every
`ping_interval_seconds` (default: **20**) of connection idle time (no
APLC-1 frames sent). The Client MUST respond with a pong automatically
(this is standard behavior in essentially every WebSocket library and
requires no APLC-1-level code). If the Server does not receive a pong
within `ping_timeout_seconds` (default: **10**) of sending a ping, it MUST
close the connection with close code `1001` (Going Away) and clean up all
streams for that Client per §7.4.

The Client-side supervisor additionally enforces its own read timeout:
if no data (including pings) is received from the Server for
`client_read_timeout_seconds` (default: **60**), the Client MUST close
and reconnect (§9.2), on the assumption the connection is a "silently
dead" NAT-timed-out TCP connection rather than a live idle one.

---

## 4. Authentication and identity

Authentication for the WebSocket connection itself is handled entirely at
the HTTP-upgrade layer (§3.1) via the `Authorization` header — APLC-1
defines no in-band authentication handshake after the WebSocket is
established. This keeps the frame protocol itself auth-agnostic: swapping
bearer tokens for mTLS client certificates, for example, would not require
any change to §5–§8 of this spec.

Per-request authorization (e.g. "is this specific HTTP path allowed to be
proxied to this device") is a Client-local policy decision, covered
normatively in §12 (Security Considerations).

---

## 5. Frame format

All multi-byte integers are **big-endian** ("network byte order"). All
frames share a fixed 6-byte header:

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Version (u8)  |  Type (u8)    |         Stream ID (u32)       |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        Payload (variable)                    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| Field | Size | Description |
|---|---|---|
| `version` | 1 byte | Frame format version. MUST be `0x01` for this spec. Receivers MUST close the connection (code `1002`, Protocol Error) on any other value. |
| `type` | 1 byte | Frame type, see §5.1. |
| `stream_id` | 4 bytes, `uint32` | Identifies which logical HTTP exchange this frame belongs to. `0` is reserved for connection-level frames (§5.1, `HELLO`/`HELLO_ACK`/`PING_APP`/`GOAWAY`) that are not tied to any single stream. |
| `payload` | remainder of message | Interpreted per `type`, see §6. |

The 6-byte header plus payload MUST exactly fill one WebSocket binary
message (§3.2) — there is no separate length field, because the
WebSocket framing layer already delivers message boundaries.

### 5.1 Frame types

| Type value | Name | Direction | Payload encoding |
|---|---|---|---|
| `0x01` | `HELLO` | Client → Server | JSON (§6.1) |
| `0x02` | `HELLO_ACK` | Server → Client | JSON (§6.2) |
| `0x10` | `REQUEST_HEAD` | Server → Client | JSON (§6.3) |
| `0x11` | `REQUEST_BODY` | Server → Client | raw bytes |
| `0x12` | `REQUEST_END` | Server → Client | empty |
| `0x20` | `RESPONSE_HEAD` | Client → Server | JSON (§6.4) |
| `0x21` | `RESPONSE_BODY` | Client → Server | raw bytes |
| `0x22` | `RESPONSE_END` | Client → Server | empty |
| `0x30` | `STREAM_ERROR` | either direction | JSON (§6.5) |
| `0x31` | `STREAM_CANCEL` | Server → Client | empty |
| `0xF0` | `GOAWAY` | either direction | JSON (§6.6) |

Frame type values are chosen with headroom for future extension: `0x01–0x0F`
reserved for connection lifecycle, `0x10–0x1F` for Server→Client
request-side frames, `0x20–0x2F` for Client→Server response-side frames,
`0x30–0x3F` for stream control, `0xF0–0xFF` for connection-terminal
signaling. Implementations MUST treat any frame `type` they do not
recognize as a no-op (log and ignore) rather than an error, to allow
forward-compatible additions in later minor protocol revisions — except
during the version negotiation window (§7.1), where an unrecognized type
before `HELLO_ACK` is a protocol error.

---

## 6. Frame payload schemas

All JSON payloads are UTF-8 encoded, single-line-or-not (whitespace is
insignificant), and MUST be parseable as a single JSON object (not an
array or scalar) unless otherwise noted. Unknown JSON fields MUST be
ignored by receivers (forward compatibility). All field names are
`snake_case`.

### 6.1 `HELLO` (Client → Server, stream_id = 0)

Sent once, immediately after the WebSocket connection is established,
before any other frame.

```json
{
  "protocol_version": 1,
  "client_version": "aipotluck-local-client/0.3.0",
  "device_id": "b3c1e9d2-4a5f-4e11-9c9a-7f2d6a1b0c33",
  "capabilities": ["gzip_body"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `protocol_version` | integer | yes | MUST equal `1`. |
| `client_version` | string | yes | Free-form; for Server-side logging/telemetry only. |
| `device_id` | string (UUID) | yes | MUST match the `X-APLC-Device-Id` upgrade header. |
| `capabilities` | array of string | no | Optional feature flags the Client supports (§14, forward-compatibility mechanism). Empty/absent means "no optional capabilities." |

### 6.2 `HELLO_ACK` (Server → Client, stream_id = 0)

Sent once, in direct response to `HELLO`. The Server MUST NOT send any
`REQUEST_HEAD` frame before sending `HELLO_ACK`.

```json
{
  "protocol_version": 1,
  "session_id": "sess_9f1c2a3b4d5e",
  "max_concurrent_streams": 8,
  "ping_interval_seconds": 20,
  "capabilities": []
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `protocol_version` | integer | yes | Echo of the negotiated version (always `1` for this spec revision). |
| `session_id` | string | yes | Server-generated opaque identifier for this connection instance, for log correlation. Not a security credential. |
| `max_concurrent_streams` | integer | yes | Maximum number of streams (§7) the Server will have open at once toward this Client. The Client MUST NOT need to enforce this (the Server self-limits how many `REQUEST_HEAD` frames it sends), but MAY use it for local resource planning/logging. |
| `ping_interval_seconds` | integer | yes | Informational echo of the Server's configured keepalive interval (§3.3), so the Client can size its own read-timeout relative to it. |
| `capabilities` | array of string | no | Optional feature flags the Server supports, intersected with the Client's advertised set determines what's actually usable this session (§14). |

If the Client does not receive `HELLO_ACK` within `hello_ack_timeout_seconds`
(default: **10**) of sending `HELLO`, it MUST close the connection and
treat it as a failed connection attempt (§9.2).

### 6.3 `REQUEST_HEAD` (Server → Client)

Opens a new stream. `stream_id` MUST be a value the Server has not
previously used on this connection (see §7.2 for allocation rules).

```json
{
  "method": "POST",
  "path": "/v1/chat/completions",
  "query": "",
  "headers": {
    "content-type": "application/json",
    "accept": "text/event-stream"
  },
  "has_body": true
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `method` | string | yes | HTTP method, uppercase (`GET`, `POST`, etc.). |
| `path` | string | yes | Request path only, no query string, no scheme/host (e.g. `/v1/chat/completions`). MUST start with `/`. |
| `query` | string | no | Raw query string, without the leading `?`. Empty string or absent means no query. |
| `headers` | object of string→string | yes | Header names lowercase. May be empty (`{}`). The Server MUST NOT forward hop-by-hop headers (`connection`, `upgrade`, `keep-alive`, `transfer-encoding`) — the Client reconstructs any necessary transport-level headers itself when making the local HTTP call (§8.1). |
| `has_body` | boolean | yes | If `false`, no `REQUEST_BODY` frames will follow for this stream and the Client should treat the body as empty immediately after this frame. If `true`, one or more `REQUEST_BODY` frames follow, terminated by `REQUEST_END`. |

### 6.4 `RESPONSE_HEAD` (Client → Server)

Sent by the Client once it has received HTTP response headers from the
local `llama-server` (or once it has locally determined an error
response, e.g. connection refused — see §8.3).

```json
{
  "status": 200,
  "headers": {
    "content-type": "text/event-stream",
    "cache-control": "no-cache"
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | integer | yes | HTTP status code as returned by (or synthesized on behalf of) llama-server. |
| `headers` | object of string→string | yes | Header names lowercase. Hop-by-hop headers (see §6.3) MUST be stripped by the Client before sending. |

### 6.5 `STREAM_ERROR` (either direction)

Signals that this specific stream cannot continue, without affecting the
rest of the connection or any other stream. Sending a `STREAM_ERROR`
implicitly ends the stream (equivalent to also sending `REQUEST_END` or
`RESPONSE_END` — receivers MUST treat the stream as closed upon receiving
this frame and MUST NOT expect further frames for that `stream_id`).

```json
{
  "code": "upstream_unreachable",
  "message": "connection refused connecting to 127.0.0.1:8080"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | yes | Machine-readable error code, see §6.5.1 for the defined set. Unrecognized codes MUST be treated by receivers as the generic `internal_error`. |
| `message` | string | no | Human-readable detail, for logs only. MUST NOT be parsed programmatically. |

#### 6.5.1 Defined `STREAM_ERROR` codes

| Code | Sent by | Meaning |
|---|---|---|
| `upstream_unreachable` | Client | The Client could not connect to the local llama-server at all (connection refused, DNS failure — should not occur for `127.0.0.1` but included for completeness, timeout establishing the local TCP connection). |
| `upstream_timeout` | Client | The local llama-server accepted the connection but did not respond within the Client's configured local-request timeout (§8.2). |
| `invalid_request` | Client | The `REQUEST_HEAD` frame failed local validation (§12.1) before any local request was attempted — e.g. disallowed path. |
| `stream_limit_exceeded` | Client | The Client is enforcing its own concurrency cap (optional, §7.3) and is refusing this stream. |
| `internal_error` | either | Unclassified failure. Always valid as a fallback. |

### 6.6 `GOAWAY` (either direction, stream_id = 0)

Signals that the sender intends to close the WebSocket connection soon and
will not open (Server) or accept (Client) further streams. This exists so
an orderly shutdown (e.g. Server-side deploy, Client-side service stop)
can let in-flight streams drain rather than being abruptly cut, and so a
receiver can distinguish "the far side is politely finishing up" from "the
far side vanished" in logs/metrics.

```json
{
  "reason": "server_shutdown",
  "last_stream_id": 41
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `reason` | string | no | Free-form, for logs. |
| `last_stream_id` | integer | yes | The highest `stream_id` the sender will still process/complete. Any stream with a higher ID that the receiver may have in flight (race condition at shutdown) will not receive further frames and should be treated as failed if not already complete. |

After sending `GOAWAY`, the sender SHOULD wait up to
`goaway_drain_seconds` (default: **10**) for in-flight streams at or
below `last_stream_id` to complete before closing the WebSocket outright.

---

## 7. Stream lifecycle and multiplexing

### 7.1 What a "stream" represents

One stream = one HTTP request/response exchange between the Server (acting
as an HTTP client toward llama-server, on behalf of some external caller)
and the Client (acting as an HTTP client toward the local llama-server, on
behalf of the Server). A stream is 1:1 with one call to, e.g.,
`POST /v1/chat/completions` — including the case where that call is a
streamed (SSE) response with many chunks. A **separate**, concurrent call
to `POST /v1/chat/completions/control` while the first is still streaming
is a **separate stream** with its own `stream_id`, opened independently;
APLC-1's multiplexing (multiple simultaneous streams on one WebSocket
connection) is exactly what makes this possible without needing a second
WebSocket connection.

### 7.2 Stream ID allocation

- Only the Server allocates stream IDs (v1 has no Client-initiated
  streams, §1.1).
- The Server MUST use monotonically increasing `uint32` values starting at
  `1` for the first stream opened after `HELLO_ACK`, incrementing by 1 for
  each subsequent stream on the same connection. `0` is reserved (§5) and
  MUST NOT be used as a stream ID.
- Stream IDs are **not reused** within a connection, even after a stream
  completes. If a connection is long-lived enough to exhaust `uint32`
  (4,294,967,295 streams — at any plausible request rate this is a
  non-issue, but the rule exists for definiteness), the Server MUST close
  the connection with `GOAWAY` and reconnect on a fresh connection rather
  than wrapping IDs.
- The Client MUST reject (via `STREAM_ERROR` with code `invalid_request`,
  or simply logging and ignoring — implementer's choice, see §12) any
  `REQUEST_HEAD` whose `stream_id` is `0` or is less than or equal to a
  `stream_id` it has already seen on this connection.

### 7.3 Stream states

Each stream, from either side's perspective, moves through these states:

```
                    REQUEST_HEAD received
   (idle) ─────────────────────────────────► [request-open]
                                                    │
                          REQUEST_BODY* (0 or more) │
                                                    ▼
                                             [request-open]
                                                    │
                                            REQUEST_END
                                                    ▼
                                          [awaiting-response]
                                                    │
                              RESPONSE_HEAD sent    │
                                                    ▼
                                           [response-open]
                                                    │
                         RESPONSE_BODY* (0 or more) │
                                                    ▼
                                           [response-open]
                                                    │
                                          RESPONSE_END
                                                    ▼
                                              [closed]

  STREAM_ERROR or STREAM_CANCEL, from any state ──► [closed]
```

A stream is **closed** (and its `stream_id` MUST NOT be referenced by
either side again) once any of the following occurs:
- `RESPONSE_END` is sent/received (successful completion), or
- `STREAM_ERROR` is sent/received on that `stream_id` (failure), or
- `STREAM_CANCEL` is sent by the Server and acknowledged by the Client
  sending its own `STREAM_ERROR` with code `internal_error` (or any
  applicable code) for that stream — the Client MUST always respond to a
  `STREAM_CANCEL` with a `STREAM_ERROR` (even if the underlying local HTTP
  call already finished successfully in a race) so the Server has a
  definite closing frame to reconcile bookkeeping against, or
- the WebSocket connection itself closes (all open streams are implicitly
  closed; see §7.4).

### 7.4 Connection loss and stream cleanup

If the WebSocket connection closes for any reason while streams are open
(not via a preceding `GOAWAY`-then-drain sequence), both sides MUST treat
every open stream on that connection as failed:

- The **Server** MUST synthesize a failure response (§11, "Connection
  lost" case) to whatever external caller initiated each affected stream.
- The **Client** MUST abort any in-flight local HTTP request(s) to
  llama-server corresponding to affected streams (best-effort; if the
  underlying `httpx`/equivalent request cannot be cancelled cleanly, it is
  acceptable to let it run to completion locally and simply discard the
  result, provided this does not leak unbounded concurrent local requests
  — see §12.3).

Neither side needs to persist any stream state across a reconnect. A new
WebSocket connection starts stream numbering over at `1` (§7.2) and has no
memory of streams from a previous connection (§1.1's non-goal: no stream
resumption).

### 7.5 Concurrency limits

`max_concurrent_streams` (advertised in `HELLO_ACK`, §6.2) is a
**Server-enforced** limit on how many streams the Server will have open
toward one Client at once — the Server simply does not send a new
`REQUEST_HEAD` past this limit until an existing stream closes. The Client
MAY additionally enforce its own local cap (e.g. to bound concurrent
outbound requests to `llama-server`, which itself has a finite
`n_parallel` slot count) and signal `stream_limit_exceeded` (§6.5.1) for
any `REQUEST_HEAD` beyond that cap. A recommended default Client-side cap
is `n_parallel` (the llama-server context-slot count already configured by
this project's installer, see the parent repo's `installer/install.py`
`--ctx-size` handling) or a fixed small number (e.g. 4) if that value is
not readily available to the transport module.

---

## 8. Client-side request handling (normative behavior)

Upon receiving a `REQUEST_HEAD` frame for a new `stream_id`:

1. Validate the stream ID per §7.2; if invalid, log and ignore the frame
   (do not attempt to open a local request).
2. Validate the request against local policy (§12.1); on rejection, send
   `STREAM_ERROR` with code `invalid_request` and stop.
3. If `has_body` is `true`, buffer or stream `REQUEST_BODY` frames as they
   arrive (implementers MAY choose to stream the body directly into the
   local HTTP client call rather than buffering in memory, which is
   RECOMMENDED for large multimodal payloads) until `REQUEST_END` is
   received.
4. Issue the corresponding local HTTP request to
   `http://127.0.0.1:<llama_port><path>[?<query>]` with the given method,
   headers (plus any headers the local HTTP client needs to add, e.g.
   `Host`), and body.
5. On receiving local HTTP response headers, send `RESPONSE_HEAD`
   immediately (do not wait for the body) — this is what allows SSE
   streaming to work end-to-end: the Server can begin relaying bytes to
   its external caller as soon as headers arrive, without waiting for
   llama-server to finish generating.
6. As local response body bytes arrive, send them as one or more
   `RESPONSE_BODY` frames. Implementers SHOULD chunk in a way that
   preserves SSE event boundaries where practical (e.g. flush on each
   `\n\n`-terminated SSE event) so the Server does not need SSE-aware
   buffering logic of its own, but this is a RECOMMENDATION, not a MUST —
   the Server MUST be able to handle arbitrary chunk boundaries regardless,
   since TCP/WebSocket framing gives no such guarantee inherently.
7. On local response completion, send `RESPONSE_END`.
8. On local connection failure (§6.5.1 `upstream_unreachable`) or timeout
   (§8.2, `upstream_timeout`), send `STREAM_ERROR` instead of
   `RESPONSE_HEAD`/`RESPONSE_END`. If the failure occurs *after*
   `RESPONSE_HEAD` was already sent (e.g. llama-server drops mid-stream),
   send `STREAM_ERROR` in place of the `RESPONSE_END` that would otherwise
   have followed.

### 8.1 Hop-by-hop header handling

Before making the local HTTP request, the Client MUST NOT forward these
header names from `REQUEST_HEAD.headers` to the local call (they are
meaningless or actively wrong once re-issued as a fresh local connection):
`connection`, `upgrade`, `keep-alive`, `transfer-encoding`,
`proxy-connection`. The Client MUST let its local HTTP client library set
its own `Host`, `Content-Length`/`Transfer-Encoding`, and connection
headers appropriately for the local call.

### 8.2 Local request timeout

The Client MUST enforce a configurable local-request timeout
(`local_request_timeout_seconds`, recommended default: **300** — long
enough for slow generations, short enough to eventually free a stream slot
if llama-server hangs) measured from issuing the local request to
receiving either response headers or the first byte of a streamed body,
whichever the local HTTP client considers "response started." A stall
*after* streaming has begun (e.g. token generation pauses) is NOT subject
to this timeout by default — only the initial time-to-first-byte — because
long-running completions are an expected, not exceptional, case. An
implementer MAY additionally add an idle-timeout-since-last-byte for
already-started streams as a defensive measure; this spec recommends a
generous default (e.g. 120s of no bytes at all) if implemented, to avoid
false positives on slow token generation.

### 8.3 Synthesizing `RESPONSE_HEAD` for local errors

If the Client cannot reach llama-server at all, it sends `STREAM_ERROR`
(§6.5), not a synthesized HTTP 502 `RESPONSE_HEAD`. The distinction
matters for the Server's own external-facing error mapping (§11): a
`STREAM_ERROR` unambiguously means "the transport/local-proxy layer
failed," never "llama-server itself returned an error status," which the
Server needs to distinguish for accurate upstream error reporting.

---

## 9. Server-side connection handling (normative behavior)

### 9.1 Accepting a connection

1. Validate the upgrade request's `Authorization` bearer token and
   `X-APLC-Protocol-Version` header (§3.1) before completing the WebSocket
   handshake. Reject with a plain HTTP error status on failure.
2. After the WebSocket handshake completes, wait for `HELLO`
   (§6.1) with a timeout (recommended: **10 seconds**); close the
   connection if it does not arrive in time or fails validation (device_id
   mismatch with the upgrade header, unsupported `protocol_version`).
3. Send `HELLO_ACK` (§6.2).
4. Register this connection in the Server's device-connection registry,
   keyed by `device_id`, so that external callers routing to this device
   can find the live connection. If a connection already exists for this
   `device_id` (e.g. the Client reconnected before the Server noticed the
   old socket was dead), the Server SHOULD close the *older* connection
   (sending `GOAWAY` first if time permits) rather than the new one, since
   the new connection is more likely to be live.

### 9.2 Client reconnection behavior (informative, Client-side requirement)

While this is Client-side behavior, it is documented here because Server
implementers need to know what to expect:

- On any connection failure (upgrade rejected, `HELLO_ACK` timeout,
  unexpected close, ping timeout), the Client MUST reconnect using
  **exponential backoff with jitter**: base delay 1s, doubling up to a
  cap of 60s, with ±20% random jitter applied to each computed delay to
  avoid thundering-herd reconnection storms across many devices after a
  shared Server restart. This mirrors the backoff pattern already
  implemented for llama-server crash recovery in this project's
  `service/llama_supervisor.py` (`BACKOFF_SCHEDULE`), for consistency.
- A successful connection that stays open for at least
  `backoff_reset_seconds` (recommended: **120**, matching
  `STABLE_UPTIME_RESET_SECONDS` in the existing llama-server supervisor for
  consistency) resets the backoff counter to the base delay on the next
  failure.
- An upgrade rejection due to authentication failure (401/403) SHOULD use
  a longer, mostly-fixed retry interval (recommended: **300s**, not the
  short exponential schedule) since retrying a bad token rapidly cannot
  succeed and only generates noise; this is a deliberate deviation from
  the transient-failure backoff schedule above.

### 9.3 Routing external HTTP calls onto a stream

The Server-side "opens a stream" operation is triggered by whatever
external interface the AI Potluck cloud product exposes (out of scope for
this spec — e.g. its own public HTTP API routing a client's chat request
to a specific device). Conceptually:

1. Look up the target device's live WebSocket connection in the registry
   (§9.1 step 4). If none exists, fail the external caller immediately
   with a "device offline" error (§11) — do not queue or wait.
2. Allocate the next `stream_id` for that connection (§7.2).
3. Send `REQUEST_HEAD` (and `REQUEST_BODY`*/`REQUEST_END` if there is a
   body) reflecting the external caller's HTTP request.
4. As `RESPONSE_HEAD`/`RESPONSE_BODY`*/`RESPONSE_END` (or `STREAM_ERROR`)
   arrive for that `stream_id`, relay them to the external caller as its
   own HTTP response (mapping per §11).
5. If the external caller disconnects/cancels before the stream
   completes, the Server SHOULD send `STREAM_CANCEL` (§6.5) for that
   stream to let the Client stop work early, though this is a courtesy
   optimization, not correctness-critical (the Client will otherwise just
   complete the local request and have its `RESPONSE_*` frames ignored).

---

## 10. Reliability model and explicit limitations

### 10.1 What is reliable

- **Connection liveness** is continuously verified via keepalive (§3.3);
  a dead connection is detected within `ping_timeout_seconds` +
  `ping_interval_seconds` (≤30s with defaults) by the Server, and within
  `client_read_timeout_seconds` (60s default) by the Client.
- **Reconnection** is automatic and does not require any external
  intervention (§9.2).
- **In-flight stream failure on disconnect is explicit**, not silent: both
  sides have a defined obligation (§7.4) to surface a clear failure rather
  than leaving a caller hanging indefinitely.

### 10.2 What is explicitly NOT reliable (v1 limitation, by design)

- **No stream resumption across a reconnect.** If the connection drops
  mid-completion, that specific HTTP exchange fails outright; the external
  caller must retry the whole request (a fresh chat completion) rather
  than the protocol attempting to resume token streaming from where it
  left off. This is a deliberate simplicity tradeoff: implementing
  resumable streams would require the Client to buffer/replay partial
  responses and the Server to track resumption state per stream — real
  complexity for a failure mode (mid-generation network drop) that should
  be rare given the keepalive/backoff design, and which most callers will
  handle by retrying anyway.
- **No exactly-once delivery guarantee for a request.** If a connection
  drops after the Server sent `REQUEST_HEAD`/body but before any
  `RESPONSE_*` arrives, the Server cannot distinguish "the Client never
  received it" from "the Client processed it and the response was lost in
  transit" — the request may or may not have actually run against
  llama-server. Callers that need idempotency should use their own
  idempotency keys at the application layer (outside this spec's scope);
  APLC-1 provides at-most-once effective delivery on failure, not
  exactly-once.

### 10.3 Known future extension point: backpressure

v1 has no explicit flow-control frame type. A very large `RESPONSE_BODY`
being generated faster than the Server can relay to a slow external caller
will backpressure naturally at the TCP/WebSocket-buffer level (the
Client's writes will block/slow once the Server's read buffer and the
underlying TCP window fill), which is sufficient for the "few concurrent
streams per device" scale this protocol targets. If a future revision
needs true multiplexed flow control (e.g. many high-throughput streams
sharing one connection where one slow consumer could starve others), that
would be introduced as a new frame type (e.g. `WINDOW_UPDATE`, mirroring
HTTP/2's design) under a negotiated capability (§14) rather than a
breaking change to this spec.

---

## 11. Mapping to the Server's external-facing HTTP API

This section is informative guidance for Server implementers connecting
APLC-1 to whatever public HTTP interface the cloud product exposes.
Recommended status code mapping when relaying a stream's outcome to an
external caller:

| APLC-1 outcome | Recommended external HTTP status | Notes |
|---|---|---|
| `RESPONSE_HEAD` received, status `S` | Relay `S` verbatim | Pass llama-server's own status through unchanged; APLC-1 does not reinterpret it. |
| `STREAM_ERROR` code `upstream_unreachable` | `502 Bad Gateway` | The device's local llama-server is down/unreachable — a "backend" problem from the external caller's point of view. |
| `STREAM_ERROR` code `upstream_timeout` | `504 Gateway Timeout` | |
| `STREAM_ERROR` code `invalid_request` | `400 Bad Request` | Rare in practice if the Server constructs `REQUEST_HEAD` correctly; indicates a Client-side policy rejection (§12.1). |
| `STREAM_ERROR` code `stream_limit_exceeded` | `503 Service Unavailable` | Retryable; suggest a `Retry-After` header. |
| No live connection for target device (§9.3 step 1) | `503 Service Unavailable` or a product-specific "device offline" error body | Not an APLC-1 wire condition — a Server-local routing decision. |
| Connection lost mid-stream (§7.4) | `502 Bad Gateway` (if before `RESPONSE_HEAD`) or best-effort truncate the already-sent partial response (if after) | If headers and partial body were already relayed to the external caller (e.g. SSE already streaming), the Server cannot "change its mind" about the status code — it should terminate the external response stream and let the caller detect the truncation, which is standard SSE/HTTP client behavior for a dropped connection. |

---

## 12. Security considerations

### 12.1 Path allowlisting (Client-side, STRONGLY RECOMMENDED)

Because the Client will issue a local HTTP request to whatever `path` the
Server sends in `REQUEST_HEAD`, a compromised or misconfigured Server (or
a Server whose auth was somehow bypassed) could otherwise direct the
Client to make arbitrary local requests. Implementations SHOULD maintain
an allowlist of path prefixes the Client will proxy — at minimum
`/v1/` (the OpenAI-compatible surface) and `/health` — and reject
(`STREAM_ERROR` code `invalid_request`) anything else, in particular
llama-server's non-`/v1` administrative endpoints (`/slots`,
`/props` POST, `/lora-adapters` POST) unless the deployment specifically
intends to expose those remotely.

### 12.2 Transport security

- `wss://` (TLS) is REQUIRED for any Client connecting over an untrusted
  network (i.e., always, in practice, for this project). Certificate
  validation MUST NOT be disabled in production Client builds.
- The bearer token (§3.1, §4) is a long-lived credential; it MUST be
  stored with filesystem permissions restricting it to the service
  account running the Client (consistent with this project's existing
  `runtime.json` handling), and MUST NOT be logged.

### 12.3 Resource exhaustion

- The Client SHOULD enforce a maximum concurrent local-request count
  (§7.5) to avoid a misbehaving or malicious Server exhausting local
  resources (memory, llama-server's own slot count) by opening many
  streams at once.
- The Client SHOULD enforce a maximum buffered-body size for
  `REQUEST_BODY` frames if it chooses to buffer rather than stream bodies
  directly to the local HTTP call (§8 step 3), to bound memory use for
  unexpectedly large uploads.
- The Server SHOULD enforce `max_concurrent_streams` (§7.5) per
  connection and a reasonable global cap on total open streams across all
  devices, as ordinary service-capacity hygiene.

### 12.4 Denial of a single device vs. the fleet

Because each device has its own independent WebSocket connection and
`device_token`, compromising or disconnecting one device's credential or
connection has no protocol-level effect on any other device's connection
— there is no shared session state between devices at the APLC-1 layer.

---

## 13. Versioning policy

- `version` in the frame header (§5) covers **wire-format** compatibility
  (byte layout). A change here is a hard breaking change requiring a new
  major protocol version and MUST be negotiable via
  `X-APLC-Protocol-Version` at connection time (§3.1) so a Server can
  support multiple versions simultaneously during a rollout if needed.
- `protocol_version` in `HELLO`/`HELLO_ACK` (§6.1, §6.2) is redundant with
  the upgrade header by design (defense in depth against a proxy/CDN that
  strips custom headers) — both MUST agree; a mismatch is a connection
  error.
- New optional frame types or JSON fields that do not change existing
  behavior (i.e., backward-compatible additions) do NOT require a version
  bump; they are gated by the `capabilities` mechanism (§14) instead.

## 14. Capability negotiation (extension mechanism)

`HELLO.capabilities` and `HELLO_ACK.capabilities` (§6.1, §6.2) are
open-ended string arrays for advertising optional, backward-compatible
features without a protocol version bump. A capability is "active" for a
connection only if **both** sides advertised it. No capabilities are
defined in this v1.0 spec; the field exists so, for example, a future
`gzip_body` capability (compressing `REQUEST_BODY`/`RESPONSE_BODY`
payloads) could be added by both sides recognizing the string
`"gzip_body"` and agreeing to compress, with older implementations on
either side simply not advertising it and continuing to exchange
uncompressed bytes exactly as this spec defines.

---

## 15. Configuration parameters summary

All defaults below are RECOMMENDATIONS; implementations SHOULD make them
configurable but MAY ship fixed values for a first version.

| Parameter | Default | Side | Defined in |
|---|---|---|---|
| `ping_interval_seconds` | 20 | Server | §3.3 |
| `ping_timeout_seconds` | 10 | Server | §3.3 |
| `client_read_timeout_seconds` | 60 | Client | §3.3 |
| `hello_ack_timeout_seconds` | 10 | Client | §6.2 |
| `hello_timeout_seconds` | 10 | Server | §9.1 |
| `max_concurrent_streams` | 8 | Server | §6.2, §7.5 |
| `local_request_timeout_seconds` | 300 | Client | §8.2 |
| `goaway_drain_seconds` | 10 | either | §6.6 |
| Reconnect backoff: base / cap / jitter | 1s / 60s / ±20% | Client | §9.2 |
| Reconnect backoff reset threshold | 120s stable uptime | Client | §9.2 |
| Auth-failure retry interval | 300s | Client | §9.2 |

---

## 16. Reference pseudocode

Non-normative, illustrating the frame read/write loop each side needs.
Python for the Client (matches this project's existing stack), TypeScript
for the Server (matches a SvelteKit/Node cloud backend).

### 16.1 Client: frame codec

```python
import struct
import json

FRAME_HEADER = struct.Struct(">BBI")  # version(u8), type(u8), stream_id(u32)

def encode_frame(frame_type: int, stream_id: int, payload: bytes) -> bytes:
    return FRAME_HEADER.pack(1, frame_type, stream_id) + payload

def decode_frame(message: bytes) -> tuple[int, int, int, bytes]:
    version, ftype, stream_id = FRAME_HEADER.unpack(message[:6])
    return version, ftype, stream_id, message[6:]

def encode_json_frame(frame_type: int, stream_id: int, obj: dict) -> bytes:
    return encode_frame(frame_type, stream_id, json.dumps(obj).encode("utf-8"))
```

### 16.2 Client: per-stream handler sketch

```python
async def handle_request_head(ws, stream_id: int, head: dict, llama_base_url: str):
    if not is_path_allowed(head["path"]):  # section 12.1
        await ws.send(encode_json_frame(0x30, stream_id, {
            "code": "invalid_request", "message": "path not allowed"
        }))
        return

    body_chunks = []  # or stream directly; buffering shown for clarity
    # ... caller has already accumulated REQUEST_BODY frames until REQUEST_END ...

    url = llama_base_url + head["path"]
    if head.get("query"):
        url += "?" + head["query"]

    try:
        async with httpx_client.stream(
            head["method"], url,
            headers=strip_hop_by_hop(head["headers"]),
            content=b"".join(body_chunks),
            timeout=LOCAL_REQUEST_TIMEOUT_SECONDS,
        ) as resp:
            await ws.send(encode_json_frame(0x20, stream_id, {
                "status": resp.status_code,
                "headers": dict(resp.headers),
            }))
            async for chunk in resp.aiter_bytes():
                await ws.send(encode_frame(0x21, stream_id, chunk))
            await ws.send(encode_frame(0x22, stream_id, b""))
    except httpx.ConnectError:
        await ws.send(encode_json_frame(0x30, stream_id, {
            "code": "upstream_unreachable", "message": "connect failed"
        }))
    except httpx.TimeoutException:
        await ws.send(encode_json_frame(0x30, stream_id, {
            "code": "upstream_timeout", "message": "no response in time"
        }))
```

### 16.3 Server: frame codec

```typescript
const HEADER_SIZE = 6;

function encodeFrame(type: number, streamId: number, payload: Buffer): Buffer {
  const header = Buffer.alloc(HEADER_SIZE);
  header.writeUInt8(1, 0);        // version
  header.writeUInt8(type, 1);     // frame type
  header.writeUInt32BE(streamId, 2);
  return Buffer.concat([header, payload]);
}

function decodeFrame(message: Buffer): { version: number; type: number; streamId: number; payload: Buffer } {
  return {
    version: message.readUInt8(0),
    type: message.readUInt8(1),
    streamId: message.readUInt32BE(2),
    payload: message.subarray(HEADER_SIZE),
  };
}

function encodeJsonFrame(type: number, streamId: number, obj: unknown): Buffer {
  return encodeFrame(type, streamId, Buffer.from(JSON.stringify(obj), "utf-8"));
}
```

### 16.4 Server: opening a stream for an external HTTP call

```typescript
async function proxyToDevice(deviceConn: DeviceConnection, req: IncomingHttpRequest): Promise<ProxiedResponse> {
  const streamId = deviceConn.nextStreamId++;
  const pending = deviceConn.registerPendingStream(streamId); // resolves/rejects below

  deviceConn.ws.send(encodeJsonFrame(0x10, streamId, {
    method: req.method,
    path: req.path,
    query: req.query ?? "",
    headers: req.headers,
    has_body: req.body !== null,
  }));

  if (req.body !== null) {
    deviceConn.ws.send(encodeFrame(0x11, streamId, req.body));
    deviceConn.ws.send(encodeFrame(0x12, streamId, Buffer.alloc(0)));
  }

  return pending; // caller relays RESPONSE_HEAD/BODY*/END frames as they resolve the promise/stream
}

// Elsewhere, in the WebSocket message handler for this connection:
function onMessage(deviceConn: DeviceConnection, message: Buffer) {
  const { type, streamId, payload } = decodeFrame(message);
  const stream = deviceConn.pendingStreams.get(streamId);
  if (!stream) return; // unknown/closed stream id -- ignore per forward-compat rule

  switch (type) {
    case 0x20: // RESPONSE_HEAD
      stream.onHead(JSON.parse(payload.toString("utf-8")));
      break;
    case 0x21: // RESPONSE_BODY
      stream.onBodyChunk(payload);
      break;
    case 0x22: // RESPONSE_END
      stream.onComplete();
      deviceConn.pendingStreams.delete(streamId);
      break;
    case 0x30: // STREAM_ERROR
      stream.onError(JSON.parse(payload.toString("utf-8")));
      deviceConn.pendingStreams.delete(streamId);
      break;
  }
}
```

---

## 17. Worked example: end-to-end sequence

A non-streaming chat completion, shown as the sequence of frames on the
wire (arrows show direction; `S` = Server, `C` = Client):

```
S → C   HELLO is not sent by the Server; connection already established
C → S   HELLO           {protocol_version: 1, device_id: "...", client_version: "..."}
S → C   HELLO_ACK        {protocol_version: 1, session_id: "sess_abc", max_concurrent_streams: 8, ...}

        [ time passes; external caller hits the cloud's public API ]

S → C   REQUEST_HEAD     stream_id=1  {method: "POST", path: "/v1/chat/completions",
                                        headers: {"content-type": "application/json"}, has_body: true}
S → C   REQUEST_BODY     stream_id=1  <raw JSON bytes: {"model": "...", "messages": [...], "stream": false}>
S → C   REQUEST_END      stream_id=1  <empty>

        [ Client makes local HTTP call to 127.0.0.1:8080/v1/chat/completions ]

C → S   RESPONSE_HEAD    stream_id=1  {status: 200, headers: {"content-type": "application/json"}}
C → S   RESPONSE_BODY    stream_id=1  <raw JSON response bytes>
C → S   RESPONSE_END     stream_id=1  <empty>

        [ Server relays the 200 + JSON body back to the external caller; stream 1 is now closed ]
```

A streaming completion **concurrent with** a control call looks like:

```
S → C   REQUEST_HEAD     stream_id=2  {method: "POST", path: "/v1/chat/completions",
                                        headers: {..., "accept": "text/event-stream"}, has_body: true}
S → C   REQUEST_BODY     stream_id=2  <JSON with "stream": true>
S → C   REQUEST_END      stream_id=2  <empty>

C → S   RESPONSE_HEAD    stream_id=2  {status: 200, headers: {"content-type": "text/event-stream"}}
C → S   RESPONSE_BODY    stream_id=2  <SSE chunk: "data: {...}\n\n">
C → S   RESPONSE_BODY    stream_id=2  <SSE chunk: "data: {...}\n\n">

        [ external caller decides to steer the completion mid-stream ]

S → C   REQUEST_HEAD     stream_id=3  {method: "POST", path: "/v1/chat/completions/control",
                                        headers: {"content-type": "application/json"}, has_body: true}
S → C   REQUEST_BODY     stream_id=3  <JSON: {"id": "chatcmpl-...", "action": "reasoning_end"}>
S → C   REQUEST_END      stream_id=3  <empty>

C → S   RESPONSE_HEAD    stream_id=3  {status: 200, headers: {"content-type": "application/json"}}
C → S   RESPONSE_BODY    stream_id=3  <JSON: {"success": true}>
C → S   RESPONSE_END     stream_id=3  <empty>

        [ stream 3 closes; stream 2 is still open and continues independently ]

C → S   RESPONSE_BODY    stream_id=2  <SSE chunk: "data: {...}\n\n">
C → S   RESPONSE_BODY    stream_id=2  <SSE chunk: "data: [DONE]\n\n">
C → S   RESPONSE_END     stream_id=2  <empty>
```

This demonstrates the core value of multiplexing: stream 3 (the control
call) opens, runs, and closes entirely independently of stream 2 (the
still-open SSE completion), on the single WebSocket connection, with no
protocol-level coordination required between them beyond distinct
`stream_id`s.

---

## 18. Glossary

- **Client**: the `aipotluck-local-client` Python background service
  running on an end-user machine, dialing out to the Server.
- **Server**: the AI Potluck cloud backend's WebSocket endpoint.
- **Stream**: one logical HTTP request/response exchange multiplexed over
  the shared WebSocket connection.
- **Connection**: the single underlying WebSocket TCP/TLS connection
  between one Client and the Server.
- **Frame**: one APLC-1 protocol message, always exactly one WebSocket
  binary message (§3.2).
- **llama-server**: the local llama.cpp HTTP server this project's
  installer already sets up and supervises (see the parent repo's
  `service/llama_supervisor.py`), the actual target of proxied requests.

[RFC 6455]: https://www.rfc-editor.org/rfc/rfc6455
