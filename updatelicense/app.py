#!/usr/bin/env python3
"""
GitHub License Changer — PyQt6 GUI
Change all repo licenses to Public Domain (CC0) within a date range.

Usage:
    pip install PyQt6 requests
    python license_changer_gui.py
"""

import sys
import time
import base64
import requests

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem,
    QProgressBar, QPlainTextEdit, QGroupBox, QDateEdit, QMessageBox, QHeaderView,
    QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QFont

GITHUB_API = "https://api.github.com"

# ─── Dark Theme (Catppuccin Mocha) ────────────────────────────────────────────

STYLE = """
QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; font-size: 13px; }
QGroupBox {
    border: 1px solid #45475a; border-radius: 8px;
    margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: bold;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #89b4fa; }
QLineEdit, QDateEdit, QPlainTextEdit {
    background-color: #313244; border: 1px solid #45475a;
    border-radius: 6px; padding: 6px; selection-background-color: #89b4fa;
}
QLineEdit:focus, QDateEdit:focus { border-color: #89b4fa; }
QPushButton {
    background-color: #45475a; border: none; border-radius: 6px;
    padding: 8px 18px; font-weight: bold; color: #cdd6f4;
}
QPushButton:hover { background-color: #585b70; }
QPushButton:disabled { background-color: #313244; color: #6c7086; }
QPushButton#fetchBtn  { background-color: #89b4fa; color: #1e1e2e; }
QPushButton#fetchBtn:hover { background-color: #74c7ec; }
QPushButton#startBtn  { background-color: #a6e3a1; color: #1e1e2e; }
QPushButton#startBtn:hover { background-color: #94e2d5; }
QPushButton#cancelBtn { background-color: #f38ba8; color: #1e1e2e; }
QPushButton#cancelBtn:hover { background-color: #eba0ac; }
QTableWidget {
    background-color: #313244; alternate-background-color: #28283d;
    border: 1px solid #45475a; border-radius: 6px; gridline-color: #45475a;
}
QHeaderView::section {
    background-color: #45475a; color: #cdd6f4;
    padding: 6px; border: none; font-weight: bold;
}
QTableCornerButton::section { background-color: #45475a; border: none; }
QProgressBar {
    background-color: #313244; border: 1px solid #45475a;
    border-radius: 6px; text-align: center; min-height: 20px;
}
QProgressBar::chunk { background-color: #89b4fa; border-radius: 5px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QPlainTextEdit#logBox { background-color: #11111b; font-family: Consolas, monospace; }
QSplitter::handle { background-color: #45475a; }
"""


# ─── GitHub API Helpers ───────────────────────────────────────────────────────

def headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }


def fetch_license_text() -> str:
    """Fetch full CC0 text from GitHub's license API; fallback to short version."""
    try:
        r = requests.get(f"{GITHUB_API}/licenses/cc0-1.0", timeout=10)
        if r.status_code == 200:
            return r.json()["body"]
    except Exception:
        pass
    return (
        "CC0 1.0 Universal (Public Domain Dedication)\n\n"
        "To the extent possible under law, the author(s) have dedicated all\n"
        "copyright and related neighboring rights to this software to the\n"
        "public domain worldwide. This software is distributed without any\n"
        "warranty.\n\n"
        "You should have received a copy of the CC0 Public Domain Dedication\n"
        "along with this software. If not, see\n"
        "<http://creativecommons.org/publicdomain/zero/1.0/>.\n"
    )


def get_all_repos(token: str, org: str = None) -> list:
    url = f"{GITHUB_API}/orgs/{org}/repos" if org else f"{GITHUB_API}/user/repos"
    repos, page = [], 1
    while True:
        resp = requests.get(url, headers=headers(token), timeout=30, params={
            "per_page": 100, "page": page, "type": "all", "sort": "updated"
        })
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def get_default_branch(token: str, owner: str, repo: str) -> str:
    resp = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json()["default_branch"]


def get_current_license(token: str, owner: str, repo: str) -> str:
    try:
        resp = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers(token), timeout=30)
        lic = resp.json().get("license")
        return lic.get("spdx_id", "None") if lic else "None"
    except Exception:
        return "?"


def get_file_sha(token: str, owner: str, repo: str, branch: str):
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/LICENSE",
            headers=headers(token), params={"ref": branch}, timeout=30,
        )
        return resp.json()["sha"] if resp.status_code == 200 else None
    except Exception:
        return None


def commit_license(token: str, license_text: str, owner: str, repo: str, branch: str,
                   dry_run: bool) -> tuple:
    """Returns (status, message) where status in {'changed', 'skipped', 'failed'}."""
    sha = get_file_sha(token, owner, repo, branch)

    if sha:  # already has a LICENSE — check if it's already public domain
        resp = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/contents/LICENSE",
            headers=headers(token), params={"ref": branch}, timeout=30,
        )
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()["content"]).decode("utf-8", errors="ignore")
            if "CC0" in content or "public domain" in content.lower():
                return "skipped", "Already public domain"

    current = get_current_license(token, owner, repo)
    if dry_run:
        return "changed", f"Dry-run: would {'update' if sha else 'create'} LICENSE (was: {current})"

    encoded = base64.b64encode(license_text.encode("utf-8")).decode("utf-8")
    data = {"message": "chore: change license to CC0 (Public Domain)",
            "content": encoded, "branch": branch}
    if sha:
        data["sha"] = sha

    resp = requests.put(
        f"{GITHUB_API}/repos/{owner}/{repo}/contents/LICENSE",
        headers=headers(token), json=data, timeout=30,
    )
    if resp.status_code in (200, 201):
        action = "updated" if sha else "created"
        return "changed", f"Successfully {action} LICENSE (was: {current})"
    return "failed", f"API error {resp.status_code}: {resp.text[:120]}"


# ─── Worker: Fetch Repositories ───────────────────────────────────────────────

class FetchWorker(QThread):
    repos_ready = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, token, org, after, before, include_forks, include_archived):
        super().__init__()
        self.token, self.org = token, org
        self.after, self.before = after, before
        self.include_forks = include_forks
        self.include_archived = include_archived

    def run(self):
        try:
            self.repos_ready.emit(self._fetch())
        except Exception as e:
            self.failed.emit(str(e))

    def _fetch(self):
        repos = get_all_repos(self.token, self.org)

        def in_range(r):
            created = r["created_at"][:10]
            if self.after and created < self.after:
                return False
            if self.before and created > self.before:
                return False
            return True

        return [
            r for r in repos
            if (self.include_forks or not r["fork"])
            and (self.include_archived or not r["archived"])
            and in_range(r)
        ]


# ─── Worker: Process Licenses ─────────────────────────────────────────────────

class ProcessWorker(QThread):
    row_status = pyqtSignal(int, str, str)          # row, text, color
    progress = pyqtSignal(int)                       # 0–100
    log = pyqtSignal(str)
    done = pyqtSignal(int, int, int, int)            # changed, skipped, failed, total

    def __init__(self, token, license_text, rows, dry_run):
        super().__init__()
        self.token, self.license_text = token, license_text
        self.rows = rows                              # [(row_index, owner, name)]
        self.dry_run = dry_run
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        changed = skipped = failed = 0
        total = len(self.rows)

        for i, (row, owner, name) in enumerate(self.rows):
            if self._cancel:
                self.log.emit("⏹  Cancelled by user.")
                break

            self.row_status.emit(row, "Processing…", "#f9e2af")
            try:
                branch = get_default_branch(self.token, owner, name)
                status, msg = commit_license(
                    self.token, self.license_text, owner, name, branch, self.dry_run
                )
                if status == "changed":
                    changed += 1
                    self.row_status.emit(row, "Dry-run ✓" if self.dry_run else "Done ✓", "#a6e3a1")
                elif status == "skipped":
                    skipped += 1
                    self.row_status.emit(row, "Already PD", "#94e2d5")
                else:
                    failed += 1
                    self.row_status.emit(row, "Failed ✗", "#f38ba8")
                self.log.emit(f"[{i + 1}/{total}] {owner}/{name} → {msg}")

            except Exception as e:
                failed += 1
                self.row_status.emit(row, "Error ✗", "#f38ba8")
                self.log.emit(f"[{i + 1}/{total}] {owner}/{name} → ❌ {e}")

            self.progress.emit(int((i + 1) / total * 100))
            time.sleep(0.4)  # gentle on rate limits

        self.done.emit(changed, skipped, failed, total)


# ─── Main Window ──────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.fetch_worker = None
        self.process_worker = None
        self.license_text = fetch_license_text()

        self.setWindowTitle("GitHub License Changer → Public Domain (CC0)")
        self.resize(1050, 820)
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)

        # ── Config group ──
        cfg = QGroupBox("Configuration")
        grid = QGridLayout(cfg)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("Token:"), 0, 0)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("ghp_… (needs 'repo' scope)")
        grid.addWidget(self.token_edit, 0, 1, 1, 3)

        grid.addWidget(QLabel("Org (blank = personal):"), 1, 0)
        self.org_edit = QLineEdit()
        self.org_edit.setPlaceholderText("e.g. my-company")
        grid.addWidget(self.org_edit, 1, 1)

        self.forks_cb = QCheckBox("Include forks")
        self.archived_cb = QCheckBox("Include archived")
        grid.addWidget(self.forks_cb, 1, 2)
        grid.addWidget(self.archived_cb, 1, 3)

        # Date range
        self.after_cb = QCheckBox("Created after:")
        self.after_date = QDateEdit(QDate(2023, 1, 1))
        self.after_date.setCalendarPopup(True)
        self.after_date.setDisplayFormat("yyyy-MM-dd")
        self.after_date.setEnabled(False)
        self.after_cb.toggled.connect(self.after_date.setEnabled)

        self.before_cb = QCheckBox("Created before:")
        self.before_date = QDateEdit(QDate.currentDate())
        self.before_date.setCalendarPopup(True)
        self.before_date.setDisplayFormat("yyyy-MM-dd")
        self.before_date.setEnabled(False)
        self.before_cb.toggled.connect(self.before_date.setEnabled)

        grid.addWidget(self.after_cb, 2, 0)
        grid.addWidget(self.after_date, 2, 1)
        grid.addWidget(self.before_cb, 2, 2)
        grid.addWidget(self.before_date, 2, 3)

        root.addWidget(cfg)

        # ── Action bar ──
        bar = QHBoxLayout()
        self.fetch_btn = QPushButton("🔄  Fetch Repos")
        self.fetch_btn.setObjectName("fetchBtn")
        self.fetch_btn.clicked.connect(self.fetch_repos)

        self.select_all_btn = QPushButton("Select All")
        self.select_none_btn = QPushButton("Deselect All")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all_checks(False))

        self.dry_run_cb = QCheckBox("Dry run")
        self.dry_run_cb.setToolTip("Preview without committing")

        self.start_btn = QPushButton("🚀  Change Licenses")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self.start_processing)

        self.cancel_btn = QPushButton("⛔  Cancel")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_processing)

        for w in (self.fetch_btn, self.select_all_btn, self.select_none_btn,
                  self.dry_run_cb, self.start_btn, self.cancel_btn):
            bar.addWidget(w)
        bar.addStretch()
        root.addLayout(bar)

        # ── Repo table + log (splitter) ──
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Owner", "Repo", "Created", "Current License", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        splitter.addWidget(self.table)

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("logBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(2000)
        splitter.addWidget(self.log_box)
        splitter.setSizes([420, 180])

        root.addWidget(splitter, 1)

        # ── Progress ──
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.status_label = QLabel("Ready. Enter your token and fetch repos.")
        root.addWidget(self.progress)
        root.addWidget(self.status_label)

    # ── Fetch repos ───────────────────────────────────────────────────────

    def fetch_repos(self):
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "Missing Token", "Please enter a GitHub token.")
            return

        self._set_busy(True, fetch=True)
        self.status_label.setText("Fetching repositories…")
        self.log_box.appendPlainText("📡 Fetching repositories…")

        after = self.after_date.date().toString("yyyy-MM-dd") if self.after_cb.isChecked() else None
        before = self.before_date.date().toString("yyyy-MM-dd") if self.before_cb.isChecked() else None

        self.fetch_worker = FetchWorker(
            token, self.org_edit.text().strip() or None,
            after, before, self.forks_cb.isChecked(), self.archived_cb.isChecked(),
        )
        self.fetch_worker.repos_ready.connect(self.populate_table)
        self.fetch_worker.failed.connect(self.on_fetch_failed)
        self.fetch_worker.finished.connect(lambda: self._set_busy(False, fetch=True))
        self.fetch_worker.start()

    def on_fetch_failed(self, msg):
        self.log_box.appendPlainText(f"❌ Fetch failed: {msg}")
        self.status_label.setText("Fetch failed.")
        QMessageBox.critical(self, "Fetch Failed", msg)

    def populate_table(self, repos):
        self.table.setRowCount(len(repos))
        for i, r in enumerate(repos):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(r["owner"]["login"]))
            self.table.setItem(i, 2, QTableWidgetItem(r["name"]))
            self.table.setItem(i, 3, QTableWidgetItem(r["created_at"][:10]))
            lic = r["license"]["spdx_id"] if r.get("license") else "None"
            self.table.setItem(i, 4, QTableWidgetItem(lic))
            self.table.setItem(i, 5, QTableWidgetItem(""))

        date_note = ""
        if self.after_cb.isChecked() or self.before_cb.isChecked():
            lo = self.after_date.date().toString("yyyy-MM-dd") if self.after_cb.isChecked() else "…"
            hi = self.before_date.date().toString("yyyy-MM-dd") if self.before_cb.isChecked() else "…"
            date_note = f"  (dates: {lo} → {hi})"
        self.status_label.setText(f"Found {len(repos)} repos{date_note}. Select and run.")
        self.log_box.appendPlainText(f"✅ Found {len(repos)} matching repositories.")

    def _set_all_checks(self, state: bool):
        val = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(val)

    # ── Process licenses ──────────────────────────────────────────────────

    def start_processing(self):
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "Missing Token", "Please enter a GitHub token.")
            return

        rows = [
            (i, self.table.item(i, 1).text(), self.table.item(i, 2).text())
            for i in range(self.table.rowCount())
            if self.table.item(i, 0).checkState() == Qt.CheckState.Checked
        ]
        if not rows:
            QMessageBox.information(self, "Nothing Selected", "Check at least one repository.")
            return

        if not self.dry_run_cb.isChecked():
            confirm = QMessageBox.question(
                self, "Confirm",
                f"This will write a CC0 (public domain) LICENSE to {len(rows)} repo(s).\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self._set_busy(True)
        self.progress.setValue(0)
        self.status_label.setText(
            f"{'DRY RUN — ' if self.dry_run_cb.isChecked() else ''}Processing {len(rows)} repos…"
        )
        self.log_box.appendPlainText(f"\n🚀 Starting ({'dry run' if self.dry_run_cb.isChecked() else 'LIVE'}) on {len(rows)} repos…")

        self.process_worker = ProcessWorker(token, self.license_text, rows, self.dry_run_cb.isChecked())
        self.process_worker.row_status.connect(self.update_row_status)
        self.process_worker.progress.connect(self.progress.setValue)
        self.process_worker.log.connect(self.log_box.appendPlainText)
        self.process_worker.done.connect(self.on_done)
        self.process_worker.finished.connect(lambda: self._set_busy(False))
        self.process_worker.start()

    def update_row_status(self, row, text, color):
        item = QTableWidgetItem(text)
        item.setForeground(Qt.GlobalColor.white)
        from PyQt6.QtGui import QColor
        item.setForeground(QColor(color))
        self.table.setItem(row, 5, item)

    def on_done(self, changed, skipped, failed, total):
        self.status_label.setText(
            f"Finished — ✅ {changed} changed · ⏭ {skipped} skipped · ❌ {failed} failed · {total} total"
        )
        self.log_box.appendPlainText(f"\n📊 Summary: {changed} changed · {skipped} skipped · {failed} failed")
        QMessageBox.information(self, "Complete", self.status_label.text())

    def cancel_processing(self):
        if self.process_worker:
            self.process_worker.cancel()
            self.cancel_btn.setEnabled(False)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool, fetch: bool = False):
        self.fetch_btn.setEnabled(not busy)
        self.start_btn.setEnabled(not busy)
        self.select_all_btn.setEnabled(not busy)
        self.select_none_btn.setEnabled(not busy)
        if fetch:
            self.cancel_btn.setEnabled(False)  # fetch is quick; no cancel
        else:
            self.cancel_btn.setEnabled(busy)
            if not busy:
                self.cancel_btn.setEnabled(False)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()