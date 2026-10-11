import os
import re
import json
import subprocess
import threading
import time
import shutil
import stat
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
import logging
import webbrowser
from typing import List, Dict, Tuple, Optional, Set


class GitRepoCreatorApp:
    """
    GUI app that walks through subfolders of a chosen parent folder.
    v3.0: Fixed timeout misdiagnosis, improved network error messages, 
    secure token handling, and robust Windows lock handling.
    """

    EXCLUDE_FOLDERS: Set[str] = {"input", "output", "temp", "venv", ".venv", "__pycache__",
                                 "node_modules", "build", "dist"}

    LICENSE_TEMPLATES: Dict[str, str] = {
        "None": "",
        "MIT": """MIT License

Copyright (c) {year}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""",
        "Apache 2.0": """                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
""",
        "GPLv3": """This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Git Repo Creator / Updater v3.0")
        self.root.geometry("950x780")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.logger = logging.getLogger("GitRepoCreator")
        self.logger.setLevel(logging.INFO)

        # Variables
        self.folder_var = tk.StringVar()
        self.commit_msg_var = tk.StringVar(value="Automated commit")
        self.visibility_var = tk.StringVar(value="private")
        self.dry_run_var = tk.BooleanVar(value=False)
        self.auto_readme_var = tk.BooleanVar(value=True)
        self.auto_license_var = tk.BooleanVar(value=False)
        self.license_type_var = tk.StringVar(value="MIT")
        self.rate_limit_var = tk.DoubleVar(value=3.0)
        
        self.squash_history_var = tk.BooleanVar(value=False)
        self.surgical_history_var = tk.BooleanVar(value=False)
        self.show_git_log_var = tk.BooleanVar(value=True)

        # Runtime state
        self.running = False
        self.cancelled = False
        self.dry_run = False
        self.stats_lock = threading.Lock()
        self.stats = self._empty_stats()
        self.progress_file: Optional[str] = None
        self.completed_repos: Set[str] = set()
        self.start_time: Optional[float] = None

        self._load_settings()
        self._build_ui()

    def _empty_stats(self) -> Dict:
        return {
            "total": 0, "processed": 0, "created": 0, "updated": 0,
            "failed": 0, "skipped": 0, "files_cleaned": 0,
            "bytes_freed_estimate": 0, "errors": [],
            "repo_sizes_before": {}, "repo_sizes_after": {}
        }

    # ---------- UI ----------
    def _build_ui(self):
        main = tk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        top = tk.Frame(main)
        top.pack(fill=tk.X)

        tk.Label(top, text="Parent Folder:").grid(row=0, column=0, sticky=tk.W, pady=4)
        tk.Entry(top, textvariable=self.folder_var, width=65).grid(row=0, column=1, padx=5, pady=4)
        tk.Button(top, text="Browse…", command=self.browse_folder).grid(row=0, column=2, pady=4)

        tk.Label(top, text="Commit Message:").grid(row=1, column=0, sticky=tk.W, pady=4)
        tk.Entry(top, textvariable=self.commit_msg_var, width=65).grid(row=1, column=1, padx=5, pady=4)

        tk.Label(top, text="Visibility:").grid(row=2, column=0, sticky=tk.W, pady=4)
        vis = tk.Frame(top)
        vis.grid(row=2, column=1, sticky=tk.W, pady=4)
        tk.Radiobutton(vis, text="Private", variable=self.visibility_var, value="private").pack(side=tk.LEFT)
        tk.Radiobutton(vis, text="Public",  variable=self.visibility_var, value="public").pack(side=tk.LEFT, padx=10)
        tk.Label(vis, text="License:").pack(side=tk.LEFT, padx=(20, 4))
        tk.OptionMenu(vis, self.license_type_var, *self.LICENSE_TEMPLATES.keys()).pack(side=tk.LEFT)

        opts = tk.Frame(top)
        opts.grid(row=3, column=1, sticky=tk.W, pady=4)
        tk.Checkbutton(opts, text="Dry-Run (preview only)", variable=self.dry_run_var).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(opts, text="Auto README.md", variable=self.auto_readme_var).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(opts, text="Auto LICENSE", variable=self.auto_license_var).pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(opts, text="Show recent git log after commit", variable=self.show_git_log_var, fg="blue").pack(side=tk.LEFT, padx=4)

        hist_opts = tk.Frame(top)
        hist_opts.grid(row=4, column=1, sticky=tk.W, pady=4)
        tk.Checkbutton(hist_opts, text="Squash history into 1 single commit (Recommended for bloated repos)", 
                       variable=self.squash_history_var, fg="darkred").pack(side=tk.LEFT, padx=4)
        tk.Checkbutton(hist_opts, text="Surgical history removal (requires 'git-filter-repo')", 
                       variable=self.surgical_history_var).pack(side=tk.LEFT, padx=4)

        rl = tk.Frame(top)
        rl.grid(row=5, column=1, sticky=tk.W, pady=4)
        tk.Label(rl, text="Rate limit (sec between GitHub API calls):").pack(side=tk.LEFT)
        tk.Spinbox(rl, from_=0.5, to=30.0, increment=0.5, textvariable=self.rate_limit_var, width=6).pack(side=tk.LEFT, padx=4)
        tk.Label(rl, text="(Protects your account from bans)", foreground="gray").pack(side=tk.LEFT)

        btns = tk.Frame(top)
        btns.grid(row=6, column=1, sticky=tk.W, pady=8)
        self.start_btn = tk.Button(btns, text="▶ Start", command=self.start_processing, bg="lightgreen", width=10)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        self.cancel_btn = tk.Button(btns, text="■ Cancel", command=self.cancel_processing, state=tk.DISABLED, width=10)
        self.cancel_btn.pack(side=tk.LEFT, padx=2)
        self.reset_btn = tk.Button(btns, text="Reset Progress", command=self.reset_progress, width=13)
        self.reset_btn.pack(side=tk.LEFT, padx=2)
        self.cleanup_btn = tk.Button(btns, text="Force Cleanup .git", command=self.force_cleanup_git_folders, bg="orange", width=16)
        self.cleanup_btn.pack(side=tk.LEFT, padx=2)
        self.log_btn = tk.Button(btns, text="Open Log", command=self.open_log_file, width=10)
        self.log_btn.pack(side=tk.LEFT, padx=2)
        self.clear_btn = tk.Button(btns, text="Clear Log", command=self.clear_log, width=10)
        self.clear_btn.pack(side=tk.LEFT, padx=2)
        self.report_btn = tk.Button(btns, text="Export Report", command=self.export_report, state=tk.DISABLED, width=13)
        self.report_btn.pack(side=tk.LEFT, padx=2)

        prog_frame = tk.Frame(main)
        prog_frame.pack(fill=tk.X, pady=4)
        self.progress = ttk.Progressbar(prog_frame, mode='determinate', maximum=100)
        self.progress.pack(fill=tk.X, side=tk.LEFT, expand=True)
        self.progress_label = tk.Label(prog_frame, text="Ready.", width=50, anchor=tk.W, padx=6)
        self.progress_label.pack(side=tk.RIGHT)

        stats = tk.Frame(main)
        stats.pack(fill=tk.X, pady=4)
        self.stats_label = tk.Label(stats, text="", anchor=tk.W, relief=tk.SUNKEN, padx=6, pady=4, bg="#f0f0f0")
        self.stats_label.pack(fill=tk.X)

        self.log = scrolledtext.ScrolledText(main, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        self.log.pack(fill=tk.BOTH, expand=True)
        self.log.tag_configure("success", foreground="green")
        self.log.tag_configure("fail", foreground="red")
        self.log.tag_configure("info", foreground="blue")
        self.log.tag_configure("warn", foreground="darkorange")
        self.log.tag_configure("critical", foreground="white", background="red")
        self.log.tag_configure("dryrun", foreground="purple")

    # ---------- Settings & Progress ----------
    def _load_settings(self):
        home_settings = os.path.join(os.path.expanduser("~"), ".git_creator_settings.json")
        path = home_settings if os.path.exists(home_settings) else None
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.folder_var.set(data.get("folder", ""))
            self.commit_msg_var.set(data.get("commit_msg", "Automated commit"))
            self.visibility_var.set(data.get("visibility", "private"))
            self.dry_run_var.set(data.get("dry_run", False))
            self.auto_readme_var.set(data.get("auto_readme", True))
            self.auto_license_var.set(data.get("auto_license", False))
            self.license_type_var.set(data.get("license_type", "MIT"))
            self.rate_limit_var.set(data.get("rate_limit", 3.0))
            self.squash_history_var.set(data.get("squash_history", False))
            self.surgical_history_var.set(data.get("surgical_history", False))
            self.show_git_log_var.set(data.get("show_git_log", True))
            geo = data.get("geometry")
            if geo: self.root.geometry(geo)
        except Exception:
            pass

    def _save_settings(self):
        data = {
            "folder": self.folder_var.get(), "commit_msg": self.commit_msg_var.get(),
            "visibility": self.visibility_var.get(), "dry_run": self.dry_run_var.get(),
            "auto_readme": self.auto_readme_var.get(), "auto_license": self.auto_license_var.get(),
            "license_type": self.license_type_var.get(), "rate_limit": self.rate_limit_var.get(),
            "squash_history": self.squash_history_var.get(), "surgical_history": self.surgical_history_var.get(),
            "show_git_log": self.show_git_log_var.get(),
            "geometry": self.root.geometry()
        }
        try:
            with open(os.path.join(os.path.expanduser("~"), ".git_creator_settings.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _on_close(self):
        self._save_settings()
        self.root.destroy()

    def _load_progress(self, parent_folder: str) -> bool:
        self.progress_file = os.path.join(parent_folder, ".git_creator_progress.json")
        self.completed_repos = set()
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.completed_repos = set(data.get("completed", []))
                return True
            except Exception:
                pass
        return False

    def _save_progress(self, repo_name: str):
        self.completed_repos.add(repo_name)
        try:
            with open(self.progress_file, "w", encoding="utf-8") as f:
                json.dump({"completed": sorted(self.completed_repos), "updated_at": datetime.now().isoformat()}, f, indent=2)
        except Exception as e:
            self._log(f"  ! Failed to save progress: {e}", "warn")

    def reset_progress(self):
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showinfo("Info", "Select a folder first.")
            return
        pf = os.path.join(folder, ".git_creator_progress.json")
        if os.path.exists(pf):
            if messagebox.askyesno("Reset Progress", "Delete saved progress for this folder?"):
                os.remove(pf)
                self._log("Progress file deleted.", "info")
        else:
            messagebox.showinfo("Info", "No progress file found.")

    # ---------- Force Cleanup ----------
    def force_cleanup_git_folders(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid parent folder first.")
            return
            
        if not messagebox.askyesno("Force Cleanup", 
                                   "This will DELETE all .git folders in the selected directory.\n\n"
                                   "⚠️ This will erase ALL Git history for all repositories!\n"
                                   "Continue?"):
            return
            
        self._log("=" * 60, "warn")
        self._log("FORCE CLEANUP MODE - Deleting all .git folders...", "warn")
        self._log("=" * 60, "warn")
        
        deleted = 0
        failed = 0
        
        try:
            subfolders = [
                os.path.join(folder, d)
                for d in os.listdir(folder)
                if os.path.isdir(os.path.join(folder, d))
                and not d.startswith(".")
                and d.lower() not in self.EXCLUDE_FOLDERS
            ]
        except Exception as e:
            self._log(f"Failed to list subfolders: {e}", "fail")
            return
            
        for sf in subfolders:
            git_dir = os.path.join(sf, ".git")
            if os.path.exists(git_dir):
                name = os.path.basename(sf)
                self._log(f"  · Processing {name}...", "info")
                if self._delete_git_directory(git_dir):
                    self._log(f"    ✓ Deleted .git folder", "success")
                    deleted += 1
                else:
                    self._log(f"    ✗ Failed to delete .git folder", "fail")
                    failed += 1
                    
        self._log("=" * 60, "info")
        self._log(f"Cleanup complete: {deleted} deleted, {failed} failed", "info")
        messagebox.showinfo("Cleanup Complete", f"Deleted: {deleted}\nFailed: {failed}")

    # ---------- Helpers ----------
    def browse_folder(self):
        f = filedialog.askdirectory()
        if f:
            self.folder_var.set(f)
            self._save_settings()

    def log_msg(self, msg: str, tag: Optional[str] = None):
        self.log.config(state=tk.NORMAL)
        if tag:
            self.log.insert(tk.END, msg + "\n", tag)
        else:
            self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def _log(self, msg: str, tag: Optional[str] = None):
        if hasattr(self, 'logger'):
            if tag in ("fail", "critical", "warn"):
                self.logger.warning(msg)
            else:
                self.logger.info(msg)
        self.root.after(0, lambda: self.log_msg(msg, tag))

    def clear_log(self):
        self.log.config(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.config(state=tk.DISABLED)

    def open_log_file(self):
        log_file_path = os.path.join(os.getcwd(), "git_creator_run.log")
        if os.path.exists(log_file_path):
            if os.name == 'nt':
                os.startfile(log_file_path)
            else:
                webbrowser.open(f"file://{log_file_path}")
        else:
            messagebox.showinfo("Info", "Log file does not exist yet. Run a process first.")

    def set_progress(self, current: int, total: int, label: str = ""):
        pct = (current / total * 100) if total > 0 else 0
        elapsed = time.time() - self.start_time if self.start_time else 0
        eta = ""
        if current > 0 and pct > 0:
            total_est = elapsed / (current / total)
            remaining = max(0, total_est - elapsed)
            eta = f" | ETA {int(remaining//60)}m{int(remaining%60)}s"
        text = f"{current}/{total} ({pct:.0f}%){eta}"
        if label: text += f" — {label}"
        self.root.after(0, lambda: (self.progress.config(value=pct), self.progress_label.config(text=text)))

    def set_stats(self, text: str):
        self.root.after(0, lambda: self.stats_label.config(text=text))

    def run_cmd(self, cmd: List[str], cwd: str, timeout: int = 600, dry_run_safe: bool = False, extra_env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        if self.dry_run and not dry_run_safe:
            self._log(f"  [DRY RUN] Would run: {' '.join(cmd)}", "dryrun")
            return True, "[dry-run]"
        
        if self.cancelled:
            return False, "Cancelled"

        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0 
            
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            if extra_env:
                env.update(extra_env)
            
            process = subprocess.Popen(
                cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
                text=True, startupinfo=startupinfo, env=env
            )
            
            start_time = time.time()
            while process.poll() is None:
                if self.cancelled:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return False, "Cancelled by user"
                
                time.sleep(0.2)
                if time.time() - start_time > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return False, f"Command timed out after {timeout} seconds"
            
            stdout, stderr = process.communicate()
            return process.returncode == 0, (stdout or "") + (stderr or "")
        except Exception as e:
            return False, str(e)

    def gh_cmd(self, cmd: List[str], cwd: str, timeout: int = 600, dry_run_safe: bool = False, extra_env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        delay = self.rate_limit_var.get()
        if not self.dry_run and not dry_run_safe:
            time.sleep(delay)
        return self.run_cmd(cmd, cwd, timeout, dry_run_safe, extra_env)

    def _is_disk_full(self, err_msg: str) -> bool:
        if "no space left on device" in err_msg.lower():
            self._log("  ⚠️ CRITICAL: Disk is FULL!", "critical")
            return True
        return False

    def _analyze_repo_size(self, sf: str) -> int:
        ok, out = self.run_cmd(["git", "count-objects", "-v"], sf, dry_run_safe=True)
        if not ok: return 0
        size = 0
        for line in out.splitlines():
            if line.startswith("size:") or line.startswith("size-pack:"):
                try: size += int(line.split(":")[1].strip())
                except Exception: pass
        return size

    def _format_size(self, kb: int) -> str:
        if kb < 1024: return f"{kb} KB"
        elif kb < 1024 * 1024: return f"{kb/1024:.1f} MB"
        else: return f"{kb/(1024*1024):.2f} GB"

    # ---------- Smart .git Deletion ----------
    def _delete_git_directory(self, git_dir: str) -> bool:
        if not os.path.exists(git_dir):
            return True
            
        def remove_readonly(func, path, excinfo):
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                func(path)
            except Exception:
                pass

        try:
            shutil.rmtree(git_dir, onerror=remove_readonly)
            if not os.path.exists(git_dir):
                return True
        except Exception:
            self._log("  ⚠️ Initial deletion blocked. Retrying...", "warn")

        time.sleep(2)
        try:
            shutil.rmtree(git_dir, onerror=remove_readonly)
            if not os.path.exists(git_dir):
                return True
        except Exception:
            self._log("  ⚠️ Second attempt failed. Using Windows native force-delete...", "warn")

        if os.name == 'nt':
            try:
                subprocess.run(['attrib', '-H', '-R', '-S', '/S', '/D', f'{git_dir}\\*.*'], 
                               capture_output=True, timeout=30)
                subprocess.run(['cmd', '/c', 'rd', '/s', '/q', git_dir], 
                               capture_output=True, text=True, timeout=60)
                if not os.path.exists(git_dir):
                    return True
            except Exception as e:
                self._log(f"  ✗ Windows native deletion failed: {e}", "fail")
        
        return not os.path.exists(git_dir)

    # ---------- Pre-Push Checks ----------
    def _check_for_large_files_and_repo_size(self, sf: str) -> bool:
        large_files = []
        critical_files = []
        
        for root, dirs, files in os.walk(sf):
            if '.git' in root:
                continue
            for f in files:
                fp = os.path.join(root, f)
                try:
                    size = os.path.getsize(fp)
                    if size > 100 * 1024 * 1024:
                        critical_files.append((fp, size))
                    elif size > 50 * 1024 * 1024:
                        large_files.append((fp, size))
                except Exception:
                    pass
                    
        if critical_files:
            self._log("  🚨 CRITICAL: Found files > 100MB! GitHub will REJECT the push.", "critical")
            for fp, size in critical_files[:5]:
                self._log(f"     └─ {os.path.relpath(fp, sf)} ({self._format_size(size//1024)})", "critical")
            return False
            
        if large_files:
            self._log("  ⚠️ WARNING: Found files > 50MB.", "warn")
            for fp, size in large_files[:5]:
                self._log(f"     └─ {os.path.relpath(fp, sf)} ({self._format_size(size//1024)})", "warn")
                
        git_dir = os.path.join(sf, '.git')
        if os.path.exists(git_dir):
            git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns)
            if git_size > 1024 * 1024 * 1024:
                self._log(f"  ⚠️ WARNING: .git folder is massive ({self._format_size(git_size//1024)}). Pushing may timeout.", "warn")
            elif git_size > 200 * 1024 * 1024:
                self._log(f"  ℹ️ .git folder size: {self._format_size(git_size//1024)}.", "info")
                
        return True

    def _log_recent_git_history(self, sf: str, repo_name: str):
        if not self.show_git_log_var.get():
            return
        ok, out = self.run_cmd(["git", "log", "--oneline", "-5"], sf, dry_run_safe=True)
        if ok and out.strip():
            self._log(f"  📜 Recent git log for '{repo_name}':", "info")
            for line in out.strip().split('\n'):
                self._log(f"     {line}", "info")

    # ---------- Cleanup ----------
    def _cleanup_unnecessary_files(self, sf: str):
        self._log("  · Scanning and cleaning large media/archives/dev junk...", "info")
        media_archive_exts = {'.mp4', '.wav', '.mp3', '.mov', '.mkv', '.avi', '.webm', '.flv', '.wmv',
                              '.png', '.jpg', '.jpeg', '.flac', '.aac', '.ogg', '.m4a', 
                              '.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.blend'}
        dirs_to_remove = {"__pycache__", "venv", "env", ".venv", ".pytest_cache", "node_modules", 
                         ".idea", ".vscode", "build", "dist", "model_cache", "models", "checkpoints"}

        bytes_freed = 0
        files_cleaned = 0

        ok, tracked = self.run_cmd(["git", "ls-files"], sf, dry_run_safe=True)
        if ok:
            for line in tracked.splitlines():
                fp = line.strip()
                if not fp: continue
                _, ext = os.path.splitext(fp.lower())
                if ext in media_archive_exts:
                    self.run_cmd(["git", "rm", "-r", "--cached", "--ignore-unmatch", fp], sf)

        for root, dirs, files in os.walk(sf, topdown=True):
            if ".git" in root:
                dirs[:] = []
                continue
            for d in [d for d in dirs if d in dirs_to_remove]:
                dirs.remove(d)
                dir_path = os.path.join(root, d)
                try:
                    if self.dry_run:
                        sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, ffs in os.walk(dir_path) for f in ffs)
                        self._log(f"  [DRY RUN] Would remove dir: {dir_path} ({self._format_size(sz//1024)})", "dryrun")
                        bytes_freed += sz
                    else:
                        sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, ffs in os.walk(dir_path) for f in ffs)
                        shutil.rmtree(dir_path)
                        bytes_freed += sz
                    files_cleaned += 1
                except Exception as e:
                    self._log(f"    Warning: Could not remove {dir_path}: {e}", "warn")

            for f in files:
                _, ext = os.path.splitext(f.lower())
                if ext in media_archive_exts or f in {".env", ".env.local", "Thumbs.db", ".DS_Store"} or f.endswith((".pyc", ".pyo", ".log", ".tmp", ".bak")):
                    file_path = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(file_path) if not self.dry_run else 0
                        if self.dry_run:
                            self._log(f"  [DRY RUN] Would remove file: {file_path} ({self._format_size(sz//1024)})", "dryrun")
                        else:
                            os.remove(file_path)
                        bytes_freed += sz
                        files_cleaned += 1
                    except Exception as e:
                        self._log(f"    Warning: Could not remove {file_path}: {e}", "warn")

        gitignore_path = os.path.join(sf, ".gitignore")
        default_ignores = ["# Media and Archives", "*.mp4", "*.wav", "*.mp3", "*.mov", "*.mkv", "*.avi", "*.webm",
                           "*.png", "*.jpg", "*.jpeg", "*.flac", "*.aac", "*.ogg", "*.m4a",
                           "*.zip", "*.rar", "*.7z", "*.tar", "*.gz", "*.iso", "*.blend",
                           "# AI Models", "*.bin", "*.safetensors", "*.ckpt", "*.pth", "*.pt",
                           "# Python / Dev", "__pycache__/", "*.py[cod]", ".env", ".env.local", "venv/", "env/", 
                           ".venv/", ".pytest_cache/", "*.log", "Thumbs.db", ".DS_Store", "*.tmp", "*.bak", "node_modules/",
                           "model_cache/", "models/", "checkpoints/"]
        try:
            if self.dry_run:
                self._log(f"  [DRY RUN] Would ensure .gitignore has media/dev exclusions.", "dryrun")
            elif not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(default_ignores) + "\n")
                self._log("  · Created default .gitignore.", "info")
            else:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    existing = f.read()
                missing = [ign for ign in default_ignores if ign not in existing]
                if missing:
                    with open(gitignore_path, "a", encoding="utf-8") as f:
                        f.write("\n# Added by Git Repo Creator\n")
                        f.write("\n".join(missing) + "\n")
                    self._log("  · Updated .gitignore.", "info")
        except Exception as e:
            self._log(f"  ! .gitignore warning: {e}", "warn")

        with self.stats_lock:
            self.stats["files_cleaned"] += files_cleaned
            self.stats["bytes_freed_estimate"] += bytes_freed

    # ---------- README / LICENSE ----------
    def _add_readme_and_license(self, sf: str, repo_name: str):
        if self.dry_run:
            if self.auto_readme_var.get(): self._log(f"  [DRY RUN] Would create README.md for '{repo_name}'.", "dryrun")
            if self.auto_license_var.get(): self._log(f"  [DRY RUN] Would create LICENSE ({self.license_type_var.get()}).", "dryrun")
            return

        if self.auto_readme_var.get():
            readme_path = os.path.join(sf, "README.md")
            if not os.path.exists(readme_path):
                try:
                    with open(readme_path, "w", encoding="utf-8") as f:
                        f.write(f"# {repo_name}\n\nProject repository.\n")
                    self._log("  · Created README.md.", "info")
                except Exception as e:
                    self._log(f"  ! README warning: {e}", "warn")

        if self.auto_license_var.get():
            license_path = os.path.join(sf, "LICENSE")
            if not os.path.exists(license_path):
                template = self.LICENSE_TEMPLATES.get(self.license_type_var.get(), "")
                if template:
                    try:
                        with open(license_path, "w", encoding="utf-8") as f:
                            f.write(template.format(year=datetime.now().year))
                        self._log(f"  · Created LICENSE ({self.license_type_var.get()}).", "info")
                    except Exception as e:
                        self._log(f"  ! LICENSE warning: {e}", "warn")

    # ---------- History Management ----------
    def _squash_and_push(self, sf: str, gh_name: str, visibility: str) -> bool:
        self._log("  · [SQUASH MODE] Consolidating entire history into 1 single commit...", "warn")
        self._cleanup_unnecessary_files(sf)
        
        git_dir = os.path.join(sf, ".git")
        
        if os.path.isdir(git_dir) and not self.dry_run:
            self._log("  · Deleting old .git folder to erase bloated history...", "info")
            if not self._delete_git_directory(git_dir):
                self._log("  ✗ Failed to delete .git folder. It is locked by Windows Defender, VS Code, or a background process.", "fail")
                self._log("  └─ SOLUTION: Close VS Code/File Explorer, wait 10 seconds, and manually delete the .git folder.", "warn")
                return False
            self._log("  · Deleted old .git folder successfully.", "info")
        elif os.path.isdir(git_dir) and self.dry_run:
            self._log(f"  [DRY RUN] Would delete {git_dir}", "dryrun")

        ok, err = self.run_cmd(["git", "init"], sf)
        if not ok:
            self._log(f"  ✗ git init failed: {err.strip()}", "fail")
            return False

        ok, err = self.run_cmd(["git", "add", "-A"], sf)
        if not ok:
            self._log(f"  ✗ git add failed: {err.strip()}", "fail")
            return False
            
        ok_status, status_out = self.run_cmd(["git", "status", "--porcelain"], sf)
        if ok_status and not status_out.strip():
            self._log("  · Working tree is empty after cleanup. Creating initial empty commit.", "info")
            ok, err = self.run_cmd(["git", "commit", "--allow-empty", "-m", "Squashed commit: initial empty repo"], sf)
        else:
            ok, err = self.run_cmd(["git", "commit", "-m", "Squashed commit: cleaned history and removed large media files"], sf)
            
        if not ok:
            self._log(f"  ✗ git commit failed: {err.strip()}", "fail")
            return False

        ok, remote_url = self.run_cmd(["git", "remote", "get-url", "origin"], sf, dry_run_safe=True)
        remote_url = remote_url.strip() if ok else None
        
        if not remote_url:
            self._log("  · No remote found. Creating GitHub repo...", "info")
            gh_cmd = ["gh", "repo", "create", gh_name, "--source=.", f"--{visibility}", "--remote=origin"]
            ok, err = self.gh_cmd(gh_cmd, sf)
            if not ok:
                if "already exists" in err.lower():
                    ok_url, url_out = self.gh_cmd(["gh", "repo", "view", gh_name, "--json", "url"], sf, dry_run_safe=True)
                    if ok_url:
                        try:
                            remote_url = json.loads(url_out).get("url")
                            if not remote_url.endswith(".git"): remote_url += ".git"
                            self.run_cmd(["git", "remote", "add", "origin", remote_url], sf)
                            self._log(f"  · Linked to existing repo: {remote_url}", "info")
                        except Exception: pass
                else:
                    self._log(f"  ✗ gh repo create failed: {err.strip()}", "fail")
                    return False
        else:
            if "@github.com" in remote_url and not remote_url.startswith("https://github.com"):
                clean_url = "https://" + remote_url.split("@")[1]
                self.run_cmd(["git", "remote", "set-url", "origin", clean_url], sf)
                self._log(f"  · Cleaned up token-injected remote URL.", "info")
                remote_url = clean_url
            self._log(f"  · Remote already exists: {remote_url}", "info")

        self._log("  · Force-pushing squashed history (overwrites remote)...", "info")
        return self._force_push_with_gh_fallback(sf, gh_name)

    def _surgical_history_removal(self, sf: str) -> bool:
        self._log("  · Attempting surgical history removal (git-filter-repo)...", "info")
        ok, _ = self.run_cmd(["git", "filter-repo", "--version"], sf, dry_run_safe=True)
        if not ok:
            self._log("  ✗ 'git-filter-repo' not found.", "fail")
            self._log("  └─ SOLUTION: Install it via 'pip install git-filter-repo'", "warn")
            return False
        
        extensions = [
            "*.bin", "*.safetensors", "*.ckpt", "*.pth", "*.pt",
            "*.wav", "*.mp4", "*.mp3", "*.mov", "*.mkv", "*.avi", 
            "*.png", "*.jpg", "*.jpeg",
            "*.zip", "*.rar", "*.7z", "*.tar", "*.gz", "*.iso", "*.blend"
        ]
        filter_args = ["git", "filter-repo", "--invert-paths", "--force", "--no-gc"]
        for ext in extensions:
            filter_args.extend(["--path-glob", ext])
        
        ok, err = self.run_cmd(filter_args, sf)
        if ok:
            self._log("  ✓ Surgical history removal successful.", "success")
            if not self.dry_run:
                time.sleep(1.5)
            return True
        else:
            self._log(f"  ✗ Surgical history removal failed: {err.strip()}", "fail")
            return False

    def _force_push_with_gh_fallback(self, sf: str, gh_name: str) -> bool:
        self._log("  · History was rewritten. Preparing to force-push...", "info")
        
        if not self._check_for_large_files_and_repo_size(sf):
            self._log("  ✗ Aborting push due to critical file size violations.", "fail")
            return False

        # NEW: Pre-flight connectivity check to fail fast on network blocks
        self._log("  · Verifying network connectivity to GitHub...", "info")
        ok_net, net_err = self.run_cmd(["git", "ls-remote", "origin", "HEAD"], sf, timeout=15)
        if not ok_net:
            self._log("  ✗ FAILED to reach GitHub. This is a network/firewall/proxy issue, NOT a credential issue.", "critical")
            self._log(f"  └─ Details: {net_err.strip()[:300]}", "warn")
            self._log("  └─ ACTION: Check Antivirus, Firewall, or Git proxy settings (git config --global http.proxy).", "warn")
            return False

        ok_branch, branch_name = self.run_cmd(["git", "branch", "--show-current"], sf, dry_run_safe=True)
        branch_name = branch_name.strip()
        if not branch_name:
            self._log("  ⚠️ Repo is in detached HEAD state. Creating 'main' branch...", "warn")
            self.run_cmd(["git", "checkout", "-b", "main"], sf)
            branch_name = "main"
        
        self._log(f"  · Target branch for push: '{branch_name}'", "info")
        
        ok, err = self.run_cmd(
            ["git", "push", "--force", "--set-upstream", "origin", f"{branch_name}:{branch_name}"], 
            sf, timeout=120
        )
        
        if ok:
            self._log("  ✓ Force-pushed clean history and set upstream.", "success")
            self._log_recent_git_history(sf, gh_name)
            return True
            
        self._log(f"  ✗ Initial force-push failed for '{gh_name}'.", "fail")
        err_lines = err.strip().split('\n')
        if len(err_lines) > 15:
            self._log(f"  └─ Raw error output (last 15 lines):\n" + "\n".join(err_lines[-15:]), "warn")
        else:
            self._log(f"  └─ Raw error output:\n{err.strip()}", "warn")
        
        err_lower = err.lower()
        
        # FIXED: Handle timeouts explicitly as network issues, NOT credential issues
        if "timed out" in err_lower or "timeout" in err_lower:
            git_dir = os.path.join(sf, '.git')
            git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns) if os.path.exists(git_dir) else 0
            self._log(f"  ⏱️ PUSH TIMED OUT. The .git folder is only {self._format_size(git_size//1024)}.", "critical")
            self._log("     └─ CAUSE: This is a NETWORK or LOCAL MACHINE issue, NOT a credential problem.", "critical")
            self._log("     └─ COMMON CULPRITS:", "warn")
            self._log("        1. Antivirus / Windows Defender actively scanning 'git-remote-https.exe'", "warn")
            self._log("        2. Corporate Firewall or Proxy silently dropping the connection", "warn")
            self._log("        3. Git proxy misconfiguration (check: git config --global http.proxy)", "warn")
            self._log("        4. IPv6 routing issues (try: git config --global core.sshCommand 'ssh -4')", "warn")
            self._log("  ✗ Aborting push. Please resolve the network issue before retrying.", "fail")
            return False

        # FIXED: ONLY trigger token fallback for ACTUAL authentication errors
        auth_keywords = [
            "authentication failed", "could not read password", "permission denied", 
            "fatal: could not read", "bad credentials", "invalid username or password", 
            "remote: bad credentials", "remote: invalid username or password"
        ]
        
        if any(keyword in err_lower for keyword in auth_keywords):
            self._log("  ⚠️ Push failed due to authentication. Attempting to use 'gh auth token' to bypass...", "warn")
            
            ok_gh, gh_status = self.run_cmd(["gh", "auth", "status"], sf, dry_run_safe=True)
            if not ok_gh:
                self._log(f"  ✗ 'gh auth status' failed.", "fail")
            else:
                self._log(f"  ℹ️ 'gh auth status' output: {gh_status.strip()}", "info")

            ok_tok, token = self.run_cmd(["gh", "auth", "token"], sf, dry_run_safe=True)
            if ok_tok and token.strip():
                token = token.strip()
                ok_url, current_url = self.run_cmd(["git", "remote", "get-url", "origin"], sf, dry_run_safe=True)
                if ok_url and current_url.strip():
                    current_url = current_url.strip()
                    
                    if "@" in current_url and current_url.startswith("https://"):
                        clean_url = "https://" + current_url.split("@", 1)[1]
                        self._log(f"  · Stripped leftover credentials from remote URL to prevent double-token error.", "info")
                        current_url = clean_url
                        self.run_cmd(["git", "remote", "set-url", "origin", current_url], sf)
                    
                    self._log(f"  ℹ️ Current remote URL: {current_url}", "info")
                    
                    if current_url.startswith("https://"):
                        secure_url = current_url.replace("https://", f"https://{token}@", 1)
                        self.run_cmd(["git", "remote", "set-url", "origin", secure_url], sf)
                        self._log("  · Retrying force-push with gh token injected in URL...", "info")
                        
                        try:
                            ok, push_err = self.run_cmd(
                                ["git", "push", "--force", "--set-upstream", "origin", f"{branch_name}:{branch_name}"], 
                                sf, timeout=120
                            )
                        finally:
                            # GUARANTEE cleanup of the token from the remote URL
                            self.run_cmd(["git", "remote", "set-url", "origin", current_url], sf)
                            self._log("  · Secured: Reverted remote URL to remove token.", "info")
                        
                        if ok:
                            self._log("  ✓ Force-pushed clean history using gh token.", "success")
                            self._log_recent_git_history(sf, gh_name)
                            return True
                        else:
                            self._log(f"  ✗ Force-push with token ALSO failed.", "fail")
                            push_err_lines = push_err.strip().split('\n')
                            if len(push_err_lines) > 15:
                                self._log(f"  └─ Raw error output (last 15 lines):\n" + "\n".join(push_err_lines[-15:]), "fail")
                            else:
                                self._log(f"  └─ Raw error output:\n{push_err.strip()}", "fail")
                    else:
                        self._log(f"  ⚠️ Remote URL is SSH ({current_url}). Token injection only works for HTTPS.", "warn")
                else:
                    self._log("  ✗ Could not determine remote origin URL.", "fail")
            else:
                self._log("  ✗ Failed to retrieve gh auth token.", "fail")
        else:
            self._log("  ⚠️ Push failed for a reason other than authentication or timeout.", "warn")
            
        self._log(f"  ✗ Force push ultimately failed for '{gh_name}'.", "fail")
        return False

    # ---------- Main flow ----------
    def start_processing(self):
        if self.running: return
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid parent folder.")
            return
        commit_msg = self.commit_msg_var.get().strip()
        if not commit_msg:
            messagebox.showerror("Error", "Please enter a commit message.")
            return

        for tool in ("git", "gh"):
            ok, _ = self.run_cmd([tool, "--version"], cwd=folder, timeout=30, dry_run_safe=True)
            if not ok:
                messagebox.showerror("Error", f"'{tool}' not found in PATH.\n\nPlease install it and ensure it's added to your system's PATH.")
                return

        self.dry_run = self.dry_run_var.get()
        self.running = True
        self.cancelled = False
        self.stats = self._empty_stats()
        self.start_time = time.time()
        
        log_file_path = os.path.join(os.getcwd(), "git_creator_run.log")
        self.logger.handlers = []
        fh = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S'))
        self.logger.addHandler(fh)
        self.logger.info("=== Git Repo Creator Run Started ===")
        self._log(f"📄 Log file initialized at: {log_file_path}", "info")
        
        self._log("Ensuring GitHub CLI credential helper is configured...", "info")
        self.run_cmd(["gh", "auth", "setup-git"], os.path.expanduser("~"), dry_run_safe=True)

        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(text="■ Cancel", state=tk.NORMAL)
        self.report_btn.config(state=tk.DISABLED)

        threading.Thread(target=self.process_folders, args=(folder, commit_msg), daemon=True).start()

    def cancel_processing(self):
        if not self.cancelled:
            self.cancelled = True
            self._log("⚠️ Cancellation requested. Interrupting current command...", "warn")
            self.cancel_btn.config(text="Cancelling...", state=tk.DISABLED)

    def process_folders(self, parent_folder: str, commit_msg: str):
        try:
            subfolders = sorted(
                os.path.join(parent_folder, d)
                for d in os.listdir(parent_folder)
                if os.path.isdir(os.path.join(parent_folder, d))
                and not d.startswith(".")
                and d.lower() not in self.EXCLUDE_FOLDERS
                and not d.startswith(".git_creator")
            )
        except Exception as e:
            self._log(f"Failed to list subfolders: {e}", "fail")
            self._finish()
            return

        if not subfolders:
            self._log("No valid subfolders found.", "info")
            self._finish()
            return

        resumed = self._load_progress(parent_folder)
        if resumed and self.completed_repos:
            self._log(f"Resuming: {len(self.completed_repos)} repo(s) already completed. Skipping them.\n", "info")

        total = len(subfolders)
        self.stats["total"] = total

        mode = "🔍 DRY-RUN MODE — no changes will be made" if self.dry_run else "LIVE MODE"
        self._log(f"Found {total} subfolders. [{mode}]\n", "info")

        visibility = self.visibility_var.get()

        for idx, sf in enumerate(subfolders, start=1):
            name = os.path.basename(sf)
            self.set_progress(idx, total, name)

            if self.cancelled:
                self._log("✅ Processing cancelled by user.", "warn")
                break

            if name in self.completed_repos:
                self._log(f"--- [{idx}/{total}] {name} --- (already done, skipping)", "info")
                with self.stats_lock:
                    self.stats["skipped"] += 1
                    self.stats["processed"] += 1
                continue

            gh_name = name.replace(" ", "-")
            if name != gh_name:
                self._log(f"Note: Using '{gh_name}' as GitHub repo name for '{name}'.", "info")

            self.set_stats(f"Processing {idx}/{total}: {name}")
            self._log(f"--- [{idx}/{total}] {name} ---", "info")

            size_before = self._analyze_repo_size(sf)
            with self.stats_lock:
                self.stats["repo_sizes_before"][name] = size_before

            has_git = os.path.isdir(os.path.join(sf, ".git"))
            ok = True
            action = "updated" if has_git else "created"

            if not has_git:
                ok = self._create_new_repo(sf, gh_name, commit_msg, visibility)
            else:
                ok = self._update_existing_repo(sf, gh_name, commit_msg, visibility)

            size_after = self._analyze_repo_size(sf)
            with self.stats_lock:
                self.stats["repo_sizes_after"][name] = size_after
                self.stats["processed"] += 1
                if ok:
                    self.stats[action] += 1
                    if not self.dry_run:
                        self._save_progress(name)
                else:
                    self.stats["failed"] += 1
                    self.stats["errors"].append({"repo": name, "action": action})

            self.set_stats(f"Done: {self.stats['created']} created | {self.stats['updated']} updated | {self.stats['failed']} failed | {self.stats['skipped']} skipped")

        self._finish()

    def _create_new_repo(self, sf: str, gh_name: str, commit_msg: str, visibility: str) -> bool:
        self._log(f"  · No local .git found for '{gh_name}'. Initializing...", "info")
        ok, err = self.run_cmd(["git", "init"], sf)
        if not ok:
            if self._is_disk_full(err): return False
            self._log(f"  ✗ git init failed: {err.strip()}", "fail")
            return False

        self._cleanup_unnecessary_files(sf)
        self._add_readme_and_license(sf, gh_name)

        ok, err = self.run_cmd(["git", "add", "-A"], sf)
        if not ok:
            if self._is_disk_full(err): return False
            self._log(f"  ✗ git add failed: {err.strip()}", "fail")
            return False

        ok, output = self.run_cmd(["git", "commit", "-m", commit_msg], sf)
        if ok:
            if output.strip() and "nothing to commit" not in output.lower():
                self._log(f"  · Git commit output: {output.strip()}", "info")
        elif "nothing to commit" not in output.lower():
            if self._is_disk_full(output): return False
            self._log(f"  ! git commit warning: {output.strip()}", "warn")

        self.run_cmd(["git", "gc", "--prune=now"], sf)

        if not self._check_for_large_files_and_repo_size(sf):
            self._log("  ✗ Aborting push due to critical file size violations.", "fail")
            return False
        
        gh_cmd = ["gh", "repo", "create", gh_name, "--source=.", f"--{visibility}", "--remote=origin", "--push"]
        ok, err = self.gh_cmd(gh_cmd, sf)
        if ok:
            self._log(f"  ✓ Created GitHub repo '{gh_name}' ({visibility}) and pushed.", "success")
            self._log_recent_git_history(sf, gh_name)
            return True
        elif "already exists" in err.lower():
            self._log(f"  · Repo already exists. Linking and updating...", "warn")
            return self._update_existing_repo(sf, gh_name, commit_msg, visibility)
        else:
            self._log(f"  ✗ gh repo create failed for '{gh_name}'.", "fail")
            err_lines = err.strip().split('\n')
            if len(err_lines) > 15:
                self._log(f"  └─ Raw error output (last 15 lines):\n" + "\n".join(err_lines[-15:]), "fail")
            else:
                self._log(f"  └─ Raw error output:\n{err.strip()}", "fail")
            return False

    def _update_existing_repo(self, sf: str, gh_name: str, commit_msg: str, visibility: str) -> bool:
        if self.squash_history_var.get():
            return self._squash_and_push(sf, gh_name, visibility)

        self.run_cmd(["git", "merge", "--abort"], sf)
        self.run_cmd(["git", "rebase", "--abort"], sf)

        self._cleanup_unnecessary_files(sf)
        
        surgical_ran = False
        if self.surgical_history_var.get():
            surgical_ran = self._surgical_history_removal(sf)

        ok, out = self.run_cmd(["git", "remote"], sf, dry_run_safe=True)
        remotes = out.strip().split()
        if "origin" not in remotes:
            self._log(f"  · No 'origin' remote. Linking to GitHub...", "info")
            gh_cmd = ["gh", "repo", "create", gh_name, "--source=.", f"--{visibility}", "--remote=origin"]
            ok, err = self.gh_cmd(gh_cmd, sf)
            if not ok:
                if "already exists" in err.lower() or "already a repository" in err.lower():
                    ok_url, url_out = self.gh_cmd(["gh", "repo", "view", gh_name, "--json", "url"], sf, dry_run_safe=True)
                    if not ok_url:
                        self._log(f"  ✗ Failed to get repo URL: {url_out.strip()}", "fail")
                        return False
                    try:
                        repo_url = json.loads(url_out).get("url")
                    except Exception:
                        self._log(f"  ✗ Failed to parse URL: {url_out}", "fail")
                        return False
                    if not repo_url: return False
                    if not repo_url.endswith(".git"): repo_url += ".git"
                    self.run_cmd(["git", "remote", "add", "origin", repo_url], sf)
                    self._log(f"  · Added 'origin' → {repo_url}.", "info")
                else:
                    self._log(f"  ✗ gh repo create failed: {err.strip()}", "fail")
                    return False

        ok, err = self.run_cmd(["git", "add", "-A"], sf)
        if not ok:
            if self._is_disk_full(err): return False
            self._log(f"  ✗ git add failed: {err.strip()}", "fail")
            return False

        ok, output = self.run_cmd(["git", "commit", "-m", commit_msg], sf)
        if ok:
            self._log(f"  · Committed new changes.", "info")
            if output.strip() and "nothing to commit" not in output.lower():
                self._log(f"  · Git commit output: {output.strip()}", "info")
        elif "nothing to commit" in output.lower() or "no changes added" in output.lower():
            self._log(f"  · No new changes to commit.", "info")
            has_commits, _ = self.run_cmd(["git", "rev-parse", "HEAD"], sf, dry_run_safe=True)
            if not has_commits:
                self._log(f"  · Empty repo. Creating initial commit.", "info")
                self.run_cmd(["git", "commit", "--allow-empty", "-m", "Initial commit"], sf)
        else:
            if self._is_disk_full(output): return False
            self._log(f"  ! git commit warning: {output.strip()}", "warn")

        if not surgical_ran:
            self._log("  · Running git garbage collection...", "info")
            self.run_cmd(["git", "gc", "--prune=now"], sf)
        else:
            self._log("  · Garbage collection already handled by git-filter-repo.", "info")

        if surgical_ran:
            return self._force_push_with_gh_fallback(sf, gh_name)

        if not self._check_for_large_files_and_repo_size(sf):
            self._log("  ✗ Aborting push due to critical file size violations.", "fail")
            return False

        self._log(f"  · Syncing with remote...", "info")
        pull_ok, pull_err = self.run_cmd(["git", "pull", "origin", "HEAD", "--no-edit"], sf)

        if not pull_ok:
            low = pull_err.lower()
            if "refusing to merge unrelated histories" in low:
                pull_ok, pull_err = self.run_cmd(["git", "pull", "origin", "HEAD", "--allow-unrelated-histories", "--no-edit"], sf)
                low = pull_err.lower()

            if not pull_ok:
                cf_ok, cf_out = self.run_cmd(["git", "diff", "--name-only", "--diff-filter=U"], sf, dry_run_safe=True)
                conflicted = [l for l in cf_out.splitlines() if l.strip()] if cf_ok else []

                if conflicted:
                    self._log(f"  ! Merge conflicts in {len(conflicted)} file(s). Resolving (prefer local)...", "warn")
                    self.run_cmd(["git", "checkout", "--ours", "--"] + conflicted, sf)
                    self.run_cmd(["git", "add", "-A"], sf)
                    self.run_cmd(["git", "commit", "--no-edit", "-m", "Merge remote (conflicts resolved: prefer local)"], sf)
                elif "couldn't find remote ref" in low or "no tracking information" in low:
                    self._log(f"  · Remote empty/missing HEAD, proceeding to push.", "info")
                else:
                    self._log(f"  ! git pull warning: {pull_err.strip()}", "warn")
                    self.run_cmd(["git", "merge", "--abort"], sf)

        ok, output = self.run_cmd(["git", "push", "-u", "origin", "HEAD"], sf)
        if ok:
            self._log(f"  ✓ Pushed.", "success")
            if output.strip():
                self._log(f"  · Git push output: {output.strip()}", "info")
            self._log_recent_git_history(sf, gh_name)
            return True

        self._log(f"  ✗ Initial push failed for '{gh_name}'.", "fail")
        err_lines = output.strip().split('\n')
        if len(err_lines) > 15:
            self._log(f"  └─ Raw error output (last 15 lines):\n" + "\n".join(err_lines[-15:]), "warn")
        else:
            self._log(f"  └─ Raw error output:\n{output.strip()}", "warn")

        # FIXED: Consistent timeout handling in _update_existing_repo
        if "timed out" in output.lower() or "timeout" in output.lower():
            git_dir = os.path.join(sf, '.git')
            git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns) if os.path.exists(git_dir) else 0
            self._log(f"  ⏱️ PUSH TIMED OUT. The .git folder is {self._format_size(git_size//1024)}.", "critical")
            if git_size > 200 * 1024 * 1024:
                self._log("     └─ CAUSE: The repository packfile is too large to upload within the time limit.", "critical")
            else:
                self._log("     └─ CAUSE: This is a NETWORK or LOCAL MACHINE issue, NOT a credential problem.", "critical")
                self._log("     └─ COMMON CULPRITS: Antivirus scanning 'git-remote-https.exe', Firewall/Proxy dropping connection, or Git proxy misconfiguration.", "warn")
            return False

        self._log(f"  · Push rejected. Trying --force-with-lease...", "warn")
        ok, output = self.run_cmd(["git", "push", "-u", "--force-with-lease", "origin", "HEAD"], sf, timeout=300)
        if ok:
            self._log(f"  ✓ Force-pushed (with lease).", "success")
            self._log_recent_git_history(sf, gh_name)
            return True

        self._log(f"  ✗ Force-push (with lease) also failed for '{gh_name}'.", "fail")
        err_lines = output.strip().split('\n')
        if len(err_lines) > 15:
            self._log(f"  └─ Raw error output (last 15 lines):\n" + "\n".join(err_lines[-15:]), "fail")
        else:
            self._log(f"  └─ Raw error output:\n{output.strip()}", "fail")
            
        return False

    # ---------- Report ----------
    def export_report(self):
        folder = self.folder_var.get().strip()
        if not folder: return
        path = filedialog.asksaveasfilename(defaultextension=".json", initialdir=folder,
                                            initialfile="git_creator_report.json", filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
            messagebox.showinfo("Saved", f"Report saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed: {e}")

    def _finish(self):
        def _do():
            elapsed = time.time() - self.start_time if self.start_time else 0
            mins, secs = divmod(int(elapsed), 60)

            self.set_progress(100, 100, "Done")
            self.set_stats(f"Done in {mins}m{secs}s | Created: {self.stats['created']} | Updated: {self.stats['updated']} | Failed: {self.stats['failed']} | Skipped: {self.stats['skipped']} | Cleaned: {self.stats['files_cleaned']} files ({self._format_size(self.stats['bytes_freed_estimate']//1024)})")

            self._log("", None)
            self._log("=" * 60, "info")
            self._log("FINAL REPORT", "info")
            self._log("=" * 60, "info")
            self._log(f"Total repos:        {self.stats['total']}", "info")
            self._log(f"Created (new):      {self.stats['created']}", "success")
            self._log(f"Updated (existing): {self.stats['updated']}", "success")
            self._log(f"Failed:             {self.stats['failed']}", "fail")
            self._log(f"Skipped (resume):   {self.stats['skipped']}", "info")
            self._log(f"Files cleaned:      {self.stats['files_cleaned']}", "info")
            self._log(f"Disk freed (est):   {self._format_size(self.stats['bytes_freed_estimate']//1024)}", "info")
            self._log(f"Elapsed:            {mins}m {secs}s", "info")

            size_changes = []
            for name, before in self.stats["repo_sizes_before"].items():
                after = self.stats["repo_sizes_after"].get(name, 0)
                diff = before - after
                if diff > 0:
                    size_changes.append((name, before, after, diff))
            if size_changes:
                size_changes.sort(key=lambda x: -x[3])
                self._log("\nTop repos by size reduction:", "info")
                for name, b, a, d in size_changes[:10]:
                    self._log(f"  {name}: {self._format_size(b)} → {self._format_size(a)} (saved {self._format_size(d)})", "success")

            if self.stats["errors"]:
                self._log(f"\nFailed repos ({len(self.stats['errors'])}):", "fail")
                for e in self.stats["errors"][:20]:
                    self._log(f"  • {e['repo']} ({e['action']})", "fail")

            self._log("=" * 60, "info")

            self.start_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(text="■ Cancel", state=tk.DISABLED)
            self.report_btn.config(state=tk.NORMAL)
            self.running = False
            self.cancelled = False
            self._save_settings()
        self.root.after(0, _do)


if __name__ == "__main__":
    root = tk.Tk()
    app = GitRepoCreatorApp(root)
    root.mainloop()