import os
import fnmatch
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class FileFinderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Recursive File Finder")
        self.root.geometry("900x600")

        self.matches = []

        # ---------- Folder ----------
        folder_frame = ttk.Frame(root)
        folder_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(folder_frame, text="Folder:").pack(side="left")

        self.folder_var = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self.folder_var).pack(
            side="left", fill="x", expand=True, padx=5
        )

        ttk.Button(
            folder_frame,
            text="Browse...",
            command=self.browse_folder
        ).pack(side="left")

        # ---------- Pattern ----------
        pattern_frame = ttk.Frame(root)
        pattern_frame.pack(fill="x", padx=10)

        ttk.Label(pattern_frame, text="File Name / Pattern:").pack(side="left")

        self.pattern_var = tk.StringVar(value="*.txt")
        ttk.Entry(
            pattern_frame,
            textvariable=self.pattern_var,
            width=40
        ).pack(side="left", padx=5)

        ttk.Button(
            pattern_frame,
            text="Search",
            command=self.search
        ).pack(side="left", padx=5)

        # ---------- Status ----------
        self.status = ttk.Label(root, text="Ready")
        self.status.pack(anchor="w", padx=10, pady=5)

        # ---------- Results ----------
        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True, padx=10, pady=5)

        scrollbar = ttk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            frame,
            font=("Consolas", 10),
            selectmode="extended",
            yscrollcommand=scrollbar.set
        )

        self.listbox.pack(fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)

        # ---------- Buttons ----------
        button_frame = ttk.Frame(root)
        button_frame.pack(fill="x", padx=10, pady=10)

        ttk.Button(
            button_frame,
            text="Copy Selected",
            command=self.copy_selected
        ).pack(side="left")

        ttk.Button(
            button_frame,
            text="Copy All",
            command=self.copy_all
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Export TXT",
            command=self.export_txt
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Clear",
            command=self.clear
        ).pack(side="left", padx=5)

        ttk.Button(
            button_frame,
            text="Exit",
            command=root.destroy
        ).pack(side="right")

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def search(self):
        folder = self.folder_var.get().strip()
        pattern = self.pattern_var.get().strip()

        if not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder.")
            return

        self.listbox.delete(0, tk.END)
        self.matches = []

        self.status.config(text="Searching...")
        self.root.update()

        for root_dir, dirs, files in os.walk(folder):
            for file in files:
                if fnmatch.fnmatch(file, pattern):
                    path = os.path.join(root_dir, file)
                    self.matches.append(path)
                    self.listbox.insert(tk.END, path)

        if self.matches:
            self.status.config(
                text=f"Found {len(self.matches)} matching file(s)."
            )
        else:
            self.status.config(text="No matching files found.")
            self.listbox.insert(tk.END, "*** No files found ***")

    def export_txt(self):
        if not self.matches:
            messagebox.showinfo("Nothing to Export", "No results available.")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt")]
        )

        if filename:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"Matches: {len(self.matches)}\n\n")
                for item in self.matches:
                    f.write(item + "\n")

            messagebox.showinfo("Saved", "Results exported successfully.")

    def copy_selected(self):
        sel = self.listbox.curselection()

        if not sel:
            return

        text = "\n".join(self.listbox.get(i) for i in sel)

        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def copy_all(self):
        if not self.matches:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(self.matches))

    def clear(self):
        self.matches = []
        self.listbox.delete(0, tk.END)
        self.status.config(text="Ready")


if __name__ == "__main__":
    root = tk.Tk()
    app = FileFinderApp(root)
    root.mainloop()