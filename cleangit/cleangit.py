import os
import subprocess
import threading
import queue
import shutil
import sys
import platform
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk

# ═══════════════════════════════════════════════════════════════════════════
#  Junk-detection patterns
# ═══════════════════════════════════════════════════════════════════════════

JUNK_FILE_EXTENSIONS = {
    # OS / editor temp
    '.tmp', '.temp', '.bak', '.swp', '.swo', '.orig', '.rej', '.old', '.save',
    # Python bytecode
    '.pyc', '.pyo', '.pyd',
    # Logs
    '.log',
    # Archives
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.iso',
    # Binaries / native libs
    '.exe', '.dll', '.so', '.dylib', '.bin', '.o', '.a', '.lib', '.obj',
    '.pdb', '.idb', '.ilk',
    # Video
    '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v',
    # Audio
    '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma', '.aiff',
    # Large design files
    '.psd', '.ai', '.sketch', '.tif', '.tiff',
    # Java artifacts
    '.class', '.war', '.ear',
}

JUNK_FILE_NAMES = {
    '.ds_store', 'thumbs.db', 'desktop.ini', '.directory',
    'cmakecache.txt', 'cmake_install.cmake',
    'npm-debug.log', 'yarn-error.log', 'yarn-debug.log',
}

JUNK_DIR_NAMES = {
    '__pycache__', '.pytest_cache', '.mypy_cache', '.tox', '.eggs',
    'node_modules', 'bower_components', '.sass-cache', '.parcel-cache',
    'dist', 'build', 'target', 'out', '.next', '.nuxt', '.svelte-kit',
    '.gradle', '.m2', '.idea', '.vs', 'cmakefiles',
    'htmlcov', '.coverage', 'coverage', '.cache', '.tscache',
    '.venv', 'venv', 'env',
}

# Folders to never descend into during a disk scan (besides .git)
SKIP_DIRS_DISK = {'.git', '$RECYCLE.BIN', 'System Volume Information', '.Trash'}

DEFAULT_LARGE_FILE_MB = 5
MAX_RESULTS = 5000  # safety cap to keep UI responsive

# Git configuration for auto-remote
GIT_USERNAME = "Entendore"
GIT_REMOTE_BASE = f"https://github.com/{GIT_USERNAME}"


# ═══════════════════════════════════════════════════════════════════════════
#  Utility helpers
# ═══════════════════════════════════════════════════════════════════════════

def format_size(size_bytes):
    """Human-readable file size."""
    size = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def git_available():
    """Check whether git is installed and on PATH."""
    try:
        subprocess.run(["git", "--version"],
                       capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def dir_size(path):
    """Total size of all files under *path* (non-recursive walk)."""
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path, followlinks=False):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


# ═══════════════════════════════════════════════════════════════════════════
#  Main application
# ═══════════════════════════════════════════════════════════════════════════

class GitCleanerApp:
    def __init__(self, root):
        self.root = root
        root.title("Git Repo & Disk Cleaner")
        root.geometry("980x800")
        root.minsize(700, 600)

        # Center window on screen
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f'+{x}+{y}')

        # Prevent Git from hanging the UI if it asks for credentials
        self.proc_env = os.environ.copy()
        self.proc_env["GIT_TERMINAL_PROMPT"] = "0"

        # ── State ──
        self.queue = queue.Queue()
        self.repo_path = tk.StringVar()
        self.found_repos = []
        self.scan_mode = "git"          # "git" or "disk"
        self.scan_base = ""             # repo path or disk-scan root
        self.cancel_flag = threading.Event()
        self.item_paths = {}            # tree-item-id -> full path
        self.item_is_dir = {}           # tree-item-id -> bool
        self.sort_state = {"col": "Size", "reverse": True}

        self._build_ui()
        self.root.after(80, self._process_queue)

    # ───────────────────────────────────────────────────────────── UI build
    def _build_ui(self):
        # ── Top: repository / folder selector ──
        top = tk.Frame(self.root, pady=8, padx=10)
        top.pack(fill=tk.X)

        tk.Label(top, text="Repository / Folder:").pack(side=tk.LEFT)
        self.repo_combo = ttk.Combobox(top, textvariable=self.repo_path,
                                      width=55, state='normal')
        self.repo_combo.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        tk.Button(top, text="Browse…", command=self.browse_folder) \
            .pack(side=tk.LEFT, padx=(2, 0))
        tk.Button(top, text="Find Repos…", command=self.start_find_repos,
                  bg="#4CAF50", fg="white", font=("Arial", 9, "bold")) \
            .pack(side=tk.LEFT, padx=5)

        self.find_status = tk.StringVar()
        tk.Label(top, textvariable=self.find_status,
                 fg="#0066cc", font=("Arial", 9, "italic")) \
            .pack(side=tk.LEFT, padx=5)

        # ── Scan row ──
        scan_frame = tk.Frame(self.root, pady=5, padx=10)
        scan_frame.pack(fill=tk.X)

        self.scan_git_btn = tk.Button(
            scan_frame, text="1. Scan Git Repo (tracked junk)",
            command=self.start_scan_git, bg="#2196F3", fg="white",
            font=("Arial", 10, "bold"))
        self.scan_git_btn.pack(side=tk.LEFT, padx=5)

        self.scan_disk_btn = tk.Button(
            scan_frame, text="Scan Folder on Disk (all files)",
            command=self.start_scan_disk, bg="#7B1FA2", fg="white",
            font=("Arial", 10, "bold"))
        self.scan_disk_btn.pack(side=tk.LEFT, padx=5)

        tk.Label(scan_frame, text="Min size (MB):").pack(side=tk.LEFT, padx=(15, 2))
        self.min_size_var = tk.IntVar(value=DEFAULT_LARGE_FILE_MB)
        tk.Spinbox(scan_frame, from_=0, to=10000, width=6,
                  textvariable=self.min_size_var) \
            .pack(side=tk.LEFT)

        self.stop_btn = tk.Button(scan_frame, text="⏹ Stop",
                                  command=self.cancel_operation,
                                  state=tk.DISABLED, fg="red")
        self.stop_btn.pack(side=tk.RIGHT, padx=5)

        # ── Action row ──
        btn_frame = tk.Frame(self.root, pady=5, padx=10)
        btn_frame.pack(fill=tk.X)

        tk.Button(btn_frame, text="☐ Select All",
                  command=self.select_all) \
            .pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="☑ Deselect All",
                  command=self.deselect_all) \
            .pack(side=tk.LEFT, padx=5)

        self.untrack_btn = tk.Button(
            btn_frame, text="2. Untrack (git rm --cached)",
            command=self.start_untrack, state=tk.DISABLED)
        self.untrack_btn.pack(side=tk.LEFT, padx=5)

        self.purge_btn = tk.Button(
            btn_frame, text="3. Purge from History",
            command=self.start_purge, state=tk.DISABLED,
            bg="#f44336", fg="white")
        self.purge_btn.pack(side=tk.LEFT, padx=5)

        self.delete_btn = tk.Button(
            btn_frame, text="Delete from Disk",
            command=self.start_delete_disk, state=tk.DISABLED,
            bg="#FF9800", fg="white")
        self.delete_btn.pack(side=tk.LEFT, padx=5)

        self.gc_btn = tk.Button(
            btn_frame, text="4. Garbage Collection",
            command=self.start_gc, state=tk.DISABLED)
        self.gc_btn.pack(side=tk.LEFT, padx=5)

        # ── Progress bar ──
        self.progress = ttk.Progressbar(self.root, mode='indeterminate')

        # ── Summary label ──
        self.summary = tk.StringVar(value="No scan results yet.")
        tk.Label(self.root, textvariable=self.summary,
                 fg="#333", font=("Arial", 9, "bold")) \
            .pack(fill=tk.X, padx=12, pady=(5, 0))

        # ── Table ──
        table_frame = tk.Frame(self.root, padx=10, pady=5)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("Path", "Reason", "Size", "Type")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        
        # Define tags for row colors
        self.tree.tag_configure('large', background='#ffe0b2')  # Orange
        self.tree.tag_configure('dir', background='#e3f2fd')   # Blue
        self.tree.tag_configure('ignored', background='#f1f1f1') # Grey

        for col, label, w in [
            ("Path", "File / Directory Path", 420),
            ("Reason", "Reason", 170),
            ("Size", "Size on Disk", 90),
            ("Type", "Type", 60),
        ]:
            self.tree.heading(col, text=label,
                              command=lambda c=col: self.sort_column(c))
            self.tree.column(col, width=w, anchor=tk.W)

        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                           command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_selection_change)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # ── Log ──
        log_frame = tk.Frame(self.root, padx=10, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=False)

        tk.Label(log_frame, text="Output Log:").pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=7, state='disabled',
            bg="#1e1e1e", fg="#d4d4d4", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # ── Status bar ──
        self.status = tk.StringVar(value="Ready.")
        tk.Label(self.root, textvariable=self.status,
                 relief=tk.SUNKEN, anchor=tk.W, font=("Arial", 8)) \
            .pack(side=tk.BOTTOM, fill=tk.X)

    # ───────────────────────────────────────────────────────────── Queue I/O
    def log(self, message):
        self.queue.put(("log", str(message)))

    def _process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle_queue_msg(msg)
        except queue.Empty:
            pass
        self.root.after(80, self._process_queue)

    def _handle_queue_msg(self, msg):
        tag = msg[0]

        if tag == "log":
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg[1] + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        elif tag == "result":
            _, disp_path, reason, size_str, full_path, is_dir = msg
            
            # Determine tag for row coloring
            tags = ()
            if is_dir:
                tags = ('dir',)
            elif "Large file" in reason:
                tags = ('large',)
            elif "Matches .gitignore" in reason:
                tags = ('ignored',)

            item_id = self.tree.insert("", tk.END,
                                       values=(disp_path, reason, size_str,
                                               "DIR" if is_dir else "FILE"),
                                       tags=tags)
            self.item_paths[item_id] = full_path
            self.item_is_dir[item_id] = is_dir

        elif tag == "summary":
            self.summary.set(msg[1])

        elif tag == "status":
            self.status.set(msg[1])

        elif tag == "progress_start":
            self.progress.pack(fill=tk.X, padx=10, pady=2)
            self.progress.start(12)

        elif tag == "progress_stop":
            self.progress.stop()
            self.progress.pack_forget()

        elif tag == "repos":
            repos = msg[1]
            self.found_repos = repos
            self.repo_combo['values'] = repos
            if repos:
                self.repo_path.set(repos[0])
                self.log(f"→ Auto-selected: {repos[0]}")

        elif tag == "clear_table":
            self.clear_table()

        elif tag == "done":
            self._set_buttons_busy(False)
            self.cancel_flag.clear()
            self.stop_btn.config(state=tk.DISABLED)
            self.find_status.set("")
            self.status.set("Ready.")

    # ───────────────────────────────────────────────────────────── Helpers
    def _run_git_cmd_streamed(self, cmd, cwd):
        """Runs a subprocess and streams stderr/stdout to the log window."""
        self.log(f"$ {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd, cwd=cwd, env=self.proc_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                text=True, encoding='utf-8', errors='ignore'
            )
            for line in process.stdout:
                clean_line = line.replace('\r', '').strip()
                if clean_line:
                    self.log(f"  {clean_line}")
            process.wait()
            return process.returncode
        except Exception as e:
            self.log(f"❌ Command failed: {e}")
            return -1

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Git Repository or Folder")
        if folder:
            self.repo_path.set(folder)
            self.log(f"Selected: {folder}")

    def is_git_repo(self, path):
        return os.path.isdir(path) and os.path.exists(os.path.join(path, ".git"))

    def clear_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.item_paths.clear()
        self.item_is_dir.clear()

    def get_selected_items(self):
        result = []
        for item in self.tree.selection():
            result.append((item,
                           self.item_paths.get(item, ""),
                           self.item_is_dir.get(item, False)))
        return result

    def _set_buttons_busy(self, busy):
        state = tk.DISABLED if busy else tk.NORMAL
        for btn in (self.scan_git_btn, self.scan_disk_btn,
                    self.untrack_btn, self.purge_btn,
                    self.delete_btn, self.gc_btn):
            btn.config(state=state)
        if busy:
            self.stop_btn.config(state=tk.NORMAL)

    def cancel_operation(self):
        self.cancel_flag.set()
        self.log("⏹ Stop requested — finishing current file…")

    def _on_selection_change(self, _event):
        selected = self.get_selected_items()
        total = sum(self._item_size(i[0]) for i in selected)
        self.summary.set(
            f"{len(selected)} item(s) selected  |  "
            f"Total: {format_size(total)}  |  "
            f"Mode: {self.scan_mode.upper()}")

    def _item_size(self, item_id):
        try:
            size_str = self.tree.set(item_id, "Size")
            num = float(size_str.split()[0])
            unit = size_str.split()[1]
            mult = {'B': 1, 'KB': 1024, 'MB': 1024**2,
                    'GB': 1024**3, 'TB': 1024**4}
            return num * mult.get(unit, 0)
        except (ValueError, IndexError, KeyError):
            return 0

    def sort_column(self, col):
        reverse = not self.sort_state["reverse"] \
            if self.sort_state["col"] == col else False
        self.sort_state = {"col": col, "reverse": reverse}

        data = []
        for item in self.tree.get_children():
            vals = self.tree.set(item, col)
            if col == "Size":
                data.append((self._item_size(item), item))
            else:
                data.append((str(vals).lower(), item))

        data.sort(key=lambda x: x[0], reverse=reverse)
        for idx, (_, item) in enumerate(data):
            self.tree.move(item, '', idx)

    def _show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Open in File Manager",
                         command=self._open_in_file_manager)
        menu.add_command(label="Copy Path",
                         command=self._copy_path)
        menu.add_separator()
        menu.add_command(label="Select All", command=self.select_all)
        menu.add_command(label="Deselect All", command=self.deselect_all)
        menu.tk_popup(event.x_root, event.y_root)

    def _open_in_file_manager(self):
        selected = self.get_selected_items()
        if not selected:
            return
        full_path = selected[0][1]
        parent = os.path.dirname(full_path) or full_path
        try:
            if sys.platform == "win32":
                os.startfile(parent)
            elif sys.platform == "darwin":
                subprocess.run(["open", parent])
            else:
                subprocess.run(["xdg-open", parent])
        except Exception as e:
            self.log(f"Could not open file manager: {e}")

    def _copy_path(self):
        selected = self.get_selected_items()
        if not selected:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(selected[0][1])

    # ═══════════════════════════════════════════════════════════════════════
    #  FIND REPOSITORIES
    # ═══════════════════════════════════════════════════════════════════════

    def start_find_repos(self):
        root_folder = filedialog.askdirectory(
            title="Select Root Folder — recursively find Git repos")
        if not root_folder:
            return

        self.found_repos = []
        self.repo_combo['values'] = []
        self.repo_path.set("")
        self.find_status.set("Scanning for repositories…")
        self._set_buttons_busy(True)
        self.log(f"\n=== Searching for Git repos under:\n  {root_folder} ===")

        threading.Thread(target=self._find_repos_thread,
                         args=(root_folder,), daemon=True).start()

    def _find_repos_thread(self, root_folder):
        found = []
        visited = 0
        skip = SKIP_DIRS_DISK | JUNK_DIR_NAMES

        for dirpath, dirnames, _ in os.walk(root_folder, followlinks=False):
            if self.cancel_flag.is_set():
                break
            visited += 1
            if visited % 50 == 0:
                self.queue.put(("status",
                    f"Scanning… {visited} dirs visited, {len(found)} repos found"))

            if os.path.exists(os.path.join(dirpath, ".git")):
                found.append(dirpath)
                self.log(f"  ✓ {dirpath}")
                dirnames[:] = []          # don't descend into repo subdirs
                continue

            dirnames[:] = [d for d in dirnames
                          if d.lower() not in skip]

        self.queue.put(("repos", found))
        self.log(f"\nDone. Found {len(found)} repositories.")
        self.queue.put(("done",))

    # ═══════════════════════════════════════════════════════════════════════
    #  SCAN — GIT REPO (tracked junk files)
    # ═══════════════════════════════════════════════════════════════════════

    def start_scan_git(self):
        if not git_available():
            messagebox.showerror("Git Not Found",
                "Git is not installed or not on PATH.")
            return

        repo = self.repo_path.get().strip()
        if not repo or not self.is_git_repo(repo):
            messagebox.showerror("Error",
                "Please select a valid Git repository.")
            return

        self.scan_mode = "git"
        self.scan_base = repo
        self._set_buttons_busy(True)
        self.cancel_flag.clear()
        self.queue.put(("clear_table",))
        self.queue.put(("progress_start",))
        self.queue.put(("status", "Scanning Git repo for junk…"))
        self.log(f"\n{'='*60}\nScanning Git repo: {repo}\n{'='*60}")

        threading.Thread(target=self._scan_git_thread,
                         args=(repo,), daemon=True).start()

    def _scan_git_thread(self, repo):
        try:
            result = subprocess.run(["git", "ls-files"], cwd=repo,
                                    capture_output=True, text=True, check=True)
            tracked = [f for f in result.stdout.splitlines() if f]
        except subprocess.CalledProcessError as e:
            self.log(f"❌ git ls-files failed: {e.stderr}")
            self.queue.put(("done",))
            return

        ignored_set = set()
        try:
            ign = subprocess.run(["git", "ls-files", "-i", "-c"], cwd=repo,
                                  capture_output=True, text=True)
            ignored_set = set(l for l in ign.stdout.splitlines() if l)
        except subprocess.CalledProcessError:
            pass 

        total = len(tracked)
        found_count = 0
        found_size = 0
        min_bytes = self.min_size_var.get() * 1024 * 1024

        for idx, f in enumerate(tracked):
            if self.cancel_flag.is_set():
                self.log("⏹ Scan cancelled by user.")
                break
            if idx % 500 == 0:
                self.queue.put(("status",
                    f"Scanning… {idx}/{total} files checked, "
                    f"{found_count} junk found"))

            f_lower = f.lower()
            reason = ""
            is_junk = False
            full_path = os.path.join(repo, f)
            size = 0

            if f in ignored_set:
                is_junk = True
                reason = "Matches .gitignore"

            try:
                size = os.path.getsize(full_path)
                if size > min_bytes:
                    is_junk = True
                    if not reason:
                        reason = f"Large file ({format_size(size)})"
            except OSError:
                size = 0
                is_junk = True
                reason = "Missing on disk"

            ext = os.path.splitext(f_lower)[1]
            fname = os.path.basename(f_lower)
            if not is_junk:
                if ext in JUNK_FILE_EXTENSIONS:
                    is_junk = True
                    reason = f"Junk extension ({ext})"
                elif fname in JUNK_FILE_NAMES:
                    is_junk = True
                    reason = f"Junk file ({fname})"
                else:
                    for pat in ('cmakecache.txt', 'cmake_install.cmake',
                                'cmakefiles'):
                        if pat in f_lower:
                            is_junk = True
                            reason = "CMake artifact"
                            break

            if is_junk:
                found_count += 1
                found_size += size
                self.queue.put(("result", f, reason, format_size(size),
                               full_path, False))

            if found_count >= MAX_RESULTS:
                self.log(f"⚠ Result limit ({MAX_RESULTS}) reached. "
                         f"Stopping scan for performance.")
                break

        self.queue.put(("summary",
            f"Found {found_count} junk file(s)  |  "
            f"Total: {format_size(found_size)}  |  "
            f"Mode: GIT (tracked files)"))
        self.log(f"\nScan complete: {found_count} junk files found "
                 f"({format_size(found_size)}).")
        self.queue.put(("progress_stop",))
        self.queue.put(("status", "Scan complete."))
        self.queue.put(("done",))

    # ═══════════════════════════════════════════════════════════════════════
    #  SCAN — DISK (all files in a folder, tracked or not)
    # ═══════════════════════════════════════════════════════════════════════

    def start_scan_disk(self):
        root_folder = filedialog.askdirectory(
            title="Select a folder to scan ALL files for junk")
        if not root_folder:
            return

        self.scan_mode = "disk"
        self.scan_base = root_folder
        self._set_buttons_busy(True)
        self.cancel_flag.clear()
        self.queue.put(("clear_table",))
        self.queue.put(("progress_start",))
        self.queue.put(("status", "Scanning disk for junk files…"))
        self.log(f"\n{'='*60}\nScanning disk folder: {root_folder}\n"
                 f"{'='*60}")

        threading.Thread(target=self._scan_disk_thread,
                         args=(root_folder,), daemon=True).start()

    def _scan_disk_thread(self, root_folder):
        found_count = 0
        found_size = 0
        visited = 0
        min_bytes = self.min_size_var.get() * 1024 * 1024
        skip = SKIP_DIRS_DISK | {'.git'}

        for dirpath, dirnames, filenames in os.walk(root_folder,
                                                    followlinks=False):
            if self.cancel_flag.is_set():
                self.log("⏹ Scan cancelled by user.")
                break
            visited += 1
            if visited % 100 == 0:
                self.queue.put(("status",
                    f"Scanning… {visited} dirs visited, "
                    f"{found_count} junk items found"))

            kept_dirs = []
            for d in dirnames:
                d_lower = d.lower()
                if d_lower in JUNK_DIR_NAMES:
                    full = os.path.join(dirpath, d)
                    size = dir_size(full)
                    found_count += 1
                    found_size += size
                    rel = os.path.relpath(full, root_folder)
                    reason = f"Junk dir ({d_lower})"
                    self.queue.put(("result", rel, reason,
                                   format_size(size), full, True))
                    if found_count >= MAX_RESULTS:
                        break
                elif d_lower in skip:
                    pass
                else:
                    kept_dirs.append(d)
            dirnames[:] = kept_dirs

            if found_count >= MAX_RESULTS:
                self.log(f"⚠ Result limit ({MAX_RESULTS}) reached.")
                break

            for fname in filenames:
                if self.cancel_flag.is_set():
                    break
                f_lower = fname.lower()
                ext = os.path.splitext(f_lower)[1]
                full = os.path.join(dirpath, fname)
                size = 0
                reason = ""

                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue

                is_junk = False
                if ext in JUNK_FILE_EXTENSIONS:
                    is_junk = True
                    reason = f"Junk ext ({ext})"
                elif f_lower in JUNK_FILE_NAMES:
                    is_junk = True
                    reason = f"Junk file ({f_lower})"
                elif size > min_bytes:
                    is_junk = True
                    reason = f"Large file ({format_size(size)})"
                else:
                    for pat in ('cmakecache.txt', 'cmake_install.cmake'):
                        if pat in f_lower:
                            is_junk = True
                            reason = "CMake artifact"
                            break

                if is_junk:
                    found_count += 1
                    found_size += size
                    rel = os.path.relpath(full, root_folder)
                    self.queue.put(("result", rel, reason,
                                   format_size(size), full, False))
                    if found_count >= MAX_RESULTS:
                        self.log(f"⚠ Result limit ({MAX_RESULTS}) reached.")
                        break

        self.queue.put(("summary",
            f"Found {found_count} junk item(s)  |  "
            f"Total: {format_size(found_size)}  |  "
            f"Mode: DISK (all files)"))
        self.log(f"\nDisk scan complete: {found_count} items found "
                 f"({format_size(found_size)}).")
        self.queue.put(("progress_stop",))
        self.queue.put(("status", "Disk scan complete."))
        self.queue.put(("done",))

    # ═══════════════════════════════════════════════════════════════════════
    #  SELECTION
    # ═══════════════════════════════════════════════════════════════════════

    def select_all(self):
        for item in self.tree.get_children():
            self.tree.selection_add(item)

    def deselect_all(self):
        for item in self.tree.selection():
            self.tree.selection_remove(item)

    # ═══════════════════════════════════════════════════════════════════════
    #  UNTRACK  (git rm --cached)
    # ═══════════════════════════════════════════════════════════════════════

    def start_untrack(self):
        if not git_available():
            messagebox.showerror("Git Not Found",
                "Git is not installed or not on PATH.")
            return
        if self.scan_mode != "git":
            messagebox.showinfo("Wrong Mode",
                "Untrack is only available in Git scan mode.\n"
                "Use 'Scan Git Repo' first.")
            return

        selected = self.get_selected_items()
        if not selected:
            messagebox.showwarning("Warning",
                "Please select files in the table to untrack.")
            return

        files = [s[1] for s in selected if not s[2]]
        if not files:
            messagebox.showinfo("No Files",
                "Only individual files can be untracked.\n"
                "Directory entries are not supported for git rm.")
            return

        repo = self.scan_base
        self._set_buttons_busy(True)
        self.log(f"\nUntracking {len(files)} file(s) from Git index…")
        threading.Thread(target=self._untrack_thread,
                         args=(repo, files), daemon=True).start()

    def _untrack_thread(self, repo, files):
        try:
            BATCH = 200
            for i in range(0, len(files), BATCH):
                batch = files[i:i + BATCH]
                rel_paths = [os.path.relpath(f, repo) for f in batch]
                subprocess.run(["git", "rm", "--cached"] + rel_paths,
                               cwd=repo, check=True,
                               capture_output=True, text=True)
                self.log(f"  Untracked batch {i // BATCH + 1}"
                         f" ({len(batch)} files)")

            commit = subprocess.run(
                ["git", "commit", "-m", "Remove junk/ignored files from tracking"],
                cwd=repo, capture_output=True, text=True)
            if commit.returncode != 0:
                self.log(f"⚠ Commit failed: {commit.stderr or commit.stdout}")
                self.log("   You will need to commit or stash manually before purging.")
            else:
                self.log("✅ Untracked files and committed changes.")
        except subprocess.CalledProcessError as e:
            self.log(f"❌ Error untracking: {e.stderr or e.stdout}")
        except Exception as e:
            self.log(f"❌ Exception: {e}")

        self.queue.put(("done",))

    # ═══════════════════════════════════════════════════════════════════════
    #  PURGE FROM HISTORY  (git filter-repo)
    # ═══════════════════════════════════════════════════════════════════════

    def start_purge(self):
        if not git_available():
            messagebox.showerror("Git Not Found",
                "Git is not installed or not on PATH.")
            return
        if self.scan_mode != "git":
            messagebox.showinfo("Wrong Mode",
                "Purge is only available in Git scan mode.")
            return

        try:
            subprocess.run(["git", "filter-repo", "--version"],
                           capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            messagebox.showerror("Missing Dependency",
                "This feature requires 'git-filter-repo'.\n\n"
                "Install via terminal:\n"
                "  pip install git-filter-repo\n"
                "  (or: brew install git-filter-repo)")
            return

        selected = self.get_selected_items()
        if not selected:
            return

        repo = self.scan_base
        repo_name = os.path.basename(os.path.normpath(repo))
        remote_url = f"{GIT_REMOTE_BASE}/{repo_name}.git"

        confirm = messagebox.askyesno("Confirm History Purge & Force Push",
            f"⚠ WARNING: This REWRITES Git history to completely\n"
            f"delete these files from ALL commits.\n\n"
            f"This changes commit hashes. Because history is rewritten,\n"
            f"the app will automatically re-add the remote and force push.\n\n"
            f"Remote URL that will be pushed to:\n"
            f"{remote_url}\n\n"
            f"Proceed with purge and force push?")
        if not confirm:
            return

        self._set_buttons_busy(True)
        self.log(f"\nPurging {len(selected)} path(s) from history…")
        threading.Thread(target=self._purge_thread,
                         args=(repo, selected, remote_url), daemon=True).start()

    def _purge_thread(self, repo, selected, remote_url):
        stashed = False
        try:
            # 1. Check working tree is clean. If not, auto-stash.
            status = subprocess.run(["git", "status", "--porcelain"],
                                    cwd=repo, capture_output=True, text=True)
            if status.stdout.strip():
                self.log("⚠ Working tree not clean. Auto-stashing untracked/modified files…")
                stash = subprocess.run(
                    ["git", "stash", "push", "--include-untracked", "-m",
                     "auto-stash before filter-repo"],
                    cwd=repo, capture_output=True, text=True)
                if stash.returncode != 0:
                    self.log(f"❌ Could not stash: {stash.stderr or stash.stdout}")
                    self.log("   Commit or stash changes manually, then retry.")
                    self.queue.put(("done",))
                    return
                stashed = "No local changes" not in (stash.stdout or "")
                if stashed:
                    self.log("  ✓ Stashed. (Will be restored after purge.)")
                else:
                    self.log("  ℹ Nothing to stash — proceeding.")

            # 2. Run git filter-repo (Stream output)
            cmd = ["git", "filter-repo", "--force"]
            for _, full_path, is_dir in selected:
                rel = os.path.relpath(full_path, repo)
                cmd.extend(["--path", rel])
            cmd.extend(["--invert-paths"])

            ret = self._run_git_cmd_streamed(cmd, cwd=repo)
            
            if ret != 0:
                self.log(f"❌ Purge failed with return code {ret}.")
            else:
                self.log("✅ History purged successfully.")
                
                # 3. Re-add origin remote (using set-url to be safe if it already exists)
                self.log(f"Configuring remote 'origin' to {remote_url}…")
                set_url = subprocess.run(["git", "remote", "set-url", "origin", remote_url],
                                         cwd=repo, capture_output=True, text=True)
                if set_url.returncode != 0:
                    # If set-url fails, it means origin doesn't exist yet. Try adding.
                    add_remote = subprocess.run(["git", "remote", "add", "origin", remote_url],
                                                cwd=repo, capture_output=True, text=True)
                    if add_remote.returncode != 0:
                        self.log(f"⚠ Could not add remote: {add_remote.stderr or add_remote.stdout}")
                self.log("  ✓ Remote configured.")

                # 4. Force push all branches
                self.log("Force pushing all branches to origin...")
                ret_push = self._run_git_cmd_streamed(["git", "push", "--force", "origin", "--all"], cwd=repo)
                if ret_push != 0:
                    self.log("⚠ Push --all failed. (Check credentials or network)")
                else:
                    self.log("  ✓ Pushed all branches.")

                # 5. Force push all tags
                self.log("Force pushing tags to origin...")
                ret_tags = self._run_git_cmd_streamed(["git", "push", "--force", "origin", "--tags"], cwd=repo)
                if ret_tags != 0:
                    self.log("⚠ Push --tags failed.")
                else:
                    self.log("  ✓ Pushed all tags.")
                    
                self.log("✅ Remote update complete!")

        except Exception as e:
            self.log(f"❌ Exception: {e}")
        finally:
            if stashed:
                self.log("Restoring auto-stashed changes…")
                pop = subprocess.run(["git", "stash", "pop"],
                                     cwd=repo, capture_output=True, text=True)
                if pop.returncode != 0:
                    self.log(f"⚠ Could not pop stash: {pop.stderr or pop.stdout}")
                    self.log("   Run `git stash pop` manually when ready.")
                else:
                    self.log("  ✓ Stash restored.")
            self.queue.put(("done",))

    # ═══════════════════════════════════════════════════════════════════════
    #  DELETE FROM DISK  (direct file/dir removal)
    # ═══════════════════════════════════════════════════════════════════════

    def start_delete_disk(self):
        selected = self.get_selected_items()
        if not selected:
            messagebox.showwarning("Warning",
                "Please select items in the table to delete.")
            return

        # Safety check: prevent deleting .git directory
        for item in selected:
            if os.path.basename(item[1]) == ".git":
                messagebox.showerror("Unsafe Operation Blocked",
                    "Deleting the '.git' folder from disk is blocked to prevent repository corruption.")
                return

        total_size = sum(self._item_size(s[0]) for s in selected)
        confirm = messagebox.askyesno("Confirm Delete from Disk",
            f"⚠ This will PERMANENTLY DELETE {len(selected)} item(s)\n"
            f"from disk ({format_size(total_size)}).\n\n"
            f"This cannot be undone!\n\n"
            f"Proceed?")
        if not confirm:
            return

        self._set_buttons_busy(True)
        self.log(f"\nDeleting {len(selected)} item(s) from disk…")
        threading.Thread(target=self._delete_disk_thread,
                         args=(selected,), daemon=True).start()

    def _delete_disk_thread(self, selected):
        deleted = 0
        errors = 0
        for item_id, full_path, is_dir in selected:
            if self.cancel_flag.is_set():
                self.log("⏹ Deletion cancelled.")
                break
            try:
                if is_dir:
                    shutil.rmtree(full_path)
                else:
                    os.remove(full_path)
                deleted += 1
                self.log(f"  🗑 Deleted: {full_path}")
            except Exception as e:
                errors += 1
                self.log(f"  ❌ Failed: {full_path} — {e}")

        self.log(f"\nDeleted {deleted} item(s), {errors} error(s).")
        self.queue.put(("done",))

    # ═══════════════════════════════════════════════════════════════════════
    #  GARBAGE COLLECTION
    # ═══════════════════════════════════════════════════════════════════════

    def start_gc(self):
        if not git_available():
            messagebox.showerror("Git Not Found",
                "Git is not installed or not on PATH.")
            return
        repo = self.scan_base or self.repo_path.get().strip()
        if not repo or not self.is_git_repo(repo):
            messagebox.showerror("Error",
                "Please select a valid Git repository first.")
            return

        self._set_buttons_busy(True)
        self.log(f"\nRunning Garbage Collection on:\n  {repo}")
        threading.Thread(target=self._gc_thread,
                         args=(repo,), daemon=True).start()

    def _gc_thread(self, repo):
        commands = [
            ("Expiring reflog…",
             ["git", "reflog", "expire", "--expire-unreachable=now", "--all"]),
            ("Running aggressive GC…",
             ["git", "gc", "--prune=now", "--aggressive"]),
        ]
        for msg, cmd in commands:
            self.log(msg)
            ret = self._run_git_cmd_streamed(cmd, cwd=repo)
            if ret != 0:
                self.log(f"  ⚠ Command failed with return code {ret}.")
            else:
                self.log("  ✓ done")

        self.log("✅ Garbage Collection complete!")
        self.queue.put(("done",))


# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()
    app = GitCleanerApp(root)
    root.mainloop()