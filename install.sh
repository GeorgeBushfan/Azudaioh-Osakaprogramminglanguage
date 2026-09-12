#!/bin/sh
# install.sh — install the Osaka toolchain (no Python required).
#
# Usage:
#   ./install.sh              install to /usr/local (prompts for sudo if needed)
#   PREFIX=$HOME/.local ./install.sh   install somewhere else
#
# Installs:
#   <prefix>/bin/osakavm   the native VM (C, self-contained)
#   <prefix>/bin/osakac    the compiler driver (runs the seed compiler artifact)
#   <prefix>/bin/osaka     the runner (osaka run x.saka / osaka prog.sbc)
#   <prefix>/lib/osakac.stage2.sbc  the seed compiler artifact
set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${OSAKA_PREFIX:-/usr/local}"

# 1. Build the native VM if it is not already built.
if [ ! -x "$REPO/native/osakavm" ]; then
    echo "==> building native VM (make -C native)"
    make -C "$REPO/native" osakavm
fi

# 2. Pick an install prefix we can write to.
PREFIX="$PREFIX"
if [ ! -w "$(dirname "$PREFIX")" ] && [ "$PREFIX" = "/usr/local" ]; then
    if [ "$(id -u)" -ne 0 ]; then
        echo "install.sh: /usr/local is not writable; falling back to \$HOME/.local"
        echo "(set OSAKA_PREFIX to override; make sure \$HOME/.local/bin is on your PATH)"
        PREFIX="$HOME/.local"
    fi
fi

echo "==> installing to $PREFIX"
mkdir -p "$PREFIX/bin" "$PREFIX/lib"
cp "$REPO/native/osakavm" "$PREFIX/bin/osakavm"
cp "$REPO/bin/osaka" "$PREFIX/bin/osaka"
cp "$REPO/bin/osakac" "$PREFIX/bin/osakac"
cp "$REPO/bootstrap/osakac.stage2.sbc" "$PREFIX/lib/osakac.stage2.sbc"
chmod +x "$PREFIX/bin/osaka" "$PREFIX/bin/osakac" "$PREFIX/bin/osakavm"

echo "==> installed. Try it:"
echo "     osaka run examples/hello.saka"
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *) echo "     (add $PREFIX/bin to your PATH to use 'osaka' anywhere)" ;;
esac