import os
import sys
import re
import json
import shutil
import tempfile
import subprocess

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt, lfilter, freqz, sosfreqz

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSettings, QTimer
from PySide6.QtGui import (QPainter, QPen, QColor, QPolygonF, QPointF,
                           QShortcut, QKeySequence)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox, QDoubleSpinBox,
    QCheckBox, QProgressBar, QPlainTextEdit, QFileDialog, QMessageBox,
    QStackedWidget, QSlider, QListWidget,
)

# Playback - ships with PySide6, but guard anyway (rare missing system libs)
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    HAS_MULTIMEDIA = True
except Exception:
    HAS_MULTIMEDIA = False

INPUT_EXTS = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".oga",
              ".m4a", ".wma", ".opus")


# ----------------------------------------------------------------------
# DSP helpers
# ----------------------------------------------------------------------

def peaking_eq(x, sr, f0, q, gain_db):
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / sr
    alpha = np.sin(w0) / (2 * q)
    b = np.array([1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A])
    a = np.array([1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A])
    return lfilter(b / a[0], a / a[0], x, axis=0)


def env_follow(x, sr, attack_ms, release_ms):
    """Fast-attack / slow-release envelope (max of two one-pole followers)."""
    a_at = np.exp(-1.0 / (attack_ms * sr / 1000.0))
    a_re = np.exp(-1.0 / (release_ms * sr / 1000.0))
    fast = lfilter([1 - a_at], [1, -a_at], x, axis=0)
    slow = lfilter([1 - a_re], [1, -a_re], x, axis=0)
    return np.maximum(fast, slow)


def compress(x, sr, threshold_db=-20.0, ratio=3.0, attack_ms=30.0,
             release_ms=150.0, knee_db=6.0, makeup_db=6.0):
    env = env_follow(np.abs(x), sr, attack_ms, release_ms)
    env_db = 20 * np.log10(np.maximum(env, 1e-9))
    over = env_db - threshold_db
    slope = 1.0 - 1.0 / ratio
    w = max(knee_db, 1e-6)
    reduction = np.where(
        over <= -w / 2, 0.0,
        np.where(over >= w / 2, over * slope,
                 slope * (over + w / 2) ** 2 / (2.0 * w)))
    return x * 10 ** ((-reduction + makeup_db) / 20)


def de_ess(x, sr, freq_hz, threshold_db, max_red_db):
    """Attenuate the sibilance band when its energy exceeds the threshold."""
    lo = max(freq_hz * 0.7, 800.0)
    hi = min(freq_hz * 1.4, sr / 2 * 0.98)
    if lo >= hi:
        return x
    sos = butter(2, [lo, hi], "bandpass", fs=sr, output="sos")
    band = sosfiltfilt(sos, x, axis=0)
    a = np.exp(-1.0 / (0.005 * sr))
    env = lfilter([1 - a], [1, -a], np.abs(band), axis=0)
    env_db = 20 * np.log10(np.maximum(env, 1e-9))
    over = np.clip(env_db - threshold_db, 0.0, max_red_db)
    g = 10 ** (-over / 20)
    return x + band * (g - 1.0)


def normalize_level(x, sr, mode, target_db, max_gain_db, log):
    """Gain toward target level. Returns (signal, applied_linear_gain)."""
    mode_used = mode
    measured = None
    if mode == "lufs":
        try:
            import pyloudnorm as pyln
            measured = pyln.Meter(sr).integrated_loudness(x)
            if not np.isfinite(measured):
                measured = None
        except ImportError:
            log("  ! pyloudnorm not installed - falling back to RMS "
                "(pip install pyloudnorm for true LUFS)")
            mode_used = "rms"
        except Exception:
            log("  ! LUFS measurement failed - falling back to RMS")
            mode_used = "rms"
    if measured is None:
        if mode_used == "peak":
            measured = 20 * np.log10(max(float(np.max(np.abs(x))), 1e-9))
        else:
            measured = 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-9))

    label = {"peak": "peak", "rms": "RMS", "lufs": "integrated loudness"}[mode_used]
    gain_db = min(target_db - measured, max_gain_db)
    log(f"  Normalizing {label}: measured {measured:.1f}, target {target_db:.1f}, "
        f"gain {gain_db:+.1f} dB (limit +{max_gain_db:.0f} dB)")
    return x * 10 ** (gain_db / 20), 10 ** (gain_db / 20)


def predicted_eq_response(opt, n_pts=400):
    """Static magnitude response (dB) of the enabled HPF + peaking EQs.
    Evaluated at 48 kHz - curve shape is essentially identical at 44.1 kHz."""
    sr = 48000
    freqs = np.logspace(np.log10(20), np.log10(20000), n_pts)
    total_db = np.zeros(n_pts)
    try:
        if opt["hpf"]:
            order = {12: 1, 24: 2, 48: 4}[opt["hpf_slope"]]
            sos = butter(order, opt["hpf_freq"], "highpass", fs=sr, output="sos")
            _w, h = sosfreqz(sos, worN=freqs, fs=sr)
            total_db += 2 * 20 * np.log10(np.maximum(np.abs(h), 1e-12))  # filtfilt = 2x
        for en, fk, gk, qk in (("mud", "mud_freq", "mud_gain", "mud_q"),
                               ("pres", "pres_freq", "pres_gain", "pres_q")):
            if opt[en] and opt[gk] != 0:
                A = 10 ** (opt[gk] / 40)
                w0 = 2 * np.pi * opt[fk] / sr
                alpha = np.sin(w0) / (2 * max(opt[qk], 0.1))
                b = np.array([1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A])
                a = np.array([1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A])
                _w, h = freqz(b / a[0], a / a[0], worN=freqs, fs=sr)
                total_db += 20 * np.log10(np.maximum(np.abs(h), 1e-12))
    except Exception:
        pass
    return freqs, total_db


def auto_out_path(in_path, fmt):
    ext = ".mp3" if fmt == "mp3" else ".wav"
    return os.path.splitext(in_path)[0] + "_voiceboost" + ext


def decode_input(in_path, log):
    """Return a WAV path for any supported input, decoding via ffmpeg if
    needed. Returns (wav_path, TemporaryDirectory-or-None)."""
    ext = os.path.splitext(in_path)[1].lower()
    if ext in (".wav", ".aif", ".aiff"):
        return in_path, None
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(f"Cannot read {ext} files: ffmpeg not installed")
    tmp = tempfile.TemporaryDirectory()
    out_wav = os.path.join(tmp.name, "decoded.wav")
    log(f"  Decoding {os.path.basename(in_path)} to WAV via ffmpeg...")
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                           "-i", in_path, out_wav], capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(out_wav):
        tmp.cleanup()
        raise RuntimeError(f"ffmpeg decode failed: {proc.stderr.strip()[:300]}")
    return out_wav, tmp


# ----------------------------------------------------------------------
# Output helpers (WAV direct, MP3 via ffmpeg)
# ----------------------------------------------------------------------

def encode_mp3(wav_path, mp3_path, bitrate_kbps, log):
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", wav_path, "-b:a", f"{bitrate_kbps}k", mp3_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg MP3 encode failed: {proc.stderr.strip()[:300]}")
    log(f"Encoded MP3 @ {bitrate_kbps} kbps")


def save_output(x, sr, out_path, fmt, bitrate_kbps, log):
    if fmt == "mp3":
        if shutil.which("ffmpeg") is None:
            log("! ffmpeg not found - falling back to WAV output")
            out_path = os.path.splitext(out_path)[0] + ".wav"
            sf.write(out_path, x, sr, subtype="PCM_24")
            log(f"Saved WAV: {out_path}")
            return out_path
        with tempfile.TemporaryDirectory() as tmp:
            tmp_wav = os.path.join(tmp, "out.wav")
            sf.write(tmp_wav, x, sr, subtype="PCM_24")
            encode_mp3(tmp_wav, out_path, bitrate_kbps, log)
    else:
        sf.write(out_path, x, sr, subtype="PCM_24")
        log(f"Saved WAV: {out_path}")
    return out_path


def finalize(x, sr, out_path, opt, log):
    """Shared boost -> normalize -> clip -> save stage for both methods."""
    x = x * 10 ** (opt["boost"] / 20)
    log(f"  Voice boost +{opt['boost']:.1f} dB")

    if opt["normalize"]:
        x, _ = normalize_level(x, sr, opt["norm_mode"], opt["norm_target"],
                               opt["norm_max"], log)

    x = np.clip(x, -0.98, 0.98)
    save_output(x, sr, out_path, opt["fmt"], opt["bitrate"], log)


# ----------------------------------------------------------------------
# Method 1: DSP processing
# ----------------------------------------------------------------------

def process_dsp(in_path, out_path, opt, log):
    log(f"Reading {in_path}")
    tmp = None
    try:
        src, tmp = decode_input(in_path, log)
        if opt.get("snippet"):
            start, dur = opt["snippet"]
            with sf.SoundFile(src) as f:
                sr = f.samplerate
                f.seek(int(start * sr))
                x = f.read(int(dur * sr), always_2d=True, dtype="float32")
        else:
            x, sr = sf.read(src, always_2d=True, dtype="float32")
        log(f"  {sr} Hz, {x.shape[1]} ch, {len(x)/sr:.1f}s")

        if opt["denoise"]:
            try:
                import noisereduce as nr
                mode = "stationary" if opt["den_mode"] else "non-stationary"
                log(f"  Denoising ({mode}, strength {opt['den_amt']:.2f})...")
                x = np.stack([
                    nr.reduce_noise(y=x[:, c], sr=sr, stationary=opt["den_mode"],
                                    prop_decrease=opt["den_amt"])
                    for c in range(x.shape[1])
                ], axis=1)
            except ImportError:
                log("  ! noisereduce not installed - skipping denoise")

        if opt["hpf"]:
            order = {12: 1, 24: 2, 48: 4}[opt["hpf_slope"]]
            log(f"  High-pass {opt['hpf_freq']:.0f} Hz ({opt['hpf_slope']} dB/oct)")
            sos = butter(order, opt["hpf_freq"], "highpass", fs=sr, output="sos")
            x = sosfiltfilt(sos, x, axis=0)

        if opt["mud"] and opt["mud_gain"] != 0:
            log(f"  Mud cut {opt['mud_gain']:+.1f} dB @ {opt['mud_freq']:.0f} Hz "
                f"(Q {opt['mud_q']:.2f})")
            x = peaking_eq(x, sr, opt["mud_freq"], opt["mud_q"], opt["mud_gain"])

        if opt["pres"] and opt["pres_gain"] != 0:
            log(f"  Presence {opt['pres_gain']:+.1f} dB @ {opt['pres_freq']:.0f} Hz "
                f"(Q {opt['pres_q']:.2f})")
            x = peaking_eq(x, sr, opt["pres_freq"], opt["pres_q"], opt["pres_gain"])

        if opt["comp"]:
            log(f"  Compressor: {opt['comp_ratio']:.1f}:1, thr {opt['comp_thr']:.0f} dB, "
                f"atk {opt['comp_atk']:.1f} ms, rel {opt['comp_rel']:.0f} ms, "
                f"knee {opt['comp_knee']:.0f} dB, makeup +{opt['comp_makeup']:.1f} dB")
            x = compress(x, sr, opt["comp_thr"], opt["comp_ratio"], opt["comp_atk"],
                         opt["comp_rel"], opt["comp_knee"], opt["comp_makeup"])

        if opt["deess"]:
            log(f"  De-esser @ {opt['deess_freq']:.0f} Hz, thr {opt['deess_thr']:.0f} dB, "
                f"max -{opt['deess_amt']:.0f} dB")
            x = de_ess(x, sr, opt["deess_freq"], opt["deess_thr"], opt["deess_amt"])

        finalize(x, sr, out_path, opt, log)
    finally:
        if tmp:
            tmp.cleanup()


# ----------------------------------------------------------------------
# Method 2: Demucs AI separation
# ----------------------------------------------------------------------

def process_demucs(in_path, out_path, opt, log):
    if shutil.which("demucs") is None:
        raise RuntimeError("demucs not found. Install with: pip install demucs")

    pct = opt.get("_pct")
    tmp = None
    try:
        src, tmp = decode_input(in_path, log)
        if opt.get("snippet"):
            if tmp is None:
                tmp = tempfile.TemporaryDirectory()
            start, dur = opt["snippet"]
            snip = os.path.join(tmp.name, "snippet.wav")
            with sf.SoundFile(src) as f:
                sr0 = f.samplerate
                f.seek(int(start * sr0))
                sf.write(snip, f.read(int(dur * sr0), always_2d=True),
                         sr0, subtype="PCM_24")
            src = snip
            log(f"  Preview snippet {start:.1f}s - {start + dur:.1f}s")

        log("Running Demucs vocal separation (first run downloads ~80 MB model)...")
        cmd = ["demucs", "--two-stems=vocals", "-o", tmp.name, src]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)

        shown = {"progress": False}

        def handle(line):
            m = list(re.finditer(r"(\d{1,3})%", line))
            if m and pct is not None:
                v = int(m[-1].group(1))
                pct(v)
                if not shown["progress"]:
                    log(f"  Working... {v}% (live progress in the bar)")
                    shown["progress"] = True
                return
            log("  " + line)

        buf = []
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n":
                line = "".join(buf).strip()
                buf = []
                if line:
                    handle(line)
            else:
                buf.append(ch)
        if buf:
            tail = "".join(buf).strip()
            if tail:
                handle(tail)

        if proc.wait() != 0:
            raise RuntimeError("demucs exited with an error (see log above)")
        if pct is not None:
            pct(100)

        stems_dir = None
        for root, _dirs, files in os.walk(tmp.name):
            if "vocals.wav" in files:
                stems_dir = root
                break
        if stems_dir is None:
            raise RuntimeError("Could not find separated stems in output")

        voc, sr = sf.read(os.path.join(stems_dir, "vocals.wav"), always_2d=True)

        if opt["mode"] == "voice":
            out = voc
            log("Using isolated voice only")
        else:
            bg, _ = sf.read(os.path.join(stems_dir, "no_vocals.wav"), always_2d=True)
            n = min(len(voc), len(bg))
            out = voc[:n] + bg[:n]
            log("Remixing boosted voice with background")

        finalize(out, sr, out_path, opt, log)
    finally:
        if tmp:
            tmp.cleanup()


# ----------------------------------------------------------------------
# Spectrum display (pure QPainter - no matplotlib)
# ----------------------------------------------------------------------

class SpectrumView(QWidget):
    F_MIN, F_MAX = 20.0, 20000.0
    DB_MIN, DB_MAX = -100.0, 0.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self._in = self._out = self._curve = None

    def set_input(self, f, d):  self._in = (f, d);    self.update()
    def set_output(self, f, d): self._out = (f, d);   self.update()
    def set_curve(self, f, d):  self._curve = (f, d); self.update()

    def _x(self, f, w):
        lo, hi = np.log10(self.F_MIN), np.log10(self.F_MAX)
        return (np.log10(np.clip(f, self.F_MIN, self.F_MAX)) - lo) / (hi - lo) * (w - 1)

    def _y(self, db, h):
        t = (np.clip(db, self.DB_MIN, self.DB_MAX) - self.DB_MIN) / (self.DB_MAX - self.DB_MIN)
        return (1.0 - t) * (h - 1)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(24, 26, 30))
        w, h = self.width(), self.height()
        font = p.font(); font.setPointSize(7); p.setFont(font)

        p.setPen(QPen(QColor(55, 58, 66), 1))
        for f in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000):
            x = int(self._x(f, w))
            p.drawLine(x, 0, x, h)
            label = "20k" if f == 20000 else (f"{f // 1000}k" if f >= 1000 else str(f))
            p.drawText(x + 2, h - 3, label)
        for db in range(self.DB_MIN + 20, self.DB_MAX, 20):
            y = int(self._y(db, h))
            p.drawLine(0, y, w, y)
            p.drawText(3, y - 2, f"{db}")

        def draw(series, color, width=1, style=Qt.SolidLine):
            if not series or series[0] is None:
                return
            freqs, db = series
            pts = QPolygonF()
            for f, d in zip(freqs, db):
                pts.append(QPointF(self._x(f, w), self._y(d, h)))
            if pts.count() < 2:
                return
            pen = QPen(QColor(color), width, style)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPolyline(pts)

        draw(self._in, "#39d353")
        draw(self._out, "#58a6ff")
        draw(self._curve, "#f0883e", 1, Qt.DashLine)
        p.end()


class SpectrumWorker(QThread):
    ready = Signal(str, str, object, object)   # tag, path, freqs-or-None, db-or-None

    def __init__(self, tag, path):
        super().__init__()
        self.tag, self.path = tag, path

    def run(self):
        try:
            x, sr = sf.read(self.path, always_2d=True, dtype="float32")
            mono = x.mean(axis=1)
            n = 8192
            if len(mono) <= n:
                padded = np.zeros(n, dtype=np.float32)
                padded[:len(mono)] = mono
                windows = [padded]
            else:
                hop = max((len(mono) - n) // 47, n)
                windows = [mono[i:i + n] for i in range(0, len(mono) - n + 1, hop)]
            win = np.hanning(n).astype(np.float32)
            acc = None
            for w in windows:
                mag = np.abs(np.fft.rfft(w * win))
                acc = mag if acc is None else acc + mag
            spec = acc / len(windows)
            freqs = np.fft.rfftfreq(n, 1.0 / sr)
            db = 20 * np.log10(np.maximum(spec, 1e-12) / (n / 4))
            keep = (freqs >= SpectrumView.F_MIN) & (freqs <= min(sr / 2, SpectrumView.F_MAX))
            self.ready.emit(self.tag, self.path, freqs[keep], db[keep])
        except Exception:
            self.ready.emit(self.tag, self.path, None, None)


# ----------------------------------------------------------------------
# Background processing worker (single job or batch queue)
# ----------------------------------------------------------------------

class Worker(QThread):
    progress = Signal(str)
    percent = Signal(int)
    finished_ok = Signal(str)
    failed = Signal(str)
    file_done = Signal(str, bool)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def _batch_pct(self, base, span):
        def wrapped(v):
            self.percent.emit(base + int(v * span / 100.0))
        return wrapped

    def _run_one(self, inp, outp, opt):
        if opt["method"] == "demucs":
            process_demucs(inp, outp, opt, self.progress.emit)
        else:
            process_dsp(inp, outp, opt, self.progress.emit)

    def run(self):
        p = self.params
        try:
            jobs = p.get("batch")
            if jobs:
                total = len(jobs)
                ok_count = 0
                for i, (inp, outp) in enumerate(jobs):
                    base, span = 100.0 * i / total, 100.0 / total
                    one = dict(p)
                    one["input"], one["output"] = inp, outp
                    one.pop("batch", None)
                    one["_pct"] = self._batch_pct(base, span)
                    self.progress.emit(f"[{i + 1}/{total}] {os.path.basename(inp)}")
                    try:
                        self._run_one(inp, outp, one)
                        ok_count += 1
                        self.file_done.emit(outp, True)
                    except Exception as e:
                        self.progress.emit(f"  ERROR: {e}")
                        self.file_done.emit(outp, False)
                    self.percent.emit(int(base + span))
                self.finished_ok.emit(f"{ok_count}/{total} files processed")
            else:
                self._run_one(p["input"], p["output"], p)
                self.finished_ok.emit(p["output"])
        except Exception as e:
            self.failed.emit(str(e))


def ms_to_str(ms):
    s = int(max(ms, 0) // 1000)
    return f"{s // 60}:{s % 60:02d}"


def spin(lo, hi, val, step, suffix="", dec=1):
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(val)
    if suffix:
        w.setSuffix(suffix)
    return w


FOCUS_BLOCKERS = (QLineEdit, QPlainTextEdit, QDoubleSpinBox, QComboBox,
                  QListWidget, QPushButton, QCheckBox, QSlider)


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Voice Booster")
        self.resize(740, 860)
        self.setAcceptDrops(True)
        self.worker = None
        self._spec_worker = None
        self._spec_keepalive = []
        self._spec_cache = {}
        self._batching = False
        self._batch_failed = []
        self._previewing = False
        self._pending_seek = None
        self.preview_info = None

        self.settings = QSettings("VoiceBooster", "VoiceBooster")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ================= Files + format =================
        file_box = QGroupBox("Files & export format (drag & drop audio anywhere)")
        fl = QFormLayout(file_box)

        in_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Input audio file (wav/mp3/flac/ogg/m4a)...")
        self.input_edit.textChanged.connect(self.load_play_source)
        btn_in = QPushButton("Browse...")
        btn_in.setToolTip("Ctrl+O")
        btn_in.clicked.connect(self.browse_input)
        in_row.addWidget(self.input_edit)
        in_row.addWidget(btn_in)
        fl.addRow("Input:", in_row)

        out_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output file (auto-filled)...")
        btn_out = QPushButton("Browse...")
        btn_out.clicked.connect(self.browse_output)
        out_row.addWidget(self.output_edit)
        out_row.addWidget(btn_out)
        fl.addRow("Output:", out_row)

        fmt_row = QHBoxLayout()
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("WAV", "wav")
        self.fmt_combo.addItem("MP3", "mp3")
        self.fmt_combo.currentIndexChanged.connect(self.fmt_changed)
        self.br_combo = QComboBox()
        for br in (128, 192, 256, 320):
            self.br_combo.addItem(f"{br} kbps", br)
        self.br_combo.setCurrentIndex(1)
        self.br_combo.setEnabled(False)
        self.ffmpeg_status = QLabel()
        fmt_row.addWidget(QLabel("Format:"))
        fmt_row.addWidget(self.fmt_combo)
        fmt_row.addWidget(QLabel("Bitrate:"))
        fmt_row.addWidget(self.br_combo)
        fmt_row.addStretch(1)
        fmt_row.addWidget(self.ffmpeg_status)
        fl.addRow("", fmt_row)
        layout.addWidget(file_box)
        self.check_ffmpeg()

        # ================= Batch queue =================
        g_batch = QGroupBox("Batch queue (optional) - each file is processed "
                            "with the settings below")
        bl = QVBoxLayout(g_batch)
        self.queue_list = QListWidget()
        self.queue_list.setToolTip("Drop several files at once, or hold Shift "
                                   "while dropping one file, to fill the queue")
        bl.addWidget(self.queue_list)
        br1 = QHBoxLayout()
        b = QPushButton("Add files..."); b.clicked.connect(self.batch_add)
        br1.addWidget(b)
        b = QPushButton("Remove selected"); b.clicked.connect(self.batch_remove)
        br1.addWidget(b)
        b = QPushButton("Clear"); b.clicked.connect(self.batch_clear)
        br1.addWidget(b)
        br1.addStretch(1)
        self.batch_run_btn = QPushButton("Process Queue ▶")
        self.batch_run_btn.setToolTip("Ctrl+Shift+R")
        self.batch_run_btn.clicked.connect(self.start_batch)
        br1.addWidget(self.batch_run_btn)
        bl.addLayout(br1)
        layout.addWidget(g_batch)

        # ================= Preview player =================
        pb_box = QGroupBox("Preview")
        pl = QVBoxLayout(pb_box)

        row1 = QHBoxLayout()
        self.play_src = QComboBox()
        self.play_src.addItem("Input file", "input")
        self.play_src.addItem("Output file", "output")
        self.play_src.currentIndexChanged.connect(lambda _: self.load_play_source())
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_playback)
        self.ab_btn = QPushButton("A/B swap")
        self.ab_btn.setEnabled(False)
        self.ab_btn.setToolTip("Swap Input/Output at the same playback position")
        self.ab_btn.clicked.connect(self.ab_swap)
        row1.addWidget(QLabel("Source:"))
        row1.addWidget(self.play_src)
        row1.addWidget(self.play_btn)
        row1.addWidget(self.stop_btn)
        row1.addWidget(self.ab_btn)
        row1.addStretch(1)
        pl.addLayout(row1)

        row1b = QHBoxLayout()
        self.preview_btn = QPushButton("⚡ Preview 15 s")
        self.preview_btn.setToolTip("Process a 15-second snippet starting at the "
                                    "playhead - fast way to audition settings (Ctrl+P)")
        self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self.start_preview)
        self.cmp_btn = QPushButton("Compare original")
        self.cmp_btn.setToolTip("Play the input from where the preview started")
        self.cmp_btn.setEnabled(False)
        self.cmp_btn.clicked.connect(self.play_original_snippet)
        row1b.addWidget(self.preview_btn)
        row1b.addWidget(self.cmp_btn)
        pl.addLayout(row1b)

        row2 = QHBoxLayout()
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderReleased.connect(
            lambda: self.player.setPosition(self.seek.value()))
        self.seek.sliderMoved.connect(
            lambda v: self.time_lbl.setText(
                f"{ms_to_str(v)} / {ms_to_str(self.player.duration())}"))
        self.time_lbl = QLabel("0:00 / 0:00")
        row2.addWidget(self.seek, 1)
        row2.addWidget(self.time_lbl)
        pl.addLayout(row2)
        layout.addWidget(pb_box)

        if HAS_MULTIMEDIA:
            self.audio_out = QAudioOutput()
            self.player = QMediaPlayer()
            self.player.setAudioOutput(self.audio_out)
            self.player.positionChanged.connect(self.on_position)
            self.player.durationChanged.connect(self.on_duration)
            self.player.playbackStateChanged.connect(self.on_playstate)
            self.player.mediaStatusChanged.connect(self.on_media_status)
            self.player.errorOccurred.connect(
                lambda err, s: self.log(f"Playback error: {s}"))
        else:
            self.player = None
            pb_box.setTitle("Preview (unavailable - QtMultimedia failed to load)")
            self.play_btn.setEnabled(False)

        # ================= Spectrum =================
        g_spec = QGroupBox("Spectrum: input (green) · output (blue) · "
                           "predicted EQ curve (orange, HPF+EQ only)")
        sl = QVBoxLayout(g_spec)
        self.spectrum = SpectrumView()
        self.spectrum.setFixedHeight(170)
        sl.addWidget(self.spectrum)
        layout.addWidget(g_spec)

        self._spec_timer = QTimer(self)
        self._spec_timer.setSingleShot(True)
        self._spec_timer.setInterval(350)
        self._spec_timer.timeout.connect(self.analyze_current_spectrum)

        # ================= Method =================
        method_box = QGroupBox("Method")
        ml = QVBoxLayout(method_box)

        self.method_combo = QComboBox()
        self.method_combo.addItem("AI Voice Isolation (Demucs)", "demucs")
        self.method_combo.addItem("DSP (Denoise + EQ + Compressor)", "dsp")
        self.method_combo.currentIndexChanged.connect(
            lambda i: self.stack.setCurrentIndex(i))
        ml.addWidget(self.method_combo)

        self.demucs_status = QLabel()
        ml.addWidget(self.demucs_status)
        self.check_demucs()

        self.stack = QStackedWidget()
        ml.addWidget(self.stack)

        # --- Page 0: Demucs options ---
        page_demucs = QWidget()
        form_d = QFormLayout(page_demucs)
        self.d_mode = QComboBox()
        self.d_mode.addItem("Boosted mix (voice + background)", "mix")
        self.d_mode.addItem("Isolated voice only", "voice")
        form_d.addRow("Output:", self.d_mode)
        hint = QLabel("AI model truly separates voice from everything else. "
                      "Slower, best quality.")
        hint.setWordWrap(True)
        form_d.addRow(hint)
        self.stack.addWidget(page_demucs)

        # --- Page 1: DSP options ---
        page_dsp = QWidget()
        v = QVBoxLayout(page_dsp)
        v.setContentsMargins(0, 0, 0, 0)

        g_nr = QGroupBox("Noise reduction")
        f1 = QFormLayout(g_nr)
        self.s_denoise = QCheckBox("Enable spectral noise reduction")
        self.s_denoise.setChecked(True)
        f1.addRow(self.s_denoise)
        self.s_den_mode = QComboBox()
        self.s_den_mode.addItem("Non-stationary (varying noise)", False)
        self.s_den_mode.addItem("Stationary (constant hiss/hum)", True)
        f1.addRow("Noise type:", self.s_den_mode)
        self.s_den_amt = spin(0.0, 1.0, 0.80, 0.05, "", 2)
        f1.addRow("Strength:", self.s_den_amt)
        v.addWidget(g_nr)

        g_eq = QGroupBox("EQ / filters")
        f2 = QFormLayout(g_eq)

        self.s_hpf = QCheckBox(); self.s_hpf.setChecked(True)
        self.s_hpf_freq = spin(20, 300, 100, 5, " Hz", 0)
        self.s_hpf_slope = QComboBox()
        for s in (12, 24, 48):
            self.s_hpf_slope.addItem(f"{s} dB/oct", s)
        self.s_hpf_slope.setCurrentIndex(2)
        r = QHBoxLayout()
        r.addWidget(self.s_hpf); r.addWidget(self.s_hpf_freq)
        r.addWidget(QLabel("Slope:")); r.addWidget(self.s_hpf_slope)
        r.addStretch(1)
        f2.addRow("High-pass:", r)

        self.s_mud = QCheckBox(); self.s_mud.setChecked(True)
        self.s_mud_freq = spin(100, 600, 300, 10, " Hz", 0)
        self.s_mud_gain = spin(-12.0, 0.0, -3.0, 0.5, " dB", 1)
        self.s_mud_q = spin(0.3, 5.0, 1.0, 0.1, "", 2)
        r = QHBoxLayout()
        r.addWidget(self.s_mud); r.addWidget(self.s_mud_freq)
        r.addWidget(QLabel("Gain:")); r.addWidget(self.s_mud_gain)
        r.addWidget(QLabel("Q:")); r.addWidget(self.s_mud_q)
        r.addStretch(1)
        f2.addRow("Mud cut:", r)

        self.s_pres = QCheckBox(); self.s_pres.setChecked(True)
        self.s_pres_freq = spin(1500, 8000, 3200, 100, " Hz", 0)
        self.s_pres_gain = spin(0.0, 12.0, 4.0, 0.5, " dB", 1)
        self.s_pres_q = spin(0.3, 5.0, 0.8, 0.1, "", 2)
        r = QHBoxLayout()
        r.addWidget(self.s_pres); r.addWidget(self.s_pres_freq)
        r.addWidget(QLabel("Gain:")); r.addWidget(self.s_pres_gain)
        r.addWidget(QLabel("Q:")); r.addWidget(self.s_pres_q)
        r.addStretch(1)
        f2.addRow("Presence:", r)
        v.addWidget(g_eq)

        g_ds = QGroupBox("De-esser (tames harsh \"s\" sounds)")
        f3 = QFormLayout(g_ds)
        self.s_deess = QCheckBox("Enable")
        self.s_deess_freq = spin(4000, 9000, 6500, 100, " Hz", 0)
        self.s_deess_thr = spin(-60.0, 0.0, -35.0, 1.0, " dB", 0)
        self.s_deess_amt = spin(0.0, 18.0, 8.0, 1.0, " dB", 0)
        f3.addRow(self.s_deess)
        f3.addRow("Frequency:", self.s_deess_freq)
        f3.addRow("Threshold:", self.s_deess_thr)
        f3.addRow("Max reduction:", self.s_deess_amt)
        v.addWidget(g_ds)

        g_cp = QGroupBox("Compressor")
        f4 = QFormLayout(g_cp)
        self.s_comp = QCheckBox("Enable"); self.s_comp.setChecked(True)
        f4.addRow(self.s_comp)
        self.s_comp_thr = spin(-60.0, 0.0, -20.0, 1.0, " dB", 0)
        self.s_comp_ratio = spin(1.0, 20.0, 3.0, 0.5, " :1", 1)
        self.s_comp_atk = spin(0.5, 200.0, 30.0, 5.0, " ms", 1)
        self.s_comp_rel = spin(10.0, 1000.0, 150.0, 10.0, " ms", 0)
        self.s_comp_knee = spin(0.0, 24.0, 6.0, 1.0, " dB", 0)
        self.s_comp_makeup = spin(0.0, 24.0, 6.0, 0.5, " dB", 1)
        f4.addRow("Threshold:", self.s_comp_thr)
        f4.addRow("Ratio:", self.s_comp_ratio)
        r = QHBoxLayout()
        r.addWidget(QLabel("Attack:")); r.addWidget(self.s_comp_atk)
        r.addWidget(QLabel("Release:")); r.addWidget(self.s_comp_rel)
        f4.addRow(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("Knee:")); r.addWidget(self.s_comp_knee)
        r.addWidget(QLabel("Makeup:")); r.addWidget(self.s_comp_makeup)
        f4.addRow(r)
        v.addWidget(g_cp)

        v.addStretch(1)
        self.stack.addWidget(page_dsp)
        layout.addWidget(method_box)

        # ================= Level (shared by both methods) =================
        g_lvl = QGroupBox("Level (applies to both methods)")
        f5 = QFormLayout(g_lvl)
        self.boost = spin(-24.0, 24.0, 4.0, 0.5, " dB", 1)
        f5.addRow("Voice boost:", self.boost)

        r = QHBoxLayout()
        self.norm = QCheckBox("Normalize")
        self.norm.setChecked(True)
        self.norm_mode = QComboBox()
        self.norm_mode.addItem("RMS", "rms")
        self.norm_mode.addItem("Peak", "peak")
        self.norm_mode.addItem("Loudness (LUFS)", "lufs")
        self.norm_target = spin(-30.0, -6.0, -16.0, 0.5, " dBFS", 1)
        self.norm_max = spin(0.0, 24.0, 12.0, 1.0, " dB", 0)
        r.addWidget(self.norm); r.addWidget(self.norm_mode)
        r.addWidget(QLabel("to")); r.addWidget(self.norm_target)
        r.addWidget(QLabel("max gain")); r.addWidget(self.norm_max)
        r.addStretch(1)
        f5.addRow(r)
        layout.addWidget(g_lvl)

        # ================= Settings import/export =================
        set_row = QHBoxLayout()
        set_row.addWidget(QLabel("Settings:"))
        btn_export = QPushButton("Export as JSON...")
        btn_export.clicked.connect(self.export_settings)
        btn_import = QPushButton("Import JSON...")
        btn_import.clicked.connect(self.import_settings)
        set_row.addWidget(btn_export)
        set_row.addWidget(btn_import)
        set_row.addStretch(1)
        self.reset_btn = QPushButton("↺ Defaults")
        self.reset_btn.clicked.connect(self.reset_defaults)
        set_row.addWidget(self.reset_btn)
        layout.addLayout(set_row)

        # ================= Run =================
        run_row = QHBoxLayout()
        self.run_btn = QPushButton("Process")
        self.run_btn.setMinimumHeight(34)
        self.run_btn.setToolTip("Ctrl+R")
        self.run_btn.clicked.connect(self.start)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        run_row.addWidget(self.run_btn, 1)
        run_row.addWidget(self.progress)
        layout.addLayout(run_row)

        # ================= Log =================
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(140)
        layout.addWidget(self.log_box, 1)

        # ================= Keyboard shortcuts =================
        sc = QShortcut(QKeySequence(Qt.Key_Space), self)
        sc.setContext(Qt.WindowShortcut)
        sc.activated.connect(self.toggle_play_guarded)
        self.run_btn.setShortcut("Ctrl+R")
        self.preview_btn.setShortcut("Ctrl+P")
        self.batch_run_btn.setShortcut("Ctrl+Shift+R")
        btn_in.setShortcut("Ctrl+O")

        # ================= Widget registry / persistence =================
        self._widgets = {
            "files/input": self.input_edit,
            "files/output": self.output_edit,
            "fmt": self.fmt_combo,
            "bitrate": self.br_combo,
            "method": self.method_combo,
            "demucs/mode": self.d_mode,
            "level/boost": self.boost,
            "level/norm": self.norm,
            "level/norm_mode": self.norm_mode,
            "level/norm_target": self.norm_target,
            "level/norm_max": self.norm_max,
            "nr/en": self.s_denoise,
            "nr/mode": self.s_den_mode,
            "nr/amt": self.s_den_amt,
            "eq/hpf_en": self.s_hpf,
            "eq/hpf_freq": self.s_hpf_freq,
            "eq/hpf_slope": self.s_hpf_slope,
            "eq/mud_en": self.s_mud,
            "eq/mud_freq": self.s_mud_freq,
            "eq/mud_gain": self.s_mud_gain,
            "eq/mud_q": self.s_mud_q,
            "eq/pres_en": self.s_pres,
            "eq/pres_freq": self.s_pres_freq,
            "eq/pres_gain": self.s_pres_gain,
            "eq/pres_q": self.s_pres_q,
            "ds/en": self.s_deess,
            "ds/freq": self.s_deess_freq,
            "ds/thr": self.s_deess_thr,
            "ds/amt": self.s_deess_amt,
            "cp/en": self.s_comp,
            "cp/thr": self.s_comp_thr,
            "cp/ratio": self.s_comp_ratio,
            "cp/atk": self.s_comp_atk,
            "cp/rel": self.s_comp_rel,
            "cp/knee": self.s_comp_knee,
            "cp/makeup": self.s_comp_makeup,
        }
        self._defaults = {k: self._get(w) for k, w in self._widgets.items()
                          if not k.startswith("files/")}

        for w in (self.s_hpf, self.s_hpf_freq, self.s_hpf_slope,
                  self.s_mud, self.s_mud_freq, self.s_mud_gain, self.s_mud_q,
                  self.s_pres, self.s_pres_freq, self.s_pres_gain, self.s_pres_q):
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self.update_eq_curve)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self.update_eq_curve)
            else:
                w.valueChanged.connect(self.update_eq_curve)

        self.load_settings()
        geo = self.settings.value("ui/geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        self.update_eq_curve()

        self.log("Ready. Load audio (or drop files), tweak the options, hit Process. "
                 "Shortcuts: Space play/pause · Ctrl+P preview · Ctrl+R process · "
                 "Ctrl+O open · Ctrl+Shift+R batch.")

    # ------------------------------------------------------------------
    # Status checks
    # ------------------------------------------------------------------

    def check_demucs(self):
        if shutil.which("demucs"):
            self.demucs_status.setText("demucs: found")
            self.demucs_status.setStyleSheet("color: green;")
        else:
            self.demucs_status.setText("demucs: NOT installed "
                                       "(pip install demucs to enable)")
            self.demucs_status.setStyleSheet("color: red;")

    def check_ffmpeg(self):
        if shutil.which("ffmpeg"):
            self.ffmpeg_status.setText("ffmpeg: found")
            self.ffmpeg_status.setStyleSheet("color: green;")
        else:
            self.ffmpeg_status.setText("ffmpeg: not found "
                                       "(MP3/non-WAV input will be unavailable)")
            self.ffmpeg_status.setStyleSheet("color: orange;")

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _get(w):
        if isinstance(w, QDoubleSpinBox):
            return w.value()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return w.currentIndex()
        if isinstance(w, QLineEdit):
            return w.text()
        return None

    @staticmethod
    def _set(w, v):
        if v is None:
            return
        try:
            if isinstance(w, QDoubleSpinBox):
                w.setValue(float(v))
            elif isinstance(w, QCheckBox):
                w.setChecked(v if isinstance(v, bool) else str(v).lower() in ("true", "1"))
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(int(v))
            elif isinstance(w, QLineEdit):
                w.setText(str(v))
        except (TypeError, ValueError):
            pass

    def load_settings(self):
        for k, w in self._widgets.items():
            self._set(w, self.settings.value(k))

    def save_settings(self):
        for k, w in self._widgets.items():
            self.settings.setValue(k, self._get(w))
        self.settings.setValue("ui/geometry", self.saveGeometry())

    def reset_defaults(self):
        for k, v in self._defaults.items():
            self._set(self._widgets[k], v)
        self.log("All processing settings reset to defaults.")

    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export settings",
                                              "voiceboost_settings.json",
                                              "JSON (*.json)")
        if not path:
            return
        data = {k: self._get(w) for k, w in self._widgets.items()
                if not k.startswith("files/")}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.log(f"Settings exported: {path}")
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "",
                                              "JSON (*.json)")
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import failed", f"Could not read JSON: {e}")
            return
        n = 0
        for k, val in data.items():
            if k in self._widgets and not k.startswith("files/"):
                self._set(self._widgets[k], val)
                n += 1
        self.log(f"Settings imported: {n} values applied "
                 f"from {os.path.basename(path)}")

    # ------------------------------------------------------------------
    # Format / file helpers
    # ------------------------------------------------------------------

    def fmt_changed(self, _idx):
        is_mp3 = self.fmt_combo.currentData() == "mp3"
        self.br_combo.setEnabled(is_mp3)
        self.fix_out_ext()

    def fix_out_ext(self):
        want = ".mp3" if self.fmt_combo.currentData() == "mp3" else ".wav"
        path = self.output_edit.text().strip()
        if not path:
            return
        base, ext = os.path.splitext(path)
        if ext.lower() in (".wav", ".mp3") and ext.lower() != want:
            self.output_edit.setText(base + want)

    def browse_input(self):
        filt = ("Audio files (*.wav *.aif *.aiff *.mp3 *.flac *.ogg *.oga "
                "*.m4a *.opus *.wma);;All files (*)")
        path, _ = QFileDialog.getOpenFileName(self, "Select input audio", "", filt)
        if path:
            self.input_edit.setText(path)
            self.output_edit.setText(auto_out_path(path, self.fmt_combo.currentData()))

    def browse_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save output",
                                              self.output_edit.text() or "",
                                              "Audio (*.wav *.mp3)")
        if path:
            self.output_edit.setText(path)

    def log(self, msg):
        self.log_box.appendPlainText(msg)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith(INPUT_EXTS)]
        if not paths:
            return
        if len(paths) > 1 or (e.modifiers() & Qt.ShiftModifier):
            self.batch_add_paths(paths)
            return
        path = paths[0]
        self.input_edit.setText(path)
        self.output_edit.setText(auto_out_path(path, self.fmt_combo.currentData()))

    # ------------------------------------------------------------------
    # Spectrum
    # ------------------------------------------------------------------

    def _current_path_for(self, tag):
        return (self.input_edit.text().strip() if tag == "in"
                else self.output_edit.text().strip())

    def analyze_current_spectrum(self):
        tag = "in" if self.play_src.currentData() == "input" else "out"
        path = self._current_path_for(tag)
        setter = self.spectrum.set_input if tag == "in" else self.spectrum.set_output
        if not (path and os.path.isfile(path)):
            setter(None, None)
            return
        abs_p = os.path.abspath(path)
        if abs_p in self._spec_cache:
            freqs, db = self._spec_cache[abs_p]
            setter(freqs, db)
            return
        if len(self._spec_cache) > 8:
            self._spec_cache.clear()
        old = self._spec_worker
        if old is not None and old.isRunning():
            try:
                old.ready.disconnect(self.on_spectrum_ready)
            except Exception:
                pass
            self._spec_keepalive.append(old)
            self._spec_keepalive = [t for t in self._spec_keepalive if t.isRunning()]
        self._spec_worker = SpectrumWorker(tag, path)
        self._spec_worker.ready.connect(self.on_spectrum_ready)
        self._spec_worker.start()

    def on_spectrum_ready(self, tag, path, freqs, db):
        if freqs is None:
            return
        try:
            same = os.path.abspath(path) == os.path.abspath(self._current_path_for(tag))
        except Exception:
            same = False
        if not same:
            return
        self._spec_cache[os.path.abspath(path)] = (freqs, db)
        (self.spectrum.set_input if tag == "in"
         else self.spectrum.set_output)(freqs, db)

    def update_eq_curve(self, *_):
        if not hasattr(self, "spectrum"):
            return
        freqs, db = predicted_eq_response(self.collect_params())
        self.spectrum.set_curve(freqs, db)

    # ------------------------------------------------------------------
    # Batch queue
    # ------------------------------------------------------------------

    def batch_add(self):
        filt = ("Audio files (*.wav *.aif *.aiff *.mp3 *.flac *.ogg *.oga "
                "*.m4a *.opus *.wma);;All files (*)")
        paths, _ = QFileDialog.getOpenFileNames(self, "Add audio files", "", filt)
        self.batch_add_paths(paths)

    def batch_add_paths(self, paths):
        have = {self.queue_list.item(i).text()
                for i in range(self.queue_list.count())}
        added = 0
        for p in paths:
            if p and os.path.isfile(p) and p not in have:
                self.queue_list.addItem(p)
                have.add(p)
                added += 1
        if added:
            self.log(f"Queue: +{added} file(s), {self.queue_list.count()} total")

    def batch_remove(self):
        for item in self.queue_list.selectedItems():
            self.queue_list.takeItem(self.queue_list.row(item))

    def batch_clear(self):
        self.queue_list.clear()

    def start_batch(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "A job is already running.")
            return
        if self.queue_list.count() == 0:
            QMessageBox.information(self, "Batch", "Queue is empty - add files first.")
            return
        if (self.method_combo.currentData() == "demucs"
                and shutil.which("demucs") is None):
            QMessageBox.warning(self, "Demucs missing",
                                "demucs is not installed. Install with: pip install demucs")
            return
        fmt = self.fmt_combo.currentData()
        jobs = [(self.queue_list.item(i).text(),
                 auto_out_path(self.queue_list.item(i).text(), fmt))
                for i in range(self.queue_list.count())]
        existing = [o for _, o in jobs if os.path.isfile(o)]
        if existing:
            r = QMessageBox.question(
                self, "Overwrite?",
                f"{len(existing)} output file(s) already exist and will be "
                f"overwritten.\nContinue?", QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                return

        self._batching = True
        self._batch_failed = []
        self.run_btn.setEnabled(False)
        self.batch_run_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log("=" * 60)
        self.log(f"Batch: {len(jobs)} file(s), method={self.method_combo.currentData()}, "
                 f"format={fmt}")
        params = self.collect_params()
        params["batch"] = jobs
        self._launch_worker(params)

    def on_file_done(self, path, ok):
        if not ok:
            self._batch_failed.append(path)
        self.log(("OK: " if ok else "FAILED: ") + os.path.basename(path))

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def load_play_source(self):
        if not getattr(self, "player", None):
            return
        key = self.play_src.currentData()
        path = (self.input_edit.text().strip() if key == "input"
                else self.output_edit.text().strip())
        if path and os.path.isfile(path):
            self.player.setSource(QUrl.fromLocalFile(path))
            self.play_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
        else:
            self.player.setSource(QUrl())
            self.play_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.seek.setRange(0, 0)
            self.time_lbl.setText("0:00 / 0:00")
        if hasattr(self, "ab_btn"):
            in_ok = os.path.isfile(self.input_edit.text().strip())
            out_ok = os.path.isfile(self.output_edit.text().strip())
            self.ab_btn.setEnabled(in_ok and out_ok)
        if hasattr(self, "preview_btn"):
            self.preview_btn.setEnabled(os.path.isfile(self.input_edit.text().strip()))
        if hasattr(self, "_spec_timer"):
            self._spec_timer.start()

    def toggle_play_guarded(self):
        if isinstance(self.focusWidget(), FOCUS_BLOCKERS):
            return
        self.toggle_play()

    def toggle_play(self):
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop_playback(self):
        if not self.player:
            return
        self.player.stop()
        self.player.setPosition(0)

    def ab_swap(self):
        if not self.player:
            return
        other = 0 if self.play_src.currentIndex() == 1 else 1
        path = (self.input_edit.text().strip() if other == 0
                else self.output_edit.text().strip())
        if not (path and os.path.isfile(path)):
            self.log("A/B: that source isn't available yet.")
            return
        was_playing = self.player.playbackState() == QMediaPlayer.PlayingState
        pos = self.player.position()
        self.play_src.blockSignals(True)
        self.play_src.setCurrentIndex(other)
        self.play_src.blockSignals(False)
        self.load_play_source()
        self.player.setPosition(pos)
        if was_playing:
            self.player.play()
        self.log(f"A/B: now playing {'Input' if other == 0 else 'Output'} "
                 f"@ {ms_to_str(pos)}")

    def on_playstate(self, state):
        self.play_btn.setText("⏸ Pause" if state == QMediaPlayer.PlayingState
                              else "▶ Play")

    def on_media_status(self, status):
        if status == QMediaPlayer.LoadedMedia and self._pending_seek is not None:
            self.player.setPosition(self._pending_seek)
            self._pending_seek = None
        if status == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
            self.seek.setValue(0)

    def on_position(self, pos):
        if not self.seek.isSliderDown():
            self.seek.setValue(pos)
        self.time_lbl.setText(f"{ms_to_str(pos)} / {ms_to_str(self.player.duration())}")

    def on_duration(self, dur):
        self.seek.setRange(0, max(int(dur), 1))

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def collect_params(self):
        return {
            "input": self.input_edit.text().strip(),
            "output": self.output_edit.text().strip(),
            "method": self.method_combo.currentData(),
            "fmt": self.fmt_combo.currentData(),
            "bitrate": self.br_combo.currentData(),
            "boost": self.boost.value(),
            "normalize": self.norm.isChecked(),
            "norm_mode": self.norm_mode.currentData(),
            "norm_target": self.norm_target.value(),
            "norm_max": self.norm_max.value(),
            "mode": self.d_mode.currentData(),
            "denoise": self.s_denoise.isChecked(),
            "den_mode": self.s_den_mode.currentData(),
            "den_amt": self.s_den_amt.value(),
            "hpf": self.s_hpf.isChecked(),
            "hpf_freq": self.s_hpf_freq.value(),
            "hpf_slope": self.s_hpf_slope.currentData(),
            "mud": self.s_mud.isChecked(),
            "mud_freq": self.s_mud_freq.value(),
            "mud_gain": self.s_mud_gain.value(),
            "mud_q": self.s_mud_q.value(),
            "pres": self.s_pres.isChecked(),
            "pres_freq": self.s_pres_freq.value(),
            "pres_gain": self.s_pres_gain.value(),
            "pres_q": self.s_pres_q.value(),
            "deess": self.s_deess.isChecked(),
            "deess_freq": self.s_deess_freq.value(),
            "deess_thr": self.s_deess_thr.value(),
            "deess_amt": self.s_deess_amt.value(),
            "comp": self.s_comp.isChecked(),
            "comp_thr": self.s_comp_thr.value(),
            "comp_ratio": self.s_comp_ratio.value(),
            "comp_atk": self.s_comp_atk.value(),
            "comp_rel": self.s_comp_rel.value(),
            "comp_knee": self.s_comp_knee.value(),
            "comp_makeup": self.s_comp_makeup.value(),
        }

    def _launch_worker(self, params):
        if params.get("batch") or (params["method"] == "demucs"
                                   and shutil.which("demucs")
                                   and not params.get("snippet")):
            self.progress.setRange(0, 100)
        else:
            self.progress.setRange(0, 0)
        self.progress.setValue(0)
        self.worker = Worker(params)
        params["_pct"] = self.worker.percent.emit
        self.worker.progress.connect(self.log)
        self.worker.percent.connect(self.progress.setValue)
        self.worker.file_done.connect(self.on_file_done)
        self.worker.finished_ok.connect(self.on_done)
        self.worker.failed.connect(self.on_error)
        self.worker.start()

    def start(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "A job is already running.")
            return
        if (self.method_combo.currentData() == "demucs"
                and shutil.which("demucs") is None):
            QMessageBox.warning(self, "Demucs missing",
                                "demucs is not installed. Install with: pip install demucs")
            return
        self.fix_out_ext()
        in_path = self.input_edit.text().strip()
        out_path = self.output_edit.text().strip()
        if not in_path or not os.path.isfile(in_path):
            QMessageBox.warning(self, "Error", "Please select a valid input file.")
            return
        if not out_path:
            QMessageBox.warning(self, "Error", "Please set an output file path.")
            return
        self._previewing = False
        self._batching = False
        self.run_btn.setEnabled(False)
        self.batch_run_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log("=" * 60)
        self._launch_worker(self.collect_params())

    def start_preview(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "A job is already running.")
            return
        in_path = self.input_edit.text().strip()
        if not in_path or not os.path.isfile(in_path):
            QMessageBox.warning(self, "Error", "Load a valid input file first.")
            return
        start_ms = self.player.position() if self.player else 0
        start_sec = start_ms / 1000.0
        base = os.path.splitext(self.output_edit.text().strip() or in_path)[0]
        preview_path = base + "_preview.wav"

        params = self.collect_params()
        params["output"] = preview_path
        params["fmt"] = "wav"
        params["snippet"] = (start_sec, 15.0)

        self._previewing = True
        self.preview_info = {"start_ms": int(start_ms), "path": preview_path}
        self.run_btn.setEnabled(False)
        self.batch_run_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log("=" * 60)
        self.log(f"Preview: processing {start_sec:.1f}s - {start_sec + 15.0:.1f}s "
                 f"-> {os.path.basename(preview_path)}")
        self._launch_worker(params)

    def play_original_snippet(self):
        if not (self.player and self.preview_info):
            return
        in_path = self.input_edit.text().strip()
        if in_path and os.path.isfile(in_path):
            self._pending_seek = self.preview_info["start_ms"]
            self.player.setSource(QUrl.fromLocalFile(in_path))
            self.player.play()
            self.log(f"Playing original from {ms_to_str(self.preview_info['start_ms'])}")

    def on_done(self, result):
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.run_btn.setEnabled(True)
        self.batch_run_btn.setEnabled(True)
        self.preview_btn.setEnabled(os.path.isfile(self.input_edit.text().strip()))

        if self._previewing:
            self._previewing = False
            self.cmp_btn.setEnabled(True)
            self.log("Preview done - playing processed snippet "
                     "('Compare original' toggles the unprocessed version).")
            if self.player:
                self.player.setSource(QUrl.fromLocalFile(result))
                self.player.play()
            return

        if self._batching:
            self._batching = False
            failed = set(self._batch_failed)
            for i in range(self.queue_list.count() - 1, -1, -1):
                if self.queue_list.item(i).text() not in failed:
                    self.queue_list.takeItem(i)   # keep only failures for retry
            self.log("Batch complete.")
            QMessageBox.information(self, "Batch finished", result)
            return

        self.log("Done.")
        try:
            self._spec_cache.pop(os.path.abspath(result), None)  # stale output cache
        except Exception:
            pass
        self.play_src.blockSignals(True)
        self.play_src.setCurrentIndex(1)
        self.play_src.blockSignals(False)
        self.load_play_source()
        QMessageBox.information(self, "Finished", f"Saved output to:\n{result}")

    def on_error(self, msg):
        self.progress.setVisible(False)
        self.progress.setRange(0, 100)
        self.run_btn.setEnabled(True)
        self.batch_run_btn.setEnabled(True)
        self.preview_btn.setEnabled(os.path.isfile(self.input_edit.text().strip()))
        self._previewing = False
        self._batching = False
        self.log(f"ERROR: {msg}")
        QMessageBox.critical(self, "Processing failed", msg)

    def closeEvent(self, e):
        self.save_settings()
        if self.player:
            self.player.stop()
        threads = [self.worker, self._spec_worker] + list(self._spec_keepalive)
        for t in threads:
            if t and t.isRunning():
                t.terminate()
                t.wait(2000)
        e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())