#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
#
# Set WEFT up on this machine: prerequisites, container image, the package,
# and a starter configuration.
#
# It does not install Quartus and never will. Quartus is yours to install and
# licence; this script finds it if it is there and says so if it is not.
#
#   ./setup.sh                      everything, into ~/.config/weft/weft.toml
#   ./setup.sh --workspace ~/fpga   put the sandbox somewhere specific
#   ./setup.sh --no-image           skip the container build (it is the slow part)
#   ./setup.sh --no-install         skip installing the Python package
#   ./setup.sh --check              report what is present and change nothing

set -euo pipefail

IMAGE=weft-tools
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/weft/weft.toml"
WORKSPACE=""
BUILD_IMAGE=1
INSTALL_PACKAGE=1
CHECK_ONLY=0

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --workspace) WORKSPACE="${2:?--workspace needs a path}"; shift 2 ;;
        --config)    CONFIG="${2:?--config needs a path}"; shift 2 ;;
        --no-image)  BUILD_IMAGE=0; shift ;;
        --no-install) INSTALL_PACKAGE=0; shift ;;
        --check)     CHECK_ONLY=1; shift ;;
        -h|--help)   usage 0 ;;
        *)           warn "unknown argument: $1"; usage 2 ;;
    esac
done

cd "$(dirname "$0")"

# ---------------------------------------------------------------- prerequisites

python_ok() {
    command -v "$1" >/dev/null 2>&1 || return 1
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if python_ok "$candidate"; then echo "$candidate"; return 0; fi
    done
    return 1
}

say "== checking what is here =="

PYTHON=$(find_python) || die "no Python 3.11 or newer found. Ubuntu 22.04 ships 3.10; use 24.04, or install one with uv."
echo "python        $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

if command -v podman >/dev/null 2>&1; then
    echo "podman        $(podman --version 2>/dev/null | head -1)"
else
    warn "podman        NOT FOUND -- lint and simulate need it"
    warn "              Arch:   sudo pacman -S --needed podman"
    warn "              Ubuntu: sudo apt install podman uidmap"
fi

# Quartus and Questa are found, never installed. The paths below are where
# Intel's own installer puts them; anything else goes in the config by hand.
QUARTUS_ROOT=""
QUESTA_ROOT=""
for base in "$HOME/altera_lite" "$HOME/intelFPGA_lite" "$HOME/altera" "$HOME/intelFPGA" /opt/altera /opt/intelFPGA_lite /opt/intelFPGA_pro; do
    [ -d "$base" ] || continue
    for version in "$base"/*/; do
        [ -x "$version/quartus/bin/quartus_sh" ] && QUARTUS_ROOT="${version}quartus"
        [ -x "$version/questa_fse/bin/vsim" ] && QUESTA_ROOT="${version}questa_fse"
    done
done

if [ -n "$QUARTUS_ROOT" ]; then
    echo "quartus       $QUARTUS_ROOT"
else
    warn "quartus       NOT FOUND -- lint and simulate work without it; compiling does not"
fi
[ -n "$QUESTA_ROOT" ] && echo "questa        $QUESTA_ROOT" \
    || echo "questa        not found (optional; the only way to simulate mixed Verilog/VHDL)"

if [ "$CHECK_ONLY" = 1 ]; then
    say "== --check given, changing nothing =="
    exit 0
fi

# ------------------------------------------------------------------- workspace

if [ -z "$WORKSPACE" ]; then
    WORKSPACE="$HOME/fpga"
    say "== workspace =="
    echo "No --workspace given, so using $WORKSPACE."
    echo "Nothing outside it can be read or written. Change it in $CONFIG later."
fi
mkdir -p "$WORKSPACE"
WORKSPACE=$(cd "$WORKSPACE" && pwd)

# --------------------------------------------------------------- container image

if [ "$BUILD_IMAGE" = 1 ] && command -v podman >/dev/null 2>&1; then
    if podman image exists "$IMAGE" 2>/dev/null; then
        say "== image $IMAGE already built, skipping =="
    else
        say "== building $IMAGE =="
        echo "GHDL is compiled from source, so this takes a while. It is also the"
        echo "only step here that needs the network."
        podman build -t "$IMAGE" -f containers/Containerfile.weft-tools . \
            || die "image build failed. Re-run with --no-image to set the rest up anyway."
    fi
fi

# ------------------------------------------------------------------- the package

if [ "$INSTALL_PACKAGE" = 1 ]; then
    say "== installing weft =="
    "$PYTHON" -m pip install --user . || die "pip install failed. In a venv: $PYTHON -m venv .venv && .venv/bin/pip install ."
fi

WEFT_BIN=$(command -v weft || echo "$HOME/.local/bin/weft")

# ---------------------------------------------------------------- configuration

if [ -e "$CONFIG" ]; then
    say "== $CONFIG exists, leaving it alone =="
else
    say "== writing $CONFIG =="
    mkdir -p "$(dirname "$CONFIG")"
    {
        echo "# Written by setup.sh. Every key is documented at"
        echo "# https://weft-mcp.readthedocs.io/en/main/configuration.html"
        echo
        echo "[workspace]"
        echo "# Nothing outside this directory can be read or written."
        echo "root = \"$WORKSPACE\""
        echo
        echo "[container]"
        echo "image = \"$IMAGE\""
        if [ -n "$QUARTUS_ROOT" ]; then
            echo
            echo "[quartus.lite]"
            echo "root = \"$QUARTUS_ROOT\""
        fi
        if [ -n "$QUESTA_ROOT" ]; then
            echo
            echo "[questa]"
            echo "root = \"$QUESTA_ROOT\""
            if [ -n "${SALT_LICENSE_SERVER:-}" ]; then
                echo "env = { SALT_LICENSE_SERVER = \"$SALT_LICENSE_SERVER\" }"
            else
                echo "# Questa reads its licence from SALT_LICENSE_SERVER, which a server"
                echo "# started outside your desktop session does not inherit. Set it here:"
                echo "# env = { SALT_LICENSE_SERVER = \";$HOME/.altera.quartus/questa_lic.dat\" }"
            fi
        fi
    } > "$CONFIG"
fi

# --------------------------------------------------------------------- next step

say "== done =="
echo
echo "Register it with Claude Code:"
echo
echo "    claude mcp add --scope user weft -- $WEFT_BIN --transport stdio --config $CONFIG"
echo
echo "Then check it connected, inside a session:  /mcp"
echo
echo "For Claude Desktop, add this to its config file (it does not inherit your"
echo "PATH, which is why the absolute path matters):"
echo
cat <<JSON
    {
      "mcpServers": {
        "weft": {
          "command": "$WEFT_BIN",
          "args": ["--transport", "stdio", "--config", "$CONFIG"]
        }
      }
    }
JSON
echo
[ -n "$QUARTUS_ROOT" ] || echo "No Quartus was found. lint and simulate work; compiling needs it."
