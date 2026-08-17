import os
import json
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox
from pathlib import Path

class MP4HealthChecker:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube MP4 Health Checker")
        self.root.geometry("700x500")
        
        # Check if ffprobe is installed
        if not self.check_ffprobe():
            messagebox.showerror("Dependency Missing", 
                                 "FFprobe (part of FFmpeg) was not found.\n"
                                 "Please install FFmpeg and ensure it is in your system PATH.")
            self.root.destroy()
            return

        # GUI Elements
        self.btn_select = tk.Button(root, text="Select Folder to Scan", command=self.select_folder, font=("Arial", 12))
        self.btn_select.pack(pady=10)

        self.lbl_status = tk.Label(root, text="Waiting for folder selection...", fg="gray")
        self.lbl_status.pack(pady=5)

        self.txt_log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=80, height=20, font=("Consolas", 10))
        self.txt_log.pack(pady=10, padx=10, fill=tk.BOTH, expand=True)
        
        # Tag configurations for colored text
        self.txt_log.tag_config("ok", foreground="green")
        self.txt_log.tag_config("warn", foreground="orange")
        self.txt_log.tag_config("err", foreground="red")
        self.txt_log.tag_config("info", foreground="blue")

    def check_ffprobe(self):
        """Check if ffprobe is accessible via subprocess."""
        try:
            subprocess.run(["ffprobe", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def select_folder(self):
        folder = filedialog.askdirectory(title="Select Folder Containing MP4s")
        if folder:
            self.btn_select.config(state=tk.DISABLED)
            self.lbl_status.config(text="Scanning... Please wait.")
            # Start scanning in a new thread to prevent GUI freezing
            threading.Thread(target=self.scan_folder, args=(folder,), daemon=True).start()

    def scan_folder(self, folder):
        """Recursively find MP4s and check them."""
        self.log(f"Scanning started in: {folder}\n", "info")
        
        mp4_files = list(Path(folder).rglob('*.mp4'))
        total_files = len(mp4_files)
        
        if total_files == 0:
            self.log("No MP4 files found in the selected folder.\n", "warn")
            self.reset_ui()
            return

        self.log(f"Found {total_files} MP4 files. Checking health...\n\n", "info")

        for i, filepath in enumerate(mp4_files, 1):
            self.lbl_status.config(text=f"Checking file {i} of {total_files}...")
            self.check_file_health(filepath)
            
        self.log("\n--- Scan Complete ---\n", "info")
        self.reset_ui()

    def check_file_health(self, filepath):
        """Uses ffprobe to check the file integrity and codecs."""
        filepath_str = str(filepath)
        self.log(f"Checking: {filepath.name}\n", "info")
        
        # Command to extract stream info in JSON format
        cmd = [
            "ffprobe",
            "-v", "error", # Only show errors
            "-show_format",
            "-show_streams",
            "-of", "json",
            filepath_str
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            # If ffprobe returns a non-zero exit code, the file is structurally broken
            if result.returncode != 0:
                self.log(f"  [FAILED] - File is corrupt or unreadable.\n  Error: {result.stderr.strip()}\n\n", "err")
                return

            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            
            if not streams:
                self.log("  [FAILED] - No video or audio streams found.\n\n", "err")
                return

            video_codec = None
            audio_codec = None
            
            for stream in streams:
                if stream.get("codec_type") == "video":
                    video_codec = stream.get("codec_name")
                elif stream.get("codec_type") == "audio":
                    audio_codec = stream.get("codec_name")

            issues = []
            
            # Check YouTube recommended codecs
            if video_codec and video_codec.lower() not in ["h264", "h265", "vp8", "vp9", "av1"]:
                issues.append(f"Video codec '{video_codec}' is not ideal for YouTube (Recommend H.264).")
            elif not video_codec:
                issues.append("No video stream found.")
                
            if audio_codec and audio_codec.lower() not in ["aac", "mp3", "opus", "vorbis"]:
                issues.append(f"Audio codec '{audio_codec}' is not ideal for YouTube (Recommend AAC).")
            elif not audio_codec:
                issues.append("No audio stream found.")

            if issues:
                issue_text = "; ".join(issues)
                self.log(f"  [WARNING] - {issue_text}\n\n", "warn")
            else:
                self.log(f"  [OK] - Healthy. Video: {video_codec}, Audio: {audio_codec}\n\n", "ok")

        except subprocess.TimeoutExpired:
            self.log("  [FAILED] - FFprobe took too long to respond. File might be severely corrupted.\n\n", "err")
        except Exception as e:
            self.log(f"  [FAILED] - Unexpected error: {str(e)}\n\n", "err")

    def log(self, message, tag=""):
        """Thread-safe logging to the text widget."""
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, message, tag)
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)
        self.root.update_idletasks()

    def reset_ui(self):
        """Re-enable the button after scanning."""
        self.btn_select.config(state=tk.NORMAL)
        self.lbl_status.config(text="Scan complete. Select another folder or close.")

if __name__ == "__main__":
    root = tk.Tk()
    app = MP4HealthChecker(root)
    root.mainloop()