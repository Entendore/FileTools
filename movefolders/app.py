import shutil
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

class FolderCopyApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Folder Structure Copier")
        self.root.geometry("700x500")
        self.root.resizable(True, True)

        # Variables to store paths
        self.source_path = tk.StringVar()
        self.dest_path = tk.StringVar()

        self._build_ui()

    def _build_ui(self):
        # --- Source Folder Selection ---
        frame_src = tk.Frame(self.root, padx=10, pady=10)
        frame_src.pack(fill=tk.X)
        
        tk.Label(frame_src, text="Source Folder:", width=15, anchor=tk.W).pack(side=tk.LEFT)
        tk.Entry(frame_src, textvariable=self.source_path, width=50).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        tk.Button(frame_src, text="Browse...", command=self.browse_source).pack(side=tk.LEFT)

        # --- Destination Folder Selection ---
        frame_dest = tk.Frame(self.root, padx=10, pady=5)
        frame_dest.pack(fill=tk.X)
        
        tk.Label(frame_dest, text="Destination Folder:", width=15, anchor=tk.W).pack(side=tk.LEFT)
        tk.Entry(frame_dest, textvariable=self.dest_path, width=50).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        tk.Button(frame_dest, text="Browse...", command=self.browse_dest).pack(side=tk.LEFT)

        # --- Action Button ---
        frame_action = tk.Frame(self.root, padx=10, pady=10)
        frame_action.pack(fill=tk.X)
        
        self.start_btn = tk.Button(frame_action, text="Start Copy", command=self.start_copy_process, bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
        self.start_btn.pack(pady=5)

        # --- Log Output Area ---
        frame_log = tk.Frame(self.root, padx=10, pady=5)
        frame_log.pack(fill=tk.BOTH, expand=True)
        
        tk.Label(frame_log, text="Progress Log:", anchor=tk.W).pack()
        
        self.log_text = scrolledtext.ScrolledText(frame_log, wrap=tk.WORD, height=15, state=tk.DISABLED, bg="#f4f4f4")
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def browse_source(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.source_path.set(folder)

    def browse_dest(self):
        folder = filedialog.askdirectory(title="Select Destination Folder")
        if folder:
            self.dest_path.set(folder)

    def log(self, message):
        """Thread-safe method to update the log text area."""
        self.root.after(0, lambda: self._update_log(message))

    def _update_log(self, message):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)  # Auto-scroll to the bottom
        self.log_text.config(state=tk.DISABLED)

    def start_copy_process(self):
        source = self.source_path.get().strip()
        destination = self.dest_path.get().strip()

        if not source or not destination:
            messagebox.showwarning("Input Error", "Please select both source and destination folders.")
            return

        # Disable button to prevent multiple clicks
        self.start_btn.config(state=tk.DISABLED, text="Copying...")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END) # Clear previous logs
        self.log_text.config(state=tk.DISABLED)

        # Run the copy process in a separate thread so the GUI doesn't freeze
        thread = threading.Thread(target=self._copy_worker, args=(source, destination), daemon=True)
        thread.start()

    def _copy_worker(self, source_folder, destination_folder):
        """The core copy logic running in a background thread."""
        try:
            source = Path(source_folder).resolve()
            destination = Path(destination_folder).resolve()

            if not source.exists():
                self.log(f"Error: Source folder does not exist: {source}")
                return

            if source == destination or source in destination.parents:
                self.log("Error: Destination cannot be inside the source folder.")
                return

            destination.mkdir(parents=True, exist_ok=True)

            copied = 0
            skipped = 0
            failed = 0

            self.log(f"Starting copy from:\n  {source}\nto:\n  {destination}\n" + "-"*50)

            for file in source.rglob("*"):
                if file.is_file():
                    relative_path = file.relative_to(source)
                    target = destination / relative_path

                    target.parent.mkdir(parents=True, exist_ok=True)

                    # Do not overwrite if the file already exists in the destination
                    if target.exists():
                        self.log(f"Skipped (exists): {relative_path}")
                        skipped += 1
                        continue

                    try:
                        self.log(f"Copying: {relative_path}")
                        # copy2 preserves file metadata (timestamps, etc.)
                        shutil.copy2(str(file), str(target))
                        copied += 1
                    except (OSError, shutil.Error) as e:
                        self.log(f"  ! Failed to copy {relative_path}: {e}")
                        failed += 1

            self.log("-" * 50)
            self.log(f"Finished. Copied {copied} file(s).")
            self.log(f"Skipped {skipped} existing file(s).")
            if failed > 0:
                self.log(f"Failed to copy {failed} file(s).")

        except Exception as e:
            self.log(f"An unexpected error occurred: {e}")
        finally:
            # Re-enable the button when done
            self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL, text="Start Copy"))


if __name__ == "__main__":
    # Create the main window and run the app
    root = tk.Tk()
    app = FolderCopyApp(root)
    root.mainloop()