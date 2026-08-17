#!/usr/bin/env python3
"""
project_cleaner_gui.py
----------------------
GUI app to scan a folder and clean:
  • Rust projects  -> target/          (prefers `cargo clean`)
  • CMake projects -> build/ + files  (file deletion — no CLI clean exists)
  • Go projects    -> bin/, pkg/       (prefers `go clean`)

Strategy:
  1. Try the language's own CLI clean command first.
  2. If it fails or the tool isn't installed, fall back to
     deleting the files/directories directly.
  3. Hardened deletion: clears read-only flags (like Git pack files)
     and retries on transient Windows file locks (AV, OneDrive, etc.).

Run:
    python project_cleaner_gui.py
"""

from __future__ import annotations

import os
import queue
import shutil
import stat
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RUST_BUILD_DIRS  = {"target"}
CMAKE_BUILD_DIRS = {"build", "Build", "BUILD", "_build", "cmake-build-debug",
                    "cmake-build-release", "out"}
GO_BUILD_DIRS    = {"bin", "pkg"}

CMAKE_GENERATED_FILES = {
    "CMakeCache.txt",
    "cmake_install.cmake",
    "compile_commands.json",
    "CTestTestfile.cmake",
    "DartConfiguration.tcl",
}
CMAKE_GENERATED_DIRS = {"CMakeFiles", "Testing", "Depend", "CompilerId"}

MAX_DEPTH_DEFAULT = 8
CLI_TIMEOUT_SEC   = 120


# ---------------------------------------------------------------------------
# Hardened Deletion Helpers
# ---------------------------------------------------------------------------

def _force_rmtree(path: str, retries: int = 3, delay: float = 0.4) -> None:
    """
    Like shutil.rmtree, but:
      • clears the read-only bit on every git pack/loose object
      • retries a few times to outwait AV / search indexer locks
    """
    def on_error(func, p, _exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
        except OSError:
            pass
        try:
            func(p)
        except FileNotFoundError:
            pass
        except OSError:
            raise

    last_err = None
    for attempt in range(retries):
        try:
            shutil.rmtree(path, onerror=on_error)
            return
        except OSError as e:
            last_err = e
            # WinError 5 / 32 (in use) / 33 (sharing violation) — retry
            time.sleep(delay * (attempt + 1))
    raise last_err


def _force_remove_file(path: str, retries: int = 3, delay: float = 0.3) -> None:
    """
    Remove a file, clearing read-only attributes and retrying on transient locks.
    """
    for attempt in range(retries):
        try:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(delay * (attempt + 1))
    # Final attempt, let it raise if it fails
    try:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CleanItem:
    path: Path
    kind: str              # "Rust" | "CMake" | "Go"
    is_dir: bool           # plain bool — never call with ()
    size: int = 0
    project_root: Path | None = None   # dir containing Cargo.toml / go.mod /
                                       # CMakeLists.txt


@dataclass
class CleanPlan:
    items: list[CleanItem] = field(default_factory=list)

    def total_items(self) -> int:
        return len(self.items)

    def total_size(self) -> int:
        return sum(i.size for i in self.items)


# ---------------------------------------------------------------------------
# Scanning helpers
# ---------------------------------------------------------------------------

def dir_size(p: Path) -> int:
    total = 0
    try:
        for root, _, files in os.walk(p):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def is_rust_project(p: Path)  -> bool: return (p / "Cargo.toml").is_file()
def is_cmake_project(p: Path) -> bool: return (p / "CMakeLists.txt").is_file()
def is_go_project(p: Path)    -> bool: return (p / "go.mod").is_file()


def scan(root: Path, max_depth: int = MAX_DEPTH_DEFAULT) -> CleanPlan:
    plan = CleanPlan()
    seen_dirs: set[Path] = set()

    def recurse(folder: Path, depth: int):
        if depth > max_depth:
            return
        try:
            entries = list(folder.iterdir())
        except (PermissionError, OSError):
            return

        is_rust  = is_rust_project(folder)
        is_cmake = is_cmake_project(folder)
        is_go    = is_go_project(folder)

        for entry in entries:
            if entry.is_symlink():
                continue

            if entry.is_dir():
                name = entry.name

                if is_rust and name in RUST_BUILD_DIRS:
                    plan.items.append(CleanItem(
                        entry, "Rust", True, dir_size(entry),
                        project_root=folder))
                    continue
                if is_cmake and name in CMAKE_BUILD_DIRS:
                    plan.items.append(CleanItem(
                        entry, "CMake", True, dir_size(entry),
                        project_root=folder))
                    continue
                if is_cmake and name in CMAKE_GENERATED_DIRS:
                    plan.items.append(CleanItem(
                        entry, "CMake", True, dir_size(entry),
                        project_root=folder))
                    continue
                if is_go and name in GO_BUILD_DIRS:
                    plan.items.append(CleanItem(
                        entry, "Go", True, dir_size(entry),
                        project_root=folder))
                    continue

                real = entry.resolve()
                if real in seen_dirs:
                    continue
                seen_dirs.add(real)
                recurse(entry, depth + 1)

            elif entry.is_file():
                if is_cmake and entry.name in CMAKE_GENERATED_FILES:
                    try:
                        sz = entry.stat().st_size
                    except OSError:
                        sz = 0
                    plan.items.append(CleanItem(
                        entry, "CMake", False, sz,
                        project_root=folder))

    recurse(root, 0)
    return plan


def fmt_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------

class ProjectCleanerApp:
    PADDING = 8

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Project Cleaner")
        self.root.geometry("1000x700")
        self.root.minsize(780, 540)

        self.plan: CleanPlan | None = None
        self.checked: dict[int, bool] = {}
        self.scanning = False
        self.deleting = False
        self.msg_q: queue.Queue[tuple[str, dict]] = queue.Queue()

        # Toggle options
        self.auto_delete_var  = tk.BooleanVar(value=True)
        self.skip_confirm_var = tk.BooleanVar(value=False)
        self.use_cli_var      = tk.BooleanVar(value=True)
        self.keep_deps_var    = tk.BooleanVar(value=False)

        # Cache which CLI tools are available
        self._has_cargo = shutil.which("cargo") is not None
        self._has_go    = shutil.which("go") is not None

        self._build_ui()
        self._poll_queue()

    # --------------------------------------------------------- CLI helpers
    def _clean_method_for(self, item: CleanItem) -> str:
        """Return a short label for the tree's 'Method' column."""
        if not self.use_cli_var.get():
            return "delete"
        if item.kind == "Rust" and self._has_cargo:
            return "cargo"
        if item.kind == "Go" and self._has_go:
            return "go"
        return "delete"

    def _try_cargo_clean(self, project_root: Path) -> bool:
        """Run `cargo clean` in project_root. Returns True on success."""
        try:
            self.msg_q.put(("log", {"msg":
                f"  → cargo clean  (cwd: {project_root})"}))
            rc = subprocess.run(
                ["cargo", "clean"],
                cwd=str(project_root),
                capture_output=True, text=True,
                timeout=CLI_TIMEOUT_SEC,
            )
            if rc.returncode == 0:
                self.msg_q.put(("log", {"msg":
                    f"  ✓ cargo clean succeeded"}))
                return True
            else:
                stderr = (rc.stderr or "").strip()[:300]
                self.msg_q.put(("log", {"msg":
                    f"  ⚠ cargo clean failed (rc={rc.returncode})"
                    + (f": {stderr}" if stderr else "")}))
                return False
        except subprocess.TimeoutExpired:
            self.msg_q.put(("log", {"msg":
                "  ⚠ cargo clean timed out"}))
            return False
        except FileNotFoundError:
            self.msg_q.put(("log", {"msg":
                "  ⚠ cargo not found"}))
            return False
        except Exception as e:
            self.msg_q.put(("log", {"msg":
                f"  ⚠ cargo clean error: {e}"}))
            return False

    def _try_go_clean(self, project_root: Path) -> bool:
        """Run `go clean` in project_root. Returns True on success."""
        try:
            self.msg_q.put(("log", {"msg":
                f"  → go clean  (cwd: {project_root})"}))
            rc = subprocess.run(
                ["go", "clean"],
                cwd=str(project_root),
                capture_output=True, text=True,
                timeout=CLI_TIMEOUT_SEC,
            )
            if rc.returncode == 0:
                self.msg_q.put(("log", {"msg":
                    f"  ✓ go clean succeeded"}))
                return True
            else:
                stderr = (rc.stderr or "").strip()[:300]
                self.msg_q.put(("log", {"msg":
                    f"  ⚠ go clean failed (rc={rc.returncode})"
                    + (f": {stderr}" if stderr else "")}))
                return False
        except subprocess.TimeoutExpired:
            self.msg_q.put(("log", {"msg":
                "  ⚠ go clean timed out"}))
            return False
        except FileNotFoundError:
            self.msg_q.put(("log", {"msg":
                "  ⚠ go not found"}))
            return False
        except Exception as e:
            self.msg_q.put(("log", {"msg":
                f"  ⚠ go clean error: {e}"}))
            return False

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = self.PADDING
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Top bar
        top = ttk.Frame(self.root, padding=pad)
        top.pack(fill="x")

        ttk.Label(top, text="Folder:").pack(side="left")
        self.folder_var = tk.StringVar(value="(no folder selected)")
        ttk.Label(top, textvariable=self.folder_var,
                  width=60, relief="sunken", anchor="w").pack(
                  side="left", padx=(4, 8), fill="x", expand=True)

        self.btn_pick = ttk.Button(top, text="Pick Folder…",
                                   command=self.on_pick_folder)
        self.btn_pick.pack(side="left", padx=2)

        self.btn_scan = ttk.Button(top, text="Scan", state="disabled",
                                   command=self.on_scan)
        self.btn_scan.pack(side="left", padx=2)

        # Options row
        opts = ttk.Frame(self.root, padding=(pad, 0))
        opts.pack(fill="x")

        ttk.Checkbutton(opts, text="Auto-delete after scan",
                        variable=self.auto_delete_var).pack(
            side="left", padx=(0, 12))
        ttk.Checkbutton(opts, text="Skip confirmation",
                        variable=self.skip_confirm_var).pack(
            side="left", padx=(0, 12))
        ttk.Checkbutton(opts, text="Use CLI commands (cargo / go)",
                        variable=self.use_cli_var,
                        command=self.render_tree).pack(
            side="left", padx=(0, 12))
        ttk.Checkbutton(opts, text="Keep CMake _deps/",
                        variable=self.keep_deps_var).pack(
            side="left", padx=(0, 12))

        # Show tool availability
        tool_info = []
        if self._has_cargo:
            tool_info.append("cargo ✓")
        else:
            tool_info.append("cargo ✗")
        if self._has_go:
            tool_info.append("go ✓")
        else:
            tool_info.append("go ✗")
        ttk.Label(opts, text="  |  ".join(tool_info),
                  foreground="#555555").pack(side="left", padx=(8, 0))

        ttk.Separator(opts, orient="vertical").pack(
            side="left", fill="y", padx=8)
        ttk.Label(opts, text="Show:").pack(side="left")
        self.filter_var = tk.StringVar(value="All")
        for label in ("All", "Rust", "CMake", "Go"):
            ttk.Radiobutton(opts, text=label, value=label,
                            variable=self.filter_var,
                            command=self.apply_filter).pack(
                side="left", padx=4)
        ttk.Separator(opts, orient="vertical").pack(
            side="left", fill="y", padx=8)
        ttk.Button(opts, text="Select All",
                   command=self.select_all).pack(side="left", padx=2)
        ttk.Button(opts, text="Deselect All",
                   command=self.deselect_all).pack(side="left", padx=2)
        ttk.Button(opts, text="Invert",
                   command=self.invert).pack(side="left", padx=2)

        # Tree
        tree_frame = ttk.Frame(self.root, padding=(pad, pad, pad, 0))
        tree_frame.pack(fill="both", expand=True)

        cols = ("check", "kind", "method", "path", "size", "type")
        self.tree = ttk.Treeview(tree_frame, columns=cols,
                                 show="headings", selectmode="none")
        self.tree.heading("check",  text="Del?")
        self.tree.heading("kind",   text="Project")
        self.tree.heading("method", text="Method")
        self.tree.heading("path",   text="Path")
        self.tree.heading("size",   text="Size")
        self.tree.heading("type",   text="Type")

        self.tree.column("check",  width=40,  anchor="center",
                         stretch=False)
        self.tree.column("kind",   width=60,  anchor="center",
                         stretch=False)
        self.tree.column("method", width=70,  anchor="center",
                         stretch=False)
        self.tree.column("path",   width=480, anchor="w",
                         stretch=True)
        self.tree.column("size",   width=80,  anchor="e",
                         stretch=False)
        self.tree.column("type",   width=50,  anchor="center",
                         stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.tag_configure("checked",   foreground="#1a7a1a")
        self.tree.tag_configure("unchecked", foreground="#666666")
        self.tree.tag_configure("Rust",  background="#fff7e6")
        self.tree.tag_configure("CMake", background="#eef6ff")
        self.tree.tag_configure("Go",    background="#f0fff4")

        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<space>",   self.on_tree_space)

        # Status + progress
        bottom = ttk.Frame(self.root, padding=pad)
        bottom.pack(fill="x", side="bottom")

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.status_var,
                  anchor="w").pack(fill="x")

        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 0))

        self.btn_delete = ttk.Button(bottom, text="Delete Selected",
                                     state="disabled",
                                     command=self.on_delete)
        self.btn_delete.pack(side="right", pady=(4, 0))

        # Log
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=pad)
        log_frame.pack(fill="x", side="bottom", padx=pad, pady=pad)

        self.log = tk.Text(log_frame, height=7, state="disabled",
                          font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    # ----------------------------------------------------------- utilities
    def log_msg(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_status(self, msg: str) -> None:
        self.status_var.set(msg)

    def set_progress(self, value: float) -> None:
        self.progress["value"] = value

    # ----------------------------------------------------------- pick folder
    def on_pick_folder(self) -> None:
        folder = filedialog.askdirectory(title="Pick a folder to scan")
        if not folder:
            return
        self.folder_var.set(folder)
        self.btn_scan.config(state="normal")
        self.btn_delete.config(state="disabled")
        self.plan = None
        self.checked.clear()
        self.tree.delete(*self.tree.get_children())
        self.set_status(f"Folder selected: {folder}")
        self.log_msg(f"Selected folder: {folder}")

    # ----------------------------------------------------------------- scan
    def on_scan(self) -> None:
        if self.scanning or self.deleting:
            return
        folder_str = self.folder_var.get()
        if not folder_str or folder_str == "(no folder selected)":
            return
        folder = Path(folder_str)
        if not folder.is_dir():
            messagebox.showerror("Error", f"Not a directory:\n{folder}")
            return

        self.scanning = True
        self.btn_scan.config(state="disabled")
        self.btn_pick.config(state="disabled")
        self.btn_delete.config(state="disabled")
        self.set_status("Scanning…")
        self.set_progress(0)
        self.tree.delete(*self.tree.get_children())
        self.plan = None
        self.checked.clear()

        t = threading.Thread(target=self._scan_worker, args=(folder,),
                             daemon=True)
        t.start()

    def _scan_worker(self, folder: Path) -> None:
        try:
            plan = scan(folder)
            self.msg_q.put(("scan_done", {"plan": plan}))
        except Exception as e:
            self.msg_q.put(("scan_error", {"error": str(e)}))

    # ---------------------------------------------------------- tree render
    def render_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if not self.plan:
            return
        filt = self.filter_var.get()
        for i, item in enumerate(self.plan.items):
            if filt != "All" and item.kind != filt:
                continue
            checked = self.checked.get(i, True)
            checkmark = "☑" if checked else "☐"
            method   = self._clean_method_for(item)
            tags = [item.kind, "checked" if checked else "unchecked"]
            self.tree.insert("", "end", iid=str(i),
                             values=(checkmark, item.kind, method,
                                     str(item.path),
                                     fmt_bytes(item.size),
                                     "Dir" if item.is_dir else "File"),
                             tags=tags)
        self.update_selection_summary()

    def apply_filter(self) -> None:
        self.render_tree()

    def visible_indices(self) -> list[int]:
        if not self.plan:
            return []
        filt = self.filter_var.get()
        return [i for i, it in enumerate(self.plan.items)
                if filt == "All" or it.kind == filt]

    # ----------------------------------------------------------- selection
    def select_all(self) -> None:
        for i in self.visible_indices():
            self.checked[i] = True
        self.render_tree()

    def deselect_all(self) -> None:
        for i in self.visible_indices():
            self.checked[i] = False
        self.render_tree()

    def invert(self) -> None:
        for i in self.visible_indices():
            self.checked[i] = not self.checked.get(i, True)
        self.render_tree()

    def update_selection_summary(self) -> None:
        if not self.plan:
            return
        sel = [self.plan.items[i]
               for i, c in self.checked.items() if c]
        n = len(sel)
        sz = sum(it.size for it in sel)
        self.set_status(
            f"{n}/{len(self.plan.items)} selected — "
            f"{fmt_bytes(sz)} / {fmt_bytes(self.plan.total_size())}"
        )
        self.btn_delete.config(state="normal" if n > 0 else "disabled")

    # ------------------------------------------------------- click handling
    def on_tree_click(self, event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        col = self.tree.identify_column(event.x)
        if region != "cell" or col != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        idx = int(iid)
        self.checked[idx] = not self.checked.get(idx, True)
        self.render_tree()

    def on_tree_space(self, _event) -> None:
        iid = self.tree.focus()
        if not iid:
            return
        idx = int(iid)
        self.checked[idx] = not self.checked.get(idx, True)
        self.render_tree()

    # -------------------------------------------------------------- delete
    def on_delete(self) -> None:
        if not self.plan or self.deleting:
            return
        sel = [self.plan.items[i]
               for i, c in self.checked.items() if c]
        if not sel:
            return
        if not self.skip_confirm_var.get():
            if not messagebox.askyesno(
                "Confirm",
                f"Delete {len(sel)} selected item(s)?\n"
                f"Total: {fmt_bytes(sum(i.size for i in sel))}\n\n"
                "This cannot be undone."):
                return

        self._start_deletion(sel, log_prefix="Deleting")

    def _start_deletion(self, sel: list[CleanItem],
                        log_prefix: str = "Deleting") -> None:
        self.deleting = True
        self.btn_delete.config(state="disabled")
        self.btn_scan.config(state="disabled")
        self.btn_pick.config(state="disabled")
        self.progress.config(mode="determinate")
        self.set_progress(0)
        self.log_msg(f"--- {log_prefix} {len(sel)} item(s) ---")

        t = threading.Thread(target=self._delete_worker, args=(sel,),
                             daemon=True)
        t.start()

    def _delete_worker(self, items: list[CleanItem]) -> None:
        """
        Delete items in a background thread.

        Strategy:
          1. Group items by (project_root, kind).
          2. For each group, try the language CLI command first
             (cargo clean / go clean). CMake has no CLI clean — skip.
          3. After the CLI command (or if it was skipped), check which
             artifacts still exist on disk and delete them directly.
        """
        total = len(items)
        freed = 0
        done = 0

        # --- Group by project -------------------------------------------
        # Key: (project_root_str, kind)
        groups: dict[tuple[str, str], list[CleanItem]] = {}
        for item in items:
            root_str = str(item.project_root) \
                if item.project_root else ""
            key = (root_str, item.kind)
            groups.setdefault(key, []).append(item)

        # --- Process each group -----------------------------------------
        for (root_str, kind), group_items in groups.items():
            project_root = Path(root_str) if root_str else None
            cli_succeeded = False

            # --- Try CLI command first ---
            if self.use_cli_var.get() and project_root:
                if kind == "Rust" and self._has_cargo:
                    self.log_msg(f"[Rust] Cleaning project: {project_root}")
                    cli_succeeded = self._try_cargo_clean(project_root)

                elif kind == "Go" and self._has_go:
                    self.log_msg(f"[Go] Cleaning project: {project_root}")
                    cli_succeeded = self._try_go_clean(project_root)

                elif kind == "CMake":
                    self.msg_q.put(("log", {"msg":
                        "[CMake] No CLI clean available — "
                        "deleting files directly"}))

            # --- Delete remaining artifacts via file deletion ---
            for item in group_items:
                path_str = str(item.path)
                path_obj = Path(path_str)

                # Check if the path still exists
                # (CLI command may have already removed it)
                if not os.path.exists(path_str):
                    freed += item.size
                    done += 1
                    if cli_succeeded:
                        self.msg_q.put(("log", {"msg":
                            f"  ✓ cleaned via CLI: {path_str}"}))
                    else:
                        self.msg_q.put(("log", {"msg":
                            f"  • already gone: {path_str}"}))
                    self.msg_q.put(("progress", {
                        "value":  done / total * 100,
                        "status": f"Deleting {done}/{total}…",
                    }))
                    continue

                # If it's a CMake build directory and user wants to keep _deps
                if (self.keep_deps_var.get() and 
                    item.kind == "CMake" and 
                    item.is_dir and 
                    (path_obj / "_deps").is_dir()):
                    
                    self.msg_q.put(("log", {"msg":
                        f"  ↳ cleaning build but keeping _deps: {path_str}"}))
                    try:
                        for child in path_obj.iterdir():
                            if child.name == "_deps":
                                continue
                            try:
                                if child.is_dir():
                                    _force_rmtree(str(child))
                                else:
                                    _force_remove_file(str(child))
                            except Exception as e:
                                self.msg_q.put(("log", {"msg":
                                    f"  ✗ failed: {child} ({e})"}))
                        
                        self.msg_q.put(("log", {"msg":
                            f"  ✓ cleaned (kept _deps): {path_str}"}))
                        freed += item.size  # Approximate freed size
                    except Exception as e:
                        self.msg_q.put(("log", {"msg":
                            f"  ✗ failed: {path_str} ({e})"}))

                    done += 1
                    self.msg_q.put(("progress", {
                        "value":  done / total * 100,
                        "status": f"Deleting {done}/{total}…",
                    }))
                    continue

                # Path still exists — delete it directly
                try:
                    if item.is_dir:
                        _force_rmtree(path_str)
                    else:
                        _force_remove_file(path_str)

                    freed += item.size
                    self.msg_q.put(("log", {"msg":
                        f"  ✓ removed: {path_str}"}))

                except FileNotFoundError:
                    self.msg_q.put(("log", {"msg":
                        f"  • already gone: {path_str}"}))

                except PermissionError as e:
                    self.msg_q.put(("log", {"msg":
                        f"  ✗ permission denied: {path_str} ({e})"}))

                except Exception as e:
                    self.msg_q.put(("log", {"msg":
                        f"  ✗ failed: {path_str} ({e})"}))

                done += 1
                self.msg_q.put(("progress", {
                    "value":  done / total * 100,
                    "status": f"Deleting {done}/{total}…",
                }))

        self.msg_q.put(("delete_done",
                        {"freed": freed, "deleted": done}))

    # ----------------------------------------------------------- msg queue
    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "scan_done":
                    self._handle_scan_done(payload["plan"])
                elif kind == "scan_error":
                    self._handle_scan_error(payload["error"])
                elif kind == "log":
                    self.log_msg(payload["msg"])
                elif kind == "progress":
                    self.set_progress(payload["value"])
                    self.set_status(payload["status"])
                elif kind == "delete_done":
                    self._handle_delete_done(payload["freed"],
                                              payload["deleted"])
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _handle_scan_done(self, plan: CleanPlan) -> None:
        self.plan = plan
        for i in range(len(plan.items)):
            self.checked[i] = True
        self.scanning = False
        self.btn_pick.config(state="normal")
        self.btn_scan.config(state="normal")
        self.render_tree()

        if plan.total_items() == 0:
            self.set_status("Nothing to clean. ✨")
            self.log_msg("Scan finished — no build artifacts found.")
            self.set_progress(100)
            return

        self.set_status(
            f"Scan complete: {plan.total_items()} item(s), "
            f"{fmt_bytes(plan.total_size())}"
        )
        self.log_msg(f"Scan complete: {plan.total_items()} item(s), "
                     f"{fmt_bytes(plan.total_size())}")
        self.set_progress(100)

        # AUTO-DELETE FLOW
        if self.auto_delete_var.get():
            self.log_msg("Auto-delete enabled — starting deletion…")
            self.set_status("Auto-deleting…")
            self.root.after(400, self._auto_delete_kickoff)

    def _auto_delete_kickoff(self) -> None:
        """Trigger deletion automatically (called via after())."""
        if not self.plan or self.deleting:
            return
        sel = [self.plan.items[i]
               for i, c in self.checked.items() if c]
        if not sel:
            return

        if not self.skip_confirm_var.get():
            if not messagebox.askyesno(
                "Confirm auto-delete",
                f"Auto-delete {len(sel)} item(s)?\n"
                f"Total: {fmt_bytes(sum(i.size for i in sel))}\n\n"
                "Tip: tick 'Skip confirmation' to avoid this popup "
                "next time."):
                self.set_status("Auto-delete cancelled.")
                return

        self._start_deletion(sel, log_prefix="Auto-deleting")

    def _handle_scan_error(self, err: str) -> None:
        self.scanning = False
        self.btn_pick.config(state="normal")
        self.btn_scan.config(state="normal")
        self.set_status("Scan failed.")
        self.log_msg(f"Scan error: {err}")
        messagebox.showerror("Scan failed", err)

    def _handle_delete_done(self, freed: int, deleted: int) -> None:
        self.deleting = False
        self.btn_pick.config(state="normal")
        self.btn_scan.config(state="normal")
        self.log_msg(f"Deleted {deleted} item(s); reclaimed "
                     f"~{fmt_bytes(freed)}.")
        self.set_status("Done.")
        self.set_progress(100)
        
        # Re-scan to refresh tree (auto-delete disabled to avoid loop)
        prev_auto = self.auto_delete_var.get()
        if self.folder_var.get() != "(no folder selected)":
            self.auto_delete_var.set(False)  # Disable BEFORE on_scan
            self.on_scan()
            self.root.after(2000,
                            lambda: self.auto_delete_var.set(prev_auto))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    root = tk.Tk()
    ProjectCleanerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()