#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BUILDTOOLS_ROOT = Path(__file__).resolve().parent


def run(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def data_arg(src: Path, dst: str) -> str:
    sep = ";" if os.name == "nt" else ":"
    return f"{src}{sep}{dst}"


def exe_name(base: str, target: str) -> str:
    return f"{base}.exe" if target == "windows" else base


def resolve_modkit_root(override: str | None) -> Path:
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser().resolve())
    env_root = os.environ.get("UCS_MODKIT_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser().resolve())
    candidates.append((BUILDTOOLS_ROOT.parent / "ucs-modkit").resolve())

    for candidate in candidates:
        if (candidate / "ucs_modkit.py").is_file() and (candidate / "ucs_modkit_gui.py").is_file():
            return candidate

    raise FileNotFoundError(
        "Could not locate ucs-modkit root. Pass --modkit-root or set UCS_MODKIT_ROOT to the folder containing ucs_modkit.py"
    )


def ensure_pyinstaller(py: str, modkit_root: Path) -> None:
    probe = subprocess.run([py, "-c", "import PyInstaller"], cwd=str(modkit_root))
    if probe.returncode == 0:
        return
    run([py, "-m", "pip", "install", "--upgrade", "pip", "pyinstaller"], cwd=modkit_root)


def build(
    py: str,
    target: str,
    zip_release: bool,
    modkit_root: Path,
    dist_dir: Path,
    work_dir: Path,
    spec_dir: Path,
) -> Path:
    ensure_pyinstaller(py, modkit_root)
    dist_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    common = [
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
    ]

    cli_cmd = [
        py,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "ucs_modkit_cli",
        *common,
        str(modkit_root / "ucs_modkit.py"),
        "--add-data",
        data_arg(modkit_root / "third_party", "third_party"),
        "--add-data",
        data_arg(modkit_root / "modloader", "modloader"),
        "--add-data",
        data_arg(modkit_root / "README.md", "."),
        "--collect-all",
        "UnityPy",
        "--collect-all",
        "PIL",
    ]
    run(cli_cmd, cwd=modkit_root)

    gui_cmd = [
        py,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "ucs_modkit_gui",
        *common,
        str(modkit_root / "ucs_modkit_gui.py"),
    ]
    run(gui_cmd, cwd=modkit_root)

    release_dir = dist_dir / f"UCS-Modkit-{target}"
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)

    gui_exe = dist_dir / exe_name("ucs_modkit_gui", target)
    cli_exe = dist_dir / exe_name("ucs_modkit_cli", target)

    if not gui_exe.exists() or not cli_exe.exists():
        raise FileNotFoundError("PyInstaller output missing (GUI or CLI executable).")

    shutil.copy2(gui_exe, release_dir / gui_exe.name)
    shutil.copy2(cli_exe, release_dir / cli_exe.name)
    shutil.copy2(modkit_root / "README.md", release_dir / "README.md")

    if zip_release:
        archive_base = dist_dir / f"UCS-Modkit-{target}"
        zip_path = dist_dir / f"UCS-Modkit-{target}.zip"
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(archive_base), "zip", root_dir=str(release_dir.parent), base_dir=release_dir.name)

    print(f"Release folder: {release_dir}")
    return release_dir


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build UCS Modkit GUI/CLI executables with PyInstaller")
    p.add_argument("--target", choices=("linux", "windows"), default=("windows" if os.name == "nt" else "linux"))
    p.add_argument("--python", default=sys.executable, help="Python executable to use for build")
    p.add_argument("--zip", action="store_true", help="Also produce a .zip archive in dist/")
    p.add_argument("--modkit-root", default=None, help="Path to ucs-modkit root")
    p.add_argument("--dist-dir", default=None, help="Output dist directory (default: <modkit-root>/dist)")
    p.add_argument("--work-dir", default=None, help="PyInstaller work directory (default: <buildtools>/build/pyinstaller)")
    p.add_argument("--spec-dir", default=None, help="PyInstaller spec directory (default: <buildtools>/spec)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    modkit_root = resolve_modkit_root(args.modkit_root)
    dist_dir = Path(args.dist_dir).expanduser().resolve() if args.dist_dir else (modkit_root / "dist")
    work_dir = Path(args.work_dir).expanduser().resolve() if args.work_dir else (BUILDTOOLS_ROOT / "build" / "pyinstaller")
    spec_dir = Path(args.spec_dir).expanduser().resolve() if args.spec_dir else (BUILDTOOLS_ROOT / "spec")
    build(args.python, args.target, args.zip, modkit_root, dist_dir, work_dir, spec_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
