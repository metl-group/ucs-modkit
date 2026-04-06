# Used Cars Simulator Modkit

Komplett-Toolkit fuer:
- Modmaker (Texture Export/Edit)
- Runtime-Modloader (BepInEx + Bundle Overlay aus `Mods/`)
- GUI-Frontend
- Optionales direktes Patching + Restore

## Setup

```bash
cd /home/david/tools/ucs-modkit
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Optional (Build des C#-Loaders):

```bash
curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 8.0 --install-dir /home/david/.local/share/dotnet
```

## Game Path

```bash
GAME="/mnt/4TBN/SteamLibrary/steamapps/common/Used Cars Simulator"
```

## Schnellstart (Runtime-Mods, empfohlen)

1) Loader installieren (BepInEx + UCS Plugin):

```bash
cd /home/david/tools/ucs-modkit
. .venv/bin/activate
python ucs_modkit.py install-loader --game-dir "$GAME" --build
```

2) In Steam (Linux/Proton) Launch Option setzen:

```text
WINEDLLOVERRIDES="winhttp=n,b" %command%
```

3) Mod exportieren:

```bash
python ucs_modkit.py export --game-dir "$GAME" --mod my_first_mod --scope bundles
```

4) PNGs bearbeiten in:

```text
$GAME/Mods/my_first_mod/textures/
```

5) Runtime-Overrides bauen:

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod my_first_mod
```

6) Mod ein-/ausschalten:

```bash
python ucs_modkit.py set-mod --game-dir "$GAME" --mod my_first_mod --enabled true --priority 10
```

7) Modularen Runtime-Merge neu bauen:

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME"
```

Der Merge-Mod liegt dann in:

```text
$GAME/Mods/_runtime_merged/
```

## GUI

```bash
cd /home/david/tools/ucs-modkit
./run_gui.sh
```

Die GUI steuert:
- Export
- Package
- Loader Build/Install
- Mod Enable/Disable/Priority
- Runtime Merge Rebuild/Clean

## Direkter Patch-Workflow (ohne Runtime-Loader)

1) Export:

```bash
cd /home/david/tools/ucs-modkit
. .venv/bin/activate
python ucs_modkit.py export --game-dir "$GAME" --mod my_first_mod --scope all
```

2) PNGs bearbeiten in:

```text
$GAME/Mods/my_first_mod/textures/
```

3) Mod anwenden:

```bash
python ucs_modkit.py apply --game-dir "$GAME" --mod my_first_mod
```

4) Originale wiederherstellen:

```bash
python ucs_modkit.py restore --game-dir "$GAME"
```

## Wichtige Befehle

Texturen scannen:

```bash
python ucs_modkit.py scan --game-dir "$GAME" --scope all
```

Nur bestimmte Namen exportieren (Regex):

```bash
python ucs_modkit.py export --game-dir "$GAME" --mod ui_mod --name-filter "icon|thumbnail|brand"
```

Runtime Override Package erzeugen:

```bash
python ucs_modkit.py package --game-dir "$GAME" --mod ui_mod
```

Runtime Merge bauen (alle aktiven Mods):

```bash
python ucs_modkit.py merge-runtime --game-dir "$GAME"
```

Merge Output loeschen:

```bash
python ucs_modkit.py clean-merged --game-dir "$GAME"
```

Alle Mods direkt patchen (alt):

```bash
python ucs_modkit.py apply --game-dir "$GAME" --all
```

Status anzeigen:

```bash
python ucs_modkit.py status --game-dir "$GAME"
```

Status als JSON:

```bash
python ucs_modkit.py status --game-dir "$GAME" --json
```

## Hinweise

- Runtime-Mods liegen unter `Mods/<mod>/` mit:
  - `mod.ini`
  - `overrides.map`
  - `overrides/...`
- Der Merger erzeugt einen kombinierten Mod in `Mods/_runtime_merged`.
- Bei Bundle-Konflikten gilt: hoehere `priority` gewinnt; Konflikte landen in `merge_report.json`.
- Backups fuer direkten Patch liegen unter: `$GAME/Mods/.ucs_backups/`
- `apply` schreibt standardmaessig nur geaenderte PNGs (Hash-Vergleich).
- Mit `--force` werden alle PNGs aus dem Mod erneut geschrieben.
- `export --force` loescht vorhandene exportierte PNGs im Mod-Ordner.
