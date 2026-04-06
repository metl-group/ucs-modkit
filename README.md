# Used Cars Simulator Modkit

Complete toolkit for:
- Modmaker (texture export/edit)
- Runtime modloader (BepInEx + runtime overlay from `Mods/`)
- GUI frontend
- Optional direct patch + restore workflow

## Repository Layout

- `main` branch: core modkit (CLI, GUI, loader, runtime merge)
- `buildtools` branch: packaging/release build scripts

GitHub links:
- Main: `https://github.com/metl-group/ucs-modkit`
- Buildtools branch: `https://github.com/metl-group/ucs-modkit/tree/buildtools`

Quick clone:

```bash
git clone https://github.com/metl-group/ucs-modkit.git
cd ucs-modkit
```

## Setup

```bash
cd /path/to/ucs-modkit
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Optional (build C# loader plugin):

```bash
curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 8.0 --install-dir "$HOME/.local/share/dotnet"
```

## Game Path

```bash
GAME="/path/to/steamapps/common/Used Cars Simulator"
```

## Quick Start (Runtime Mods, Recommended)

1. Install loader (BepInEx + UCS plugin):

```bash
cd /path/to/ucs-modkit
. .venv/bin/activate
python ucs_modkit.py install-loader --game-dir "$GAME"
```

2. Set launch option (Linux/Proton only):

```text
WINEDLLOVERRIDES="winhttp=n,b" %command%
```

3. Export textures:

```bash
python ucs_modkit.py export --game-dir "$GAME" --mod my_first_mod --scope bundles
```

4. Edit PNGs in:

```text
$GAME/Mods/my_first_mod/textures/
```

5. Build runtime overrides:

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod my_first_mod
```

Default behavior includes both `.bundle` and `.assets` containers.
Use `--bundles-only` if you explicitly want bundle-only packaging.

6. Enable/disable and set priority:

```bash
python ucs_modkit.py set-mod --game-dir "$GAME" --mod my_first_mod --enabled true --priority 10
```

7. Rebuild modular runtime merge:

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME"
```

Windows: no launch option is required.

Generated merge mod output:

```text
$GAME/Mods/_runtime_merged/
```

## GUI

```bash
cd /path/to/ucs-modkit
./run_gui.sh
```

GUI controls:
- Export
- Package
- Loader build/install
- Mod enable/disable/priority
- Runtime merge rebuild/clean

## Distribution Builds (User-Friendly)

This toolkit can be packaged for non-technical users:
- Linux: AppImage (`UCS-Modkit-linux-x86_64.AppImage`)
- Windows: portable `.exe` bundle + `.zip`

Build scripts are maintained in the `buildtools` branch of this repository.
Compatibility wrappers remain in `packaging/` in this branch.

### Linux AppImage

```bash
git clone -b buildtools https://github.com/metl-group/ucs-modkit.git ucs-modkit-buildtools
cd ucs-modkit-buildtools
./build_linux_appimage.sh
```

Output:
- `dist/UCS-Modkit-linux-x86_64.AppImage`

### Windows .exe Bundle

Build on a Windows machine (PyInstaller cannot cross-compile from Linux):

```powershell
cd C:\path\to\ucs-modkit-buildtools
.\build_windows_release.ps1 -ModkitRoot C:\path\to\ucs-modkit
```

Outputs:
- `dist/UCS-Modkit-windows\ucs_modkit_gui.exe`
- `dist/UCS-Modkit-windows\ucs_modkit_cli.exe`
- `dist/UCS-Modkit-windows.zip`

Both packaged versions keep loader installation support through the GUI/CLI (`install-loader`), including Windows game installs (no Proton required).

Compatibility wrappers still exist under `packaging/`, but the canonical build entrypoint is the `buildtools` branch.

## Direct Patch Workflow (Without Runtime Loader)

1. Export:

```bash
cd /path/to/ucs-modkit
. .venv/bin/activate
python ucs_modkit.py export --game-dir "$GAME" --mod my_first_mod --scope all
```

2. Edit PNGs in:

```text
$GAME/Mods/my_first_mod/textures/
```

3. Apply mod directly:

```bash
python ucs_modkit.py apply --game-dir "$GAME" --mod my_first_mod
```

4. Restore originals:

```bash
python ucs_modkit.py restore --game-dir "$GAME"
```

## Important Commands

Scan textures:

```bash
python ucs_modkit.py scan --game-dir "$GAME" --scope all
```

Export only matching names (regex):

```bash
python ucs_modkit.py export --game-dir "$GAME" --mod ui_mod --name-filter "icon|thumbnail|brand"
```

Build runtime override package:

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod ui_mod
```

Bundle-only packaging:

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod ui_mod --bundles-only
```

Build runtime merge (all active mods):

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME"
```

Bundle-only merge:

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME" --bundles-only
```

Delete merge output:

```bash
python ucs_modkit.py clean-merged --game-dir "$GAME"
```

Apply all mods directly (legacy flow):

```bash
python ucs_modkit.py apply --game-dir "$GAME" --all
```

Show status:

```bash
python ucs_modkit.py status --game-dir "$GAME"
```

Show status as JSON:

```bash
python ucs_modkit.py status --game-dir "$GAME" --json
```

## Manifest-Only Redistributable Mods

After `package`, the tool writes runtime merge metadata into `manifest.json` (`runtime_overrides`).
That allows `merge-runtime` to merge texture-level changes even if `textures/` is removed.

Minimal redistributable mod structure:

```text
Mods/<mod_name>/
  mod.ini
  overrides.map
  manifest.json
  overrides/...
```

Notes:
- `manifest.json` must be the one produced/updated by this tool (contains `runtime_overrides`).
- If only `overrides.map + overrides` exist without runtime metadata, merge falls back to opaque bundle replacement for those entries.
- For additive merge across multiple mods touching the same bundle, keep `manifest.json` with runtime metadata.
- `.assets` overrides are now applied at runtime via a session overlay with automatic backup/restore.

## Notes

- Runtime mods live under `Mods/<mod>/` with:
  - `mod.ini`
  - `overrides.map`
  - `manifest.json`
  - `overrides/...`
- Merger generates a combined mod in `Mods/_runtime_merged`.
- Conflict rule: higher `priority` wins; conflicts are written to `merge_report.json`.
- `status` now reports runtime entry count when available (`runtime_overrides.entry_count`), not full export size.
- Backups for direct patch mode are under: `$GAME/Mods/.ucs_backups/`
- `apply` writes only changed PNGs by default (hash compare).
- `--force` re-applies all PNGs from the mod.
- `export --force` deletes existing exported PNGs in the mod folder.
