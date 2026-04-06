#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$ROOT_DIR/modloader/Ucs.AddressablesOverlayLoader/Ucs.AddressablesOverlayLoader.csproj"
DOTNET_BIN="${DOTNET_BIN:-}"
if [[ -z "$DOTNET_BIN" ]]; then
  if [[ -x "$HOME/.local/share/dotnet/dotnet" ]]; then
    DOTNET_BIN="$HOME/.local/share/dotnet/dotnet"
  elif [[ -x "/home/david/.local/share/dotnet/dotnet" ]]; then
    DOTNET_BIN="/home/david/.local/share/dotnet/dotnet"
  else
    DOTNET_BIN="dotnet"
  fi
fi

if [[ ! -x "$DOTNET_BIN" ]]; then
  echo "dotnet not found at: $DOTNET_BIN" >&2
  echo "Install with: https://dot.net/v1/dotnet-install.sh" >&2
  exit 2
fi

GAME_DIR="${1:-/mnt/4TBN/SteamLibrary/steamapps/common/Used Cars Simulator}"
GAME_MANAGED="$GAME_DIR/Used Cars Simulator_Data/Managed"
if [[ ! -d "$GAME_MANAGED" ]]; then
  echo "Game Managed dir not found: $GAME_MANAGED" >&2
  exit 2
fi

echo "[build] Game Managed: $GAME_MANAGED"
"$DOTNET_BIN" build "$PROJECT" -c Release /p:GameManagedDir="$GAME_MANAGED"

OUT_DLL="$ROOT_DIR/modloader/Ucs.AddressablesOverlayLoader/bin/Release/net472/Ucs.AddressablesOverlayLoader.dll"
if [[ ! -f "$OUT_DLL" ]]; then
  echo "Build done, but DLL missing: $OUT_DLL" >&2
  exit 3
fi

echo "[build] OK: $OUT_DLL"
