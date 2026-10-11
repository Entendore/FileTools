import os
import re
import json
import subprocess
import threading
import time
import shutil
import stat
import signal
import logging
import webbrowser
import tempfile
from html import escape
from typing import List, Dict, Tuple, Optional, Set
import concurrent.futures
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPlainTextEdit,
    QProgressBar, QGroupBox, QFileDialog, QMessageBox, QScrollArea, QTextEdit
)
from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QFont, QColor, QTextCursor


class WorkerSignals(QObject):
    log_signal = Signal(str, str, str)
    progress_signal = Signal(int, int, str)
    step_signal = Signal(int, int, str, str)
    stats_signal = Signal(str)
    finished_signal = Signal()
    repo_start_signal = Signal(str)
    repo_done_signal = Signal(str, bool)
    thread_progress_signal = Signal(int, str, str, int, str)


class ThreadProgressRow(QWidget):
    def __init__(self, thread_id: int, parent=None):
        super().__init__(parent)
        self.thread_id = thread_id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)
        
        self.thread_label = QLabel(f"T-{thread_id}")
        self.thread_label.setStyleSheet("color: #569cd6; font-weight: bold; font-size: 11px; min-width: 30px;")
        layout.addWidget(self.thread_label)
        
        self.repo_label = QLabel("Idle")
        self.repo_label.setStyleSheet("color: #d4d4d4; font-size: 11px; min-width: 150px; max-width: 200px;")
        self.repo_label.setWordWrap(False)
        layout.addWidget(self.repo_label)
        
        self.step_label = QLabel("💤 Waiting...")
        self.step_label.setStyleSheet("color: #808080; font-size: 11px; min-width: 180px; max-width: 250px;")
        self.step_label.setWordWrap(False)
        layout.addWidget(self.step_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setFixedWidth(120)
        self.progress_bar.setStyleSheet(self._progress_bar_style("#3c3c3c"))
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("💤")
        self.status_label.setStyleSheet("font-size: 14px; min-width: 20px;")
        layout.addWidget(self.status_label)
        layout.addStretch()
    
    def _progress_bar_style(self, color: str, bg: str = "#2d2d30") -> str:
        return f"""
            QProgressBar {{ background-color: {bg}; border: 1px solid #3c3c3c; border-radius: 3px; text-align: center; color: #d4d4d4; font-weight: bold; font-size: 10px; }}
            QProgressBar::chunk {{ background-color: {color}; border-radius: 2px; }}
        """
    
    def update_progress(self, repo_name: str, step_name: str, progress_pct: int, status: str):
        if repo_name:
            display_name = repo_name if len(repo_name) <= 25 else repo_name[:22] + "..."
            self.repo_label.setText(display_name)
            self.repo_label.setToolTip(repo_name)
        else:
            self.repo_label.setText("Idle")
            self.repo_label.setToolTip("")
        self.step_label.setText(step_name if step_name else "💤 Waiting...")
        self.progress_bar.setValue(progress_pct)
        self.progress_bar.setFormat(f"{progress_pct}%")
        color_map = {"running": "#2ea043", "success": "#4ec9b0", "warning": "#e3b341", "error": "#f44747", "idle": "#3c3c3c"}
        self.progress_bar.setStyleSheet(self._progress_bar_style(color_map.get(status, "#3c3c3c")))
        icon_map = {"running": "⏳", "success": "✅", "warning": "⚠️", "error": "❌", "idle": "💤"}
        self.status_label.setText(icon_map.get(status, "💤"))


class GitRepoCreatorApp(QMainWindow):
    """
    Git Repo Creator / Updater v6.7 (PySide6)
    
    ★ NEW IMPROVEMENTS:
    - Professional 3-column layout for Advanced Cleanup Filters (saves vertical space, adds tooltips).
    - Highly informative, actionable error messages for GitHub rate limits (explains cause, action, and tips).
    """

    EXCLUDE_FOLDERS: Set[str] = {"input", "output", "temp", "venv", ".venv", "__pycache__",
                                 "node_modules", "build", "dist"}

    STEPS_NEW = ["Cleanup", "Init", "Add Files", "Commit", "Create Remote", "Push"]
    STEPS_UPDATE = ["Cleanup", "Add Files", "Commit", "Sync Remote", "Push"]
    STEPS_SQUASH = ["Cleanup", "Delete .git", "Init", "Add Files", "Commit", "Force Push"]

    LICENSE_TEMPLATES: Dict[str, str] = {
        "None": "",
        "MIT": """MIT License\n\nCopyright (c) {year}\n\nPermission is hereby granted...""",
        "Apache 2.0": """                                 Apache License\n                           Version 2.0, January 2004...""",
        "GPLv3": """This program is free software: you can redistribute it and/or modify..."""
    }

    DEFAULT_EXTENSIONS = [
        "*.bin", "*.safetensors", "*.ckpt", "*.pth", "*.pt", "*.gguf", "*.onnx",
        "*.wav", "*.mp4", "*.mp3", "*.mov", "*.mkv", "*.avi",
        "*.png", "*.jpg", "*.jpeg",
        "*.zip", "*.rar", "*.7z", "*.tar", "*.gz", "*.iso", "*.blend"
    ]

    DEFAULT_MEDIA_EXTS = {
        '.mp4', '.wav', '.mp3', '.mov', '.mkv', '.avi', '.webm', '.flv', '.wmv',
        '.png', '.jpg', '.jpeg', '.flac', '.aac', '.ogg', '.m4a',
        '.zip', '.rar', '.7z', '.tar', '.gz', '.iso', '.blend',
        '.safetensors', '.bin', '.ckpt', '.pth', '.pt', '.gguf', '.onnx'
    }

    DEFAULT_DIRS_TO_REMOVE = {
        "__pycache__", "venv", "env", ".venv", ".pytest_cache", "node_modules",
        ".idea", ".vscode", "build", "dist", "model_cache", "models", "checkpoints"
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Git Repo Creator / Updater v6.7 (PySide6)")
        self.resize(1100, 950)

        self.logger = logging.getLogger("GitRepoCreator")
        self.logger.setLevel(logging.INFO)
        self._thread_local = threading.local()

        self.signals = WorkerSignals()
        self.signals.log_signal.connect(self.append_log)
        self.signals.progress_signal.connect(self.update_progress)
        self.signals.step_signal.connect(self.update_step_progress)
        self.signals.stats_signal.connect(self.update_stats)
        self.signals.finished_signal.connect(self.finish_processing)
        self.signals.repo_start_signal.connect(self.on_repo_start)
        self.signals.repo_done_signal.connect(self.on_repo_done)
        self.signals.thread_progress_signal.connect(self.update_thread_progress)

        self.running = False
        self.cancelled = False
        self.dry_run = False
        self._exiting = False

        self.stats_lock = threading.Lock()
        self.progress_lock = threading.Lock()
        self.gh_rate_limit_lock = threading.Lock()
        self._active_processes_lock = threading.Lock()
        self._active_processes: Set[subprocess.Popen] = set()

        self.stats = self._empty_stats()
        self.progress_file: Optional[str] = None
        self.completed_repos: Set[str] = set()
        self.start_time: Optional[float] = None
        self._session_start_time: Optional[float] = None
        self._saved_data = {}
        self._worker_thread: Optional[threading.Thread] = None
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None

        self._current_repo_name = ""
        self._repo_success_count = 0
        self._repo_fail_count = 0
        self._rate_limited_repos: List[Dict] = []
        self._rate_limited_lock = threading.Lock()
        self._global_progress_file = os.path.join(os.getcwd(), "git_creator_progress.json")
        self._thread_progress_rows: Dict[int, ThreadProgressRow] = {}
        self._thread_progress_lock = threading.Lock()

        self._load_settings()
        self._build_ui()
        self._apply_settings_to_ui()
        self._install_signal_handlers()
        self._build_thread_progress_ui()

        self._cancel_watchdog = QTimer(self)
        self._cancel_watchdog.setInterval(500)
        self._cancel_watchdog.timeout.connect(self._check_cancel_complete)

    def _empty_stats(self) -> Dict:
        return {
            "total": 0, "processed": 0, "created": 0, "updated": 0,
            "failed": 0, "skipped": 0, "files_cleaned": 0,
            "bytes_freed_estimate": 0, "errors": [],
            "repo_sizes_before": {}, "repo_sizes_after": {}
        }

    def _install_signal_handlers(self):
        try:
            signal.signal(signal.SIGINT, self._sigint_handler)
            timer = QTimer(self)
            timer.start(200)
            timer.timeout.connect(lambda: None)
        except Exception:
            pass

    def _sigint_handler(self, signum, frame):
        self._log("⚠️ SIGINT received (Ctrl+C). Initiating graceful cancel...", "warn")
        QTimer.singleShot(0, self.cancel_processing)

    def _build_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        basic_group = QGroupBox("Basic Settings")
        basic_layout = QGridLayout()
        self.folder_edit = QLineEdit()
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.browse_folder)
        basic_layout.addWidget(QLabel("Parent Folder:"), 0, 0)
        basic_layout.addWidget(self.folder_edit, 0, 1)
        basic_layout.addWidget(browse_btn, 0, 2)

        self.commit_edit = QLineEdit()
        self.commit_edit.setText("Automated commit")
        basic_layout.addWidget(QLabel("Commit Message:"), 1, 0)
        basic_layout.addWidget(self.commit_edit, 1, 1, 1, 2)

        self.visibility_combo = QComboBox()
        self.visibility_combo.addItems(["private", "public"])
        basic_layout.addWidget(QLabel("Visibility:"), 2, 0)
        basic_layout.addWidget(self.visibility_combo, 2, 1)

        self.license_combo = QComboBox()
        self.license_combo.addItems(list(self.LICENSE_TEMPLATES.keys()))
        basic_layout.addWidget(QLabel("License:"), 2, 2)
        basic_layout.addWidget(self.license_combo, 2, 3)

        self.rate_limit_spin = QDoubleSpinBox()
        self.rate_limit_spin.setRange(0.5, 30.0)
        self.rate_limit_spin.setSingleStep(0.5)
        self.rate_limit_spin.setValue(3.0)
        basic_layout.addWidget(QLabel("Rate Limit (sec):"), 3, 0)
        basic_layout.addWidget(self.rate_limit_spin, 3, 1)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 16)
        self.threads_spin.setValue(3)
        self.threads_spin.valueChanged.connect(self._on_threads_changed)
        basic_layout.addWidget(QLabel("Threads:"), 3, 2)
        basic_layout.addWidget(self.threads_spin, 3, 3)
        basic_group.setLayout(basic_layout)
        scroll_layout.addWidget(basic_group)

        opts_group = QGroupBox("Options")
        opts_layout = QGridLayout()
        self.dry_run_check = QCheckBox("Dry-Run (preview only)")
        self.auto_readme_check = QCheckBox("Auto README.md")
        self.auto_readme_check.setChecked(True)
        self.auto_license_check = QCheckBox("Auto LICENSE")
        self.show_log_check = QCheckBox("Show recent git log after commit")
        self.show_log_check.setChecked(True)
        self.squash_check = QCheckBox("Squash history into 1 single commit")
        self.squash_check.setStyleSheet("color: #f44747; font-weight: bold;")
        self.surgical_check = QCheckBox("Surgical history removal (requires 'git-filter-repo')")
        opts_layout.addWidget(self.dry_run_check, 0, 0)
        opts_layout.addWidget(self.auto_readme_check, 0, 1)
        opts_layout.addWidget(self.auto_license_check, 0, 2)
        opts_layout.addWidget(self.show_log_check, 1, 0)
        opts_layout.addWidget(self.squash_check, 1, 1)
        opts_layout.addWidget(self.surgical_check, 1, 2)
        opts_group.setLayout(opts_layout)
        scroll_layout.addWidget(opts_group)

        # ★ IMPROVED: 3-Column Grid Layout for Cleanup Filters
        cleanup_group = QGroupBox("Advanced Cleanup Filters")
        cleanup_layout = QGridLayout()
        
        cleanup_layout.addWidget(QLabel("Extensions to remove from Git history:"), 0, 0)
        self.extensions_edit = QPlainTextEdit()
        self.extensions_edit.setPlainText("\n".join(self.DEFAULT_EXTENSIONS))
        self.extensions_edit.setMaximumHeight(150)
        self.extensions_edit.setToolTip("These patterns will be removed from Git history. Use wildcards like *.bin")
        cleanup_layout.addWidget(self.extensions_edit, 1, 0)
        
        cleanup_layout.addWidget(QLabel("Media/Archive/AI files to untrack & delete locally:"), 0, 1)
        self.media_exts_edit = QPlainTextEdit()
        self.media_exts_edit.setPlainText("\n".join(sorted(list(self.DEFAULT_MEDIA_EXTS))))
        self.media_exts_edit.setMaximumHeight(150)
        self.media_exts_edit.setToolTip("These files will be untracked and deleted locally to prevent >100MB GitHub push rejections.")
        cleanup_layout.addWidget(self.media_exts_edit, 1, 1)
        
        cleanup_layout.addWidget(QLabel("Directories to remove completely:"), 0, 2)
        self.dirs_edit = QPlainTextEdit()
        self.dirs_edit.setPlainText("\n".join(sorted(list(self.DEFAULT_DIRS_TO_REMOVE))))
        self.dirs_edit.setMaximumHeight(150)
        self.dirs_edit.setToolTip("These directories will be completely deleted from the local filesystem.")
        cleanup_layout.addWidget(self.dirs_edit, 1, 2)
        
        # Make columns evenly distributed
        cleanup_layout.setColumnStretch(0, 1)
        cleanup_layout.setColumnStretch(1, 1)
        cleanup_layout.setColumnStretch(2, 1)
        
        cleanup_group.setLayout(cleanup_layout)
        scroll_layout.addWidget(cleanup_group)

        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start")
        self.start_btn.setStyleSheet("background-color: #2ea043; color: white; font-weight: bold; padding: 6px;")
        self.start_btn.clicked.connect(self.start_processing)
        self.cancel_btn = QPushButton("■ Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet("background-color: #d73a49; color: white; padding: 6px;")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.force_quit_btn = QPushButton("⚠ Force Quit")
        self.force_quit_btn.setStyleSheet("background-color: #721c24; color: white; font-weight: bold; padding: 6px;")
        self.force_quit_btn.setVisible(False)
        self.force_quit_btn.clicked.connect(self.force_quit)
        self.reset_btn = QPushButton("Reset Progress")
        self.reset_btn.clicked.connect(self.reset_progress)
        self.cleanup_btn = QPushButton("Force Cleanup .git")
        self.cleanup_btn.setStyleSheet("background-color: #e3b341; padding: 6px;")
        self.cleanup_btn.clicked.connect(self.force_cleanup_git_folders)
        self.log_btn = QPushButton("Open Log")
        self.log_btn.clicked.connect(self.open_log_file)
        self.global_progress_btn = QPushButton("📊 View Global Progress")
        self.global_progress_btn.clicked.connect(self.open_global_progress)
        self.clear_btn = QPushButton("Clear Log")
        self.clear_btn.clicked.connect(self.clear_log)
        self.report_btn = QPushButton("Export Report")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self.export_report)

        for btn in [self.start_btn, self.cancel_btn, self.force_quit_btn, self.reset_btn, self.cleanup_btn, self.log_btn, self.global_progress_btn, self.clear_btn, self.report_btn]:
            btn_layout.addWidget(btn)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        progress_group = QGroupBox("Overall Progress")
        progress_group.setStyleSheet("QGroupBox { font-weight: bold; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 10px; padding-top: 16px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }")
        progress_layout = QVBoxLayout()
        progress_layout.addWidget(QLabel("📦 Repositories", styleSheet="color: #569cd6; font-weight: bold; font-size: 12px;"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(28)
        self.progress_bar.setStyleSheet(self._progress_bar_style("#2ea043"))
        progress_layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("Ready.")
        self.progress_label.setStyleSheet("color: #808080; font-size: 11px; padding: 2px 0;")
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(QLabel("⚙️ Current Operation (Overall)", styleSheet="color: #ce9178; font-weight: bold; font-size: 12px; margin-top: 6px;"))
        self.step_progress_bar = QProgressBar()
        self.step_progress_bar.setRange(0, 100)
        self.step_progress_bar.setValue(0)
        self.step_progress_bar.setTextVisible(True)
        self.step_progress_bar.setFixedHeight(22)
        self.step_progress_bar.setStyleSheet(self._progress_bar_style("#569cd6"))
        progress_layout.addWidget(self.step_progress_bar)
        self.step_label = QLabel("Idle")
        self.step_label.setStyleSheet("color: #808080; font-size: 11px; padding: 2px 0;")
        progress_layout.addWidget(self.step_label)

        stats_row = QHBoxLayout()
        self.stat_created = QLabel("✅ 0"); self.stat_created.setStyleSheet("color: #4ec9b0; font-weight: bold; font-size: 12px;"); stats_row.addWidget(self.stat_created)
        self.stat_updated = QLabel("🔄 0"); self.stat_updated.setStyleSheet("color: #569cd6; font-weight: bold; font-size: 12px;"); stats_row.addWidget(self.stat_updated)
        self.stat_failed = QLabel("❌ 0"); self.stat_failed.setStyleSheet("color: #f44747; font-weight: bold; font-size: 12px;"); stats_row.addWidget(self.stat_failed)
        self.stat_deferred = QLabel("⏳ 0"); self.stat_deferred.setStyleSheet("color: #e3b341; font-weight: bold; font-size: 12px;"); stats_row.addWidget(self.stat_deferred)
        self.stat_skipped = QLabel("⏭️ 0"); self.stat_skipped.setStyleSheet("color: #808080; font-weight: bold; font-size: 12px;"); stats_row.addWidget(self.stat_skipped)
        self.stat_cleaned = QLabel("🧹 0 files"); self.stat_cleaned.setStyleSheet("color: #c586c0; font-weight: bold; font-size: 12px;"); stats_row.addWidget(self.stat_cleaned)
        stats_row.addStretch()
        self.stat_elapsed = QLabel("⏱️ 0m 0s"); self.stat_elapsed.setStyleSheet("color: #d4d4d4; font-size: 12px;"); stats_row.addWidget(self.stat_elapsed)
        progress_layout.addLayout(stats_row)
        progress_group.setLayout(progress_layout)
        main_layout.addWidget(progress_group)

        self.thread_progress_group = QGroupBox("Thread Progress (Per-Worker Status)")
        self.thread_progress_group.setStyleSheet("QGroupBox { font-weight: bold; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 6px; margin-top: 10px; padding-top: 16px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }")
        self.thread_progress_layout = QVBoxLayout()
        self.thread_progress_group.setLayout(self.thread_progress_layout)
        main_layout.addWidget(self.thread_progress_group)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("background-color: #2d2d30; color: #d4d4d4; padding: 6px; border: 1px solid #3c3c3c; border-radius: 4px; font-size: 11px;")
        self.stats_label.setVisible(False)
        main_layout.addWidget(self.stats_label)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 9))
        self.log_output.setStyleSheet("QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 4px; padding: 6px; }")
        main_layout.addWidget(self.log_output)

    def _build_thread_progress_ui(self):
        for row in self._thread_progress_rows.values():
            self.thread_progress_layout.removeWidget(row)
            row.deleteLater()
        self._thread_progress_rows.clear()
        for i in range(1, self.threads_spin.value() + 1):
            row = ThreadProgressRow(i)
            self._thread_progress_rows[i] = row
            self.thread_progress_layout.addWidget(row)

    def _on_threads_changed(self, value: int):
        if not self.running:
            self._build_thread_progress_ui()

    def _progress_bar_style(self, color: str, bg: str = "#2d2d30") -> str:
        return f"QProgressBar {{ background-color: {bg}; border: 1px solid #3c3c3c; border-radius: 4px; text-align: center; color: #d4d4d4; font-weight: bold; font-size: 11px; }} QProgressBar::chunk {{ background-color: {color}; border-radius: 3px; }}"

    def _set_progress_color(self, state: str):
        self.progress_bar.setStyleSheet(self._progress_bar_style({"running": "#2ea043", "warning": "#e3b341", "error": "#d73a49", "done": "#569cd6", "idle": "#3c3c3c", "paused": "#ce9178"}.get(state, "#2ea043")))

    def _set_step_color(self, state: str):
        self.step_progress_bar.setStyleSheet(self._progress_bar_style({"running": "#569cd6", "success": "#4ec9b0", "warning": "#e3b341", "error": "#f44747", "idle": "#3c3c3c"}.get(state, "#569cd6")))

    def get_extensions(self) -> List[str]:
        return [ext.strip() for ext in self.extensions_edit.toPlainText().split('\n') if ext.strip()]

    def get_media_exts(self) -> Set[str]:
        exts = set()
        for ext in self.media_exts_edit.toPlainText().split('\n'):
            ext = ext.strip().lower()
            if ext:
                if not ext.startswith('.'): ext = '.' + ext
                exts.add(ext)
        return exts

    def get_dirs_to_remove(self) -> Set[str]:
        return {d.strip() for d in self.dirs_edit.toPlainText().split('\n') if d.strip()}

    def _load_settings(self):
        path = os.path.join(os.path.expanduser("~"), ".git_creator_settings.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f: self._saved_data = json.load(f)
            except Exception: self._saved_data = {}

    def _apply_settings_to_ui(self):
        data = self._saved_data
        if not data: return
        self.folder_edit.setText(data.get("folder", ""))
        self.commit_edit.setText(data.get("commit_msg", "Automated commit"))
        for combo, key in [(self.visibility_combo, "visibility"), (self.license_combo, "license_type")]:
            idx = combo.findText(data.get(key, combo.itemText(0)))
            if idx >= 0: combo.setCurrentIndex(idx)
        self.rate_limit_spin.setValue(data.get("rate_limit", 3.0))
        self.threads_spin.setValue(data.get("concurrency", 3))
        self.dry_run_check.setChecked(data.get("dry_run", False))
        self.auto_readme_check.setChecked(data.get("auto_readme", True))
        self.auto_license_check.setChecked(data.get("auto_license", False))
        self.show_log_check.setChecked(data.get("show_git_log", True))
        self.squash_check.setChecked(data.get("squash_history", False))
        self.surgical_check.setChecked(data.get("surgical_history", False))
        for edit, key in [(self.extensions_edit, "extensions"), (self.media_exts_edit, "media_exts"), (self.dirs_edit, "dirs_to_remove")]:
            if key in data: edit.setPlainText(data[key])
        if "geometry" in data:
            try: self.restoreGeometry(bytes.fromhex(data["geometry"]))
            except Exception: pass

    def _save_settings(self):
        data = {
            "folder": self.folder_edit.text(), "commit_msg": self.commit_edit.text(),
            "visibility": self.visibility_combo.currentText(), "dry_run": self.dry_run_check.isChecked(),
            "auto_readme": self.auto_readme_check.isChecked(), "auto_license": self.auto_license_check.isChecked(),
            "license_type": self.license_combo.currentText(), "rate_limit": self.rate_limit_spin.value(),
            "concurrency": self.threads_spin.value(), "squash_history": self.squash_check.isChecked(),
            "surgical_history": self.surgical_check.isChecked(), "show_git_log": self.show_log_check.isChecked(),
            "extensions": self.extensions_edit.toPlainText(), "media_exts": self.media_exts_edit.toPlainText(),
            "dirs_to_remove": self.dirs_edit.toPlainText(), "geometry": self.saveGeometry().toHex().data().decode('utf-8')
        }
        try:
            with open(os.path.join(os.path.expanduser("~"), ".git_creator_settings.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception: pass

    def closeEvent(self, event):
        if self._exiting:
            event.accept()
            return
        if self.running:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Processing in Progress")
            msg_box.setText("A background task is still running.")
            msg_box.setInformativeText("• 'Cancel && Exit' stops gracefully.\n• 'Force Quit' hard-kills everything.\n• 'Keep Running' continues processing.")
            msg_box.setIcon(QMessageBox.Icon.Warning)
            cancel_btn = msg_box.addButton("Cancel && Exit", QMessageBox.ButtonRole.AcceptRole)
            force_btn = msg_box.addButton("Force Quit", QMessageBox.ButtonRole.DestructiveRole)
            keep_btn = msg_box.addButton("Keep Running", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(keep_btn)
            msg_box.exec()
            clicked = msg_box.clickedButton()
            if clicked == keep_btn:
                event.ignore()
                return
            elif clicked == force_btn:
                self._exiting = True
                self.force_quit()
                QTimer.singleShot(500, lambda: event.accept())
                return
            else:
                self._exiting = True
                self.cancel_processing()
                self._wait_and_close(event)
                return
        else:
            self._save_settings()
            event.accept()

    def _wait_and_close(self, event):
        def _check():
            if self._worker_thread and self._worker_thread.is_alive():
                QTimer.singleShot(500, lambda: self._wait_and_close(event))
            else:
                try:
                    if hasattr(self, 'signals'):
                        for sig in [self.signals.log_signal, self.signals.progress_signal, self.signals.step_signal, self.signals.stats_signal, self.signals.finished_signal, self.signals.repo_start_signal, self.signals.repo_done_signal, self.signals.thread_progress_signal]:
                            sig.disconnect()
                except Exception: pass
                self._save_settings()
                event.accept()
        _check()

    def force_quit(self):
        self._log("⚠️ FORCE QUIT — terminating all subprocesses...", "critical")
        self.cancelled = True
        self.terminate_all_subprocesses()
        if self._executor:
            try: self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception: pass
        self._cancel_watchdog.stop()
        self._exiting = True
        self._save_settings()
        QTimer.singleShot(300, lambda: QApplication.instance().quit())

    def _on_cancel_clicked(self):
        if not self.running: return
        self.cancel_processing()

    def cancel_processing(self):
        if self.cancelled and not self.running: return
        if not self.cancelled:
            self.cancelled = True
            self._log("⚠️ Cancellation requested. Terminating active subprocesses...", "warn")
            self._set_progress_color("paused")
            self._set_step_color("warning")
            self.step_label.setText("⏸️ Cancelling...")
            self.terminate_all_subprocesses()
            self.cancel_btn.setText("Cancelling...")
            self.cancel_btn.setEnabled(False)
            self.force_quit_btn.setVisible(True)
            self._cancel_watchdog.start()

    def _check_cancel_complete(self):
        if not self.running:
            self._cancel_watchdog.stop()
            return
        if self._worker_thread and not self._worker_thread.is_alive():
            self._cancel_watchdog.stop()
            self._log("✓ All threads have finished. Cleanup complete.", "success")

    def terminate_all_subprocesses(self):
        with self._active_processes_lock:
            procs = list(self._active_processes)
        if not procs: return
        self._log(f"  · Terminating {len(procs)} active subprocess(es)...", "warn")
        for p in procs:
            try:
                if p.poll() is None: p.terminate()
            except Exception: pass
        time.sleep(0.5)
        with self._active_processes_lock:
            still_alive = [p for p in self._active_processes if p.poll() is None]
        for p in still_alive:
            try:
                self._log(f"  · Force-killing stubborn process (PID {p.pid})...", "warn")
                p.kill()
            except Exception: pass
        time.sleep(0.3)
        with self._active_processes_lock:
            self._active_processes.clear()

    def _load_progress(self, parent_folder: str) -> bool:
        self.progress_file = os.path.join(parent_folder, ".git_creator_progress.json")
        self.completed_repos = set()
        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    self.completed_repos = set(json.load(f).get("completed", []))
                return True
            except Exception: pass
        return False

    def _save_progress(self, repo_name: str):
        with self.progress_lock:
            self.completed_repos.add(repo_name)
            data = {"completed": sorted(self.completed_repos), "updated_at": datetime.now().isoformat()}
            try:
                dir_name = os.path.dirname(self.progress_file)
                fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
                    if os.path.exists(self.progress_file): os.remove(self.progress_file)
                    os.rename(temp_path, self.progress_file)
                except Exception:
                    try: os.remove(temp_path)
                    except Exception: pass
                    raise
            except Exception as e:
                self._log(f"  ! Failed to save progress: {e}", "warn")

    def _update_global_progress(self, current: int, total: int, repo_name: str = "", status: str = "running"):
        elapsed = time.time() - self._session_start_time if self._session_start_time else 0
        with self.stats_lock:
            stats_snapshot = {
                "created": self.stats["created"], "updated": self.stats["updated"],
                "failed": self.stats["failed"], "skipped": self.stats["skipped"],
                "files_cleaned": self.stats["files_cleaned"], "bytes_freed": self.stats["bytes_freed_estimate"],
                "deferred": len(self._rate_limited_repos)
            }
        data = {
            "session_id": datetime.now().strftime("%Y%m%d_%H%M%S"), "started_at": self._session_start_time,
            "elapsed_seconds": int(elapsed), "current": current, "total": total,
            "percentage": int((current / total * 100) if total > 0 else 0), "current_repo": repo_name,
            "status": status, "stats": stats_snapshot, "cancelled": self.cancelled, "updated_at": datetime.now().isoformat()
        }
        try:
            cwd = os.getcwd()
            self._global_progress_file = os.path.join(cwd, "git_creator_progress.json")
            fd, temp_path = tempfile.mkstemp(dir=cwd, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
                if os.path.exists(self._global_progress_file): os.remove(self._global_progress_file)
                os.rename(temp_path, self._global_progress_file)
            except Exception:
                try: os.remove(temp_path)
                except Exception: pass
        except Exception: pass

    def reset_progress(self):
        folder = self.folder_edit.text().strip()
        if not folder:
            QMessageBox.information(self, "Info", "Select a folder first.")
            return
        pf = os.path.join(folder, ".git_creator_progress.json")
        if os.path.exists(pf):
            if QMessageBox.question(self, "Reset Progress", "Delete saved progress for this folder?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                os.remove(pf)
                self._log("Progress file deleted.", "info")
        else:
            QMessageBox.information(self, "Info", "No progress file found.")

    def force_cleanup_git_folders(self):
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.critical(self, "Error", "Please select a valid parent folder first.")
            return
        if QMessageBox.question(self, "Force Cleanup", "This will DELETE all .git folders in the selected directory.\n\n⚠️ This will erase ALL Git history for all repositories!\nContinue?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._log("=" * 60, "warn")
        self._log("FORCE CLEANUP MODE - Deleting all .git folders...", "warn")
        self._log("=" * 60, "warn")
        deleted, failed = 0, 0
        try:
            subfolders = [os.path.join(folder, d) for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d)) and not d.startswith(".") and d.lower() not in self.EXCLUDE_FOLDERS]
        except Exception as e:
            self._log(f"Failed to list subfolders: {e}", "fail")
            return
        for sf in subfolders:
            git_dir = os.path.join(sf, ".git")
            if os.path.exists(git_dir):
                self._log(f"  · Processing {os.path.basename(sf)}...", "info")
                if self._delete_git_directory(git_dir):
                    self._log(f"    ✓ Deleted .git folder", "success")
                    deleted += 1
                else:
                    self._log(f"    ✗ Failed to delete .git folder", "fail")
                    failed += 1
        self._log("=" * 60, "info")
        self._log(f"Cleanup complete: {deleted} deleted, {failed} failed", "info")
        QMessageBox.information(self, "Cleanup Complete", f"Deleted: {deleted}\nFailed: {failed}")

    def _get_thread_id(self) -> int:
        match = re.search(r'_(\d+)$', threading.current_thread().name)
        return int(match.group(1)) + 1 if match else 0

    def _get_thread_label(self) -> str:
        thread_name = threading.current_thread().name
        if thread_name == "MainThread": return "[Main]"
        match = re.search(r'_(\d+)$', thread_name)
        tid = str(int(match.group(1)) + 1) if match else thread_name
        repo_name = getattr(self._thread_local, 'current_repo', None)
        return f"[T-{tid} | {repo_name}]" if repo_name else f"[T-{tid}]"

    def _safe_emit(self, signal_func, *args):
        try:
            if hasattr(self, 'signals') and self.signals is not None:
                signal_func(*args)
        except RuntimeError as e:
            if "Signal source has been deleted" not in str(e): raise

    def append_log(self, msg: str, tag: str, thread_label: str):
        color_map = {"success": "#4ec9b0", "fail": "#f44747", "info": "#569cd6", "warn": "#ce9178", "critical": "#ffffff", "dryrun": "#c586c0", "trace": "#808080"}
        bg_map = {"critical": "#721c24", "dryrun": "#2d1f3d", "trace": "#252526"}
        color, bg = color_map.get(tag, "#d4d4d4"), bg_map.get(tag, "transparent")
        safe_msg = escape(msg).replace('\n', '<br>')
        bg_style = f" background-color: {bg};" if bg != "transparent" else ""
        html_msg = f'<span style="color: #808080; font-weight: bold;">{escape(thread_label)}</span> <span style="color: {color};{bg_style}">{safe_msg}</span><br>'
        cursor = self.log_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html_msg)
        if self.log_output.document().blockCount() > 5000:
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor, 1000)
            cursor.removeSelectedText()
            cursor.deleteChar()
        self.log_output.setTextCursor(cursor)
        self.log_output.ensureCursorVisible()

    def update_progress(self, current: int, total: int, label: str):
        pct = int((current / total * 100) if total > 0 else 0)
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(f"{current}/{total}  ({pct}%)")
        elapsed = time.time() - self.start_time if self.start_time else 0
        eta = ""
        if current > 0 and pct > 0:
            total_est = elapsed / (current / total)
            remaining = max(0, total_est - elapsed)
            eta = f" | ETA {int(remaining // 60)}m{int(remaining % 60)}s"
        text = f"Repos: {current}/{total} ({pct}%){eta}"
        if label: text += f" — Last: {label}"
        self.progress_label.setText(text)
        with self.stats_lock:
            failed = self.stats["failed"]
        if failed > 0 and current > 0:
            fail_ratio = failed / current
            if fail_ratio > 0.5: self._set_progress_color("error")
            elif fail_ratio > 0.2: self._set_progress_color("warning")
            else: self._set_progress_color("running")
        else:
            self._set_progress_color("running")

    def update_step_progress(self, current_step: int, total_steps: int, step_name: str, status: str):
        pct = int((current_step / total_steps) * 100) if total_steps > 0 else 0
        self.step_progress_bar.setValue(pct)
        self.step_progress_bar.setFormat(f"Step {current_step}/{total_steps}  ({pct}%)")
        self.step_label.setText(f"{{'running': '⏳', 'success': '✅', 'warning': '⚠️', 'error': '❌', 'idle': '💤'}.get(status, '⏳')} {step_name}")
        self._set_step_color(status)

    def update_thread_progress(self, thread_id: int, repo_name: str, step_name: str, progress_pct: int, status: str):
        with self._thread_progress_lock:
            if thread_id in self._thread_progress_rows:
                self._thread_progress_rows[thread_id].update_progress(repo_name, step_name, progress_pct, status)

    def on_repo_start(self, repo_name: str):
        self._current_repo_name = repo_name
        self.step_progress_bar.setRange(0, 0)
        self.step_label.setText(f"⏳ Starting: {repo_name}")
        self._set_step_color("running")

    def on_repo_done(self, repo_name: str, success: bool):
        self.step_progress_bar.setRange(0, 100)
        if success:
            self._repo_success_count += 1
            self.step_progress_bar.setValue(100)
            self.step_label.setText(f"✅ Completed: {repo_name}")
            self._set_step_color("success")
        else:
            self._repo_fail_count += 1
            self.step_label.setText(f"❌ Failed: {repo_name}")
            self._set_step_color("error")

    def update_stats(self, text: str):
        self.stats_label.setText(text)
        with self.stats_lock:
            self.stat_created.setText(f"✅ {self.stats['created']}")
            self.stat_updated.setText(f"🔄 {self.stats['updated']}")
            self.stat_failed.setText(f"❌ {self.stats['failed']}")
            self.stat_deferred.setText(f"⏳ {len(self._rate_limited_repos)}")
            self.stat_skipped.setText(f"⏭️ {self.stats['skipped']}")
            self.stat_cleaned.setText(f"🧹 {self.stats['files_cleaned']} files")
        elapsed = time.time() - self.start_time if self.start_time else 0
        mins, secs = divmod(int(elapsed), 60)
        self.stat_elapsed.setText(f"⏱️ {mins}m {secs}s")

    def _log(self, msg: str, tag: str = "info"):
        thread_label = self._get_thread_label()
        if hasattr(self, 'logger'):
            if tag in ("fail", "critical", "warn"): self.logger.warning(f"{thread_label} {msg}")
            else: self.logger.info(f"{thread_label} {msg}")
        self._safe_emit(self.signals.log_signal.emit, msg, tag, thread_label)

    def _report_step(self, step_name: str, current: int, total: int, status: str = "running"):
        self._safe_emit(self.signals.step_signal.emit, current, total, step_name, status)
        thread_id = self._get_thread_id()
        if thread_id > 0:
            repo_name = getattr(self._thread_local, 'current_repo', "")
            progress_pct = int((current / total) * 100) if total > 0 else 0
            self._safe_emit(self.signals.thread_progress_signal.emit, thread_id, repo_name, step_name, progress_pct, status)

    def _report_thread_file_progress(self, files_added: int, total_files: int):
        thread_id = self._get_thread_id()
        if thread_id > 0 and total_files > 0:
            repo_name = getattr(self._thread_local, 'current_repo', "")
            progress_pct = int((files_added / total_files) * 100)
            self._safe_emit(self.signals.thread_progress_signal.emit, thread_id, repo_name, f"Adding files ({files_added}/{total_files})", progress_pct, "running")

    def clear_log(self):
        self.log_output.clear()

    def browse_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Select Parent Folder")
        if f: self.folder_edit.setText(f)

    def open_log_file(self):
        log_file_path = os.path.join(os.getcwd(), "git_creator_run.log")
        if os.path.exists(log_file_path):
            if os.name == 'nt': os.startfile(log_file_path)
            else: webbrowser.open(f"file://{log_file_path}")
        else:
            QMessageBox.information(self, "Info", "Log file does not exist yet. Run a process first.")

    def open_global_progress(self):
        if os.path.exists(self._global_progress_file):
            if os.name == 'nt': os.startfile(self._global_progress_file)
            else: webbrowser.open(f"file://{self._global_progress_file}")
        else:
            QMessageBox.information(self, "Info", "No global progress file found yet. Start processing first.")

    def run_cmd(self, cmd: List[str], cwd: str, timeout: int = 600, dry_run_safe: bool = False, extra_env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        if self.dry_run and not dry_run_safe:
            self._log(f"  [DRY RUN] Would run: {' '.join(cmd)}", "dryrun")
            return True, "[dry-run]"
        if self.cancelled: return False, "Cancelled"
        process = None
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            env["GIT_EDITOR"] = "true"
            env["GPG_TTY"] = ""
            if extra_env: env.update(extra_env)
            process = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo, env=env)
            with self._active_processes_lock:
                self._active_processes.add(process)
            stdout_lines, stderr_lines = [], []
            def read_out():
                try:
                    for line in process.stdout: stdout_lines.append(line)
                except Exception: pass
            def read_err():
                try:
                    for line in process.stderr: stderr_lines.append(line)
                except Exception: pass
            t_out = threading.Thread(target=read_out, daemon=True)
            t_err = threading.Thread(target=read_err, daemon=True)
            t_out.start()
            t_err.start()
            start_time = time.time()
            while process.poll() is None:
                if self.cancelled:
                    try: process.terminate(); process.wait(timeout=2)
                    except subprocess.TimeoutExpired: process.kill()
                    return False, "Cancelled by user"
                if time.time() - start_time > timeout:
                    try: process.terminate(); process.wait(timeout=2)
                    except subprocess.TimeoutExpired: process.kill()
                    return False, f"Command timed out after {timeout} seconds"
                time.sleep(0.1)
            t_out.join(timeout=2)
            t_err.join(timeout=2)
            return process.returncode == 0, "".join(stdout_lines) + "".join(stderr_lines)
        except Exception as e:
            return False, str(e)
        finally:
            if process is not None:
                with self._active_processes_lock:
                    self._active_processes.discard(process)

    def gh_cmd(self, cmd: List[str], cwd: str, timeout: int = 600, dry_run_safe: bool = False, extra_env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        delay = self.rate_limit_spin.value()
        if not self.dry_run and not dry_run_safe:
            with self.gh_rate_limit_lock:
                for _ in range(int(delay * 10)):
                    if self.cancelled: return False, "Cancelled"
                    time.sleep(0.1)
        return self.run_cmd(cmd, cwd, timeout, dry_run_safe, extra_env)

    def _is_disk_full(self, err_msg: str) -> bool:
        if "no space left on device" in (err_msg or "").lower():
            self._log("  ⚠️ CRITICAL: Disk is FULL!", "critical")
            return True
        return False

    def _analyze_repo_size(self, sf: str) -> int:
        ok, out = self.run_cmd(["git", "count-objects", "-v"], sf, dry_run_safe=True)
        if not ok: return 0
        size = 0
        for line in out.splitlines():
            if line.startswith("size:") or line.startswith("size-pack:"):
                try: size += int(line.split(":", 1)[1].strip())
                except Exception: pass
        return size

    def _format_size(self, kb: int) -> str:
        if kb < 1024: return f"{kb} KB"
        elif kb < 1024 * 1024: return f"{kb / 1024:.1f} MB"
        else: return f"{kb / (1024 * 1024):.2f} GB"

    def _count_files_and_size(self, sf: str) -> Tuple[int, int]:
        file_count, total_size = 0, 0
        for root, dirs, files in os.walk(sf):
            if '.git' in root: continue
            for f in files:
                fp = os.path.join(root, f)
                try:
                    file_count += 1
                    total_size += os.path.getsize(fp)
                except Exception: pass
        return file_count, total_size

    def _delete_git_directory(self, git_dir: str) -> bool:
        if not os.path.exists(git_dir): return True
        def remove_readonly(func, path, excinfo):
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                func(path)
            except Exception: pass
        try:
            shutil.rmtree(git_dir, onerror=remove_readonly)
            if not os.path.exists(git_dir): return True
        except Exception:
            self._log("  ⚠️ Initial deletion blocked. Retrying...", "warn")
        time.sleep(2)
        try:
            shutil.rmtree(git_dir, onerror=remove_readonly)
            if not os.path.exists(git_dir): return True
        except Exception:
            self._log("  ⚠️ Second attempt failed. Using Windows native force-delete...", "warn")
        if os.name == 'nt':
            try:
                subprocess.run(['attrib', '-H', '-R', '-S', '/S', '/D', f'{git_dir}\\*.*'], capture_output=True, timeout=30)
                subprocess.run(['cmd', '/c', 'rd', '/s', '/q', git_dir], capture_output=True, text=True, timeout=60)
                if not os.path.exists(git_dir): return True
            except Exception as e:
                self._log(f"  ✗ Windows native deletion failed: {e}", "fail")
        return not os.path.exists(git_dir)

    def _check_for_large_files_and_repo_size(self, sf: str) -> bool:
        large_files, critical_files = [], []
        for root, dirs, files in os.walk(sf):
            if '.git' in root: continue
            for f in files:
                fp = os.path.join(root, f)
                try:
                    size = os.path.getsize(fp)
                    if size > 100 * 1024 * 1024: critical_files.append((fp, size))
                    elif size > 50 * 1024 * 1024: large_files.append((fp, size))
                except Exception: pass
        if critical_files:
            self._log("  🚨 CRITICAL: Found files > 100MB! GitHub will REJECT the push.", "critical")
            for fp, size in critical_files[:5]:
                self._log(f"     └─ {os.path.relpath(fp, sf)} ({self._format_size(size // 1024)})", "critical")
            return False
        if large_files:
            self._log("  ⚠️ WARNING: Found files > 50MB.", "warn")
            for fp, size in large_files[:5]:
                self._log(f"     └─ {os.path.relpath(fp, sf)} ({self._format_size(size // 1024)})", "warn")
        git_dir = os.path.join(sf, '.git')
        if os.path.exists(git_dir):
            git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns)
            if git_size > 1024 * 1024 * 1024:
                self._log(f"  ⚠️ WARNING: .git folder is massive ({self._format_size(git_size // 1024)}). Pushing may timeout.", "warn")
            elif git_size > 200 * 1024 * 1024:
                self._log(f"  ℹ️ .git folder size: {self._format_size(git_size // 1024)}.", "info")
        return True

    def _log_recent_git_history(self, sf: str, repo_name: str):
        if not self.show_log_check.isChecked(): return
        ok, out = self.run_cmd(["git", "log", "--oneline", "-5"], sf, dry_run_safe=True)
        if ok and out.strip():
            self._log(f"  📜 Recent git log for '{repo_name}':", "info")
            for line in out.strip().split('\n'):
                self._log(f"     {line}", "info")

    def _check_gitattributes(self, sf: str) -> bool:
        gitattributes = os.path.join(sf, ".gitattributes")
        if not os.path.exists(gitattributes): return False
        try:
            with open(gitattributes, "r", encoding="utf-8") as f: content = f.read()
            content_lower = content.lower()
            has_lfs = "filter=lfs" in content_lower or "diff=lfs" in content_lower or "lfs" in content_lower
            has_filter = "filter=" in content_lower and "filter=lfs" not in content_lower
            if has_lfs:
                self._log(f"  🚨 .gitattributes contains LFS patterns!", "critical")
                self._log(f"     └─ This can cause git add to hang if git-lfs isn't configured.", "critical")
                for line in content.split('\n')[:10]:
                    if line.strip(): self._log(f"        {line}", "warn")
                return True
            elif has_filter:
                self._log(f"  ⚠️ .gitattributes contains custom filter patterns.", "warn")
                for line in content.split('\n')[:5]:
                    if line.strip(): self._log(f"        {line}", "warn")
                return True
        except Exception as e:
            self._log(f"  ! Could not read .gitattributes: {e}", "warn")
        return False

    def _run_git_add_with_monitoring(self, sf: str, timeout: int = 60, total_files: int = 0) -> Tuple[bool, str]:
        self._log(f"  · Running git add -A with trace (timeout: {timeout}s)...", "info")
        index_lock = os.path.join(sf, ".git", "index.lock")
        if os.path.exists(index_lock):
            self._log(f"  🔧 Found stale index.lock — removing...", "warn")
            try:
                os.remove(index_lock)
                self._log(f"  ✓ Removed index.lock", "success")
            except Exception as e:
                self._log(f"  ✗ Could not remove index.lock: {e}", "fail")
        has_problematic_attrs = self._check_gitattributes(sf)
        if has_problematic_attrs:
            self._log(f"  💡 TIP: Consider deleting .gitattributes or installing git-lfs.", "warn")
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_TRACE"] = "1"
        env["GIT_TRACE_PERFORMANCE"] = "1"
        env["GIT_TRACE_SETUP"] = "1"
        env["GIT_TRACE_PACKET"] = "1"
        env["GIT_FSMONITOR_DAEMON"] = "0"
        env["core.fsmonitor"] = "false"
        process = None
        try:
            process = subprocess.Popen(["git", "add", "-A", "--verbose"], cwd=sf, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, startupinfo=startupinfo, env=env, bufsize=1)
            with self._active_processes_lock:
                self._active_processes.add(process)
            stderr_lines, stdout_lines = [], []
            stderr_lock, stdout_lock = threading.Lock(), threading.Lock()
            files_added_count = [0]
            def read_stderr():
                try:
                    for line in process.stderr:
                        with stderr_lock:
                            stderr_lines.append(line.rstrip())
                            line_lower = line.lower()
                            if any(kw in line_lower for kw in ["trace:", "error", "fatal", "wait", "lock", "lfs", "filter", "credential", "index"]):
                                self._log(f"     [git-trace] {line.rstrip()}", "trace")
                except Exception: pass
            def read_stdout():
                try:
                    for line in process.stdout:
                        with stdout_lock:
                            stdout_lines.append(line.rstrip())
                            if line.strip().startswith("add "):
                                files_added_count[0] += 1
                                if total_files > 0 and files_added_count[0] % 10 == 0:
                                    self._report_thread_file_progress(files_added_count[0], total_files)
                except Exception: pass
            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            stderr_thread.start()
            stdout_thread.start()
            start_time = time.time()
            last_progress = start_time
            while process.poll() is None:
                if self.cancelled:
                    process.terminate()
                    return False, "Cancelled"
                elapsed = time.time() - start_time
                if elapsed - last_progress > 3:
                    with stderr_lock: trace_count = len(stderr_lines)
                    self._log(f"  ⏳ git add still running... ({int(elapsed)}s, {trace_count} trace lines, {files_added_count[0]} files added)", "warn")
                    if os.path.exists(index_lock):
                        try:
                            os.remove(index_lock)
                            self._log(f"  ✓ Removed index.lock", "success")
                        except Exception as e:
                            self._log(f"  ✗ Could not remove index.lock: {e}", "fail")
                    last_progress = time.time()
                if elapsed > timeout:
                    try: process.terminate(); process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill()
                    self._log(f"  ⏱️ GIT ADD TIMED OUT after {int(elapsed)}s!", "critical")
                    self._log(f"     └─ Files added so far: {files_added_count[0]}", "critical")
                    with stderr_lock:
                        last_lines = stderr_lines[-30:]
                        full_trace = "\n".join(stderr_lines).lower()
                    if last_lines:
                        self._log(f"  📋 Last {len(last_lines)} git trace lines (diagnostic):", "warn")
                        for line in last_lines: self._log(f"     {line}", "warn")
                    if "lfs" in full_trace:
                        self._log(f"  🎯 DIAGNOSIS: Git LFS is involved!", "critical")
                        gitattributes = os.path.join(sf, ".gitattributes")
                        if os.path.exists(gitattributes):
                            try:
                                os.remove(gitattributes)
                                self._log(f"  🔧 Auto-fix: Deleted .gitattributes. Retrying...", "success")
                                ok, err = self.run_cmd(["git", "add", "-A"], sf, timeout=60)
                                if ok: return True, "Success after auto-fix"
                            except Exception as e:
                                self._log(f"  ✗ Could not delete .gitattributes: {e}", "fail")
                    elif "index" in full_trace or "lock" in full_trace:
                        self._log(f"  🎯 DIAGNOSIS: Git index/lock issue detected!", "critical")
                        if os.path.exists(index_lock):
                            try:
                                os.remove(index_lock)
                                self._log(f"  🔧 Auto-fix: Removed index.lock. Retrying...", "success")
                                ok, err = self.run_cmd(["git", "add", "-A"], sf, timeout=60)
                                if ok: return True, "Success after removing index.lock"
                            except Exception as e:
                                self._log(f"  ✗ Could not remove index.lock: {e}", "fail")
                    elif "filter" in full_trace:
                        self._log(f"  🎯 DIAGNOSIS: A git filter/smudge hook is involved.", "critical")
                    elif "credential" in full_trace:
                        self._log(f"  🎯 DIAGNOSIS: Git is trying to access credentials.", "critical")
                    else:
                        self._log(f"  🎯 DIAGNOSIS: Git is hanging in file scanning phase.", "critical")
                        self._log(f"     └─ LIKELY CAUSE: NTFS journaling or file system lock", "warn")
                    return False, f"Command timed out after {int(elapsed)} seconds"
                time.sleep(0.3)
            stderr_thread.join(timeout=2)
            stdout_thread.join(timeout=2)
            stdout = "\n".join(stdout_lines)
            with stderr_lock: stderr = "\n".join(stderr_lines)
            if total_files > 0:
                self._report_thread_file_progress(files_added_count[0], total_files)
            return process.returncode == 0, stdout + stderr
        except Exception as e:
            return False, str(e)
        finally:
            if process is not None:
                with self._active_processes_lock:
                    self._active_processes.discard(process)

    def _batch_git_add(self, sf: str, total_files: int) -> bool:
        batch_size = 1000
        added = 0
        all_files = []
        for root, dirs, files in os.walk(sf):
            if '.git' in root: continue
            for f in files:
                all_files.append(os.path.relpath(os.path.join(root, f), sf))
        total_batches = (len(all_files) + batch_size - 1) // batch_size
        for i in range(0, len(all_files), batch_size):
            if self.cancelled: return False
            batch = all_files[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            self._log(f"  · Adding batch {batch_num}/{total_batches} ({len(batch)} files)...", "info")
            thread_id = self._get_thread_id()
            if thread_id > 0:
                repo_name = getattr(self._thread_local, 'current_repo', "")
                progress_pct = int((added / total_files) * 100) if total_files > 0 else 0
                self._safe_emit(self.signals.thread_progress_signal.emit, thread_id, repo_name, f"Batch {batch_num}/{total_batches}", progress_pct, "running")
            cmd = ["git", "add", "--"] + batch
            ok, err = self.run_cmd(cmd, sf, timeout=max(120, len(batch) * 0.5))
            if not ok:
                self._log(f"  ✗ Batch {batch_num} failed: {err.strip()[:200]}", "fail")
                return False
            added += len(batch)
            self._log(f"  ✓ Added {added:,}/{len(all_files):,} files ({(added / len(all_files)) * 100:.1f}%)", "success")
        return True

    def _verbose_git_commit(self, sf: str, commit_msg: str, allow_empty: bool = False, timeout: int = 300) -> Tuple[bool, str]:
        ok_status, status_out = self.run_cmd(["git", "status", "--short"], sf, dry_run_safe=True)
        staged_files = [l for l in status_out.strip().split('\n') if l.strip()] if ok_status and status_out.strip() else []
        self._log(f"  📋 Staged files for commit: {len(staged_files)} file(s)", "info")
        if not staged_files and not allow_empty:
            self._log(f"  · Nothing to commit (working tree clean). Skipping commit step.", "info")
            return True, "nothing to commit"
        if staged_files:
            for line in staged_files[:15]: self._log(f"     {line}", "info")
            if len(staged_files) > 15: self._log(f"     ... and {len(staged_files) - 15} more files", "warn")
        ok_stat, stat_out = self.run_cmd(["git", "diff", "--cached", "--stat"], sf, dry_run_safe=True)
        if ok_stat and stat_out.strip():
            stat_lines = stat_out.strip().split('\n')
            self._log(f"  📊 Change summary: {stat_lines[-1].strip()}", "info")
            if len(stat_lines) <= 6:
                for line in stat_lines[:-1]: self._log(f"     {line.strip()}", "info")
        git_dir = os.path.join(sf, '.git')
        if os.path.exists(git_dir):
            git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns)
            self._log(f"  💾 Current .git folder size: {self._format_size(git_size // 1024)}", "info")
        cmd = ["git", "commit", "-m", commit_msg, "--no-verify"]
        if allow_empty: cmd.append("--allow-empty")
        self._log(f"  ⏳ Running git commit (timeout: {timeout}s)...", "info")
        commit_start = time.time()
        ok, output = self.run_cmd(cmd, sf, timeout=timeout)
        commit_elapsed = time.time() - commit_start
        if ok:
            if "nothing to commit" in output.lower():
                self._log(f"  · Nothing to commit (working tree clean).", "info")
            else:
                self._log(f"  ✓ Commit successful in {commit_elapsed:.1f}s.", "success")
                ok_hash, hash_out = self.run_cmd(["git", "log", "-1", "--oneline"], sf, dry_run_safe=True)
                if ok_hash and hash_out.strip(): self._log(f"  📌 {hash_out.strip()}", "success")
        else:
            if "nothing to commit" in output.lower() or "no changes added" in output.lower():
                self._log(f"  · Nothing to commit.", "info")
            elif "timed out" in output.lower() or "timeout" in output.lower():
                self._log(f"  ⏱️ GIT COMMIT TIMED OUT after {timeout} seconds!", "critical")
                if os.path.exists(git_dir):
                    git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns)
                    self._log(f"     └─ .git folder size: {self._format_size(git_size // 1024)}", "critical")
                self._log(f"     └─ Staged files at time of commit: {len(staged_files)}", "critical")
                if staged_files:
                    self._log(f"     └─ Likely culprits (large staged files):", "warn")
                    file_sizes = []
                    for line in staged_files[:200]:
                        parts = line.strip().split(None, 1)
                        if len(parts) == 2:
                            try: file_sizes.append((parts[1], os.path.getsize(os.path.join(sf, parts[1]))))
                            except Exception: pass
                    file_sizes.sort(key=lambda x: -x[1])
                    for fname, sz in file_sizes[:5]:
                        self._log(f"        • {fname} ({self._format_size(sz // 1024)})", "warn")
                self._log(f"     └─ CAUSE: Too many files, large files, slow disk I/O, or a hanging pre-commit hook.", "warn")
            elif "Cancelled" in output:
                self._log(f"  ⚠️ Commit cancelled by user.", "warn")
            else:
                self._log(f"  ✗ git commit failed ({commit_elapsed:.1f}s): {output.strip()}", "fail")
        return ok, output

    def _cleanup_unnecessary_files(self, sf: str):
        if self.cancelled: return
        self._log("  · Scanning and cleaning large media/archives/AI models/dev junk...", "info")
        media_archive_exts = self.get_media_exts()
        dirs_to_remove = self.get_dirs_to_remove()
        bytes_freed, files_cleaned = 0, 0
        files_to_rm = []
        ok, tracked = self.run_cmd(["git", "ls-files"], sf, dry_run_safe=True)
        if ok:
            for line in tracked.splitlines():
                fp = line.strip()
                if not fp: continue
                _, ext = os.path.splitext(fp.lower())
                if ext in media_archive_exts: files_to_rm.append(fp)
        if files_to_rm:
            self._log(f"  · Removing {len(files_to_rm)} tracked media/archive/AI model files from git index...", "info")
            batch_size = 100
            for i in range(0, len(files_to_rm), batch_size):
                self.run_cmd(["git", "rm", "-r", "--cached", "--ignore-unmatch"] + files_to_rm[i:i+batch_size], sf)
        for root, dirs, files in os.walk(sf, topdown=True):
            if self.cancelled: break
            if ".git" in root:
                dirs[:] = []
                continue
            for d in [d for d in dirs if d in dirs_to_remove]:
                dirs.remove(d)
                dir_path = os.path.join(root, d)
                try:
                    if self.dry_run:
                        sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, ffs in os.walk(dir_path) for f in ffs)
                        self._log(f"  [DRY RUN] Would remove dir: {dir_path} ({self._format_size(sz // 1024)})", "dryrun")
                        bytes_freed += sz
                    else:
                        sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, ffs in os.walk(dir_path) for f in ffs)
                        shutil.rmtree(dir_path)
                        bytes_freed += sz
                    files_cleaned += 1
                except Exception as e:
                    self._log(f"    Warning: Could not remove {dir_path}: {e}", "warn")
            for f in files:
                if self.cancelled: break
                _, ext = os.path.splitext(f.lower())
                if ext in media_archive_exts or f in {".env", ".env.local", "Thumbs.db", ".DS_Store"} or f.endswith((".pyc", ".pyo", ".log", ".tmp", ".bak")):
                    file_path = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(file_path) if not self.dry_run else 0
                        if self.dry_run:
                            self._log(f"  [DRY RUN] Would remove file: {file_path} ({self._format_size(sz // 1024)})", "dryrun")
                        else:
                            os.remove(file_path)
                            self._log(f"  · Deleted local file: {f} ({self._format_size(sz // 1024)}) to prevent push rejection", "info")
                        bytes_freed += sz
                        files_cleaned += 1
                    except Exception as e:
                        self._log(f"    Warning: Could not remove {file_path}: {e}", "warn")
        gitignore_path = os.path.join(sf, ".gitignore")
        default_ignores = ["# Media, Archives, and AI Models"]
        for ext in sorted(list(media_archive_exts)):
            default_ignores.append(f"*{ext}" if not ext.startswith('*') else ext)
        default_ignores.extend(["# Python / Dev", "*.py[cod]", ".env", ".env.local", "*.log", "Thumbs.db", ".DS_Store", "*.tmp", "*.bak"])
        try:
            if self.dry_run:
                self._log(f"  [DRY RUN] Would ensure .gitignore has media/dev exclusions.", "dryrun")
            elif not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f: f.write("\n".join(default_ignores) + "\n")
                self._log("  · Created default .gitignore.", "info")
            else:
                with open(gitignore_path, "r", encoding="utf-8") as f: existing = f.read()
                missing = [ign for ign in default_ignores if ign not in existing]
                if missing:
                    with open(gitignore_path, "a", encoding="utf-8") as f:
                        f.write("\n# Added by Git Repo Creator\n" + "\n".join(missing) + "\n")
                    self._log("  · Updated .gitignore.", "info")
        except Exception as e:
            self._log(f"  ! .gitignore warning: {e}", "warn")
        with self.stats_lock:
            self.stats["files_cleaned"] += files_cleaned
            self.stats["bytes_freed_estimate"] += bytes_freed

    def _add_readme_and_license(self, sf: str, repo_name: str):
        if self.dry_run:
            if self.auto_readme_check.isChecked(): self._log(f"  [DRY RUN] Would create README.md for '{repo_name}'.", "dryrun")
            if self.auto_license_check.isChecked(): self._log(f"  [DRY RUN] Would create LICENSE ({self.license_combo.currentText()}).", "dryrun")
            return
        if self.auto_readme_check.isChecked():
            readme_path = os.path.join(sf, "README.md")
            if not os.path.exists(readme_path):
                try:
                    with open(readme_path, "w", encoding="utf-8") as f: f.write(f"# {repo_name}\n\nProject repository.\n")
                    self._log("  · Created README.md.", "info")
                except Exception as e: self._log(f"  ! README warning: {e}", "warn")
        if self.auto_license_check.isChecked():
            license_path = os.path.join(sf, "LICENSE")
            if not os.path.exists(license_path):
                template = self.LICENSE_TEMPLATES.get(self.license_combo.currentText(), "")
                if template:
                    try:
                        with open(license_path, "w", encoding="utf-8") as f: f.write(template.format(year=datetime.now().year))
                        self._log(f"  · Created LICENSE ({self.license_combo.currentText()}).", "info")
                    except Exception as e: self._log(f"  ! LICENSE warning: {e}", "warn")

    def _gh_repo_create_with_retry(self, gh_name: str, sf: str, visibility: str) -> Tuple[bool, str]:
        ok, err = self.gh_cmd(["gh", "repo", "create", gh_name, "--source=.", f"--{visibility}", "--remote=origin", "--push"], sf)
        if ok: return True, err
        err_lower = (err or "").lower()
        
        # ★ IMPROVED: Highly informative rate limit error message
        if "too many repositories" in err_lower or "rate limit" in err_lower or "try again later" in err_lower:
            self._log(f"  ⚠️ GitHub Rate Limit Hit for '{gh_name}'!", "warn")
            self._log(f"     └─ CAUSE: GitHub API secondary rate limit or repository creation limit reached.", "warn")
            self._log(f"     └─ ACTION: Deferring to retry queue. Will auto-retry with exponential backoff after main batch.", "info")
            self._log(f"     └─ TIP: Verify 'gh auth status' or wait a few minutes if this persists.", "info")
            with self._rate_limited_lock:
                self._rate_limited_repos.append({"sf": sf, "gh_name": gh_name, "visibility": visibility, "attempt": 1})
            return False, "RATE_LIMITED"
        return False, err

    def _retry_rate_limited_repos(self, commit_msg: str):
        if not self._rate_limited_repos: return
        self._log(f"\n{'=' * 60}\n🔄 RETRYING {len(self._rate_limited_repos)} RATE-LIMITED REPOS\n{'=' * 60}\n", "info")
        max_rounds, round_num = 3, 0
        while self._rate_limited_repos and round_num < max_rounds:
            if self.cancelled:
                self._log("⚠️ Cancelled during rate limit retry phase.", "warn")
                return
            round_num += 1
            with self._rate_limited_lock:
                batch = list(self._rate_limited_repos)
                self._rate_limited_repos.clear()
            wait_time = 60 * (2 ** (round_num - 1))
            
            # ★ IMPROVED: Better retry logging
            self._log(f"⏳ Round {round_num}: Waiting {wait_time}s before retrying {len(batch)} rate-limited repos...", "warn")
            self._log(f"     └─ Exponential backoff helps bypass GitHub's secondary API rate limits.", "info")
            
            self._update_global_progress(self.stats["processed"], self.stats["total"], f"Retry round {round_num}", "retry_wait")
            for _ in range(wait_time):
                if self.cancelled:
                    self._log("⚠️ Cancelled during rate limit wait.", "warn")
                    return
                time.sleep(1)
            self._log(f"🔄 Round {round_num}: Retrying {len(batch)} repos...\n", "info")
            for repo_data in batch:
                if self.cancelled: break
                sf, gh_name, visibility, attempt = repo_data["sf"], repo_data["gh_name"], repo_data["visibility"], repo_data["attempt"]
                self._thread_local.current_repo = gh_name
                self._log(f"--- Retrying: {gh_name} (attempt {attempt}) ---", "info")
                ok, err = self.gh_cmd(["gh", "repo", "create", gh_name, "--source=.", f"--{visibility}", "--remote=origin", "--push"], sf)
                if ok:
                    self._log(f"  ✓ Successfully created '{gh_name}' on retry.", "success")
                    self._log_recent_git_history(sf, gh_name)
                    with self.stats_lock:
                        self.stats["created"] += 1
                        self.stats["errors"] = [e for e in self.stats["errors"] if e.get("repo") != gh_name]
                    if not self.dry_run: self._save_progress(gh_name)
                elif "too many repositories" in (err or "").lower() or "rate limit" in (err or "").lower() or "try again later" in (err or "").lower():
                    self._log(f"  ⏳ Still rate limited. Deferring for next round...", "warn")
                    with self._rate_limited_lock:
                        self._rate_limited_repos.append({"sf": sf, "gh_name": gh_name, "visibility": visibility, "attempt": attempt + 1})
                else:
                    self._log(f"  ✗ Failed with different error: {(err or '').strip()[:200]}", "fail")
                self._thread_local.current_repo = None
                
        if self._rate_limited_repos:
            self._log(f"\n⚠️ {len(self._rate_limited_repos)} repos still rate-limited after {max_rounds} retry rounds:", "warn")
            for repo_data in self._rate_limited_repos:
                self._log(f"  • {repo_data['gh_name']} (attempts: {repo_data['attempt']})", "warn")
            self._log("  └─ These repos will need to be retried manually later.", "warn")
            self._log("  └─ TIP: Run 'gh auth status' to ensure you are properly authenticated.", "info")
            with self.stats_lock:
                for repo_data in self._rate_limited_repos:
                    self.stats["failed"] += 1
                    self.stats["errors"].append({"repo": repo_data["gh_name"], "action": "rate_limited"})
        else:
            self._log(f"\n✓ All rate-limited repos successfully created!", "success")

    def _squash_and_push(self, sf: str, gh_name: str, visibility: str) -> bool:
        if self.cancelled: return False
        steps, total = self.STEPS_SQUASH, len(self.STEPS_SQUASH)
        self._log("  · [SQUASH MODE] Consolidating entire history into 1 single commit...", "warn")
        self._report_step(f"{steps[0]}: Scanning files...", 1, total, "running")
        self._cleanup_unnecessary_files(sf)
        self._report_step(f"{steps[0]}: Done", 1, total, "success")
        git_dir = os.path.join(sf, ".git")
        self._report_step(f"{steps[1]}: Deleting old .git...", 2, total, "running")
        if os.path.isdir(git_dir) and not self.dry_run:
            self._log("  · Deleting old .git folder to erase bloated history...", "info")
            if not self._delete_git_directory(git_dir):
                self._log("  ✗ Failed to delete .git folder.", "fail")
                self._report_step(f"{steps[1]}: Failed", 2, total, "error")
                return False
            self._log("  · Deleted old .git folder successfully.", "info")
            self._log("  · Waiting 2s for OS to release directory locks...", "info")
            time.sleep(2)
        self._report_step(f"{steps[1]}: Done", 2, total, "success")
        self._report_step(f"{steps[2]}: git init...", 3, total, "running")
        ok, err = self.run_cmd(["git", "init"], sf, timeout=30)
        if not ok:
            self._log(f"  ✗ git init failed: {err.strip()[:200]}", "fail")
            self._report_step(f"{steps[2]}: Failed", 3, total, "error")
            return False
        self._report_step(f"{steps[2]}: Done", 3, total, "success")
        self._report_step(f"{steps[3]}: git add...", 4, total, "running")
        file_count, total_size = self._count_files_and_size(sf)
        self._log(f"  · Found {file_count:,} files totaling {self._format_size(total_size // 1024)}", "info")
        if file_count > 10000:
            ok = self._batch_git_add(sf, file_count)
        else:
            ok, err = self._run_git_add_with_monitoring(sf, timeout=60, total_files=file_count)
            if not ok:
                self._log(f"  ✗ git add failed: {err.strip()[:500]}", "fail")
                self._report_step(f"{steps[3]}: Failed", 4, total, "error")
                return False
        self._report_step(f"{steps[3]}: Done", 4, total, "success")
        self._report_step(f"{steps[4]}: git commit...", 5, total, "running")
        ok_status, status_out = self.run_cmd(["git", "status", "--porcelain"], sf, timeout=30)
        if ok_status and not status_out.strip():
            self._log("  · Working tree is empty. Creating initial empty commit.", "info")
            ok, err = self._verbose_git_commit(sf, "Squashed commit: initial empty repo", allow_empty=True, timeout=120)
        else:
            ok, err = self._verbose_git_commit(sf, "Squashed commit: cleaned history", timeout=300)
        if not ok:
            self._log(f"  ✗ Commit step failed.", "fail")
            self._report_step(f"{steps[4]}: Failed", 5, total, "error")
            return False
        self._report_step(f"{steps[4]}: Done", 5, total, "success")
        self._report_step(f"{steps[5]}: Force pushing...", 6, total, "running")
        ok, remote_url = self.run_cmd(["git", "remote", "get-url", "origin"], sf, dry_run_safe=True, timeout=10)
        remote_url = remote_url.strip() if ok else None
        if not remote_url:
            self._log("  · No remote found. Creating GitHub repo...", "info")
            ok, err = self._gh_repo_create_with_retry(gh_name, sf, visibility)
            if ok: pass
            elif err == "RATE_LIMITED":
                self._log(f"  ⏳ Repo creation deferred due to rate limiting.", "warn")
                self._report_step(f"{steps[5]}: Deferred", 6, total, "warning")
                return False
            elif "already exists" in (err or "").lower() or "name already exists" in (err or "").lower():
                self._log(f"  · Repo '{gh_name}' already exists. Linking and updating...", "warn")
                return self._update_existing_repo(sf, gh_name, commit_msg, visibility)
            else:
                self._log(f"  ✗ gh repo create failed for '{gh_name}'.", "fail")
                err_lines = (err or "").strip().split('\n')
                self._log(f"  └─ Raw error:\n{(err or '').strip()}", "fail")
                self._report_step(f"{steps[5]}: Failed", 6, total, "error")
                return False
        else:
            if "@github.com" in remote_url and not remote_url.startswith("https://github.com"):
                clean_url = "https://" + remote_url.split("@")[1]
                self.run_cmd(["git", "remote", "set-url", "origin", clean_url], sf, timeout=10)
                remote_url = clean_url
            self._log(f"  · Remote already exists: {remote_url}", "info")
        self._log("  · Force-pushing squashed history...", "info")
        result = self._force_push_with_gh_fallback(sf, gh_name)
        if result: self._report_step(f"{steps[5]}: Done", 6, total, "success")
        else: self._report_step(f"{steps[5]}: Failed", 6, total, "error")
        return result

    def _surgical_history_removal(self, sf: str) -> bool:
        self._log("  · Attempting surgical history removal (git-filter-repo)...", "info")
        ok, _ = self.run_cmd(["git", "filter-repo", "--version"], sf, dry_run_safe=True)
        if not ok:
            self._log("  ✗ 'git-filter-repo' not found.", "fail")
            self._log("  └─ SOLUTION: Install it via 'pip install git-filter-repo'", "warn")
            return False
        extensions = self.get_extensions()
        filter_args = ["git", "filter-repo", "--invert-paths", "--force", "--no-gc"]
        for ext in extensions: filter_args.extend(["--path-glob", ext])
        ok, err = self.run_cmd(filter_args, sf)
        if ok:
            self._log("  ✓ Surgical history removal successful.", "success")
            if not self.dry_run: time.sleep(1.5)
            return True
        else:
            self._log(f"  ✗ Surgical history removal failed: {err.strip()}", "fail")
            return False

    def _force_push_with_gh_fallback(self, sf: str, gh_name: str) -> bool:
        if self.cancelled: return False
        self._log("  · History was rewritten. Preparing to force-push...", "info")
        if not self._check_for_large_files_and_repo_size(sf):
            self._log("  ✗ Aborting push due to critical file size violations.", "fail")
            return False
        self._log("  · Verifying network connectivity to GitHub...", "info")
        ok_net, net_err = self.run_cmd(["git", "ls-remote", "origin", "HEAD"], sf, timeout=15)
        if not ok_net:
            self._log("  ✗ FAILED to reach GitHub. Network/firewall/proxy issue.", "critical")
            self._log(f"  └─ Details: {net_err.strip()[:300]}", "warn")
            self._log("  └─ ACTION: Check Antivirus, Firewall, or Git proxy settings.", "warn")
            return False
        ok_branch, branch_name = self.run_cmd(["git", "branch", "--show-current"], sf, dry_run_safe=True)
        branch_name = branch_name.strip()
        if not branch_name:
            self._log("  ⚠️ Repo is in detached HEAD state. Creating 'main' branch...", "warn")
            self.run_cmd(["git", "checkout", "-b", "main"], sf)
            branch_name = "main"
        self._log(f"  · Target branch for push: '{branch_name}'", "info")
        ok, err = self.run_cmd(["git", "push", "--force", "--set-upstream", "origin", f"{branch_name}:{branch_name}"], sf, timeout=600)
        if ok:
            self._log("  ✓ Force-pushed clean history and set upstream.", "success")
            self._log_recent_git_history(sf, gh_name)
            return True
        self._log(f"  ✗ Initial force-push failed for '{gh_name}'.", "fail")
        err_lines = (err or "").strip().split('\n')
        self._log(f"  └─ Raw error output:\n{(err or '').strip()}", "warn")
        err_lower = (err or "").lower()
        if "timed out" in err_lower or "timeout" in err_lower:
            git_dir = os.path.join(sf, '.git')
            git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns) if os.path.exists(git_dir) else 0
            self._log(f"  ⏱️ PUSH TIMED OUT. .git folder: {self._format_size(git_size // 1024)}.", "critical")
            self._log("     └─ CAUSE: Network or local machine issue, NOT credentials.", "critical")
            return False
        auth_keywords = ["authentication failed", "could not read password", "permission denied", "fatal: could not read", "bad credentials", "invalid username or password", "remote: bad credentials", "remote: invalid username or password"]
        if any(keyword in err_lower for keyword in auth_keywords):
            self._log("  ⚠️ Auth failure. Attempting 'gh auth token' bypass...", "warn")
            ok_tok, token = self.run_cmd(["gh", "auth", "token"], sf, dry_run_safe=True)
            if ok_tok and token.strip():
                token = token.strip()
                ok_url, current_url = self.run_cmd(["git", "remote", "get-url", "origin"], sf, dry_run_safe=True)
                if ok_url and current_url.strip():
                    current_url = current_url.strip()
                    if "@" in current_url and current_url.startswith("https://"):
                        current_url = "https://" + current_url.split("@", 1)[1]
                        self.run_cmd(["git", "remote", "set-url", "origin", current_url], sf)
                    if current_url.startswith("https://"):
                        secure_url = current_url.replace("https://", f"https://{token}@", 1)
                        self.run_cmd(["git", "remote", "set-url", "origin", secure_url], sf)
                        self._log("  · Retrying force-push with gh token...", "info")
                        try:
                            ok, push_err = self.run_cmd(["git", "push", "--force", "--set-upstream", "origin", f"{branch_name}:{branch_name}"], sf, timeout=120)
                        finally:
                            self.run_cmd(["git", "remote", "set-url", "origin", current_url], sf)
                            self._log("  · Secured: Reverted remote URL to remove token.", "info")
                        if ok:
                            self._log("  ✓ Force-pushed using gh token.", "success")
                            self._log_recent_git_history(sf, gh_name)
                            return True
                        else:
                            self._log(f"  ✗ Token push also failed.", "fail")
                    else:
                        self._log(f"  ⚠️ SSH remote. Token injection only works for HTTPS.", "warn")
            else:
                self._log("  ✗ Failed to retrieve gh auth token.", "fail")
        else:
            self._log("  ⚠️ Push failed for non-auth, non-timeout reason.", "warn")
        self._log(f"  ✗ Force push ultimately failed for '{gh_name}'.", "fail")
        return False

    def start_processing(self):
        if self.running: return
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.critical(self, "Error", "Please select a valid parent folder.")
            return
        commit_msg = self.commit_edit.text().strip()
        if not commit_msg:
            QMessageBox.critical(self, "Error", "Please enter a commit message.")
            return
        for tool in ("git", "gh"):
            ok, _ = self.run_cmd([tool, "--version"], cwd=folder, timeout=30, dry_run_safe=True)
            if not ok:
                QMessageBox.critical(self, "Error", f"'{tool}' not found in PATH.\n\nPlease install it and ensure it's in your PATH.")
                return
        self._build_thread_progress_ui()
        self.dry_run = self.dry_run_check.isChecked()
        self.running, self.cancelled, self._exiting = True, False, False
        self.stats = self._empty_stats()
        self.start_time = time.time()
        self._session_start_time = time.time()
        self._repo_success_count, self._repo_fail_count = 0, 0
        self._rate_limited_repos = []
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("0/0  (0%)")
        self._set_progress_color("running")
        self.step_progress_bar.setValue(0)
        self.step_progress_bar.setFormat("Step 0/0  (0%)")
        self._set_step_color("idle")
        self.step_label.setText("💤 Waiting...")
        self.stats_label.setVisible(False)
        self._update_global_progress(0, 0, "", "starting")
        log_file_path = os.path.join(os.getcwd(), "git_creator_run.log")
        self.logger.handlers = []
        fh = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S'))
        self.logger.addHandler(fh)
        self.logger.info("=== Git Repo Creator Run Started ===")
        self._log(f"📄 Log file initialized at: {log_file_path}", "info")
        self._log("Ensuring GitHub CLI credential helper is configured...", "info")
        self.run_cmd(["gh", "auth", "setup-git"], os.path.expanduser("~"), dry_run_safe=True)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("■ Cancel")
        self.force_quit_btn.setVisible(False)
        self.report_btn.setEnabled(False)
        self._worker_thread = threading.Thread(target=self.process_folders, args=(folder, commit_msg), daemon=True)
        self._worker_thread.start()

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
            self._safe_emit(self.signals.finished_signal.emit)
            return
        if not subfolders:
            self._log("No valid subfolders found.", "info")
            self._safe_emit(self.signals.finished_signal.emit)
            return
        resumed = self._load_progress(parent_folder)
        if resumed and self.completed_repos:
            self._log(f"Resuming: {len(self.completed_repos)} repo(s) already completed. Skipping them.\n", "info")
        total = len(subfolders)
        self.stats["total"] = total
        self._update_global_progress(0, total, "", "scanning")
        mode = "🔍 DRY-RUN MODE — no changes will be made" if self.dry_run else "LIVE MODE"
        threads = self.threads_spin.value()
        self._log(f"Found {total} subfolders. Processing with {threads} parallel thread(s). [{mode}]\n", "info")
        visibility = self.visibility_combo.currentText()
        completed_count = 0
        completed_lock = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=threads)

        def process_single(sf: str):
            nonlocal completed_count
            name = os.path.basename(sf)
            self._thread_local.current_repo = name
            if self.cancelled: return None
            if name in self.completed_repos:
                self._log(f"--- (already done, skipping)", "info")
                with self.stats_lock:
                    self.stats["skipped"] += 1
                    self.stats["processed"] += 1
                with completed_lock:
                    completed_count += 1
                    self._safe_emit(self.signals.progress_signal.emit, completed_count, total, name)
                    self._update_global_progress(completed_count, total, name, "skipped")
                return None
            gh_name = name.replace(" ", "-")
            if name != gh_name:
                self._log(f"Note: Using '{gh_name}' as GitHub repo name.", "info")
            self._log(f"--- Starting processing ---", "info")
            self._safe_emit(self.signals.repo_start_signal.emit, name)
            self._update_global_progress(completed_count, total, name, "processing")
            size_before = self._analyze_repo_size(sf)
            has_git = os.path.isdir(os.path.join(sf, ".git"))
            ok = True
            action = "updated" if has_git else "created"
            if not has_git:
                ok = self._create_new_repo(sf, gh_name, commit_msg, visibility)
            else:
                ok = self._update_existing_repo(sf, gh_name, commit_msg, visibility)
            size_after = self._analyze_repo_size(sf)
            with self.stats_lock:
                self.stats["repo_sizes_before"][name] = size_before
                self.stats["repo_sizes_after"][name] = size_after
                self.stats["processed"] += 1
                if ok:
                    self.stats[action] += 1
                    if not self.dry_run: self._save_progress(name)
                else:
                    with self._rate_limited_lock:
                        is_rate_limited = any(r["gh_name"] == gh_name for r in self._rate_limited_repos)
                    if not is_rate_limited:
                        self.stats["failed"] += 1
                        self.stats["errors"].append({"repo": name, "action": action})
            self._safe_emit(self.signals.repo_done_signal.emit, name, ok)
            with completed_lock:
                completed_count += 1
                self._safe_emit(self.signals.progress_signal.emit, completed_count, total, name)
                with self._rate_limited_lock:
                    is_rate_limited = any(r["gh_name"] == gh_name for r in self._rate_limited_repos)
                status = "completed" if ok else ("deferred" if is_rate_limited else "failed")
                self._update_global_progress(completed_count, total, name, status)
            self._safe_emit(self.signals.stats_signal.emit, f"Done: {self.stats['created']} created | {self.stats['updated']} updated | {self.stats['failed']} failed | {len(self._rate_limited_repos)} deferred | {self.stats['skipped']} skipped")
            thread_id = self._get_thread_id()
            if thread_id > 0:
                self._safe_emit(self.signals.thread_progress_signal.emit, thread_id, "", "💤 Idle", 0, "idle")
            self._thread_local.current_repo = None
            return ok

        try:
            futures = {self._executor.submit(process_single, sf): sf for sf in subfolders}
            for future in concurrent.futures.as_completed(futures):
                if self.cancelled:
                    for f in futures: f.cancel()
                    break
                try: future.result()
                except Exception as e:
                    self._log(f"Unexpected error in thread: {e}", "fail")
        finally:
            if self._executor:
                self._executor.shutdown(wait=True)
                self._executor = None
        if self._rate_limited_repos and not self.cancelled:
            self._retry_rate_limited_repos(commit_msg)
        self._safe_emit(self.signals.finished_signal.emit)

    def _create_new_repo(self, sf: str, gh_name: str, commit_msg: str, visibility: str) -> bool:
        if self.cancelled: return False
        steps, total = self.STEPS_NEW, len(self.STEPS_NEW)
        self._report_step(f"{steps[0]}: Cleaning...", 1, total, "running")
        self._log(f"  · No local .git found. Initializing...", "info")
        ok, err = self.run_cmd(["git", "init"], sf)
        if not ok:
            if self._is_disk_full(err): return False
            self._log(f"  ✗ git init failed: {err.strip()}", "fail")
            self._report_step(f"{steps[1]}: Failed", 2, total, "error")
            return False
        self._report_step(f"{steps[1]}: Done", 2, total, "success")
        self._report_step(f"{steps[0]}: Cleaning...", 1, total, "running")
        self._cleanup_unnecessary_files(sf)
        self._add_readme_and_license(sf, gh_name)
        self._report_step(f"{steps[0]}: Done", 1, total, "success")
        self._report_step(f"{steps[2]}: git add...", 3, total, "running")
        file_count, total_size = self._count_files_and_size(sf)
        self._log(f"  · Found {file_count:,} files totaling {self._format_size(total_size // 1024)}", "info")
        if file_count > 10000:
            ok = self._batch_git_add(sf, file_count)
        else:
            ok, err = self._run_git_add_with_monitoring(sf, timeout=60, total_files=file_count)
            if not ok:
                if self._is_disk_full(err): return False
                self._log(f"  ✗ git add failed: {err.strip()}", "fail")
                self._report_step(f"{steps[2]}: Failed", 3, total, "error")
                return False
        self._report_step(f"{steps[2]}: Done", 3, total, "success")
        self._report_step(f"{steps[3]}: git commit...", 4, total, "running")
        ok, output = self._verbose_git_commit(sf, commit_msg)
        if not ok and "nothing to commit" not in output.lower():
            if self._is_disk_full(output): return False
            self._report_step(f"{steps[3]}: Warning", 4, total, "warning")
        else:
            self._report_step(f"{steps[3]}: Done", 4, total, "success")
        self.run_cmd(["git", "gc", "--prune=now"], sf)
        if not self._check_for_large_files_and_repo_size(sf):
            self._log("  ✗ Aborting push due to critical file size violations.", "fail")
            return False
        self._report_step(f"{steps[4]}: Creating remote...", 5, total, "running")
        ok, err = self._gh_repo_create_with_retry(gh_name, sf, visibility)
        if ok:
            self._log(f"  ✓ Created GitHub repo '{gh_name}' ({visibility}) and pushed.", "success")
            self._log_recent_git_history(sf, gh_name)
            self._report_step(f"{steps[5]}: Done", 6, total, "success")
            return True
        elif err == "RATE_LIMITED":
            self._log(f"  ⏳ Repo creation deferred due to rate limiting.", "warn")
            self._report_step(f"{steps[4]}: Deferred", 5, total, "warning")
            return False
        else:
            err_lower = (err or "").lower()
            if "already exists" in err_lower or "name already exists" in err_lower:
                self._log(f"  · Repo '{gh_name}' already exists. Linking and updating...", "warn")
                return self._update_existing_repo(sf, gh_name, commit_msg, visibility)
            else:
                self._log(f"  ✗ gh repo create failed for '{gh_name}'.", "fail")
                err_lines = (err or "").strip().split('\n')
                self._log(f"  └─ Raw error:\n{(err or '').strip()}", "fail")
                self._report_step(f"{steps[4]}: Failed", 5, total, "error")
                return False

    def _update_existing_repo(self, sf: str, gh_name: str, commit_msg: str, visibility: str) -> bool:
        if self.cancelled: return False
        if self.squash_check.isChecked():
            return self._squash_and_push(sf, gh_name, visibility)
        steps, total = self.STEPS_UPDATE, len(self.STEPS_UPDATE)
        self.run_cmd(["git", "merge", "--abort"], sf)
        self.run_cmd(["git", "rebase", "--abort"], sf)
        self._report_step(f"{steps[0]}: Cleaning...", 1, total, "running")
        self._cleanup_unnecessary_files(sf)
        self._report_step(f"{steps[0]}: Done", 1, total, "success")
        surgical_ran = False
        if self.surgical_check.isChecked():
            surgical_ran = self._surgical_history_removal(sf)
        ok, out = self.run_cmd(["git", "remote"], sf, dry_run_safe=True)
        remotes = out.strip().split()
        if "origin" not in remotes:
            self._log(f"  · No 'origin' remote. Linking to GitHub...", "info")
            ok, err = self.gh_cmd(["gh", "repo", "create", gh_name, "--source=.", f"--{visibility}", "--remote=origin"], sf)
            if not ok:
                err_lower = (err or "").lower()
                if "already exists" in err_lower or "already a repository" in err_lower or "name already exists" in err_lower:
                    self._log(f"  · Repo '{gh_name}' already exists. Attempting to link...", "info")
                    ok_url, url_out = self.gh_cmd(["gh", "repo", "view", gh_name, "--json", "url"], sf, dry_run_safe=True)
                    if ok_url:
                        try:
                            repo_url = json.loads(url_out).get("url")
                            if not repo_url.endswith(".git"): repo_url += ".git"
                            self.run_cmd(["git", "remote", "add", "origin", repo_url], sf)
                            self._log(f"  · Added 'origin' → {repo_url}.", "info")
                        except Exception as e:
                            self._log(f"  ✗ Failed to parse URL: {e}", "fail")
                            return False
                    else:
                        self._log(f"  ⚠️ 'gh repo view' failed, attempting manual URL construction...", "warn")
                        ok_user, user_out = self.run_cmd(["gh", "api", "user", "--jq", ".login"], sf, dry_run_safe=True)
                        if ok_user and user_out.strip():
                            username = user_out.strip()
                            manual_url = f"https://github.com/{username}/{gh_name}.git"
                            self.run_cmd(["git", "remote", "add", "origin", manual_url], sf)
                            self._log(f"  · Added 'origin' → {manual_url} (fallback).", "info")
                        else:
                            self._log(f"  ✗ Failed to get repo URL or username. Cannot link.", "fail")
                            return False
                else:
                    self._log(f"  ✗ gh repo create failed: {(err or '').strip()}", "fail")
                    return False
        self._report_step(f"{steps[1]}: git add...", 2, total, "running")
        file_count, total_size = self._count_files_and_size(sf)
        self._log(f"  · Found {file_count:,} files totaling {self._format_size(total_size // 1024)}", "info")
        if file_count > 10000:
            ok = self._batch_git_add(sf, file_count)
            if not ok:
                self._report_step(f"{steps[1]}: Failed", 2, total, "error")
                return False
        else:
            ok, err = self._run_git_add_with_monitoring(sf, timeout=60, total_files=file_count)
            if not ok:
                if self._is_disk_full(err): return False
                self._log(f"  ✗ git add failed: {err.strip()}", "fail")
                self._report_step(f"{steps[1]}: Failed", 2, total, "error")
                return False
        self._report_step(f"{steps[1]}: Done", 2, total, "success")
        self._report_step(f"{steps[2]}: git commit...", 3, total, "running")
        ok, output = self._verbose_git_commit(sf, commit_msg)
        if ok:
            self._report_step(f"{steps[2]}: Done", 3, total, "success")
        elif "nothing to commit" in output.lower() or "no changes added" in output.lower():
            has_commits, _ = self.run_cmd(["git", "rev-parse", "HEAD"], sf, dry_run_safe=True)
            if not has_commits:
                self._log(f"  · Empty repo. Creating initial commit.", "info")
                self._verbose_git_commit(sf, "Initial commit", allow_empty=True)
            self._report_step(f"{steps[2]}: Done (no changes)", 3, total, "success")
        else:
            if self._is_disk_full(output): return False
            self._report_step(f"{steps[2]}: Warning", 3, total, "warning")
        if not surgical_ran:
            self._log("  · Running git garbage collection...", "info")
            self.run_cmd(["git", "gc", "--prune=now"], sf)
        if surgical_ran:
            self._report_step(f"{steps[4]}: Force pushing...", 5, total, "running")
            result = self._force_push_with_gh_fallback(sf, gh_name)
            if result: self._report_step(f"{steps[4]}: Done", 5, total, "success")
            else: self._report_step(f"{steps[4]}: Failed", 5, total, "error")
            return result
        if not self._check_for_large_files_and_repo_size(sf):
            self._log("  ✗ Aborting push due to critical file size violations.", "fail")
            return False
        self._report_step(f"{steps[3]}: Syncing remote...", 4, total, "running")
        self._log(f"  · Syncing with remote...", "info")
        pull_ok, pull_err = self.run_cmd(["git", "pull", "origin", "HEAD", "--no-edit"], sf)
        if not pull_ok:
            low = (pull_err or "").lower()
            if "refusing to merge unrelated histories" in low:
                pull_ok, pull_err = self.run_cmd(["git", "pull", "origin", "HEAD", "--allow-unrelated-histories", "--no-edit"], sf)
                low = (pull_err or "").lower()
            if not pull_ok:
                cf_ok, cf_out = self.run_cmd(["git", "diff", "--name-only", "--diff-filter=U"], sf, dry_run_safe=True)
                conflicted = [l for l in cf_out.splitlines() if l.strip()] if cf_ok else []
                if conflicted:
                    self._log(f"  ! Merge conflicts in {len(conflicted)} file(s). Resolving (prefer local)...", "warn")
                    self.run_cmd(["git", "checkout", "--ours", "--"] + conflicted, sf)
                    self.run_cmd(["git", "add", "-A"], sf)
                    self._verbose_git_commit(sf, "Merge remote (conflicts resolved: prefer local)")
                elif "couldn't find remote ref" in low or "no tracking information" in low:
                    self._log(f"  · Remote empty/missing HEAD, proceeding to push.", "info")
                else:
                    self._log(f"  ! git pull warning: {(pull_err or '').strip()}", "warn")
                    self.run_cmd(["git", "merge", "--abort"], sf)
        self._report_step(f"{steps[3]}: Done", 4, total, "success")
        self._report_step(f"{steps[4]}: Pushing...", 5, total, "running")
        ok, output = self.run_cmd(["git", "push", "-u", "origin", "HEAD"], sf, timeout=600)
        if ok:
            self._log(f"  ✓ Pushed.", "success")
            if output.strip(): self._log(f"  · Git push output: {output.strip()}", "info")
            self._log_recent_git_history(sf, gh_name)
            self._report_step(f"{steps[4]}: Done", 5, total, "success")
            return True
        self._log(f"  ✗ Initial push failed for '{gh_name}'.", "fail")
        if "timed out" in (output or "").lower() or "timeout" in (output or "").lower():
            git_dir = os.path.join(sf, '.git')
            git_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fns in os.walk(git_dir) for f in fns) if os.path.exists(git_dir) else 0
            self._log(f"  ⏱️ PUSH TIMED OUT. .git folder: {self._format_size(git_size // 1024)}.", "critical")
            self._report_step(f"{steps[4]}: Timeout", 5, total, "error")
            return False
        self._log(f"  · Push rejected. Trying --force-with-lease...", "warn")
        ok, output = self.run_cmd(["git", "push", "-u", "--force-with-lease", "origin", "HEAD"], sf, timeout=600)
        if ok:
            self._log(f"  ✓ Force-pushed (with lease).", "success")
            self._log_recent_git_history(sf, gh_name)
            self._report_step(f"{steps[4]}: Done", 5, total, "success")
            return True
        self._log(f"  ✗ Force-push (with lease) also failed for '{gh_name}'.", "fail")
        self._report_step(f"{steps[4]}: Failed", 5, total, "error")
        return False

    def export_report(self):
        folder = self.folder_edit.text().strip()
        if not folder: return
        path, _ = QFileDialog.getSaveFileName(self, "Save Report", os.path.join(folder, "git_creator_report.json"), "JSON Files (*.json);;All Files (*)")
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
            QMessageBox.information(self, "Saved", f"Report saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed: {e}")

    def finish_processing(self):
        elapsed = time.time() - self.start_time if self.start_time else 0
        mins, secs = divmod(int(elapsed), 60)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("COMPLETE  (100%)")
        self._set_progress_color("done")
        self.step_progress_bar.setValue(100)
        self.step_progress_bar.setRange(0, 100)
        self.step_progress_bar.setFormat("DONE")
        self._set_step_color("success")
        self.step_label.setText("✅ All operations complete")
        summary = f"Done in {mins}m{secs}s | Created: {self.stats['created']} | Updated: {self.stats['updated']} | Failed: {self.stats['failed']} | Deferred: {len(self._rate_limited_repos)} | Skipped: {self.stats['skipped']}"
        self._safe_emit(self.signals.stats_signal.emit, summary)
        self.stats_label.setVisible(True)
        self.stats_label.setText(summary)
        self._log("", "info")
        self._log("=" * 60, "info")
        self._log("FINAL REPORT", "info")
        self._log("=" * 60, "info")
        self._log(f"Total repos:        {self.stats['total']}", "info")
        self._log(f"Created (new):      {self.stats['created']}", "success")
        self._log(f"Updated (existing): {self.stats['updated']}", "success")
        self._log(f"Failed:             {self.stats['failed']}", "fail")
        self._log(f"Deferred (rate):    {len(self._rate_limited_repos)}", "warn" if self._rate_limited_repos else "info")
        self._log(f"Skipped (resume):   {self.stats['skipped']}", "info")
        self._log(f"Files cleaned:      {self.stats['files_cleaned']}", "info")
        self._log(f"Disk freed (est):   {self._format_size(self.stats['bytes_freed_estimate'] // 1024)}", "info")
        self._log(f"Elapsed:            {mins}m {secs}s", "info")
        if self._rate_limited_repos:
            self._log(f"\n⚠️ Rate-limited repos that couldn't be retried ({len(self._rate_limited_repos)}):", "warn")
            for repo_data in self._rate_limited_repos:
                self._log(f"  • {repo_data['gh_name']} (attempts: {repo_data['attempt']})", "warn")
            self._log("  └─ These repos need manual retry later.", "warn")
        size_changes = []
        for name, before in self.stats["repo_sizes_before"].items():
            after = self.stats["repo_sizes_after"].get(name, 0)
            diff = before - after
            if diff > 0: size_changes.append((name, before, after, diff))
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
        self._update_global_progress(self.stats["processed"], self.stats["total"], "", "complete" if self.stats["failed"] == 0 and not self._rate_limited_repos else "completed_with_issues")
        for thread_id in self._thread_progress_rows:
            self._safe_emit(self.signals.thread_progress_signal.emit, thread_id, "", "💤 Idle", 0, "idle")
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("■ Cancel")
        self.force_quit_btn.setVisible(False)
        self.report_btn.setEnabled(True)
        self.running, self.cancelled = False, False
        self._cancel_watchdog.stop()
        if not self._exiting:
            self._save_settings()


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = GitRepoCreatorApp()
    window.show()
    sys.exit(app.exec())