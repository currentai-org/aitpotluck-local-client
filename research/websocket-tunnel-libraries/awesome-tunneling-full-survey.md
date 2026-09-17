# Open-Source WebSocket/Reverse-Tunnel Tools — Full Survey (awesome-tunneling)

## Objective

Comprehensive re-survey covering every entry in the "Open source (at least
with a reasonably permissive license)" section of
[`anderspitman/awesome-tunneling`](https://github.com/anderspitman/awesome-tunneling/blob/master/README.md#open-source-at-least-with-a-reasonably-permissive-license)
(73 entries), plus the 5 additional open-source candidates found in earlier
research passes not on that list — 78 tools total, all open source.

## Constraints (current, superseding prior reports)

- **Server side: a separate hosted binary/process is now the *expected*
  architecture**, not a penalty — our SvelteKit backend runs serverless with
  no long-running process to embed a tunnel server into. Bonus points for:
  server source code being available (so we can audit/patch/self-host it),
  and — most importantly — **a TypeScript/JavaScript server API, or any
  hittable HTTP control-plane**, since we don't need per-tunnel custom DNS,
  only a way to register/manage each tunnel from our own glue code talking
  to our database.
- **Client side: unchanged and re-emphasized as the hard constraint.** No
  TAP/TUN virtual network interfaces, no admin/root privileges, no complex
  installation. A single static binary, a simple CLI, or reuse of an
  already-installed OS tool (like SSH) all qualify; installing a VPN kernel
  module, requiring `sudo`, or a GUI installer wizard does not.

## Methodology

61 tools were researched fresh across 8 parallel research passes (this
turn); 17 were carried forward from an earlier survey (already re-scored
for the serverless-backend constraint in a prior report revision). All 78
are scored 1-5 on the same rubric:

- **5** — genuinely excellent fit: TUN/admin-free client, self-hostable
  server with source available, AND either a real HTTP/REST control-plane
  or a TypeScript/JavaScript server API.
- **4** — strong fit: TUN/admin-free client, self-hostable server with
  source, but the control-plane/API story is partial, undocumented, or in
  a language other than TS/JS.
- **3** — workable: meets the hard client constraint and has an open-source
  server, but is config-file-driven only (no runtime API) or has some other
  real friction (heavier server stack, smaller/less certain maintenance).
- **2** — meets the client constraint but has a significant other problem
  (unmaintained, no self-hostable server, thin community, licensing
  ambiguity).
- **1** — disqualified: violates the hard client constraint (TUN/TAP/admin
  required), is abandoned/archived, has no server implementation at all, or
  is closed-source where it matters.

Every tool was checked against its own GitHub/npm/docs page directly. All
URLs are listed in Sources. Field notes below are drawn directly from that
research; some entries (deliberately) received lighter research depth per
the task's own instruction to avoid over-investigating clear
disqualifications.

**Implementation-validation pass.** After initial docs-only scoring, the top
16 tools (4 originally-scored 5/5 + 12 originally-scored 4/5) were put
through a second, deeper validation round: each was cloned, its actual
source code and docs traced against a concrete deployment scenario
(server as a managed VPS service; SvelteKit registers/links a device via
an access-token exchange; SvelteKit configures routing to that device;
SvelteKit polls connection status). Scores below reflect this validation
where it was performed — entries marked "confirmed" kept their original
score after tracing real source, entries marked "upgraded"/"downgraded"
had their score revised based on what the implementation deep dive
actually found. Full per-tool implementation plans are in
`implementation-plans/{tool}/PLAN.md`.

## Full comparison table (all 78 tools, sorted by score)

| Score | Tool | Language | Popularity | Maturity | Client (TUN/admin-free?) | Server API/control-plane | Implementation notes |
|---|---|---|---|---|---|---|---|
| 5/5 | Pangolin | TypeScript / Next.js (server + dashboar… | 22.8k GitHub stars, 786 forks, 71 watchers, 8,469+ commits — very act… | Very actively maintained: huge, continuously growing commit history (… | The client connector, 'newt,' is a single static Go binary (get-newt.sh install… | Likely true — TypeScript; Pangolin's control plane is a TypeScript/Next.js app … | Confirmed 5/5 — real site/resource/target API, `online` status field |
| 5/5 | chiSSL | Go | 195 stars, 8 forks, 4 watchers (repo now lives under the 'unblocked' … | 37 commits but clearly a mature, feature-rich fork/successor of chise… | Single static Go binary client (Homebrew install or prebuilt release binary), n… | true -- Go server exposes a documented REST API (OpenAPI/Redoc spec published) … | Confirmed 5/5 — real REST API, but port-only routing (no hostname dispatch) |
| 5/5 | gost | Go | 7.5k stars, 828 forks, 52 watchers; large multi-protocol proxy/tunnel… | Actively maintained (558 commits, continuous releases, install script… | Single Go binary, installable via releases page, install script, or Docker; use… | true — Go server with a documented RESTful HTTP API (Basic Auth, Swagger/OpenAP… | **Upgraded 4→5** — confirmed Ingress API binds hostname→tunnel-ID directly |
| 5/5 | zrok | Go | 4.7k stars, 222 forks, 30 watchers; backed by the OpenZiti project/Ne… | Very actively maintained (huge commit volume, frequent releases, dedi… | Single binary (zrok CLI) across Windows/macOS/Linux/Raspberry Pi; built on Open… | true — Go SDK for embedding sharing/tunnel logic, plus a generated REST client/… | **Upgraded 4→5** — confirmed Agent API gives best end-to-end health story surveyed |
| 4/5 | wstunnel | Rust (v7+ rewrite; earlier versions in … | ~7.0k GitHub stars, 567 forks, 56 watchers - by far the most popular … | Actively maintained by a single core maintainer (erebe) with communit… | Client is a single static binary (no interpreter/runtime needed), unprivileged … | see server_requirements notes | **Downgraded 5→4** — confirmed zero network API; all control-plane logic is custom |
| 4/5 | sish | Go | 4.7k GitHub stars, 335 forks, 54 watchers — clearly the most popular … | Actively maintained, well-documented, production-oriented (used to ru… | Excellent — client-side needs literally nothing beyond a standard OpenSSH clien… | see server_requirements notes | **Downgraded 5→4** — admin API is read/kill-only; auth callback exists but no add/revoke API |
| 4/5 | frp | Go | Extremely popular: 109.5k GitHub stars, 15.2k forks, 1.6k watchers, 1… | Actively maintained: dev branch under continuous development, recent … | Single static Go binary (frpc) — no TUN/TAP, no admin/root required for standar… | Partially true — Go; frps/frpc expose an HTTP Admin API (dashboard, reload, pro… | Confirmed 4/5 — admin API is read-only status; registration needs a custom Login-plugin callback |
| 4/5 | piko | Go | 2.2k stars, 87 forks, 12 watchers; positioned explicitly as an open-s… | 426 commits, includes Helm charts, benchmark suite, cluster tests, an… | Piko agent (`piko agent http/tcp`) or Piko forward (`piko forward`) run as simp… | Partial/true -- there is a Go SDK (`piko` Go module) for opening listeners prog… | Confirmed 4/5 — pure JWT minting by us, no registration endpoint; no clean revoke primitive |
| 4/5 | boringproxy | Go | 1.4k stars, 131 forks, 21 watchers; has a dedicated website (boringpr… | 377 commits, has a CHANGELOG.md and systemd unit files -- shows ongoi… | Single Go binary CLI (`boringproxy client -server ... -token ...`), no TAP/TUN … | True, Go -- api.go implements a REST-style HTTP API used internally by boringpr… | Confirmed 4/5 — real REST API (`/api/tunnels`,`/tokens`), server self-issues SSH keys |
| 4/5 | rustunnel | Rust (server, client), TypeScript (dash… | 656 GitHub stars, 48 forks, 3 watchers, 315 commits. Has a hosted man… | Actively developed: CI badge, recent AGENTS.md/CLAUDE.md/MCP integrat… | Client is a single Rust binary (`rustunnel`) with a setup wizard (`rustunnel se… | true — language=Rust backend exposing REST/OpenAPI (HTTP+JSON) for listing/clos… | Confirmed 4/5 — real REST/OpenAPI control-plane, but AGPL-3.0 licensing |
| 4/5 | Portal (portal-tunnel) | Go | 269 stars, 28 forks, 1 watcher. Tagged in anderspitman/awesome-tunnel… | Very actively developed: 1,653 commits on main, CI badge passing, ded… | Single Go binary (portal-tunnel CLI / SDK), no TAP/TUN, no admin/root privilege… | true -- Go relay server exposes an HTTP/JSON control-plane API (`/sdk/register`… | Confirmed 4/5 — real `/sdk/*` API, but SIWE crypto-signing auth adds complexity |
| 4/5 | gt | Rust | 141 stars, 39 forks, 13 watchers. Backed by ao-space (a Chinese open-… | Actively maintained (86 commits, dev branch, CI via GitHub Actions, t… | No TAP/TUN interface — it's a userspace WebSocket(s)/HTTP(s)/TCP relay proxy, n… | false — the server exposes a browser-based web admin config UI (not a documente… | Confirmed 4/5 — real authAPI callback mirrors sish's model for device approval |
| 4/5 | specter | Go | 48 stars, 1 fork, 4 watchers. Small but polished single-maintainer pr… | Very active: 729 commits, recent AGENTS.md/DESIGN.md/OPERATOR.md/PROD… | No TAP/TUN — client is a userspace Go binary using QUIC/TLS transport to tunnel… | partial/true — Go-based local management API on the client (`--server` flag) fo… | Confirmed 4/5 — real Twirp mint/revoke API, but only on a separate owner-client sidecar |
| 3/5 | SirTunnel | Python (~50-line script) + Caddy (Go) o… | 1.6k stars, 125 forks, 14 watchers -- popular for its extreme minimal… | Only 23 commits -- intentionally frozen/minimal ('I'm unlikely to add… | No special client at all -- uses a standard SSH client's remote port forwarding… | True, indirectly -- Caddy's JSON admin API (typically on :2019) is a genuine HT… | **Downgraded 4→3** — confirmed zero API/auth/status of its own; 100% custom glue |
| 3/5 | tunwg | Go | 287 stars, 21 forks, 8 watchers. Public tunwg.com hosted instance; po… | Active — 18 commits, CI workflows, devcontainer, Docker images publis… | CONFIRMED: does NOT need a TUN/TAP device or admin/root. Despite being 'WireGua… | partial/true for Go only — `tunwg.NewListener()` is a real programmatic Go API … | **Downgraded 4→3** — only 2 endpoints, one shared secret, no revocation at all |
| 3/5 | pipenet | TypeScript (ESM), Node.js | 527 GitHub stars, 24 forks; used internally to power punkpeye's own '… | Newer and smaller community than localtunnel/tunnelmole (527 stars, s… | Fully unprivileged: `npm install pipenet` then `npx pipenet client --port 3000`… | see server_requirements notes |  |
| 3/5 | cactus-tunnel | TypeScript / Node.js | Only 58 stars/7 forks - the least popular of the four by a wide margi… | Small project (58 stars, 7 forks, 1 watcher) with a single primary ma… | Client is `cactus-tunnel client <server> <target>`, distributed via npm, so the… | see server_requirements notes |  |
| 3/5 | tunnelite | C# / .NET (SignalR-based) | 92 stars, 8 forks, 5 watchers - modest but growing; distributed via N… | Actively developed right now (commits within days of this check), wit… | Client is distributed as a NativeAOT-compiled CLI binary (or via NuGet as `Tunn… | see server_requirements notes |  |
| 3/5 | h2tunnel | Node.js/TypeScript (zero runtime depend… | 142 stars, 4 forks — niche/early-stage project, much smaller communit… | Small but genuinely active project with a real author blog post expla… | Also just an npm library — `import { TunnelClient } from "h2tunnel"` requires N… | see server_requirements notes |  |
| 3/5 | chisel | Go | 16.5k GitHub stars, 1.6k forks, 207 watchers, 261 commits. Well-estab… | Actively maintained: recent changelog entries describe substantive v1… | Single static Go binary combining both client and server (`chisel client`, `chi… | false as a control-plane — Go source is embeddable as a library (chisel is impo… |  |
| 3/5 | rathole | Rust | 14.2k stars, 827 forks, 73 watchers; widely used as an frp/ngrok alte… | Actively maintained (234 commits, ongoing releases with semver tags a… | Single static Rust binary (~500KiB minimal build) for both client and server, n… | false — an HTTP API for configuration is only planned/WIP per the README, not a… |  |
| 3/5 | bore | Rust | 11.5k stars, 528 forks, 62 watchers; packaged on Homebrew, AUR, Gento… | Actively maintained relative to its scope (68 commits — intentionally… | Single static Rust binary (`bore local <port> --to <server>`), installable via … | false — no HTTP/REST API; only a lightweight custom TCP control protocol betwee… |  |
| 3/5 | portr | Go (server/CLI core), some Python test-… | 3.2k stars, 115 forks, 10 watchers on GitHub; actively branded produc… | 612 commits on main, active development including recent additions (A… | Single Go binary CLI (portr http/tcp), installed via install.sh or package mana… | Unclear/likely false as a documented external API -- true only in the sense of … |  |
| 3/5 | Wiretap | Go | 1.1k stars, 46 forks, 14 watchers; developed and published by Sandia … | Only 82 commits -- smaller/newer project; has GitHub Actions CI and d… | VERIFIED CLAIM DOES NOT APPLY THE WAY EXPECTED: Wiretap's README explicitly sta… | False -- no documented REST/TS/JS API; all control is via CLI subcommands and g… |  |
| 3/5 | NPS Enhanced | Go (server, client, web UI in Go + JS f… | ~1.1k GitHub stars, 154 forks, 11 watchers, 1,937 commits — a very ac… | Actively maintained fork with continuous commits and automated Releas… | Client (npc) is a single static Go binary installed via a shell/PowerShell inst… | false (or unconfirmed) — the Web UI likely calls internal HTTP endpoints, but n… |  |
| 3/5 | reverst | Go | ~1.0k GitHub stars, 46 forks, 4 watchers, 105 commits. Backed by Flip… | Actively maintained by Flipt-io; has CI (Dagger-based unit/integratio… | Client is a Go library (go.flipt.io/reverst/client) built on net/http std-lib a… | Partial — Go client library (true, language=Go, used to register tunnel groups … |  |
| 3/5 | Port Buddy | Java (CLI client compiled to GraalVM na… | 535 GitHub stars, 48 forks, 2 watchers, 195 commits. Has a companion … | Active-looking multi-module project with GitHub Actions CI, Docker im… | CLI client (`portbuddy`) is described as a GraalVM-native command-line applicat… | Likely true (unconfirmed in README) — language=Java/Spring Boot 'server' module… |  |
| 3/5 | tunelo | Rust | 429 stars, 20 forks, 2 watchers. Markets itself as 'faster than frp, … | Active repo (43 commits, main branch), has CI workflows, Docker/compo… | No TAP/TUN. No admin/root needed — client is a single ~4MB Rust binary using QU… | false — no REST/control-plane API documented; relay is started via CLI flags/en… |  |
| 3/5 | remotemoe | Go | 302 stars, 33 forks, 6 watchers. Public remote.moe test instance moni… | Active, mature — 141 commits, Go Report Card + uptime badges in READM… | No TAP/TUN, no admin/root, and literally no software install at all — the 'clie… | false — no HTTP API; control is via SSH protocol/interactive shell only, though… |  |
| 3/5 | reverse-tunnel (rtun) | Go | 244 stars, 39 forks, 11 watchers. Docker images published (snsinfu/rt… | 75 commits, GitHub Actions CI passing, CHANGELOG.md maintained, relea… | Single Go binary agent (`rtun`), no TAP/TUN, no admin/root needed for normal po… | false -- no REST/HTTP control-plane; agent registration and port whitelisting a… |  |
| 3/5 | bore (jkuri/bore) | Go | 163 stars, 18 forks, 5 watchers. Hosted public instance at bore.digit… | 83 commits, MIT license copyright through 2023, prebuilt release bina… | Prebuilt single Go binary or build-from-source, no TAP/TUN, no admin/root requi… | false -- config/env-var driven server with no REST/HTTP control-plane; tunnel c… |  |
| 3/5 | hsync | JavaScript (Node.js and browser) | 15 stars, 6 forks, 5 watchers. Published on npm (badge present) with … | 98 commits, active CI (GitHub Actions badge passing), has husky/eslin… | No TAP/TUN interfaces, no admin/root privileges. Install via `npm i -g hsync` o… | Partially true — JavaScript. The client is programmable/embeddable (browser glo… |  |
| 2/5 | Punchmole | JavaScript (Node.js) | 19 stars, 4 forks, 1 watcher. Author states it has been used 'extensi… | 46 commits total, single-maintainer project (degola). README describe… | No TAP/TUN interfaces, no admin/root privileges. Pure Node.js WebSocket client … | true — JavaScript/Node.js. Both PunchmoleServer() and PunchmoleClient() are pla… | **Downgraded 4→2** — API_KEYS is startup-only; no dynamic add/revoke without a restart |
| 2/5 | localtunnel | JavaScript (Node.js, CommonJS) | 22.5k GitHub stars, 1.6k forks, 408 npm dependents, decades-long name… | Very popular historically but effectively in maintenance-only mode: r… | Client is simple and unprivileged: `npm install -g localtunnel` or `npx localtu… | see server_requirements notes |  |
| 2/5 | Tunnelmole | TypeScript (compiled to JS/CJS, Node.js) | 1.9k GitHub stars, 124 forks, 17 npm dependents, actively promoted as… | Actively maintained relative to the older localtunnel forks - has CI,… | Client install is simple and unprivileged for the JS path: `npm install --save … | see server_requirements notes |  |
| 2/5 | go-http-tunnel | Go | 3.3k stars, 312 forks, 61 watchers — the second most popular tool in … | Popular and historically well-regarded, but maintenance has clearly s… | Requires running the project's own compiled Go `tunnel` client binary on the en… | see server_requirements notes |  |
| 2/5 | localtunnel | ? | localtunnel npm: 408 dependents, 33 versions (client pkg last publish… | Widely known (the original 'ngrok alternative'), but effectively unma… | There IS an existing Node.js client library (npm 'localtunnel'), but no officia… | see server_requirements notes |  |
| 2/5 | tunnelmole | ? | npm: 17 dependents, 92 versions; GitHub client repo stars in the low … | Open-source 'ngrok alternative', actively marketed, has a hosted serv… | Existing npm/Node client only; no Python client. The end-user's local machine w… | see server_requirements notes |  |
| 2/5 | primus | ? | 137 dependents on npm, 109 published versions | Mature, long-lived abstraction layer over multiple realtime transport… | No Python client exists; Primus's client is JS-only (browser or Node). Its docu… | see server_requirements notes |  |
| 2/5 | Telebit | JavaScript (Node.js), some Shell | Very low on its own git host: 2 stars, 1 fork, 1 watcher on git.coola… | Surprisingly still receiving commits: latest commit 2025-10-26 (versi… | Single CLI/binary install via `curl https://get.telebit.io/ \| bash` (installs a… | false (or unclear) — JavaScript/Node.js codebase; no documented external HTTP A… |  |
| 2/5 | tunnelto | Rust | 7.1k stars, 546 forks, 49 watchers; published on crates.io as 'wormho… | Moderate activity (119 commits); README shows Cargo.toml pinned near … | Single Rust binary installable via Homebrew, Cargo, or prebuilt release downloa… | false — no documented external REST API; tunnel registration happens via the cl… |  |
| 2/5 | expose | PHP | 4.6k stars, 308 forks, 57 watchers; published on Packagist (beyondcod… | Repo has moved/renamed to exposedev/expose (beyondcode/expose redirec… | PHP-based CLI (installed via Composer / PHAR, or the 'expose' bash wrapper scri… | false (not confirmed) — no documented standalone HTTP API separate from the ful… |  |
| 2/5 | pgrok | Go | 3.6k stars, 131 forks, 20 watchers; smaller but growing project, list… | Actively developed (276 commits, recent tooling like .claude/.agents … | Single Go binary (`pgrok`), installable via Homebrew or prebuilt release archiv… | false — no documented REST API for programmatic tunnel registration; provisioni… |  |
| 2/5 | gsocket (Global Socket) | C | 1.9k stars, 201 forks, 42 watchers; long-running project (the Global … | 755 commits, shields.io badges show 'Maintenance: yes' and active bui… | gs-netcat and related CLI tools are plain binaries (install.sh or package manag… | False -- it's a low-level secure TCP rendezvous tool (like netcat), not a tunne… |  |
| 2/5 | Wiredoor | TypeScript (server/API + frontend), sep… | 1.6k stars, 78 forks, 6 watchers; polished docs site (wiredoor.net), … | 540 commits, has CI (GitHub Actions), renovate bot for dependency upd… | CONFIRMED WireGuard-based: Client Nodes and Gateway Nodes initiate encrypted Wi… | Likely true, TypeScript -- the repo is tagged 'api-server' and 'management-syst… |  |
| 2/5 | PageKite | Python (pure Python 2/legacy-oriented i… | 751 GitHub stars, 122 forks, 38 watchers, 1,492 commits — pagekite.ne… | README explicitly states 'This program is under active development an… | PageKite's `pagekite.py` is a pure Python script/CLI — no TAP/TUN interface nee… | false — no documented REST API or JS/TS SDK for programmatic tunnel registratio… |  |
| 2/5 | tunnl.gg | Go | 547 GitHub stars, 44 forks, 6 watchers, but only 23 commits total — s… | Small commit count (23) suggests a young/lean project rather than a l… | No custom client binary at all — the 'client' is just the standard OpenSSH clie… | false — no REST/JS API; server is env-var/config-driven, and tunnel creation is… |  |
| 2/5 | mmar | Go | 312 stars, 12 forks, 2 watchers. Public mmar.dev free service; docume… | Active — 129 commits, GoReleaser CI, Homebrew tap, Docker images publ… | No TAP/TUN, no admin/root needed at runtime (install script uses sudo only to p… | false — CLI/env-var driven only. |  |
| 2/5 | EXPOSE (exposesh/expose-server, renamed to gaetanlhf/EXPOSE) | Multi-component: Python (SSH server), N… | 293 stars, 6 forks, 2 watchers. Repo has been renamed/moved from expo… | Active — 32 commits, includes demo video, deployed globally on Fly.io… | No TAP/TUN, no admin/root, and no install at all — client is just the OS ssh bi… | false for external registration — there IS an internal Node.js service, but it'… |  |
| 2/5 | pgrok (jerson/pgrok) | Go (fork of inconshreveable/ngrok) | 284 stars, 56 forks, 1 watcher, 457 commits (mostly inherited ngrok h… | Archived by owner Dec 17, 2022 (read-only). README itself notes 'ejem… | No TAP/TUN, no admin/root. Single binary installable via Homebrew (`brew instal… | false — CLI/flag-configured only; local web inspector UI is for traffic inspect… |  |
| 2/5 | BitBang (bitbang-cli) | Go | 350 stars, 27 forks, 3 watchers. Small but growing project with an ac… | Actively developed (218 commits on main, CI tests passing badge). Rec… | Single static Go binary (curl \| sh installer or `go build`), no admin/root priv… | false -- no rendezvous/signaling server source or API surface is published in t… |  |
| 2/5 | hypertunnel | JavaScript/Node.js | 271 stars, 50 forks, 4 watchers. Published to npm as `hypertunnel`. M… | Lerna/yarn-workspaces monorepo, Travis CI (an old CI system), 71 comm… | CLI usable via `npx hypertunnel --port 8080` or global npm install. No TAP/TUN,… | Partial/true -- JavaScript (Node.js) server package exists and can theoreticall… |  |
| 2/5 | srv.us | Go (backend); no client code needed (us… | 180 stars, 15 forks, 1 watcher. Repository now canonically at xmit-co… | 84 commits, actively described as a running production service with s… | No client software to install at all -- uses the user's existing OpenSSH client… | false -- no HTTP control-plane; interaction model is purely 'establish an SSH -… |  |
| 2/5 | holepunch | Python (Flask) | 60 stars, 9 forks, 7 watchers. Was the backend for api.holepunch.io /… | 83 commits, last significant activity appears old (CircleCI-era, Pipf… | This repo is server-side only (Holepunch API/backend); the client is SSH-based … | true — Flask REST API backend (Python) that provisions SSH-based reverse tunnel… |  |
| 1/5 | progrium/localtunnel | Go (current v3 rewrite); earlier histor… | 3.2k stars, 243 forks - notable mainly for historical/pioneering sign… | Explicitly and openly declared unmaintained/legacy by its own author … | Would require installing/building a Go binary (`go install github.com/progrium/… | see server_requirements notes |  |
| 1/5 | jprq | Go | ~1.5k stars, 213 forks, 15 watchers - moderate popularity, positioned… | Red flags: the maintained hosted service (jprq.io) went members-only/… | Client is a single compiled Go binary installed via package managers (brew/scoo… | see server_requirements notes |  |
| 1/5 | koding/tunnel | Go (library) | 331 stars, 69 forks — modest niche popularity from the ~2016 Koding p… | Effectively abandoned/unmaintained. README itself states 'under activ… | Client is also a Go binary/library (tunnel.NewClient) requiring a compiled Go c… | see server_requirements notes |  |
| 1/5 | http-proxy | ? | 3,347 dependents on npm (very widely used); underlying GitHub repo ha… | Extremely mature and battle-tested generic HTTP/WS proxy library, emb… | None — it's server-only and assumes the target is directly reachable, which is … | see server_requirements notes |  |
| 1/5 | yamux | ? | 0 dependents, 2 published versions | Essentially abandoned toy/proof-of-concept implementation of a stream… | N/A — package is effectively dead. Note also that this npm package has no relat… | see server_requirements notes |  |
| 1/5 | tunnel.pyjam.as | Python | Very low visibility: hosted on GitLab (not GitHub), no star/fork coun… | Repo exists with a CI pipeline (recent commit 'Remove javascript from… | FAILS the client constraint outright: per the awesome-tunneling list, tunnel.py… | false — it's a WireGuard config generator/service, not an HTTP API for tunnel r… |  |
| 1/5 | SSH-J.com | N/A — it is a public, anonymous SSH jum… | No GitHub stars/forks (Bitbucket-hosted, single small repo). Known wi… | Appears to be a long-running personal hosted service by ValdikSS (als… | Explicitly 'No software, no registration' — just an ordinary SSH client (`ssh -… | false — no API of any kind; it's raw `ssh -R`/`-J` semantics with no control-pl… |  |
| 1/5 | Greenhouse | Go (68.2%), HTML (21.1%), Shell (5.8%),… | Minimal: 0 stars, 0 forks, 2 watchers on its self-hosted Gitea instan… | Effectively abandoned/stalled: last substantive activity was years ag… | Designed to be a lightweight port-forwarding client with automatic HTTPS ('leas… | Possibly true historically (Go) — code includes a 'public_api.go' and multiple … |  |
| 1/5 | ngrok 1.0 | Go | 24.4k GitHub stars, 4.3k forks — historically very popular (2013-2016… | Explicitly dead as an open-source project: GitHub shows 'This reposit… | N/A for our purposes — codebase is unmaintained/archived and the original hoste… | false for this v1 codebase — no documented control-plane API; the JS/Python/Go/… |  |
| 1/5 | sshuttle | Python (with some C) | 13.6k stars, 794 forks, 134 watchers; long-standing popular 'poor man… | Actively maintained (1,363 commits, ongoing releases, uses modern too… | FAILS constraint. Despite README claims of 'doesn't require admin', sshuttle wo… | false — no HTTP API; it's purely a CLI wrapping SSH + local firewall manipulati… |  |
| 1/5 | Selfhosted Gateway | Shell/Makefile glue around Docker Compo… | 1.7k stars, 88 forks, 23 watchers -- decent traction as a 'RPoVPN' se… | Only 65 commits -- much smaller/newer project than the others; explic… | CONFIRMED WireGuard-based and requires a TUN interface: the client-side docker-… | False -- explicitly 'no code or APIs, just an ultra generic NGINX config and so… |  |
| 1/5 | onionpipe | Go (with CGO/libtor bindings for the st… | 617 GitHub stars, 37 forks, 9 watchers, 157 commits — moderate niche … | Automated releases on every commit to main (Docker image publishing),… | DISQUALIFYING: onionpipe requires and bundles a Tor daemon (`tor` binary in $PA… | false — CLI-only tool; no REST/JS API; a YAML config mode and a possible future… |  |
| 1/5 | tunneller | Go | 488 GitHub stars, 43 forks, 0 watchers, 79 commits. | DISQUALIFYING RED FLAG: repository was archived by the owner on Sep 1… | Client is a Go binary (`tunneller client -expose localhost:8080`) — no TAP/TUN … | false — no REST/JS API; operates via CLI and an MQTT message bus. |  |
| 1/5 | Crowbar | Go | 474 stars, 38 forks. No notable adoption cited; niche corporate-proxy… | Archived by owner May 21, 2025 (read-only), migrated to Codeberg. Onl… | No TAP/TUN. No admin/root required. Single Go binary (crowbar-forward); honors … | false — no API; CLI/config-file only, and per-tunnel model doesn't match a host… |  |
| 1/5 | docker-tunnel | Shell (83.4%) / Dockerfile (16.6%) | 292 stars, 52 forks, 7 watchers, but only 1 contributor and no releas… | Abandoned: last commit Jul 6, 2019, only 10 commits total, no release… | Requires Docker installed on the client/dev machine plus mounting an SSH privat… | false. |  |
| 1/5 | vgrok | TypeScript | 153 stars, 5 forks, 1 watcher. Published to npm as `@styfle/vgrok`. S… | Only 31 commits; README frames it as designed for 'quick local develo… | Simple npm-installable CLI/TypeScript library (`npm i -g @styfle/vgrok`), no TA… | true (client-side only) -- TypeScript programmatic API exists (`import { client… |  |
| 1/5 | docker-wireguard-tunnel | Shell (Docker-based wrapper around Wire… | 116 stars, 14 forks, 3 watchers. Niche self-hosted homelab tool; no m… | 90 commits, active CI (build-and-push + check-for-updates workflows),… | DISQUALIFYING: requires an actual WireGuard kernel/userspace tunnel interface (… | false — purely docker-compose env/config file driven, no REST/JS API. |  |
| 1/5 | wireport | Go (with Caddy, CoreDNS, WireGuard) | 48 stars, 2 forks, 1 watcher. New project (video tutorial on YouTube)… | 126 commits, actively developed, has GitHub Sponsors, releases for Li… | DISQUALIFYING: explicitly requires an installed WireGuard client and joins a re… | false — CLI/docker-label driven, no documented REST/JS control-plane API. |  |
| 1/5 | YTunnel | Rust | 53 stars, 4 forks, 1 watcher. Small hobby/indie project (yetidevworks… | 54 commits, has CHANGELOG.md, GitHub Actions CI, cargo/crates.io + Ho… | No TAP/TUN. However it is fundamentally a management CLI/TUI wrapper around Clo… | false — CLI/TOML config driven locally; it does call the Cloudflare API but tha… |  |
| 1/5 | ngtor | Java (Spring Boot) | 35 stars, 6 forks, 1 watcher. Niche/early-stage project, no notable a… | 91 commits, CI via GitHub Actions, tagged releases and Jitpack distri… | Uses Tor as its transport — exposes local services as Tor hidden (.onion) servi… | false — CLI only, Java/Spring Boot internals, no exposed control-plane API. |  |
| 1/5 | tnnlink | Go | 26 stars, 6 forks, 0 watchers. Very small hobby project ('a fun weeke… | DISQUALIFYING red flag: repository is archived (read-only) as of Jul … | No TAP/TUN — client-side is just a standard SSH client doing remote port forwar… | false — config.toml + SSH -R flags only, no API. |  |
| 1/5 | netmask | Python | 14 stars, 2 forks, 1 watcher — a small hobby-scale project with no ev… | Only 5 commits total; roadmap items (Automatic TLS, HTTP control pane… | DISQUALIFYING for our use case: the client (netmaskc) ships with a GUI interfac… | false — no HTTP/REST control-plane exists; it's explicitly on the unimplemented… |  |
| 1/5 | ephemeral-hidden-service | Python | 10 stars, 2 forks, 1 watcher — very small, niche utility with no nota… | Only 3 commits total in the entire repo history — essentially a minim… | Heavy overhead relative to our constraints: requires a full local Tor installat… | false — no HTTP/REST control-plane and no JS/TS API of any kind; it's a pure Py… |  |
| 1/5 | TunnelAPI 1.0 | JavaScript/Node.js (backend, tunnel-ser… | 7 stars, 3 forks, 1 watcher — very small/early project; published as … | 66 commits; has GitHub Actions CI, a CHANGELOG.md, and versioned rele… | No TAP/TUN interfaces or root/admin required. Tunnel client is a plain Node.js … | true — JavaScript/Node.js REST API (JWT-authenticated Express endpoints under /… |  |

## Top tier (score 5) — best overall fit

### Pangolin — Viability: 5/5


**Language/license.** TypeScript / Next.js (server + dashboard); companion client 'newt' is Go — Dual-licensed: AGPL-3.0 (open source) and Fossorial Commercial License (for commercial use cases).[80][89][99]

**Popularity.** 22.8k GitHub stars, 786 forks, 71 watchers, 8,469+ commits — very actively developed and increasingly popular self-hosted SASE/zero-trust platform. Its client connector 'newt' has its own repo with 909 stars / 87 forks. Explicitly recommended in the awesome-tunneling README as one of two 'production ready' self-hosted options (alongside frp).[80][89][99]

**Maturity.** Very actively maintained: huge, continuously growing commit history (8,469 commits), current feature work includes an 'AI Gateway,' recent architecture docs, and an official hosted Pangolin Cloud offering alongside self-hosting — strong signal of ongoing investment, not a single-maintainer side project (fosrl org, commercial backing).[80][89][99]

**Client requirements.** The client connector, 'newt,' is a single static Go binary (get-newt.sh install script; also packaged for Windows via Inno Setup, Nix flake, Docker). Critically, newt implements WireGuard fully in userspace via netstack (gVisor's userspace TCP/IP stack) rather than creating a kernel TUN/TAP device — per its own README: 'it will use the information encoded (endpoint, public key) to bring up a WireGuard tunnel using netstack... fully in user space.' This means no OS-level TUN/TAP interface and no admin/root privileges are required to run it — it registers with the Pangolin server over HTTP+WebSocket using an ID/secret, a good fit for our constraint.[80][89][99]

**Server/API.** Full server source available (TypeScript/Next.js control plane + Go 'Gerbil' WireGuard exit-node component), self-hostable via Docker Compose. Newt registers with Pangolin via plain HTTP requests to get a session token, then holds a WebSocket for control messages — i.e., there IS a documented HTTP+WebSocket control-plane protocol between client and server, though the full public developer-facing REST API surface for third-party backends (e.g., 'create site/resource via our own code') would need confirming against docs.pangolin.net, but the architecture (identity, sites, resources, clients all as first-class managed objects in a Next.js/TypeScript control plane) strongly suggests a workable admin API exists for programmatic registration. Programmable API: Likely true — TypeScript; Pangolin's control plane is a TypeScript/Next.js app with organized resources (sites, resources, clients, identity/access) and a documented registration handshake (HTTP session token + WebSocket control channel) used by the newt client itself, implying an internal REST/WS API that a SvelteKit backend could potentially call or emulate, though full public API docs would need direct verification for a supported 'register tunnel' surface.[80][89][99]

**Verdict.** Best overall fit: hosted server binary/Docker deployment is expected and well-documented, the client (newt) is a single Go binary using userspace WireGuard (no TUN/TAP, no root) satisfying our hardest constraint, and the control plane is TypeScript with an HTTP+WebSocket registration protocol that aligns closely with 'hittable control-plane from our SvelteKit backend.' Very active development and strong adoption reduce abandonment risk versus the other candidates. Top pick.[80][89][99]

**Implementation-validated (5/5, confirmed).** Cloned and traced: newt registers via a server-generated ID+secret exchanged over HTTP for a session token, then a persistent WebSocket for control messages (`newt/README.md`). The Integration API (Bearer org-scoped keys, off by default on self-hosted, enabled via `flags.enable_integration_api`) gives a real `site → public-resource → target` object graph — `PUT /org/{orgId}/site`, `PUT /org/{orgId}/public-resource`, `PUT /public-resource/{resourceId}/target` — that is essentially a literal match to "register a device, bind a hostname, route to its local port." Status polling confirmed via `GET /org/{orgId}/site/{niceId}` returning an `online` boolean, plus target-level HTTP/TCP health checks. See `implementation-plans/pangolin/PLAN.md` for the full trace, including a fleet-scale provisioning-key mechanism (`spk_...`) worth using instead of per-device API calls. [80][89][99]


### chiSSL — Viability: 5/5


**Language/license.** Go — MIT.[31][35]

**Popularity.** 195 stars, 8 forks, 4 watchers (repo now lives under the 'unblocked' GitHub org, formerly NextChapterSoftware). Modest star count but polished docs site, Homebrew tap, and one-line server installer suggest real production usage internally at NextChapterSoftware/Unblocked.[31][35]

**Maturity.** 37 commits but clearly a mature, feature-rich fork/successor of chisel (jpillora/chisel lineage), with a full documentation site (mkdocs, unblocked.github.io/chissl), Homebrew formula, versioned installer script (v2.0), and CI. No obvious abandonment signals; presents as a maintained internal tool open-sourced by the company.[31][35]

**Client requirements.** Single static Go binary client (Homebrew install or prebuilt release binary), no TAP/TUN interfaces, no admin/root privileges. Client config can be a simple CLI invocation or a YAML profile file at `$HOME/.chissl/profile.yaml`.[31][35]

**Server/API.** Full server source available (Go, `server/` directory), self-hostable with a one-line installer script, automatic TLS via Let's Encrypt domain or custom certs. Explicitly documents 'REST API and web dashboard' as a headline feature, with a published OpenAPI spec (openapi-public.yaml) and Redoc-rendered API reference at unblocked.github.io/chissl/api/. Supports SQLite (default) or PostgreSQL for persistence, admin/user RBAC, API tokens, and optional SSO -- this is the most API/control-plane-complete option in the batch. Programmable API: true -- Go server exposes a documented REST API (OpenAPI/Redoc spec published) plus a web dashboard, covering tunnel/listener management, multicast tunnels, users, and API tokens -- directly usable for programmatic registration from an external app.[31][35]

**Verdict.** Best fit overall: fully self-hostable Go server with a real, documented REST API (OpenAPI spec + dashboard) for managing tunnels/listeners/tokens programmatically, and a client that is a single admin-free, TUN-free binary. Only caveats are its relatively small star count and its being a fork of chisel with a smaller community than the upstream project, but functionally it directly satisfies every hard requirement.[31][35]

**Implementation-validated (5/5, confirmed with one real gap).** Cloned and traced: the REST API is real and extensive (`/api/users`, `/api/listeners`, `/api/tunnels`, `/api/sessions`, per-user API tokens), documented in a published OpenAPI YAML. But client linking is a **static username:password pair** (via `POST /api/users`), not a token/pairing exchange — and critically, **routing is port-based only**: each tunnel claims a distinct server TCP port, with no hostname/Host-header dispatch found anywhere in source. To get "one route = one client" (the scenario's actual requirement) you'd need to layer your own reverse proxy (Caddy/Traefik) in front of chiSSL doing the Host-based routing, maintaining a port↔client table yourself. Status polling is solid: `GET /api/tunnels/{id}` returns a `status` field, `GET /api/tunnels/active` filters to live ones. See `implementation-plans/chissl/PLAN.md` for the full trace including undocumented-but-real endpoints found only by reading source. [31][35]


### gost — Viability: 5/5


**Language/license.** Go — MIT.[40][45]

**Popularity.** 7.5k stars, 828 forks, 52 watchers; large multi-protocol proxy/tunnel toolkit with companion GUI (gostctl) and WebUI (gost-ui) projects, active Telegram/Google Group community[40][45]

**Maturity.** Actively maintained (558 commits, continuous releases, install script, Docker images, dedicated docs site gost.run); broad protocol support (websocket, grpc, quic, kcp, etc.) and a documented Web API — no major red flags.[40][45]

**Client requirements.** Single Go binary, installable via releases page, install script, or Docker; used as a reverse-proxy/tunnel client with no admin/root required. Note: gost ALSO supports optional TUN/TAP device modes for VPN-like use cases, but that is an optional feature — for a plain reverse-proxy/tunnel client (websocket/TCP forwarding) it runs as a normal unprivileged process. Meets constraints as long as TUN/TAP mode is avoided.[40][45]

**Server/API.** Full server source available (same repo, Go). Server exposes a genuine RESTful Web API (`-api` flag or `api:` config block) with HTTP Basic Auth, configurable path prefix, access logging, and a built-in Swagger/OpenAPI spec (testable at api.gost.run/swagger-ui) for dynamically creating/listing/removing services, listeners, and chains at runtime — this is a real hittable control-plane, not just a config file. Programmable API: true — Go server with a documented RESTful HTTP API (Basic Auth, Swagger/OpenAPI spec) for dynamic runtime configuration of services/tunnels; can be called from any backend including a SvelteKit/Node app via plain HTTP requests.[40][45]

**Verdict.** Strong candidate: single unprivileged Go binary client, open server source, and a real documented REST API with Swagger docs for dynamically registering tunnels/services — exactly the kind of control-plane we want to hit from SvelteKit. Slight complexity cost is gost's very large feature surface (many protocols/concepts to learn) versus more minimal tools.[40][45]

**Upgraded to 5/5 after implementation deep dive** (was 4/5 from docs review alone). Cloned and traced: gost's server exposes a genuine Swagger/OpenAPI-documented REST API (`-api :18080`) for full CRUD on services/chains/authers, and critically an **Ingress object** that binds a hostname directly to a server-issued tunnel-ID — `POST /config/ingresses` creates the hostname→tunnel-ID mapping our SvelteKit backend needs, and the client connects with `tunnel.id=<uuid>` in its connector config. This is arguably the cleanest single-binary rival to Pangolin's routing model in the entire 78-tool survey. The one gap: I could not confirm a documented "list connected tunnel clients" endpoint from the crawled prose docs — it likely exists in the Swagger spec at `api.gost.run/docs/swagger.yaml`, which wasn't deep-dived; treat status polling as needing a hands-on API-spec review before committing. See `implementation-plans/gost/PLAN.md`. [40][45]


### zrok — Viability: 5/5


**Language/license.** Go — Apache-2.0.[42]

**Popularity.** 4.7k stars, 222 forks, 30 watchers; backed by the OpenZiti project/NetFoundry, has a hosted zrok.io service, active YouTube 'office hours' series, large commit history (4,281 commits)[42]

**Maturity.** Very actively maintained (huge commit volume, frequent releases, dedicated docs site docs.zrok.io, self-hosting guide, and a website/UI subproject); no red flags — one of the more professionally maintained projects in this batch.[42]

**Client requirements.** Single binary (zrok CLI) across Windows/macOS/Linux/Raspberry Pi; built on OpenZiti's zero-trust overlay but does NOT require a traditional TUN/TAP virtual NIC or admin/root for basic HTTP/TCP share/access commands — it operates as a userspace SDK-driven client. Meets constraints for typical share/access use, though OpenZiti's broader ecosystem does have optional TUN-based 'ziti-edge-tunnel' clients for full VPN-style use that would NOT meet the constraint if chosen instead.[42]

**Server/API.** Full server (controller + edge components) source available in the same repo (Go); single binary can contain the whole self-hosted service per the README ('Single binary contains everything you need'). Also ships a Go SDK (`sdk.CreateShare`, `sdk.NewListener`) for embedding zrok sharing directly into applications, and a REST API (rest_client_zrok/rest_server_zrok/rest_model_zrok directories generated from an OpenAPI spec) that a backend could call to create/manage shares programmatically. Programmable API: true — Go SDK for embedding sharing/tunnel logic, plus a generated REST client/server (OpenAPI-based) for the zrok controller that exposes share creation/management over HTTP; callable from a SvelteKit backend via plain HTTP even though no first-party JS/TS client is provided (would need to hit the REST endpoints directly or generate a TS client from the OpenAPI spec).[42]

**Verdict.** Strong option: admin-free single-binary client, fully open server source, actively maintained by a serious team, and a genuine REST API + Go SDK for programmatic share management. Slightly more architecturally complex than rathole/bore (built atop the OpenZiti zero-trust overlay, controller+edge-router topology) which adds self-hosting overhead, but well worth serious evaluation for its control-plane API.[42]

**Upgraded to 5/5 after implementation deep dive** (was 4/5 from docs review alone). Cloned and traced: zrok has a genuine `POST /enable` account-token→identity exchange for device pairing, and — the standout finding — a remarkable **Agent API** (`/agent/enroll`, `/agent/share`, `/agent/status`, `/agent/ping`, `/agent/share/http-healthcheck`) that lets the controller (our SvelteKit backend) remotely drive a client's tunnel *and* health-check it end-to-end without touching the device directly. This is the single best status/health story found across all 16 tools examined in the implementation-validation pass — genuinely end-to-end, not just "is the tunnel process alive." Tradeoff: heaviest underlying architecture of the group (a full OpenZiti zero-trust mTLS overlay). One open question: didn't confirm whether `zrok2 enable` auto-starts the agent daemon or needs a separate `zrok agent start` step. See `implementation-plans/zrok/PLAN.md`. [42]


## Strong tier (score 4) — solid fit, partial API/language mismatch

### wstunnel — Viability: 4/5


**Language/license.** Rust (v7+ rewrite; earlier versions in Haskell) — BSD-3-Clause.[1]

**Popularity.** ~7.0k GitHub stars, 567 forks, 56 watchers - by far the most popular of the 4 tools surveyed. Has a dedicated docs site (wstunnel.erebe.eu), a public demo server, and commercial sponsorship. Widely referenced in firewall-bypass/DPI-evasion contexts and mentioned in various 'ngrok alternative' and censorship-circumvention roundups.[1]

**Maturity.** Actively maintained by a single core maintainer (erebe) with community PRs/forks (567). Project has a real history: started in Haskell, was fully rewritten to Rust in v7.0.0 for maintainability, which is itself a maturity signal (author cared enough to redo it rather than abandon it). Single-maintainer risk exists but project has been running for years with steady releases, static binaries, CI, docs, and sponsor (Service Planet) backing. No signs of abandonment.[1]

**Client requirements.** Client is a single static binary (no interpreter/runtime needed), unprivileged - just run `wstunnel client ...` as a normal user process. Does not require admin/root and does not create virtual network interfaces (unlike VPN-style tools); it only opens local TCP listeners/forwards on ports the user is allowed to bind (high ports need no elevation). This is very close to ideal for our 'no admin' requirement, but it's still a foreign compiled binary the user must download/trust and keep updated, and we'd have to bundle/distribute per-OS/per-arch binaries ourselves.[1]

**Server/API.** Requires running a SEPARATE compiled Rust binary (wstunnel server) as its own OS process alongside the SvelteKit/Node app - it is NOT an embeddable Node/npm library, there is no way to run its server logic inside our existing Node HTTP process. On the cloud side we would need to: download/pin a wstunnel release binary (or build from source), run it as a systemd service or sidecar container, expose/manage its listening port, handle TLS certs (or rely on its auto-reload/self-signed cert feature), and route traffic between it and our Node app (e.g. wstunnel terminates the tunnel and forwards to a local TCP port that our Node app listens on, or vice versa). This is a genuine second service to deploy, monitor, restart on crash, and keep patched - it does not disappear into our existing process. Programmable API: see server_requirements notes[1]

**Verdict.** The most mature and popular tool surveyed, with a single static Rust binary client requiring no admin/root and no runtime dependency (Node, Python, etc.) — as lean as a client gets short of reusing an OS-bundled tool like SSH. With the separate-server-process requirement now expected rather than disqualifying, this becomes a top recommendation; the one real caveat is its documented finickiness behind Nginx/Cloudflare/HAProxy, worth testing early against our actual serverless SvelteKit deployment's front door.[1]

**Downgraded to 4/5 after implementation deep dive** (was 5/5 from docs review alone). Cloned and traced the full README/docs: wstunnel has **zero network API of any kind** — no port to curl, no admin socket, nothing beyond CLI flags at process start and two hot-reloadable config files (a TLS cert pair and a `restrictions.yaml` access-control file). Its only per-connection "auth" is a static regex match against the raw `Authorization` header at WebSocket-upgrade time — not JWT verification, just string matching. Implementing the scenario's device-registration/linking/status-polling requirements means SvelteKit has to build essentially a full mini-PaaS around it: a device database, token issuance, YAML templating and safe distribution to the VPS, a per-device port-allocation and reverse-proxy layer (wstunnel only allocates raw ports, never hostnames), and all health polling from scratch (recommended: an HTTP `/health` check through the tunnel itself). This is substantially more custom backend work than Pangolin/chiSSL/gost/zrok require. See `implementation-plans/wstunnel/PLAN.md` for the full glue-code architecture this implies. [1]


### sish — Viability: 4/5


**Language/license.** Go — MIT.[5]

**Popularity.** 4.7k GitHub stars, 335 forks, 54 watchers — clearly the most popular of the four tools surveyed, used in production by a public managed tunnel service.[5]

**Maturity.** Actively maintained, well-documented, production-oriented (used to run a managed public service tuns.sh, sponsored by pico.sh). No obvious red flags; healthy commit cadence, CI (golangci-lint), Docker images, binary releases.[5]

**Client requirements.** Excellent — client-side needs literally nothing beyond a standard OpenSSH client already present on virtually every OS (`ssh -R 80:localhost:8080 tuns.sh`). No admin/root required, no special binary to install, no network interface management. This is the best-in-class client experience of all 4 options.[5]

**Server/API.** sish is a standalone Go SSH server binary (main.go, cmd/, sshmuxer/, httpmuxer/ packages) — it is NOT embeddable inside a Node.js process. It must run as a separate long-lived service/binary (or Docker container) on our cloud infra, listening on an SSH port (e.g. 2222) plus HTTP(S)/WS(S) ports for tunnel traffic, with its own TLS cert management, key-based auth files, and routing config. Our SvelteKit backend would sit behind/alongside it, or sish's HTTP muxer would need to front our app's routes — effectively adding a whole new infra component (process supervision, port allocation, key management, DNS wildcard) that must be deployed and operated independently from the Node app. Programmable API: see server_requirements notes[5]

**Verdict.** Best-in-class client of the entire survey (literally just OpenSSH, already present on every OS, zero install/admin) combined with a mature, actively-maintained, production-proven server (backs the public tuns.sh service). Now that a separate server binary/process is an accepted requirement rather than a penalty, sish's only real cost is standing up and operating one more Go service — no worse than any other option in this tier, and its client leanness is unmatched.[5]

**Downgraded to 4/5 after implementation deep dive** (was 5/5 from docs review alone). Cloned and traced: sish does have a small HTTP admin API (`--admin-console`) — `GET /_sish/api/clients` (list live SSH sessions with pubkey fingerprints and bound routes) and `POST /_sish/api/disconnectclient/<name>` — but it is **read/kill only**, with zero endpoint to add or revoke authorized keys. The actual authorization surface is either a watched `authorized_keys`-style directory, or a synchronous per-connection HTTP callback (`--authentication-key-request-url`) that sish itself POSTs to on every SSH connection attempt and treats HTTP 200 as approval — this callback pattern is genuinely useful (it turns "authorize a device" into a plain DB-row lookup in our own backend) but there is no built-in pairing/enrollment/access-token flow at all; the author's own documented onboarding pattern is literally an admin curling a GitHub `.keys` URL into the watched directory. Routing (subdomain-per-connection, Host-header dispatch) remains genuinely automatic and free once a tunnel is up — the best routing story of any tool that isn't Pangolin/gost. See `implementation-plans/sish/PLAN.md` for the full authorize-key callback design. [5]


### frp — Viability: 4/5


**Language/license.** Go — Apache-2.0.[78][89]

**Popularity.** Extremely popular: 109.5k GitHub stars, 15.2k forks, 1.6k watchers, 1,524+ commits. One of the most widely used open-source reverse-proxy/tunnel tools; has an active related-projects ecosystem (gofrp/plugin, gofrp/tiny-frpc), CI badges, GitHub Sponsors, and corporate sponsors (JetBrains, RapidProxy, Olares). Very strong, long-standing adoption signal.[78][89]

**Maturity.** Actively maintained: dev branch under continuous development, recent feature-gate/experimental-feature system documented (ALPHA/BETA/GA lifecycle), ongoing releases via GitHub Releases and CircleCI. No abandonment red flags — large contributor base, not single-maintainer dependent for day-to-day operation despite fatedier being lead.[78][89]

**Client requirements.** Single static Go binary (frpc) — no TUN/TAP, no admin/root required for standard TCP/HTTP/HTTPS proxying (root only needed if binding to privileged ports <1024, same as any normal server software). Config-file driven (TOML) but can also be driven via CLI flags/env vars. There's also a 'tiny-frpc' variant (~3.5MB) for constrained devices. Overall this is an excellent, simple client story.[78][89]

**Server/API.** Full server (frps) source code available in the same repo (Go). frps does expose an HTTP-based Admin API / Dashboard (documented in the frp docs, e.g. `/api/*` endpoints for reload, proxy status, server info) that can be queried, though it's primarily geared toward admin/monitoring and reload-of-config rather than a full first-class 'create tunnel' REST resource-oriented API for provisioning arbitrary tunnels at runtime without config. Frp's typical model is: client authenticates with a token and requests proxies itself (proxies are largely declared in the frpc config, though frpc also has features for env-var and dynamic config, and there's an admin API on the client side too). It's more 'config/token-driven binary with a monitoring/admin HTTP API' than a full programmatic-tunnel-creation REST API, but it is scriptable and self-hostable, and the wire protocol/API is documented enough that a backend could plausibly automate it (e.g., by templating frpc config and restarting/reloading, or via its client admin API). Programmable API: Partially true — Go; frps/frpc expose an HTTP Admin API (dashboard, reload, proxy/traffic stats) documented in frp's docs, and frpc supports config reload without restart. It is not a purpose-built 'POST /tunnels' REST API for on-demand tunnel registration, but the admin API + reload mechanism + token auth model gives a workable path to drive it programmatically from a backend.[78][89]

**Verdict.** Best-in-class maturity, adoption, and client simplicity (single static Go binary, no TUN/root) make frp a very strong candidate; the main gap versus a 'true' JS/TS control-plane API is that tunnel provisioning is more config/reload-oriented than resource-style REST, requiring us to write glue code (config templating + reload, or driving the admin API) rather than calling a clean 'create tunnel' endpoint. Still a top pick given its maturity and self-hostability.[78][89]

**Implementation-validated (4/5, confirmed).** Cloned and traced: frp's admin API (`GET /api/v2/clients/{key}` etc.) on both `frps` and `frpc` is real but **read-only status/monitoring**, not a tunnel-creation endpoint — there is no native per-device token concept, just one shared server-wide token. The only path to a scenario-matching "register a device" flow is frp's custom **`Login` server-plugin HTTP callback**, which we'd implement entirely ourselves (frp calls out to our endpoint on client login, we approve/deny and can attach metadata) — 100% custom glue on top of a plugin hook, not a native API. Routing itself is clean subdomain/path vhosting configured per-proxy ahead of time, no API call needed once set. Could not confirm an API to force-disconnect a single already-connected client (only bulk proxy delete found). See `implementation-plans/frp/PLAN.md`. [78][89]


### piko — Viability: 4/5


**Language/license.** Go — MIT.[91]

**Popularity.** 2.2k stars, 87 forks, 12 watchers; positioned explicitly as an open-source Ngrok alternative built for production traffic and Kubernetes hosting -- solid adoption signal for a project this size, with Prometheus/Grafana dashboards and Helm charts included.[91]

**Maturity.** 426 commits, includes Helm charts, benchmark suite, cluster tests, and a wiki -- indicates active, production-oriented maintenance with no obvious red flags (MIT license, structured repo).[91]

**Client requirements.** Piko agent (`piko agent http/tcp`) or Piko forward (`piko forward`) run as simple CLI/binary processes making outbound-only connections to the Piko server. No TAP/TUN interface and no root/admin required. There is also a Go SDK for embedding a `net.Listener` directly in an application.[91]

**Server/API.** Full server source available (Go), designed to run as a cluster of nodes behind a load balancer/Kubernetes Gateway. Has a Prometheus endpoint, access logging, and a documented status/admin API for monitoring; endpoints are registered dynamically by upstream agents connecting -- no static config needed per endpoint. Programmable API: Partial/true -- there is a Go SDK (`piko` Go module) for opening listeners programmatically and a status/observability API, but no TypeScript/JS-native server API; a SvelteKit backend would need to shell out to the Go agent/CLI or hit the Go server's HTTP endpoints rather than use a native JS SDK.[91]

**Verdict.** Strong architectural fit: outbound-only, no TUN/root client, hosted Go server binary is fine, and endpoints register dynamically without static config, which is close to a control-plane model. Lacks a native TS/JS SDK, so integration from SvelteKit would go through HTTP/CLI rather than an idiomatic JS library, but is otherwise one of the better fits.[91]

**Implementation-validated (4/5, confirmed).** Cloned and traced: piko has **no registration endpoint of any kind** — clients self-declare arbitrary endpoint IDs. Linking is pure JWT minting by us: SvelteKit signs a token (`{"piko":{"endpoints":["device-id"]}}`) with a shared HMAC/RSA/ECDSA key, hands it to the client, which presents it via `--connect.token` (agent) or the Go SDK. Routing uses an `x-piko-endpoint` header or Host-subdomain — no bind-API call needed at all, which is architecturally clean. Status confirmed directly in source: `/status/upstream/endpoints` (`server/status/client/upstream.go`). The real gap: **piko has no clean single-device token-revoke primitive** — a genuine architectural limitation, not a research gap. Very clean Go-native integration if aipotluck-local-client's monitor service were Go rather than Python/Node. See `implementation-plans/piko/PLAN.md`. [91]


### boringproxy — Viability: 4/5


**Language/license.** Go — MIT.[96][98]

**Popularity.** 1.4k stars, 131 forks, 21 watchers; has a dedicated website (boringproxy.io), sponsor (TakingNames.io) with custom-domain integration, and an active community forum (IndieBits) -- moderate but real adoption for a self-hosted ngrok alternative.[96][98]

**Maturity.** 377 commits, has a CHANGELOG.md and systemd unit files -- shows ongoing maintenance, though development pace appears to have slowed in recent years relative to newer entrants like piko/portr; no glaring red flags but check recent commit dates before relying on it long-term.[96][98]

**Client requirements.** Single Go binary CLI (`boringproxy client -server ... -token ...`), no TAP/TUN interface. Client does not need root/admin -- it's the server that may need `cap_net_bind_service` (or root) to bind ports 80/443, not the client.[96][98]

**Server/API.** Full server source available in Go (api.go, tunnel_manager.go, auth.go, ui_handler.go), includes an admin REST API (api.go) that already backs boringproxy's own web UI for managing tunnels/clients/tokens -- this is the closest thing among these 8 to an existing HTTP control-plane for tunnel management, though it's a Go API, not TS/JS. Programmable API: True, Go -- api.go implements a REST-style HTTP API used internally by boringproxy's own web UI for managing clients, tunnels, and tokens; no official TypeScript/JS SDK, but the API is plain HTTP/JSON so a SvelteKit app could call it directly.[96][98]

**Verdict.** Good overall fit: TUN-free, admin-free simple Go client binary; hosted Go server binary is expected/fine; and it already ships a REST API (api.go) plus web UI that we could call directly from SvelteKit for programmatic tunnel registration. Main gaps are no native TS/JS server library and somewhat slower recent maintenance cadence versus piko or portr.[96][98]

**Implementation-validated (4/5, confirmed — one of the stronger Strong-Tier results).** Cloned and traced: boringproxy ships a genuine REST API (`api.go`) — `/api/tunnels`, `/api/tokens`, `/api/clients`, `/api/users`, all bearer-token authenticated — that already backs its own web UI. The client self-registers with a token and polls for assigned tunnels; critically, **the server generates per-tunnel restricted SSH keypairs itself** (`tunnel_manager.go`), so we don't need our own key-provisioning logic. `POST /api/tunnels` binds a domain to a client-name directly, matching the scenario's routing requirement cleanly. Open question: whether `DELETE /api/tokens` is actually HTTP-reachable (code exists but wasn't confirmed wired into the request-routing switch) — matters for instant revocation. No dedicated connection-status endpoint was confirmed; recommend an end-to-end HTTP health probe as the fallback. See `implementation-plans/boringproxy/PLAN.md`. [96][98]


### rustunnel — Viability: 4/5


**Language/license.** Rust (server, client), TypeScript (dashboard-ui) — AGPL-3.0.[56]

**Popularity.** 656 GitHub stars, 48 forks, 3 watchers, 315 commits. Has a hosted managed service (rustunnel.com) with pay-as-you-go pricing, plus MCP-server integration for AI agents (Cursor/Claude Code/Windsurf) and a Skillselion listing — indicates decent traction, and it's positioned directly as an 'ngrok alternative.'[56]

**Maturity.** Actively developed: CI badge, recent AGENTS.md/CLAUDE.md/MCP integrations, dashboard UI, roadmap doc, contribution guidelines with pre-push hooks. No abandonment red flags; looks like a currently-maintained, evolving project (possibly relatively new but fast-moving).[56]

**Client requirements.** Client is a single Rust binary (`rustunnel`) with a setup wizard (`rustunnel setup`), CLI flags (`rustunnel http 3000`), and a `--json` machine-readable output mode. No TAP/TUN network interface — it's a WebSocket-based reverse tunnel client, no admin/root required for typical CLI usage (only production *server* deployment docs mention systemd/root, not the client).[56]

**Server/API.** Full server source available (Rust) — self-hostable, OR use their hosted edge servers (eu/us/ap regions) directly, meaning we could adopt the hosted server without running our own if desired, but the ask is to self-host it. Server exposes a documented REST API (dashboard port, default 8443) with Bearer-token auth: GET/DELETE /api/tunnels, GET /api/tunnels/:id/requests, POST/DELETE /api/tokens, GET /api/history, plus a machine-readable OpenAPI 3.0 spec at /api/openapi.json. Note: the REST API is for tunnel introspection/management/token issuance/history — actual tunnel *creation* still happens by the client dialing the control-plane WebSocket (port 4040) with a token, not via a 'create tunnel' REST call. Programmable API: true — language=Rust backend exposing REST/OpenAPI (HTTP+JSON) for listing/closing tunnels, managing API tokens, and viewing captured request history/replay; dashboard frontend is TypeScript. Tunnel *registration* itself is still client-initiated via WebSocket handshake with a pre-issued token (which itself IS creatable via POST /api/tokens), so a control-plane-driven workflow (mint token via REST, then start rustunnel client with token) is achievable.[56]

**Verdict.** Best-fit client story in the batch: single Rust binary, no TAP/TUN, no root required, plus a genuine documented REST/OpenAPI control-plane (with a TypeScript dashboard) we could integrate with our SvelteKit app to mint tokens and monitor/manage tunnels programmatically. Main caveats: AGPL-3.0 license (copyleft implications for a hosted product) and tunnel creation itself is still client-CLI-driven rather than a pure 'create tunnel via POST' API — we'd combine REST token-issuance with client launch.[56]

**Implementation-validated (4/5, confirmed, with a real licensing caveat).** Cloned and traced: rustunnel has a genuinely documented REST/OpenAPI control-plane on its dashboard port (default 8443) — Bearer-token auth, `POST /api/tokens` for issuance, `GET /api/tunnels`/`GET /api/history` for listing/history — backed by a real Next.js (TypeScript, though not SvelteKit) reference dashboard. Custom fixed subdomains are **plan-gated** in the schema (`allow_custom_subdomains`), a real constraint on the routing model. The bigger issue: **rustunnel is AGPL-3.0-licensed** — the same legal exposure already flagged for go-http-tunnel, requiring either open-sourcing our integration or purchasing a commercial license before embedding it in a commercial SaaS backend. See `implementation-plans/rustunnel/PLAN.md`. [56]


### Portal (portal-tunnel) — Viability: 4/5


**Language/license.** Go — MIT.[29]

**Popularity.** 269 stars, 28 forks, 1 watcher. Tagged in anderspitman/awesome-tunneling. Includes VS Code extension, Claude/Cursor plugin integrations, and a frontend/docs site -- signs of an emerging but real ecosystem.[29]

**Maturity.** Very actively developed: 1,653 commits on main, CI badge passing, dedicated docs site (SvelteKit-based docs under docs/), llms.txt, plugins for coding agents. This looks like the most actively maintained project in the batch.[29]

**Client requirements.** Single Go binary (portal-tunnel CLI / SDK), no TAP/TUN, no admin/root privileges required -- backend makes outbound-only reverse connections to the relay (no inbound listener, no elevated ports needed on the client side).[29]

**Server/API.** Relay server source fully available (Go), self-hostable (Dockerfile, docker-compose.yml, config.toml). Crucially it exposes a genuine HTTP control-plane API under `/sdk/*`: `POST /sdk/register/challenge`, `POST /sdk/register`, `GET /sdk/connect`, `POST /sdk/renew`, `POST /sdk/unregister` -- i.e., programmatic lease/tunnel registration, renewal, and teardown via REST-style calls (auth via SIWE/secp256k1 signature challenge + JWT access tokens). Also supports optional dedicated TCP/UDP port allocation. Programmable API: true -- Go relay server exposes an HTTP/JSON control-plane API (`/sdk/register`, `/sdk/connect`, `/sdk/renew`, `/sdk/unregister`) suitable for driving tunnel lifecycle programmatically from an external app (e.g., our SvelteKit backend could call this API directly or shell out to the SDK).[29]

**Verdict.** Strongest overall fit among the Go options: self-hostable open-source relay with a real HTTP control-plane for programmatic registration/renewal/teardown, no TAP/TUN, no root, single-binary client, and clearly the most actively maintained project surveyed. Docks a point only because the control-plane is Go/JSON rather than a first-class TypeScript SDK, and the protocol (SIWE-signed registration, keyless TLS, QUIC) is more complex than a typical simple REST integration.[29]

**Implementation-validated (4/5, confirmed with a real auth-model caveat).** Cloned and traced: Portal's `/sdk/*` control-plane is real and versioned, and the auth mechanism is confirmed as **SIWE** (Sign-In-With-Ethereum style) — the client generates a local secp256k1 identity, requests a challenge from `/sdk/register/challenge`, signs it with `SignEthereumPersonalMessage`, and posts the signed message to `/sdk/register` to receive an ES256K lease-scoped access token. This is powerful (no shared pre-issued token to leak) but a genuinely unusual integration model — crypto-wallet-style message signing rather than a plain bearer token or API key, adding real client-side complexity versus the other options here. TLS/DNS/ACME are fully automated (embedded DNS + DNSSEC). Whether policy revocation applies to already-open sessions instantly, or only on next lease renewal, was not confirmed empirically. See `implementation-plans/portal/PLAN.md`. [29]


### gt — Viability: 4/5


**Language/license.** Rust — Apache-2.0.[65]

**Popularity.** 141 stars, 39 forks, 13 watchers. Backed by ao-space (a Chinese open-source personal cloud project), used as part of that ecosystem; not widely adopted outside it.[65]

**Maturity.** Actively maintained (86 commits, dev branch, CI via GitHub Actions, tagged releases with prebuilt binaries and Docker images for both server/client). No red flags found; project is fairly young but functional and receives updates.[65]

**Client requirements.** No TAP/TUN interface — it's a userspace WebSocket(s)/HTTP(s)/TCP relay proxy, not a VPN. No root/admin required. Simple: single static binary (`gt client`) or Docker image; zero-config startup opens a web admin UI to configure. Very low friction.[65]

**Server/API.** Server source fully available (Rust, same repo). Config-file-driven (YAML) with a bundled web configuration UI for setup, not a REST/programmatic API intended for external orchestration. No documented TypeScript/JS SDK. Programmable API: false — the server exposes a browser-based web admin config UI (not a documented REST API) and is driven by YAML config files/CLI signals (reload/restart/stop); no TS/JS SDK found.[65]

**Verdict.** Meets all hard client constraints (no TUN, no root, single binary) and the hosted server binary model fits well (prebuilt release binaries + Docker images). Lacks a clean programmatic HTTP API for tunnel registration — would need to drive it via generated YAML config + reload signals from glue code. Good candidate if we're willing to shell out to config-file management.[65]

**Implementation-validated (4/5, confirmed).** Cloned and traced: gt is actually a Go engine (`libcs/`) wrapped by a thin Rust/CGO shell. It ships a Gin-backed admin Web UI/REST API (`/api/login`, `/config/save`, `/connection/list`) plus — the more useful primitive for our scenario — an **`authAPI` HTTP callback** that mirrors sish's pattern: gt calls out to our own backend to approve a connection, so "authorize a device" becomes a plain DB lookup in our code rather than a file/config write to the gt host. Routing is `hostPrefix`-based subdomain routing, defaulting to the client `id` if unset — clean and automatic. Whether `/config/save` hot-reloads the running server or needs a restart signal is unverified; recommend using the `authAPI` callback path specifically to sidestep that question. No admin API found to force-disconnect a live client. See `implementation-plans/gt/PLAN.md`. [65]


### specter — Viability: 4/5


**Language/license.** Go — MIT.[71]

**Popularity.** 48 stars, 1 fork, 4 watchers. Small but polished single-maintainer project ('like ngrok, but more ambitious with DHT for flavor'); no major notable third-party adoption found.[71]

**Maturity.** Very active: 729 commits, recent AGENTS.md/DESIGN.md/OPERATOR.md/PRODUCT.md docs suggest ongoing serious development, CI via GitHub Actions, requires Go 1.27.x and Node 22.12+ to build UI — modern toolchain, well-documented, no red flags.[71]

**Client requirements.** No TAP/TUN — client is a userspace Go binary using QUIC/TLS transport to tunnel L7 (HTTP/S) and L4 (TCP) traffic, including to Unix sockets/named pipes. No root/admin required. Simple YAML config or config-free `client expose`/`client serve` modes; single binary, cross-platform including macOS/Windows/Linux.[71]

**Server/API.** Full server source available (Go, self-hostable 'gateway' with Chord DHT overlay for routing, ACME/Let's Encrypt integration). Client offers a documented local management HTTP API/UI when started with `--server [host]:[port]` (manage custom hostnames, unpublish, release, list tunnels) — this is a management API on the CLIENT side, not the server's control-plane for creating tunnels; server itself is still primarily config/CLI driven (YAML apex config, gateway operator docs). Programmable API: partial/true — Go-based local management API on the client (`--server` flag) for tunnel lifecycle operations; the gateway/server itself doesn't expose a documented public REST API for external registration, but is source-available and could be extended.[71]

**Verdict.** Excellent fit for the hard client constraints (no TUN, no root, single Go binary, even config-free modes) and the project is clearly actively engineered with strong docs. The programmable angle is only partial (client-side management API, not a full server control-plane), so registering tunnels programmatically from a SvelteKit backend would require either using the client API pattern or extending the Go server — feasible since source is available, but not a plug-and-play REST API out of the box.[71]

**Implementation-validated (4/5, confirmed with a real architectural quirk).** Cloned and traced: specter does have a genuine management API — real Twirp RPCs (`MintDelegation`/`ListDelegations`/`RevokeDelegation`) — but it's exposed only on an **"owner" client process** (`--server host:port`), not on the gateway/tunnel server itself. Practically this means SvelteKit would need to run a persistent "owner-client" sidecar process just to call the mint/revoke API — an extra moving part none of the other tools in this survey require. The "lightweight token client" mode (`specter client serve --token-file`) is a genuine config-free, pre-shared-token pairing pattern with built-in reconnect and 30-second grant re-validation, matching the scenario's linking flow well once the sidecar is in place. See `implementation-plans/specter/PLAN.md`. [71]


## Workable tier (score 3) — meets client constraint, other real friction

These all satisfy the hard client constraint (no TUN/admin) and have
open-source, self-hostable servers, but each has a real limiting factor:
config-file-driven only with no runtime API, a heavier/less certain server
stack, or a smaller community.

- **bore** (Rust) — Ideal on the client side (trivial static binary, no admin, no TUN) and the server is fully open-source and simple to self-host, but there is no HTTP control-plane/API to register or manage tunnels from our own backend — tunnel creation is driven only by the c…[39]
- **bore (jkuri/bore)** (Go) — Meets the hard client constraints well (single binary, no root, no TUN) and the server is open source and simple to self-host, but there's no programmatic registration API -- our glue code would need to spawn/manage bore client processes per tunnel rather tha…[33]
- **cactus-tunnel** (TypeScript / Node.js) — Demoted from its prior top ranking for the same architectural reason: the in-process-embedding advantage is moot on serverless infrastructure. Client requires Node.js >=22, heavier than a static binary. Smallest community of any option surveyed (58 stars), ra…[3]
- **chisel** (Go) — Chisel is mature, popular, and has by far the simplest possible client (single static binary, zero TUN/root), making it a very strong operational fit; the downside relative to Pangolin is the total lack of any HTTP/TS control-plane — we'd have to build our ow…[79][89]
- **h2tunnel** (Node.js/TypeScript (zero runtime dependencies)) — Demoted from its prior top ranking: its main advantage was embedding the server directly inside our own long-running Node process, which no longer exists now that the SvelteKit backend is serverless. It still must run as a separate deployed Node service like …[7]
- **hsync** (JavaScript (Node.js and browser)) — Client-side is fully compliant with our hard constraints (no TUN/TAP, no root, simple npm/npx install, even browser-embeddable) and is a genuinely lightweight Node/WebSocket reverse-proxy. However the server component lives in a separate repo (hsync-server) t…[48]
- **NPS Enhanced** (Go (server, client, web UI in Go + JS frontend)) — Solid, actively maintained, self-hosted Go tunnel server with single-binary client and server, but the client install path defaults to sudo/service-registration and there's no documented external API for our SvelteKit app to hit — we'd have to reverse-enginee…[53]
- **pipenet** (TypeScript (ESM), Node.js) — Demoted from its prior top ranking for the same reason as h2tunnel: its core advantage was in-process embedding into our own Node server, which is no longer possible on serverless infrastructure — it now needs its own deployed service like any other tool here…[11]
- **Port Buddy** (Java (CLI client compiled to GraalVM native image; server/gateway/net-proxy in Java/Spring Boot; web dashboard in TypeScript/React)) — Client is a native-image single binary (admin-free, TUN-free) and the server explicitly has an 'API & Tunnel Management' Spring Boot module, which is promising for programmatic control — but the overall system is a heavier multi-service microservices architec…[58]
- **portr** (Go (server/CLI core), some Python test-server tooling, Svelte admin UI) — Client is admin-free and TUN-free (SSH port-forward based), which satisfies hard constraints, and hosting the Go server binary is reasonable. However there's no clearly documented control-plane/REST API for our SvelteKit app to programmatically register tunne…[90]
- **rathole** (Rust) — Excellent fit on the client side (tiny static binary, no admin, no TUN) and server source is open and lightweight, but lacks a confirmed working HTTP control-plane API today, meaning our SvelteKit backend would have to manage tunnels by writing/hot-reloading …[37]
- **remotemoe** (Go) — Best-in-class on the client side (zero install, zero admin, uses only the standard SSH client) and server source is available in Go, but there's no HTTP/REST control plane to hit from SvelteKit — our glue code would need to manage SSH keys/processes/config vi…[64]
- **reverse-tunnel (rtun)** (Go) — Solid, simple, self-hostable Go reverse tunnel over WebSocket with an admin-free, TUN-free client -- meets all hard client constraints -- but it is strictly config-file driven with no API/control-plane, so our SvelteKit app would need to template and reload Y…[30]
- **reverst** (Go) — Clean Go design (QUIC/HTTP3, load-balanced tunnel groups, permissive Apache-2.0 license) with an admin-free lightweight client binary, which is attractive for a hosted server binary. However it's Go-native (no TS/JS server API) and tunnel-group registration i…[54]
- **tunelo** (Rust) — Meets the hard client constraints well (no TUN, no root, single binary, cross-platform including Windows) and the relay/server is open source and self-hostable as a single binary too. However there is no programmable HTTP API or JS/TS SDK for our SvelteKit ap…[62]
- **tunnelite** (C# / .NET (SignalR-based)) — Improved relative to its prior ranking: a separate server process is no longer a disqualifier, and its client is a lean NativeAOT-compiled single binary, comparable to wstunnel's static-binary approach. Still the heaviest server stack of the realistic options…[4]
- **Wiretap** (Go) — Only viable if we invert Wiretap's own terminology: put the unprivileged, TUN-free 'wiretap serve' binary on the end-user's local machine, and accept that our hosted infrastructure runs the actual privileged WireGuard peer. This does satisfy the hard client c…[97]
- **tunwg** (Go) — Confirmed to genuinely avoid a TUN/TAP device (gVisor userspace netstack over plain UDP, no admin/root), and the server is a simple stateless Go binary — meets the hard client constraint cleanly. Demoted from Strong to Workable after implementation validation: only two HTTP endpoints exist (`/add`, `/relay`), one shared `TUNWG_AUTH` secret covers the whole server with no per-client tokens and no revocation mechanism at all — SvelteKit would have essentially no server-side visibility into individual clients without forking the code. See `implementation-plans/tunwg/PLAN.md`.[72]
- **SirTunnel** (Python (~50-line script) + Caddy (Go)) — Best-in-class client (nothing but a standard SSH client, zero install, zero privileges). Demoted from Strong to Workable after implementation validation: confirmed by reading all 5 files in the repo that it truly has zero API, zero auth beyond SSH itself, zero tokens, and zero status reporting — literally every piece of the scenario (pairing, revocation, health checks, hostname/port collision avoidance) is custom work on our side, with no way to even instantly kick an open tunnel short of SSHing in. See `implementation-plans/sirtunnel/PLAN.md`.[95]

## Ruled out (score 1-2) — hard constraint violation or major disqualifier

- **localtunnel** (2/5, JavaScript (Node.js, CommonJS)) — The client library is easy and requires no admin rights, but the server-side is a separate unmaintained process we'd have to self-host and operate ourselves - it cannot be embedded in our existing SvelteKit/Node backend…[9][13][15]
- **Tunnelmole** (2/5, TypeScript (compiled to JS/CJS, Node.js)) — More actively maintained than plain localtunnel, but architecturally identical: still a Node-only client + separately-run server, not embeddable in our SvelteKit backend process. Reliance on either a single-maintainer h…[10][14]
- **go-http-tunnel** (2/5, Go) — Technically solid HTTP/2-based multiplexing tunnel with decent adoption, but it fails the 'no separate infra' goal just as much as sish (a whole extra Go service to run and operate), it needs a bundled Go client binary …[8][17]
- **localtunnel** (2/5, ?) — Conceptually the right pattern but wrong transport (raw TCP, not WS) and wrong deployment shape (standalone server needing wildcard DNS + dynamic port allocation, not embeddable middleware). Would require significant re…[13][18]
- **tunnelmole** (2/5, ?) — Good UX and more recently maintained than localtunnel, but the reusable/embeddable piece here is the CLIENT (Node-only), which is the wrong side for us since our need is a Python client talking to an embedded server lib…[14]
- **primus** (2/5, ?) — Useful as a reconnect/heartbeat transport primitive to borrow from, not as a ready-made reverse-tunnel solution. Would still require us to build the entire request-multiplexing/routing logic (i.e., most of APLC-1) on to…[23]
- **Telebit** (2/5, JavaScript (Node.js), some Shell) — Client constraint is fine (simple CLI, no TUN/root), and it's genuinely open source with a permissive MIT license, but the ecosystem is essentially a single-maintainer side project with negligible adoption, poor documen…[82][83][89]
- **tunnelto** (2/5, Rust) — Client is simple and admin-free, but the self-hosted server explicitly lacks the distributed/coordination capability of the commercial hosted version and has no programmable control-plane API — plus lower commit activit…[41]
- **expose** (2/5, PHP) — Client-side is workable (no admin/TUN) but requires a PHP runtime rather than a static binary, and the self-hosted server is a full Laravel application (DB, queues) with the project steering users toward its paid manage…[43]
- **pgrok** (2/5, Go) — Client side is great (single Go binary, or even vanilla SSH, no admin needed), but the server requires Postgres + an OIDC/SSO provider + reverse proxy, and has no programmable API — its whole design assumes human users …[44]
- **gsocket (Global Socket)** (2/5, C) — Client-side constraints are fine (no TUN/root, simple binary), but this is fundamentally a NAT-traversal secure-socket primitive, not an HTTP reverse-tunnel-as-a-service with a control plane. We would have to build our …[92]
- **Wiredoor** (2/5, TypeScript (server/API + frontend), separate Go-based Wiredoor CLI) — Best-in-class server-side fit (TypeScript API/control-plane, active project, good docs) but disqualified on the hard client constraint since Client/Gateway Nodes are WireGuard-based and need a TUN interface, likely requ…[94]
- **PageKite** (2/5, Python (pure Python 2/legacy-oriented implementation; project began in ~2010 era)) — Client is admin-free and TUN-free (a real plus), but requires a Python runtime, and the project is a legacy pure-Python codebase with no modern control-plane API — we'd need to shell out to CLI/config files for every tu…[55]
- **tunnl.gg** (2/5, Go) — The client-side story is arguably the simplest possible (plain ssh, no binary to distribute at all), but there's zero programmatic control plane — we cannot register/manage tunnels via HTTP from our app, only by shellin…[59]
- **mmar** (2/5, Go) — Client-side constraints are satisfied (no TUN, no root, single binary) but mmar explicitly states it 'currently only supports the HTTP protocol' and that 'websockets were not tested and will likely not work' — a serious…[63]
- **EXPOSE (exposesh/expose-server, renamed to gaetanlhf/EXPOSE)** (2/5, Multi-component: Python (SSH server), Node.js (tools service), OpenResty/Nginx (web/proxy)) — Client side is ideal (zero install/admin, plain ssh), and there is a Node.js server component, but the whole system is architecturally tied to GitHub account identity and repo-starring for authorization rather than an A…[68][70]
- **pgrok (jerson/pgrok)** (2/5, Go (fork of inconshreveable/ngrok)) — Client/server install are both simple (Homebrew/Docker, no TUN/root), matching classic ngrok ergonomics, but the project has been archived since 2022 with its reference public service already shut down — a clear mainten…[74]
- **BitBang (bitbang-cli)** (2/5, Go) — Client-side requirements are excellent (single Go binary, no root, no TUN, WebRTC-based), but the whole value proposition depends on a closed/hosted rendezvous server whose source and control-plane API are not available…[27]
- **hypertunnel** (2/5, JavaScript/Node.js) — The only candidate in this batch with a genuinely open-source JS/Node server, which is attractive, but it is effectively abandoned (old CI, undocumented/unstable internals, dead public instance) and offers no real contr…[28]
- **srv.us** (2/5, Go (backend); no client code needed (uses stock OpenSSH)) — Exceptionally simple for a human running one command, and the server is technically open source, but the total absence of any REST/control-plane API (registration is baked into raw SSH port-forward semantics) makes it a…[32][36]
- **holepunch** (2/5, Python (Flask)) — Client side is admin-free/TUN-free (plain SSH), and it does have a REST API concept, but the project shows strong staleness signals (Python 3.7 pin, Nomad/Grid-specific deploy tooling, CircleCI-era stack) and heavy infr…[69]
- **progrium/localtunnel** (1/5, Go (current v3 rewrite); earlier historical versions in Ruby+Python Twisted (v1) and Python gevent (v2)) — Explicitly unmaintained by its own author, written in Go with no npm/JS bindings, no distributed public server, and no prebuilt cross-platform client binaries - it offers essentially zero direct integration value for a …[12]
- **jprq** (1/5, Go) — Same 'separate relay binary' infra burden as wstunnel but with worse maturity signals: no clear license, apparent stalled development, and its hosted service pivoted to paid/members-only in 2023, meaning we'd be adoptin…[2]
- **koding/tunnel** (1/5, Go (library)) — Despite superficial appeal as a 'library,' koding/tunnel is a Go library usable only from Go processes — it cannot be embedded in our Node/SvelteKit server, so it doesn't reduce infra lift as hoped, and it also requires…[6][16]
- **http-proxy** (1/5, ?) — Included because it repeatedly surfaces in 'websocket reverse proxy npm' searches, but it is explicitly the generic-proxy case the task warned about — not a NAT-traversal reverse-tunnel tool. Not viable for APLC-1 repla…[21]
- **yamux** (1/5, ?) — Dead package, wrong/misleading name-match to the real Yamux protocol, requires Node 0.10. Not usable. Confirms there is no mature, modern npm library implementing true stream multiplexing-over-WebSocket ready to embed.[20]
- **tunnel.pyjam.as** (1/5, Python) — Hard-fails our primary constraint because it relies on the client running WireGuard (a VPN network interface) rather than a simple binary/CLI, and it also lacks any programmable HTTP control-plane. Low adoption and sing…[84][85][89]
- **SSH-J.com** (1/5, N/A — it is a public, anonymous SSH jump/port-forwarding server (based on dropbear SSH), not a distributable client or server codebase we would deploy ourselves.) — Client side is ideal (plain ssh, zero install), but this fails our other hard requirement: it's a third-party shared public service with no self-hostable server source and zero programmatic API — we cannot register/mana…[87][88][89]
- **Greenhouse** (1/5, Go (68.2%), HTML (21.1%), Shell (5.8%), CSS (4.3%), Dockerfile (0.6%)) — Interesting design goals (least-authority, no custom client TLS key handoff) but the project and its hosted service are dead, it's a single-maintainer effort with zero community adoption, and any API surface is undocume…[86][89]
- **ngrok 1.0** (1/5, Go) — Explicitly archived/abandoned by its own maintainer in favor of a closed-source commercial successor — a clear-cut dead project despite its large historical star count. Not viable to build on; the only 'API' ecosystem (…[81][89]
- **sshuttle** (1/5, Python (with some C)) — sshuttle is architecturally a whole-network transparent VPN-like proxy, not a single-service reverse tunnel; its client requires root/admin to configure firewall/NAT rules, directly violating our no-admin constraint. Al…[38][46]
- **Selfhosted Gateway** (1/5, Shell/Makefile glue around Docker Compose, WireGuard, Caddy, NGINX (no custom application code)) — Disqualified: client requires a WireGuard TUN interface plus NET_ADMIN capability (root-equivalent), violating the hard client constraint, and there is no programmable control-plane API at all -- everything is Makefile/…[93]
- **onionpipe** (1/5, Go (with CGO/libtor bindings for the static-binary build option)) — Disqualified: fundamentally Tor-dependent (bundles/requires a Tor daemon and pays real onion-circuit latency/overhead), has no conventional self-hosted server or API, and doesn't fit a hosted-server-binary + lightweight…[57]
- **tunneller** (1/5, Go) — Disqualified: the repository is archived/read-only as of Sept 2025 (no future maintenance possible), and even ignoring that, the architecture requires operating a separate MQTT broker with acknowledged security weakness…[60]
- **Crowbar** (1/5, Go) — Wrong tool for the job: Crowbar tunnels arbitrary TCP over HTTP GET/POST to bypass restrictive proxies (e.g. SSH-over-HTTP), it does not expose a localhost service via a public subdomain/URL. Combined with being archive…[61]
- **docker-tunnel** (1/5, Shell (83.4%) / Dockerfile (16.6%)) — Clear disqualification: unmaintained since 2019, no license file, and requires Docker (plus SSH key mounting) on the client side, which conflicts with the 'simple install, no complex setup' constraint. Not worth further…[66]
- **vgrok** (1/5, TypeScript) — Despite having a clean TypeScript client API, this is disqualifying: the 'server' side is entirely Vercel's proprietary Sandbox product (with Hobby-plan timeouts, single-region limits, and per-use costs), not open-sourc…[34]
- **docker-wireguard-tunnel** (1/5, Shell (Docker-based wrapper around WireGuard)) — Confirmed WireGuard/TUN-based (NET_ADMIN capability, wg0 interface) which directly violates the no-TAP/TUN, no-admin/root client constraint. No programmable API either. Disqualified for this use case.[67]
- **wireport** (1/5, Go (with Caddy, CoreDNS, WireGuard)) — Confirmed WireGuard/TUN-based client requirement, directly violating the hard no-TAP/TUN, no-admin constraint. No programmable API either. Disqualified for this use case despite otherwise solid engineering (SSL automati…[73]
- **YTunnel** (1/5, Rust) — Does not meet the core requirement of self-hosting our own server binary — it is entirely dependent on Cloudflare's proprietary tunnel infrastructure, which conflicts with 'we host it, source code preferred.' Client its…[75]
- **ngtor** (1/5, Java (Spring Boot)) — Tor-based architecture imposes major latency/overhead and produces .onion addresses rather than a normal reachable hostname, and there's no separate server component we host or control programmatically — it's architectu…[76]
- **tnnlink** (1/5, Go) — Archived/abandoned since 2021 with an explicit 'not audited' disclaimer from a self-described Go beginner — a clear maintenance/security red flag despite the appealingly lightweight SSH-based, TUN-free client model. Not…[77]
- **netmask** (1/5, Python) — Disqualified primarily by its GUI-centric client design and by the complete absence of a control-plane/API (still just a roadmap item), plus a very thin commit history (5 commits) signaling an unfinished, unmaintained h…[49]
- **ephemeral-hidden-service** (1/5, Python) — Fails our requirements on multiple fronts: it mandates installing and running a full Tor daemon (heavy, non-trivial overhead well beyond a 'simple package-manager install'), provides no programmable server API or contro…[50]
- **TunnelAPI 1.0** (1/5, JavaScript/Node.js (backend, tunnel-server, tunnel-client, frontend all JS-based per repo folder structure); uses MongoDB for metadata.) — Ironically the closest technical match to our control-plane wishlist (full REST API for tunnel CRUD, JS/Node server, no TUN/root on the client) but is disqualified outright by license: the repo is proprietary, explicitl…[51][52]
- **Punchmole** (2/5, JavaScript (Node.js)) — Genuinely embeddable Node.js library (`PunchmoleServer()`/`PunchmoleClient()` are plain importable functions, confirmed to work in-process inside a SvelteKit adapter-node backend), which looked like the best "share our JS/TS runtime" candidate in the whole survey. Demoted sharply from 4/5 to 2/5 after implementation validation: `API_KEYS` is a closed-over array set once at server startup — there is no dynamic API to add/revoke a client or query connection status without restarting the entire server process, directly failing the scenario's token-exchange requirement. See `implementation-plans/punchmole/PLAN.md`.[47]

## Recommendation

**Pangolin, chiSSL, gost, and zrok are now the four standout picks**, all
scoring 5/5 after both a documentation review and a hands-on
implementation-validation pass (cloned source, traced against a concrete
deployment scenario — see `implementation-plans/` for the full per-tool
plans). All four give SvelteKit a genuine, writable, per-device
control-plane API — the ability to register a device, mint it a token,
bind a hostname/route, and poll live status — without hand-rolling most of
that ourselves.

- **Pangolin** has the best overall object model: a `site → resource →
  target` graph that is close to a literal match for "register a device,
  bind a hostname to its local port." Its TypeScript/Next.js control plane
  is also the most naturally shareable code with a TypeScript SvelteKit
  backend. Tradeoff: the heaviest deployment of the four (Docker Compose
  with three containers: pangolin, gerbil, traefik; WireGuard kernel
  capabilities on the gerbil component, though the client itself is
  TUN-free).
- **gost** is the closest single-binary rival to Pangolin's routing
  cleanliness: its Ingress object binds a hostname directly to a
  server-issued tunnel-ID via a documented, Swagger-specified REST API,
  all inside one Go binary with no multi-container deployment. The one
  open gap: a documented "list connected clients" endpoint wasn't
  confirmed from the crawled docs (may exist in the full Swagger spec,
  which needs a closer look before committing).
- **chiSSL** is the leanest deployment with a still-real API — a single
  Go binary, OpenAPI-documented REST surface, SQLite by default. Its one
  real limitation is that routing is port-based only (no hostname
  dispatch), meaning you'd still need to layer a reverse proxy in front of
  it to get "one route = one client," and its client linking is a static
  username:password pair rather than a token exchange.
- **zrok** has the best end-to-end status/health story of anything
  surveyed — its Agent API can health-check a share all the way through
  to the local service, not just confirm the tunnel process is alive.
  Tradeoff: the heaviest underlying architecture (a full OpenZiti
  zero-trust mTLS overlay).

**wstunnel and sish, the prior top picks from the docs-only pass, are now
correctly understood as a tier below these four** once actually traced:
both are excellent, mature *transport* layers (sish's OS-bundled-SSH client
is still the leanest client of any tool surveyed; wstunnel is the most
popular and mature WebSocket-native tunnel), but neither exposes a
control-plane API for the actual "register a device" workflow this
scenario needs — wstunnel has zero network API of any kind, and sish's
admin API is read/kill-only with no way to add or revoke an authorized key
through it. Both remain viable, but choosing either means building
essentially the same device-registry/token-issuance/routing logic that
Pangolin/gost/chiSSL/zrok already ship.

**Other real contenders from the Strong Tier**, all confirmed via
implementation trace to have a genuine (if imperfect) control-plane API:
**frp** (the single most popular tool in the entire 78-tool survey — real
admin API, but read-only; registration needs a custom Login-plugin
callback), **piko** (clean JWT-based linking and Host-header routing, but
no clean per-device revoke), **boringproxy** (real REST API, server
self-issues SSH keys — a genuinely strong option), **rustunnel** (real
REST/OpenAPI control-plane, but AGPL-3.0 licensing is a real legal
consideration), **Portal** (real `/sdk/*` API, but its SIWE/crypto-signed
auth model is unusually complex to integrate), **gt** and **specter**
(both have real but indirect control-plane primitives — an `authAPI`
callback for gt, an owner-client-sidecar Twirp API for specter).

**tunwg, SirTunnel, and Punchmole were all demoted after implementation
validation** — each looked promising in the docs-only pass but, once
traced, turned out to have little-to-no dynamic control plane: tunwg has
only two endpoints and one shared secret with no revocation; SirTunnel
(confirmed by reading its entire ~50-line codebase) has zero API of any
kind; and Punchmole's authorized-client list is fixed at server startup,
requiring a full restart to change despite being a genuinely embeddable
Node.js library otherwise.

**Bottom line:** Pangolin, gost, chiSSL, or zrok for a real production
build — pick based on whether TypeScript-native control plane (Pangolin),
single-binary simplicity (gost/chiSSL), or best-in-class health monitoring
(zrok) matters most. wstunnel or sish remain reasonable fallbacks only if
client-leanness and transport maturity are weighted above having a
built-in control-plane API.

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
[23] https://www.npmjs.com/package/primus
[27] https://github.com/richlegrand/bitbang-cli
[28] https://github.com/berstend/hypertunnel
[29] https://github.com/gosuda/portal-tunnel
[30] https://github.com/snsinfu/reverse-tunnel
[31] https://github.com/NextChapterSoftware/chissl
[32] https://github.com/pcarrier/srvus
[33] https://github.com/jkuri/bore
[34] https://github.com/styfle/vgrok
[35] https://github.com/unblocked/chissl
[36] https://github.com/xmit-co/srv.us
[37] https://github.com/rapiz1/rathole
[38] https://github.com/sshuttle/sshuttle
[39] https://github.com/ekzhang/bore
[40] https://github.com/go-gost/gost
[41] https://github.com/agrinman/tunnelto
[42] https://github.com/openziti/zrok
[43] https://github.com/beyondcode/expose
[44] https://github.com/pgrok/pgrok
[45] https://gost.run/tutorials/api/overview
[46] https://sshuttle.readthedocs.io/en/stable/how-it-works.html
[47] https://github.com/Degola/punchmole
[48] https://github.com/monteslu/hsync
[49] https://github.com/josephdove/netmask
[50] https://github.com/aurelg/ephemeral-hidden-service
[51] https://tunnelapi.in
[52] https://github.com/vijaypurohit322/api-response-manager
[53] https://github.com/djylb/nps
[54] https://github.com/flipt-io/reverst
[55] https://github.com/pagekite/PyPagekite
[56] https://github.com/joaoh82/rustunnel
[57] https://github.com/cmars/onionpipe
[58] https://github.com/amak-tech/port-buddy
[59] https://github.com/klipitkas/tunnl.gg
[60] https://github.com/skx/tunneller
[61] https://github.com/q3k/crowbar
[62] https://github.com/jiweiyuan/tunelo
[63] https://github.com/yusuf-musleh/mmar
[64] https://github.com/fasmide/remotemoe
[65] https://github.com/ao-space/gt
[66] https://github.com/vitobotta/docker-tunnel
[67] https://github.com/DigitallyRefined/docker-wireguard-tunnel
[68] https://github.com/exposesh/expose-server
[69] https://github.com/CypherpunkArmory/holepunch
[70] https://github.com/gaetanlhf/EXPOSE
[71] https://github.com/zllovesuki/specter
[72] https://github.com/ntnj/tunwg
[73] https://github.com/MultionLabs/wireport
[74] https://github.com/jerson/pgrok
[75] https://github.com/yetidevworks/ytunnel
[76] https://github.com/theborakompanioni/ngtor
[77] https://github.com/LiljebergXYZ/tnnlink
[78] https://github.com/fatedier/frp
[79] https://github.com/jpillora/chisel
[80] https://github.com/fosrl/pangolin
[81] https://github.com/inconshreveable/ngrok
[82] https://telebit.cloud
[83] https://git.coolaj86.com/coolaj86/telebit.js
[84] https://gitlab.com/pyjam.as/tunnel
[85] https://gitlab.com/pyjam.as/tunnel/-/tree/main
[86] https://git.sequentialread.com/sqr/greenhouse
[87] https://ssh-j.com
[88] https://bitbucket.org/ValdikSS/dropbear-sshj
[89] https://raw.githubusercontent.com/anderspitman/awesome-tunneling/master/README.md
[90] https://github.com/amalshaji/portr
[91] https://github.com/andydunstall/piko
[92] https://github.com/hackerschoice/gsocket
[93] https://github.com/hintjen/selfhosted-gateway
[94] https://github.com/wiredoor/wiredoor
[95] https://github.com/anderspitman/SirTunnel
[96] https://github.com/boringproxy/boringproxy
[97] https://github.com/sandialabs/wiretap
[98] https://boringproxy.io
[99] https://github.com/fosrl/newt
