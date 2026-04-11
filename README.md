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

Optional (small redistributable, archive-style delta mod):

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod my_first_mod --archive-deltas --archive-only --prune-archived
```

`--prune-archived` is non-destructive: it creates a flat `release/<mod>.zip` with:
- `manifest.json`
- `mod.ini`
- `overrides.map`
- only changed/delta `textures/*.png`

No nested ZIPs are included (NexusMods-friendly).
If `--archive-deltas` is not set, no `archives/` folder is created.

6. Enable/disable and set priority (global in `Mods/mods.ini`):

```bash
python ucs_modkit.py set-mod --game-dir "$GAME" --mod my_first_mod --enabled true --priority 10
```

`Mods/mods.ini` example:

```ini
mod.my_first_mod.enabled=true
mod.my_first_mod.priority=10
```

7. Rebuild modular runtime merge:

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME"
```

`merge-runtime` always rebuilds a clean output mod from scratch. Manual pre-clean is not required.

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
- Export 3D models (OBJ)
- Package
- Loader build/install
- Mod enable/disable/global priority (`Mods/mods.ini`)
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
- `dist/UCS-Modkit-windows\ucs_modkit_gui\ucs_modkit_gui.exe`
- `dist/UCS-Modkit-windows\ucs_modkit_gui\ucs_modkit_cli.exe`
- `dist/UCS-Modkit-windows\ucs_modkit_cli.exe`
- `dist/UCS-Modkit-windows.zip`

Note: Windows defaults to GUI `onedir` packaging (better AV compatibility).  
You can force onefile with `-Layout onefile`.

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

Scan 3D models:

```bash
python ucs_modkit.py scan-models --game-dir "$GAME" --scope all --output meshes.json
```

Export 3D models (OBJ) into a mod folder:

```bash
python ucs_modkit.py export-models --game-dir "$GAME" --mod model_lab --scope all
```

Export only matching names (regex):

```bash
python ucs_modkit.py export --game-dir "$GAME" --mod ui_mod --name-filter "icon|thumbnail|brand"
```

Build runtime override package:

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod ui_mod
```

Build archive-only delta mod (small upload size):

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod ui_mod --archive-deltas --archive-only --prune-archived
```

Note: `--archive-only` requires `--archive-deltas`.

Default alpha handling is `preserve` (recommended for character/clothing textures).
To use edited alpha as-is (advanced/risky), pass `--alpha-mode keep`.

Bundle-only packaging:

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod ui_mod --bundles-only
```

Force opaque alpha (debug/helper):

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod ui_mod --alpha-mode opaque
```

Build runtime merge (all active mods):

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME"
```

Bundle-only merge:

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME" --bundles-only
```

Use edited alpha during merge (advanced/risky):

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME" --alpha-mode keep
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

## Redistributable Mod Formats

Classic runtime override mod:

```text
Mods/<mod_name>/
  mod.ini
  overrides.map
  overrides/...
  manifest.json
```

Flat release ZIP mod (recommended for upload/sharing):

```text
Mods/<mod_name>.zip
  manifest.json
  mod.ini
  overrides.map
  textures/*.png
```

Optional folder-based delta mod (advanced/internal):

```text
Mods/<mod_name>/
  manifest.json
  archives/delta_textures.zip
  optional mod.ini
```

Notes:
- Flat ZIP mods are read directly by `merge-runtime` (no extraction required).
- `merge-runtime` can merge folder mods and `.zip` mods together.
- Global `enabled` / `priority` are controlled in `Mods/mods.ini`, not inside each mod.
- Multiple mods can touch the same bundle; conflict resolution still uses priority (higher wins).

## NPC Transparency Notes

- Many NPC clothing/body textures are shared atlases used by multiple NPCs.
- If alpha is zeroed, shaders can hide whole meshes (can look like all NPCs disappear).
- Modkit defaults to `--alpha-mode preserve` to keep original alpha while applying RGB edits.
- Use `--alpha-mode keep` only when you intentionally want alpha changes and have tested the result.

Suggested clothing test loop:
1. Build with `--alpha-mode preserve`.
2. Run in-game and verify body remains visible for all NPC variants.
3. Only then experiment with `--alpha-mode keep` on selected assets.

## Notes

- Runtime mods live under `Mods/<mod>/` with:
  - `manifest.json`
  - optional `mod.ini`
  - either `overrides.map + overrides/...` or `archives/*.zip`
- ZIP mods can be dropped directly as `Mods/<mod>.zip` (manifest/mod.ini/overrides.map/textures).
- Global mod settings live in: `Mods/mods.ini`
- Merger generates a combined mod in `Mods/_runtime_merged`.
- Conflict rule: higher `priority` wins; conflicts are written to `merge_report.json`.
- `status` now reports runtime entry count when available (`runtime_overrides.entry_count`), not full export size.
- Backups for direct patch mode are under: `$GAME/Mods/.ucs_backups/`
- `apply` writes only changed PNGs by default (hash compare).
- `--force` re-applies all PNGs from the mod.
- `export --force` deletes existing exported PNGs in the mod folder.
