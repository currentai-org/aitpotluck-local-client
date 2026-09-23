# Repository guide for coding agents

Guidance for coding agents working in this repository.

## Project overview

Cross-platform installer and local inference server wrapper: it downloads and supervises
`llama-server` (from [llama.cpp](https://github.com/ggml-org/llama.cpp)) as a background OS
service, and optionally pairs the machine to a managed tunnel endpoint (`newt`) so it can be
reached as a "managed" local inference device. `README.md` covers quick start, the public
one-line installer, the `aipotluck-local-client` CLI (login/logout/status), and the full repo
layout; `ARCHITECTURE.md` covers the design rationale. Read those before making structural
changes — this file only covers working conventions, not what the code does.

Everything lives under the `aipotluck` root package (`aipotluck.installer.*`,
`aipotluck.service.*`). There is no build step and no vendored third-party Python dependency: the
installer downloads prebuilt release binaries rather than building anything, which is also why
there's no pip-installed console script driving the CLI — see `cli_shim.py`'s own header comment
before reaching for `pip install .` as a "simpler" alternative to the PATH-shim it writes instead.

## Branching, committing, and pushing

This is currently a solo, no-remote project — every commit lands directly on `main`, and that's
fine for a change scoped the way most changes here are. The discipline that matters isn't which
branch a commit lands on, it's what's true before it lands:

- **A change is ready to commit once its tests pass, and — for anything a unit test can't
  reach — once it's been verified for real.** See "Tests and gates" below; a green `pytest` run is
  necessary, never sufficient, for the OS-service-registration and network-download paths in
  particular.
- **Commit as you go, without waiting to be asked.** There's a real test suite backing this repo
  now, so a passing commit is a cheap, checkable unit rather than a leap of faith — use that.
  Break the work into logical units (one coherent change, verified on its own) and commit each as
  it lands, rather than batching everything into one commit at the end of a session. A commit
  should be something a later `git bisect` or `git revert` could act on by itself: a rename plus
  every import it breaks is one unit; a genuinely unrelated doc fix picked up along the way is
  another. Stay tight, though — `git status` before a broad `git add`, and leave out whatever the
  session didn't actually touch (a pre-existing untracked file is never this commit's to pick up).
- **Commit messages follow `type(scope): summary`** — `feat`/`fix`/`docs`/`test`/`refactor`/`chore`,
  a lowercase area in parens (`installer`, `cli`, `service`, `tests`, ...), and a concise,
  why-not-what summary line, same shape as aipotluck.org's convention minus the ticket reference
  (there's no tracker here to reference). The body is 1-3 sentences on *why*, not a restatement of
  the diff — `git log` already shows what changed.
- **Never push, create a remote, or open a PR without explicit approval**, and treat that approval
  as scoped to the specific thing asked, not standing permission to push again later.
- **Never force-push, rewrite already-shared history, or delete a branch** without the user
  explicitly asking for that specific destructive action.
- For a non-trivial change (touches more than one file, changes a public CLI surface, changes
  what gets written to disk or registered with the OS), prefer working on a short-lived branch
  over committing straight to `main` — cheap to do, and it keeps `main` bisectable if something
  needs reverting. A one-line doc fix or comment update doesn't need one.
- **If a git hook is ever added** (pre-commit/pre-push — none exist today), give the `git`
  command a generous timeout rather than the default. A hook that runs the test suite or a real
  install smoke test can legitimately take longer than a short default timeout, and a killed hook
  reads exactly like a hang, not like a failure — don't let a tooling default make a passing check
  look broken.

## Tests and gates: name the contract, not the behavior

A green suite is evidence of conformance to *intent*, and only as good as whether the intent was
right, and whether the suite can actually fail. Four rules, kept general on purpose since this
project doesn't (yet) have a ticket tracker to hang incident numbers on — but each is grounded in
something that actually happened in this repo, not hypothetical.

1. **A check that cannot go red is not a check.** Before trusting a new test, injure the thing it
   guards and confirm it fails *for that reason*. This repo's own suite was built this way, not
   just asserted to work: `aipotluck/service/runner.py`'s login-gating condition was temporarily forced to
   `True` and `_StatusHandler._redacted_runtime_config`'s secret-masking was temporarily disabled,
   one at a time, and the corresponding test was confirmed to fail each time before either was
   trusted. A test that has never been watched to fail is a test whose failure mode nobody has
   actually seen.
2. **A test on a fail-open or silently-permissive path must say so in its name.** If a function
   returns `None`/an empty result/a default on a bad or missing input, the test's name should state
   that as the guarantee (`test_returns_none_when_binary_missing`, not `test_build_supervisor`) —
   if it passes forever, what does it actually promise, and is that the right promise? A vaguely
   named test on this kind of path is the one most likely to keep passing after the contract quietly
   inverts.
3. **A green unit suite is necessary, never sufficient, for anything a unit test can't actually
   reach.** This project's own suite mocks every OS-service-manager, network-download, and
   subprocess boundary on purpose (see each test file's own header comment) — that's what makes it
   fast and deterministic, and it also means passing tests are not proof the installer works on a
   real machine. The real OS-native service backends (systemd/launchd/Scheduled Task
   registration), and the real download+checksum+extract path, only get verified by actually
   running them: a real install, a real `systemctl`/`launchctl` status, a real paired device, a
   real model download and inference request — the same standard `README.md`'s "Tests" section
   and `ARCHITECTURE.md`'s verification notes hold today. Don't let "tests pass" stand in for that
   on a change to one of those paths.
4. **Never assert on the source text of the code under test** (grepping the implementation for a
   string, or otherwise checking how the code is written rather than what it does). It goes red on
   a harmless rename or refactor where nothing regressed, and stays green through the exact
   regression it was supposedly guarding, because the string is still there. Assert on real
   behavior instead — the actual file written, the actual return value, the actual process state —
   the way this repo's suite does throughout (e.g. `tests/test_cli_shim.py` reads the shim file
   it wrote back off disk and checks its contents, rather than checking that `install_cli_shim`
   contains a `write_text` call).

Run the suite with `pytest` from the repo root (`pip install -e ".[dev]"` first if `pytest` isn't
already available) — see `README.md`'s "Tests" section for what is and isn't covered.
