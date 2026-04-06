#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILDTOOLS_DIR="${UCS_MODKIT_BUILDTOOLS:-$ROOT_DIR/../ucs-modkit-buildtools}"
SCRIPT="$BUILDTOOLS_DIR/build_linux_appimage.sh"
if [[ ! -x "$SCRIPT" ]]; then
  echo "Buildtools script not found: $SCRIPT" >&2
  exit 2
fi
export UCS_MODKIT_ROOT="$ROOT_DIR"
exec "$SCRIPT" "$@"
