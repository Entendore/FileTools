#!/usr/bin/env python3
"""
WAV -> MP3 Chunker — enhanced PySide6 GUI
Features: drag & drop, scan table, waveform preview with chunk markers,
dark theme, persistent settings. No ffmpeg needed.

    pip install PySide6 numpy soundfile lameenc
"""

import random
import sys
import traceback
from pathlib import Path

import numpy as np
import soundfile as sf
import lameenc

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QColor, QFontDatabase, QPalette, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

MP3_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
WAV_EXTS = {".wav", ".wave"}

ALGORITHMS = (
    "Consecutive (front of file)",
    "Evenly spaced (whole file)",
    "Equal partitions (head of each)",
    "Random (may overlap)",
    "Random spread (non-overlapping)",
)
ALGO_HELP = {
    ALGORITHMS[0]: "Chunks are cut back-to-back from the start: 0-10s, 10-20s, 20-30s ...",
    ALGORITHMS[1]: "Chunk starts are spread evenly so they cover the file from beginning to end.",
    ALGORITHMS[2]: "File is divided into N equal parts; the first 10s of each part is taken.",
    ALGORITHMS[3]: "Each chunk starts at a random position anywhere in the file (chunks may overlap).",
    ALGORITHMS[4]: "File divided into N equal parts; a random 10s window is picked inside each part (no overlaps).",
}
FILL_MODES = ("Pad with silence", "Loop audio", "Skip short chunks")


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def nearest_mp3_rate(sr: int) -> int:
    return min(MP3_RATES, key=lambda r: abs(r - sr))


def resample(data: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or data.shape[0] == 0:
        return data
    n_in = data.shape[0]
    n_out = max(1, round(n_in * sr_out / sr_in))
    x_in = np.arange(n_in, dtype=np.float64)
    x_out = np.minimum(np.arange(n_out, dtype=np.float64) * (sr_in / sr_out), n_in - 1)
    if data.ndim == 1:
        return np.interp(x_out, x_in, data).astype(np.float32)[:, None]
    chans = [np.interp(x_out, x_in, data[:, c]) for c in range(data.shape[1])]
    return np.stack(chans, axis=1).astype(np.float32)


def plan_starts(duration: float, chunk_len: float, count: int,
                algorithm: str, rng: random.Random):
    count = max(1, count)
    if duration <= 0:
        return []

    if algorithm == ALGORITHMS[0]:                       # Consecutive
        starts = [i * chunk_len for i in range(count) if i * chunk_len < duration]
        return starts or [0.0]

    if algorithm == ALGORITHMS[1]:                       # Evenly spaced
        if duration <= chunk_len or count == 1:
            return [0.0]
        span = duration - chunk_len
        return [span * i / (count - 1) for i in range(count)]

    if algorithm == ALGORITHMS[2]:                       # Equal partitions
        if duration <= chunk_len:
            return [0.0]
        seg = duration / count
        return [i * seg for i in range(count)]

    if algorithm == ALGORITHMS[3]:                       # Random anywhere
        hi = duration - chunk_len
        if hi <= 0:
            return [0.0]
        return [rng.uniform(0.0, hi) for _ in range(count)]

    if algorithm == ALGORITHMS[4]:                       # Random spread
        if duration <= chunk_len:
            return [0.0]
        seg = duration / count
        last = max(0.0, duration - chunk_len)
        starts = []
        for i in range(count):
            lo = min(i * seg, last)
            hi = max(lo, min(i * seg + seg - chunk_len, last))
            starts.append(rng.uniform(lo, hi))
        return starts

    return []


def plan_count(duration: float, chunk_len: float, count: int,
               algorithm: str, skip_short: bool) -> int:
    starts = plan_starts(duration, chunk_len, count, algorithm, random.Random(0))
    if skip_short:
        starts = [s for s in starts if s + chunk_len <= duration + 1e-6]
    return len(starts)


# --------------------------------------------------------------------------- #
#  Background workers
# --------------------------------------------------------------------------- #

class ScanWorker(QThread):
    """Finds WAV files and emits them one by one."""
    found = Signal(int, object)          # gen, file-info dict
    finished_scan = Signal(int, int)     # gen, number of files

    def __init__(self, root: Path, recursive: bool, gen: int):
        super().__init__()
        self.root, self.recursive, self.gen = root, recursive, gen
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        n = 0
        it = self.root.rglob("*") if self.recursive else self.root.glob("*")
        for p in sorted(it):
            if self._stop:
                break
            if not (p.is_file() and p.suffix.lower() in WAV_EXTS):
                continue
            try:
                info = sf.info(str(p))
            except Exception:
                continue
            n += 1
            self.found.emit(self.gen, dict(
                path=str(p), name=p.name,
                duration=info.frames / info.samplerate if info.samplerate else 0.0,
                channels=info.channels, rate=info.samplerate))
        self.finished_scan.emit(self.gen, n)


class PreviewWorker(QThread):
    """Builds a min/max envelope of a file for the waveform view."""
    loaded = Signal(int, object)         # gen, dict(env, duration, rate, path)
    failed = Signal(int, str, str)       # gen, path, error

    def __init__(self, path: str, gen: int):
        super().__init__()
        self.path, self.gen = path, gen
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            info = sf.info(self.path)
            n, sr = info.frames, info.samplerate
            block = max(1, n // 2000)            # ~2000 envelope points max
            mins, maxs = [], []
            pos = 0
            while pos < n:
                if self._stop:
                    return
                stop = min(n, pos + block)
                data, _ = sf.read(self.path, start=pos, stop=stop,
                                  dtype="float32", always_2d=True)
                if data.size:
                    mono = data.mean(axis=1)
                    mins.append(float(mono.min()))
                    maxs.append(float(mono.max()))
                pos = stop
            if not mins:
                mins, maxs = [0.0], [0.0]
            env = np.stack([np.clip(np.array(mins), -1, 1),
                            np.clip(np.array(maxs), -1, 1)], axis=1)
            self.loaded.emit(self.gen, dict(env=env,
                                            duration=n / sr if sr else 0.0,
                                            rate=sr, path=self.path))
        except Exception as e:
            self.failed.emit(self.gen, self.path, str(e))


class ConvertWorker(QThread):
    progress = Signal(int, int)
    line = Signal(str)
    done = Signal(int, int, int)
    failed = Signal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self._work()
        except Exception:
            self.failed.emit(traceback.format_exc())

    def _work(self):
        cfg = self.cfg
        src = Path(cfg["source"]).resolve()
        out_root = Path(cfg["output"])
        rng = random.Random(cfg["seed"] or None)
        used_names = set()

        self.line.emit(f"Scanning {src} ...")
        it = src.rglob("*") if cfg["recursive"] else src.glob("*")
        wavs = sorted(p for p in it if p.is_file() and p.suffix.lower() in WAV_EXTS)
        if not wavs:
            self.line.emit("No .wav files found.")
            self.done.emit(0, 0, 0)
            return
        self.line.emit(f"Found {len(wavs)} wav file(s).")

        jobs = []
        skipped = 0
        for p in wavs:
            if self._stop:
                break
            try:
                info = sf.info(str(p))
            except Exception as e:
                self.line.emit(f"[skip] {p.name}: unreadable ({e})")
                skipped += 1
                continue
            dur = info.frames / info.samplerate
            starts = plan_starts(dur, cfg["chunk_len"], cfg["count"],
                                 cfg["algorithm"], rng)
            if cfg["fill_mode"] == FILL_MODES[2]:
                starts = [s for s in starts if s + cfg["chunk_len"] <= dur + 1e-6]
            if not starts:
                self.line.emit(f"[skip] {p.name}: {dur:.1f}s -> no chunks possible")
                skipped += 1
                continue
            jobs.append((p, info, starts, dur))

        total = sum(len(s) for _, _, s, _ in jobs)
        self.line.emit(f"Plan: {len(jobs)} file(s), {total} mp3 chunk(s) to write.")
        self.progress.emit(0, max(total, 1))

        written = errors = done_chunks = 0
        for p, info, starts, dur in jobs:
            if self._stop:
                break
            try:
                rel_parent = p.parent.relative_to(src)
                out_dir = out_root / rel_parent if cfg["preserve"] else out_root
                out_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.line.emit(f"[error] {p.name}: output dir ({e})")
                errors += 1
                continue

            self.line.emit(f"- {p.relative_to(src)}  [{info.samplerate} Hz, "
                           f"{info.channels} ch, {dur:.1f}s] -> {len(starts)} chunk(s)")
            target_sr = info.samplerate if info.samplerate in MP3_RATES \
                else nearest_mp3_rate(info.samplerate)
            total_frames = int(round(cfg["chunk_len"] * info.samplerate))

            for idx, start_s in enumerate(starts, 1):
                if self._stop:
                    break
                try:
                    i0 = int(round(start_s * info.samplerate))
                    chunk = self._read_chunk(p, info, i0, total_frames,
                                             cfg["fill_mode"])
                    if chunk.shape[0] == 0:
                        raise RuntimeError("empty chunk")
                    if target_sr != info.samplerate:
                        chunk = resample(chunk, info.samplerate, target_sr)
                    mp3 = self._encode(chunk, target_sr, info.channels,
                                       cfg["bitrate"])
                    dest = out_dir / f"{p.stem}_{idx:03d}.mp3"
                    k = 1
                    while str(dest) in used_names:
                        dest = out_dir / f"{p.stem}_{idx:03d}_{k}.mp3"
                        k += 1
                    used_names.add(str(dest))
                    with open(dest, "wb") as fh:
                        fh.write(mp3)
                    written += 1
                    self.line.emit(f"    + {dest.name}  "
                                   f"({start_s:.1f}s - {start_s + cfg['chunk_len']:.1f}s)")
                except Exception as e:
                    errors += 1
                    self.line.emit(f"    [error] {p.name} chunk {idx}: {e}")
                finally:
                    done_chunks += 1
                    self.progress.emit(done_chunks, max(total, 1))

        if self._stop:
            self.line.emit("--- Cancelled ---")
        self.done.emit(written, skipped, errors)

    def _read_chunk(self, path: Path, info, i0: int, total: int, fill_mode: str):
        n = info.frames
        out = np.zeros((total, info.channels), dtype=np.float32)
        filled = 0
        if i0 < n:
            stop = min(n, i0 + total)
            data, _ = sf.read(str(path), start=i0, stop=stop,
                              dtype="float32", always_2d=True)
            take = min(total, data.shape[0])
            out[:take] = data[:take]
            filled = take
        if filled < total and fill_mode == FILL_MODES[1] and n > 0:
            while filled < total:
                take = min(total - filled, n)
                data, _ = sf.read(str(path), start=0, stop=take,
                                  dtype="float32", always_2d=True)
                if data.shape[0] == 0:
                    break
                out[filled:filled + data.shape[0]] = data
                filled += data.shape[0]
        return out

    @staticmethod
    def _encode(chunk: np.ndarray, sr: int, channels: int, bitrate: int) -> bytes:
        pcm = np.clip(chunk, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2").tobytes()
        enc = lameenc.Encoder()
        enc.set_bit_rate(int(bitrate))
        enc.set_in_sample_rate(int(sr))
        enc.set_channels(int(channels))
        enc.set_quality(2)
        return bytes(enc.encode(pcm) + enc.flush())


# --------------------------------------------------------------------------- #
#  Waveform view
# --------------------------------------------------------------------------- #

class WaveformWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.env = None
        self.duration = 0.0
        self.markers = []
        self.chunk_len = 0.0

    def clear(self):
        self.env, self.duration, self.markers = None, 0.0, []
        self.update()

    def set_waveform(self, env, duration):
        self.env, self.duration = env, duration
        self.update()

    def set_markers(self, starts, chunk_len):
        self.markers, self.chunk_len = starts, chunk_len
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(24, 25, 28))

        if self.env is None or len(self.env) == 0:
            p.setPen(QColor(130, 130, 135))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Select a file in the table to preview its waveform")
            p.end()
            return

        mid = h // 2
        p.setPen(QPen(QColor(60, 62, 66), 1))
        p.drawLine(0, mid, w, mid)

        # chunk regions (shaded) + start markers
        if self.duration > 0:
            shade = QColor(70, 130, 240, 36)
            for s in self.markers:
                x0 = int(s / self.duration * w)
                x1 = int(min(1.0, (s + self.chunk_len) / self.duration) * w)
                p.fillRect(x0, 0, max(1, x1 - x0), h, shade)

        mins, maxs = self.env[:, 0], self.env[:, 1]
        n = len(mins)
        amp = max(4, mid - 4)
        p.setPen(QPen(QColor(120, 215, 160), 1))
        for x in range(w):
            i0 = x * n // w
            i1 = max(i0 + 1, (x + 1) * n // w)
            lo = float(mins[i0:i1].min())
            hi = float(maxs[i0:i1].max())
            y0 = int(mid - hi * amp)
            y1 = int(mid - lo * amp)
            if y1 <= y0:
                y1 = y0 + 1
            p.drawLine(x, y0, x, y1)

        p.setPen(QPen(QColor(255, 170, 70), 1))
        for s in self.markers:
            x = int(s / self.duration * w)
            p.drawLine(x, 0, x, h)
        p.end()


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WAV -> MP3 Chunker")
        self.setAcceptDrops(True)
        self.worker = None
        self.scan_worker = None
        self.preview_worker = None
        self.scan_gen = 0
        self.preview_gen = 0
        self.preview_duration = 0.0
        self.settings = QSettings("WavChunker", "WavChunker")
        self._build_ui()
        self._load_settings()
        if self.src_edit.text().strip():
            self.start_scan()

    # ------------------------------------------------------------------ UI #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # folder row
        grid = QGridLayout()
        grid.addWidget(QLabel("Source folder (WAVs):"), 0, 0)
        self.src_edit = QLineEdit()
        self.src_edit.setAcceptDrops(False)
        grid.addWidget(self.src_edit, 0, 1)
        btn_src = QPushButton("Browse...")
        btn_src.clicked.connect(self.pick_source)
        grid.addWidget(btn_src, 0, 2)

        grid.addWidget(QLabel("Output folder (MP3s):"), 1, 0)
        self.out_edit = QLineEdit()
        self.out_edit.setAcceptDrops(False)
        grid.addWidget(self.out_edit, 1, 1)
        btn_out = QPushButton("Browse...")
        btn_out.clicked.connect(self.pick_output)
        grid.addWidget(btn_out, 1, 2)
        root.addLayout(grid)

        # splitter: settings | table + waveform
        self.splitter = QSplitter(Qt.Horizontal)

        opts = QGroupBox("Split settings")
        form = QGridLayout(opts)

        form.addWidget(QLabel("Chunk length:"), 0, 0)
        self.chunk_spin = QDoubleSpinBox()
        self.chunk_spin.setRange(0.5, 600.0)
        self.chunk_spin.setValue(10.0)
        self.chunk_spin.setDecimals(1)
        self.chunk_spin.setSuffix(" s")
        form.addWidget(self.chunk_spin, 0, 1)

        form.addWidget(QLabel("MP3s per WAV:"), 1, 0)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 1000)
        self.count_spin.setValue(10)
        form.addWidget(self.count_spin, 1, 1)

        form.addWidget(QLabel("Split algorithm:"), 2, 0)
        self.algo_combo = QComboBox()
        self.algo_combo.addItems(ALGORITHMS)
        form.addWidget(self.algo_combo, 2, 1)

        self.algo_help = QLabel(ALGO_HELP[ALGORITHMS[0]])
        self.algo_help.setWordWrap(True)
        self.algo_help.setStyleSheet("color: gray;")
        form.addWidget(self.algo_help, 3, 0, 1, 2)
        self.algo_combo.currentTextChanged.connect(
            lambda t: self.algo_help.setText(ALGO_HELP.get(t, "")))

        form.addWidget(QLabel("Bitrate:"), 4, 0)
        self.br_combo = QComboBox()
        self.br_combo.addItems(["96", "128", "160", "192", "256", "320"])
        self.br_combo.setCurrentText("192")
        form.addWidget(self.br_combo, 4, 1)

        form.addWidget(QLabel("If a chunk runs past the end:"), 5, 0)
        self.fill_combo = QComboBox()
        self.fill_combo.addItems(FILL_MODES)
        form.addWidget(self.fill_combo, 5, 1)

        form.addWidget(QLabel("Random seed (0 = random):"), 6, 0)
        self.seed_spin = QSpinBox()
        self.seed_spin.setRange(0, 999999)
        self.seed_spin.setValue(0)
        form.addWidget(self.seed_spin, 6, 1)

        self.recursive_cb = QCheckBox("Include subfolders")
        self.recursive_cb.setChecked(True)
        self.recursive_cb.toggled.connect(self.start_scan)
        form.addWidget(self.recursive_cb, 7, 0, 1, 2)
        self.preserve_cb = QCheckBox("Preserve subfolder structure in output")
        self.preserve_cb.setChecked(True)
        form.addWidget(self.preserve_cb, 8, 0, 1, 2)

        btn_scan = QPushButton("Rescan folder")
        btn_scan.clicked.connect(self.start_scan)
        form.addWidget(btn_scan, 9, 0, 1, 2)
        form.setRowStretch(10, 1)

        self.file_label = QLabel("No scan yet — drop a folder or press Browse")
        self.file_label.setWordWrap(True)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["File", "Length", "Ch", "Rate", "Chunks"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)

        self.wave = WaveformWidget()
        self.preview_label = QLabel(" ")

        right = QSplitter(Qt.Vertical)
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addWidget(self.file_label)
        tv.addWidget(self.table, 1)
        right.addWidget(top)
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.addWidget(self.preview_label)
        bv.addWidget(self.wave, 1)
        right.addWidget(bottom)
        right.setSizes([360, 180])

        self.splitter.addWidget(opts)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, 3)

        # run row
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        row.addWidget(self.start_btn)
        row.addWidget(self.cancel_btn)
        self.progress = QProgressBar()
        self.progress.setFormat("%v / %m chunks")
        row.addWidget(self.progress, 1)
        root.addLayout(row)

        # log
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAcceptDrops(False)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        root.addWidget(self.log_view, 2)

        # live-updating widgets
        self.chunk_spin.valueChanged.connect(self.on_plan_changed)
        self.count_spin.valueChanged.connect(self.on_plan_changed)
        self.algo_combo.currentIndexChanged.connect(self.on_plan_changed)
        self.fill_combo.currentIndexChanged.connect(self.on_plan_changed)

        self._inputs = [self.src_edit, self.out_edit, btn_src, btn_out, opts,
                        self.chunk_spin, self.count_spin, self.algo_combo,
                        self.br_combo, self.fill_combo, self.seed_spin,
                        self.recursive_cb, self.preserve_cb]

    # ------------------------------------------------------------- actions #

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            for url in e.mimeData().urls():
                f = Path(url.toLocalFile())
                if f.is_dir():
                    e.acceptProposedAction()
                    return
        e.ignore()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            f = Path(url.toLocalFile())
            if f.is_dir():
                self.set_source(f)
                e.acceptProposedAction()
                return

    def set_source(self, folder: Path):
        self.src_edit.setText(str(folder))
        if not self.out_edit.text().strip():
            self.out_edit.setText(str(folder / "mp3_chunks"))
        self.start_scan()

    def pick_source(self):
        d = QFileDialog.getExistingDirectory(self, "Select source folder")
        if d:
            self.set_source(Path(d))

    def pick_output(self):
        d = QFileDialog.getExistingDirectory(self, "Select output folder")
        if d:
            self.out_edit.setText(d)

    def log(self, text: str):
        self.log_view.appendPlainText(text)

    def set_running(self, running: bool):
        for w in self._inputs:
            w.setEnabled(not running)
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)

    # ----------------------------------------------------------- scanning #

    def start_scan(self):
        src = self.src_edit.text().strip()
        if not src or not Path(src).is_dir():
            return
        self.scan_gen += 1
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.stop()
        self.table.setRowCount(0)
        self.table.setUpdatesEnabled(False)
        self.file_label.setText("Scanning...")
        self.scan_worker = ScanWorker(Path(src), self.recursive_cb.isChecked(),
                                      self.scan_gen)
        self.scan_worker.found.connect(self.on_scan_found)
        self.scan_worker.finished_scan.connect(self.on_scan_done)
        self.scan_worker.finished.connect(self.scan_worker.deleteLater)
        self.scan_worker.start()

    def on_scan_found(self, gen: int, d: dict):
        if gen != self.scan_gen:
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QTableWidgetItem(d["name"])
        item.setData(Qt.UserRole, d)
        item.setToolTip(d["path"])
        self.table.setItem(r, 0, item)
        self.table.setItem(r, 1, QTableWidgetItem(f"{d['duration']:.1f} s"))
        self.table.setItem(r, 2, QTableWidgetItem(str(d["channels"])))
        self.table.setItem(r, 3, QTableWidgetItem(f"{d['rate']} Hz"))
        self.table.setItem(r, 4, QTableWidgetItem("-"))

    def on_scan_done(self, gen: int, count: int):
        if gen != self.scan_gen:
            return
        self.table.setUpdatesEnabled(True)
        self.file_label.setText(f"{count} wav file(s) found")
        self.refresh_chunk_counts()

    def refresh_chunk_counts(self):
        algo = self.algo_combo.currentText()
        cl = self.chunk_spin.value()
        cnt = self.count_spin.value()
        skip = self.fill_combo.currentText() == FILL_MODES[2]
        for r in range(self.table.rowCount()):
            d = self.table.item(r, 0).data(Qt.UserRole)
            n = plan_count(d["duration"], cl, cnt, algo, skip)
            self.table.item(r, 4).setText(str(n))

    # ------------------------------------------------------------ preview #

    def on_selection_changed(self):
        row = self.table.currentRow()
        if row < 0:
            return
        d = self.table.item(row, 0).data(Qt.UserRole)
        self.preview_gen += 1
        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.stop()
        self.wave.clear()
        self.preview_duration = 0.0
        self.preview_label.setText(f"Loading {d['name']} ...")
        self.preview_worker = PreviewWorker(d["path"], self.preview_gen)
        self.preview_worker.loaded.connect(self.on_preview_loaded)
        self.preview_worker.failed.connect(self.on_preview_failed)
        self.preview_worker.finished.connect(self.preview_worker.deleteLater)
        self.preview_worker.start()

    def on_preview_loaded(self, gen: int, res: dict):
        if gen != self.preview_gen:
            return
        self.preview_duration = res["duration"]
        self.wave.set_waveform(res["env"], res["duration"])
        self.preview_label.setText(
            f"{Path(res['path']).name}  —  {res['duration']:.1f}s @ {res['rate']} Hz")
        self.refresh_markers()

    def on_preview_failed(self, gen: int, path: str, err: str):
        if gen != self.preview_gen:
            return
        self.preview_label.setText(f"Could not load {Path(path).name}: {err}")

    def on_plan_changed(self, *_):
        self.refresh_chunk_counts()
        self.refresh_markers()

    def refresh_markers(self):
        if self.preview_duration <= 0:
            return
        starts = plan_starts(self.preview_duration, self.chunk_spin.value(),
                             self.count_spin.value(),
                             self.algo_combo.currentText(), random.Random(0))
        self.wave.set_markers(starts, self.chunk_spin.value())

    # --------------------------------------------------------- conversion #

    def start(self):
        src_text = self.src_edit.text().strip()
        out_text = self.out_edit.text().strip()
        if not src_text:
            QMessageBox.warning(self, "Missing folder", "Choose a source folder first.")
            return
        src = Path(src_text)
        if not src.is_dir():
            QMessageBox.warning(self, "Not found", f"Source folder does not exist:\n{src}")
            return
        if not out_text:
            out_text = str(src / "mp3_chunks")
            self.out_edit.setText(out_text)

        cfg = dict(
            source=str(src), output=out_text,
            chunk_len=self.chunk_spin.value(),
            count=self.count_spin.value(),
            algorithm=self.algo_combo.currentText(),
            bitrate=int(self.br_combo.currentText()),
            fill_mode=self.fill_combo.currentText(),
            recursive=self.recursive_cb.isChecked(),
            preserve=self.preserve_cb.isChecked(),
            seed=self.seed_spin.value(),
        )
        self.set_running(True)
        self.log("")
        self.log("=== Run started ===")
        self.progress.setValue(0)
        self.progress.setMaximum(1)

        self.worker = ConvertWorker(cfg)
        self.worker.line.connect(self.log)
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def cancel(self):
        if self.worker:
            self.log("Cancelling after current chunk...")
            self.cancel_btn.setEnabled(False)
            self.worker.stop()

    def on_progress(self, done: int, total: int):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)

    def on_done(self, written: int, skipped: int, errors: int):
        self.log(f"=== Finished: {written} mp3 written, "
                 f"{skipped} file(s) skipped, {errors} error(s) ===")
        self.set_running(False)
        self.worker = None
        QMessageBox.information(self, "Finished",
                                f"{written} MP3 file(s) written.\n"
                                f"Skipped files: {skipped}\nErrors: {errors}")

    def on_failed(self, tb: str):
        self.log("=== FAILED ===\n" + tb)
        self.set_running(False)
        self.worker = None
        QMessageBox.critical(self, "Error", "Conversion failed - see log for details.")

    # ---------------------------------------------------------- settings #

    def _load_settings(self):
        s = self.settings
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        sizes = s.value("splitter")
        if sizes is not None:
            self.splitter.restoreState(sizes)
        self.src_edit.setText(s.value("src", "", type=str))
        self.out_edit.setText(s.value("out", "", type=str))
        self.chunk_spin.setValue(s.value("chunk", 10.0, type=float))
        self.count_spin.setValue(s.value("count", 10, type=int))
        self.algo_combo.setCurrentIndex(s.value("algo", 0, type=int))
        self.br_combo.setCurrentIndex(s.value("bitrate", 3, type=int))
        self.fill_combo.setCurrentIndex(s.value("fill", 0, type=int))
        self.seed_spin.setValue(s.value("seed", 0, type=int))
        self.recursive_cb.setChecked(s.value("recursive", True, type=bool))
        self.preserve_cb.setChecked(s.value("preserve", True, type=bool))

    def _save_settings(self):
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("splitter", self.splitter.saveState())
        s.setValue("src", self.src_edit.text())
        s.setValue("out", self.out_edit.text())
        s.setValue("chunk", self.chunk_spin.value())
        s.setValue("count", self.count_spin.value())
        s.setValue("algo", self.algo_combo.currentIndex())
        s.setValue("bitrate", self.br_combo.currentIndex())
        s.setValue("fill", self.fill_combo.currentIndex())
        s.setValue("seed", self.seed_spin.value())
        s.setValue("recursive", self.recursive_cb.isChecked())
        s.setValue("preserve", self.preserve_cb.isChecked())

    def closeEvent(self, event):
        self._save_settings()
        for w in (self.scan_worker, self.preview_worker):
            if w and w.isRunning():
                w.stop()
                w.wait(1500)
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, "Still running", "Conversion in progress. Stop and quit?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


# --------------------------------------------------------------------------- #
#  Dark palette
# --------------------------------------------------------------------------- #

def dark_palette():
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(37, 38, 42))
    pal.setColor(QPalette.WindowText, QColor(220, 220, 220))
    pal.setColor(QPalette.Base, QColor(25, 26, 29))
    pal.setColor(QPalette.AlternateBase, QColor(37, 38, 42))
    pal.setColor(QPalette.ToolTipBase, QColor(45, 46, 50))
    pal.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    pal.setColor(QPalette.Text, QColor(220, 220, 220))
    pal.setColor(QPalette.Button, QColor(45, 46, 50))
    pal.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    pal.setColor(QPalette.BrightText, Qt.red)
    pal.setColor(QPalette.Link, QColor(80, 160, 255))
    pal.setColor(QPalette.Highlight, QColor(42, 100, 170))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
    return pal


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    win = MainWindow()
    win.resize(1050, 720)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()