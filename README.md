# UCS Modkit Buildtools

Build-specific tooling for packaging `/home/david/tools/ucs-modkit`.

## Linux AppImage

```bash
cd /home/david/tools/ucs-modkit-buildtools
./build_linux_appimage.sh
```

Optional environment variables:
- `UCS_MODKIT_ROOT` (default: sibling `../ucs-modkit`)
- `UCS_MODKIT_DIST_DIR` (default: `$UCS_MODKIT_ROOT/dist`)
- `PYTHON_BIN` (default: tries `$UCS_MODKIT_ROOT/.venv/bin/python` first)

## Windows Bundle

Run on Windows:

```powershell
cd C:\path\to\ucs-modkit-buildtools
.\build_windows_release.ps1 -ModkitRoot C:\path\to\ucs-modkit
```

Outputs are written into `<modkit-root>/dist`.

## Direct PyInstaller Build

```bash
python build_pyinstaller.py --target linux --modkit-root /home/david/tools/ucs-modkit
```

Defaults:
- `dist`: `<modkit-root>/dist`
- `work`: `<buildtools>/build/pyinstaller`
- `spec`: `<buildtools>/spec`
