
import sys
import shutil
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QProgressBar, QTextEdit, QCheckBox,
    QMessageBox, QGroupBox, QSpinBox
)


class CopyWorker(QThread):
    progress = Signal(int, int)
    log = Signal(str)
    finished_signal = Signal(int, int)

    def __init__(
        self, source, destination, extensions,
        preserve_structure, overwrite
    ):
        super().__init__()

        self.source = Path(source)
        self.destination = Path(destination)
        self.extensions = extensions
        self.preserve_structure = preserve_structure
        self.overwrite = overwrite
        self.cancelled = False

    def run(self):
        files = [
            f for f in self.source.rglob("*")
            if f.is_file()
            and f.suffix.lower() in self.extensions
            and not f.is_relative_to(self.destination)
        ]

        total = len(files)
        copied = 0
        skipped = 0

        self.log.emit(f"Found {total} matching files.")

        for index, file in enumerate(files, 1):
            if self.cancelled:
                break

            if self.preserve_structure:
                relative = file.relative_to(self.source)
                target = self.destination / relative
            else:
                target = self.destination / file.name

            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists() and not self.overwrite:
                if not self.preserve_structure:
                    # Avoid collisions when flattening folders.
                    stem = target.stem
                    suffix = target.suffix
                    counter = 1

                    while target.exists():
                        target = target.with_name(
                            f"{stem}_{counter}{suffix}"
                        )
                        counter += 1
                else:
                    skipped += 1
                    self.log.emit(f"Skipped: {file.name}")
                    self.progress.emit(index, total)
                    continue

            try:
                shutil.copy2(file, target)
                copied += 1
                self.log.emit(f"Copied: {file} -> {target}")
            except Exception as error:
                skipped += 1
                self.log.emit(f"Error: {file}: {error}")

            self.progress.emit(index, total)

        self.finished_signal.emit(copied, skipped)

    def cancel(self):
        self.cancelled = True


class FileCopier(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("File Type Copier")
        self.resize(750, 650)

        self.worker = None

        self.build_ui()

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)

        title = QLabel("File Type Copier")
        title.setStyleSheet(
            "font-size: 25px; font-weight: bold;"
        )
        layout.addWidget(title)

        # Source folder
        source_group = QGroupBox("Source Folder")
        source_layout = QHBoxLayout(source_group)

        self.source_input = QLineEdit()
        self.source_input.setPlaceholderText(
            "Select folder to search..."
        )

        source_button = QPushButton("Browse")
        source_button.clicked.connect(self.select_source)

        source_layout.addWidget(self.source_input)
        source_layout.addWidget(source_button)

        layout.addWidget(source_group)

        # Destination folder
        destination_group = QGroupBox("Destination Folder")
        destination_layout = QHBoxLayout(destination_group)

        self.destination_input = QLineEdit()
        self.destination_input.setPlaceholderText(
            "Select destination folder..."
        )

        destination_button = QPushButton("Browse")
        destination_button.clicked.connect(
            self.select_destination
        )

        destination_layout.addWidget(self.destination_input)
        destination_layout.addWidget(destination_button)

        layout.addWidget(destination_group)

        # Extensions
        extension_group = QGroupBox("File Extensions")
        extension_layout = QVBoxLayout(extension_group)

        self.extensions_input = QLineEdit(".py, .cpp, .h")
        self.extensions_input.setPlaceholderText(
            "Example: .py, .png, .mp4, .wav"
        )

        extension_layout.addWidget(
            QLabel("Extensions (comma separated):")
        )
        extension_layout.addWidget(self.extensions_input)

        layout.addWidget(extension_group)

        # Options
        options_group = QGroupBox("Copy Options")
        options_layout = QVBoxLayout(options_group)

        self.structure_checkbox = QCheckBox(
            "Preserve original folder structure"
        )

        self.overwrite_checkbox = QCheckBox(
            "Overwrite existing files"
        )

        options_layout.addWidget(self.structure_checkbox)
        options_layout.addWidget(self.overwrite_checkbox)

        layout.addWidget(options_group)

        # Progress
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)

        layout.addWidget(self.progress_bar)

        # Buttons
        button_layout = QHBoxLayout()

        self.copy_button = QPushButton("Start Copying")
        self.copy_button.clicked.connect(self.start_copy)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_copy)
        self.cancel_button.setEnabled(False)

        button_layout.addWidget(self.copy_button)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        # Activity log
        layout.addWidget(QLabel("Activity Log"))

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        layout.addWidget(self.log_output)

    def select_source(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Source Folder"
        )
        if folder:
            self.source_input.setText(folder)

    def select_destination(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder"
        )
        if folder:
            self.destination_input.setText(folder)

    def start_copy(self):
        source = Path(self.source_input.text()).resolve()
        destination = Path(
            self.destination_input.text()
        ).resolve()

        if not source.is_dir():
            QMessageBox.warning(
                self, "Error", "Select a valid source folder."
            )
            return

        if source == destination or source in destination.parents:
            QMessageBox.warning(
                self, "Error",
                "Destination cannot be the source folder or inside it."
            )
            return

        extensions = {
            ext.strip().lower()
            for ext in self.extensions_input.text().split(",")
            if ext.strip()
        }

        extensions = {
            ext if ext.startswith(".") else "." + ext
            for ext in extensions
        }

        if not extensions:
            QMessageBox.warning(
                self, "Error", "Enter at least one extension."
            )
            return

        self.progress_bar.setValue(0)
        self.log_output.clear()

        self.worker = CopyWorker(
            source,
            destination,
            extensions,
            self.structure_checkbox.isChecked(),
            self.overwrite_checkbox.isChecked()
        )

        self.worker.progress.connect(self.update_progress)
        self.worker.log.connect(self.add_log)
        self.worker.finished_signal.connect(self.copy_finished)
        self.worker.start()

        self.copy_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status_label.setText("Copying files...")

    def update_progress(self, current, total):
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)
        self.status_label.setText(
            f"Processing {current} of {total} files"
        )

    def add_log(self, message):
        self.log_output.append(message)

    def cancel_copy(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling...")

    def copy_finished(self, copied, skipped):
        self.copy_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

        cancelled = self.worker.cancelled

        self.status_label.setText(
            f"Finished: {copied} copied, {skipped} skipped"
            if not cancelled else
            f"Cancelled: {copied} copied, {skipped} skipped"
        )

        self.log_output.append("\nOperation complete.")
        self.log_output.append(f"Copied: {copied}")
        self.log_output.append(f"Skipped/errors: {skipped}")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()

        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = FileCopier()
    window.show()

    sys.exit(app.exec())
