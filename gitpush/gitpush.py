import os
import json
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext


class GitRepoCreatorApp:
    """
    GUI app that walks through subfolders of a chosen parent folder:
      - If a subfolder has no .git, it creates a new GitHub repo via `gh` and pushes.
      - If a subfolder already has .git, it commits (if needed), pulls (merging), and pushes.
    Handles missing remotes and unrelated histories gracefully.
    """

    def __init__(self, root):
        self.root = root
        self.root.title("Git Repo Creator / Updater")
        self.root.geometry("820x650")

        self.folder_var = tk.StringVar()
        self.commit_msg_var = tk.StringVar(value="Automated commit")
        self.visibility_var = tk.StringVar(value="private")
        self.running = False
        self.cancelled = False

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        top = tk.Frame(self.root, padx=10, pady=10)
        top.pack(fill=tk.X)

        tk.Label(top, text="Parent Folder:").grid(row=0, column=0, sticky=tk.W)
        tk.Entry(top, textvariable=self.folder_var, width=65).grid(row=0, column=1, padx=5)
        tk.Button(top, text="Browse…", command=self.browse_folder).grid(row=0, column=2)

        tk.Label(top, text="Commit Message:").grid(row=1, column=0, sticky=tk.W, pady=8)
        tk.Entry(top, textvariable=self.commit_msg_var, width=65).grid(row=1, column=1, padx=5, pady=8)

        tk.Label(top, text="New Repo Visibility:").grid(row=2, column=0, sticky=tk.W)
        vis = tk.Frame(top)
        vis.grid(row=2, column=1, sticky=tk.W)
        tk.Radiobutton(vis, text="Private", variable=self.visibility_var, value="private").pack(side=tk.LEFT)
        tk.Radiobutton(vis, text="Public",  variable=self.visibility_var, value="public").pack(side=tk.LEFT)

        btns = tk.Frame(top)
        btns.grid(row=3, column=1, sticky=tk.W, pady=8)
        self.start_btn = tk.Button(btns, text="Start", command=self.start_processing, bg="lightgreen", width=10)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.cancel_btn = tk.Button(btns, text="Cancel", command=self.cancel_processing, state=tk.DISABLED, width=10)
        self.cancel_btn.pack(side=tk.LEFT, padx=4)
        self.clear_btn = tk.Button(btns, text="Clear Log", command=self.clear_log, width=10)
        self.clear_btn.pack(side=tk.LEFT, padx=4)
        self.save_btn = tk.Button(btns, text="Save Log…", command=self.save_log, width=10)
        self.save_btn.pack(side=tk.LEFT, padx=4)

        # Stats bar
        stats = tk.Frame(self.root)
        stats.pack(fill=tk.X, padx=10)
        self.stats_label = tk.Label(stats, text="Ready.", anchor=tk.W, relief=tk.SUNKEN, padx=6, pady=3)
        self.stats_label.pack(fill=tk.X)

        # Log
        self.log = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.log.tag_configure("success", foreground="green")
        self.log.tag_configure("fail",    foreground="red")
        self.log.tag_configure("info",    foreground="blue")
        self.log.tag_configure("warn",    foreground="darkorange")

    # ---------- Helpers ----------
    def browse_folder(self):
        f = filedialog.askdirectory()
        if f:
            self.folder_var.set(f)

    def log_msg(self, msg, tag=None):
        self.log.config(state=tk.NORMAL)
        if tag:
            self.log.insert(tk.END, msg + "\n", tag)
        else:
            self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def _log(self, msg, tag=None):
        # thread-safe log via the main thread
        self.root.after(0, lambda: self.log_msg(msg, tag))

    def clear_log(self):
        self.log.config(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.config(state=tk.DISABLED)

    def save_log(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                             filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        self.log.config(state=tk.NORMAL)
        content = self.log.get("1.0", tk.END)
        self.log.config(state=tk.DISABLED)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Saved", f"Log saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save log:\n{e}")

    def set_stats(self, text):
        self.root.after(0, lambda: self.stats_label.config(text=text))

    def run_cmd(self, cmd, cwd, timeout=600):
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
            return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        except Exception as e:
            return False, str(e)

    # ---------- Main flow ----------
    def start_processing(self):
        if self.running:
            return
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid parent folder.")
            return
        commit_msg = self.commit_msg_var.get().strip()
        if not commit_msg:
            messagebox.showerror("Error", "Please enter a commit message.")
            return
        # Sanity-check that gh and git exist
        for tool in ("git", "gh"):
            ok, _ = self.run_cmd([tool, "--version"], cwd=folder, timeout=30)
            if not ok:
                messagebox.showerror("Error", f"'{tool}' was not found in PATH. Please install it first.")
                return

        self.running = True
        self.cancelled = False
        self.start_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        threading.Thread(target=self.process_folders, args=(folder, commit_msg), daemon=True).start()

    def cancel_processing(self):
        self.cancelled = True
        self._log("Cancellation requested — will stop after current folder.", "warn")

    def process_folders(self, parent_folder, commit_msg):
        try:
            subfolders = sorted(
                os.path.join(parent_folder, d)
                for d in os.listdir(parent_folder)
                if os.path.isdir(os.path.join(parent_folder, d)) and not d.startswith(".")
            )
        except Exception as e:
            self._log(f"Failed to list subfolders: {e}", "fail")
            self._finish(0, 0)
            return

        if not subfolders:
            self._log("No subfolders found in the chosen parent folder.", "info")
            self._finish(0, 0)
            return

        total = len(subfolders)
        self._log(f"Found {total} subfolders. Starting…\n", "info")

        successes = 0
        failures = 0
        visibility = self.visibility_var.get()  # "private" or "public"

        for idx, sf in enumerate(subfolders, start=1):
            if self.cancelled:
                self._log("Cancelled by user.", "warn")
                break

            name = os.path.basename(sf)
            # GitHub repo names cannot contain spaces
            gh_name = name.replace(" ", "-")
            if name != gh_name:
                self._log(f"Note: Using '{gh_name}' as GitHub repo name for '{name}'.", "info")

            self.set_stats(f"Processing {idx}/{total}: {name}")
            self._log(f"--- [{idx}/{total}] {name} ---", "info")

            has_git = os.path.isdir(os.path.join(sf, ".git"))
            ok = True

            if not has_git:
                ok = self._create_new_repo(sf, gh_name, commit_msg, visibility)
            else:
                ok = self._update_existing_repo(sf, gh_name, commit_msg, visibility)

            if ok:
                successes += 1
            else:
                failures += 1

        self._finish(successes, failures)

    def _create_new_repo(self, sf, gh_name, commit_msg, visibility):
        self._log(f"  · No local .git found. Initializing...", "info")
        # git init
        ok, err = self.run_cmd(["git", "init"], sf)
        if not ok:
            self._log(f"  ✗ git init failed: {err.strip()}", "fail")
            return False

        # git add -A
        ok, err = self.run_cmd(["git", "add", "-A"], sf)
        if not ok:
            self._log(f"  ✗ git add failed: {err.strip()}", "fail")
            return False

        # git commit (may fail if nothing to commit — that's fine)
        ok, err = self.run_cmd(["git", "commit", "-m", commit_msg], sf)
        if not ok and "nothing to commit" not in err.lower():
            self._log(f"  ! git commit warning: {err.strip()}", "warn")

        # gh repo create --source=. --push --<visibility> --remote=origin <name>
        gh_cmd = [
            "gh", "repo", "create", gh_name,
            "--source=.",
            f"--{visibility}",
            "--remote=origin",
            "--push",
        ]
        ok, err = self.run_cmd(gh_cmd, sf)
        if ok:
            self._log(f"  ✓ Created GitHub repo '{gh_name}' ({visibility}) and pushed.", "success")
            return True
        elif "already exists" in err.lower():
            self._log(f"  · Repo already exists on GitHub. Linking and updating...", "warn")
            return self._update_existing_repo(sf, gh_name, commit_msg, visibility)
        else:
            self._log(f"  ✗ gh repo create failed: {err.strip()}", "fail")
            return False

    def _update_existing_repo(self, sf, gh_name, commit_msg, visibility):
        # 1. Check if remote 'origin' exists
        ok, out = self.run_cmd(["git", "remote"], sf)
        remotes = out.strip().split()
        if "origin" not in remotes:
            self._log(f"  · No 'origin' remote found locally. Attempting to create/link GitHub repo...", "info")
            gh_cmd = [
                "gh", "repo", "create", gh_name,
                "--source=.",
                f"--{visibility}",
                "--remote=origin"
            ]
            ok, err = self.run_cmd(gh_cmd, sf)
            if not ok:
                if "already exists" in err.lower() or "already a repository" in err.lower():
                    self._log(f"  · GitHub repo '{gh_name}' already exists. Fetching URL and adding remote...", "info")
                    ok_url, url_out = self.run_cmd(["gh", "repo", "view", gh_name, "--json", "url"], sf)
                    if ok_url:
                        try:
                            repo_url = json.loads(url_out).get("url")
                            if repo_url:
                                if not repo_url.endswith(".git"):
                                    repo_url += ".git"
                                self.run_cmd(["git", "remote", "add", "origin", repo_url], sf)
                                self._log(f"  · Added 'origin' remote pointing to {repo_url}.", "info")
                            else:
                                self._log(f"  ✗ Failed to parse repo URL.", "fail")
                                return False
                        except Exception:
                            self._log(f"  ✗ Failed to parse repo URL: {url_out}", "fail")
                            return False
                    else:
                        self._log(f"  ✗ Failed to get repo URL for '{gh_name}': {url_out.strip()}", "fail")
                        return False
                else:
                    self._log(f"  ✗ gh repo create failed: {err.strip()}", "fail")
                    return False

        # 2. git add -A
        ok, err = self.run_cmd(["git", "add", "-A"], sf)
        if not ok:
            self._log(f"  ✗ git add failed: {err.strip()}", "fail")
            return False

        # 3. git commit (ok=False is fine if nothing to commit)
        ok, err = self.run_cmd(["git", "commit", "-m", commit_msg], sf)
        if ok:
            self._log(f"  · Committed new changes.", "info")
        elif "nothing to commit" in err.lower() or "no changes added" in err.lower():
            self._log(f"  · No new changes to commit.", "info")
        else:
            self._log(f"  ! git commit warning: {err.strip()}", "warn")

        # 4. git pull (to handle "fetch first" push rejection) - NO REBASE
        self._log(f"  · Syncing with remote to prevent push rejections...", "info")
        ok, err = self.run_cmd(["git", "pull", "origin", "HEAD", "--no-edit"], sf)
        if not ok:
            if "couldn't find remote ref" in err.lower() or "no tracking information" in err.lower():
                self._log(f"  · Remote is empty or missing HEAD, proceeding to push.", "info")
            elif "refusing to merge unrelated histories" in err.lower():
                # Fall back to allowing unrelated histories if standard merge failed
                self._log(f"  · Unrelated histories detected. Attempting merge...", "info")
                ok2, err2 = self.run_cmd(["git", "pull", "origin", "HEAD", "--allow-unrelated-histories", "--no-edit"], sf)
                if not ok2:
                    if "couldn't find remote ref" in err2.lower() or "no tracking information" in err2.lower():
                        self._log(f"  · Remote is empty or missing HEAD, proceeding to push.", "info")
                    else:
                        self._log(f"  ! git pull warning: {err2.strip()}", "warn")
            else:
                self._log(f"  ! git pull warning: {err.strip()}", "warn")

        # 5. git push -u origin HEAD
        ok, err = self.run_cmd(["git", "push", "-u", "origin", "HEAD"], sf)
        if ok:
            self._log(f"  ✓ Pushed existing repo.", "success")
            return True

        self._log(f"  ✗ git push failed: {err.strip()}", "fail")
        return False

    def _finish(self, successes, failures):
        def _do():
            self.set_stats(f"Done.  Successes: {successes}   Failures: {failures}")
            self._log("", None)
            self._log("========== SUMMARY ==========", "info")
            self._log(f"Successes: {successes}", "success")
            self._log(f"Failures : {failures}", "fail")
            self._log("=============================", "info")
            self.start_btn.config(state=tk.NORMAL)
            self.cancel_btn.config(state=tk.DISABLED)
            self.running = False
            self.cancelled = False
        self.root.after(0, _do)


if __name__ == "__main__":
    root = tk.Tk()
    app = GitRepoCreatorApp(root)
    root.mainloop()