import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# ASCII characters
BRANCH = "|-- "
LAST = "`-- "
VERTICAL = "|   "
SPACE = "    "


class FolderTreeGenerator:

    def __init__(self):
        self.lines = []

    def format_size(self, size):
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size)
        for unit in units:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def walk(
        self,
        folder,
        prefix="",
        depth=0,
        max_depth=None,
        show_hidden=False,
        show_sizes=False,
    ):
        if max_depth is not None and depth > max_depth:
            return

        try:
            entries = sorted(
                [
                    e
                    for e in folder.iterdir()
                    if show_hidden or not e.name.startswith(".")
                ],
                key=lambda x: (x.is_file(), x.name.lower()),
            )
        except PermissionError:
            self.lines.append(prefix + "[Permission Denied]")
            return

        for i, entry in enumerate(entries):
            last = i == len(entries) - 1
            connector = LAST if last else BRANCH

            if entry.is_dir():
                self.lines.append(f"{prefix}{connector}{entry.name}/")
                extension = SPACE if last else VERTICAL
                self.walk(
                    entry,
                    prefix + extension,
                    depth + 1,
                    max_depth,
                    show_hidden,
                    show_sizes,
                )
            else:
                text = entry.name
                if show_sizes:
                    try:
                        text += (
                            f" ({self.format_size(entry.stat().st_size)})"
                        )
                    except Exception:
                        pass

                self.lines.append(prefix + connector + text)

    def generate(
        self,
        root,
        max_depth=None,
        show_hidden=False,
        show_sizes=False,
    ):
        self.lines = [str(root)]
        self.walk(
            root,
            "",
            0,
            max_depth,
            show_hidden,
            show_sizes,
        )
        return "\n".join(self.lines)


class App:

    def __init__(self, root):
        self.root = root
        self.root.title("Folder Structure Generator")
        self.root.geometry("1000x700")

        self.generator = FolderTreeGenerator()

        top = ttk.Frame(root)
        top.pack(fill="x", padx=10, pady=10)

        self.folder_var = tk.StringVar()

        ttk.Entry(
            top,
            textvariable=self.folder_var,
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            top,
            text="Browse",
            command=self.browse,
        ).pack(side="left", padx=5)

        options = ttk.Frame(root)
        options.pack(fill="x", padx=10)

        self.hidden = tk.BooleanVar()
        self.sizes = tk.BooleanVar()

        ttk.Checkbutton(
            options,
            text="Show Hidden Files",
            variable=self.hidden,
        ).pack(side="left")

        ttk.Checkbutton(
            options,
            text="Show File Sizes",
            variable=self.sizes,
        ).pack(side="left", padx=20)

        ttk.Label(options, text="Max Depth").pack(side="left")

        self.depth = tk.StringVar(value="")
        ttk.Entry(
            options,
            textvariable=self.depth,
            width=6,
        ).pack(side="left")

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", padx=10, pady=5)

        ttk.Button(
            buttons,
            text="Generate",
            command=self.generate,
        ).pack(side="left")

        ttk.Button(
            buttons,
            text="Save TXT",
            command=self.save,
        ).pack(side="left", padx=5)

        ttk.Button(
            buttons,
            text="Copy",
            command=self.copy,
        ).pack(side="left")

        self.text = tk.Text(
            root,
            wrap="none",
            font=("Consolas", 10),
        )
        self.text.pack(fill="both", expand=True, padx=10, pady=10)

        yscroll = ttk.Scrollbar(
            self.text,
            command=self.text.yview,
        )
        self.text.configure(yscrollcommand=yscroll.set)

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_var.set(folder)

    def generate(self):
        folder = self.folder_var.get()

        if not folder:
            messagebox.showerror(
                "Error",
                "Choose a folder first.",
            )
            return

        path = Path(folder)

        if not path.exists():
            messagebox.showerror(
                "Error",
                "Folder not found.",
            )
            return

        depth = None
        if self.depth.get().strip():
            try:
                depth = int(self.depth.get())
            except ValueError:
                messagebox.showerror(
                    "Error",
                    "Depth must be an integer.",
                )
                return

        tree = self.generator.generate(
            path,
            max_depth=depth,
            show_hidden=self.hidden.get(),
            show_sizes=self.sizes.get(),
        )

        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", tree)

    def save(self):
        text = self.text.get("1.0", tk.END)

        if not text.strip():
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt")],
        )

        if filename:
            with open(filename, "w", encoding="utf8") as f:
                f.write(text)

            messagebox.showinfo(
                "Saved",
                "Text file saved successfully.",
            )

    def copy(self):
        text = self.text.get("1.0", tk.END)

        self.root.clipboard_clear()
        self.root.clipboard_append(text)

        messagebox.showinfo(
            "Copied",
            "Copied to clipboard.",
        )


root = tk.Tk()
App(root)
root.mainloop()