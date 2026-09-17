# SirTunnel Implementation Plan

**Source verified against:** `git clone https://github.com/anderspitman/SirTunnel` cloned to
`/opt/data/aipotluck-local-client/.scratch/clones/sirtunnel` (master branch, commit as of clone
time). Files cited below are all relative to that repo root unless a full URL is given. The
**entire** server-side "product" is 3 files: `sirtunnel.py` (47 lines), `caddy_config.json` (13
lines), `install.sh`/`run_server.sh` (helper shell scripts), plus `README.md` and
`create_tunnel_example.sh` documenting client usage. There is no other source to read — this is
confirmed exhaustive by directory listing.

---

## Installation & Global VPS Configuration

SirTunnel is **not a service you install** in the traditional sense — it is a thin Python wrapper
script that issues HTTP calls to **Caddy's admin API** (`http://127.0.0.1:2019`). "Installing
SirTunnel" really means "installing and running Caddy with a specific config, plus placing
`sirtunnel.py` on the `$PATH` of the SSH login shell." Concretely, per `README.md` lines 67-78 and
`install.sh`:

1. **Download Caddy binary** (`install.sh` lines 1-13):
   ```bash
   caddyVersion=2.1.1
   curl -s -O -L https://github.com/caddyserver/caddy/releases/download/v${caddyVersion}/caddy_${caddyVersion}_linux_amd64.tar.gz
   tar xf caddy_${caddyVersion}_linux_amd64.tar.gz
   sudo setcap 'cap_net_bind_service=+ep' caddy   # allow binding :443 without root
   ```
   (Note: the pinned version 2.1.1 is old — for production use a current Caddy release from
   https://github.com/caddyserver/caddy/releases; the install mechanics are identical.)
2. **Config file**: `caddy_config.json` (repo root, 13 lines) — a static Caddy JSON config that
   declares one empty HTTP server named `sirtunnel` listening on `:443` with zero routes:
   ```json
   {
     "apps": { "http": { "servers": { "sirtunnel": {
       "listen": [":443"],
       "routes": []
     }}}}
   }
   ```
   This is the **only** config file in the whole project. All per-tunnel routes are added later at
   runtime via Caddy's admin API (see next section) — "Essentially stateless... the only state is
   the certs... and the tunnel mappings, which are ephemeral" (`README.md` lines 58-60).
3. **Run the server**: `run_server.sh` (3 lines) — `./caddy run --config caddy_config.json`. Caddy's
   admin API automatically binds to `127.0.0.1:2019` (Caddy's own default; SirTunnel does not set
   this explicitly — it's a hardcoded assumption in `sirtunnel.py` line 32:
   `create_url = 'http://127.0.0.1:2019/config/apps/http/servers/sirtunnel/routes'`).
4. **Ports required on the VPS**:
   - `:443` — Caddy's public HTTPS listener (bound per `caddy_config.json`; `:80` is not opened by
     this config at all, so there is no automatic HTTP→HTTPS redirect or ACME HTTP-01 challenge
     listener configured out of the box — Caddy would need `:80` added to the config for HTTP-01;
     as shipped it presumably relies on Caddy's default ACME behavior needing outbound access, or a
     wildcard/DNS-01 setup would need extra JSON not present in this minimal example).
   - `:22` (or whatever port) — the **existing SSH server** on the VPS; SirTunnel adds no SSH server
     of its own. "Assuming you already have an ssh server running" (`README.md` line 69).
   - `127.0.0.1:2019` — Caddy's admin API, loopback-only by Caddy's own default (not modified by
     this config), so it is **not** exposed to the public internet unless the operator explicitly
     changes Caddy's `admin` stanza.
5. **`sirtunnel.py` must be on `$PATH`** for the SSH user that tunnel-initiating clients log in as
   (`README.md` line 13: "A copy of the sirtunnel.py script available on the PATH of the server."),
   and it must be executable (`chmod +x sirtunnel.py`, `#!/usr/bin/env python3` shebang, line 1).
6. **Wrapping as a systemd service** (not shipped by upstream — README only documents `run_server.sh`
   as a foreground command). A minimal systemd unit consistent with the shipped scripts:
   ```ini
   [Service]
   WorkingDirectory=/opt/sirtunnel
   ExecStart=/opt/sirtunnel/caddy run --config /opt/sirtunnel/caddy_config.json
   Restart=always
   AmbientCapabilities=CAP_NET_BIND_SERVICE
   ```
   (`AmbientCapabilities` is the systemd-native equivalent of the `setcap` step in `install.sh`.)
   Docker is not mentioned anywhere in the repo — there is no official Dockerfile; a container would
   just need the Caddy image plus mounting `caddy_config.json` and `sirtunnel.py`.
7. **One-time global config**: there is **no admin account, no DB, no TLS cert config beyond
   Caddy's own automatic-HTTPS defaults** (Caddy auto-provisions Let's Encrypt certs per-domain the
   first time a route with that `host` match is added — this is stock Caddy behavior, not something
   SirTunnel configures). Authentication for who can create tunnels is delegated **entirely to SSH**
   (standard `authorized_keys` / SSH server auth) — SirTunnel itself has zero auth of its own and zero
   CLI flags ("0-configuration. There is no configuration on the server side. Not even CLI
   arguments." — `README.md` lines 56-57).

**Summary**: install = download Caddy binary + copy 2 files (`caddy_config.json`,
`sirtunnel.py`) + ensure an SSH server + wrap `caddy run` in systemd. No database, no admin
account creation, no separate TLS setup step.

---

## Cloud-Side Control-Plane API (SvelteKit ↔ SirTunnel server)

**SirTunnel itself exposes no HTTP API to SvelteKit at all.** The only real API here is **Caddy's
own admin API** (`http://127.0.0.1:2019`), which `sirtunnel.py` calls locally on the VPS, not
something a remote SvelteKit backend calls directly (it's loopback-bound by default, and Caddy
requires the caller to already be on the VPS or tunneled in).

Tracing exactly how `sirtunnel.py` drives it (`sirtunnel.py` lines 9-36):
```python
host = sys.argv[1]          # e.g. "sub1.example.com"
port = sys.argv[2]          # e.g. "9001" (the SSH remote-forward port on the VPS)
tunnel_id = host + '-' + port

caddy_add_route_request = {
    "@id": tunnel_id,
    "match": [{"host": [host]}],
    "handle": [{
        "handler": "reverse_proxy",
        "upstreams": [{"dial": ':' + port}]
    }]
}
body = json.dumps(caddy_add_route_request).encode('utf-8')
create_url = 'http://127.0.0.1:2019/config/apps/http/servers/sirtunnel/routes'
req = request.Request(method='POST', url=create_url, headers={'Content-Type': 'application/json'})
request.urlopen(req, body)
```
This is a **POST to Caddy's `/config/.../routes` endpoint**, appending one route object (tagged
with a Caddy `@id` for later deletion) that reverse-proxies `Host: <host>` traffic to
`127.0.0.1:<port>`. On `Ctrl-C` (SIGINT, caught because the SSH session used `-t`),
`sirtunnel.py` issues `DELETE http://127.0.0.1:2019/id/<tunnel_id>` (lines 43-46) to remove the
route again — this is the **entire lifecycle API**, and it only runs locally, invoked as the
*command* of an SSH session, not as a standalone daemon SvelteKit can call remotely.

**Consequence for our architecture**: SvelteKit **cannot** "register a tunnel" or "issue a
per-client token" against SirTunnel via any bespoke REST surface — there isn't one. The only two
integration points are:
1. **SSH authentication itself** (who is allowed to open `ssh -tR ... sirtunnel.py ...` on the
   VPS) — entirely delegated to whatever SSH auth the VPS already has (public keys in
   `~/.ssh/authorized_keys`, PAM, etc.). SirTunnel does not participate in this at all.
2. **Caddy's admin API**, if SvelteKit were given network access to it (e.g. if the operator
   deliberately re-binds Caddy's `admin` listener off loopback, or if SvelteKit runs colocated on
   the same VPS/network), SvelteKit *could* call the same
   `POST /config/apps/http/servers/sirtunnel/routes` / `DELETE /id/<tunnelId>` endpoints directly to
   pre-create or manage routes — but this bypasses SirTunnel's own script entirely and duplicates
   its ~15 lines of logic; SirTunnel provides no dedicated multi-tenant/token-scoped wrapper around
   Caddy's admin API (Caddy's admin API itself has no built-in auth beyond network exposure control —
   see Caddy docs, https://caddyserver.com/docs/api).

**Bottom line: there is no "SirTunnel API" for SvelteKit to talk to.** If we choose SirTunnel, the
cloud app's only lever is (a) managing SSH key authorization on the VPS (via `authorized_keys` file
writes, exactly as in the sish plan's "directory watcher" approach) and (b) optionally talking
directly to Caddy's admin API ourselves for route bookkeeping/pre-registration, which is not
something SirTunnel ships or documents as a stable interface.

---

## Client Linking Flow (access token exchange)

SirTunnel has **zero concept of tokens, pairing, or enrollment**. Quoting the entire documented
usage flow (`README.md` lines 17-24):
```bash
ssh -tR 9001:localhost:8080 example.com sirtunnel.py sub1.example.com 9001
```
This single SSH invocation **is** the entire "client linking" mechanism:
- `-t` allocates a pty so that `Ctrl-C` on the laptop propagates to kill the remote
  `sirtunnel.py` process, which is what triggers its cleanup/`DELETE` call (README "Note", lines
  38-41).
- `-R 9001:localhost:8080` opens a standard SSH remote port forward: VPS port `9001` → laptop's
  `localhost:8080`.
- `example.com` is the plain SSH target — auth is whatever SSH auth already exists (public key,
  password, etc.) for that account on the VPS.
- `sirtunnel.py sub1.example.com 9001` is the **remote command** executed over that same SSH
  session, which does the Caddy API call described above.

**There is no access-token exchange, no config file with a pre-shared secret, no pairing flow of
any kind** — the security boundary is 100% "can this SSH keypair log into this VPS account."

**For our "linking a new machine's client to SvelteKit" requirement, we would have to build 100%
of it ourselves**, essentially identical to the sish plan's glue code:
1. Local-client installer generates an SSH keypair (`ssh-keygen -t ed25519 -f ~/.aipotluck/id_ed25519 -N ""`).
2. SvelteKit issues a one-time pairing/access token (our own invention).
3. Local client calls our own SvelteKit REST endpoint (e.g. `POST /api/link-device
   {token, public_key}`), which validates the token and appends the given public key to that
   device's line in `~/.ssh/authorized_keys` on the SirTunnel VPS (via SSH/SCP from SvelteKit's
   backend, or a shared filesystem/volume if colocated) — SirTunnel provides zero help with this
   step; it is pure standard Unix/SSH administration.
4. From then on, the local-client daemon runs the `ssh -tR <port>:localhost:<llama-port> <vps>
   sirtunnel.py <assigned-subdomain> <port>` command itself (or a Go/Python SSH library
   equivalent) to establish the tunnel and register the Caddy route in one shot.
5. Revocation = remove the line from `authorized_keys` (next SSH attempt fails) — there is no live
   "kick connected client" API analogous to sish's `/disconnectclient` endpoint; the operator (or
   SvelteKit's backend over SSH) would need to also `pkill` or otherwise terminate the existing SSH
   session/process for instant revocation.

---

## Request Routing (public traffic → local client)

Routing is **purely Caddy host-header virtual hosting**, driven by whatever `host`/`port` pair the
client passed as arguments to `sirtunnel.py` at connection time — see the exact Caddy route JSON
in section 2 above (`"match": [{"host": [host]}]`, `"handle": [{"handler": "reverse_proxy",
"upstreams": [{"dial": ":" + port}]}]`).

- The **subdomain is chosen by the connecting client**, passed as the first CLI argument to
  `sirtunnel.py` (e.g. `sub1.example.com`) — there is no server-side allocation, collision
  protection, or validation of any kind in the 47-line script. Two simultaneous clients requesting
  the same hostname would silently overwrite each other's Caddy route (last POST wins; Caddy allows
  duplicate `@id`s to be replaced) — SirTunnel does nothing to prevent this (the referenced fork
  `https://github.com/matiboy/SirTunnel`, per `README.md` line 87-88, exists specifically to "help
  multiple users avoid overwriting each others' tunnels", confirming this is a known unaddressed gap
  in upstream).
- The **local port** (`9001` in the example) is the **server-side** port that the `ssh -R` remote
  forward binds to (on `127.0.0.1` per default SSH behavior) — Caddy's reverse-proxy `dial` target
  is literally `:<that port>` (`sirtunnel.py` line 23: `"dial": ':' + port`), i.e. it dials
  `127.0.0.1:<port>` on the VPS itself, which SSH is tunneling back to the local machine's
  `localhost:8080`. **This port must be chosen manually by the client** and is never allocated or
  tracked server-side — a fixed-port-per-device convention (as in our sish plan) is equally
  necessary here to avoid collisions across multiple devices sharing one VPS.
- **For our use case**: at pairing time, SvelteKit-side logic would need to assign each device a
  fixed `(subdomain, ssh-remote-port)` pair (e.g. `device-<id>.tunnels.ourapp.com`, port derived
  deterministically from device ID) and configure the local-client daemon to always invoke
  `ssh -tR <fixedPort>:localhost:<llama-port> <vps-host> sirtunnel.py device-<id>.tunnels.ourapp.com
  <fixedPort>` — SirTunnel does zero validation/enforcement of this convention; it is entirely our
  responsibility to prevent two devices being assigned the same port or subdomain.
- No SvelteKit-callable "bind hostname to client ID" API exists (see section 2) — the binding
  happens as a side effect of the SSH command line the client itself runs.

---

## Status Polling / Health Checks

**SirTunnel provides no status/health API whatsoever** — no admin console, no `/api/clients`
equivalent, nothing. The entire source (`sirtunnel.py`) has no listener of its own; it runs once,
makes one POST, then just sleeps in a `while True: time.sleep(1)` loop (lines 38-40) waiting for
`Ctrl-C`/SIGINT to trigger cleanup. There is no logging beyond `print("Tunnel created
successfully")` (line 36) to the SSH session's stdout, and no persistent state describing "is
`sub1.example.com` currently backed by a live SSH tunnel."

Available signals we would have to build ourselves:
1. **Caddy's admin API introspection** (again `http://127.0.0.1:2019`, loopback by default):
   `GET /config/apps/http/servers/sirtunnel/routes` would list currently-registered routes (their
   `@id`s), telling us *"a route with this hostname currently exists in Caddy's config"* — but this
   only proves the route was added and never explicitly removed; it does **not** prove the
   underlying `ssh -R` tunnel/process is still alive (if the SSH connection silently drops without
   a clean `Ctrl-C`, Caddy's route would remain, now pointing at a dead upstream, until Caddy's own
   reverse-proxy dial fails and returns 502s to real traffic).
2. **Direct HTTP health probe** of the assigned subdomain (`https://device-<id>.tunnels.ourapp.com/health`)
   is the only end-to-end signal SirTunnel's architecture naturally supports — a 502/504 means the
   Caddy route exists but the local llama.cpp server / SSH tunnel is unreachable; a 200 means the
   whole path (Caddy → SSH tunnel → local service) is healthy. This is functionally identical to
   the "recommended combination" fallback described for sish, except here it's not a
   fallback — **it's the only viable signal at all**, since there's no admin API tracking live SSH
   sessions.
3. We would need to build our own liveness bookkeeping in SvelteKit's DB, e.g. having the
   local-client daemon itself periodically call back into a SvelteKit "heartbeat" endpoint over its
   own tunnel or a separate plain HTTPS call — entirely outside anything SirTunnel or Caddy provide.

---

## Open Questions / Assumptions

- **No auth, no rate limiting, no multi-tenancy** in the shipped script: the well-known race
  condition of two clients claiming the same hostname/port (acknowledged by upstream's own README
  pointing to the `matiboy/SirTunnel` fork for this exact problem) means production use requires
  either forking `sirtunnel.py` ourselves to add locking/validation, or building that logic
  entirely into our SvelteKit-side allocation of fixed per-device subdomains/ports (recommended,
  and consistent with how we'd need to handle sish/wstunnel too).
- **Caddy admin API exposure**: confirmed loopback-only by Caddy's own default (not overridden by
  `caddy_config.json`, which has no `admin` stanza at all) — if we want SvelteKit to call Caddy's
  API directly (bypassing `sirtunnel.py`), we'd need to either colocate SvelteKit on the same host/
  network namespace as Caddy, or deliberately expose the admin API off loopback (a real security
  decision Caddy's own docs warn about: https://caddyserver.com/docs/api — "the admin endpoint...
  should not be exposed to the public internet").
- **No HTTP:80/ACME challenge listener** is present in the shipped `caddy_config.json` (only `:443`
  is declared) — not verified whether Caddy's automatic HTTPS still works out-of-the-box here via
  TLS-ALPN-01 (which doesn't need port 80) or whether the operator must add a `:80` server block;
  this needs to be tested against a current Caddy release before relying on it.
- **No revocation/kill-switch API**: unlike sish's `/disconnectclient`, there is no way to forcibly
  terminate an already-established SirTunnel SSH session from the cloud side short of SSHing in and
  killing the process or restarting Caddy — a real gap for our "revoke access instantly" requirement.
- **Maintenance status**: SirTunnel's own README frames it as intentionally frozen/minimal ("I'm
  unlikely to add many features moving forward" — line 82-83) and points to two community forks
  (`matiboy/SirTunnel`, `daps94/SirTunnel`) for missing multi-user and stale-tunnel-cleanup features
  — worth evaluating those forks directly if SirTunnel's approach (Caddy admin API + plain SSH) is
  otherwise attractive, rather than building all the missing pieces (auth, cleanup, status) from
  scratch against unforked upstream.
- **Verified confidently**: sections 1 (install) and 4 (routing mechanism) are fully traced from
  the complete, tiny source and are not guesses. Sections 2, 3, and 5 are confidently answered as
  "SirTunnel provides essentially nothing here" — this is a real finding, not a research gap, since
  the entire source was read in full (47 lines) and no hidden API surface exists.

---

## Summary of custom glue code required

SirTunnel supplies exactly three things: (1) a documented pattern for using Caddy's admin API to
add/remove a reverse-proxy route, (2) a single Python script that does that from the far end of an
`ssh -R` command, (3) nothing else. There is no server process, no daemon beyond Caddy itself, no
auth model, no token system, no status API, and no multi-tenant safety net. Essentially **all** of
sections 2 (control-plane API), 3 (pairing/tokens), part of 4 (collision-safe subdomain/port
allocation), and 5 (status/health) must be built by us — either as a fork of `sirtunnel.py` with
locking/auth added, as direct SvelteKit-to-Caddy-admin-API integration, or as an entirely separate
SSH-key-provisioning + health-probing system wrapped around vanilla Caddy + SSH.
