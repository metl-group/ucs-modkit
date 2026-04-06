#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parent
CLI = ROOT / "ucs_modkit.py"
BUILD_SCRIPT = ROOT / "build_modloader.sh"
DEFAULT_GAME = "/mnt/4TBN/SteamLibrary/steamapps/common/Used Cars Simulator"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("UCS Modkit Studio")
        self.geometry("1180x760")
        self.minsize(980, 640)

        self._busy = False
        self._selected_mod: str | None = None

        self._setup_style()
        self._build_ui()
        self.refresh_mods()

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Title.TLabel", font=("DejaVu Sans", 14, "bold"))
        style.configure("Hint.TLabel", foreground="#4a5568")
        style.configure("Accent.TButton", padding=(10, 6))

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self, padding=10)
        root_frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(root_frame)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top, text="UCS Modkit Studio", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            top,
            text="Modloader + Modmaker + GUI fuer Used Cars Simulator",
            style="Hint.TLabel",
        ).pack(side=tk.LEFT, padx=(12, 0))

        path_row = ttk.Frame(root_frame)
        path_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(path_row, text="Game Dir:").pack(side=tk.LEFT)
        self.game_var = tk.StringVar(value=DEFAULT_GAME)
        ttk.Entry(path_row, textvariable=self.game_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(path_row, text="Browse", command=self._browse_game_dir).pack(side=tk.LEFT)

        notebook = ttk.Notebook(root_frame)
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

        log_frame = ttk.LabelFrame(root_frame, text="Log", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=False, pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=12, wrap=tk.WORD, font=("DejaVu Sans Mono", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self._log("Bereit.")

    def _build_modmaker_tab(self) -> None:
        frame = self.tab_modmaker
        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=4)
        ttk.Label(row1, text="Mod Name:").pack(side=tk.LEFT)
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
        self.force_export_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Export --force", variable=self.force_export_var).pack(side=tk.LEFT)
        self.package_force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Package --force", variable=self.package_force_var).pack(side=tk.LEFT, padx=(12, 0))
        self.include_assets_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Package include .assets", variable=self.include_assets_var).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(row2, text="Priority:").pack(side=tk.LEFT, padx=(12, 0))
        self.priority_var = tk.StringVar(value="0")
        ttk.Entry(row2, textvariable=self.priority_var, width=6).pack(side=tk.LEFT, padx=6)

        row3 = ttk.Frame(frame)
        row3.pack(fill=tk.X, pady=8)
        ttk.Button(row3, text="1) Export Textures", style="Accent.TButton", command=self.do_export).pack(side=tk.LEFT)
        ttk.Button(row3, text="2) Open Texture Folder", command=self.open_texture_folder).pack(side=tk.LEFT, padx=8)
        ttk.Button(row3, text="3) Package Runtime Overrides", style="Accent.TButton", command=self.do_package).pack(side=tk.LEFT)

    def _build_loader_tab(self) -> None:
        frame = self.tab_loader
        info = (
            "Installiert BepInEx + den UCS Bundle Overlay Loader.\n"
            "Der Loader sucht pro Bundle zuerst in Mods/<mod>/overrides/..."
        )
        ttk.Label(frame, text=info, style="Hint.TLabel").pack(anchor=tk.W, pady=(0, 8))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=4)
        self.loader_force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Force overwrite", variable=self.loader_force_var).pack(side=tk.LEFT)
        ttk.Button(row, text="Build Loader", style="Accent.TButton", command=self.do_build_loader).pack(side=tk.LEFT, padx=8)
        ttk.Button(row, text="Install Loader", style="Accent.TButton", command=self.do_install_loader).pack(side=tk.LEFT, padx=8)
        ttk.Button(row, text="Open BepInEx Log", command=self.open_bepinex_log).pack(side=tk.LEFT, padx=8)

        launch = ttk.LabelFrame(frame, text="Steam Launch Option (Linux/Proton)", padding=8)
        launch.pack(fill=tk.X, pady=(12, 4))
        ttk.Label(launch, text='WINEDLLOVERRIDES="winhttp=n,b" %command%').pack(anchor=tk.W)

    def _build_mods_tab(self) -> None:
        frame = self.tab_mods
        cols = ("mod", "enabled", "priority", "entries", "map")
        self.mods_tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        self.mods_tree.heading("mod", text="Mod")
        self.mods_tree.heading("enabled", text="Enabled")
        self.mods_tree.heading("priority", text="Priority")
        self.mods_tree.heading("entries", text="Entries")
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
        self.merge_include_assets_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(merge, text="include .assets", variable=self.merge_include_assets_var).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(merge, text="Rebuild Merge", style="Accent.TButton", command=self.do_merge_runtime).pack(side=tk.LEFT, padx=8)
        ttk.Button(merge, text="Clean Merge", command=self.do_clean_merged).pack(side=tk.LEFT)

    def _browse_game_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.game_var.get() or "/")
        if selected:
            self.game_var.set(selected)
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
            raise ValueError("Mod Name ist leer.")
        return name

    def _game_dir(self) -> str:
        game = self.game_var.get().strip()
        if not game:
            raise ValueError("Game Dir ist leer.")
        return game

    def do_export(self) -> None:
        try:
            game = self._game_dir()
            mod = self._mod_name()
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return

        cmd = [sys.executable, str(CLI), "export", "--game-dir", game, "--mod", mod, "--scope", self.scope_var.get()]
        if self.filter_var.get().strip():
            cmd += ["--name-filter", self.filter_var.get().strip()]
        if self.force_export_var.get():
            cmd.append("--force")
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_package(self) -> None:
        try:
            game = self._game_dir()
            mod = self._mod_name()
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return

        cmd = [sys.executable, str(CLI), "package", "--game-dir", game, "--mod", mod, "--priority", self.priority_var.get().strip() or "0"]
        if self.package_force_var.get():
            cmd.append("--force")
        if self.include_assets_var.get():
            cmd.append("--include-assets")
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_build_loader(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return
        cmd = [str(BUILD_SCRIPT), game]
        self.run_command(cmd)

    def do_install_loader(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return
        cmd = [sys.executable, str(CLI), "install-loader", "--game-dir", game, "--build"]
        if self.loader_force_var.get():
            cmd.append("--force")
        self.run_command(cmd)

    def open_texture_folder(self) -> None:
        try:
            folder = Path(self._game_dir()) / "Mods" / self._mod_name() / "textures"
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(folder)])

    def open_bepinex_log(self) -> None:
        try:
            game = Path(self._game_dir())
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return
        candidates = [
            game / "BepInEx" / "LogOutput.log",
            game / "BepInEx" / "LogOutput.txt",
        ]
        log = next((p for p in candidates if p.exists()), None)
        if log is None:
            messagebox.showinfo("Info", "Kein BepInEx Log gefunden.")
            return
        subprocess.Popen(["xdg-open", str(log)])

    def refresh_mods(self) -> None:
        try:
            game = self._game_dir()
        except ValueError:
            return

        cmd = [sys.executable, str(CLI), "status", "--game-dir", game, "--json"]
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
            messagebox.showinfo("Info", "Bitte zuerst eine Mod in der Liste auswaehlen.")
            return
        cmd = [
            sys.executable,
            str(CLI),
            "set-mod",
            "--game-dir",
            self._game_dir(),
            "--mod",
            self._selected_mod,
            "--enabled",
            "true" if enabled else "false",
        ]
        self.run_command(cmd, on_done=self.refresh_mods)

    def set_selected_mod_priority(self) -> None:
        if not self._selected_mod:
            messagebox.showinfo("Info", "Bitte zuerst eine Mod in der Liste auswaehlen.")
            return
        cmd = [
            sys.executable,
            str(CLI),
            "set-mod",
            "--game-dir",
            self._game_dir(),
            "--mod",
            self._selected_mod,
            "--priority",
            self.sel_priority_var.get().strip() or "0",
        ]
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_merge_runtime(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return
        output_mod = self.merge_output_mod_var.get().strip() or "_runtime_merged"
        cmd = [
            sys.executable,
            str(CLI),
            "merge-runtime",
            "--game-dir",
            game,
            "--output-mod",
            output_mod,
        ]
        if self.merge_include_assets_var.get():
            cmd.append("--include-assets")
        self.run_command(cmd, on_done=self.refresh_mods)

    def do_clean_merged(self) -> None:
        try:
            game = self._game_dir()
        except ValueError as exc:
            messagebox.showerror("Fehler", str(exc))
            return
        output_mod = self.merge_output_mod_var.get().strip() or "_runtime_merged"
        cmd = [
            sys.executable,
            str(CLI),
            "clean-merged",
            "--game-dir",
            game,
            "--output-mod",
            output_mod,
        ]
        self.run_command(cmd, on_done=self.refresh_mods)

    def run_command(self, cmd: list[str], on_done=None) -> None:
        if self._busy:
            messagebox.showinfo("Bitte warten", "Es laeuft bereits ein Befehl.")
            return
        self._busy = True
        self._log("$ " + " ".join(cmd))

        def worker() -> None:
            rc = -1
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
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
