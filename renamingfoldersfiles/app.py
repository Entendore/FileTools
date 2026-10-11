"""
WAV Renamer & Organizer 3.0 — PySide6

New in v3:
  * Model/View rewrite (QAbstractTableModel + QTableView):
      - per-file INCLUDE checkbox — greyed rows are skipped by Apply
      - live filter/search box for the file list
      - checkbox & selection state survive refreshes
  * WAV headers + INFO chunks are read in a BACKGROUND thread, so adding
    thousands of files no longer freezes the UI ("reading…" shown meanwhile)
  * Duplicate detection (size + quick content hash) flags files listed twice
  * New template tokens:  {sr} {bits} {ch} {dur}  and  {title} {artist}
    {comment} (pulled from the file's LIST/INFO chunk, if present)
  * Naming PRESETS — save / reuse / delete complete rule sets
  * Multi-level UNDO — dialog lists the last 25 batches, undo any of them
  * Copies are VERIFIED (byte size) after writing
  * Export the plan as CSV
  * Menu bar + shortcuts: Ctrl+O, Ctrl+Shift+O, Del, Ctrl+A, Ctrl+Z, Ctrl+H,
    Ctrl+E, F5, Ctrl+Enter (apply), Esc (cancel batch), Ctrl+T (theme)
  * Dark / light theme toggle
  * In-place renames now stay in the list (path updated) instead of vanishing

Requires:  pip install PySide6
Run:       python wav_organizer.py
"""

import csv
import hashlib
import itertools
import json
import re
import shutil
import sys
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSettings, Qt,
                            QThread, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QKeySequence,
                           QPalette, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QDialog, QFileDialog, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QRadioButton,
                               QSpinBox, QStyle, QTableView, QVBoxLayout, QWidget)

try:  # optional: enables the play feature
    from PySide6.QtMultimedia import QSoundEffect
    HAS_AUDIO = True
except Exception:
    HAS_AUDIO = False

AUDIO_EXTS = {".wav", ".wave"}
ILLEGAL = re.compile(r'[<>:"/\\|?*]')
HISTORY_FILE = Path.home() / ".wav_organizer_history.json"
HISTORY_MAX = 25
COLUMNS = ["✓", "#", "Original file", "→ New name", "Format", "Length", "Status"]
COL_NUM, COL_ORIG, COL_NEW, COL_FMT, COL_LEN, COL_STATUS = range(1, 7)
INFO_TAGS = {b"INAM": "title", b"IART": "artist", b"ICMT": "comment",
             b"ICRD": "date", b"IGNR": "genre", b"ISFT": "software"}


# ----------------------------------------------------------------- helpers
def read_wav_info(path: Path) -> dict | None:
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate() or 0
            return {"rate": rate, "bits": w.getsampwidth() * 8,
                    "ch": w.getnchannels(),
                    "dur": w.getnframes() / rate if rate else 0.0}
    except Exception:
        return None


def read_wav_tags(path: Path) -> dict:
    """Extract LIST/INFO chunk tags (title, artist, comment, …) if present."""
    tags = {}
    try:
        with path.open("rb") as f:
            if f.read(12)[8:12] != b"WAVE":
                return tags
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid, size = hdr[:4], int.from_bytes(hdr[4:8], "little")
                if cid == b"LIST":
                    data = f.read(size)
                    if len(data) > 8 and data[4:8] == b"INFO":
                        pos = 8
                        while pos + 8 <= len(data):
                            sid, ss = data[pos:pos + 4], int.from_bytes(
                                data[pos + 4:pos + 8], "little")
                            val = data[pos + 8:pos + 8 + ss].split(b"\x00")[0]
                            key = INFO_TAGS.get(sid)
                            if key:
                                try:
                                    tags[key] = val.decode("utf-8", "replace").strip()
                                except Exception:
                                    pass
                            pos += 8 + ss + (ss & 1)          # word alignment
                else:
                    f.seek(size + (size & 1), 1)
    except Exception:
        pass
    return tags


def quick_hash(path: Path) -> str:
    """Cheap fingerprint: SHA1 of first + last 64 KB."""
    h = hashlib.sha1()
    with path.open("rb") as f:
        h.update(f.read(65536))
        if path.stat().st_size > 65536:
            f.seek(-65536, 2)
            h.update(f.read(65536))
    return h.hexdigest()


def fmt_rate(rate) -> str:
    return f"{rate / 1000:g} kHz" if rate else "—"


def fmt_dur(sec) -> str:
    sec = int(round(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}" if sec else "—"


def fmt_size(n) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _to_bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).lower() in ("true", "1", "yes")


def _to_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def status_color(t: str):
    if not t:
        return None
    if t.startswith("error") or t.startswith("⚠"):
        return QColor("#d9534f")
    if t.startswith("possible duplicate"):
        return QColor("#e67e22")
    if t in ("copied", "moved", "renamed"):
        return QColor("#2e8b57")
    if t in ("unchanged", "cancelled"):
        return QColor("#888888")
    return None


@dataclass
class FileItem:
    path: Path
    uid: int
    size: int = 0
    info: dict | None = None
    tags: dict = field(default_factory=dict)
    include: bool = True
    pending: bool = True
    status: str = ""
    dup_of: int | None = None      # row of a possible duplicate
    num: int | None = None         # batch number (included rows only)
    new_stem: str = ""
    target: Path | None = None


# ------------------------------------------------------------ drag & drop view
class DropTableView(QTableView):
    files_dropped = Signal(list)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()


# ------------------------------------------------------------ background threads
class MetaWorker(QThread):
    """Reads WAV headers / INFO tags / duplicate fingerprints off the UI thread."""
    item_meta = Signal(int, object, object, object)   # uid, info, tags, dup-key
    meta_done = Signal()

    def __init__(self, batch, parent=None):
        super().__init__(parent)
        self.batch = batch                # [(uid, Path), …]
        self.stop_flag = False

    def run(self):
        for uid, path in self.batch:
            if self.stop_flag:
                break
            key = None
            try:
                key = (path.stat().st_size, quick_hash(path))
            except OSError:
                pass
            self.item_meta.emit(uid, read_wav_info(path), read_wav_tags(path), key)
        self.meta_done.emit()


class Worker(QThread):
    progress = Signal(int)
    row_status = Signal(int, str)
    current = Signal(int, str)            # 1-based position, filename
    batch_done = Signal(int, list, list)  # done, errors, log

    def __init__(self, jobs, overwrite, parent=None):
        super().__init__(parent)
        self.jobs = jobs                  # [(row, src, dst, action), …]
        self.overwrite = overwrite
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        done, errors, log = 0, [], []
        for pos, (row, src, dst, action) in enumerate(self.jobs, 1):
            self.current.emit(pos, src.name)
            if self.cancelled:
                self.row_status.emit(row, "cancelled")
                continue
            try:
                if src.resolve() == dst.resolve():
                    self.row_status.emit(row, "unchanged")
                    done += 1
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        if self.overwrite:
                            dst.unlink()
                        else:                       # auto-rename duplicates
                            k = 2
                            while True:
                                alt = dst.with_name(f"{dst.stem} ({k}){dst.suffix}")
                                if not alt.exists():
                                    dst = alt
                                    break
                                k += 1
                    if action == "copy":
                        shutil.copy2(src, dst)
                        if src.stat().st_size != dst.stat().st_size:
                            raise OSError("copy verification failed (size mismatch)")
                    else:
                        shutil.move(str(src), str(dst))
                    log.append({"action": action, "src": str(src),
                                "dst": str(dst), "row": row})
                    self.row_status.emit(row, "copied" if action == "copy" else "moved")
                    done += 1
            except OSError as e:
                errors.append(f"{src.name}: {e}")
                self.row_status.emit(row, f"error: {e}")
            self.progress.emit(done)
        self.batch_done.emit(done, errors, log)


# ---------------------------------------------------------------- table model
class TableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[FileItem] = []

    # ---- Qt plumbing -------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, s, o, role=Qt.ItemDataRole.DisplayRole):
        if o == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[s]
        return None

    def flags(self, idx):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if idx.column() == 0:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid():
            return None
        it, c = self.items[idx.row()], idx.column()
        if role == Qt.ItemDataRole.CheckStateRole and c == 0:
            return (Qt.CheckState.Checked if it.include
                    else Qt.CheckState.Unchecked)
        if role == Qt.ItemDataRole.DisplayRole:
            if c == COL_NUM:
                return str(it.num) if it.num is not None else ""
            if c == COL_ORIG:
                return it.path.name
            if c == COL_NEW:
                return f"{it.new_stem}{it.path.suffix}" if it.new_stem else ""
            if c == COL_FMT:
                if it.info:
                    i = it.info
                    return f"{fmt_rate(i['rate'])} · {i['bits']}-bit · {i['ch']}ch"
                return "reading…" if it.pending else "—"
            if c == COL_LEN:
                return fmt_dur((it.info or {}).get("dur", 0))
            if c == COL_STATUS:
                return it.status
        if role == Qt.ItemDataRole.ToolTipRole:
            if c == COL_ORIG:
                return str(it.path)
            if c == COL_NEW:
                return str(it.target) if it.target else ""
            if c == COL_STATUS and it.dup_of is not None:
                return f"Same size & content as row {it.dup_of + 1}"
        if role == Qt.ItemDataRole.ForegroundRole:
            if not it.include:
                return QColor("#909090")
            if c == COL_STATUS:
                col = status_color(it.status)
                if col:
                    return col
        if role == Qt.ItemDataRole.TextAlignmentRole and c == COL_NUM:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def setData(self, idx, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and idx.column() == 0:
            it = self.items[idx.row()]
            try:
                it.include = Qt.CheckState(value) == Qt.CheckState.Checked
            except (TypeError, ValueError):
                it.include = bool(value)
            self.dataChanged.emit(self.index(idx.row(), 0),
                                  self.index(idx.row(), COL_STATUS))
            return True
        return False

    # ---- mutators ------------------------------------------------------
    def add_items(self, new_items):
        first = len(self.items)
        self.beginInsertRows(QModelIndex(), first, first + len(new_items) - 1)
        self.items.extend(new_items)
        self.endInsertRows()

    def remove_rows(self, rows):
        for r in sorted(set(rows), reverse=True):
            if 0 <= r < len(self.items):
                self.beginRemoveRows(QModelIndex(), r, r)
                del self.items[r]
                self.endRemoveRows()

    def clear(self):
        self.beginResetModel()
        self.items.clear()
        self.endResetModel()

    def move_row(self, r, delta) -> bool:
        nr = r + delta
        if not (0 <= nr < len(self.items)):
            return False
        dest = nr + 1 if delta > 0 else nr
        self.beginMoveRows(QModelIndex(), r, r, QModelIndex(), dest)
        self.items[r], self.items[nr] = self.items[nr], self.items[r]
        self.endMoveRows()
        return True

    def resort(self, key):
        self.beginResetModel()
        self.items.sort(key=key)
        self.endResetModel()

    def set_meta(self, row, info, tags):
        it = self.items[row]
        it.info, it.tags, it.pending = info, tags, False
        self.dataChanged.emit(self.index(row, COL_FMT),
                              self.index(row, COL_LEN))

    def set_status(self, row, text):
        if 0 <= row < len(self.items):
            self.items[row].status = text
            self.dataChanged.emit(self.index(row, COL_STATUS),
                                  self.index(row, COL_STATUS))

    def apply_plan(self, plan):
        """plan: list aligned with items → (num|None, stem, target|None)."""
        for r, (num, stem, target) in enumerate(plan):
            it = self.items[r]
            it.num, it.new_stem, it.target = num, stem, target
        if self.items:
            self.dataChanged.emit(self.index(0, COL_NUM),
                                  self.index(len(self.items) - 1, COL_NEW))


class NameFilterProxy(QSortFilterProxyModel := __import__(
        "PySide6.QtCore", fromlist=["QSortFilterProxyModel"]
).QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""

    def set_filter(self, text):
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self.text:
            return True
        it = self.sourceModel().items[row]
        return (self.text in it.path.name.lower()
                or self.text in it.new_stem.lower())


# -------------------------------------------------------------- undo dialog
class UndoDialog(QDialog):
    def __init__(self, hist, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Undo history")
        self.resize(600, 340)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Batches, newest first. Double-click (or select and "
                           "press Undo) to reverse one."))
        self.listw = QListWidget()
        for b in hist:
            entries = b.get("entries", [])
            act = entries[0]["action"] if entries else "?"
            dest = Path(entries[0]["dst"]).parent if entries else "?"
            self.listw.addItem(
                f"{b.get('when', '?')}   ·   {len(entries)} {act} op(s)   ·   {dest}")
        v.addWidget(self.listw)
        row = QHBoxLayout()
        self.btn_undo = QPushButton("Undo selected")
        btn_close = QPushButton("Close")
        row.addStretch()
        row.addWidget(self.btn_undo)
        row.addWidget(btn_close)
        v.addLayout(row)
        self.listw.itemDoubleClicked.connect(lambda _: self.accept())
        self.btn_undo.clicked.connect(self.accept)
        btn_close.clicked.connect(self.reject)


# --------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("WavTools", "WavOrganizer")
        self.model = TableModel(self)
        self.proxy = NameFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self._uid = itertools.count()
        self._seen_keys: dict = {}
        self._meta_queue: list = []
        self._meta_worker: MetaWorker | None = None
        self.worker: Worker | None = None
        self.running = False
        self._player = None
        self._preset_lock = False
        self.presets: dict = {}
        self._build_ui()
        self._load_settings()
        self._populate_presets()
        self.refresh_plan()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.setWindowTitle("WAV Renamer & Organizer")
        self.resize(1020, 760)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)

        self._menus()

        # 1 · file selection --------------------------------------------
        self.g1 = QGroupBox("1 · Select WAV files   (drag & drop onto the table works too)")
        row = QHBoxLayout(self.g1)
        self.btn_add_files = QPushButton("Add files…")
        self.btn_add_folder = QPushButton("Add folder…")
        self.btn_up = QPushButton("↑")
        self.btn_down = QPushButton("↓")
        self.btn_up.setToolTip("Move selected files up (changes {n} numbering)")
        self.btn_down.setToolTip("Move selected files down")
        self.btn_remove = QPushButton("Remove selected")
        self.btn_clear = QPushButton("Clear all")
        self.chk_recursive = QCheckBox("Include subfolders")
        self.chk_recursive.setChecked(True)
        for w in (self.btn_add_files, self.btn_add_folder, self.btn_up,
                  self.btn_down, self.btn_remove, self.btn_clear, self.chk_recursive):
            row.addWidget(w)
        row.addStretch()
        root.addWidget(self.g1)

        # 2 · rename rules -----------------------------------------------
        self.g2 = QGroupBox("2 · Rename rules")
        grid = QGridLayout(self.g2)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("Name template:"), 0, 0)
        self.edit_template = QLineEdit("{name}")
        grid.addWidget(self.edit_template, 0, 1, 1, 4)
        hint = QLabel("Tokens:  {name} {n} {date} {time} {parent}  ·  audio: "
                      "{rate} {sr} {bits} {ch} {dur}  ·  INFO tags: {title} {artist} {comment}")
        hint.setStyleSheet("color: gray;")
        grid.addWidget(hint, 1, 1, 1, 4)

        grid.addWidget(QLabel("Find:"), 2, 0)
        self.edit_find = QLineEdit()
        self.edit_find.setPlaceholderText("text to remove")
        grid.addWidget(self.edit_find, 2, 1)
        grid.addWidget(QLabel("Replace with:"), 2, 2)
        self.edit_replace = QLineEdit()
        grid.addWidget(self.edit_replace, 2, 3)
        self.chk_regex = QCheckBox("regex")
        grid.addWidget(self.chk_regex, 2, 4)

        grid.addWidget(QLabel("Case:"), 3, 0)
        self.combo_case = QComboBox()
        self.combo_case.addItems(["Keep case", "lowercase", "UPPERCASE", "Title Case"])
        grid.addWidget(self.combo_case, 3, 1)
        num = QHBoxLayout()
        num.addWidget(QLabel("Numbering: start"))
        self.spin_start = QSpinBox()
        self.spin_start.setRange(0, 999_999)
        self.spin_start.setValue(1)
        num.addWidget(self.spin_start)
        num.addWidget(QLabel("digits"))
        self.spin_digits = QSpinBox()
        self.spin_digits.setRange(1, 6)
        self.spin_digits.setValue(3)
        num.addWidget(self.spin_digits)
        num.addStretch()
        grid.addLayout(num, 3, 2, 1, 3)

        grid.addWidget(QLabel("Order:"), 4, 0)
        self.combo_sort = QComboBox()
        self.combo_sort.addItems(["Name", "Date modified", "Sample rate", "Length"])
        self.combo_sort.setToolTip("Re-sorts the list — the order decides {n} numbering")
        grid.addWidget(self.combo_sort, 4, 1)
        
        # --- ADD THIS NEW ROW FOR PRESETS ---
        grid.addWidget(QLabel("Presets:"), 5, 0)
        self.combo_presets = QComboBox()
        self.btn_preset_save = QPushButton("Save")
        self.btn_preset_delete = QPushButton("Delete")
        grid.addWidget(self.combo_presets, 5, 1, 1, 2)
        grid.addWidget(self.btn_preset_save, 5, 3)
        grid.addWidget(self.btn_preset_delete, 5, 4)
        # ------------------------------------

        root.addWidget(self.g2)

        # 3 · destination --------------------------------------------------
        self.g3 = QGroupBox("3 · Destination")
        grid3 = QGridLayout(self.g3)
        self.rb_move = QRadioButton("Move")
        self.rb_copy = QRadioButton("Copy")
        self.rb_move.setChecked(True)
        self.chk_inplace = QCheckBox("Rename in place (leave files in their folders)")
        self.edit_dest = QLineEdit()
        self.edit_dest.setPlaceholderText("Choose destination folder…")
        self.btn_browse = QPushButton("Browse…")
        grid3.addWidget(self.rb_move, 0, 0)
        grid3.addWidget(self.rb_copy, 0, 1)
        grid3.addWidget(self.edit_dest, 0, 2)
        grid3.addWidget(self.btn_browse, 0, 3)
        grid3.addWidget(self.chk_inplace, 1, 0, 1, 2)
        grid3.addWidget(QLabel("Auto subfolders:"), 2, 0)
        self.combo_sub = QComboBox()
        self.combo_sub.addItems(["None",
                                 "By sample rate  (48000 Hz)",
                                 "By month modified  (2025-01)",
                                 "By day modified  (2025-01-15)"])
        grid3.addWidget(self.combo_sub, 2, 1, 1, 2)
        self.chk_overwrite = QCheckBox("Overwrite existing files")
        grid3.addWidget(self.chk_overwrite, 2, 3)
        root.addWidget(self.g3)

        # 4 · preview table --------------------------------------------------
        g4 = QGroupBox("4 · Preview   (double-click = play · right-click = menu · "
                       "untick rows to skip them)")
        v4 = QVBoxLayout(g4)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Filter:"))
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("show only files matching…")
        self.edit_filter.setClearButtonEnabled(True)
        frow.addWidget(self.edit_filter, stretch=1)
        v4.addLayout(frow)
        self.table = DropTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 28)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_ORIG, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_NEW, QHeaderView.ResizeMode.Stretch)
        for c in (0, COL_NUM, COL_FMT, COL_LEN, COL_STATUS):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        v4.addWidget(self.table)
        root.addWidget(g4, stretch=1)
        self.table.addAction(self.act_remove)

        # bottom bar ---------------------------------------------------------
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.hide()
        self.btn_undo = QPushButton("↩ Undo last batch")
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setMinimumHeight(34)
        f = self.btn_apply.font()
        f.setBold(True)
        self.btn_apply.setFont(f)
        bottom.addWidget(self.progress, stretch=1)
        bottom.addWidget(self.btn_undo)
        bottom.addWidget(self.btn_apply)
        root.addLayout(bottom)

        self.statusBar().showMessage("0 files")

        # shortcuts -----------------------------------------------------------
        for keys, fn in ((QKeySequence("Del"), self.remove_selected),
                         (QKeySequence("Ctrl+A"), self.table.selectAll)):
            sc = QShortcut(keys, self.table)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        # connections -----------------------------------------------------------
        self.btn_add_files.clicked.connect(self.add_files_dialog)
        self.btn_add_folder.clicked.connect(self.add_folder_dialog)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_up.clicked.connect(lambda: self.move_rows(-1))
        self.btn_down.clicked.connect(lambda: self.move_rows(1))
        self.btn_browse.clicked.connect(self.browse_dest)
        self.btn_apply.clicked.connect(self.apply)
        self.btn_undo.clicked.connect(self.undo_last)
        self.table.files_dropped.connect(self.add_paths)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.doubleClicked.connect(
            lambda idx: self.play_row(self.proxy.mapToSource(idx).row()))
        self.edit_filter.textChanged.connect(self.proxy.set_filter)
        self.chk_inplace.toggled.connect(self._inplace_changed)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh_plan)
        for e in (self.edit_template, self.edit_find, self.edit_replace,
                  self.edit_dest):
            e.textChanged.connect(lambda: self._debounce.start())

        self.chk_regex.toggled.connect(self.refresh_plan)
        self.combo_case.currentIndexChanged.connect(self.refresh_plan)
        self.spin_start.valueChanged.connect(self.refresh_plan)
        self.spin_digits.valueChanged.connect(self.refresh_plan)
        self.combo_sub.currentIndexChanged.connect(self.refresh_plan)
        self.chk_overwrite.toggled.connect(self.refresh_plan)
        self.rb_move.toggled.connect(self.refresh_plan)
        self.rb_copy.toggled.connect(self.refresh_plan)
        
        self.combo_sort.currentIndexChanged.connect(self.sort_items)
        
        self.combo_presets.currentIndexChanged.connect(self._preset_selected)
        self.btn_preset_save.clicked.connect(self._preset_save)
        self.btn_preset_delete.clicked.connect(self._preset_delete)
        # ------------------------------
        
        self._groups = [self.g1, self.g2, self.g3]

    def _menus(self):
        def act(text, slot=None, shortcut=None, checkable=False):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setCheckable(checkable)
            if slot:
                a.triggered.connect(slot)
            return a

        self.act_add_files = act("&Add files…", self.add_files_dialog, "Ctrl+O")
        self.act_add_folder = act("Add f&older…", self.add_folder_dialog, "Ctrl+Shift+O")
        self.act_export = act("&Export plan as CSV…", self.export_csv, "Ctrl+E")
        self.act_clear_all = act("Clear &all", self.clear_all)
        self.act_quit = act("E&xit", self.close, "Ctrl+Q")
        self.act_undo_last = act("&Undo last batch", self.undo_last, "Ctrl+Z")
        self.act_undo_hist = act("Undo &history…", self.undo_history, "Ctrl+H")
        self.act_remove = act("&Remove selected", self.remove_selected)
        self.act_refresh = act("&Refresh preview", self.refresh_plan, "F5")
        self.act_dark = act("&Dark theme", None, "Ctrl+T", checkable=True)
        self.act_dark.toggled.connect(self._theme_changed)
        self.act_about = act("&About", self._about)
        self.act_cancel = act("Cancel batch", self._cancel_run, "Esc")

        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        for a in (self.act_add_files, self.act_add_folder, self.act_export):
            m_file.addAction(a)
        m_file.addAction(self.act_clear_all)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)
        m_edit = mb.addMenu("&Edit")
        m_edit.addAction(self.act_undo_last)
        m_edit.addAction(self.act_undo_hist)
        m_edit.addSeparator()
        m_edit.addAction(self.act_remove)
        m_view = mb.addMenu("&View")
        m_view.addAction(self.act_dark)
        m_view.addAction(self.act_refresh)
        m_help = mb.addMenu("&Help")
        m_help.addAction(self.act_about)
        self.addAction(self.act_cancel)          # Esc while a batch runs
        # table-scoped Delete / Ctrl+A shown in the Edit menu, work only in table
        self.act_remove.setShortcut(QKeySequence("Del"))
        self.act_remove.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    # ------------------------------------------------------------- actions
    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose WAV files", "",
            "WAV audio (*.wav *.wave);;All files (*)")
        self.add_paths(files)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder of WAV files")
        if d:
            self.add_paths([d])

    def add_paths(self, paths):
        known = {it.path.resolve() for it in self.model.items}
        new_items = []
        for s in paths:
            p = Path(s)
            if p.is_dir():
                scan = p.rglob("*") if self.chk_recursive.isChecked() else p.iterdir()
                candidates = sorted(scan)
            else:
                candidates = [p]
            for c in candidates:
                if not c.is_file() or c.suffix.lower() not in AUDIO_EXTS:
                    continue
                key = c.resolve()
                if key in known:
                    continue
                known.add(key)
                try:
                    size = c.stat().st_size
                except OSError:
                    continue
                new_items.append(FileItem(path=c, uid=next(self._uid), size=size))
        if not new_items:
            return
        self.model.add_items(new_items)
        self._meta_queue.extend((it.uid, it.path) for it in new_items)
        self._pump_meta()
        self.refresh_plan()
        self.statusBar().showMessage(f"Added {len(new_items)} file(s)", 4000)

    def _pump_meta(self):
        if self._meta_worker and self._meta_worker.isRunning():
            return
        if not self._meta_queue:
            return
        batch, self._meta_queue = self._meta_queue, []
        self._meta_worker = MetaWorker(batch, self)
        self._meta_worker.item_meta.connect(self._on_item_meta)
        self._meta_worker.meta_done.connect(self._on_meta_done)
        self._meta_worker.start()

    def _on_item_meta(self, uid, info, tags, key):
        row = next((r for r, it in enumerate(self.model.items) if it.uid == uid), None)
        if row is None:
            return
        self.model.set_meta(row, info, tags)
        if key is not None:
            first_uid = self._seen_keys.setdefault(key, uid)
            if first_uid != uid:
                frow = next((r for r, it in enumerate(self.model.items)
                             if it.uid == first_uid), None)
                if frow is not None:
                    self.model.items[row].dup_of = frow
                    self.model.set_status(row, f"possible duplicate of #{frow + 1}")

    def _on_meta_done(self):
        self._meta_worker = None
        self.refresh_plan()
        self._pump_meta()

    def remove_selected(self):
        rows = [self.proxy.mapToSource(i).row()
                for i in self.table.selectionModel().selectedRows()]
        self.model.remove_rows(rows)
        self.refresh_plan()

    def clear_all(self):
        self.model.clear()
        self._seen_keys.clear()
        self.refresh_plan()

    def browse_dest(self):
        d = QFileDialog.getExistingDirectory(self, "Destination folder")
        if d:
            self.edit_dest.setText(d)

    def move_rows(self, delta):
        rows = sorted({self.proxy.mapToSource(i).row()
                       for i in self.table.selectionModel().selectedRows()},
                      reverse=delta > 0)
        changed = any(self.model.move_row(r, delta) for r in rows)
        if changed:
            self.refresh_plan()

    def sort_items(self, *_):
        keys = (lambda it: it.path.name.lower(),
                lambda it: _mtime(it.path),
                lambda it: (it.info or {}).get("rate", 0),
                lambda it: (it.info or {}).get("dur", 0))
        self.model.resort(keys[self.combo_sort.currentIndex()])
        self.refresh_plan()

    def _inplace_changed(self, on):
        for w in (self.rb_move, self.rb_copy, self.edit_dest,
                  self.btn_browse, self.combo_sub):
            w.setEnabled(not on)
        self.refresh_plan()

    def context_menu(self, pos):
        rows = sorted({self.proxy.mapToSource(i).row()
                       for i in self.table.selectionModel().selectedRows()})
        if not rows:
            return
        menu = QMenu(self)
        a_inc = menu.addAction("Include selected")
        a_exc = menu.addAction("Exclude selected")
        menu.addSeparator()
        a_rm = menu.addAction("Remove from list")
        a_open = menu.addAction("Open containing folder")
        a_copy = menu.addAction("Copy full path")
        a_play = menu.addAction("▶ Play") if HAS_AUDIO else None
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is a_inc or chosen is a_exc:
            for r in rows:
                self.model.items[r].include = chosen is a_inc
                self.model.dataChanged.emit(self.model.index(r, 0),
                                            self.model.index(r, COL_STATUS))
            self.refresh_plan()
        elif chosen is a_rm:
            self.remove_selected()
        elif chosen is a_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(
                str(self.model.items[rows[0]].path.parent)))
        elif chosen is a_copy:
            QApplication.clipboard().setText(
                "\n".join(str(self.model.items[r].path) for r in rows))
        elif a_play and chosen is a_play:
            self.play_row(rows[0])

    def play_row(self, row):
        if not HAS_AUDIO or not (0 <= row < len(self.model.items)):
            return
        if self._player is None:
            self._player = QSoundEffect(self)
            self._player.setVolume(0.9)
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(self.model.items[row].path)))
        self._player.play()

    def _about(self):
        QMessageBox.about(self, "About",
                          "<b>WAV Renamer &amp; Organizer 3.0</b><br>"
                          "Batch-rename and organise WAV files.<br>"
                          "PySide6 · metadata-aware · undoable.")

    def _theme_changed(self, on):
        apply_theme(QApplication.instance(), on)
        self.settings.setValue("dark", on)

    def _cancel_run(self):
        if self.running and self.worker:
            self.worker.cancel()

    # ---------------------------------------------------------------- logic
    def new_stem(self, item: FileItem, num: int) -> str:
        name = item.path.stem
        find, repl = self.edit_find.text(), self.edit_replace.text()
        if find:
            if self.chk_regex.isChecked():
                try:
                    name = re.sub(find, repl, name)
                except re.error:
                    name = name.replace(find, repl)
            else:
                name = name.replace(find, repl)
        case = self.combo_case.currentIndex()
        if case == 1:
            name = name.lower()
        elif case == 2:
            name = name.upper()
        elif case == 3:
            name = name.title()

        info, now = item.info or {}, datetime.now()
        subs = {
            "name": name,
            "n": str(num).zfill(self.spin_digits.value()),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H%M%S"),
            "rate": fmt_rate(info.get("rate", 0)).replace(" ", ""),
            "sr": str(info.get("rate", "")),
            "bits": str(info.get("bits", "")),
            "ch": str(info.get("ch", "")),
            "dur": fmt_dur(info.get("dur", 0)),
            "parent": item.path.parent.name,
            "title": item.tags.get("title", ""),
            "artist": item.tags.get("artist", ""),
            "comment": item.tags.get("comment", ""),
        }
        template = self.edit_template.text().strip() or "{name}"
        stem = re.sub(r"\{(\w+)\}", lambda m: subs.get(m.group(1), ""), template)
        return ILLEGAL.sub("_", stem).strip(" .") or "untitled"

    def folder_for(self, item: FileItem):
        if self.chk_inplace.isChecked():
            return item.path.parent
        base = self.edit_dest.text().strip()
        if not base:
            return None
        base = Path(base)
        mode = self.combo_sub.currentIndex()
        if mode == 1 and item.info:
            return base / f"{item.info['rate']} Hz"
        if mode in (2, 3):
            fmt = "%Y-%m" if mode == 2 else "%Y-%m-%d"
            return base / datetime.fromtimestamp(_mtime(item.path)).strftime(fmt)
        return base

    def build_plan(self):
        """[(num|None, stem, target|None)] aligned with model rows.
        Numbering counts only included rows; uniqueness is per target folder."""
        plan, used, n = [], {}, self.spin_start.value()
        for it in self.model.items:
            if not it.include:
                plan.append((None, "", None))
                continue
            stem = self.new_stem(it, n)
            folder = self.folder_for(it)
            seen = used.setdefault(folder, set())
            base, k = stem, 2
            while stem in seen:
                stem = f"{base} ({k})"
                k += 1
            seen.add(stem)
            target = folder / f"{stem}{it.path.suffix}" if folder else None
            plan.append((n, stem, target))
            n += 1
        return plan

    def refresh_plan(self, *_):
        if self.running:
            return
        plan = self.build_plan()
        self.model.apply_plan(plan)

        items = self.model.items
        inc = [it for it in items if it.include]
        pending = sum(1 for it in items if it.pending)
        tot_dur = sum((it.info or {}).get("dur", 0) for it in inc)
        tot_size = sum(it.size for it in inc)
        msg = f"{len(inc)}/{len(items)} selected · {fmt_dur(tot_dur)} · {fmt_size(tot_size)}"
        if pending:
            msg += f" · reading {pending}…"
        self.statusBar().showMessage(msg)

    def _set_inputs(self, on):
        for g in self._groups:
            g.setEnabled(on)
        self.btn_undo.setEnabled(on and bool(self._load_history()))
        self.act_undo_last.setEnabled(on and bool(self._load_history()))
        self.act_cancel.setEnabled(not on)
        self.act_refresh.setEnabled(on)

    # ---------------------------------------------------------------- apply
    def apply(self):
        if self.running:                       # button doubles as Cancel
            self._cancel_run()
            return

        plan = self.build_plan()
        if not any(p[0] is not None for p in plan):
            QMessageBox.information(self, "Nothing to do",
                                    "Add some WAV files (and tick them) first.")
            return
        if not self.chk_inplace.isChecked() and not self.edit_dest.text().strip():
            QMessageBox.warning(self, "No destination",
                                "Choose a destination folder first.")
            return

        action = "copy" if self.rb_copy.isChecked() else "move"
        jobs = [(r, self.model.items[r].path, plan[r][2], action)
                for r in range(len(plan))
                if plan[r][0] is not None and plan[r][2] is not None]

        overwrite = self.chk_overwrite.isChecked()
        clashes = [d for _r, s, d, _a in jobs
                   if d.exists() and s.resolve() != d.resolve()]
        if clashes and not overwrite:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Name collisions")
            box.setText(f"{len(clashes)} target file(s) already exist.")
            box.setInformativeText("Overwrite them, or auto-rename the new "
                                   "files (e.g. “name (2).wav”)?")
            btn_ow = box.addButton("Overwrite", QMessageBox.ButtonRole.YesRole)
            box.addButton("Auto-rename", QMessageBox.ButtonRole.NoRole)
            btn_cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is btn_cancel:
                return
            overwrite = box.clickedButton() is btn_ow

        self.running = True
        self._run_total = len(jobs)
        self._set_inputs(False)
        self.btn_apply.setText("Cancel")
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.show()
        self.worker = Worker(jobs, overwrite)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.row_status.connect(self.model.set_status)
        self.worker.current.connect(self._show_current)
        self.worker.batch_done.connect(self._batch_done)
        self.worker.start()

    def _show_current(self, pos, name):
        self.statusBar().showMessage(f"Processing {pos}/{self._run_total} — {name}")

    def _batch_done(self, done, errors, log):
        was_cancelled = self.worker is not None and self.worker.cancelled
        self.running = False
        self._set_inputs(True)
        self.btn_apply.setText("Apply")
        self.progress.hide()

        # in-place renames stay in the list with their new path; cross-folder
        # moves leave the list; copies stay marked green
        remove = []
        for e in reversed(log):
            r = e.get("row")
            if r is None or r >= len(self.model.items):
                continue
            src, dst = Path(e["src"]), Path(e["dst"])
            if e["action"] == "move" and src.parent == dst.parent \
                    and self.model.items[r].path == src:
                it = self.model.items[r]
                it.path = dst
                it.status = "renamed"
                self.model.dataChanged.emit(self.model.index(r, COL_ORIG),
                                            self.model.index(r, COL_STATUS))
            elif e["action"] == "move":
                remove.append(r)
        self.model.remove_rows(remove)
        self.refresh_plan()

        if was_cancelled:
            QMessageBox.warning(self, "Cancelled",
                                f"Stopped early — {done} file(s) were processed.")
        elif errors:
            QMessageBox.warning(self, "Finished with errors",
                                f"{done} file(s) processed.\n\nSome failed:\n"
                                + "\n".join(errors[:15]))
        else:
            QMessageBox.information(
                self, "Done",
                f"{done} file(s) processed.\n\nTip: Ctrl+Z (or “Undo history…”) "
                f"reverses a batch.")

    # --------------------------------------------------------------- undo
    def _load_history(self) -> list:
        try:
            data = json.loads(HISTORY_FILE.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_history(self, hist: list):
        del hist[HISTORY_MAX:]
        try:
            HISTORY_FILE.write_text(json.dumps(hist, indent=1))
        except OSError:
            pass
        has = bool(hist)
        self.btn_undo.setEnabled(has and not self.running)
        self.act_undo_last.setEnabled(has and not self.running)

    @staticmethod
    def _perform_undo(batch):
        errors, restored = [], []
        for entry in reversed(batch.get("entries", [])):
            src, dst = Path(entry["src"]), Path(entry["dst"])
            try:
                if entry["action"] == "copy":
                    if dst.exists():
                        dst.unlink()
                elif dst.exists():
                    shutil.move(str(dst), str(src))
                    restored.append(src)
            except OSError as e:
                errors.append(f"{dst.name}: {e}")
        return errors, restored

    def _re_add(self, paths):
        if paths:
            self.add_paths([str(p) for p in paths])

    def undo_last(self):
        hist = self._load_history()
        if not hist:
            QMessageBox.information(self, "Undo", "Nothing to undo.")
            return
        errors, restored = self._perform_undo(hist[0])
        hist.pop(0)
        self._save_history(hist)
        self._re_add(restored)
        self.refresh_plan()
        if errors:
            QMessageBox.warning(self, "Undo finished with errors",
                                "\n".join(errors[:15]))
        else:
            QMessageBox.information(self, "Undo", "Last batch reversed.")

    def undo_history(self):
        hist = self._load_history()
        if not hist:
            QMessageBox.information(self, "Undo", "Nothing to undo.")
            return
        dlg = UndoDialog(hist, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        idx = dlg.listw.currentRow()
        if not (0 <= idx < len(hist)):
            return
        errors, restored = self._perform_undo(hist[idx])
        hist.pop(idx)
        self._save_history(hist)
        self._re_add(restored)
        self.refresh_plan()
        if errors:
            QMessageBox.warning(self, "Undo finished with errors",
                                "\n".join(errors[:15]))
        else:
            QMessageBox.information(self, "Undo", "Batch reversed.")

    # ---------------------------------------------------------------- csv
    def export_csv(self):
        if not self.model.items:
            QMessageBox.information(self, "Export", "The list is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export plan as CSV",
                                              "wav_plan.csv", "CSV (*.csv)")
        if not path:
            return
        action = "copy" if self.rb_copy.isChecked() else "move"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["num", "source", "target", "action", "sample_rate",
                        "bits", "channels", "seconds", "bytes"])
            for it in self.model.items:
                if not it.include or not it.target:
                    continue
                i = it.info or {}
                w.writerow([it.num, str(it.path), str(it.target), action,
                            i.get("rate", ""), i.get("bits", ""), i.get("ch", ""),
                            round(i.get("dur", 0), 2), it.size])
        self.statusBar().showMessage(f"Plan exported to {path}", 6000)

    # ------------------------------------------------------------- presets
    def _preset_state(self):
        return {
            "template": self.edit_template.text(),
            "find": self.edit_find.text(),
            "replace": self.edit_replace.text(),
            "regex": self.chk_regex.isChecked(),
            "case": self.combo_case.currentIndex(),
            "start": self.spin_start.value(),
            "digits": self.spin_digits.value(),
            "sub": self.combo_sub.currentIndex(),
            "inplace": self.chk_inplace.isChecked(),
            "overwrite": self.chk_overwrite.isChecked(),
            "mode": "copy" if self.rb_copy.isChecked() else "move",
        }

    def _apply_preset(self, p):
        self.edit_template.setText(p.get("template", "{name}"))
        self.edit_find.setText(p.get("find", ""))
        self.edit_replace.setText(p.get("replace", ""))
        self.chk_regex.setChecked(_to_bool(p.get("regex")))
        self.combo_case.setCurrentIndex(_to_int(p.get("case"), 0))
        self.spin_start.setValue(_to_int(p.get("start"), 1))
        self.spin_digits.setValue(_to_int(p.get("digits"), 3))
        self.combo_sub.setCurrentIndex(_to_int(p.get("sub"), 0))
        self.chk_inplace.setChecked(_to_bool(p.get("inplace")))
        self.chk_overwrite.setChecked(_to_bool(p.get("overwrite")))
        self.rb_copy.setChecked(p.get("mode") == "copy")
        self.rb_move.setChecked(p.get("mode") != "copy")

    def _persist_presets(self):
        self.settings.setValue("presets", json.dumps(self.presets))

    def _populate_presets(self, select=None):
        self._preset_lock = True
        self.combo_presets.clear()
        self.combo_presets.addItem("(select preset…)")
        self.combo_presets.addItems(sorted(self.presets))
        if select:
            self.combo_presets.setCurrentText(select)
        self._preset_lock = False

    def _preset_selected(self, idx):
        if self._preset_lock or idx <= 0:
            return
        self._apply_preset(self.presets.get(self.combo_presets.currentText(), {}))

    def _preset_save(self):
        name, ok = QInputDialog.getText(self, "Save preset",
                                        "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        self.presets[name] = self._preset_state()
        self._persist_presets()
        self._populate_presets(select=name)

    def _preset_delete(self):
        if self.combo_presets.currentIndex() <= 0:
            return
        name = self.combo_presets.currentText()
        if QMessageBox.question(self, "Delete preset",
                                f"Delete preset “{name}”?") == \
                QMessageBox.StandardButton.Yes:
            self.presets.pop(name, None)
            self._persist_presets()
            self._populate_presets()

    # ------------------------------------------------------------- settings
    def _load_settings(self):
        s = self.settings
        try:
            geo = s.value("geometry")
            if geo is not None:
                self.restoreGeometry(geo)
        except Exception:
            pass
        self.edit_dest.setText(s.value("dest", ""))
        self.edit_template.setText(s.value("template", "{name}"))
        self.edit_find.setText(s.value("find", ""))
        self.edit_replace.setText(s.value("replace", ""))
        self.combo_case.setCurrentIndex(_to_int(s.value("case"), 0))
        self.combo_sort.setCurrentIndex(_to_int(s.value("sort"), 0))
        self.combo_sub.setCurrentIndex(_to_int(s.value("subfolders"), 0))
        self.spin_start.setValue(_to_int(s.value("start"), 1))
        self.spin_digits.setValue(_to_int(s.value("digits"), 3))
        self.chk_regex.setChecked(_to_bool(s.value("regex")))
        self.chk_overwrite.setChecked(_to_bool(s.value("overwrite")))
        self.chk_inplace.setChecked(_to_bool(s.value("inplace")))
        self.chk_recursive.setChecked(_to_bool(s.value("recursive"), True))
        self.rb_copy.setChecked(str(s.value("mode", "move")) == "copy")
        try:
            self.presets = json.loads(s.value("presets") or "{}")
            if not isinstance(self.presets, dict):
                self.presets = {}
        except Exception:
            self.presets = {}
        self.act_dark.blockSignals(True)
        self.act_dark.setChecked(_to_bool(s.value("dark")))
        self.act_dark.blockSignals(False)

    def closeEvent(self, event):
        if self._meta_worker and self._meta_worker.isRunning():
            self._meta_worker.stop_flag = True
            self._meta_worker.wait(1500)
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("dest", self.edit_dest.text())
        s.setValue("template", self.edit_template.text())
        s.setValue("find", self.edit_find.text())
        s.setValue("replace", self.edit_replace.text())
        s.setValue("case", self.combo_case.currentIndex())
        s.setValue("sort", self.combo_sort.currentIndex())
        s.setValue("subfolders", self.combo_sub.currentIndex())
        s.setValue("start", self.spin_start.value())
        s.setValue("digits", self.spin_digits.value())
        s.setValue("regex", self.chk_regex.isChecked())
        s.setValue("overwrite", self.chk_overwrite.isChecked())
        s.setValue("inplace", self.chk_inplace.isChecked())
        s.setValue("recursive", self.chk_recursive.isChecked())
        s.setValue("mode", "copy" if self.rb_copy.isChecked() else "move")
        s.sync()
        super().closeEvent(event)


def apply_theme(app: QApplication, dark: bool):
    app.setStyle("Fusion")
    if not dark:
        app.setPalette(app.style().standardPalette())
        return
    p = QPalette(QColor(45, 45, 48))
    p.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base, QColor(30, 30, 32))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(38, 38, 40))
    p.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button, QColor(55, 55, 58))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 50, 52))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(150, 150, 150))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
               QColor(120, 120, 120))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
               QColor(120, 120, 120))
    app.setPalette(p)


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("WavTools")
    app.setApplicationName("WavOrganizer")
    apply_theme(app, _to_bool(QSettings().value("dark")))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()