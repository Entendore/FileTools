import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from PIL import Image

SUPPORTED_INPUT_FORMATS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".webp", ".ico"}

class ImageConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bulk Image Converter")
        self.root.geometry("650x450")
        self.root.minsize(550, 350)

        # --- Variables ---
        self.input_folder = tk.StringVar()
        self.output_folder = tk.StringVar()
        self.target_format = tk.StringVar(value="png")
        self.is_converting = False

        self._build_ui()

    def _build_ui(self):
        # Main padding container
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights so the log expands
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(3, weight=1)

        # --- Input Folder ---
        ttk.Label(main_frame, text="Input Folder:").grid(row=0, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.input_folder, state='readonly').grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5, pady=5)
        ttk.Button(main_frame, text="Browse...", command=self.browse_input).grid(row=0, column=2, pady=5)

        # --- Output Folder ---
        ttk.Label(main_frame, text="Output Folder:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.output_folder, state='readonly').grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5, pady=5)
        ttk.Button(main_frame, text="Browse...", command=self.browse_output).grid(row=1, column=2, pady=5)

        # --- Format Selection ---
        format_frame = ttk.Frame(main_frame)
        format_frame.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=10)
        
        ttk.Label(format_frame, text="Convert to:").pack(side=tk.LEFT, padx=(0, 10))
        format_options = ["png", "jpg", "jpeg", "webp", "bmp", "tiff"]
        format_menu = ttk.OptionMenu(format_frame, self.target_format, self.target_format.get(), *format_options)
        format_menu.pack(side=tk.LEFT)

        self.convert_btn = ttk.Button(format_frame, text="Start Conversion", command=self.start_conversion)
        self.convert_btn.pack(side=tk.RIGHT)

        # --- Log Panel ---
        log_label = ttk.Label(main_frame, text="Progress Log:")
        log_label.grid(row=3, column=0, columnspan=3, sticky=tk.SW, pady=(10, 0))
        
        self.log_text = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=10, state='disabled', bg="#f0f0f0", font=("Consolas", 9))
        self.log_text.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(5, 0))

    def browse_input(self):
        folder = filedialog.askdirectory(title="Select the folder containing images to convert")
        if folder:
            self.input_folder.set(folder)

    def browse_output(self):
        folder = filedialog.askdirectory(title="Select the output folder")
        if folder:
            self.output_folder.set(folder)

    def log(self, message):
        """Thread-safe logging to the text panel."""
        def _append():
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        
        # Schedule the update on the main thread
        self.root.after(0, _append)

    def start_conversion(self):
        if self.is_converting:
            return

        in_folder = self.input_folder.get()
        out_folder = self.output_folder.get()
        fmt = self.target_format.get().lower()

        if not in_folder or not out_folder:
            messagebox.showwarning("Missing Information", "Please select both an input and an output folder.")
            return

        # Clear log
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')

        # Update UI state
        self.is_converting = True
        self.convert_btn.config(state='disabled', text="Converting...")

        # Start conversion in a separate thread
        thread = threading.Thread(target=self.run_conversion, args=(in_folder, out_folder, fmt), daemon=True)
        thread.start()

        # Check when the thread is done
        self.root.after(100, lambda: self.check_thread_status(thread))

    def check_thread_status(self, thread):
        if thread.is_alive():
            self.root.after(100, lambda: self.check_thread_status(thread))
        else:
            self.is_converting = False
            self.convert_btn.config(state='normal', text="Start Conversion")
            self.log("\n--- All tasks finished ---")

    def run_conversion(self, input_folder, output_folder, target_format):
        os.makedirs(output_folder, exist_ok=True)
        converted = 0
        failed = 0

        # Normalize format for Pillow (Pillow uses JPEG and TIFF, not JPG or TIF)
        save_format = target_format.upper()
        if save_format == "JPG":
            save_format = "JPEG"
        elif save_format == "TIF":
            save_format = "TIFF"

        for root, dirs, files in os.walk(input_folder):
            # Recreate the subfolder structure
            relative_path = os.path.relpath(root, input_folder)
            dest_root = output_folder if relative_path == "." else os.path.join(output_folder, relative_path)
            os.makedirs(dest_root, exist_ok=True)

            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in SUPPORTED_INPUT_FORMATS:
                    continue

                input_path = os.path.join(root, filename)

                try:
                    with Image.open(input_path) as img:
                        base_name = os.path.splitext(filename)[0]
                        output_path = os.path.join(dest_root, f"{base_name}.{target_format}")

                        # Avoid overwriting source file if they are the exact same path
                        if os.path.abspath(input_path) == os.path.abspath(output_path):
                            output_path = os.path.join(dest_root, f"{base_name}_converted.{target_format}")

                        # Convert to RGB if saving to a format that doesn't support transparency
                        if target_format in ("jpg", "jpeg") and img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")

                        # Save using the normalized format name
                        img.save(output_path, save_format)
                        self.log(f"✓ {os.path.relpath(input_path, input_folder)}")
                        converted += 1

                except Exception as e:
                    self.log(f"✗ Failed {filename}: {e}")
                    failed += 1

        self.log(f"\nSummary: Converted {converted} images, Failed: {failed}")


if __name__ == "__main__":
    # Set up High DPI awareness on Windows for crisper text
    if os.name == 'nt':
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
        
    root = tk.Tk()
    app = ImageConverterApp(root)
    root.mainloop()