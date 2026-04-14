#!/usr/bin/env bash
set -euo pipefail

BUILDTOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
Usage: ./build_linux_appimage.sh

Builds UCS Modkit Linux binaries and wraps them into an AppImage.

Environment variables:
  UCS_MODKIT_ROOT     Path to ucs-modkit root (default: ../ucs-modkit)
  UCS_MODKIT_DIST_DIR Dist output directory (default: <modkit-root>/dist)
  UCS_MODKIT_PROFILE  Release profile: standard or lobotomized (default: standard)
  PYTHON_BIN          Python executable for PyInstaller
HELP
  exit 0
fi
if [[ -n "${UCS_MODKIT_ROOT:-}" ]]; then
  MODKIT_ROOT="$(cd "$UCS_MODKIT_ROOT" && pwd)"
elif [[ -d "$BUILDTOOLS_DIR/../ucs-modkit" ]]; then
  MODKIT_ROOT="$(cd "$BUILDTOOLS_DIR/../ucs-modkit" && pwd)"
else
  echo "Could not locate ucs-modkit. Set UCS_MODKIT_ROOT to the repo path." >&2
  exit 2
fi

DIST_DIR="${UCS_MODKIT_DIST_DIR:-$MODKIT_ROOT/dist}"
PROFILE="${UCS_MODKIT_PROFILE:-standard}"
if [[ "$PROFILE" != "standard" && "$PROFILE" != "lobotomized" ]]; then
  echo "Unsupported UCS_MODKIT_PROFILE: $PROFILE (expected: standard or lobotomized)" >&2
  exit 2
fi
APPDIR="$BUILDTOOLS_DIR/build/AppDir"
TOOLS_DIR="$BUILDTOOLS_DIR/tools"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  :
elif [[ -x "$MODKIT_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$MODKIT_ROOT/.venv/bin/python"
elif [[ -x "$MODKIT_ROOT/.venv/bin/python3" ]]; then
  PYTHON_BIN="$MODKIT_ROOT/.venv/bin/python3"
else
  PYTHON_BIN="python3"
fi

"$PYTHON_BIN" "$BUILDTOOLS_DIR/build_pyinstaller.py" \
  --target linux \
  --profile "$PROFILE" \
  --modkit-root "$MODKIT_ROOT" \
  --dist-dir "$DIST_DIR" \
  --work-dir "$BUILDTOOLS_DIR/build/pyinstaller" \
  --spec-dir "$BUILDTOOLS_DIR/spec"

if [[ "$PROFILE" == "lobotomized" ]]; then
  RELEASE_BASENAME="UCS-Modkit-linux-lobotomized"
else
  RELEASE_BASENAME="UCS-Modkit-linux"
fi
RELEASE_DIR="$DIST_DIR/$RELEASE_BASENAME"
GUI_BIN="$RELEASE_DIR/ucs_modkit_gui"
CLI_BIN="$RELEASE_DIR/ucs_modkit_cli"

if [[ ! -f "$GUI_BIN" || ! -f "$CLI_BIN" ]]; then
  echo "Expected binaries not found in $RELEASE_DIR" >&2
  exit 3
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"

cp "$GUI_BIN" "$APPDIR/usr/bin/ucs_modkit_gui"
cp "$CLI_BIN" "$APPDIR/usr/bin/ucs_modkit_cli"
cp "$RELEASE_DIR/README.md" "$APPDIR/usr/bin/README.md"
chmod +x "$APPDIR/usr/bin/ucs_modkit_gui" "$APPDIR/usr/bin/ucs_modkit_cli"

cat > "$APPDIR/AppRun" <<'APPEND'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/usr/bin/ucs_modkit_gui" "$@"
APPEND
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/UCS-Modkit.desktop" <<'APPEND'
[Desktop Entry]
Type=Application
Name=UCS Modkit Studio
Exec=ucs_modkit_gui
Icon=ucs-modkit
Categories=Game;Utility;
Terminal=false
APPEND

cat > "$APPDIR/ucs-modkit.xpm" <<'APPEND'
/* XPM */
static char *ucs_modkit_xpm[] = {
"32 32 3 1",
"  c #0F172A",
". c #22C55E",
"+ c #E2E8F0",
"                                ",
" ................................",
" .++++++++++++++++++++++++++++++.",
" .+                            +.",
" .+  U C S   M O D K I T       +.",
" .+                            +.",
" .+    ++++++      ++++++      +.",
" .+    +....+      +....+      +.",
" .+    +....+      +....+      +.",
" .+    +....+      +....+      +.",
" .+    ++++++      ++++++      +.",
" .+                            +.",
" .+    ++++++      ++++++      +.",
" .+    +....+      +....+      +.",
" .+    +....+      +....+      +.",
" .+    +....+      +....+      +.",
" .+    ++++++      ++++++      +.",
" .+                            +.",
" .+                            +.",
" .+                            +.",
" .+                            +.",
" .++++++++++++++++++++++++++++++.",
" ................................",
"                                ",
"                                ",
"                                ",
"                                ",
"                                ",
"                                ",
"                                ",
"                                "};
APPEND

APPIMAGETOOL_BIN=""
if command -v appimagetool >/dev/null 2>&1; then
  APPIMAGETOOL_BIN="$(command -v appimagetool)"
else
  APPIMAGETOOL_BIN="$TOOLS_DIR/appimagetool-x86_64.AppImage"
  if [[ ! -f "$APPIMAGETOOL_BIN" ]]; then
    mkdir -p "$TOOLS_DIR"
    curl -L "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" -o "$APPIMAGETOOL_BIN"
    chmod +x "$APPIMAGETOOL_BIN"
  fi
fi

OUT_APPIMAGE="$DIST_DIR/${RELEASE_BASENAME}-x86_64.AppImage"
rm -f "$OUT_APPIMAGE"

if [[ "$APPIMAGETOOL_BIN" == *.AppImage ]]; then
  APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$APPIMAGETOOL_BIN" "$APPDIR" "$OUT_APPIMAGE"
else
  ARCH=x86_64 "$APPIMAGETOOL_BIN" "$APPDIR" "$OUT_APPIMAGE"
fi

chmod +x "$OUT_APPIMAGE"
echo "AppImage built: $OUT_APPIMAGE"
