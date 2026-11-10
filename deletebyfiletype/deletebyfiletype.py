import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QProgressBar,
    QCheckBox, QPlainTextEdit, QGroupBox, QAbstractItemView
)


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def parse_extensions(text: str) -> set[str]:
    """Convert comma/space/semicolon-separated extensions into normalized suffixes."""
    result = set()
    for item in text.replace(";", ",").replace(" ", ",").split(","):
        item = item.strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = "." + item
        result.add(item)
    return result


class ScanWorker(QThread):
    found_file = Signal(str, int)
    progress = Signal(int)
    finished_scan = Signal(int, int)
    scan_error = Signal(str)

    def __init__(self, root: Path, extensions: set[str], include_hidden: bool):
        super().__init__()
        self.root = root
        self.extensions = extensions
        self.include_hidden = include_hidden
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        count = 0
        total_size = 0
        try:
            for current, dirs, files in os.walk(self.root, followlinks=False):
                if self._stop_requested:
                    break

                if not self.include_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files = [f for f in files if not f.startswith(".")]

                for filename in files:
                    if self._stop_requested:
                        break
                    path = Path(current) / filename
                    if path.suffix.lower() not in self.extensions:
                        continue
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                        size = path.stat().st_size
                    except (OSError, PermissionError):
                        continue

                    count += 1
                    total_size += size
                    self.found_file.emit(str(path), size)

                self.progress.emit(count)

        except Exception as exc:
            self.scan_error.emit(str(exc))

        self.finished_scan.emit(count, total_size)


class FileCleaner(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("File Type Cleaner")
        self.resize(1050, 720)
        self.root_folder = None
        self.worker = None
        self.total_size = 0
        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        heading = QLabel("File Type Cleaner")
        heading.setStyleSheet("font-size: 24px; font-weight: 700;")
        subtitle = QLabel("Find and remove files by extension throughout a folder and its subfolders.")
        subtitle.setStyleSheet("color: #888;")
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        folder_group = QGroupBox("1. Choose root folder")
        folder_layout = QHBoxLayout(folder_group)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Select the folder to scan...")
        self.folder_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.choose_folder)
        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(browse_btn)
        layout.addWidget(folder_group)

        filter_group = QGroupBox("2. File extensions")
        filter_layout = QVBoxLayout(filter_group)
        self.extension_edit = QLineEdit(".mp4, .wav, .log")
        self.extension_edit.setPlaceholderText("Example: .mp4, .wav, .log, .tmp")
        filter_layout.addWidget(QLabel("Separate extensions with commas. A leading dot is optional."))
        filter_layout.addWidget(self.extension_edit)
        options_layout = QHBoxLayout()
        self.hidden_check = QCheckBox("Include hidden files and folders")
        self.hidden_check.setChecked(False)
        options_layout.addWidget(self.hidden_check)
        options_layout.addStretch()
        self.scan_btn = QPushButton("Scan folder")
        self.scan_btn.setDefault(True)
        self.scan_btn.clicked.connect(self.start_scan)
        options_layout.addWidget(self.scan_btn)
        filter_layout.addLayout(options_layout)
        layout.addWidget(filter_group)

        results_group = QGroupBox("3. Review matching files")
        results_layout = QVBoxLayout(results_group)
        self.summary_label = QLabel("No scan results yet.")
        results_layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Delete", "File path", "Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        results_layout.addWidget(self.table, 1)

        selection_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select all")
        select_none_btn = QPushButton("Select none")
        select_all_btn.clicked.connect(lambda: self.set_all_checked(True))
        select_none_btn.clicked.connect(lambda: self.set_all_checked(False))
        selection_layout.addWidget(select_all_btn)
        selection_layout.addWidget(select_none_btn)
        selection_layout.addStretch()
        self.delete_btn = QPushButton("Delete checked files")
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet(
            "QPushButton { background: #b42318; color: white; padding: 8px 14px; "
            "font-weight: 600; border-radius: 5px; }"
            "QPushButton:disabled { background: #777; }"
        )
        self.delete_btn.clicked.connect(self.delete_checked)
        selection_layout.addWidget(self.delete_btn)
        results_layout.addLayout(selection_layout)
        layout.addWidget(results_group, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(95)
        self.log.setPlaceholderText("Activity log")
        layout.addWidget(self.log)

        self.statusBar().showMessage("Ready")

    def log_message(self, message: str):
        self.log.appendPlainText(message)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose folder")
        if folder:
            self.root_folder = Path(folder)
            self.folder_edit.setText(folder)
            self.log_message(f"Selected folder: {folder}")

    def start_scan(self):
        if not self.root_folder or not self.root_folder.is_dir():
            QMessageBox.warning(self, "Folder required", "Choose a valid root folder first.")
            return

        extensions = parse_extensions(self.extension_edit.text())
        if not extensions:
            QMessageBox.warning(self, "Extensions required", "Enter at least one file extension.")
            return

        self.table.setRowCount(0)
        self.total_size = 0
        self.summary_label.setText("Scanning...")
        self.scan_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.progress.show()
        self.statusBar().showMessage("Scanning subfolders...")

        self.worker = ScanWorker(
            self.root_folder, extensions, self.hidden_check.isChecked()
        )
        self.worker.found_file.connect(self.add_result)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished_scan.connect(self.scan_finished)
        self.worker.scan_error.connect(self.log_message)
        self.worker.start()
        self.log_message(f"Started scan for: {', '.join(sorted(extensions))}")

    def add_result(self, path: str, size: int):
        row = self.table.rowCount()
        self.table.insertRow(row)

        checkbox = QTableWidgetItem()
        checkbox.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        checkbox.setCheckState(Qt.Checked)
        checkbox.setData(Qt.UserRole, path)
        self.table.setItem(row, 0, checkbox)

        path_item = QTableWidgetItem(path)
        path_item.setToolTip(path)
        self.table.setItem(row, 1, path_item)

        size_item = QTableWidgetItem(format_size(size))
        size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        size_item.setData(Qt.UserRole, size)
        self.table.setItem(row, 2, size_item)
        self.total_size += size

    def update_progress(self, count: int):
        self.statusBar().showMessage(f"Scanning... {count:,} matching files found")

    def scan_finished(self, count: int, total_size: int):
        self.progress.hide()
        self.scan_btn.setEnabled(True)
        self.total_size = total_size
        self.summary_label.setText(
            f"Found {count:,} matching files | Total size: {format_size(total_size)}"
        )
        self.delete_btn.setEnabled(count > 0)
        self.statusBar().showMessage(f"Scan complete: {count:,} matching files")
        self.log_message(f"Scan complete: {count:,} files ({format_size(total_size)})")

    def set_all_checked(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def delete_checked(self):
        selected = []
        for row in range(self.table.rowCount()):
            check_item = self.table.item(row, 0)
            if check_item.checkState() == Qt.Checked:
                selected.append((check_item.data(Qt.UserRole), row))

        if not selected:
            QMessageBox.information(self, "Nothing selected", "Check the files you want to delete.")
            return

        total_bytes = sum(
            self.table.item(row, 2).data(Qt.UserRole) or 0
            for _, row in selected
        )
        answer = QMessageBox.warning(
            self,
            "Confirm permanent deletion",
            f"Delete {len(selected):,} selected files ({format_size(total_bytes)})?\n\n"
            "This action permanently deletes the files. It does not move them to the Recycle Bin.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return

        deleted = 0
        failed = 0
        rows_to_remove = []

        for path, row in selected:
            try:
                target = Path(path)
                # Re-check the target to avoid following a replaced symlink.
                if target.is_symlink() or not target.is_file():
                    raise OSError("File no longer exists or is not a regular file")
                target.unlink()
                deleted += 1
                rows_to_remove.append(row)
            except (OSError, PermissionError) as exc:
                failed += 1
                self.log_message(f"Could not delete {path}: {exc}")

        for row in reversed(rows_to_remove):
            self.table.removeRow(row)

        remaining_size = sum(
            self.table.item(row, 2).data(Qt.UserRole) or 0
            for row in range(self.table.rowCount())
        )
        remaining_count = self.table.rowCount()
        self.summary_label.setText(
            f"{remaining_count:,} files remaining | Total size: {format_size(remaining_size)}"
        )
        self.delete_btn.setEnabled(remaining_count > 0)
        self.log_message(f"Deletion finished: {deleted:,} deleted, {failed:,} failed.")
        QMessageBox.information(
            self, "Deletion complete",
            f"Deleted: {deleted:,}\nFailed: {failed:,}"
        )


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = FileCleaner()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
