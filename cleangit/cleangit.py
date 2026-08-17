import os
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox, ttk

class GitCleanerApp:
    def __init__(self, root):
        self.root = root
        root.title("Git Repo Cleaner - Untrack & Purge Junk")
        root.geometry("900x720")

        self.queue = queue.Queue()
        self.repo_path = tk.StringVar()
        self.found_repos = []  # list of repo paths discovered by recursive scan

        # --- UI Layout ---
        top_frame = tk.Frame(root, pady=10, padx=10)
        top_frame.pack(fill=tk.X)

        tk.Label(top_frame, text="Repository:").pack(side=tk.LEFT)
        # Combobox (editable) — user can pick a discovered repo OR type a path manually
        self.repo_combo = ttk.Combobox(top_frame, textvariable=self.repo_path, width=55, state='normal')
        self.repo_combo.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        tk.Button(top_frame, text="Browse…",
                  command=self.browse_folder).pack(side=tk.LEFT, padx=(2, 0))
        tk.Button(top_frame, text="Find Repos in Folder…",
                  command=self.start_find_repos,
                  bg="#4CAF50", fg="white",
                  font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)

        # Status / progress label for the recursive repo scan
        self.find_status = tk.StringVar(value="")
        tk.Label(top_frame, textvariable=self.find_status,
                 fg="#0066cc", font=("Arial", 9, "italic")).pack(side=tk.LEFT, padx=5)

        # Action Buttons
        btn_frame = tk.Frame(root, pady=5, padx=10)
        btn_frame.pack(fill=tk.X)

        self.scan_btn = tk.Button(btn_frame, text="1. Scan for Junk/Ignored Files",
                                  command=self.start_scan, bg="#2196F3", fg="white",
                                  font=("Arial", 10, "bold"))
        self.scan_btn.pack(side=tk.LEFT, padx=5)

        self.untrack_btn = tk.Button(btn_frame, text="2. Untrack Selected (Keep on Disk)",
                                     command=self.start_untrack, state=tk.DISABLED)
        self.untrack_btn.pack(side=tk.LEFT, padx=5)

        self.purge_btn = tk.Button(btn_frame, text="3. Purge Selected from History",
                                   command=self.start_purge, state=tk.DISABLED,
                                   bg="#f44336", fg="white")
        self.purge_btn.pack(side=tk.LEFT, padx=5)

        self.gc_btn = tk.Button(btn_frame, text="4. Run Garbage Collection",
                                command=self.start_gc, state=tk.DISABLED)
        self.gc_btn.pack(side=tk.LEFT, padx=5)

        # Table (Treeview)
        table_frame = tk.Frame(root, padx=10, pady=5)
        table_frame.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(table_frame, columns=("Path", "Reason", "Size"), show="headings")
        self.tree.heading("Path", text="File Path")
        self.tree.heading("Reason", text="Reason")
        self.tree.heading("Size", text="Size on Disk")
        self.tree.column("Path", width=450)
        self.tree.column("Reason", width=150)
        self.tree.column("Size", width=100)
        self.tree.pack(fill=tk.BOTH, expand=True)

        # Log Output
        log_frame = tk.Frame(root, padx=10, pady=10)
        log_frame.pack(fill=tk.BOTH, expand=False)

        tk.Label(log_frame, text="Output Log:").pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, state='disabled',
                                                  bg="#f4f4f4", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.root.after(100, self.process_queue)

    # ---------------------------------------------------------------- UI helpers
    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Git Repository")
        if folder:
            self.repo_path.set(folder)
            self.log("Selected repository: " + folder)

    def log(self, message):
        self.queue.put(message)

    # ---------------------------------------------------------------- Queue pump
    def process_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()

                # Special: discovered repos list delivered as a tuple
                if isinstance(msg, tuple) and msg and msg[0] == "__REPOS_FOUND__":
                    repos = msg[1]
                    self.found_repos = repos
                    self.repo_combo['values'] = repos
                    if repos:
                        self.repo_path.set(repos[0])
                        self.log(f"→ Auto-selected first repo: {repos[0]}")
                    continue

                if msg == "__DONE__":
                    self.scan_btn.config(state=tk.NORMAL)
                    self.untrack_btn.config(state=tk.NORMAL)
                    self.purge_btn.config(state=tk.NORMAL)
                    self.gc_btn.config(state=tk.NORMAL)
                    self.find_status.set("")
                    break
                else:
                    self.log_text.config(state=tk.NORMAL)
                    self.log_text.insert(tk.END, msg + "\n")
                    self.log_text.see(tk.END)
                    self.log_text.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    # ---------------------------------------------------------------- Git checks
    def is_git_repo(self, path):
        # Treat both .git directory and submodule-style .git file as valid
        return os.path.exists(os.path.join(path, ".git"))

    # ---------------------------------------------------------------- Find repos
    def start_find_repos(self):
        root_folder = filedialog.askdirectory(title="Select Root Folder to Recursively Scan for Git Repos")
        if not root_folder:
            return

        # Reset combo + state
        self.found_repos = []
        self.repo_combo['values'] = []
        self.repo_path.set("")
        self.find_status.set("Scanning for repositories…")

        # Lock action buttons during the recursive scan
        self.scan_btn.config(state=tk.DISABLED)
        self.untrack_btn.config(state=tk.DISABLED)
        self.purge_btn.config(state=tk.DISABLED)
        self.gc_btn.config(state=tk.DISABLED)

        self.log(f"\n=== Recursively searching for Git repositories under:\n  {root_folder} ===")

        threading.Thread(target=self.find_repos_thread,
                         args=(root_folder,), daemon=True).start()

    def find_repos_thread(self, root_folder):
        found = []
        visited = 0

        # Folders we never descend into — they are huge and never contain a repo root
        SKIP_DIRS = {
            'node_modules', '.venv', 'venv', 'env', '__pycache__',
            '.next', '.nuxt', '.svelte-kit', 'dist', 'build', 'target',
            '.cache', '.idea', '.vs', '.vscode', 'bin', 'obj',
            'site-packages', '.gradle', '.m2',
        }

        # os.walk with followlinks=False avoids symlink loops
        for dirpath, dirnames, filenames in os.walk(root_folder, followlinks=False):
            visited += 1
            if visited % 50 == 0:
                self.queue.put(f"  … scanned {visited} directories, found {len(found)} repos so far")

            git_path = os.path.join(dirpath, ".git")
            if os.path.exists(git_path):
                # This directory is itself a git repository (regular or submodule pointer)
                found.append(dirpath)
                self.queue.put(f"  ✓ Found repo: {dirpath}")

                # Do NOT descend into the .git internals (massive & pointless)
                if '.git' in dirnames:
                    dirnames.remove('.git')

            # Prune heavy/irrelevant subtrees in-place so os.walk skips them entirely
            dirnames[:] = [d for d in dirnames
                          if d.lower() not in SKIP_DIRS and d != '.git']

        # Send results back to the UI thread
        self.queue.put(("__REPOS_FOUND__", found))
        self.queue.put(f"\nScan complete. Found {len(found)} repositories. "
                       f"Pick one from the dropdown above.")
        self.queue.put("__DONE__")

    # ---------------------------------------------------------------- Scan junk
    def start_scan(self):
        repo = self.repo_path.get().strip()
        if not repo or not self.is_git_repo(repo):
            messagebox.showerror("Error", "Please select a valid Git repository.")
            return

        self.scan_btn.config(state=tk.DISABLED)
        self.untrack_btn.config(state=tk.DISABLED)
        self.purge_btn.config(state=tk.DISABLED)
        self.gc_btn.config(state=tk.DISABLED)

        # Clear table
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.log(f"\nScanning repository for junk and ignored files:\n  {repo}")
        threading.Thread(target=self.scan_thread, args=(repo,), daemon=True).start()

    def scan_thread(self, repo):
        junk_patterns = [
            # OS & Env
            '.ds_store', 'thumbs.db', '.env',
            # Python
            '__pycache__', '.pyc', '.pyo',
            # Logs & Archives
            '.log', '.zip', '.rar', '.7z', '.tar', '.gz',
            # Binaries & Libs
            '.exe', '.dll', '.so', '.dylib', '.bin',
            # Video Files
            '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm',
            # Audio Files
            '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma',
            # CMake Build Files & Directories
            'cmakecache.txt', 'cmake_install.cmake', 'cmakefiles'
        ]

        result = subprocess.run(["git", "ls-files"], cwd=repo,
                                capture_output=True, text=True)
        tracked_files = result.stdout.splitlines()

        for f in tracked_files:
            if not f:
                continue
            f_lower = f.lower()
            reason = ""
            is_junk = False

            # 1. Check if ignored by git
            check_ign = subprocess.run(["git", "check-ignore", f], cwd=repo,
                                        capture_output=True, text=True)
            if check_ign.returncode == 0:
                is_junk = True
                reason = "Matches .gitignore"

            # 2. Check file size (> 5MB)
            full_path = os.path.join(repo, f)
            try:
                size = os.path.getsize(full_path)
                if size > 5 * 1024 * 1024:
                    is_junk = True
                    if not reason:
                        reason = f"Large file ({format_size(size)})"
            except OSError:
                size = 0
                reason = "Missing on disk"

            # 3. Check common junk extensions / directories
            for pat in junk_patterns:
                if f_lower.endswith(pat) or pat in f_lower:
                    is_junk = True
                    if not reason:
                        if pat.startswith('.'):
                            reason = f"Junk file ({pat})"
                        elif pat == '__pycache__':
                            reason = "Python cache"
                        elif 'cmake' in pat:
                            reason = "CMake artifact"
                        else:
                            reason = f"Media/Junk ({pat})"
                    break

            if is_junk:
                self.queue.put(f"Found: {f} ({reason})")
                self.tree.insert("", tk.END, values=(f, reason, format_size(size)))

        self.log("Scan complete. Select files to remove.")
        self.queue.put("__DONE__")

    # ---------------------------------------------------------------- Selection
    def get_selected_files(self):
        selected = []
        for item in self.tree.selection():
            values = self.tree.item(item, "values")
            if values:
                selected.append(values[0])
        return selected

    # ---------------------------------------------------------------- Untrack
    def start_untrack(self):
        files = self.get_selected_files()
        if not files:
            messagebox.showwarning("Warning", "Please select files in the table to untrack.")
            return

        self.log(f"\nUntracking {len(files)} files from Git index (keeping them on your disk)…")
        threading.Thread(target=self.untrack_thread,
                         args=(self.repo_path.get().strip(), files),
                         daemon=True).start()

    def untrack_thread(self, repo, files):
        try:
            cmd = ["git", "rm", "--cached"] + files
            subprocess.run(cmd, cwd=repo, check=True,
                           capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m",
                             "Remove junk/ignored files from tracking"],
                           cwd=repo, capture_output=True, text=True)
            self.log("✅ Successfully untracked files and committed changes.")
        except subprocess.CalledProcessError as e:
            self.log(f"❌ Error untracking files: {e.stderr}")

        self.queue.put("__DONE__")

    # ---------------------------------------------------------------- Purge
    def start_purge(self):
        try:
            subprocess.run(["git", "filter-repo", "--version"],
                           capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            messagebox.showerror(
                "Missing Dependency",
                "This feature requires 'git-filter-repo'.\n\n"
                "Install via terminal:\n"
                "  pip install git-filter-repo\n"
                "  (or: brew install git-filter-repo)")
            return

        files = self.get_selected_files()
        if not files:
            return

        confirm = messagebox.askyesno(
            "Confirm History Purge",
            "WARNING: This rewrites Git history to completely delete these files.\n\n"
            "This changes commit hashes. If you have already pushed to a remote, "
            "you will need to force push (git push --force).\n\n"
            "Do you want to proceed?")
        if not confirm:
            return

        self.log(f"\nPurging {len(files)} files from entire Git history…")
        threading.Thread(target=self.purge_thread,
                         args=(self.repo_path.get().strip(), files),
                         daemon=True).start()

    def purge_thread(self, repo, files):
        try:
            status = subprocess.run(["git", "status", "--porcelain"],
                                    cwd=repo, capture_output=True, text=True)
            if status.stdout.strip():
                self.log("⚠️ Working tree is not clean. "
                         "Please commit or stash changes before purging history.")
                self.queue.put("__DONE__")
                return

            cmd = ["git", "filter-repo", "--force"]
            for f in files:
                cmd.extend(["--path", f])
            cmd.extend(["--invert-paths"])

            process = subprocess.run(cmd, cwd=repo,
                                     capture_output=True, text=True)
            if process.returncode != 0:
                self.log(f"❌ Error during purge: {process.stderr}")
            else:
                self.log("✅ History purged successfully.")
                self.log("Note: git-filter-repo removes the 'origin' remote. "
                         "Re-add it with:")
                self.log("  git remote add origin <your-repo-url>")
        except Exception as e:
            self.log(f"❌ Exception: {str(e)}")

        self.queue.put("__DONE__")

    # ---------------------------------------------------------------- GC
    def start_gc(self):
        repo = self.repo_path.get().strip()
        if not repo or not self.is_git_repo(repo):
            messagebox.showerror("Error", "Please select a valid Git repository.")
            return
        self.log("\nRunning Garbage Collection to reclaim disk space…")
        threading.Thread(target=self.gc_thread, args=(repo,), daemon=True).start()

    def gc_thread(self, repo):
        commands = [
            ("Expiring reflog…",
             ["git", "reflog", "expire", "--expire-unreachable=now", "--all"]),
            ("Running GC…",
             ["git", "gc", "--prune=now", "--aggressive"])
        ]
        for msg, cmd in commands:
            self.log(msg)
            process = subprocess.run(cmd, cwd=repo,
                                     capture_output=True, text=True)
            if process.returncode != 0:
                self.log(f"Error: {process.stderr}")

        self.log("✅ Garbage Collection complete!")
        self.queue.put("__DONE__")


# --- Utility Functions ---
def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


if __name__ == "__main__":
    root = tk.Tk()
    app = GitCleanerApp(root)
    root.mainloop()