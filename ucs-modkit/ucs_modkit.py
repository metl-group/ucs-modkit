#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import UnityPy
    from PIL import Image
except ImportError as exc:
    print(
        "Fehlende Abhaengigkeit. Bitte installieren mit: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


MANIFEST_VERSION = 1
BACKUP_DIR_NAME = ".ucs_backups"


@dataclass
class TextureEntry:
    id: str
    container_file: str
    assets_file: str
    path_id: int
    name: str
    object_container: str | None
    width: int
    height: int
    export_path: str
    original_hash: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "texture"


def texture_id(container_rel: str, assets_file: str, path_id: int) -> str:
    key = f"{container_rel}|{assets_file}|{path_id}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def detect_data_dir(game_dir: Path) -> Path:
    candidates = [p for p in game_dir.iterdir() if p.is_dir() and p.name.endswith("_Data")]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"Kein *_Data Ordner in {game_dir}")
    names = ", ".join(sorted(p.name for p in candidates))
    raise RuntimeError(f"Mehrere *_Data Ordner gefunden: {names}")


def discover_container_files(game_dir: Path, scope: str) -> list[Path]:
    data_dir = detect_data_dir(game_dir)
    containers: list[Path] = []

    if scope in ("assets", "all"):
        containers.extend(sorted(data_dir.glob("*.assets")))

    if scope in ("bundles", "all"):
        bundle_root = data_dir / "StreamingAssets" / "aa" / "StandaloneWindows64"
        if bundle_root.exists():
            containers.extend(sorted(bundle_root.rglob("*.bundle")))

    return containers


def ensure_mod_dir(game_dir: Path, mod_name: str) -> Path:
    mod_dir = game_dir / "Mods" / mod_name
    mod_dir.mkdir(parents=True, exist_ok=True)
    return mod_dir


def create_manifest(
    manifest_path: Path,
    game_dir: Path,
    mod_name: str,
    scope: str,
    name_filter: str | None,
    entries: Iterable[TextureEntry],
) -> None:
    entries_list = [asdict(e) for e in entries]
    payload = {
        "tool": "ucs-modkit",
        "manifest_version": MANIFEST_VERSION,
        "created_at": utc_now_iso(),
        "game_dir": str(game_dir),
        "mod_name": mod_name,
        "scope": scope,
        "name_filter": name_filter,
        "entry_count": len(entries_list),
        "entries": entries_list,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def load_manifest(mod_dir: Path) -> dict:
    manifest_path = mod_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Kein manifest.json gefunden in {mod_dir}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("manifest_version") != MANIFEST_VERSION:
        raise RuntimeError(
            f"Unbekannte Manifest-Version {data.get('manifest_version')} (erwartet {MANIFEST_VERSION})"
        )
    return data


def read_ini(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def parse_overrides_map(mod_dir: Path, map_file: str = "overrides.map") -> dict[str, str]:
    path = mod_dir / map_file
    if not path.exists():
        return {}

    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|", 1)
        if len(parts) != 2:
            continue
        original = parts[0].strip()
        override_rel = parts[1].strip()
        if not original or not override_rel:
            continue
        out[original] = override_rel
    return out


def write_ini(path: Path, values: dict[str, str]) -> None:
    keys = ["name", "enabled", "priority", "map"]
    lines = []
    for key in keys:
        if key in values:
            lines.append(f"{key}={values[key]}")
    for key in sorted(k for k in values.keys() if k not in keys):
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    v = str(value).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def changed_entries_for_mod(mod_dir: Path, entries: list[dict], force: bool) -> list[dict]:
    changed: list[dict] = []
    for entry in entries:
        rel = entry["export_path"]
        mod_file = mod_dir / rel
        if not mod_file.exists():
            continue
        current_hash = sha256_file(mod_file)
        if force or current_hash != entry["original_hash"]:
            item = dict(entry)
            item["_mod_file"] = str(mod_file)
            changed.append(item)
    return changed


def is_bundle_container(container_rel: str) -> bool:
    return container_rel.lower().endswith(".bundle")


def copy_file_with_sidecars(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Quelle fehlt: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

    lower = source.name.lower()
    if lower.endswith(".assets"):
        for suffix in (".resS", ".resource"):
            src_sidecar = source.with_name(source.name + suffix)
            if src_sidecar.exists():
                dst_sidecar = target.with_name(target.name + suffix)
                dst_sidecar.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_sidecar, dst_sidecar)


def copy_container_with_sidecars(game_dir: Path, container_rel: str, target: Path) -> None:
    source = game_dir / container_rel
    copy_file_with_sidecars(source, target)


def patch_container_with_items(container_path: Path, patch_items: list[dict]) -> int:
    env = UnityPy.load(str(container_path))
    lookup = {}
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        lookup[(str(obj.assets_file.name), int(obj.path_id))] = obj

    patched_here = 0
    for item in patch_items:
        key = (item["assets_file"], int(item["path_id"]))
        obj = lookup.get(key)
        if obj is None:
            print(
                f"[warn] Texture nicht gefunden: {container_path.name} | {key[0]} | {key[1]}",
                file=sys.stderr,
            )
            continue
        mod_file = Path(item["_mod_file"])
        try:
            with Image.open(mod_file) as im:
                img = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im.copy()
            tex = obj.read()
            tex.image = img
            tex.save()
            patched_here += 1
        except Exception as exc:
            print(
                f"[warn] Patch fehlgeschlagen: {container_path.name} path_id={item['path_id']} ({exc})",
                file=sys.stderr,
            )

    if patched_here > 0:
        container_path.write_bytes(env.file.save())
    return patched_here


def name_matches(regex: re.Pattern[str] | None, *parts: str | None) -> bool:
    if regex is None:
        return True
    haystack = " ".join((p or "") for p in parts)
    return bool(regex.search(haystack))


def command_scan(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    containers = discover_container_files(game_dir, args.scope)
    regex = re.compile(args.name_filter, re.IGNORECASE) if args.name_filter else None

    entries: list[dict] = []
    processed = 0

    for i, container_file in enumerate(containers, start=1):
        print(f"[scan] {i}/{len(containers)} {container_file.name}", file=sys.stderr)
        try:
            env = UnityPy.load(str(container_file))
        except Exception as exc:
            print(f"[warn] Konnte {container_file} nicht laden: {exc}", file=sys.stderr)
            continue

        container_rel = container_file.relative_to(game_dir).as_posix()
        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue
            try:
                tex = obj.read()
            except Exception as exc:
                print(
                    f"[warn] Texture read fehlgeschlagen ({container_file.name}, path_id={obj.path_id}): {exc}",
                    file=sys.stderr,
                )
                continue

            name = tex.m_Name or Path(obj.container or "").stem or f"texture_{obj.path_id}"
            if not name_matches(regex, name, obj.container, container_rel):
                continue

            entries.append(
                {
                    "id": texture_id(container_rel, str(obj.assets_file.name), int(obj.path_id)),
                    "container_file": container_rel,
                    "assets_file": str(obj.assets_file.name),
                    "path_id": int(obj.path_id),
                    "name": name,
                    "object_container": obj.container,
                    "width": int(tex.m_Width),
                    "height": int(tex.m_Height),
                }
            )
            processed += 1
            if args.limit and processed >= args.limit:
                break

        if args.limit and processed >= args.limit:
            break

    print(f"Gefundene Texturen: {len(entries)}")
    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(entries, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"JSON geschrieben: {out_path}")

    return 0


def command_export(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mod_dir = ensure_mod_dir(game_dir, args.mod)
    textures_dir = mod_dir / "textures"
    manifest_path = mod_dir / "manifest.json"

    if textures_dir.exists() and any(textures_dir.rglob("*.png")) and not args.force:
        print(
            f"[error] {textures_dir} enthaelt bereits PNGs. Fuer Neu-Export mit --force ausfuehren.",
            file=sys.stderr,
        )
        return 2

    if args.force and textures_dir.exists():
        shutil.rmtree(textures_dir)
    textures_dir.mkdir(parents=True, exist_ok=True)

    containers = discover_container_files(game_dir, args.scope)
    regex = re.compile(args.name_filter, re.IGNORECASE) if args.name_filter else None
    entries: list[TextureEntry] = []
    exported = 0

    for i, container_file in enumerate(containers, start=1):
        print(f"[export] {i}/{len(containers)} {container_file.name}", file=sys.stderr)
        try:
            env = UnityPy.load(str(container_file))
        except Exception as exc:
            print(f"[warn] Konnte {container_file} nicht laden: {exc}", file=sys.stderr)
            continue

        container_rel = container_file.relative_to(game_dir).as_posix()
        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue

            try:
                tex = obj.read()
            except Exception as exc:
                print(
                    f"[warn] Texture read fehlgeschlagen ({container_file.name}, path_id={obj.path_id}): {exc}",
                    file=sys.stderr,
                )
                continue

            name = tex.m_Name or Path(obj.container or "").stem or f"texture_{obj.path_id}"
            if not name_matches(regex, name, obj.container, container_rel):
                continue

            tid = texture_id(container_rel, str(obj.assets_file.name), int(obj.path_id))
            file_name = f"{tid}__{slugify(name)[:80]}.png"
            export_rel = Path("textures") / file_name
            export_abs = mod_dir / export_rel
            export_abs.parent.mkdir(parents=True, exist_ok=True)

            try:
                tex.image.save(export_abs)
            except Exception as exc:
                print(
                    f"[warn] Export fehlgeschlagen ({container_file.name}, path_id={obj.path_id}): {exc}",
                    file=sys.stderr,
                )
                continue

            entry = TextureEntry(
                id=tid,
                container_file=container_rel,
                assets_file=str(obj.assets_file.name),
                path_id=int(obj.path_id),
                name=name,
                object_container=obj.container,
                width=int(tex.m_Width),
                height=int(tex.m_Height),
                export_path=export_rel.as_posix(),
                original_hash=sha256_file(export_abs),
            )
            entries.append(entry)
            exported += 1
            if args.limit and exported >= args.limit:
                break

        if args.limit and exported >= args.limit:
            break

    create_manifest(
        manifest_path=manifest_path,
        game_dir=game_dir,
        mod_name=args.mod,
        scope=args.scope,
        name_filter=args.name_filter,
        entries=entries,
    )

    print(f"Exportiert: {len(entries)} Texturen")
    print(f"Mod-Ordner: {mod_dir}")
    print(f"Manifest: {manifest_path}")
    return 0


def patch_single_mod(game_dir: Path, mod_dir: Path, force: bool) -> tuple[int, int]:
    manifest = load_manifest(mod_dir)
    entries = manifest.get("entries", [])

    to_patch = changed_entries_for_mod(mod_dir, entries, force)

    if not to_patch:
        print(f"[apply] {mod_dir.name}: keine geaenderten Dateien gefunden")
        return (0, 0)

    grouped: dict[str, list[dict]] = {}
    for item in to_patch:
        grouped.setdefault(item["container_file"], []).append(item)

    backup_root = game_dir / "Mods" / BACKUP_DIR_NAME
    files_changed = 0
    textures_changed = 0

    for container_rel, patch_items in grouped.items():
        target = game_dir / container_rel
        if not target.exists():
            print(f"[warn] Container fehlt: {target}", file=sys.stderr)
            continue

        backup_target = backup_root / container_rel
        if not backup_target.exists():
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_target)

        try:
            patched_here = patch_container_with_items(target, patch_items)
        except Exception as exc:
            print(f"[warn] Konnte {target} nicht patchen: {exc}", file=sys.stderr)
            continue

        if patched_here > 0:
            files_changed += 1
            textures_changed += patched_here
            print(f"[apply] {container_rel}: {patched_here} Texturen gepatcht")

    return (files_changed, textures_changed)


def find_mod_dirs(game_dir: Path) -> list[Path]:
    mods_root = game_dir / "Mods"
    if not mods_root.exists():
        return []
    mods: list[Path] = []
    for item in sorted(mods_root.iterdir()):
        if not item.is_dir():
            continue
        if item.name.startswith("."):
            continue
        if (item / "manifest.json").exists() or (item / "mod.ini").exists():
            mods.append(item)
    return mods


def command_apply(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()

    mod_dirs: list[Path]
    if args.all:
        mod_dirs = find_mod_dirs(game_dir)
        if not mod_dirs:
            print("[error] Keine Mods mit manifest.json gefunden.", file=sys.stderr)
            return 2
    else:
        if not args.mod:
            print("[error] Entweder --mod oder --all angeben.", file=sys.stderr)
            return 2
        mod_dirs = [game_dir / "Mods" / args.mod]

    total_files = 0
    total_textures = 0
    for mod_dir in mod_dirs:
        print(f"[apply] Mod: {mod_dir.name}")
        try:
            files_changed, textures_changed = patch_single_mod(game_dir, mod_dir, args.force)
        except Exception as exc:
            print(f"[error] Mod fehlgeschlagen ({mod_dir.name}): {exc}", file=sys.stderr)
            continue
        total_files += files_changed
        total_textures += textures_changed

    print(f"Geaenderte Container: {total_files}")
    print(f"Geaenderte Texturen: {total_textures}")
    return 0


def command_package(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mod_dir = game_dir / "Mods" / args.mod
    bundles_only = bool(args.bundles_only)
    if not mod_dir.exists():
        print(f"[error] Mod-Ordner nicht gefunden: {mod_dir}", file=sys.stderr)
        return 2

    manifest = load_manifest(mod_dir)
    entries = manifest.get("entries", [])
    to_patch = changed_entries_for_mod(mod_dir, entries, args.force)
    if not to_patch:
        print("[package] Keine geaenderten PNGs gefunden.")
        return 0

    grouped: dict[str, list[dict]] = {}
    for item in to_patch:
        grouped.setdefault(item["container_file"], []).append(item)

    packaged_containers = 0
    packaged_textures = 0
    skipped_non_bundle = 0
    map_lines: list[str] = []

    for container_rel, patch_items in grouped.items():
        if bundles_only and not is_bundle_container(container_rel):
            skipped_non_bundle += len(patch_items)
            continue

        override_rel = (Path("overrides") / Path(container_rel)).as_posix()
        override_abs = mod_dir / override_rel

        try:
            copy_container_with_sidecars(game_dir, container_rel, override_abs)
            patched_here = patch_container_with_items(override_abs, patch_items)
        except Exception as exc:
            print(f"[warn] Package fehlgeschlagen fuer {container_rel}: {exc}", file=sys.stderr)
            continue

        if patched_here > 0:
            packaged_containers += 1
            packaged_textures += patched_here
            map_lines.append(f"{container_rel}|{override_rel}")
            print(f"[package] {container_rel}: {patched_here} Texturen in Override gepackt")

    if packaged_containers == 0:
        print("[package] Keine Overrides gebaut.")
        if skipped_non_bundle > 0 and bundles_only:
            print(
                f"[hint] {skipped_non_bundle} geaenderte Texturen lagen in .assets Dateien. "
                "Fuer Runtime-Overlay werden standardmaessig nur .bundle verpackt.",
                file=sys.stderr,
            )
        return 0

    overrides_map_path = mod_dir / "overrides.map"
    overrides_map_path.write_text(
        "# original_container_rel|override_rel\n" + "\n".join(sorted(set(map_lines))) + "\n",
        encoding="utf-8",
    )

    mod_ini_path = mod_dir / "mod.ini"
    ini = read_ini(mod_ini_path)
    ini.setdefault("name", args.mod)
    ini.setdefault("enabled", "true")
    ini.setdefault("priority", str(args.priority))
    ini["map"] = "overrides.map"
    if args.enabled is not None:
        ini["enabled"] = "true" if args.enabled else "false"
    if args.priority is not None:
        ini["priority"] = str(args.priority)
    write_ini(mod_ini_path, ini)

    print(f"Overrides gebaut: {packaged_containers} Container / {packaged_textures} Texturen")
    print(f"Map: {overrides_map_path}")
    print(f"Mod Config: {mod_ini_path}")
    if skipped_non_bundle > 0 and bundles_only:
        print(
            f"Hinweis: {skipped_non_bundle} Texturen aus nicht-Bundle-Containern wurden uebersprungen.",
            file=sys.stderr,
        )
    return 0


def command_set_mod(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mod_dir = game_dir / "Mods" / args.mod
    mod_dir.mkdir(parents=True, exist_ok=True)
    mod_ini_path = mod_dir / "mod.ini"
    ini = read_ini(mod_ini_path)
    ini.setdefault("name", args.mod)
    ini.setdefault("map", "overrides.map")
    if args.enabled is not None:
        ini["enabled"] = "true" if args.enabled else "false"
    if args.priority is not None:
        ini["priority"] = str(args.priority)
    if "enabled" not in ini:
        ini["enabled"] = "true"
    if "priority" not in ini:
        ini["priority"] = "0"
    write_ini(mod_ini_path, ini)
    print(f"Mod aktualisiert: {mod_ini_path}")
    return 0


def command_install_loader(args: argparse.Namespace) -> int:
    root_dir = Path(__file__).resolve().parent
    game_dir = Path(args.game_dir).resolve()
    if not game_dir.exists():
        print(f"[error] Spielordner fehlt: {game_dir}", file=sys.stderr)
        return 2

    bepinex_src = root_dir / "third_party" / "bepinex" / "win_x64_5.4.23.5"
    if not bepinex_src.exists():
        print(f"[error] BepInEx Template fehlt: {bepinex_src}", file=sys.stderr)
        return 2

    if args.build:
        build_script = root_dir / "build_modloader.sh"
        if not build_script.exists():
            print(f"[error] Build-Skript fehlt: {build_script}", file=sys.stderr)
            return 2
        print("[loader] Baue Modloader Plugin ...")
        proc = subprocess.run([str(build_script), str(game_dir)])
        if proc.returncode != 0:
            return proc.returncode

    plugin_dll = (
        root_dir
        / "modloader"
        / "Ucs.AddressablesOverlayLoader"
        / "bin"
        / "Release"
        / "net472"
        / "Ucs.AddressablesOverlayLoader.dll"
    )
    if not plugin_dll.exists():
        print(f"[error] Plugin DLL fehlt: {plugin_dll}", file=sys.stderr)
        print("Bitte zuerst build_modloader.sh ausfuehren oder --build nutzen.", file=sys.stderr)
        return 2

    for file_name in [".doorstop_version", "doorstop_config.ini", "winhttp.dll"]:
        src = bepinex_src / file_name
        dst = game_dir / file_name
        if not src.exists():
            continue
        if dst.exists() and not args.force:
            pass
        else:
            shutil.copy2(src, dst)

    src_bepinex_dir = bepinex_src / "BepInEx"
    dst_bepinex_dir = game_dir / "BepInEx"
    shutil.copytree(src_bepinex_dir, dst_bepinex_dir, dirs_exist_ok=True)

    plugin_dir = dst_bepinex_dir / "plugins" / "UCS.AddressablesOverlayLoader"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plugin_dll, plugin_dir / plugin_dll.name)

    mods_dir = game_dir / "Mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    readme = mods_dir / "README_UCS_MODS.txt"
    if not readme.exists() or args.force:
        readme.write_text(
            "UCS Mods Folder\n"
            "==============\n"
            "Each mod in its own folder, e.g. Mods/MyMod/\n\n"
            "Required files for runtime bundle overrides:\n"
            "- mod.ini\n"
            "- overrides.map\n"
            "- overrides/... (modified bundle files)\n\n"
            "Use ucs_modkit.py package to create these files.\n",
            encoding="utf-8",
        )

    print(f"Modloader installiert in: {game_dir}")
    print(f"Plugin: {plugin_dir / plugin_dll.name}")
    print("Steam/Proton Launch Option (Linux): WINEDLLOVERRIDES=\"winhttp=n,b\" %command%")
    return 0


def command_merge_runtime(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mods_root = game_dir / "Mods"
    output_mod = args.output_mod
    if output_mod.startswith("."):
        print("[error] output-mod darf nicht mit '.' starten (wird vom Loader ignoriert).", file=sys.stderr)
        return 2

    output_dir = mods_root / output_mod
    output_overrides = output_dir / "overrides"
    output_map = output_dir / "overrides.map"
    output_ini = output_dir / "mod.ini"
    output_report = output_dir / "merge_report.json"

    if output_dir.exists() and not args.force_output:
        ini = read_ini(output_ini)
        if ini.get("generated_by", "") != "ucs_modkit_merge_runtime":
            print(
                f"[error] Output-Mod '{output_mod}' existiert und ist kein generierter Merge-Mod. "
                "Nutze --force-output, wenn du ihn ueberschreiben willst.",
                file=sys.stderr,
            )
            return 2

    all_mods = find_mod_dirs(game_dir)
    source_mods: list[dict] = []
    for mod_dir in all_mods:
        if mod_dir.name == output_mod:
            continue
        ini = read_ini(mod_dir / "mod.ini")
        enabled = parse_bool(ini.get("enabled"), True)
        if not args.include_disabled and not enabled:
            continue
        priority = parse_int(ini.get("priority"), 0)
        map_file = ini.get("map", "overrides.map")

        delta_entries: list[dict] = []
        has_manifest = (mod_dir / "manifest.json").exists()
        if has_manifest:
            try:
                manifest = load_manifest(mod_dir)
                delta_entries = changed_entries_for_mod(mod_dir, manifest.get("entries", []), args.force)
            except Exception as exc:
                print(f"[warn] Manifest lesen fehlgeschlagen ({mod_dir.name}): {exc}", file=sys.stderr)

        # Opaque bundle overrides (fallback for external mods without manifest/textures).
        opaque_map: dict[str, str] = {}
        if args.include_opaque_always:
            opaque_map = parse_overrides_map(mod_dir, map_file)
        elif not delta_entries:
            opaque_map = parse_overrides_map(mod_dir, map_file)

        if not delta_entries and not opaque_map:
            continue

        source_mods.append(
            {
                "name": mod_dir.name,
                "dir": mod_dir,
                "priority": priority,
                "enabled": enabled,
                "delta_entries": delta_entries,
                "opaque_map": opaque_map,
            }
        )

    source_mods.sort(key=lambda m: (m["priority"], m["name"].lower()))
    if not source_mods:
        print("[merge] Keine geeigneten Mods gefunden.")
        output_dir.mkdir(parents=True, exist_ok=True)
        if output_overrides.exists():
            shutil.rmtree(output_overrides)
        output_map.write_text("# original_container_rel|override_rel\n", encoding="utf-8")
        write_ini(
            output_ini,
            {
                "name": output_mod,
                "enabled": "true",
                "priority": str(args.priority),
                "map": "overrides.map",
                "generated_by": "ucs_modkit_merge_runtime",
                "generated_at": utc_now_iso(),
            },
        )
        output_report.write_text(
            json.dumps(
                {
                    "generated_at": utc_now_iso(),
                    "source_mods": [],
                    "containers_merged": 0,
                    "textures_merged": 0,
                    "conflicts": [],
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return 0

    plan: dict[str, dict[str, list[dict]]] = {}
    texture_mods: dict[tuple[str, str, int], list[str]] = {}

    for mod in source_mods:
        mod_name = mod["name"]
        mod_dir = mod["dir"]
        prio = mod["priority"]

        for item in mod["delta_entries"]:
            container_rel = item["container_file"]
            plan.setdefault(container_rel, {"baselines": [], "patches": []})
            planned = dict(item)
            planned["_mod_name"] = mod_name
            planned["_priority"] = prio
            plan[container_rel]["patches"].append(planned)

            k = (container_rel, item["assets_file"], int(item["path_id"]))
            texture_mods.setdefault(k, []).append(mod_name)

        for original_rel, override_rel in mod["opaque_map"].items():
            override_abs = (mod_dir / override_rel).resolve()
            if not override_abs.exists():
                continue
            plan.setdefault(original_rel, {"baselines": [], "patches": []})
            plan[original_rel]["baselines"].append(
                {
                    "mod": mod_name,
                    "priority": prio,
                    "override_abs": str(override_abs),
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    if output_overrides.exists():
        shutil.rmtree(output_overrides)
    output_overrides.mkdir(parents=True, exist_ok=True)

    temp_root = Path(tempfile.mkdtemp(prefix="ucs_merge_runtime_"))
    map_lines: list[str] = []

    merged_containers = 0
    merged_textures = 0
    skipped_non_bundle = 0
    opaque_conflicts: list[dict] = []

    try:
        for container_rel in sorted(plan.keys()):
            if args.bundles_only and not is_bundle_container(container_rel):
                skipped_non_bundle += 1
                continue

            entry = plan[container_rel]
            baselines = sorted(entry["baselines"], key=lambda b: (b["priority"], b["mod"].lower()))
            patches = sorted(entry["patches"], key=lambda p: (p["_priority"], p["_mod_name"].lower()))
            if not baselines and not patches:
                continue

            if len(baselines) > 1:
                opaque_conflicts.append(
                    {
                        "container": container_rel,
                        "mods": [b["mod"] for b in baselines],
                        "type": "opaque_bundle_conflict",
                    }
                )

            tmp_container = temp_root / container_rel
            source_container = game_dir / container_rel

            seeded_from_baseline = False
            if source_container.exists():
                copy_file_with_sidecars(source_container, tmp_container)
            elif baselines:
                copy_file_with_sidecars(Path(baselines[0]["override_abs"]), tmp_container)
                seeded_from_baseline = True
            else:
                print(f"[warn] Container fehlt und keine Baseline vorhanden: {container_rel}", file=sys.stderr)
                continue

            baseline_start_idx = 1 if seeded_from_baseline else 0
            for b in baselines[baseline_start_idx:]:
                copy_file_with_sidecars(Path(b["override_abs"]), tmp_container)

            patched_here = 0
            if patches:
                patched_here = patch_container_with_items(tmp_container, patches)

            if baselines or patched_here > 0:
                merged_out = output_overrides / container_rel
                copy_file_with_sidecars(tmp_container, merged_out)
                map_lines.append(f"{container_rel}|overrides/{container_rel}")
                merged_containers += 1
                merged_textures += patched_here
                print(
                    f"[merge] {container_rel}: baselines={len(baselines)} patched_textures={patched_here}"
                )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    output_map.write_text(
        "# original_container_rel|override_rel\n" + "\n".join(sorted(set(map_lines))) + "\n",
        encoding="utf-8",
    )

    write_ini(
        output_ini,
        {
            "name": output_mod,
            "enabled": "true",
            "priority": str(args.priority),
            "map": "overrides.map",
            "generated_by": "ucs_modkit_merge_runtime",
            "generated_at": utc_now_iso(),
        },
    )

    texture_conflicts = []
    for (container_rel, assets_file, path_id), mods in texture_mods.items():
        uniq = sorted(set(mods))
        if len(uniq) > 1:
            texture_conflicts.append(
                {
                    "container": container_rel,
                    "assets_file": assets_file,
                    "path_id": path_id,
                    "mods": uniq,
                    "type": "texture_conflict",
                }
            )

    report = {
        "generated_at": utc_now_iso(),
        "output_mod": output_mod,
        "output_priority": args.priority,
        "source_mods": [
            {
                "name": m["name"],
                "priority": m["priority"],
                "enabled": m["enabled"],
                "delta_entries": len(m["delta_entries"]),
                "opaque_entries": len(m["opaque_map"]),
            }
            for m in source_mods
        ],
        "containers_merged": merged_containers,
        "textures_merged": merged_textures,
        "conflicts": texture_conflicts + opaque_conflicts,
    }
    output_report.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    print(f"Merged output mod: {output_dir}")
    print(f"Containers merged: {merged_containers}")
    print(f"Textures merged: {merged_textures}")
    print(f"Conflict count: {len(report['conflicts'])}")
    print(f"Report: {output_report}")
    if skipped_non_bundle > 0 and args.bundles_only:
        print(
            f"Hinweis: {skipped_non_bundle} nicht-Bundle Container wurden uebersprungen.",
            file=sys.stderr,
        )
    return 0


def command_clean_merged(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    output_dir = game_dir / "Mods" / args.output_mod
    if not output_dir.exists():
        print("Merge-Output nicht vorhanden.")
        return 0

    ini = read_ini(output_dir / "mod.ini")
    if ini.get("generated_by", "") != "ucs_modkit_merge_runtime" and not args.force:
        print(
            "[error] Ziel ist kein generierter Merge-Mod. Mit --force trotzdem loeschen.",
            file=sys.stderr,
        )
        return 2
    shutil.rmtree(output_dir, ignore_errors=True)
    print(f"Merge-Output geloescht: {output_dir}")
    return 0


def command_restore(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    backup_root = game_dir / "Mods" / BACKUP_DIR_NAME
    if not backup_root.exists():
        print("Keine Backups gefunden.")
        return 0

    backups = [p for p in backup_root.rglob("*") if p.is_file()]
    if not backups:
        print("Keine Backups gefunden.")
        return 0

    restored = 0
    for backup in backups:
        rel = backup.relative_to(backup_root)
        target = game_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        restored += 1

    if args.purge_backups:
        shutil.rmtree(backup_root)
        print(f"Restore abgeschlossen ({restored} Dateien). Backups wurden geloescht.")
    else:
        print(f"Restore abgeschlossen ({restored} Dateien). Backups bleiben erhalten.")
    return 0


def command_status(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mods_root = game_dir / "Mods"
    backup_root = mods_root / BACKUP_DIR_NAME

    mod_dirs = find_mod_dirs(game_dir)
    backup_count = len([p for p in backup_root.rglob("*") if p.is_file()]) if backup_root.exists() else 0

    rows = []
    for mod in mod_dirs:
        count: int | None
        if (mod / "manifest.json").exists():
            try:
                data = load_manifest(mod)
                count = int(data.get("entry_count", len(data.get("entries", []))))
            except Exception:
                count = -1
        else:
            count = None
        ini = read_ini(mod / "mod.ini")
        rows.append(
            {
                "mod": mod.name,
                "entries": count,
                "enabled": parse_bool(ini.get("enabled"), True),
                "priority": parse_int(ini.get("priority"), 0),
                "has_overrides_map": (mod / "overrides.map").exists(),
            }
        )

    payload = {
        "game_dir": str(game_dir),
        "mods_count": len(mod_dirs),
        "backups_count": backup_count,
        "mods": rows,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0

    print(f"Game: {game_dir}")
    print(f"Mods gefunden: {len(mod_dirs)}")
    for row in rows:
        entries = row["entries"]
        if entries is None:
            entries_txt = "n/a"
        elif entries >= 0:
            entries_txt = str(entries)
        else:
            entries_txt = "manifest fehlerhaft"
        print(
            f"  - {row['mod']}: {entries_txt} Eintraege | enabled={row['enabled']} "
            f"| priority={row['priority']} | overrides.map={row['has_overrides_map']}"
        )
    print(f"Backups: {backup_count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ucs_modkit",
        description="Modmaker + Runtime Modloader Toolkit fuer Used Cars Simulator.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Texturen in den Containern auflisten")
    scan.add_argument("--game-dir", required=True, help="Spielordner")
    scan.add_argument("--scope", choices=("assets", "bundles", "all"), default="all")
    scan.add_argument("--name-filter", help="Regex-Filter fuer Name/Container")
    scan.add_argument("--limit", type=int, help="Maximale Anzahl Texturen")
    scan.add_argument("--output", help="Optionales JSON-Ausgabefile")
    scan.set_defaults(func=command_scan)

    export = sub.add_parser("export", help="Texturen als PNG in einen Mod-Ordner exportieren")
    export.add_argument("--game-dir", required=True, help="Spielordner")
    export.add_argument("--mod", required=True, help="Mod-Name, z.B. my-first-mod")
    export.add_argument("--scope", choices=("assets", "bundles", "all"), default="all")
    export.add_argument("--name-filter", help="Regex-Filter fuer Name/Container")
    export.add_argument("--limit", type=int, help="Maximale Anzahl Texturen")
    export.add_argument("--force", action="store_true", help="Bestehende PNGs im Mod-Ordner loeschen")
    export.set_defaults(func=command_export)

    apply_cmd = sub.add_parser("apply", help="Geaenderte PNGs in das Spiel zurueckpatchen")
    apply_cmd.add_argument("--game-dir", required=True, help="Spielordner")
    apply_cmd.add_argument("--mod", help="Mod-Name (wenn nicht --all)")
    apply_cmd.add_argument("--all", action="store_true", help="Alle Mods mit manifest.json anwenden")
    apply_cmd.add_argument("--force", action="store_true", help="Alle PNGs anwenden, nicht nur geaenderte")
    apply_cmd.set_defaults(func=command_apply)

    package_cmd = sub.add_parser(
        "package", help="Erzeuge Runtime-Overrides fuer den Modloader (ohne Originaldateien zu ueberschreiben)"
    )
    package_cmd.add_argument("--game-dir", required=True, help="Spielordner")
    package_cmd.add_argument("--mod", required=True, help="Mod-Name")
    package_cmd.add_argument("--force", action="store_true", help="Alle exportierten PNGs verarbeiten")
    package_cmd.add_argument("--include-assets", dest="bundles_only", action="store_false", help="Auch .assets Container zulassen")
    package_cmd.add_argument("--enabled", type=parse_bool, nargs="?", const=True, help="Mod enabled setzen")
    package_cmd.add_argument("--priority", type=int, default=0, help="Mod-Prioritaet")
    package_cmd.set_defaults(bundles_only=True)
    package_cmd.set_defaults(func=command_package)

    set_mod = sub.add_parser("set-mod", help="enabled/priority in mod.ini setzen")
    set_mod.add_argument("--game-dir", required=True, help="Spielordner")
    set_mod.add_argument("--mod", required=True, help="Mod-Name")
    set_mod.add_argument("--enabled", type=parse_bool, nargs="?", const=True, help="Enabled true/false")
    set_mod.add_argument("--priority", type=int, help="Prioritaet")
    set_mod.set_defaults(func=command_set_mod)

    install_loader = sub.add_parser("install-loader", help="Installiert BepInEx + UCS Modloader Plugin")
    install_loader.add_argument("--game-dir", required=True, help="Spielordner")
    install_loader.add_argument("--build", action="store_true", help="Plugin vor Installation neu bauen")
    install_loader.add_argument("--force", action="store_true", help="Dateien ueberschreiben")
    install_loader.set_defaults(func=command_install_loader)

    merge_runtime = sub.add_parser(
        "merge-runtime",
        help="Baut einen generierten Runtime-Merge-Mod aus aktiven Mods (inkl. Konflikterkennung)",
    )
    merge_runtime.add_argument("--game-dir", required=True, help="Spielordner")
    merge_runtime.add_argument("--output-mod", default="_runtime_merged", help="Name des generierten Merge-Mods")
    merge_runtime.add_argument("--priority", type=int, default=2147483000, help="Prioritaet fuer den Merge-Mod")
    merge_runtime.add_argument("--include-disabled", action="store_true", help="Auch disabled Mods mergen")
    merge_runtime.add_argument("--force", action="store_true", help="Alle Manifest-Texturen als Delta verwenden")
    merge_runtime.add_argument("--include-assets", dest="bundles_only", action="store_false", help="Auch .assets Container mergen")
    merge_runtime.add_argument(
        "--include-opaque-always",
        action="store_true",
        help="Opaque overrides.map Eintraege auch bei Mods mit Delta-Texturen einbeziehen",
    )
    merge_runtime.add_argument("--force-output", action="store_true", help="Bestehenden Output-Mod ueberschreiben")
    merge_runtime.set_defaults(bundles_only=True)
    merge_runtime.set_defaults(func=command_merge_runtime)

    clean_merged = sub.add_parser("clean-merged", help="Loescht den generierten Merge-Mod")
    clean_merged.add_argument("--game-dir", required=True, help="Spielordner")
    clean_merged.add_argument("--output-mod", default="_runtime_merged", help="Name des Merge-Mods")
    clean_merged.add_argument("--force", action="store_true", help="Auch nicht-generierte Ziele loeschen")
    clean_merged.set_defaults(func=command_clean_merged)

    restore = sub.add_parser("restore", help="Originaldateien aus Backups wiederherstellen")
    restore.add_argument("--game-dir", required=True, help="Spielordner")
    restore.add_argument("--purge-backups", action="store_true", help="Backups nach Restore loeschen")
    restore.set_defaults(func=command_restore)

    status = sub.add_parser("status", help="Mods/Backups anzeigen")
    status.add_argument("--game-dir", required=True, help="Spielordner")
    status.add_argument("--json", action="store_true", help="Status als JSON ausgeben")
    status.set_defaults(func=command_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
