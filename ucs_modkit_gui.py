#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


IS_FROZEN = bool(getattr(sys, "frozen", False))
if IS_FROZEN:
    APP_ROOT = Path(str(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))).resolve()
    BIN_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parent
    BIN_ROOT = APP_ROOT

ROOT = APP_ROOT
CLI = APP_ROOT / "ucs_modkit.py"
BUILD_SCRIPT = APP_ROOT / "build_modloader.sh"


def settings_file_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "UCSModkitStudio" / "settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "UCSModkitStudio" / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "ucs-modkit-studio" / "settings.json"


def default_game_dir() -> str:
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Steam"
            / "steamapps"
            / "common"
            / "Used Cars Simulator",
            Path(r"C:\Program Files (x86)\Steam\steamapps\common\Used Cars Simulator"),
            Path(r"D:\SteamLibrary\steamapps\common\Used Cars Simulator"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return str(candidates[0])

    candidates = [
        Path.home() / ".steam" / "steam" / "steamapps" / "common" / "Used Cars Simulator",
        Path.home() / ".local" / "share" / "Steam" / "steamapps" / "common" / "Used Cars Simulator",
        Path("/mnt/steam/steamapps/common/Used Cars Simulator"),
        Path("/mnt/SteamLibrary/steamapps/common/Used Cars Simulator"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(candidates[0])


DEFAULT_GAME = default_game_dir()


def open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("UCS Modkit Studio")
        self.geometry("1320x860")
        self.minsize(1040, 700)

        self._busy = False
        self._selected_mod: str | None = None
        self._reflow_pending = False
        self._settings_path = settings_file_path()
        self._settings = self._load_settings()

        self._setup_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh_mods()

    def _load_settings(self) -> dict:
        try:
            if self._settings_path.exists():
                data = json.loads(self._settings_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_settings(self) -> None:
        payload = {
            "game_dir": self.game_var.get().strip(),
        }
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            # Keep running even if settings cannot be written (permission/readonly FS).
            self._log(f"[warn] Could not save settings: {exc}")

    def _on_close(self) -> None:
        self._save_settings()
        self.destroy()

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Title.TLabel", font=("DejaVu Sans", 14, "bold"))
        style.configure("Hint.TLabel", foreground="#4a5568")
        style.configure("Accent.TButton", padding=(10, 6))
        style.configure("Treeview", rowheight=32, font=("DejaVu Sans", 13))
        style.configure("Treeview.Heading", font=("DejaVu Sans", 13, "bold"))

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self, padding=10)
        root_frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root_frame)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top, text="UCS Modkit Studio", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            top,
            text="Modloader + Modmaker + GUI for Used Cars Simulator",
            style="Hint.TLabel",
        ).pack(side=tk.LEFT, padx=(12, 0))

        path_row = ttk.Frame(root_frame)
        path_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(path_row, text="Game Dir:").pack(side=tk.LEFT)
        saved_game_dir = str(self._settings.get("game_dir", "")).strip()
        initial_game_dir = saved_game_dir or DEFAULT_GAME
        self.game_var = tk.StringVar(value=initial_game_dir)
        self.game_entry = ttk.Entry(path_row, textvariable=self.game_var)
        self.game_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.game_entry.bind("<FocusOut>", lambda _e: self._save_settings())
        ttk.Button(path_row, text="Browse", command=self._browse_game_dir).pack(side=tk.LEFT)

        main_pane = ttk.PanedWindow(root_frame, orient=tk.VERTICAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        upper = ttk.Frame(main_pane)
        log_frame = ttk.LabelFrame(main_pane, text="Log", padding=8)
        main_pane.add(upper, weight=4)
        main_pane.add(log_frame, weight=2)

        notebook = ttk.Notebook(upper)
        notebook.pack(fill=tk.BOTH, expand=True)

        self.tab_modmaker = ttk.Frame(notebook, padding=10)
        self.tab_loader = ttk.Frame(notebook, padding=10)
        self.tab_mods = ttk.Frame(notebook, padding=10)
        notebook.add(self.tab_modmaker, text="Modmaker")
        notebook.add(self.tab_loader, text="Modloader")
        notebook.add(self.tab_mods, text="Mods")

        self._build_modmaker_tab()
        self._build_loader_tab()
        self._build_mods_tab()

        self.log_text = tk.Text(log_frame, height=16, wrap=tk.WORD, font=("DejaVu Sans Mono", 11))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self._log("Ready.")

    def _build_modmaker_tab(self) -> None:
        frame = self.tab_modmaker
        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=4)
        ttk.Label(row1, text="Mod name:").pack(side=tk.LEFT)
        self.mod_name_var = tk.StringVar(value="my_mod")
        ttk.Entry(row1, textvariable=self.mod_name_var, width=28).pack(side=tk.LEFT, padx=8)
        ttk.Label(row1, text="Scope:").pack(side=tk.LEFT)
        self.scope_var = tk.StringVar(value="bundles")
        ttk.Combobox(row1, textvariable=self.scope_var, values=["bundles", "all", "assets"], width=10, state="readonly").pack(
            side=tk.LEFT, padx=8
        )
        ttk.Label(row1, text="Name Filter (Regex):").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar(value="")
        ttk.Entry(row1, textvariable=self.filter_var, width=30).pack(side=tk.LEFT, padx=8)

        row2 = ttk.Frame(frame)
        row2.pack(fill=tk.X, pady=4)
        self.modmaker_options_frame = row2
        self.force_export_var = tk.BooleanVar(value=False)
        self.package_force_var = tk.BooleanVar(value=False)
        self.include_assets_var = tk.BooleanVar(value=True)
        self.archive_deltas_var = tk.BooleanVar(value=False)
        self.archive_only_var = tk.BooleanVar(value=False)
        self.prune_archived_var = tk.BooleanVar(value=False)
        self.package_alpha_mode_var = tk.StringVar(value="preserve")
        self.priority_var = tk.StringVar(value="")

        self.archive_only_check = ttk.Checkbutton(row2, text="Archive only", variable=self.archive_only_var)

        alpha_group = ttk.Frame(row2)
        ttk.Label(alpha_group, text="Alpha:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(
            alpha_group,
            textvariable=self.package_alpha_mode_var,
            values=["preserve", "keep", "opaque"],
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT)

        priority_group = ttk.Frame(row2)
        ttk.Label(priority_group, text="Global Priority:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(priority_group, textvariable=self.priority_var, width=6).pack(side=tk.LEFT)

        self._modmaker_option_widgets = [
            ttk.Checkbutton(row2, text="Export --force", variable=self.force_export_var),
            ttk.Checkbutton(row2, text="Package --force", variable=self.package_force_var),
            ttk.Checkbutton(row2, text="Include .assets", variable=self.include_assets_var),
            ttk.Checkbutton(row2, text="Archive changed textures", variable=self.archive_deltas_var),
            self.archive_only_check,
            ttk.Checkbutton(row2, text="Build flat release ZIP", variable=self.prune_archived_var),
            alpha_group,
            priority_group,
        ]

        self.archive_deltas_var.trace_add("write", lambda *_: self._on_archive_deltas_toggle())
        self.archive_only_var.trace_add("write", lambda *_: self._on_archive_only_toggle())
        self.modmaker_options_frame.bind("<Configure>", self._schedule_modmaker_reflow)
        self._on_archive_deltas_toggle()
        self._reflow_modmaker_options()

        row3 = ttk.Frame(frame)
        row3.pack(fill=tk.X, pady=8)
        ttk.Button(row3, text="1) Export Textures", style="Accent.TButton", command=self.do_export).pack(side=tk.LEFT)
        ttk.Button(row3, text="Export 3D Models (OBJ)", command=self.do_export_models).pack(side=tk.LEFT, padx=8)
        ttk.Button(row3, text="2) Open Texture Folder", command=self.open_texture_folder).pack(side=tk.LEFT, padx=8)
        ttk.Button(row3, text="3) Package Runtime Overrides", style="Accent.TButton", command=self.do_package).pack(side=tk.LEFT)

    def _schedule_modmaker_reflow(self, _event=None) -> None:
        if self._reflow_pending:
            return
        self._reflow_pending = True
        self.after_idle(self._reflow_modmaker_options)

    def _reflow_modmaker_options(self) -> None:
        self._reflow_pending = False
        frame = getattr(self, "modmaker_options_frame", None)
        widgets = getattr(self, "_modmaker_option_widgets", [])
        if frame is None or not widgets:
            return
        width = frame.winfo_width()
        if width <= 1:
            return

        max_cols = max(1, width // 300)
        for w in widgets:
            w.grid_forget()
        for i in range(12):
            frame.grid_columnconfigure(i, weight=0)
        for i, w in enumerate(widgets):
            row = i // max_cols
            col = i % max_cols
            w.grid(row=row, column=col, sticky=tk.W, padx=(0, 12), pady=2)
        for col in range(max_cols):
            frame.grid_columnconfigure(col, weight=1)

    def _on_archive_deltas_toggle(self) -> None:
        enabled = self.archive_deltas_var.get()
        if not enabled and self.archive_only_var.get():
            self.archive_only_var.set(False)
        if enabled:
            self.archive_only_check.state(["!disabled"])
        else:
            self.archive_only_check.state(["disabled"])
        self._schedule_modmaker_reflow()

    def _on_archive_only_toggle(self) -> None:
        if self.archive_only_var.get() and not self.archive_deltas_var.get():
            self.archive_deltas_var.set(True)
        self._schedule_modmaker_reflow()

    def _build_loader_tab(self) -> None:
        frame = self.tab_loader
        info = (
            "Installs BepInEx + the UCS runtime overlay loader.\n"
            "The loader checks Mods/<mod>/overrides/... first for each container."
        )
        ttk.Label(frame, text=info, style="Hint.TLabel").pack(anchor=tk.W, pady=(0, 8))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=4)
        self.loader_force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Force overwrite", variable=self.loader_force_var).pack(side=tk.LEFT)
        self.build_loader_btn = ttk.Button(row, text="Build Loader (Dev)", style="Accent.TButton", command=self.do_build_loader)
        self.build_loader_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(row, text="Install Loader", style="Accent.TButton", command=self.do_install_loader).pack(side=tk.LEFT, padx=8)
        ttk.Button(row, text="Open BepInEx Log", command=self.open_bepinex_log).pack(side=tk.LEFT, padx=8)

        if IS_FROZEN or sys.platform == "win32" or not BUILD_SCRIPT.exists():
            self.build_loader_btn.state(["disabled"])

        launch = ttk.LabelFrame(frame, text="Launch Notes", padding=8)
        launch.pack(fill=tk.X, pady=(12, 4))
        if sys.platform.startswith("linux"):
            ttk.Label(launch, text='Steam/Proton: WINEDLLOVERRIDES="winhttp=n,b" %command%').pack(anchor=tk.W)
        else:
            ttk.Label(launch, text="Windows: no special launch option required.").pack(anchor=tk.W)

    def _cli_base_cmd(self) -> list[str]:
        if not IS_FROZEN:
            return [sys.executable, str(CLI)]

        for name in ("ucs_modkit_cli.exe", "ucs_modkit_cli"):
            candidate = BIN_ROOT / name
            if candidate.exists():
                return [str(candidate)]

        raise RuntimeError(
            "Bundled CLI executable not found. Expected ucs_modkit_cli(.exe) next to the GUI executable."
        )

    def _cli_cmd(self, *args: str) -> list[str]:
        return self._cli_base_cmd() + list(args)

    def _build_mods_tab(self) -> None:
        frame = self.tab_mods
        cols = ("mod", "enabled", "priority", "entries", "map")
        self.mods_tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        self.mods_tree.heading("mod", text="Mod")
        self.mods_tree.heading("enabled", text="Enabled")
        self.mods_tree.heading("priority", text="Priority")
        self.mods_tree.heading("entries", text="Runtime")
        self.mods_tree.heading("map", text="overrides.map")
        self.mods_tree.column("mod", width=300)
        self.mods_tree.column("enabled", width=80, anchor=tk.CENTER)
        self.mods_tree.column("priority", width=80, anchor=tk.CENTER)
        self.mods_tree.column("entries", width=80, anchor=tk.CENTER)
        self.mods_tree.column("map", width=120, anchor=tk.CENTER)
        self.mods_tree.pack(fill=tk.BOTH, expand=True)
        self.mods_tree.bind("<<TreeviewSelect>>", self._on_mod_select)

        controls = ttk.Frame(frame)
        controls.pack(fill=tk.X, pady=8)
        ttk.Button(controls, text="Refresh", command=self.refresh_mods).pack(side=tk.LEFT)
        ttk.Button(controls, text="Enable", command=lambda: self.set_selected_mod(True)).pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="Disable", command=lambda: self.set_selected_mod(False)).pack(side=tk.LEFT)
        ttk.Label(controls, text="Priority:").pack(side=tk.LEFT, padx=(16, 0))
        self.sel_priority_var = tk.StringVar(value="0")
        ttk.Entry(controls, textvariable=self.sel_priority_var, width=6).pack(side=tk.LEFT, padx=6)
        ttk.Button(controls, text="Set Priority", command=self.set_selected_mod_priority).pack(side=tk.LEFT)

        merge = ttk.LabelFrame(frame, text="Runtime Merger", padding=8)
        merge.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(merge, text="Output Mod:").pack(side=tk.LEFT)
        self.merge_output_mod_var = tk.StringVar(value="_runtime_merged")
        ttk.Entry(merge, textvariable=self.merge_output_mod_var, width=24).pack(side=tk.LEFT, padx=6)
        self.merge_include_assets_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(merge, text="include .assets", variable=self.merge_include_assets_var).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(merge, text="Alpha:").pack(side=tk.LEFT, padx=(12, 0))
        self.merge_alpha_mode_var = tk.StringVar(value="preserve")
        ttk.Combobox(
            merge,
            textvariable=self.merge_alpha_mode_var,
            values=["preserve", "keep", "opaque"],
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(merge, text="Rebuild Merge", style="Accent.TButton", command=self.do_merge_runtime).pack(side=tk.LEFT, padx=8)
        ttk.Button(merge, text="Clean Merge", command=self.do_clean_merged).pack(side=tk.LEFT)

    def _browse_game_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.game_var.get() or "/")
        if selected:
            self.game_var.set(selected)
            self._save_settings()
            self.refresh_mods()

    def _on_mod_select(self, _event=None) -> None:
        sel = self.mods_tree.selection()
        if not sel:
            self._selected_mod = None
            return
        values = self.mods_tree.item(sel[0], "values")
        self._selected_mod = str(values[0])
        self.sel_priority_var.set(str(values[2]))

    def _mod_name(self) -> str:
        name = self.mod_name_var.get().strip()
        if not name:
            raise ValueError("Mod name is empty.")
        return name

    def _game_dir(self) -> str:
        game = self.game_var.get().strip()
        if not game:
            raise ValueError("Game directory is empty.")
        self._save_settings()
        return game

    def do_export(self) -> None:
        try:
            game = self._game_dir()
            mod = self._mod_name()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return

        try:
            cmd = self._cli_cmd("export", "--game-dir", game, "--mod", mod, "--scope", self.scope_var.get())
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        if self.filter_var.get().strip():
            cmd += ["--name-filter", self.filter_var.get().strip()]
        if self.force_export_var.get():
            cmd.append("--force")
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_export_models(self) -> None:
        try:
            game = self._game_dir()
            mod = self._mod_name()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return

        try:
            cmd = self._cli_cmd("export-models", "--game-dir", game, "--mod", mod, "--scope", self.scope_var.get())
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        if self.filter_var.get().strip():
            cmd += ["--name-filter", self.filter_var.get().strip()]
        if self.force_export_var.get():
            cmd.append("--force")
        self.run_command(cmd)

    def do_package(self) -> None:
        try:
            game = self._game_dir()
            mod = self._mod_name()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return

        try:
            cmd = self._cli_cmd(
                "package",
                "--game-dir",
                game,
                "--mod",
                mod,
                "--alpha-mode",
                self.package_alpha_mode_var.get().strip() or "preserve",
            )
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        priority = self.priority_var.get().strip()
        if priority:
            cmd += ["--priority", priority]
        if self.package_force_var.get():
            cmd.append("--force")
        if not self.include_assets_var.get():
            cmd.append("--bundles-only")
        if self.archive_deltas_var.get():
            cmd.append("--archive-deltas")
        if self.archive_only_var.get() and not self.archive_deltas_var.get():
            messagebox.showerror("Error", "Archive only requires Archive changed textures.")
            return
        if self.archive_only_var.get():
            cmd.append("--archive-only")
        if self.prune_archived_var.get():
            cmd.append("--prune-archived")
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_build_loader(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        if IS_FROZEN:
            messagebox.showinfo("Info", "Build Loader is a developer action and is not available in packaged builds.")
            return
        if sys.platform == "win32":
            messagebox.showinfo("Info", "Build Loader is currently provided by the Linux build script only.")
            return
        if not BUILD_SCRIPT.exists():
            messagebox.showerror("Error", f"Build script not found: {BUILD_SCRIPT}")
            return
        cmd = [str(BUILD_SCRIPT), game]
        self.run_command(cmd)

    def do_install_loader(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        try:
            cmd = self._cli_cmd("install-loader", "--game-dir", game)
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        if self.loader_force_var.get():
            cmd.append("--force")
        self.run_command(cmd)

    def open_texture_folder(self) -> None:
        try:
            folder = Path(self._game_dir()) / "Mods" / self._mod_name() / "textures"
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        folder.mkdir(parents=True, exist_ok=True)
        open_path(folder)

    def open_bepinex_log(self) -> None:
        try:
            game = Path(self._game_dir())
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        candidates = [
            game / "BepInEx" / "LogOutput.log",
            game / "BepInEx" / "LogOutput.txt",
        ]
        log = next((p for p in candidates if p.exists()), None)
        if log is None:
            messagebox.showinfo("Info", "No BepInEx log found.")
            return
        open_path(log)

    def refresh_mods(self) -> None:
        try:
            game = self._game_dir()
        except ValueError:
            return

        try:
            cmd = self._cli_cmd("status", "--game-dir", game, "--json")
        except RuntimeError as exc:
            self._log(f"[mods] refresh failed: {exc}")
            return
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
            payload = json.loads(proc.stdout)
        except Exception as exc:
            self._log(f"[mods] refresh failed: {exc}")
            return

        for item in self.mods_tree.get_children():
            self.mods_tree.delete(item)
        for mod in payload.get("mods", []):
            self.mods_tree.insert(
                "",
                tk.END,
                values=(
                    mod.get("mod", ""),
                    str(mod.get("enabled", True)),
                    str(mod.get("priority", 0)),
                    str(mod.get("entries", 0)),
                    str(mod.get("has_overrides_map", False)),
                ),
            )

    def set_selected_mod(self, enabled: bool) -> None:
        if not self._selected_mod:
            messagebox.showinfo("Info", "Select a mod from the list first.")
            return
        try:
            cmd = self._cli_cmd(
                "set-mod",
                "--game-dir",
                self._game_dir(),
                "--mod",
                self._selected_mod,
                "--enabled",
                "true" if enabled else "false",
            )
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.run_command(cmd, on_done=self.refresh_mods)

    def set_selected_mod_priority(self) -> None:
        if not self._selected_mod:
            messagebox.showinfo("Info", "Select a mod from the list first.")
            return
        try:
            cmd = self._cli_cmd(
                "set-mod",
                "--game-dir",
                self._game_dir(),
                "--mod",
                self._selected_mod,
                "--priority",
                self.sel_priority_var.get().strip() or "0",
            )
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_merge_runtime(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        output_mod = self.merge_output_mod_var.get().strip() or "_runtime_merged"
        try:
            cmd = self._cli_cmd(
                "merge-runtime",
                "--game-dir",
                game,
                "--output-mod",
                output_mod,
                "--alpha-mode",
                self.merge_alpha_mode_var.get().strip() or "preserve",
            )
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        if not self.merge_include_assets_var.get():
            cmd.append("--bundles-only")
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_clean_merged(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Error", str(exc))
            return
        output_mod = self.merge_output_mod_var.get().strip() or "_runtime_merged"
        try:
            cmd = self._cli_cmd(
                "clean-merged",
                "--game-dir",
                game,
                "--output-mod",
                output_mod,
            )
        except RuntimeError as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.run_command(cmd, on_done=self.refresh_mods)

    def run_command(self, cmd: list[str], on_done=None) -> None:
        if self._busy:
            messagebox.showinfo("Please wait", "A command is already running.")
            return
        self._busy = True
        self._log("$ " + " ".join(cmd))

        def worker() -> None:
            rc = -1
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(BIN_ROOT if IS_FROZEN else ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.after(0, self._log, line.rstrip("\n"))
                rc = proc.wait()
            except Exception as exc:
                self.after(0, self._log, f"[error] {exc}")
                rc = 1
            finally:
                def done():
                    self._busy = False
                    self._log(f"[exit] {rc}")
                    if on_done:
                        on_done()
                self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def _log(self, text: str) -> None:
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
