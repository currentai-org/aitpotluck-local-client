#!/usr/bin/env bash
# aipotluck-local-client -- one-line bootstrap installer (Linux & macOS)
#
# This is the script meant to be posted publicly and piped into bash, e.g.:
#
#   curl -fsSL https://raw.githubusercontent.com/<OWNER>/<REPO>/main/install.sh | bash
#
# It does the minimum possible on its own -- fetch the repo -- then hands
# off to the already-implemented, already-tested installer
# (installer/install.py) for everything else (llama.cpp download+verify,
# service registration, etc). No logic is duplicated here.
#
# Configurable via environment variables (all optional):
#   AIPOTLUCK_REPO_URL   git remote to clone (default: see below)
#   AIPOTLUCK_REF        branch/tag to check out (default: main)
#   AIPOTLUCK_SRC_DIR     where to clone the source (default: ~/.aipotluck/src)
#
# Any extra arguments are passed straight through to
# `python3 -m installer.install`, e.g.:
#
#   curl -fsSL .../install.sh | bash -s -- --backend cuda --model-hf "org/repo:Q4_K_M"
#
# STATUS: this script is a thin, tested-by-inspection wrapper. The
# heavy-lifting installer it calls (installer/install.py) is tested
# end-to-end on Linux; this bootstrap layer just needs git + a shell,
# and defers Python-presence checking to the installer itself, which
# raises a clear, actionable error if Python 3.9+ isn't found (see
# installer/python_bootstrap.py -- Linux/macOS get an apt/dnf/brew
# instruction rather than an automatic install, unlike the Windows path).

set -euo pipefail

# --- REPLACE THIS once the repo is hosted publicly ---
# e.g. https://github.com/your-org/aipotluck-local-client.git
DEFAULT_REPO_URL="https://github.com/REPLACE_ME/aipotluck-local-client.git"

REPO_URL="${AIPOTLUCK_REPO_URL:-$DEFAULT_REPO_URL}"
REF="${AIPOTLUCK_REF:-main}"
SRC_DIR="${AIPOTLUCK_SRC_DIR:-$HOME/.aipotluck/src}"

log() { printf '\033[1;34m[aipotluck]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[aipotluck] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "$REPO_URL" == *REPLACE_ME* ]]; then
    die "This script's default repo URL is still a placeholder. Set AIPOTLUCK_REPO_URL=<git-url> or edit DEFAULT_REPO_URL in this script before publishing/using it."
fi

command -v git >/dev/null 2>&1 || die "git is required but not found. Install git (e.g. 'sudo apt install git', 'sudo dnf install git', or 'xcode-select --install' on macOS) and re-run."

if [[ -d "$SRC_DIR/.git" ]]; then
    log "Existing checkout found at $SRC_DIR -- updating"
    git -C "$SRC_DIR" fetch --depth 1 origin "$REF"
    git -C "$SRC_DIR" checkout "$REF"
    git -C "$SRC_DIR" reset --hard "origin/$REF"
    git -C "$SRC_DIR" submodule update --init --recursive
else
    log "Cloning $REPO_URL (ref: $REF) into $SRC_DIR"
    mkdir -p "$(dirname "$SRC_DIR")"
    git clone --branch "$REF" --depth 1 --recurse-submodules "$REPO_URL" "$SRC_DIR"
fi

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
        die "No Python 3.9+ found. Install it with 'brew install python3' (https://brew.sh) or from https://www.python.org/downloads/macos/, then re-run this script."
    else
        die "No Python 3.9+ found. Install it with your distro's package manager (e.g. 'sudo apt install python3', 'sudo dnf install python3'), then re-run this script."
    fi
fi

log "Using source checkout: $SRC_DIR"
log "Handing off to: $PYTHON_BIN -m installer.install $*"
cd "$SRC_DIR"
exec "$PYTHON_BIN" -m installer.install "$@"
