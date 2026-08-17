import os
import tkinter as tk
from tkinter import ttk, filedialog

class FolderComparatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Multi-Folder Subfolder Comparison Tool")
        self.root.geometry("800x600")
        self.root.minsize(650, 500)

        # Store entry widgets dynamically
        self.folder_entries = []

        # --- Main Layout Frames ---
        self.top_frame = ttk.Frame(root, padding="10")
        self.top_frame.pack(fill=tk.X, side=tk.TOP)
        
        self.button_frame = ttk.Frame(root, padding=(10, 0, 10, 10))
        self.button_frame.pack(fill=tk.X, side=tk.TOP)
        
        self.bottom_frame = ttk.Frame(root, padding="10")
        self.bottom_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        # --- Scrollable Area for Folder Inputs ---
        self.canvas = tk.Canvas(self.top_frame, height=150, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.top_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # --- Action Buttons ---
        ttk.Button(self.button_frame, text="+ Add Folder", command=self.add_folder_row).pack(side=tk.LEFT, padx=5)
        self.compare_btn = ttk.Button(self.button_frame, text="Compare All Subfolders", command=self.compare)
        self.compare_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(self.button_frame, text="Clear All", command=self.clear_all).pack(side=tk.LEFT, padx=5)

        # --- Results Treeview (Better Visualization) ---
        # Frame to hold the treeview and its scrollbar
        self.tree_frame = ttk.Frame(self.bottom_frame)
        self.tree_frame.pack(fill=tk.BOTH, expand=True)
        
        self.tree = ttk.Treeview(self.tree_frame, columns=("Status"), selectmode="browse")
        self.tree.heading("#0", text="Folder / Subfolder Structure")
        self.tree.heading("Status", text="Status")
        self.tree.column("#0", width=500, anchor=tk.W)
        self.tree.column("Status", width=150, anchor=tk.W)
        
        # Add scrollbar to treeview
        tree_scroll = ttk.Scrollbar(self.tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill="y")

        # Define colors (tags) for the treeview items
        self.tree.tag_configure("header", font=("Segoe UI", 10, "bold"))
        self.tree.tag_configure("common", foreground="green")
        self.tree.tag_configure("missing", foreground="red")
        self.tree.tag_configure("complete", foreground="blue")

        # Add two default rows to start
        self.add_folder_row()
        self.add_folder_row()

    def _on_mousewheel(self, event):
        """Enables mousewheel scrolling for the folder input area."""
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def add_folder_row(self, default_path=""):
        """Dynamically adds a new row for folder selection."""
        row_index = len(self.folder_entries)
        
        row_frame = ttk.Frame(self.scrollable_frame)
        row_frame.pack(fill=tk.X, pady=2)

        ttk.Label(row_frame, text=f"Folder {row_index + 1}:").pack(side=tk.LEFT, padx=(0, 5))

        entry_var = tk.StringVar(value=default_path)
        entry = ttk.Entry(row_frame, textvariable=entry_var, width=60)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        browse_btn = ttk.Button(row_frame, text="Browse...", command=lambda ev=entry_var: self.browse_folder(ev))
        browse_btn.pack(side=tk.LEFT, padx=2)

        remove_btn = ttk.Button(row_frame, text="X", width=3, command=lambda rf=row_frame, e=entry: self.remove_folder_row(rf, e))
        remove_btn.pack(side=tk.LEFT, padx=2)

        self.folder_entries.append({"frame": row_frame, "entry": entry, "var": entry_var})

        self.update_labels()
        self.canvas.yview_moveto(1.0)

    def remove_folder_row(self, row_frame, entry_widget):
        """Removes a folder row from the UI and the tracking list."""
        if len(self.folder_entries) <= 1:
            return
            
        row_frame.destroy()
        self.folder_entries = [item for item in self.folder_entries if item["entry"] != entry_widget]
        self.update_labels()

    def update_labels(self):
        """Renumbers the labels (Folder 1, Folder 2, etc.) after add/remove."""
        for i, item in enumerate(self.folder_entries):
            for widget in item["frame"].winfo_children():
                if isinstance(widget, ttk.Label):
                    widget.config(text=f"Folder {i + 1}:")

    def clear_all(self):
        """Removes all rows, adds back one default row, and clears the treeview."""
        for item in self.folder_entries:
            item["frame"].destroy()
        self.folder_entries.clear()
        self.add_folder_row()
        
        # Clear treeview
        for item in self.tree.get_children():
            self.tree.delete(item)

    def browse_folder(self, entry_var):
        """Opens a dialog to select a folder and updates the StringVar."""
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            entry_var.set(folder_selected)

    def get_subfolders(self, path):
        """Returns a set of subfolder names in the given path."""
        return {entry.name for entry in os.scandir(path) if entry.is_dir()}

    def compare(self):
        """Main logic to compare an N number of folders and populate the Treeview."""
        # Clear previous treeview results
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Get all valid paths from the UI
        paths = []
        for item in self.folder_entries:
            path = item["var"].get().strip()
            if path:
                paths.append(path)

        # Validate inputs
        if len(paths) < 2:
            self.tree.insert("", tk.END, text="Error: Please provide at least 2 folders to compare.", tags=("missing",))
            return

        valid_sets = []
        for i, path in enumerate(paths):
            if not os.path.isdir(path):
                self.tree.insert("", tk.END, text=f"Error: Folder {i+1} path is not valid: {path}", tags=("missing",))
                return
            valid_sets.append(self.get_subfolders(path))

        # --- Comparison Logic ---
        common_folders = valid_sets[0].copy()
        for s in valid_sets[1:]:
            common_folders.intersection_update(s)

        all_folders_union = set()
        for s in valid_sets:
            all_folders_union.update(s)

        # 1. Populate the "Common to ALL" section
        common_node = self.tree.insert("", tk.END, text=f"✅ Common to ALL folders ({len(common_folders)})", tags=("header",))
        for name in sorted(common_folders):
            self.tree.insert(common_node, tk.END, text=name, values=("Common",), tags=("common",))
        
        # Expand the common section by default
        self.tree.item(common_node, open=True)

        # 2. Check if they match perfectly
        if common_folders == all_folders_union and len(common_folders) == len(all_folders_union):
            self.tree.insert("", tk.END, text="✅ SUCCESS: All folders match perfectly!", tags=("header",))
            return

        # 3. Populate per-folder missing items
        self.tree.insert("", tk.END, text="⚠️ Missing Subfolders (Per Folder)", tags=("header",))
        
        for i, path in enumerate(paths):
            folder_name = os.path.basename(path)
            missing = all_folders_union - valid_sets[i]
            
            # Insert a parent node for the folder
            folder_node = self.tree.insert("", tk.END, text=f"📁 Folder {i+1}: {folder_name}", tags=("header",))
            
            if missing:
                # Insert the missing subfolders as children
                for name in sorted(missing):
                    self.tree.insert(folder_node, tk.END, text=name, values=("Missing",), tags=("missing",))
            else:
                # If this folder has nothing missing
                self.tree.insert(folder_node, tk.END, text="Contains all known subfolders", values=("Complete",), tags=("complete",))
            
            # Expand the folder nodes so the missing items are visible immediately
            self.tree.item(folder_node, open=True)


if __name__ == "__main__":
    root = tk.Tk()
    app = FolderComparatorApp(root)
    root.mainloop()