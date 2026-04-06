# UCS Modkit Buildtools

This branch contains build and release tooling for `metl-group/ucs-modkit`.

Branch URL:
- `https://github.com/metl-group/ucs-modkit/tree/buildtools`

Core modkit branch:
- `https://github.com/metl-group/ucs-modkit` (`main`)

## Clone

```bash
git clone -b buildtools https://github.com/metl-group/ucs-modkit.git ucs-modkit-buildtools
cd ucs-modkit-buildtools
```

## Linux AppImage

```bash
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

### Windows VM + SMB Example

If your modkit lives on an SMB share:

```powershell
net use Z: \\tower.local\david
cd Z:\tools\ucs-modkit-buildtools
.\build_windows_release.ps1 -ModkitRoot Z:\tools\ucs-modkit -PythonExe Z:\tools\ucs-modkit\.venv\Scripts\python.exe
```

## Direct PyInstaller Build

```bash
python build_pyinstaller.py --target linux --modkit-root /path/to/ucs-modkit
```

Defaults:
- `dist`: `<modkit-root>/dist`
- `work`: `<buildtools>/build/pyinstaller`
- `spec`: `<buildtools>/spec`
