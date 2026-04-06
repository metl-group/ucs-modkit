#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MODKIT_ROOT = Path(__file__).resolve().parents[1]
BUILDTOOLS = Path(os.environ.get("UCS_MODKIT_BUILDTOOLS", MODKIT_ROOT.parent / "ucs-modkit-buildtools")).expanduser().resolve()
TARGET = BUILDTOOLS / "build_pyinstaller.py"

if not TARGET.is_file():
    raise SystemExit(f"Buildtools script not found: {TARGET}")

cmd = [sys.executable, str(TARGET), "--modkit-root", str(MODKIT_ROOT), *sys.argv[1:]]
raise SystemExit(subprocess.call(cmd, cwd=str(MODKIT_ROOT)))
