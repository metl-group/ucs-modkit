#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import UnityPy
    from PIL import Image
    from UnityPy.export import MeshExporter
except ImportError as exc:
    print(
        "Missing dependency. Install with: pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


MANIFEST_VERSION = 1
BACKUP_DIR_NAME = ".ucs_backups"
GLOBAL_MODS_INI = "mods.ini"
LOBOTOMIZED_MARKER = "LOBOTOMIZED_MODE"


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


def tool_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        mei = getattr(sys, "_MEIPASS", None)
        if mei:
            return Path(str(mei)).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_lobotomized_mode(root_dir: Path | None = None) -> bool:
    profile = str(os.environ.get("UCS_MODKIT_PROFILE", "")).strip().lower()
    if profile == "lobotomized":
        return True

    env_toggle = str(os.environ.get("UCS_MODKIT_LOBOTOMIZED", "")).strip().lower()
    if env_toggle in {"1", "true", "yes", "on"}:
        return True

    roots: list[Path] = []
    if root_dir is not None:
        roots.append(root_dir)
    try:
        roots.append(tool_root_dir())
    except Exception:
        pass

    seen: set[Path] = set()
    for root in roots:
        try:
            rr = root.resolve()
        except Exception:
            rr = root
        if rr in seen:
            continue
        seen.add(rr)
        if (rr / LOBOTOMIZED_MARKER).exists():
            return True
    return False


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


def model_id(container_rel: str, assets_file: str, path_id: int) -> str:
    key = f"mesh|{container_rel}|{assets_file}|{path_id}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def detect_data_dir(game_dir: Path) -> Path:
    candidates = [p for p in game_dir.iterdir() if p.is_dir() and p.name.endswith("_Data")]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"No *_Data directory found in {game_dir}")
    names = ", ".join(sorted(p.name for p in candidates))
    raise RuntimeError(f"Multiple *_Data directories found: {names}")


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
        raise FileNotFoundError(f"No manifest.json found in {mod_dir}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("manifest_version") != MANIFEST_VERSION:
        raise RuntimeError(
            f"Unknown manifest version {data.get('manifest_version')} (expected {MANIFEST_VERSION})"
        )
    return data


def save_manifest(mod_dir: Path, manifest_data: dict) -> None:
    manifest_path = mod_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=True, indent=2), encoding="utf-8")


def parse_ini_text(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def read_ini(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_ini_text(path.read_text(encoding="utf-8"))


def parse_overrides_map_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
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


def parse_overrides_map(mod_dir: Path, map_file: str = "overrides.map") -> dict[str, str]:
    path = mod_dir / map_file
    if not path.exists():
        return {}
    return parse_overrides_map_text(path.read_text(encoding="utf-8"))


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


def mods_ini_path(game_dir: Path) -> Path:
    return game_dir / "Mods" / GLOBAL_MODS_INI


def load_global_mod_settings(game_dir: Path) -> dict[str, dict[str, str]]:
    raw = read_ini(mods_ini_path(game_dir))
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if not key.startswith("mod."):
            continue
        rest = key[4:]
        idx = rest.rfind(".")
        if idx <= 0:
            continue
        mod_name = rest[:idx].strip()
        field = rest[idx + 1 :].strip().lower()
        if not mod_name or field not in ("enabled", "priority", "map"):
            continue
        out.setdefault(mod_name.lower(), {})[field] = value
    return out


def resolve_effective_mod_ini(
    mod_name: str, mod_ini: dict[str, str], global_settings: dict[str, dict[str, str]] | None
) -> dict[str, str]:
    effective = dict(mod_ini)
    override = (global_settings or {}).get(mod_name.lower(), {})
    for field in ("enabled", "priority", "map"):
        if field in override:
            effective[field] = override[field]
    return effective


def update_global_mod_settings(
    game_dir: Path,
    mod_name: str,
    enabled: bool | None = None,
    priority: int | None = None,
    map_file: str | None = None,
) -> Path:
    ini_path = mods_ini_path(game_dir)
    ini = read_ini(ini_path)
    key_base = f"mod.{mod_name.lower()}."
    if enabled is not None:
        ini[key_base + "enabled"] = "true" if enabled else "false"
    if priority is not None:
        ini[key_base + "priority"] = str(priority)
    if map_file is not None:
        ini[key_base + "map"] = map_file
    ini_path.parent.mkdir(parents=True, exist_ok=True)
    write_ini(ini_path, ini)
    return ini_path


def zip_find_member(zf: zipfile.ZipFile, rel_path: str) -> str | None:
    want = rel_path.replace("\\", "/").strip("/")
    names = zf.namelist()
    for name in names:
        if name.replace("\\", "/").strip("/") == want:
            return name
    suffix = "/" + want
    for name in names:
        n = name.replace("\\", "/").strip("/")
        if n.endswith(suffix):
            return name
    return None


def load_manifest_from_zip(zip_path: Path) -> dict | None:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member = zip_find_member(zf, "manifest.json")
            if member is None:
                return None
            data = json.loads(zf.read(member).decode("utf-8"))
    except Exception:
        return None
    if data.get("manifest_version") != MANIFEST_VERSION:
        return None
    return data


def read_ini_from_zip(zip_path: Path, rel_path: str = "mod.ini") -> dict[str, str]:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member = zip_find_member(zf, rel_path)
            if member is None:
                return {}
            return parse_ini_text(zf.read(member).decode("utf-8"))
    except Exception:
        return {}


def parse_overrides_map_from_zip(zip_path: Path, map_file: str = "overrides.map") -> dict[str, str]:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member = zip_find_member(zf, map_file)
            if member is None:
                return {}
            return parse_overrides_map_text(zf.read(member).decode("utf-8"))
    except Exception:
        return {}


def zip_member_exists(zip_path: Path, rel_path: str) -> bool:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            return zip_find_member(zf, rel_path) is not None
    except Exception:
        return False


def clear_global_mod_settings(game_dir: Path, mod_name: str) -> Path | None:
    ini_path = mods_ini_path(game_dir)
    if not ini_path.exists():
        return None
    ini = read_ini(ini_path)
    prefix = f"mod.{mod_name.lower()}."
    keys = [k for k in ini.keys() if k.lower().startswith(prefix)]
    if not keys:
        return None
    for key in keys:
        del ini[key]
    write_ini(ini_path, ini)
    return ini_path


def collect_changed_entries_for_zip_mod(zip_path: Path, manifest_data: dict, force: bool) -> list[dict]:
    entries = manifest_data.get("entries", [])
    if not isinstance(entries, list):
        return []

    changed_by_export: dict[str, dict] = {}
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for raw in entries:
                if not isinstance(raw, dict):
                    continue
                export_path = raw.get("export_path")
                if not isinstance(export_path, str) or not export_path:
                    continue
                member = zip_find_member(zf, export_path)
                if member is None:
                    continue
                data = zf.read(member)
                current_hash = hashlib.sha256(data).hexdigest()
                if force or current_hash != raw.get("original_hash"):
                    item = dict(raw)
                    item["_archive_abs"] = str(zip_path.resolve())
                    item["_archive_member"] = member
                    item["_changed_hash"] = current_hash
                    changed_by_export[export_path] = item
    except Exception:
        return []

    ordered: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        export_path = raw.get("export_path")
        if not isinstance(export_path, str):
            continue
        item = changed_by_export.get(export_path)
        if item is not None:
            ordered.append(item)
    return ordered


def collect_changed_entries_for_mod(mod_dir: Path, manifest_data: dict, force: bool) -> list[dict]:
    entries = manifest_data.get("entries", [])
    if not isinstance(entries, list):
        return []

    changed_by_export: dict[str, dict] = {}
    by_export: dict[str, dict] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        export_path = raw.get("export_path")
        if not isinstance(export_path, str) or not export_path:
            continue
        by_export[export_path] = raw
        mod_file = mod_dir / export_path
        if not mod_file.exists():
            continue
        current_hash = sha256_file(mod_file)
        if force or current_hash != raw.get("original_hash"):
            item = dict(raw)
            item["_mod_file"] = str(mod_file)
            item["_changed_hash"] = current_hash
            changed_by_export[export_path] = item

    delta = manifest_data.get("delta_archives")
    archives = []
    if isinstance(delta, dict):
        maybe_archives = delta.get("archives")
        if isinstance(maybe_archives, list):
            archives = maybe_archives

    for archive_meta in archives:
        if not isinstance(archive_meta, dict):
            continue
        archive_rel = archive_meta.get("archive_rel")
        if not isinstance(archive_rel, str) or not archive_rel:
            continue
        archive_abs = (mod_dir / archive_rel).resolve()
        if not archive_abs.exists():
            continue
        archive_entries = archive_meta.get("entries")
        if not isinstance(archive_entries, list):
            continue
        for archived in archive_entries:
            if not isinstance(archived, dict):
                continue
            export_path = archived.get("export_path")
            if not isinstance(export_path, str) or not export_path:
                continue
            if export_path in changed_by_export:
                continue
            base = by_export.get(export_path)
            if not isinstance(base, dict):
                continue
            archive_member = archived.get("archive_member")
            if not isinstance(archive_member, str) or not archive_member:
                archive_member = export_path
            changed_hash = archived.get("sha256")
            if (
                not force
                and isinstance(changed_hash, str)
                and isinstance(base.get("original_hash"), str)
                and changed_hash == base["original_hash"]
            ):
                continue
            item = dict(base)
            item["_archive_abs"] = str(archive_abs)
            item["_archive_member"] = archive_member
            if isinstance(changed_hash, str) and changed_hash:
                item["_changed_hash"] = changed_hash
            changed_by_export[export_path] = item

    ordered: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        export_path = raw.get("export_path")
        if not isinstance(export_path, str):
            continue
        item = changed_by_export.get(export_path)
        if item is not None:
            ordered.append(item)
    return ordered


def runtime_override_entries_from_manifest(manifest_data: dict) -> list[dict]:
    runtime = manifest_data.get("runtime_overrides")
    if not isinstance(runtime, dict):
        return []
    entries = runtime.get("entries")
    if not isinstance(entries, list):
        return []

    out: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        container_rel = raw.get("container_file")
        assets_file = raw.get("assets_file")
        path_id = raw.get("path_id")
        override_rel = raw.get("override_rel")
        if not isinstance(container_rel, str) or not isinstance(assets_file, str):
            continue
        try:
            parsed_path_id = int(path_id)
        except Exception:
            continue
        item = {
            "container_file": container_rel,
            "assets_file": assets_file,
            "path_id": parsed_path_id,
        }
        if isinstance(override_rel, str) and override_rel.strip():
            item["override_rel"] = override_rel.strip()
        out.append(item)
    return out


def is_bundle_container(container_rel: str) -> bool:
    return container_rel.lower().endswith(".bundle")


def copy_file_with_sidecars(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Source missing: {source}")
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


def build_flat_release_zip(mod_dir: Path, release_zip: Path, patch_items: list[dict]) -> tuple[int, int]:
    release_zip.parent.mkdir(parents=True, exist_ok=True)
    textures_written: set[str] = set()
    file_count = 0

    with zipfile.ZipFile(release_zip, "w", compression=zipfile.ZIP_DEFLATED) as out_zip:
        for core_name in ("manifest.json", "mod.ini", "overrides.map"):
            src = mod_dir / core_name
            if src.exists():
                out_zip.write(src, arcname=core_name)
                file_count += 1
            elif core_name == "overrides.map":
                out_zip.writestr(core_name, "# original_container_rel|override_rel\n")
                file_count += 1

        by_export: dict[str, dict] = {}
        for item in patch_items:
            export_path = item.get("export_path")
            if not isinstance(export_path, str) or not export_path:
                continue
            try:
                by_export[export_path] = item
            except Exception as exc:
                continue

        for export_path in sorted(by_export.keys()):
            item = by_export[export_path]
            rel = export_path.replace("\\", "/")
            if rel in textures_written:
                continue
            try:
                img, _ = load_patch_image_from_item(item)
            except Exception as exc:
                print(f"[warn] Could not add texture to release ZIP ({rel}): {exc}", file=sys.stderr)
                continue
            tmp = io.BytesIO()
            img.save(tmp, format="PNG")
            out_zip.writestr(rel, tmp.getvalue())
            textures_written.add(rel)
            file_count += 1

    return file_count, len(textures_written)


def build_texture_lookup(env) -> dict[tuple[str, int], object]:
    lookup: dict[tuple[str, int], object] = {}
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        lookup[(str(obj.assets_file.name), int(obj.path_id))] = obj
    return lookup


def apply_alpha_mode(edited_img: Image.Image, original_img: Image.Image, alpha_mode: str) -> Image.Image:
    edited_rgba = edited_img.convert("RGBA") if edited_img.mode != "RGBA" else edited_img.copy()
    if alpha_mode == "keep":
        return edited_rgba
    if alpha_mode == "opaque":
        r, g, b, _ = edited_rgba.split()
        return Image.merge("RGBA", (r, g, b, Image.new("L", edited_rgba.size, color=255)))
    if alpha_mode == "preserve":
        original_rgba = original_img.convert("RGBA") if original_img.mode != "RGBA" else original_img.copy()
        if original_rgba.size != edited_rgba.size:
            raise ValueError(
                f"Alpha preserve requires same texture size (edited={edited_rgba.size}, original={original_rgba.size})"
            )
        r, g, b, _ = edited_rgba.split()
        _, _, _, src_alpha = original_rgba.split()
        return Image.merge("RGBA", (r, g, b, src_alpha))
    raise ValueError(f"Unsupported alpha mode: {alpha_mode}")


def warn_if_mostly_transparent(img: Image.Image, source_label: str, context: str) -> None:
    if img.mode != "RGBA":
        return
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    total = int(sum(hist))
    if total <= 0:
        return
    zero_count = int(hist[0])
    zero_ratio = zero_count / total
    if zero_ratio >= 0.98:
        print(
            f"[warn] Edited texture is mostly transparent ({zero_ratio:.1%} alpha=0): {source_label} | {context}",
            file=sys.stderr,
        )


def load_patch_image_from_item(item: dict) -> tuple[Image.Image, str]:
    mod_file = item.get("_mod_file")
    if isinstance(mod_file, str) and mod_file:
        src = Path(mod_file)
        with Image.open(src) as im:
            edited_img = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im.copy()
        return edited_img, src.name

    archive_abs = item.get("_archive_abs")
    archive_member = item.get("_archive_member")
    if isinstance(archive_abs, str) and archive_abs and isinstance(archive_member, str) and archive_member:
        with zipfile.ZipFile(archive_abs, "r") as zf:
            data = zf.read(archive_member)
        with Image.open(io.BytesIO(data)) as im:
            edited_img = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im.copy()
        return edited_img, f"{Path(archive_abs).name}:{archive_member}"

    raise FileNotFoundError("Patch item has no valid source (_mod_file or archive entry).")


def patch_container_with_items(container_path: Path, patch_items: list[dict], alpha_mode: str = "preserve") -> int:
    env = UnityPy.load(str(container_path))
    lookup = build_texture_lookup(env)

    patched_here = 0
    for item in patch_items:
        key = (item["assets_file"], int(item["path_id"]))
        obj = lookup.get(key)
        if obj is None:
            print(
                f"[warn] Texture not found: {container_path.name} | {key[0]} | {key[1]}",
                file=sys.stderr,
            )
            continue
        try:
            edited_img, source_label = load_patch_image_from_item(item)
            tex = obj.read()
            original_img = tex.image
            patched_img = apply_alpha_mode(edited_img, original_img, alpha_mode)
            if alpha_mode == "keep":
                warn_if_mostly_transparent(
                    patched_img,
                    source_label,
                    f"{container_path.name} path_id={item['path_id']}",
                )
            tex.image = patched_img
            tex.save()
            patched_here += 1
        except Exception as exc:
            print(
                f"[warn] Patch failed: {container_path.name} path_id={item['path_id']} ({exc})",
                file=sys.stderr,
            )

    if patched_here > 0:
        container_path.write_bytes(env.file.save())
    return patched_here


def patch_container_with_bundle_items(container_path: Path, patch_items: list[dict]) -> int:
    env_target = UnityPy.load(str(container_path))
    target_lookup = build_texture_lookup(env_target)

    by_override: dict[str, list[dict]] = {}
    for item in patch_items:
        override_abs = item.get("_override_abs")
        if not isinstance(override_abs, str) or not override_abs:
            continue
        by_override.setdefault(override_abs, []).append(item)

    patched_here = 0
    for override_abs, items in by_override.items():
        try:
            env_override = UnityPy.load(override_abs)
            override_lookup = build_texture_lookup(env_override)
        except Exception as exc:
            print(f"[warn] Failed to load override bundle {override_abs}: {exc}", file=sys.stderr)
            continue

        for item in items:
            key = (item["assets_file"], int(item["path_id"]))
            target_obj = target_lookup.get(key)
            source_obj = override_lookup.get(key)
            if target_obj is None:
                print(
                    f"[warn] Target texture not found: {container_path.name} | {key[0]} | {key[1]}",
                    file=sys.stderr,
                )
                continue
            if source_obj is None:
                print(
                    f"[warn] Override texture missing: {Path(override_abs).name} | {key[0]} | {key[1]}",
                    file=sys.stderr,
                )
                continue
            try:
                source_tex = source_obj.read()
                target_tex = target_obj.read()
                src_img = source_tex.image
                if src_img.mode not in ("RGB", "RGBA"):
                    src_img = src_img.convert("RGBA")
                else:
                    src_img = src_img.copy()
                target_tex.image = src_img
                target_tex.save()
                patched_here += 1
            except Exception as exc:
                print(
                    f"[warn] Bundle delta patch failed: {container_path.name} path_id={item['path_id']} ({exc})",
                    file=sys.stderr,
                )

    if patched_here > 0:
        container_path.write_bytes(env_target.file.save())
    return patched_here


def name_matches(regex: re.Pattern[str] | None, *parts: str | None) -> bool:
    if regex is None:
        return True
    haystack = " ".join((p or "") for p in parts)
    return bool(regex.search(haystack))


def collect_mesh_entries(
    game_dir: Path,
    scope: str,
    name_filter: str | None,
    limit: int | None,
    export_dir: Path | None = None,
    export_format: str = "obj",
) -> tuple[list[dict], int]:
    containers = discover_container_files(game_dir, scope)
    regex = re.compile(name_filter, re.IGNORECASE) if name_filter else None
    exported = 0
    entries: list[dict] = []

    for i, container_file in enumerate(containers, start=1):
        print(f"[models] {i}/{len(containers)} {container_file.name}", file=sys.stderr)
        try:
            env = UnityPy.load(str(container_file))
        except Exception as exc:
            print(f"[warn] Could not load {container_file}: {exc}", file=sys.stderr)
            continue

        container_rel = container_file.relative_to(game_dir).as_posix()
        for obj in env.objects:
            if obj.type.name != "Mesh":
                continue
            try:
                mesh = obj.read()
            except Exception as exc:
                print(
                    f"[warn] Mesh read failed ({container_file.name}, path_id={obj.path_id}): {exc}",
                    file=sys.stderr,
                )
                continue

            name = mesh.m_Name or obj.container or f"mesh_{obj.path_id}"
            if not name_matches(regex, name, obj.container, container_rel):
                continue

            submesh_count = 0
            try:
                submesh_count = len(mesh.m_SubMeshes or [])
            except Exception:
                pass

            index_buffer_bytes = 0
            try:
                index_buffer_bytes = len(mesh.m_IndexBuffer or b"")
            except Exception:
                pass

            mid = model_id(container_rel, str(obj.assets_file.name), int(obj.path_id))
            entry = {
                "id": mid,
                "container_file": container_rel,
                "assets_file": str(obj.assets_file.name),
                "path_id": int(obj.path_id),
                "name": name,
                "submesh_count": submesh_count,
                "index_buffer_bytes": index_buffer_bytes,
            }

            if export_dir is not None:
                out_name = f"{mid}__{slugify(name)[:80]}.{export_format}"
                out_rel = Path("models") / out_name
                out_abs = export_dir / out_rel
                out_abs.parent.mkdir(parents=True, exist_ok=True)
                try:
                    mesh_text = MeshExporter.export_mesh(mesh, export_format)
                    out_abs.write_text(mesh_text, encoding="utf-8")
                    entry["export_path"] = out_rel.as_posix()
                except Exception as exc:
                    print(
                        f"[warn] Mesh export failed ({container_file.name}, path_id={obj.path_id}): {exc}",
                        file=sys.stderr,
                    )
                    continue

            entries.append(entry)
            exported += 1
            if limit and exported >= limit:
                return entries, len(containers)

    return entries, len(containers)


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
            print(f"[warn] Could not load {container_file}: {exc}", file=sys.stderr)
            continue

        container_rel = container_file.relative_to(game_dir).as_posix()
        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue
            try:
                tex = obj.read()
            except Exception as exc:
                print(
                    f"[warn] Texture read failed ({container_file.name}, path_id={obj.path_id}): {exc}",
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

    print(f"Found textures: {len(entries)}")
    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(entries, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"Wrote JSON: {out_path}")

    return 0


def command_scan_models(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    entries, _ = collect_mesh_entries(
        game_dir=game_dir,
        scope=args.scope,
        name_filter=args.name_filter,
        limit=args.limit,
        export_dir=None,
    )
    print(f"Found meshes: {len(entries)}")
    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(entries, ensure_ascii=True, indent=2), encoding="utf-8")
        print(f"Wrote JSON: {out_path}")
    return 0


def command_export_models(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mod_dir = ensure_mod_dir(game_dir, args.mod)
    models_dir = mod_dir / "models"
    manifest_path = mod_dir / "models_manifest.json"

    if models_dir.exists() and any(models_dir.rglob("*.obj")) and not args.force:
        print(
            f"[error] {models_dir} already contains model files. Re-run with --force to re-export.",
            file=sys.stderr,
        )
        return 2

    if args.force and models_dir.exists():
        shutil.rmtree(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    entries, _ = collect_mesh_entries(
        game_dir=game_dir,
        scope=args.scope,
        name_filter=args.name_filter,
        limit=args.limit,
        export_dir=mod_dir,
        export_format=args.format,
    )

    payload = {
        "tool": "ucs-modkit",
        "manifest_kind": "models",
        "created_at": utc_now_iso(),
        "game_dir": str(game_dir),
        "mod_name": args.mod,
        "scope": args.scope,
        "name_filter": args.name_filter,
        "format": args.format,
        "entry_count": len(entries),
        "entries": entries,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    print(f"Exported models: {len(entries)}")
    print(f"Models directory: {models_dir}")
    print(f"Manifest: {manifest_path}")
    return 0


def command_export(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mod_dir = ensure_mod_dir(game_dir, args.mod)
    textures_dir = mod_dir / "textures"
    manifest_path = mod_dir / "manifest.json"

    if textures_dir.exists() and any(textures_dir.rglob("*.png")) and not args.force:
        print(
            f"[error] {textures_dir} already contains PNGs. Re-run with --force to re-export.",
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
            print(f"[warn] Could not load {container_file}: {exc}", file=sys.stderr)
            continue

        container_rel = container_file.relative_to(game_dir).as_posix()
        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue

            try:
                tex = obj.read()
            except Exception as exc:
                print(
                    f"[warn] Texture read failed ({container_file.name}, path_id={obj.path_id}): {exc}",
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
                    f"[warn] Export failed ({container_file.name}, path_id={obj.path_id}): {exc}",
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

    print(f"Exported: {len(entries)} textures")
    print(f"Mod directory: {mod_dir}")
    print(f"Manifest: {manifest_path}")
    return 0


def patch_single_mod(game_dir: Path, mod_dir: Path, force: bool, alpha_mode: str) -> tuple[int, int]:
    manifest = load_manifest(mod_dir)
    to_patch = collect_changed_entries_for_mod(mod_dir, manifest, force)

    if not to_patch:
        print(f"[apply] {mod_dir.name}: no changed files found")
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
            print(f"[warn] Container missing: {target}", file=sys.stderr)
            continue

        backup_target = backup_root / container_rel
        if not backup_target.exists():
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_target)

        try:
            patched_here = patch_container_with_items(target, patch_items, alpha_mode=alpha_mode)
        except Exception as exc:
            print(f"[warn] Could not patch {target}: {exc}", file=sys.stderr)
            continue

        if patched_here > 0:
            files_changed += 1
            textures_changed += patched_here
            print(f"[apply] {container_rel}: patched {patched_here} textures")

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


def find_zip_mods(game_dir: Path) -> list[Path]:
    mods_root = game_dir / "Mods"
    if not mods_root.exists():
        return []
    mods: list[Path] = []
    for item in sorted(mods_root.iterdir()):
        if not item.is_file():
            continue
        if item.name.startswith("."):
            continue
        if item.suffix.lower() != ".zip":
            continue
        mods.append(item)
    return mods


def find_mod_sources(game_dir: Path) -> list[dict]:
    out: list[dict] = []
    dirs = find_mod_dirs(game_dir)
    dir_names = {p.name.lower() for p in dirs}
    for d in dirs:
        out.append({"type": "dir", "name": d.name, "path": d})
    for z in find_zip_mods(game_dir):
        name = z.stem
        if name.lower() in dir_names:
            continue
        out.append({"type": "zip", "name": name, "path": z})
    return out


def command_apply(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()

    mod_dirs: list[Path]
    if args.all:
        mod_dirs = find_mod_dirs(game_dir)
        if not mod_dirs:
            print("[error] No mods with manifest.json found.", file=sys.stderr)
            return 2
    else:
        if not args.mod:
            print("[error] Provide either --mod or --all.", file=sys.stderr)
            return 2
        mod_dirs = [game_dir / "Mods" / args.mod]

    total_files = 0
    total_textures = 0
    print(f"[apply] alpha-mode: {args.alpha_mode}")
    for mod_dir in mod_dirs:
        print(f"[apply] Mod: {mod_dir.name}")
        try:
            files_changed, textures_changed = patch_single_mod(
                game_dir,
                mod_dir,
                args.force,
                alpha_mode=args.alpha_mode,
            )
        except Exception as exc:
            print(f"[error] Mod failed ({mod_dir.name}): {exc}", file=sys.stderr)
            continue
        total_files += files_changed
        total_textures += textures_changed

    print(f"Changed containers: {total_files}")
    print(f"Changed textures: {total_textures}")
    return 0


def command_package(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mod_dir = game_dir / "Mods" / args.mod
    bundles_only = bool(args.bundles_only)
    if not mod_dir.exists():
        print(f"[error] Mod directory not found: {mod_dir}", file=sys.stderr)
        return 2

    manifest = load_manifest(mod_dir)
    print(f"[package] alpha-mode: {args.alpha_mode}")
    if args.archive_only and not args.archive_deltas:
        print("[error] --archive-only requires --archive-deltas.", file=sys.stderr)
        return 2

    to_patch = collect_changed_entries_for_mod(mod_dir, manifest, args.force)

    if args.archive_deltas:
        archive_rel = f"archives/{args.archive_name}".replace("\\", "/")
        if not archive_rel.lower().endswith(".zip"):
            archive_rel += ".zip"
        archive_abs = mod_dir / archive_rel
        archive_abs.parent.mkdir(parents=True, exist_ok=True)
        dedup: dict[str, dict] = {}
        for item in to_patch:
            export_path = item.get("export_path")
            if isinstance(export_path, str) and export_path:
                dedup[export_path] = item
        archived_entries: list[dict] = []
        with zipfile.ZipFile(archive_abs, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for export_path in sorted(dedup.keys()):
                item = dedup[export_path]
                try:
                    img, _ = load_patch_image_from_item(item)
                except Exception as exc:
                    print(f"[warn] Could not archive {export_path}: {exc}", file=sys.stderr)
                    continue
                tmp = io.BytesIO()
                img.save(tmp, format="PNG")
                png_bytes = tmp.getvalue()
                member = export_path.replace("\\", "/")
                zf.writestr(member, png_bytes)
                archived_entries.append(
                    {
                        "id": item.get("id"),
                        "container_file": item.get("container_file"),
                        "assets_file": item.get("assets_file"),
                        "path_id": parse_int(item.get("path_id"), 0),
                        "export_path": export_path,
                        "archive_member": member,
                        "sha256": hashlib.sha256(png_bytes).hexdigest(),
                    }
                )

        if archived_entries:
            delta = manifest.get("delta_archives")
            previous_archives: list[dict] = []
            if isinstance(delta, dict):
                old_archives = delta.get("archives")
                if isinstance(old_archives, list):
                    for old in old_archives:
                        if not isinstance(old, dict):
                            continue
                        if old.get("archive_rel") == archive_rel:
                            continue
                        previous_archives.append(old)
            previous_archives.append(
                {
                    "archive_rel": archive_rel,
                    "generated_at": utc_now_iso(),
                    "entry_count": len(archived_entries),
                    "entries": archived_entries,
                }
            )
            manifest["delta_archives"] = {
                "mode": "texture_delta_archive_v1",
                "generated_at": utc_now_iso(),
                "archive_count": len(previous_archives),
                "archives": previous_archives,
            }
            save_manifest(mod_dir, manifest)
            print(f"[package] Delta archive: {archive_abs} ({len(archived_entries)} entries)")
        else:
            print("[package] No delta entries were archived.")

        to_patch = collect_changed_entries_for_mod(mod_dir, manifest, args.force)

    if args.prune_archived:
        release_archive = mod_dir / "release" / f"{args.mod}.zip"
        file_count, texture_count = build_flat_release_zip(mod_dir, release_archive, to_patch)
        print(
            f"[package] Flat release ZIP created: {release_archive} "
            f"({file_count} files, {texture_count} changed textures, no nested archives)"
        )

    if args.archive_only:
        mod_ini_path = mod_dir / "mod.ini"
        ini = read_ini(mod_ini_path)
        ini.setdefault("name", args.mod)
        ini.setdefault("map", "overrides.map")
        if args.enabled is not None:
            ini["enabled"] = "true" if args.enabled else "false"
        elif "enabled" not in ini:
            ini["enabled"] = "true"
        ini.pop("priority", None)
        write_ini(mod_ini_path, ini)
        if args.enabled is not None or args.priority is not None:
            cfg_path = update_global_mod_settings(game_dir, args.mod, args.enabled, args.priority)
            print(f"[package] Global mod settings: {cfg_path}")
        print("[package] Archive-only mode complete (no overrides built).")
        return 0

    if not to_patch:
        print("[package] No changed PNGs found.")
        if args.enabled is not None or args.priority is not None:
            cfg_path = update_global_mod_settings(game_dir, args.mod, args.enabled, args.priority)
            print(f"[package] Global mod settings: {cfg_path}")
        return 0

    grouped: dict[str, list[dict]] = {}
    for item in to_patch:
        grouped.setdefault(item["container_file"], []).append(item)

    packaged_containers = 0
    packaged_textures = 0
    skipped_non_bundle = 0
    map_lines: list[str] = []
    runtime_override_entries: list[dict] = []

    for container_rel, patch_items in grouped.items():
        if bundles_only and not is_bundle_container(container_rel):
            skipped_non_bundle += len(patch_items)
            continue

        override_rel = (Path("overrides") / Path(container_rel)).as_posix()
        override_abs = mod_dir / override_rel

        try:
            copy_container_with_sidecars(game_dir, container_rel, override_abs)
            patched_here = patch_container_with_items(override_abs, patch_items, alpha_mode=args.alpha_mode)
        except Exception as exc:
            print(f"[warn] Packaging failed for {container_rel}: {exc}", file=sys.stderr)
            continue

        if patched_here > 0:
            packaged_containers += 1
            packaged_textures += patched_here
            map_lines.append(f"{container_rel}|{override_rel}")
            print(f"[package] {container_rel}: packed {patched_here} textures into override")
            for item in patch_items:
                runtime_override_entries.append(
                    {
                        "container_file": item["container_file"],
                        "assets_file": item["assets_file"],
                        "path_id": int(item["path_id"]),
                        "override_rel": override_rel,
                    }
                )

    if packaged_containers == 0:
        print("[package] No overrides were built.")
        if skipped_non_bundle > 0 and bundles_only:
            print(
                f"[hint] {skipped_non_bundle} changed textures were in .assets files. "
                "You are running bundles-only mode.",
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
    ini["map"] = "overrides.map"
    if args.enabled is not None:
        ini["enabled"] = "true" if args.enabled else "false"
    elif "enabled" not in ini:
        ini["enabled"] = "true"
    ini.pop("priority", None)
    write_ini(mod_ini_path, ini)
    if args.enabled is not None or args.priority is not None:
        cfg_path = update_global_mod_settings(game_dir, args.mod, args.enabled, args.priority)
        print(f"[package] Global mod settings: {cfg_path}")

    manifest["runtime_overrides"] = {
        "mode": "bundle_delta_v1",
        "generated_at": utc_now_iso(),
        "entry_count": len(runtime_override_entries),
        "entries": runtime_override_entries,
    }
    save_manifest(mod_dir, manifest)

    print(f"Built overrides: {packaged_containers} containers / {packaged_textures} textures")
    print(f"Map: {overrides_map_path}")
    print(f"Mod config: {mod_ini_path}")
    print("Manifest updated with runtime override metadata.")
    if skipped_non_bundle > 0 and bundles_only:
        print(
            f"Note: skipped {skipped_non_bundle} textures from non-bundle containers.",
            file=sys.stderr,
        )
    return 0


def command_set_mod(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mods_root = game_dir / "Mods"
    mod_dir = mods_root / args.mod
    mod_ini_path = mod_dir / "mod.ini"
    if mod_dir.exists() and mod_dir.is_dir():
        ini = read_ini(mod_ini_path)
        ini.setdefault("name", args.mod)
        ini.setdefault("map", "overrides.map")
        ini.pop("priority", None)
        write_ini(mod_ini_path, ini)
        print(f"Updated mod config: {mod_ini_path}")
    else:
        print(f"Updated global-only settings for '{args.mod}' (no mod folder present).")
    if args.enabled is not None or args.priority is not None:
        cfg = update_global_mod_settings(game_dir, args.mod, args.enabled, args.priority)
        print(f"Updated global mod settings: {cfg}")
    return 0


def command_install_loader(args: argparse.Namespace) -> int:
    root_dir = tool_root_dir()
    offline_profile = is_lobotomized_mode(root_dir)
    game_dir = Path(args.game_dir).resolve()
    if not game_dir.exists():
        print(f"[error] Game directory missing: {game_dir}", file=sys.stderr)
        return 2

    bepinex_src = root_dir / "third_party" / "bepinex" / "win_x64_5.4.23.5"
    if not bepinex_src.exists():
        print(f"[error] BepInEx template missing: {bepinex_src}", file=sys.stderr)
        return 2

    if args.build and offline_profile:
        print(
            "[error] This build is running in 'lobotomized' offline profile. "
            "Loader build is disabled.",
            file=sys.stderr,
        )
        return 2

    if args.build:
        build_script = root_dir / "build_modloader.sh"
        if not build_script.exists():
            print(f"[error] Build script missing: {build_script}", file=sys.stderr)
            return 2
        print("[loader] Building modloader plugin ...")
        proc = subprocess.run([str(build_script), str(game_dir)])
        if proc.returncode != 0:
            return proc.returncode

    plugin_candidates = [
        root_dir
        / "modloader"
        / "Ucs.AddressablesOverlayLoader"
        / "bin"
        / "Release"
        / "net472"
        / "Ucs.AddressablesOverlayLoader.dll",
        root_dir / "plugin_dll" / "Ucs.AddressablesOverlayLoader.dll",
    ]
    plugin_dll = next((p for p in plugin_candidates if p.exists()), None)
    if plugin_dll is None:
        print("[error] Plugin DLL missing.", file=sys.stderr)
        for candidate in plugin_candidates:
            print(f"  - expected: {candidate}", file=sys.stderr)
        if not offline_profile:
            print("Run build_modloader.sh first or use --build.", file=sys.stderr)
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
            "Global mod settings live in Mods/mods.ini (enabled/priority).\n\n"
            "Required files for runtime overrides:\n"
            "- mod.ini\n"
            "- overrides.map\n"
            "- overrides/... (modified container files)\n\n"
            "For archive-style mods (small redistributables):\n"
            "- manifest.json\n"
            "- archives/*.zip (delta textures only)\n"
            "- optional mod.ini\n\n"
            "Use ucs_modkit.py package to create these files.\n",
            encoding="utf-8",
        )

    print(f"Installed modloader into: {game_dir}")
    print(f"Plugin: {plugin_dir / plugin_dll.name}")
    if sys.platform.startswith("linux"):
        print("Steam/Proton Launch Option (Linux): WINEDLLOVERRIDES=\"winhttp=n,b\" %command%")
    elif sys.platform == "win32":
        print("Windows launch option: none required.")
    return 0


def command_merge_runtime(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mods_root = game_dir / "Mods"
    output_mod = args.output_mod
    if output_mod.startswith("."):
        print("[error] output-mod must not start with '.' (ignored by loader).", file=sys.stderr)
        return 2

    output_dir = mods_root / output_mod
    output_overrides = output_dir / "overrides"
    output_map = output_dir / "overrides.map"
    output_ini = output_dir / "mod.ini"
    output_report = output_dir / "merge_report.json"

    if output_dir.exists():
        ini = read_ini(output_ini)
        if ini.get("generated_by", "") != "ucs_modkit_merge_runtime" and not args.force_output:
            print(
                f"[error] Output mod '{output_mod}' exists and is not a generated merge mod. "
                "Use --force-output to overwrite it.",
                file=sys.stderr,
            )
            return 2
        shutil.rmtree(output_dir, ignore_errors=True)

    all_mods = find_mod_sources(game_dir)
    source_mods: list[dict] = []
    global_settings = load_global_mod_settings(game_dir)
    for source in all_mods:
        mod_name = str(source["name"])
        source_type = str(source["type"])
        source_path = Path(source["path"])
        if mod_name == output_mod:
            continue
        if source_type == "zip":
            raw_ini = read_ini_from_zip(source_path, "mod.ini")
        else:
            raw_ini = read_ini(source_path / "mod.ini")
        ini = resolve_effective_mod_ini(mod_name, raw_ini, global_settings)
        enabled = parse_bool(ini.get("enabled"), True)
        if not args.include_disabled and not enabled:
            continue
        priority = parse_int(ini.get("priority"), 0)
        map_file = ini.get("map", "overrides.map")
        if source_type == "zip":
            parsed_map = parse_overrides_map_from_zip(source_path, map_file)
        else:
            parsed_map = parse_overrides_map(source_path, map_file)

        delta_png_entries: list[dict] = []
        runtime_bundle_entries: list[dict] = []
        manifest = load_manifest_from_zip(source_path) if source_type == "zip" else (
            load_manifest(source_path) if (source_path / "manifest.json").exists() else None
        )
        if manifest is not None:
            try:
                if source_type == "zip":
                    delta_png_entries = collect_changed_entries_for_zip_mod(source_path, manifest, args.force)
                else:
                    delta_png_entries = collect_changed_entries_for_mod(source_path, manifest, args.force)
                if not delta_png_entries:
                    runtime_entries = runtime_override_entries_from_manifest(manifest)
                    for item in runtime_entries:
                        container_rel = item["container_file"]
                        override_rel = item.get("override_rel")
                        if not isinstance(override_rel, str) or not override_rel:
                            override_rel = parsed_map.get(container_rel)
                        if not isinstance(override_rel, str) or not override_rel:
                            continue
                        if source_type == "zip":
                            # Flat zip mods are expected to provide texture deltas, not opaque bundle files.
                            continue
                        override_abs = (source_path / override_rel).resolve()
                        if not override_abs.exists():
                            continue
                        runtime_item = dict(item)
                        runtime_item["_override_abs"] = str(override_abs)
                        runtime_bundle_entries.append(runtime_item)
            except Exception as exc:
                print(f"[warn] Failed to read manifest ({mod_name}): {exc}", file=sys.stderr)

        # Opaque bundle overrides (fallback for external mods without texture-level metadata).
        opaque_map: dict[str, str] = {}
        if args.include_opaque_always:
            opaque_map = parsed_map
        elif not delta_png_entries and not runtime_bundle_entries:
            opaque_map = parsed_map

        if not delta_png_entries and not runtime_bundle_entries and not opaque_map:
            continue

        source_mods.append(
            {
                "name": mod_name,
                "source_type": source_type,
                "path": source_path,
                "priority": priority,
                "enabled": enabled,
                "delta_png_entries": delta_png_entries,
                "runtime_bundle_entries": runtime_bundle_entries,
                "opaque_map": opaque_map,
            }
        )

    source_mods.sort(key=lambda m: (m["priority"], m["name"].lower()))
    if not source_mods:
        print("[merge] No suitable mods found.")
        output_dir.mkdir(parents=True, exist_ok=True)
        if output_overrides.exists():
            shutil.rmtree(output_overrides)
        output_map.write_text("# original_container_rel|override_rel\n", encoding="utf-8")
        write_ini(
            output_ini,
            {
                "name": output_mod,
                "enabled": "true",
                "map": "overrides.map",
                "generated_by": "ucs_modkit_merge_runtime",
                "generated_at": utc_now_iso(),
            },
        )
        cfg_path = update_global_mod_settings(game_dir, output_mod, True, args.priority)
        print(f"[merge] Global mod settings: {cfg_path}")
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
        mod_path = mod["path"]
        prio = mod["priority"]

        for item in mod["delta_png_entries"]:
            container_rel = item["container_file"]
            plan.setdefault(container_rel, {"baselines": [], "patches": []})
            planned = dict(item)
            planned["_mod_name"] = mod_name
            planned["_priority"] = prio
            planned["_patch_kind"] = "png"
            plan[container_rel]["patches"].append(planned)

            k = (container_rel, item["assets_file"], int(item["path_id"]))
            texture_mods.setdefault(k, []).append(mod_name)

        for item in mod["runtime_bundle_entries"]:
            container_rel = item["container_file"]
            plan.setdefault(container_rel, {"baselines": [], "patches": []})
            planned = dict(item)
            planned["_mod_name"] = mod_name
            planned["_priority"] = prio
            planned["_patch_kind"] = "bundle"
            plan[container_rel]["patches"].append(planned)

            k = (container_rel, item["assets_file"], int(item["path_id"]))
            texture_mods.setdefault(k, []).append(mod_name)

        for original_rel, override_rel in mod["opaque_map"].items():
            if mod.get("source_type") == "zip":
                continue
            override_abs = (mod_path / override_rel).resolve()
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
        print(f"[merge] alpha-mode: {args.alpha_mode}")
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
                print(f"[warn] Container missing and no baseline available: {container_rel}", file=sys.stderr)
                continue

            baseline_start_idx = 1 if seeded_from_baseline else 0
            for b in baselines[baseline_start_idx:]:
                copy_file_with_sidecars(Path(b["override_abs"]), tmp_container)

            patched_here = 0
            if patches:
                winner_by_texture: dict[tuple[str, int], dict] = {}
                for patch in patches:
                    key = (patch["assets_file"], int(patch["path_id"]))
                    winner_by_texture[key] = patch
                winner_patches = list(winner_by_texture.values())
                winner_png = [p for p in winner_patches if p.get("_patch_kind") == "png"]
                winner_bundle = [p for p in winner_patches if p.get("_patch_kind") == "bundle"]
                if winner_png:
                    patched_here += patch_container_with_items(tmp_container, winner_png, alpha_mode=args.alpha_mode)
                if winner_bundle:
                    patched_here += patch_container_with_bundle_items(tmp_container, winner_bundle)

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
            "map": "overrides.map",
            "generated_by": "ucs_modkit_merge_runtime",
            "generated_at": utc_now_iso(),
        },
    )
    cfg_path = update_global_mod_settings(game_dir, output_mod, True, args.priority)
    print(f"[merge] Global mod settings: {cfg_path}")

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
                "source_type": m.get("source_type", "dir"),
                "priority": m["priority"],
                "enabled": m["enabled"],
                "delta_entries": len(m["delta_png_entries"]),
                "runtime_entries": len(m["runtime_bundle_entries"]),
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
            f"Note: skipped {skipped_non_bundle} non-bundle containers.",
            file=sys.stderr,
        )
    return 0


def command_clean_merged(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    output_dir = game_dir / "Mods" / args.output_mod
    if not output_dir.exists():
        print("Merge output not found.")
        return 0

    ini = read_ini(output_dir / "mod.ini")
    if ini.get("generated_by", "") != "ucs_modkit_merge_runtime" and not args.force:
        print(
            "[error] Target is not a generated merge mod. Use --force to delete anyway.",
            file=sys.stderr,
        )
        return 2
    shutil.rmtree(output_dir, ignore_errors=True)
    cfg_path = clear_global_mod_settings(game_dir, args.output_mod)
    if cfg_path is not None:
        print(f"Removed global mod settings: {cfg_path}")
    print(f"Deleted merge output: {output_dir}")
    return 0


def command_restore(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    backup_root = game_dir / "Mods" / BACKUP_DIR_NAME
    if not backup_root.exists():
        print("No backups found.")
        return 0

    backups = [p for p in backup_root.rglob("*") if p.is_file()]
    if not backups:
        print("No backups found.")
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
        print(f"Restore complete ({restored} files). Backups were removed.")
    else:
        print(f"Restore complete ({restored} files). Backups were kept.")
    return 0


def command_status(args: argparse.Namespace) -> int:
    game_dir = Path(args.game_dir).resolve()
    mods_root = game_dir / "Mods"
    backup_root = mods_root / BACKUP_DIR_NAME

    mod_sources = find_mod_sources(game_dir)
    global_settings = load_global_mod_settings(game_dir)
    backup_count = len([p for p in backup_root.rglob("*") if p.is_file()]) if backup_root.exists() else 0

    rows = []
    for source in mod_sources:
        mod_name = str(source["name"])
        source_type = str(source["type"])
        mod_path = Path(source["path"])
        count: int | None
        entries_kind = "n/a"
        exported_entries: int | None = None
        runtime_entries: int | None = None
        has_overrides_map = False

        manifest_data: dict | None = None
        if source_type == "zip":
            manifest_data = load_manifest_from_zip(mod_path)
            raw_ini = read_ini_from_zip(mod_path, "mod.ini")
            map_file = raw_ini.get("map", "overrides.map")
            has_overrides_map = zip_member_exists(mod_path, map_file)
        else:
            if (mod_path / "manifest.json").exists():
                try:
                    manifest_data = load_manifest(mod_path)
                except Exception:
                    manifest_data = {"_invalid": True}
            raw_ini = read_ini(mod_path / "mod.ini")
            map_file = raw_ini.get("map", "overrides.map")
            has_overrides_map = bool(parse_overrides_map(mod_path, map_file))

        if manifest_data is not None:
            try:
                data = manifest_data
                if data.get("_invalid") is True:
                    raise RuntimeError("invalid")
                exported_entries = int(data.get("entry_count", len(data.get("entries", []))))
                runtime_obj = data.get("runtime_overrides")
                if isinstance(runtime_obj, dict):
                    runtime_entries = int(runtime_obj.get("entry_count", len(runtime_obj.get("entries", []))))
                count = runtime_entries if runtime_entries is not None else exported_entries
                entries_kind = "runtime" if runtime_entries is not None else "exported"
            except Exception:
                count = -1
                entries_kind = "invalid"
        else:
            count = None
        ini = resolve_effective_mod_ini(mod_name, raw_ini, global_settings)
        rows.append(
            {
                "mod": mod_name,
                "source_type": source_type,
                "entries": count,
                "entries_kind": entries_kind,
                "exported_entries": exported_entries,
                "runtime_entries": runtime_entries,
                "enabled": parse_bool(ini.get("enabled"), True),
                "priority": parse_int(ini.get("priority"), 0),
                "has_overrides_map": has_overrides_map,
            }
        )

    payload = {
        "game_dir": str(game_dir),
        "mods_count": len(mod_sources),
        "backups_count": backup_count,
        "mods": rows,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0

    print(f"Game: {game_dir}")
    print(f"Mods found: {len(mod_sources)}")
    for row in rows:
        entries = row["entries"]
        kind = row.get("entries_kind", "n/a")
        if entries is None:
            entries_txt = "n/a"
            entries_label = "entries"
        elif entries >= 0:
            entries_txt = str(entries)
            entries_label = f"{kind} entries"
        else:
            entries_txt = "manifest invalid"
            entries_label = "entries"
        print(
            f"  - {row['mod']} ({row.get('source_type', 'dir')}): {entries_txt} {entries_label} | enabled={row['enabled']} "
            f"| priority={row['priority']} | overrides.map={row['has_overrides_map']}"
        )
    print(f"Backups: {backup_count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ucs_modkit",
        description="Modmaker + runtime modloader toolkit for Used Cars Simulator.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="List textures inside game containers")
    scan.add_argument("--game-dir", required=True, help="Game directory")
    scan.add_argument("--scope", choices=("assets", "bundles", "all"), default="all")
    scan.add_argument("--name-filter", help="Regex filter for texture/container name")
    scan.add_argument("--limit", type=int, help="Maximum number of textures")
    scan.add_argument("--output", help="Optional output JSON file")
    scan.set_defaults(func=command_scan)

    scan_models = sub.add_parser("scan-models", help="List Mesh objects inside game containers")
    scan_models.add_argument("--game-dir", required=True, help="Game directory")
    scan_models.add_argument("--scope", choices=("assets", "bundles", "all"), default="all")
    scan_models.add_argument("--name-filter", help="Regex filter for mesh/container name")
    scan_models.add_argument("--limit", type=int, help="Maximum number of meshes")
    scan_models.add_argument("--output", help="Optional output JSON file")
    scan_models.set_defaults(func=command_scan_models)

    export_models = sub.add_parser("export-models", help="Export Mesh objects as OBJ files into a mod folder")
    export_models.add_argument("--game-dir", required=True, help="Game directory")
    export_models.add_argument("--mod", required=True, help="Mod name, e.g. model-workbench")
    export_models.add_argument("--scope", choices=("assets", "bundles", "all"), default="all")
    export_models.add_argument("--name-filter", help="Regex filter for mesh/container name")
    export_models.add_argument("--limit", type=int, help="Maximum number of meshes")
    export_models.add_argument("--format", choices=("obj",), default="obj", help="Model export format")
    export_models.add_argument("--force", action="store_true", help="Delete existing model exports in the mod folder")
    export_models.set_defaults(func=command_export_models)

    export = sub.add_parser("export", help="Export textures as PNG files into a mod folder")
    export.add_argument("--game-dir", required=True, help="Game directory")
    export.add_argument("--mod", required=True, help="Mod name, e.g. my-first-mod")
    export.add_argument("--scope", choices=("assets", "bundles", "all"), default="all")
    export.add_argument("--name-filter", help="Regex filter for texture/container name")
    export.add_argument("--limit", type=int, help="Maximum number of textures")
    export.add_argument("--force", action="store_true", help="Delete existing PNGs in the mod folder")
    export.set_defaults(func=command_export)

    apply_cmd = sub.add_parser("apply", help="Patch changed PNGs directly back into the game")
    apply_cmd.add_argument("--game-dir", required=True, help="Game directory")
    apply_cmd.add_argument("--mod", help="Mod name (if not using --all)")
    apply_cmd.add_argument("--all", action="store_true", help="Apply all mods that have a manifest.json")
    apply_cmd.add_argument("--force", action="store_true", help="Apply all PNGs, not only changed ones")
    apply_cmd.add_argument(
        "--alpha-mode",
        choices=("preserve", "keep", "opaque"),
        default="preserve",
        help="How PNG alpha is handled: preserve (recommended), keep (use edited alpha), opaque (force 255).",
    )
    apply_cmd.set_defaults(func=command_apply)

    package_cmd = sub.add_parser(
        "package", help="Build runtime overrides for the modloader (without overwriting game files)"
    )
    package_cmd.add_argument("--game-dir", required=True, help="Game directory")
    package_cmd.add_argument("--mod", required=True, help="Mod name")
    package_cmd.add_argument("--force", action="store_true", help="Process all exported PNGs")
    package_cmd.add_argument("--include-assets", dest="bundles_only", action="store_false", help="Include .assets containers (default)")
    package_cmd.add_argument("--bundles-only", dest="bundles_only", action="store_true", help="Process only .bundle containers")
    package_cmd.add_argument("--archive-deltas", action="store_true", help="Store changed textures in archives/<name>.zip and record them in manifest.json")
    package_cmd.add_argument("--archive-name", default="delta_textures.zip", help="Archive file name under mod/archives/")
    package_cmd.add_argument(
        "--prune-archived",
        action="store_true",
        help="Create flat release/<mod>.zip (manifest/mod.ini/overrides.map + changed textures/*.png, no nested archives).",
    )
    package_cmd.add_argument("--archive-only", action="store_true", help="Only build/update delta archives, do not build overrides/")
    package_cmd.add_argument(
        "--alpha-mode",
        choices=("preserve", "keep", "opaque"),
        default="preserve",
        help="How PNG alpha is handled: preserve (recommended), keep (use edited alpha), opaque (force 255).",
    )
    package_cmd.add_argument("--enabled", type=parse_bool, nargs="?", const=True, help="Set mod enabled")
    package_cmd.add_argument("--priority", type=int, help="Global mod priority (stored in Mods/mods.ini)")
    package_cmd.set_defaults(bundles_only=False)
    package_cmd.set_defaults(func=command_package)

    set_mod = sub.add_parser("set-mod", help="Set enabled/priority in Mods/mods.ini")
    set_mod.add_argument("--game-dir", required=True, help="Game directory")
    set_mod.add_argument("--mod", required=True, help="Mod name")
    set_mod.add_argument("--enabled", type=parse_bool, nargs="?", const=True, help="Global enabled true/false")
    set_mod.add_argument("--priority", type=int, help="Global priority")
    set_mod.set_defaults(func=command_set_mod)

    install_loader = sub.add_parser("install-loader", help="Install BepInEx + UCS modloader plugin")
    install_loader.add_argument("--game-dir", required=True, help="Game directory")
    install_loader.add_argument("--build", action="store_true", help="Build plugin before install")
    install_loader.add_argument("--force", action="store_true", help="Overwrite files")
    install_loader.set_defaults(func=command_install_loader)

    merge_runtime = sub.add_parser(
        "merge-runtime",
        help="Build a generated runtime merge-mod from active mods (with conflict reporting)",
    )
    merge_runtime.add_argument("--game-dir", required=True, help="Game directory")
    merge_runtime.add_argument("--output-mod", default="_runtime_merged", help="Name of generated merge mod")
    merge_runtime.add_argument("--priority", type=int, default=2147483000, help="Global priority for generated merge mod (Mods/mods.ini)")
    merge_runtime.add_argument("--include-disabled", action="store_true", help="Include disabled mods")
    merge_runtime.add_argument("--force", action="store_true", help="Treat all manifest textures as changed deltas")
    merge_runtime.add_argument("--include-assets", dest="bundles_only", action="store_false", help="Include .assets containers (default)")
    merge_runtime.add_argument("--bundles-only", dest="bundles_only", action="store_true", help="Merge only .bundle containers")
    merge_runtime.add_argument(
        "--alpha-mode",
        choices=("preserve", "keep", "opaque"),
        default="preserve",
        help="How PNG alpha deltas are handled: preserve (recommended), keep, opaque.",
    )
    merge_runtime.add_argument(
        "--include-opaque-always",
        action="store_true",
        help="Always include opaque overrides.map entries, even for mods with texture deltas",
    )
    merge_runtime.add_argument("--force-output", action="store_true", help="Overwrite existing output mod")
    merge_runtime.set_defaults(bundles_only=False)
    merge_runtime.set_defaults(func=command_merge_runtime)

    clean_merged = sub.add_parser("clean-merged", help="Delete generated merge mod")
    clean_merged.add_argument("--game-dir", required=True, help="Game directory")
    clean_merged.add_argument("--output-mod", default="_runtime_merged", help="Merge mod name")
    clean_merged.add_argument("--force", action="store_true", help="Also delete non-generated targets")
    clean_merged.set_defaults(func=command_clean_merged)

    restore = sub.add_parser("restore", help="Restore original files from backups")
    restore.add_argument("--game-dir", required=True, help="Game directory")
    restore.add_argument("--purge-backups", action="store_true", help="Delete backups after restore")
    restore.set_defaults(func=command_restore)

    status = sub.add_parser("status", help="Show mods and backups")
    status.add_argument("--game-dir", required=True, help="Game directory")
    status.add_argument("--json", action="store_true", help="Print status as JSON")
    status.set_defaults(func=command_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
