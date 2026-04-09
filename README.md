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

Default behavior:
- GUI is built as `onedir` (better AV compatibility than onefile).
- CLI stays onefile and is copied into the GUI folder for bundled execution.

Release output in `<modkit-root>\dist\UCS-Modkit-windows`:
- `ucs_modkit_gui\ucs_modkit_gui.exe`
- `ucs_modkit_gui\ucs_modkit_cli.exe`
- `ucs_modkit_cli.exe`
- `README.md`

Optional override:

```powershell
.\build_windows_release.ps1 -ModkitRoot C:\path\to\ucs-modkit -Layout onefile
```

### Windows VM + SMB Example

If your modkit lives on an SMB share:

```powershell
net use Z: \\server\share
cd Z:\path\to\ucs-modkit-buildtools
.\build_windows_release.ps1 -ModkitRoot Z:\path\to\ucs-modkit -PythonExe Z:\path\to\ucs-modkit\.venv\Scripts\python.exe
```

## Direct PyInstaller Build

```bash
python build_pyinstaller.py --target linux --modkit-root /path/to/ucs-modkit
```

Defaults:
- `dist`: `<modkit-root>/dist`
- `work`: `<buildtools>/build/pyinstaller`
- `spec`: `<buildtools>/spec`
