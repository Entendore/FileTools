import os
import sys
import re
import json
import shutil
import tempfile
import subprocess
import traceback
import logging
import time
import glob as glob_module
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt, lfilter, freqz
from scipy.signal import freqz_sos as sosfreqz

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSettings, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QPolygonF, QShortcut, QKeySequence

try:
    from PySide6.QtCore import QPointF
except ImportError:
    from PySide6.QtGui import QPointF

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QComboBox, QDoubleSpinBox,
    QCheckBox, QProgressBar, QPlainTextEdit, QFileDialog, QMessageBox,
    QStackedWidget, QSlider, QListWidget, QScrollArea, QFrame, QTabWidget,
    QInputDialog,
)

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    HAS_MULTIMEDIA = True
except Exception:
    HAS_MULTIMEDIA = False

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
LOG_FILE = "voicebooster_session.log"
logging.basicConfig(
    filename=LOG_FILE, level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s', filemode='w')
logging.info("Voice Booster application started.")

INPUT_EXTS = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".oga",
              ".m4a", ".wma", ".opus")

LARGE_FILE_THRESHOLD_SEC = 600
CHUNK_DURATION_SEC = 60
CHUNK_OVERLAP_SEC = 2
MAX_RECENT_FILES = 15

TMP_DIR = os.path.join(os.getcwd(), "tmp")
os.makedirs(TMP_DIR, exist_ok=True)
logging.info(f"Using temp directory: {TMP_DIR}")

# ----------------------------------------------------------------------
# AI Model Cache - Force all model downloads to E:\cacheAI
# ----------------------------------------------------------------------
AI_CACHE_DIR = r"E:\cacheAI"
os.makedirs(AI_CACHE_DIR, exist_ok=True)
os.environ["TORCH_HOME"] = AI_CACHE_DIR
os.environ["HF_HOME"] = os.path.join(AI_CACHE_DIR, "huggingface")
os.environ["HF_HUB_CACHE"] = os.path.join(AI_CACHE_DIR, "huggingface", "hub")
os.makedirs(os.environ["HF_HOME"], exist_ok=True)
os.makedirs(os.environ["HF_HUB_CACHE"], exist_ok=True)
logging.info(f"AI model cache: {AI_CACHE_DIR}")

DEMUCS_MODELS = {
    "htdemucs": "Hybrid Transformer (Default, Fast)",
    "htdemucs_ft": "Hybrid Transformer Fine-Tuned (Best Quality)",
    "htdemucs_6s": "Hybrid Transformer 6-Stem",
    "hdemucs_mmi": "Hybrid Demucs Multi-Mask Instrumental",
    "htcondemucs": "Conditional Hybrid Transformer",
}

# ----------------------------------------------------------------------
# Dark Theme (Catppuccin Mocha inspired)
# ----------------------------------------------------------------------
DARK_THEME = """
QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; }
QGroupBox { background-color: #313244; border: 1px solid #45475a; border-radius: 6px; margin-top: 12px; padding: 16px 8px 8px 8px; font-weight: bold; color: #89b4fa; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QTabWidget::pane { border: 1px solid #45475a; border-radius: 4px; background-color: #1e1e2e; }
QTabBar::tab { background-color: #313244; color: #cdd6f4; padding: 8px 16px; border: 1px solid #45475a; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }
QTabBar::tab:selected { background-color: #45475a; color: #89b4fa; font-weight: bold; }
QTabBar::tab:hover { background-color: #585b70; }
QPushButton { background-color: #45475a; color: #cdd6f4; border: 1px solid #585b70; border-radius: 4px; padding: 6px 12px; font-weight: 500; }
QPushButton:hover { background-color: #585b70; border-color: #89b4fa; }
QPushButton:pressed { background-color: #313244; }
QPushButton:disabled { background-color: #313244; color: #6c7086; border-color: #45475a; }
QLineEdit, QPlainTextEdit { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px; selection-background-color: #89b4fa; selection-color: #1e1e2e; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #89b4fa; }
QComboBox { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px; }
QComboBox:hover { border-color: #89b4fa; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 6px solid #cdd6f4; margin-right: 6px; }
QComboBox QAbstractItemView { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; selection-background-color: #89b4fa; selection-color: #1e1e2e; }
QDoubleSpinBox { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 4px; }
QDoubleSpinBox:hover { border-color: #89b4fa; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { background-color: #45475a; border: none; width: 16px; }
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background-color: #585b70; }
QCheckBox { color: #cdd6f4; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #45475a; border-radius: 3px; background-color: #313244; }
QCheckBox::indicator:checked { background-color: #89b4fa; border-color: #89b4fa; }
QSlider::groove:horizontal { border: 1px solid #45475a; height: 6px; background: #313244; border-radius: 3px; }
QSlider::handle:horizontal { background: #89b4fa; border: 1px solid #89b4fa; width: 16px; margin: -6px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background: #b4befe; border-color: #b4befe; }
QProgressBar { border: 1px solid #45475a; border-radius: 4px; text-align: center; color: #cdd6f4; background-color: #313244; }
QProgressBar::chunk { background-color: #89b4fa; border-radius: 3px; }
QListWidget { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; }
QListWidget::item { padding: 4px; }
QListWidget::item:selected { background-color: #89b4fa; color: #1e1e2e; }
QListWidget::item:hover { background-color: #45475a; }
QLabel { color: #cdd6f4; }
QScrollArea { border: none; background-color: transparent; }
QScrollBar:vertical { background-color: #1e1e2e; width: 12px; border: none; }
QScrollBar::handle:vertical { background-color: #45475a; border-radius: 6px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #585b70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar:horizontal { background-color: #1e1e2e; height: 12px; border: none; }
QScrollBar::handle:horizontal { background-color: #45475a; border-radius: 6px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #585b70; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
"""


# ----------------------------------------------------------------------
# Smart path parsing utilities
# ----------------------------------------------------------------------

def _clean_single_path(raw):
    """Clean a single path string, handling quotes, URLs, BOM, etc."""
    if not raw:
        return None
    s = raw.strip().strip("\ufeff").strip()
    if not s:
        return None
    if s.lower().startswith("file:///"):
        s = s[8:]
        if len(s) >= 2 and s[0] == "/" and s[2] == ":":
            s = s[1:]
    elif s.lower().startswith("file://"):
        s = s[7:]
    quote_chars = "\"'`´“”‘’„‚«»‹›"
    while len(s) >= 2 and s[0] in quote_chars and s[-1] in quote_chars:
        s = s[1:-1].strip()
    s = re.sub(r':\d+$', '', s).strip()
    s = re.sub(r'\s*\(\d+\)\s*$', '', s).strip()
    s = os.path.normpath(s)
    return s if s else None


def parse_path_list(text):
    """Parse a block of text into a list of cleaned paths."""
    if not text:
        return []
    text = text.replace("\ufeff", "")
    parts = re.split(r'[\r\n;|\t]+', text)
    results = []
    seen = set()
    for part in parts:
        cleaned = _clean_single_path(part)
        if cleaned and cleaned not in seen:
            results.append(cleaned)
            seen.add(cleaned)
    return results


def scan_folder(folder, recursive=True):
    """Scan a folder for audio files. Returns list of absolute paths."""
    found = []
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return found
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(INPUT_EXTS):
                    found.append(os.path.join(root, f))
    else:
        for f in os.listdir(folder):
            full = os.path.join(folder, f)
            if os.path.isfile(full) and f.lower().endswith(INPUT_EXTS):
                found.append(full)
    return sorted(found)


def expand_glob_pattern(pattern):
    """Expand a glob pattern to matching file paths."""
    pattern = pattern.strip().strip("\"'")
    if not pattern:
        return []
    pattern_norm = pattern.replace("\\", "/")
    if "**" in pattern_norm:
        matches = glob_module.glob(pattern_norm, recursive=True)
    else:
        matches = glob_module.glob(pattern_norm)
    return sorted([m for m in matches if os.path.isfile(m) and m.lower().endswith(INPUT_EXTS)])


# ----------------------------------------------------------------------
# DSP helpers (Memory-optimized to prevent float64 upcasting)
# ----------------------------------------------------------------------

def peaking_eq(x, sr, f0, q, gain_db):
    A = np.float32(10 ** (gain_db / 40))
    w0 = np.float32(2 * np.pi * f0 / sr)
    alpha = np.float32(np.sin(w0) / (2 * max(q, 0.1)))
    b = np.array([1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A], dtype=x.dtype)
    a = np.array([1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A], dtype=x.dtype)
    return lfilter(b / a[0], a / a[0], x, axis=0)


def low_shelf(x, sr, freq, gain_db, q):
    A = np.float32(10 ** (gain_db / 40))
    w0 = np.float32(2 * np.pi * freq / sr)
    alpha = np.float32(np.sin(w0) / (2 * max(q, 0.1)))
    sq = np.float32(2 * np.sqrt(A) * alpha)
    b0 = A * ((A + 1) - (A - 1) * np.cos(w0) + sq)
    b1 = 2 * A * ((A - 1) - (A + 1) * np.cos(w0))
    b2 = A * ((A + 1) - (A - 1) * np.cos(w0) - sq)
    a0 = (A + 1) + (A - 1) * np.cos(w0) + sq
    a1 = -2 * ((A - 1) + (A + 1) * np.cos(w0))
    a2 = (A + 1) + (A - 1) * np.cos(w0) - sq
    b = np.array([b0/a0, b1/a0, b2/a0], dtype=x.dtype)
    a = np.array([1.0, a1/a0, a2/a0], dtype=x.dtype)
    return lfilter(b, a, x, axis=0)


def high_shelf(x, sr, freq, gain_db, q):
    A = np.float32(10 ** (gain_db / 40))
    w0 = np.float32(2 * np.pi * freq / sr)
    alpha = np.float32(np.sin(w0) / (2 * max(q, 0.1)))
    sq = np.float32(2 * np.sqrt(A) * alpha)
    b0 = A * ((A + 1) + (A - 1) * np.cos(w0) + sq)
    b1 = -2 * A * ((A - 1) + (A + 1) * np.cos(w0))
    b2 = A * ((A + 1) + (A - 1) * np.cos(w0) - sq)
    a0 = (A + 1) - (A - 1) * np.cos(w0) + sq
    a1 = 2 * ((A - 1) - (A + 1) * np.cos(w0))
    a2 = (A + 1) - (A - 1) * np.cos(w0) - sq
    b = np.array([b0/a0, b1/a0, b2/a0], dtype=x.dtype)
    a = np.array([1.0, a1/a0, a2/a0], dtype=x.dtype)
    return lfilter(b, a, x, axis=0)


def env_follow(x, sr, attack_ms, release_ms):
    a_at = np.float32(np.exp(-1.0 / (max(attack_ms, 0.01) * sr / 1000.0)))
    a_re = np.float32(np.exp(-1.0 / (max(release_ms, 0.01) * sr / 1000.0)))
    fast = lfilter([np.float32(1.0) - a_at], [np.float32(1.0), -a_at], x, axis=0)
    slow = lfilter([np.float32(1.0) - a_re], [np.float32(1.0), -a_re], x, axis=0)
    return np.maximum(fast, slow)


def noise_gate(x, sr, threshold_db, ratio, attack_ms, release_ms):
    env = env_follow(np.abs(x), sr, attack_ms, release_ms)
    env_db = 20 * np.log10(np.maximum(env, np.float32(1e-9)))
    under = threshold_db - env_db
    slope = np.float32(1.0 - 1.0 / max(ratio, 1.001))
    gain_db = np.where(under <= 0, np.float32(0.0), -under * slope)
    return x * (10 ** (gain_db / 20)).astype(x.dtype)


def compress(x, sr, threshold_db, ratio, attack_ms, release_ms,
             knee_db, makeup_db, lookahead_ms=0, auto_makeup=False):
    env = env_follow(np.abs(x), sr, attack_ms, release_ms)
    env_db = 20 * np.log10(np.maximum(env, np.float32(1e-9)))
    over = env_db - threshold_db
    slope = np.float32(1.0 - 1.0 / max(ratio, 1.001))
    w = np.float32(max(knee_db, 1e-6))
    reduction = np.where(
        over <= -w / 2, np.float32(0.0),
        np.where(over >= w / 2, over * slope,
                 slope * (over + w / 2) ** 2 / (np.float32(2.0) * w)))
    mu = np.float32(makeup_db)
    if auto_makeup:
        mu = np.float32(np.max(reduction)) * np.float32(0.5)
    gain = (10 ** ((-reduction + mu) / 20)).astype(x.dtype)
    if lookahead_ms > 0.1:
        delay = min(int(lookahead_ms * sr / 1000), len(x) - 1)
        delayed = np.zeros_like(x)
        delayed[delay:] = x[:len(x) - delay]
        return delayed * gain
    return x * gain


def de_ess(x, sr, freq_hz, threshold_db, max_red_db,
           ratio=3.0, attack_ms=5.0, release_ms=50.0):
    lo = np.float32(max(freq_hz * 0.7, 800.0))
    hi = np.float32(min(freq_hz * 1.4, sr / 2 * 0.98))
    if lo >= hi:
        return x
    sos = butter(2, [lo, hi], "bandpass", fs=sr, output="sos").astype(x.dtype)
    band = sosfiltfilt(sos, x, axis=0)
    env = env_follow(np.abs(band), sr, attack_ms, release_ms)
    env_db = 20 * np.log10(np.maximum(env, np.float32(1e-9)))
    over = np.clip(env_db - threshold_db, np.float32(0.0), np.float32(max_red_db))
    slope = np.float32(1.0 - 1.0 / max(ratio, 1.001))
    red_db = over * slope
    g = (10 ** (-red_db / 20)).astype(x.dtype)
    return x + band * (g - np.float32(1.0))


def saturate(x, amount):
    if amount <= 0:
        return x
    drive = np.float32(1.0 + amount * 10.0)
    return np.tanh(x * drive) / np.float32(max(np.tanh(float(drive)), 1e-9))


def limiter_apply(x, sr, ceiling_db, release_ms):
    ceiling = np.float32(10 ** (ceiling_db / 20))
    env = env_follow(np.abs(x), sr, 0.1, max(release_ms, 1.0))
    gain = np.where(env > ceiling, ceiling / np.maximum(env, np.float32(1e-9)), np.float32(1.0)).astype(x.dtype)
    return x * gain


def apply_fades(x, sr, fade_in_ms, fade_out_ms):
    n = len(x)
    fi = min(int(fade_in_ms * sr / 1000), n)
    fo = min(int(fade_out_ms * sr / 1000), n)
    if fi > 1:
        x[:fi] *= np.linspace(0, 1, fi, dtype=x.dtype).reshape(-1, 1)
    if fo > 1:
        x[-fo:] *= np.linspace(1, 0, fo, dtype=x.dtype).reshape(-1, 1)
    return x


def normalize_level(x, sr, mode, target_db, max_gain_db, log):
    mode_used = mode
    measured = None
    if mode == "lufs":
        try:
            import pyloudnorm as pyln
            measured = pyln.Meter(sr).integrated_loudness(x)
            if not np.isfinite(measured):
                measured = None
        except ImportError:
            log("  ! pyloudnorm not installed - falling back to RMS")
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
    gain_factor = np.float32(10 ** (gain_db / 20))
    return x * gain_factor, gain_factor


def predicted_eq_response(opt, n_pts=400):
    sr = 48000
    freqs = np.logspace(np.log10(20), np.log10(20000), n_pts)
    total_db = np.zeros(n_pts)
    try:
        if opt.get("hpf"):
            order = {12: 1, 24: 2, 48: 4}[opt["hpf_slope"]]
            sos = butter(order, opt["hpf_freq"], "highpass", fs=sr, output="sos")
            _w, h = sosfreqz(sos, worN=freqs, fs=sr)
            total_db += 2 * 20 * np.log10(np.maximum(np.abs(h), 1e-12))
        for en, fk, gk, qk, fn in (
                ("lshelf", "lshelf_freq", "lshelf_gain", "lshelf_q", "low_shelf"),
                ("mud", "mud_freq", "mud_gain", "mud_q", "peaking"),
                ("pres", "pres_freq", "pres_gain", "pres_q", "peaking"),
                ("hshelf", "hshelf_freq", "hshelf_gain", "hshelf_q", "high_shelf")):
            if opt.get(en) and opt.get(gk, 0) != 0:
                A = 10 ** (opt[gk] / 40)
                w0 = 2 * np.pi * opt[fk] / sr
                alpha = np.sin(w0) / (2 * max(opt.get(qk, 0.707), 0.1))
                sq = 2 * np.sqrt(A) * alpha
                if fn == "peaking":
                    b = np.array([1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A])
                    a = np.array([1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A])
                    _w, h = freqz(b / a[0], a / a[0], worN=freqs, fs=sr)
                elif fn == "low_shelf":
                    b0 = A*((A+1)-(A-1)*np.cos(w0)+sq)
                    b1 = 2*A*((A-1)-(A+1)*np.cos(w0))
                    b2 = A*((A+1)-(A-1)*np.cos(w0)-sq)
                    a0 = (A+1)+(A-1)*np.cos(w0)+sq
                    a1 = -2*((A-1)+(A+1)*np.cos(w0))
                    a2 = (A+1)+(A-1)*np.cos(w0)-sq
                    _w, h = freqz([b0/a0,b1/a0,b2/a0],[1,a1/a0,a2/a0],worN=freqs,fs=sr)
                else:
                    b0 = A*((A+1)+(A-1)*np.cos(w0)+sq)
                    b1 = -2*A*((A-1)+(A+1)*np.cos(w0))
                    b2 = A*((A+1)+(A-1)*np.cos(w0)-sq)
                    a0 = (A+1)-(A-1)*np.cos(w0)+sq
                    a1 = 2*((A-1)-(A+1)*np.cos(w0))
                    a2 = (A+1)-(A-1)*np.cos(w0)-sq
                    _w, h = freqz([b0/a0,b1/a0,b2/a0],[1,a1/a0,a2/a0],worN=freqs,fs=sr)
                total_db += 20 * np.log10(np.maximum(np.abs(h), 1e-12))
    except Exception:
        pass
    return freqs, total_db


def auto_out_path(in_path, fmt):
    """Always saves to the Current Working Directory."""
    ext = ".mp3" if fmt == "mp3" else ".wav"
    filename = os.path.splitext(os.path.basename(in_path))[0]
    return os.path.join(os.getcwd(), filename + "_voiceboost" + ext)


def decode_input(in_path, log):
    if not os.path.isfile(in_path) or not os.access(in_path, os.R_OK):
        err = f"Input file not found or not readable: {in_path}"
        logging.error(err)
        raise RuntimeError(err)
    ext = os.path.splitext(in_path)[1].lower()
    if ext in (".wav", ".aif", ".aiff"):
        return in_path, None
    if shutil.which("ffmpeg") is None:
        err = f"Cannot read {ext} files: ffmpeg not installed. Install FFmpeg and add to PATH."
        logging.error(err)
        raise RuntimeError(err)
    tmp = tempfile.TemporaryDirectory(dir=TMP_DIR)
    out_wav = os.path.join(tmp.name, "decoded.wav")
    log(f"  Decoding {os.path.basename(in_path)} to WAV via ffmpeg...")
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                           "-i", in_path, out_wav], capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.isfile(out_wav):
        tmp.cleanup()
        stderr = proc.stderr.strip()[:300]
        raise RuntimeError(f"ffmpeg decode failed: {stderr}")
    return out_wav, tmp


def encode_mp3(wav_path, mp3_path, bitrate_kbps, log):
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                           "-i", wav_path, "-b:a", f"{bitrate_kbps}k", mp3_path],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg MP3 encode failed: {proc.stderr.strip()[:300]}")
    log(f"Encoded MP3 @ {bitrate_kbps} kbps")


def save_output(x, sr, out_path, fmt, bitrate_kbps, log):
    try:
        if fmt == "mp3":
            if shutil.which("ffmpeg") is None:
                log("! ffmpeg not found - falling back to WAV")
                out_path = os.path.splitext(out_path)[0] + ".wav"
                sf.write(out_path, x, sr, subtype="PCM_24")
                log(f"Saved WAV: {out_path}")
                return out_path
            with tempfile.TemporaryDirectory(dir=TMP_DIR) as tmp:
                tmp_wav = os.path.join(tmp, "out.wav")
                sf.write(tmp_wav, x, sr, subtype="PCM_24")
                encode_mp3(tmp_wav, out_path, bitrate_kbps, log)
        else:
            sf.write(out_path, x, sr, subtype="PCM_24")
            log(f"Saved WAV: {out_path}")
        return out_path
    except Exception as e:
        logging.error(f"Failed to save output: {e}\n{traceback.format_exc()}")
        raise


def finalize(x, sr, out_path, opt, log, step_cb=None):
    if step_cb:
        step_cb("Finalizing (fades + boost + norm + limiter + save)", "orange")
    if opt.get("fade_in", 0) > 0 or opt.get("fade_out", 0) > 0:
        x = apply_fades(x, sr, opt.get("fade_in", 0), opt.get("fade_out", 0))
    x = x * np.float32(10 ** (opt["boost"] / 20))
    if opt["boost"] != 0.0:
        log(f"  Voice boost +{opt['boost']:.1f} dB")
    if opt["normalize"]:
        x, _ = normalize_level(x, sr, opt["norm_mode"], opt["norm_target"],
                               opt["norm_max"], log)
    if opt.get("limiter"):
        x = limiter_apply(x, sr, opt.get("lim_ceil", -1.0), opt.get("lim_rel", 100))
        log(f"  Limiter: ceiling {opt.get('lim_ceil', -1.0):.1f} dBFS")
    ceil = np.float32(10 ** (opt.get("norm_ceil", -0.2) / 20))
    x = np.clip(x, -ceil, ceil)
    save_output(x, sr, out_path, opt["fmt"], opt["bitrate"], log)


def apply_dsp_chain(x, sr, opt, log, step_cb=None):
    def _r(name, color="green"):
        if step_cb:
            step_cb(name, color)

    if opt.get("gate"):
        _r("Step: Noise Gate", "green")
        x = noise_gate(x, sr, opt["gate_thr"], opt["gate_ratio"],
                       opt["gate_atk"], opt["gate_rel"])

    if opt["denoise"]:
        _r("Step: Denoising", "green")
        try:
            import noisereduce as nr
            mode = "stationary" if opt["den_mode"] else "non-stationary"
            kw = {"stationary": opt["den_mode"], "prop_decrease": opt["den_amt"]}
            if opt.get("den_nfft", 0) > 0:
                kw["n_fft"] = int(opt["den_nfft"])
            if opt.get("den_nstd", 0) > 0:
                kw["n_std_thresh"] = opt["den_nstd"]
            log(f"  Denoising ({mode}, str {opt['den_amt']:.2f})...")
            x = np.stack([
                nr.reduce_noise(y=x[:, c], sr=sr, **kw).astype(np.float32)
                for c in range(x.shape[1])
            ], axis=1)
        except ImportError:
            log("  ! noisereduce not installed - skipping denoise")

    if opt["hpf"]:
        _r("Step: High-pass filter", "green")
        order = {12: 1, 24: 2, 48: 4}[opt["hpf_slope"]]
        log(f"  HPF {opt['hpf_freq']:.0f} Hz ({opt['hpf_slope']} dB/oct)")
        sos = butter(order, opt["hpf_freq"], "highpass", fs=sr, output="sos").astype(x.dtype)
        x = sosfiltfilt(sos, x, axis=0)

    if opt.get("lshelf") and opt.get("lshelf_gain", 0) != 0:
        _r("Step: Low shelf EQ", "green")
        x = low_shelf(x, sr, opt["lshelf_freq"], opt["lshelf_gain"], opt["lshelf_q"])

    if opt["mud"] and opt["mud_gain"] != 0:
        _r("Step: Mud cut EQ", "green")
        x = peaking_eq(x, sr, opt["mud_freq"], opt["mud_q"], opt["mud_gain"])

    if opt["pres"] and opt["pres_gain"] != 0:
        _r("Step: Presence EQ", "green")
        x = peaking_eq(x, sr, opt["pres_freq"], opt["pres_q"], opt["pres_gain"])

    if opt.get("hshelf") and opt.get("hshelf_gain", 0) != 0:
        _r("Step: High shelf EQ", "green")
        x = high_shelf(x, sr, opt["hshelf_freq"], opt["hshelf_gain"], opt["hshelf_q"])

    if opt.get("sat_amt", 0) > 0:
        _r("Step: Saturation", "green")
        x = saturate(x, opt["sat_amt"])

    if opt["comp"]:
        _r("Step: Compressor", "green")
        x = compress(x, sr, opt["comp_thr"], opt["comp_ratio"], opt["comp_atk"],
                     opt["comp_rel"], opt["comp_knee"], opt["comp_makeup"],
                     opt.get("comp_look", 0), opt.get("comp_auto_mu", False))

    if opt["deess"]:
        _r("Step: De-esser", "green")
        x = de_ess(x, sr, opt["deess_freq"], opt["deess_thr"], opt["deess_amt"],
                   opt.get("deess_ratio", 3.0), opt.get("deess_atk", 5.0),
                   opt.get("deess_rel", 50.0))

    return x


def process_dsp(in_path, out_path, opt, log, step_cb=None):
    log(f"Reading {in_path}")
    tmp = None
    try:
        src, tmp = decode_input(in_path, log)
        with sf.SoundFile(src) as f:
            sr = f.samplerate
            duration_sec = f.frames / sr
        if duration_sec > LARGE_FILE_THRESHOLD_SEC:
            log(f"  Large file ({duration_sec:.1f}s) - chunked processing")
            process_dsp_chunked(src, out_path, opt, log, step_cb)
        else:
            if opt.get("snippet"):
                start, dur = opt["snippet"]
                with sf.SoundFile(src) as f:
                    f.seek(int(start * sr))
                    x = f.read(int(dur * sr), always_2d=True, dtype="float32")
            else:
                x, sr = sf.read(src, always_2d=True, dtype="float32")
            log(f"  {sr} Hz, {x.shape[1]} ch, {len(x)/sr:.1f}s")
            x = apply_dsp_chain(x, sr, opt, log, step_cb)
            finalize(x, sr, out_path, opt, log, step_cb)
    finally:
        if tmp:
            tmp.cleanup()


def process_dsp_chunked(in_path, out_path, opt, log, step_cb=None):
    with sf.SoundFile(in_path) as f:
        sr = f.samplerate
        n_ch = f.channels
        total = f.frames
    chunk = int(CHUNK_DURATION_SEC * sr)
    overlap = int(CHUNK_OVERLAP_SEC * sr)
    step = chunk - overlap
    total_chunks = max(1, (total + step - 1) // step)
    with sf.SoundFile(out_path, 'w', samplerate=sr, channels=n_ch, subtype='PCM_24') as out_f:
        pos = 0
        ci = 0
        while pos < total:
            ci += 1
            end = min(pos + chunk, total)
            if step_cb:
                step_cb(f"Chunk {ci}/{total_chunks} ({pos/sr:.0f}s-{end/sr:.0f}s)", "blue")
            with sf.SoundFile(in_path) as f:
                f.seek(pos)
                x = f.read(end - pos, always_2d=True, dtype="float32")
            x = apply_dsp_chain(x, sr, opt, log)
            x = x * np.float32(10 ** (opt["boost"] / 20))
            if opt["normalize"]:
                x, _ = normalize_level(x, sr, opt["norm_mode"], opt["norm_target"],
                                       opt["norm_max"], log)
            if opt.get("limiter"):
                x = limiter_apply(x, sr, opt.get("lim_ceil", -1.0), opt.get("lim_rel", 100))
            ceil = np.float32(10 ** (opt.get("norm_ceil", -0.2) / 20))
            x = np.clip(x, -ceil, ceil)
            if pos > 0 and (end - pos) > overlap:
                fo = np.linspace(1, 0, overlap, dtype=x.dtype).reshape(-1, 1)
                fi = np.linspace(0, 1, overlap, dtype=x.dtype).reshape(-1, 1)
                with sf.SoundFile(out_path, 'r') as pf:
                    pf.seek(pf.frames - overlap)
                    pt = pf.read(overlap, always_2d=True, dtype="float32")
                with sf.SoundFile(out_path, 'r+') as rw:
                    rw.seek(rw.frames - overlap)
                    rw.write(pt * fo + x[:overlap] * fi)
                out_f.write(x[overlap:])
            else:
                out_f.write(x)
            pos += step


# ----------------------------------------------------------------------
# AI Model Management
# ----------------------------------------------------------------------

def get_model_cache_info():
    """Scan the cache directory and return info about downloaded models."""
    info = {"dir": AI_CACHE_DIR, "exists": os.path.isdir(AI_CACHE_DIR),
            "models": {}, "total_size_mb": 0}
    if not info["exists"]:
        return info

    for root, _dirs, files in os.walk(AI_CACHE_DIR):
        for f in files:
            if f.endswith((".th", ".pth", ".ckpt", ".bin", ".safetensors")):
                fpath = os.path.join(root, f)
                size_mb = os.path.getsize(fpath) / (1024 * 1024)
                rel = os.path.relpath(fpath, AI_CACHE_DIR)
                info["models"][rel] = {"path": fpath, "size_mb": size_mb}
                info["total_size_mb"] += size_mb

    return info


def is_model_cached(model_name):
    """Check if a specific Demucs model appears to be cached."""
    info = get_model_cache_info()
    for key in info["models"]:
        if model_name.lower() in key.lower():
            return True
    return False


def download_demucs_model(model_name, log_cb, progress_cb=None):
    """Download a Demucs model by running it on a tiny silent file."""
    log_cb(f"📥 Starting download of model '{model_name}' to {AI_CACHE_DIR}...")

    tmp_dir = tempfile.mkdtemp(dir=TMP_DIR)
    tiny_wav = os.path.join(tmp_dir, "tiny_silent.wav")
    out_dir = os.path.join(tmp_dir, "out")
    os.makedirs(out_dir, exist_ok=True)

    try:
        sr = 44100
        silence = np.zeros((sr, 2), dtype=np.float32)
        sf.write(tiny_wav, silence, sr, subtype="PCM_16")
        log_cb(f"  Created test file: {tiny_wav}")

        cmd = [
            "demucs", "-n", model_name,
            "--two-stems", "vocals",
            "--segment", "7", "--shifts", "0",
            "-o", out_dir, tiny_wav
        ]
        log_cb(f"  Command: {' '.join(cmd)}")
        log_cb(f"  Downloading model weights (this may take a few minutes)...")

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=_get_demucs_env()
        )

        buf = []
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n":
                line = "".join(buf).strip()
                buf = []
                if line:
                    log_cb(f"  {line}")
                    m = list(re.finditer(r"(\d{1,3})%", line))
                    if m and progress_cb:
                        progress_cb(int(m[-1].group(1)))
            else:
                buf.append(ch)

        if buf:
            t = "".join(buf).strip()
            if t:
                log_cb(f"  {t}")

        retcode = proc.wait()

        info = get_model_cache_info()
        model_files = [k for k in info["models"] if model_name.lower() in k.lower()]

        if model_files:
            total = sum(info["models"][k]["size_mb"] for k in model_files)
            log_cb(f"  ✅ Model '{model_name}' cached successfully!")
            log_cb(f"  📁 Location: {AI_CACHE_DIR}")
            log_cb(f"  💾 Size: {total:.1f} MB ({len(model_files)} file(s))")
            if progress_cb:
                progress_cb(100)
            return True
        elif retcode == 0:
            log_cb(f"  ✅ Model '{model_name}' processed OK (may already be cached).")
            if progress_cb:
                progress_cb(100)
            return True
        else:
            log_cb(f"  ⚠ Process exited with code {retcode}. Model may still be downloading.")
            return False

    except FileNotFoundError:
        log_cb("  ❌ ERROR: demucs not found. Install with: pip install demucs")
        return False
    except Exception as e:
        log_cb(f"  ❌ ERROR: {e}")
        logging.error(f"Model download failed: {e}\n{traceback.format_exc()}")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _build_demucs_cmd(opt, tmp_name, src):
    """Builds the demucs command line from fine-tuning options."""
    cmd = ["demucs"]
    cmd += ["-n", opt.get("demucs_model", "htdemucs")]
    cmd += ["--two-stems", opt.get("demucs_stems", "vocals")]
    cmd += ["--segment", str(int(opt.get("demucs_segment", 7)))]
    cmd += ["--shifts", str(int(opt.get("demucs_shifts", 0)))]
    device = opt.get("demucs_device", "auto")
    if device != "auto":
        cmd += ["-d", device]
    clip = opt.get("demucs_clip", "rescale")
    if clip != "rescale":
        cmd += ["--clip-mode", clip]
    cmd += ["-o", tmp_name, src]
    return cmd


def _get_demucs_env():
    """Returns environment dict with AI cache paths set."""
    env = os.environ.copy()
    env["TORCH_HOME"] = AI_CACHE_DIR
    env["HF_HOME"] = os.path.join(AI_CACHE_DIR, "huggingface")
    env["HF_HUB_CACHE"] = os.path.join(AI_CACHE_DIR, "huggingface", "hub")
    return env


def process_demucs(in_path, out_path, opt, log, step_cb=None):
    if shutil.which("demucs") is None:
        raise RuntimeError("demucs not found. Install with: pip install demucs")
    pct = opt.get("_pct")
    tmp = None
    stem = opt.get("demucs_stems", "vocals")
    try:
        if step_cb:
            step_cb("Demucs: checking file duration", "blue")
        src, tmp = decode_input(in_path, log)
        if tmp is None:
            tmp = tempfile.TemporaryDirectory(dir=TMP_DIR)
        
        with sf.SoundFile(src) as f:
            sr = f.samplerate
            duration_sec = f.frames / sr
        
        is_large = duration_sec > LARGE_FILE_THRESHOLD_SEC
        
        if is_large:
            log(f"  Large file ({duration_sec:.1f}s) - chunked Demucs processing")
            process_demucs_chunked(src, out_path, opt, log, step_cb, tmp.name)
            return
        
        if opt.get("snippet"):
            start, dur = opt["snippet"]
            snip = os.path.join(tmp.name, "snippet.wav")
            with sf.SoundFile(src) as f:
                sr0 = f.samplerate
                f.seek(int(start * sr0))
                sf.write(snip, f.read(int(dur * sr0), always_2d=True, dtype="float32"), sr0, subtype="PCM_24")
            src = snip
        if step_cb:
            step_cb(f"Demucs: separating '{stem}' (AI model)", "blue")
        log(f"Running Demucs with model={opt.get('demucs_model','htdemucs')}, segment={opt.get('demucs_segment',7)}, shifts={opt.get('demucs_shifts',0)}...")
        
        cmd = _build_demucs_cmd(opt, tmp.name, src)
        log(f"  Command: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_get_demucs_env())
        shown = {"p": False}
        def handle(line):
            m = list(re.finditer(r"(\d{1,3})%", line))
            if m and pct is not None:
                v = int(m[-1].group(1))
                pct(v)
                if not shown["p"]:
                    log(f"  Working... {v}%")
                    shown["p"] = True
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
            t = "".join(buf).strip()
            if t:
                handle(t)
        if proc.wait() != 0:
            raise RuntimeError("demucs exited with an error")
        if pct is not None:
            pct(100)
        stems_dir = None
        for root, _d, files in os.walk(tmp.name):
            if f"{stem}.wav" in files:
                stems_dir = root
                break
        if stems_dir is None:
            raise RuntimeError(f"Could not find separated stems (looking for {stem}.wav)")
        isolated, sr = sf.read(os.path.join(stems_dir, f"{stem}.wav"), always_2d=True, dtype="float32")
        if opt["mode"] == "voice":
            if step_cb:
                step_cb(f"Finalizing isolated {stem}", "orange")
            finalize(isolated, sr, out_path, opt, log, step_cb)
        elif opt["mode"] == "both":
            base, ext = os.path.splitext(out_path)
            ip, bp = f"{base}_{stem}{ext}", f"{base}_no_{stem}{ext}"
            if step_cb:
                step_cb(f"Finalizing {stem}", "orange")
            finalize(isolated, sr, ip, opt, log, step_cb)
            bg_opt = dict(opt)
            bg_opt["boost"] = 0.0
            bg_opt["deess"] = False
            bg, _ = sf.read(os.path.join(stems_dir, f"no_{stem}.wav"), always_2d=True, dtype="float32")
            if step_cb:
                step_cb(f"Finalizing no_{stem}", "orange")
            finalize(bg, sr, bp, bg_opt, log, step_cb)
        else:
            if step_cb:
                step_cb(f"Remixing {stem} + no_{stem}", "orange")
            bg, _ = sf.read(os.path.join(stems_dir, f"no_{stem}.wav"), always_2d=True, dtype="float32")
            n = min(len(isolated), len(bg))
            finalize(isolated[:n] + bg[:n], sr, out_path, opt, log, step_cb)
    except RuntimeError as e:
        if "not enough memory" in str(e).lower() or "alloc" in str(e).lower():
            raise RuntimeError("Demucs ran out of memory. Files over 10 min are auto-chunked.")
        raise
    finally:
        if tmp:
            tmp.cleanup()


def process_demucs_chunked(in_path, out_path, opt, log, step_cb, tmp_base):
    pct = opt.get("_pct")
    stem = opt.get("demucs_stems", "vocals")
    
    with sf.SoundFile(in_path) as f:
        sr = f.samplerate
        n_ch = f.channels
        total = f.frames
    
    chunk = int(CHUNK_DURATION_SEC * sr)
    overlap = int(CHUNK_OVERLAP_SEC * sr)
    step = chunk - overlap
    total_chunks = max(1, (total + step - 1) // step)
    
    log(f"  Processing in {CHUNK_DURATION_SEC}s chunks with {CHUNK_OVERLAP_SEC}s overlap")
    
    iso_accum = os.path.join(tmp_base, f"{stem}_accum.wav")
    bg_accum = os.path.join(tmp_base, f"no_{stem}_accum.wav")
    
    pos = 0
    ci = 0
    
    while pos < total:
        ci += 1
        end = min(pos + chunk, total)
        
        if step_cb:
            step_cb(f"Demucs chunk {ci}/{total_chunks} ({pos/sr:.0f}s-{end/sr:.0f}s)", "blue")
        log(f"  Demucs chunk {ci}/{total_chunks}")
        
        chunk_file = os.path.join(tmp_base, f"chunk_{ci}.wav")
        with sf.SoundFile(in_path) as f:
            f.seek(pos)
            chunk_data = f.read(end - pos, always_2d=True, dtype="float32")
        sf.write(chunk_file, chunk_data, sr, subtype="PCM_24")
        
        chunk_out_dir = os.path.join(tmp_base, f"demucs_out_{ci}")
        cmd = _build_demucs_cmd(opt, chunk_out_dir, chunk_file)
        log(f"  Command: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_get_demucs_env())
        
        buf = []
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n":
                line = "".join(buf).strip()
                buf = []
                if line:
                    m = list(re.finditer(r"(\d{1,3})%", line))
                    if m and pct is not None:
                        v = int(m[-1].group(1))
                        chunk_pct = (ci - 1) * 100 / total_chunks + v / total_chunks
                        pct(int(chunk_pct))
                    log(f"    {line}")
            else:
                buf.append(ch)
        
        if proc.wait() != 0:
            raise RuntimeError(f"demucs failed on chunk {ci}")
        
        stems_dir = None
        for root, _d, files in os.walk(chunk_out_dir):
            if f"{stem}.wav" in files:
                stems_dir = root
                break
        
        if stems_dir is None:
            raise RuntimeError(f"Could not find stems for chunk {ci}")
        
        iso_chunk, _ = sf.read(os.path.join(stems_dir, f"{stem}.wav"), always_2d=True, dtype="float32")
        bg_chunk, _ = sf.read(os.path.join(stems_dir, f"no_{stem}.wav"), always_2d=True, dtype="float32")
        
        if ci == 1:
            sf.write(iso_accum, iso_chunk, sr, subtype="PCM_24")
            sf.write(bg_accum, bg_chunk, sr, subtype="PCM_24")
        else:
            if overlap > 0 and len(iso_chunk) > overlap:
                fade_out = np.linspace(1, 0, overlap, dtype=iso_chunk.dtype).reshape(-1, 1)
                fade_in = np.linspace(0, 1, overlap, dtype=iso_chunk.dtype).reshape(-1, 1)
                
                with sf.SoundFile(iso_accum, 'r') as f:
                    f.seek(f.frames - overlap)
                    prev_iso_tail = f.read(overlap, always_2d=True, dtype="float32")
                with sf.SoundFile(bg_accum, 'r') as f:
                    f.seek(f.frames - overlap)
                    prev_bg_tail = f.read(overlap, always_2d=True, dtype="float32")
                
                blended_iso = prev_iso_tail * fade_out + iso_chunk[:overlap] * fade_in
                blended_bg = prev_bg_tail * fade_out + bg_chunk[:overlap] * fade_in
                
                with sf.SoundFile(iso_accum, 'r+') as f:
                    f.seek(f.frames - overlap)
                    f.write(blended_iso)
                with sf.SoundFile(bg_accum, 'r+') as f:
                    f.seek(f.frames - overlap)
                    f.write(blended_bg)
                
                with sf.SoundFile(iso_accum, 'r+') as f:
                    f.seek(0, 2)
                    f.write(iso_chunk[overlap:])
                with sf.SoundFile(bg_accum, 'r+') as f:
                    f.seek(0, 2)
                    f.write(bg_chunk[overlap:])
            else:
                with sf.SoundFile(iso_accum, 'r+') as f:
                    f.seek(0, 2)
                    f.write(iso_chunk)
                with sf.SoundFile(bg_accum, 'r+') as f:
                    f.seek(0, 2)
                    f.write(bg_chunk)
        
        os.remove(chunk_file)
        shutil.rmtree(chunk_out_dir, ignore_errors=True)
        
        pos += step
    
    if pct is not None:
        pct(100)
    
    isolated, _ = sf.read(iso_accum, always_2d=True, dtype="float32")
    bg, _ = sf.read(bg_accum, always_2d=True, dtype="float32")
    
    os.remove(iso_accum)
    os.remove(bg_accum)
    
    if opt["mode"] == "voice":
        if step_cb:
            step_cb(f"Finalizing isolated {stem}", "orange")
        finalize(isolated, sr, out_path, opt, log, step_cb)
    elif opt["mode"] == "both":
        base, ext = os.path.splitext(out_path)
        ip, bp = f"{base}_{stem}{ext}", f"{base}_no_{stem}{ext}"
        if step_cb:
            step_cb(f"Finalizing {stem}", "orange")
        finalize(isolated, sr, ip, opt, log, step_cb)
        bg_opt = dict(opt)
        bg_opt["boost"] = 0.0
        bg_opt["deess"] = False
        if step_cb:
            step_cb(f"Finalizing no_{stem}", "orange")
        finalize(bg, sr, bp, bg_opt, log, step_cb)
    else:
        if step_cb:
            step_cb(f"Remixing {stem} + no_{stem}", "orange")
        n = min(len(isolated), len(bg))
        finalize(isolated[:n] + bg[:n], sr, out_path, opt, log, step_cb)


# ----------------------------------------------------------------------
# Spectrum
# ----------------------------------------------------------------------

class SpectrumView(QWidget):
    F_MIN, F_MAX = 20.0, 20000.0
    DB_MIN, DB_MAX = -100, 0
    def __init__(self, p=None):
        super().__init__(p)
        self.setMinimumHeight(140)
        self._in = self._out = self._curve = None
    def set_input(self, f, d):  self._in = (f, d); self.update()
    def set_output(self, f, d): self._out = (f, d); self.update()
    def set_curve(self, f, d):  self._curve = (f, d); self.update()
    def _x(self, f, w):
        lo, hi = np.log10(self.F_MIN), np.log10(self.F_MAX)
        return (np.log10(np.clip(f, self.F_MIN, self.F_MAX)) - lo) / (hi - lo) * (w - 1)
    def _y(self, db, h):
        t = (np.clip(db, self.DB_MIN, self.DB_MAX) - self.DB_MIN) / (self.DB_MAX - self.DB_MIN)
        return (1.0 - t) * (h - 1)
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1e1e2e"))
        w, h = self.width(), self.height()
        font = p.font(); font.setPointSize(7); p.setFont(font)
        p.setPen(QPen(QColor("#45475a"), 1))
        for f in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000):
            x = int(self._x(f, w))
            p.drawLine(x, 0, x, h)
            label = "20k" if f == 20000 else (f"{f//1000}k" if f >= 1000 else str(f))
            p.drawText(x + 2, h - 3, label)
        for db in range(self.DB_MIN + 20, self.DB_MAX, 20):
            y = int(self._y(db, h))
            p.drawLine(0, y, w, y)
            p.drawText(3, y - 2, f"{db}")
        def draw(s, c, lw=1, st=Qt.SolidLine):
            if not s or s[0] is None: return
            pts = QPolygonF()
            for f, d in zip(s[0], s[1]):
                pts.append(QPointF(self._x(f, w), self._y(d, h)))
            if pts.count() < 2: return
            pen = QPen(QColor(c), lw, st); pen.setCosmetic(True)
            p.setPen(pen); p.drawPolyline(pts)
        draw(self._in, "#a6e3a1"); draw(self._out, "#89b4fa")
        draw(self._curve, "#fab387", 1, Qt.DashLine)
        p.end()


class SpectrumWorker(QThread):
    ready = Signal(str, str, object, object)
    def __init__(self, tag, path):
        super().__init__(); self.tag, self.path = tag, path
    def run(self):
        try:
            x, sr = sf.read(self.path, always_2d=True, dtype="float32")
            mono = x.mean(axis=1)
            n = 8192
            if len(mono) <= n:
                padded = np.zeros(n, dtype=np.float32); padded[:len(mono)] = mono
                windows = [padded]
            else:
                hop = max((len(mono) - n) // 47, n)
                windows = [mono[i:i+n] for i in range(0, len(mono)-n+1, hop)]
            win = np.hanning(n).astype(np.float32)
            acc = None
            for w in windows:
                m = np.abs(np.fft.rfft(w * win))
                acc = m if acc is None else acc + m
            spec = acc / len(windows)
            freqs = np.fft.rfftfreq(n, 1.0/sr)
            db = 20 * np.log10(np.maximum(spec, 1e-12) / (n/4))
            keep = (freqs >= SpectrumView.F_MIN) & (freqs <= min(sr/2, SpectrumView.F_MAX))
            self.ready.emit(self.tag, self.path, freqs[keep], db[keep])
        except Exception as e:
            logging.error(f"SpectrumWorker failed: {e}\n{traceback.format_exc()}")
            self.ready.emit(self.tag, self.path, None, None)


# ----------------------------------------------------------------------
# Model Download Thread
# ----------------------------------------------------------------------

class ModelDownloadThread(QThread):
    """Background thread for downloading Demucs models."""
    log_msg = Signal(str)
    progress = Signal(int)
    finished_ok = Signal(str)
    failed = Signal(str)
    
    def __init__(self, models):
        super().__init__()
        if isinstance(models, str):
            self.models = [models]
        else:
            self.models = list(models)
    
    def run(self):
        try:
            ok = 0
            for i, model_name in enumerate(self.models):
                self.log_msg.emit(f"📥 [{i+1}/{len(self.models)}] Downloading '{model_name}'...")
                
                def log_cb(msg):
                    self.log_msg.emit(msg)
                
                def pct_cb(v):
                    if len(self.models) == 1:
                        self.progress.emit(v)
                    else:
                        base = int(100 * i / len(self.models))
                        span = int(100 / len(self.models))
                        self.progress.emit(base + int(v * span / 100))
                
                success = download_demucs_model(model_name, log_cb, pct_cb)
                if success:
                    ok += 1
                else:
                    self.log_msg.emit(f"  ⚠ Failed to download '{model_name}'")
            
            if ok == len(self.models):
                self.finished_ok.emit(f"All {ok} model(s) downloaded successfully to {AI_CACHE_DIR}")
            elif ok > 0:
                self.finished_ok.emit(f"{ok}/{len(self.models)} model(s) downloaded. Check log for details.")
            else:
                self.failed.emit("No models were downloaded. Check the log for errors.")
        except Exception as e:
            self.failed.emit(str(e))


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------

class Worker(QThread):
    progress = Signal(str)
    percent = Signal(int)
    step_info = Signal(str, str)
    finished_ok = Signal(str)
    failed = Signal(str)
    file_done = Signal(str, bool)
    
    def __init__(self, params):
        super().__init__()
        self.params = params
        self._cancelled = False
    
    def cancel(self):
        self._cancelled = True
    
    def is_cancelled(self):
        return self._cancelled
    
    def _batch_pct(self, base, span):
        def w(v):
            if not self._cancelled:
                self.percent.emit(base + int(v * span / 100.0))
        return w
    
    def _step_cb(self, text, color="green"):
        if not self._cancelled:
            self.step_info.emit(text, color)
    
    def _run_one(self, inp, outp, opt):
        if self._cancelled:
            return
        if opt["method"] == "demucs":
            process_demucs(inp, outp, opt, self.progress.emit, self._step_cb)
        else:
            if opt["method"] == "basic":
                opt = dict(opt); opt["denoise"] = False; opt["deess"] = False
            process_dsp(inp, outp, opt, self.progress.emit, self._step_cb)
    
    def run(self):
        p = self.params
        try:
            jobs = p.get("batch")
            if jobs:
                total = len(jobs); ok = 0
                for i, (inp, outp) in enumerate(jobs):
                    if self._cancelled:
                        break
                    base, span = 100.0*i/total, 100.0/total
                    one = dict(p); one["input"], one["output"] = inp, outp
                    one.pop("batch", None); one["_pct"] = self._batch_pct(base, span)
                    self.progress.emit(f"[{i+1}/{total}] {os.path.basename(inp)}")
                    self.step_info.emit(f"File {i+1}/{total}: {os.path.basename(inp)}", "green")
                    try:
                        self._run_one(inp, outp, one); ok += 1
                        self.file_done.emit(outp, True)
                    except Exception as e:
                        logging.error(f"ERROR {os.path.basename(inp)}: {e}\n{traceback.format_exc()}")
                        self.progress.emit(f"  ERROR: {e}"); self.file_done.emit(outp, False)
                    self.percent.emit(int(base + span))
                if self._cancelled:
                    self.finished_ok.emit(f"Cancelled. {ok}/{total} files processed.")
                else:
                    self.finished_ok.emit(f"{ok}/{total} files processed")
            else:
                self.step_info.emit("Starting...", "green")
                self._run_one(p["input"], p["output"], p)
                if not self._cancelled:
                    self.finished_ok.emit(p["output"])
        except Exception as e:
            logging.error(f"Worker failed: {e}\n{traceback.format_exc()}")
            self.failed.emit(str(e))


def ms_to_str(ms):
    s = int(max(ms, 0) // 1000); return f"{s//60}:{s%60:02d}"
def sec_to_str(s):
    s = int(max(s, 0)); return f"{s//60}:{s%60:02d}"
def spin(lo, hi, val, step, suffix="", dec=1):
    w = QDoubleSpinBox(); w.setRange(lo, hi); w.setDecimals(dec)
    w.setSingleStep(step); w.setValue(val)
    if suffix: w.setSuffix(suffix)
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
        self.resize(900, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(DARK_THEME)
        
        self.worker = None; self._spec_worker = None; self._spec_keepalive = []
        self._spec_cache = {}; self._batching = False; self._batch_failed = []
        self._previewing = False; self._pending_seek = None; self.preview_info = None
        self._job_start_time = None
        self._recent_files = []
        self._dl_thread = None
        self.settings = QSettings("VoiceBooster", "VoiceBooster")
        
        central = QWidget(); self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)
        
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        # ============ TAB 1: Files & Preview ============
        tab1 = QWidget()
        tab1_layout = QVBoxLayout(tab1)
        tab1_layout.setSpacing(8)
        
        fb = QGroupBox("Files & Export Format")
        fl = QFormLayout(fb)
        
        ir = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Input audio file...")
        self.input_edit.textChanged.connect(self.load_play_source)
        bi = QPushButton("Browse..."); bi.setToolTip("Ctrl+O"); bi.clicked.connect(self.browse_input)
        self.recent_combo = QComboBox()
        self.recent_combo.setToolTip("Recent files (click to load)")
        self.recent_combo.setMinimumWidth(180)
        self.recent_combo.addItem("— Recent files —", None)
        self.recent_combo.currentIndexChanged.connect(self._on_recent_selected)
        ir.addWidget(self.input_edit, 3); ir.addWidget(self.recent_combo, 2); ir.addWidget(bi)
        fl.addRow("Input:", ir)
        
        self.file_info_label = QLabel("")
        self.file_info_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        fl.addRow("", self.file_info_label)
        
        orw = QHBoxLayout(); self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Output file (auto-saved to CWD)...")
        bo = QPushButton("Browse..."); bo.clicked.connect(self.browse_output)
        orw.addWidget(self.output_edit); orw.addWidget(bo); fl.addRow("Output:", orw)
        
        self.estimated_size_label = QLabel("")
        self.estimated_size_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        fl.addRow("", self.estimated_size_label)
        
        fr = QHBoxLayout(); self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("WAV","wav"); self.fmt_combo.addItem("MP3","mp3")
        self.fmt_combo.currentIndexChanged.connect(self.fmt_changed)
        self.br_combo = QComboBox()
        for b in (128,192,256,320): self.br_combo.addItem(f"{b} kbps",b)
        self.br_combo.setCurrentIndex(1); self.br_combo.setEnabled(False)
        self.ffmpeg_status = QLabel()
        fr.addWidget(QLabel("Format:")); fr.addWidget(self.fmt_combo)
        fr.addWidget(QLabel("Bitrate:")); fr.addWidget(self.br_combo)
        fr.addStretch(1); fr.addWidget(self.ffmpeg_status); fl.addRow("",fr)
        tab1_layout.addWidget(fb)
        self.check_ffmpeg()
        
        pb = QGroupBox("Preview Player")
        pl = QVBoxLayout(pb)
        r1 = QHBoxLayout(); self.play_src = QComboBox()
        self.play_src.addItem("Input","input"); self.play_src.addItem("Output","output")
        self.play_src.currentIndexChanged.connect(lambda _: self.load_play_source())
        self.play_btn = QPushButton("▶ Play"); self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn = QPushButton("■ Stop"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_playback)
        self.ab_btn = QPushButton("A/B swap"); self.ab_btn.setEnabled(False)
        self.ab_btn.clicked.connect(self.ab_swap)
        for w in [QLabel("Source:"),self.play_src,self.play_btn,self.stop_btn,self.ab_btn]:
            r1.addWidget(w)
        r1.addStretch(1); pl.addLayout(r1)
        r1b = QHBoxLayout()
        self.preview_btn = QPushButton("⚡ Preview 15 s"); self.preview_btn.setEnabled(False)
        self.preview_btn.clicked.connect(self.start_preview)
        self.cmp_btn = QPushButton("Compare original"); self.cmp_btn.setEnabled(False)
        self.cmp_btn.clicked.connect(self.play_original_snippet)
        r1b.addWidget(self.preview_btn); r1b.addWidget(self.cmp_btn); pl.addLayout(r1b)
        r2 = QHBoxLayout(); self.seek = QSlider(Qt.Horizontal); self.seek.setRange(0,0)
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        self.seek.sliderMoved.connect(lambda v: self.time_lbl.setText(f"{ms_to_str(v)} / {ms_to_str(self.player.duration())}"))
        self.time_lbl = QLabel("0:00 / 0:00")
        r2.addWidget(self.seek,1); r2.addWidget(self.time_lbl); pl.addLayout(r2)
        tab1_layout.addWidget(pb)
        
        if HAS_MULTIMEDIA:
            self.audio_out = QAudioOutput(); self.player = QMediaPlayer()
            self.player.setAudioOutput(self.audio_out)
            self.player.positionChanged.connect(self.on_position)
            self.player.durationChanged.connect(self.on_duration)
            self.player.playbackStateChanged.connect(self.on_playstate)
            self.player.mediaStatusChanged.connect(self.on_media_status)
            self.player.errorOccurred.connect(lambda e,s: self.log(f"Playback error: {s}"))
        else:
            self.player = None; pb.setTitle("Preview (unavailable)"); self.play_btn.setEnabled(False)
        
        gs = QGroupBox("Spectrum Analyzer")
        sl = QVBoxLayout(gs)
        self.spectrum = SpectrumView(); self.spectrum.setFixedHeight(170)
        sl.addWidget(self.spectrum); tab1_layout.addWidget(gs)
        tab1_layout.addStretch()
        self.tabs.addTab(tab1, "📁 Files & Preview")
        
        self._spec_timer = QTimer(self); self._spec_timer.setSingleShot(True)
        self._spec_timer.setInterval(350); self._spec_timer.timeout.connect(self.analyze_current_spectrum)
        
        # ============ TAB 2: Method ============
        tab2 = QWidget()
        tab2_layout = QVBoxLayout(tab2)
        
        mb = QGroupBox("Processing Method")
        ml = QVBoxLayout(mb)
        self.method_combo = QComboBox()
        self.method_combo.addItem("Basic Polish (Fast: EQ + Compressor only)","basic")
        self.method_combo.addItem("Advanced DSP (Full chain)","dsp")
        self.method_combo.addItem("AI Voice Isolation (Demucs)","demucs")
        def omc(i):
            m = self.method_combo.currentData()
            self.stack.setCurrentIndex(0 if m=="demucs" else 1)
            if m=="basic": self.s_denoise.setChecked(False); self.s_deess.setChecked(False)
            elif m=="dsp": self.s_denoise.setChecked(True); self.s_deess.setChecked(True)
        self.method_combo.currentIndexChanged.connect(omc); ml.addWidget(self.method_combo)
        self.demucs_status = QLabel(); ml.addWidget(self.demucs_status); self.check_demucs()
        self.stack = QStackedWidget(); ml.addWidget(self.stack)

        pd = QWidget(); fd = QFormLayout(pd)
        self.d_mode = QComboBox()
        self.d_mode.addItem("Boosted mix (1 file)","mix")
        self.d_mode.addItem("Isolated stem only (1 file)","voice")
        self.d_mode.addItem("Both (isolated + background, 2 WAV files)","both")
        self.d_mode.setCurrentIndex(2)
        fd.addRow("Output:", self.d_mode)
        
        demucs_opts = QGroupBox("Demucs Fine-Tuning")
        dol = QFormLayout(demucs_opts)
        
        self.demucs_model = QComboBox()
        for m in [("htdemucs (Default, Fast)", "htdemucs"),
                  ("htdemucs_ft (Fine-tuned, Better quality)", "htdemucs_ft"),
                  ("htdemucs_6s (6 stems)", "htdemucs_6s"),
                  ("hdemucs_mmi (Multi-Mask Instrumental)", "hdemucs_mmi"),
                  ("htcondemucs (Conditional)", "htcondemucs")]:
            self.demucs_model.addItem(m[0], m[1])
        dol.addRow("Model:", self.demucs_model)
        
        self.demucs_stems = QComboBox()
        for s in [("Vocals (voice vs rest)", "vocals"),
                  ("No Vocals (instrumental only)", "no_vocals"),
                  ("Drums", "drums"),
                  ("Bass", "bass"),
                  ("Other instruments", "other")]:
            self.demucs_stems.addItem(s[0], s[1])
        dol.addRow("Isolate stem:", self.demucs_stems)
        
        self.demucs_segment = spin(1, 15, 7, 1, " s", 0)
        dol.addRow("Segment length:", self.demucs_segment)
        
        self.demucs_shifts = spin(0, 10, 0, 1, "", 0)
        self.demucs_shifts.setToolTip("Test-Time Augmentation. 0 = fastest, higher = better quality but slower.")
        dol.addRow("Shifts (TTA):", self.demucs_shifts)
        
        self.demucs_device = QComboBox()
        for d in [("Auto (GPU if available)", "auto"),
                  ("CUDA (NVIDIA GPU)", "cuda"),
                  ("CPU (Slower, no GPU)", "cpu")]:
            self.demucs_device.addItem(d[0], d[1])
        dol.addRow("Device:", self.demucs_device)
        
        self.demucs_clip = QComboBox()
        for c in [("Rescale (Default, safest)", "rescale"),
                  ("Clamp (Hard clip, may distort)", "clamp"),
                  ("None (No clipping)", "none")]:
            self.demucs_clip.addItem(c[0], c[1])
        dol.addRow("Clip mode:", self.demucs_clip)
        
        fd.addRow(demucs_opts)
        
        # Model Cache Management
        model_mgmt = QGroupBox("Model Cache Management")
        mml = QVBoxLayout(model_mgmt)
        
        cache_info_row = QHBoxLayout()
        self.model_cache_label = QLabel(f"📁 Cache: {AI_CACHE_DIR}")
        self.model_cache_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        self.model_cache_label.setWordWrap(True)
        cache_info_row.addWidget(self.model_cache_label, 1)
        b_open_cache = QPushButton("📂 Open Folder")
        b_open_cache.setToolTip("Open the AI cache folder in Explorer")
        b_open_cache.clicked.connect(self.open_cache_folder)
        cache_info_row.addWidget(b_open_cache)
        mml.addLayout(cache_info_row)
        
        self.model_status_label = QLabel("Checking cached models...")
        self.model_status_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        self.model_status_label.setWordWrap(True)
        mml.addWidget(self.model_status_label)
        
        dl_row = QHBoxLayout()
        self.dl_model_combo = QComboBox()
        for key, desc in DEMUCS_MODELS.items():
            self.dl_model_combo.addItem(f"{key} — {desc}", key)
        dl_row.addWidget(QLabel("Model:"))
        dl_row.addWidget(self.dl_model_combo, 1)
        self.dl_model_btn = QPushButton("⬇ Download")
        self.dl_model_btn.setToolTip("Download selected model to E:\\cacheAI")
        self.dl_model_btn.clicked.connect(self.download_selected_model)
        dl_row.addWidget(self.dl_model_btn)
        self.dl_all_btn = QPushButton("⬇ Download All")
        self.dl_all_btn.setToolTip("Download all 5 models (~4 GB total)")
        self.dl_all_btn.clicked.connect(self.download_all_models)
        dl_row.addWidget(self.dl_all_btn)
        mml.addLayout(dl_row)
        
        self.dl_progress = QProgressBar()
        self.dl_progress.setVisible(False)
        mml.addWidget(self.dl_progress)
        
        fd.addRow(model_mgmt)
        self.stack.addWidget(pd)
        
        placeholder = QWidget()
        QHBoxLayout(placeholder).addWidget(QLabel("DSP options are configured in the 🎛️ DSP tab."))
        self.stack.addWidget(placeholder)
        self.stack.setCurrentIndex(1)
        
        tab2_layout.addWidget(mb)
        tab2_layout.addStretch()
        self.tabs.addTab(tab2, "🎯 Method")
        
        # ============ TAB 3: DSP ============
        tab3 = QWidget()
        tab3_layout = QVBoxLayout(tab3)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        v = QVBoxLayout(scroll_widget)
        v.setSpacing(8)

        noise_row = QHBoxLayout()
        g = QGroupBox("Noise Reduction"); f = QFormLayout(g)
        self.s_denoise = QCheckBox("Enable"); self.s_denoise.setChecked(True); f.addRow(self.s_denoise)
        self.s_den_mode = QComboBox()
        self.s_den_mode.addItem("Non-stationary",False); self.s_den_mode.addItem("Stationary",True)
        f.addRow("Type:", self.s_den_mode)
        self.s_den_amt = spin(0,1,0.8,0.05,"",2); f.addRow("Strength:",self.s_den_amt)
        self.s_den_nfft = spin(0,8192,0,256," (0=auto)",0); f.addRow("FFT size:",self.s_den_nfft)
        self.s_den_nstd = spin(0,5,0,0.1," (0=auto)",1); f.addRow("Noise std thresh:",self.s_den_nstd)
        noise_row.addWidget(g)
        
        g = QGroupBox("Noise Gate"); f = QFormLayout(g)
        self.s_gate = QCheckBox("Enable"); f.addRow(self.s_gate)
        self.s_gate_thr = spin(-80,0,-45,1," dB",0); f.addRow("Threshold:",self.s_gate_thr)
        self.s_gate_ratio = spin(1,100,10,1," :1",0); f.addRow("Ratio:",self.s_gate_ratio)
        self.s_gate_atk = spin(0.1,50,2,0.5," ms",1); f.addRow("Attack:",self.s_gate_atk)
        self.s_gate_rel = spin(10,1000,100,10," ms",0); f.addRow("Release:",self.s_gate_rel)
        noise_row.addWidget(g)
        v.addLayout(noise_row)

        g = QGroupBox("EQ / Filters"); grid = QFormLayout(g)
        row1 = QHBoxLayout()
        self.s_hpf = QCheckBox(); self.s_hpf.setChecked(True)
        self.s_hpf_freq = spin(20,300,100,5," Hz",0)
        self.s_hpf_slope = QComboBox()
        for s in (12,24,48): self.s_hpf_slope.addItem(f"{s} dB/oct",s)
        self.s_hpf_slope.setCurrentIndex(2)
        r=QHBoxLayout(); r.addWidget(self.s_hpf); r.addWidget(self.s_hpf_freq)
        r.addWidget(QLabel("Slope:")); r.addWidget(self.s_hpf_slope); r.addStretch(1)
        row1.addLayout(r)
        
        self.s_lshelf = QCheckBox()
        self.s_lshelf_freq = spin(40,500,120,10," Hz",0)
        self.s_lshelf_gain = spin(-12,12,0,0.5," dB",1)
        self.s_lshelf_q = spin(0.3,3,0.707,0.1,"",2)
        r=QHBoxLayout(); r.addWidget(self.s_lshelf); r.addWidget(self.s_lshelf_freq)
        r.addWidget(QLabel("Gain:")); r.addWidget(self.s_lshelf_gain)
        r.addWidget(QLabel("Q:")); r.addWidget(self.s_lshelf_q); r.addStretch(1)
        row1.addLayout(r)
        grid.addRow("High-pass / Low shelf:", row1)
        
        row2 = QHBoxLayout()
        self.s_mud = QCheckBox(); self.s_mud.setChecked(True)
        self.s_mud_freq = spin(100,600,300,10," Hz",0)
        self.s_mud_gain = spin(-12,0,-3,0.5," dB",1)
        self.s_mud_q = spin(0.3,5,1,0.1,"",2)
        r=QHBoxLayout(); r.addWidget(self.s_mud); r.addWidget(self.s_mud_freq)
        r.addWidget(QLabel("Gain:")); r.addWidget(self.s_mud_gain)
        r.addWidget(QLabel("Q:")); r.addWidget(self.s_mud_q); r.addStretch(1)
        row2.addLayout(r)
        
        self.s_pres = QCheckBox(); self.s_pres.setChecked(True)
        self.s_pres_freq = spin(1500,8000,3200,100," Hz",0)
        self.s_pres_gain = spin(0,12,4,0.5," dB",1)
        self.s_pres_q = spin(0.3,5,0.8,0.1,"",2)
        r=QHBoxLayout(); r.addWidget(self.s_pres); r.addWidget(self.s_pres_freq)
        r.addWidget(QLabel("Gain:")); r.addWidget(self.s_pres_gain)
        r.addWidget(QLabel("Q:")); r.addWidget(self.s_pres_q); r.addStretch(1)
        row2.addLayout(r)
        grid.addRow("Mud / Presence:", row2)
        
        row3 = QHBoxLayout()
        self.s_hshelf = QCheckBox()
        self.s_hshelf_freq = spin(4000,16000,8000,100," Hz",0)
        self.s_hshelf_gain = spin(-12,12,0,0.5," dB",1)
        self.s_hshelf_q = spin(0.3,3,0.707,0.1,"",2)
        r=QHBoxLayout(); r.addWidget(self.s_hshelf); r.addWidget(self.s_hshelf_freq)
        r.addWidget(QLabel("Gain:")); r.addWidget(self.s_hshelf_gain)
        r.addWidget(QLabel("Q:")); r.addWidget(self.s_hshelf_q); r.addStretch(1)
        row3.addLayout(r)
        
        self.s_sat = spin(0,1,0,0.05,"",2)
        row3.addWidget(QLabel("Saturation:")); row3.addWidget(self.s_sat); row3.addStretch(1)
        grid.addRow("High shelf / Saturation:", row3)
        v.addWidget(g)

        dyn_row = QHBoxLayout()
        g = QGroupBox("Compressor"); f = QFormLayout(g)
        self.s_comp = QCheckBox("Enable"); self.s_comp.setChecked(True); f.addRow(self.s_comp)
        self.s_comp_thr = spin(-60,0,-20,1," dB",0); f.addRow("Threshold:",self.s_comp_thr)
        self.s_comp_ratio = spin(1,20,3,0.5," :1",1); f.addRow("Ratio:",self.s_comp_ratio)
        r=QHBoxLayout()
        self.s_comp_atk = spin(0.5,200,30,5," ms",1)
        self.s_comp_rel = spin(10,1000,150,10," ms",0)
        r.addWidget(QLabel("Attack:")); r.addWidget(self.s_comp_atk)
        r.addWidget(QLabel("Release:")); r.addWidget(self.s_comp_rel); f.addRow(r)
        r=QHBoxLayout()
        self.s_comp_knee = spin(0,24,6,1," dB",0)
        self.s_comp_makeup = spin(0,24,6,0.5," dB",1)
        r.addWidget(QLabel("Knee:")); r.addWidget(self.s_comp_knee)
        r.addWidget(QLabel("Makeup:")); r.addWidget(self.s_comp_makeup); f.addRow(r)
        r=QHBoxLayout()
        self.s_comp_look = spin(0,20,0,0.5," ms",1)
        self.s_comp_auto_mu = QCheckBox("Auto")
        r.addWidget(QLabel("Lookahead:")); r.addWidget(self.s_comp_look)
        r.addWidget(QLabel("Auto-makeup:")); r.addWidget(self.s_comp_auto_mu); f.addRow(r)
        dyn_row.addWidget(g)
        
        g = QGroupBox("De-esser"); f = QFormLayout(g)
        self.s_deess = QCheckBox("Enable"); f.addRow(self.s_deess)
        self.s_deess_freq = spin(4000,9000,6500,100," Hz",0); f.addRow("Frequency:",self.s_deess_freq)
        self.s_deess_thr = spin(-60,0,-35,1," dB",0); f.addRow("Threshold:",self.s_deess_thr)
        self.s_deess_amt = spin(0,18,8,1," dB",0); f.addRow("Max reduction:",self.s_deess_amt)
        self.s_deess_ratio = spin(1,20,3,0.5,":1",1); f.addRow("Ratio:",self.s_deess_ratio)
        self.s_deess_atk = spin(0.5,50,5,1," ms",1); f.addRow("Attack:",self.s_deess_atk)
        self.s_deess_rel = spin(10,500,50,5," ms",0); f.addRow("Release:",self.s_deess_rel)
        dyn_row.addWidget(g)
        v.addLayout(dyn_row)

        scroll.setWidget(scroll_widget)
        tab3_layout.addWidget(scroll)
        self.tabs.addTab(tab3, "🎛️ DSP")
        
        # ============ TAB 4: Level ============
        tab4 = QWidget()
        tab4_layout = QVBoxLayout(tab4)
        
        gl = QGroupBox("Output Level"); fl5 = QFormLayout(gl)
        self.boost = spin(-24,24,4,0.5," dB",1); fl5.addRow("Voice boost:",self.boost)
        r=QHBoxLayout(); self.norm = QCheckBox("Normalize"); self.norm.setChecked(True)
        self.norm_mode = QComboBox()
        self.norm_mode.addItem("RMS","rms"); self.norm_mode.addItem("Peak","peak")
        self.norm_mode.addItem("LUFS","lufs")
        self.norm_target = spin(-30,-6,-16,0.5," dBFS",1)
        self.norm_max = spin(0,24,12,1," dB",0)
        self.norm_ceil = spin(-3,0,-0.2,0.1," dBTP",1)
        r.addWidget(self.norm); r.addWidget(self.norm_mode)
        r.addWidget(QLabel("to")); r.addWidget(self.norm_target)
        r.addWidget(QLabel("max +")); r.addWidget(self.norm_max)
        r.addWidget(QLabel("ceiling")); r.addWidget(self.norm_ceil); r.addStretch(1)
        fl5.addRow(r)

        lim_fade_row = QHBoxLayout()
        r=QHBoxLayout(); self.s_lim = QCheckBox("Limiter enable")
        self.s_lim_ceil = spin(-6,0,-1,0.1," dBFS",1)
        self.s_lim_rel = spin(10,500,100,10," ms",0)
        r.addWidget(self.s_lim); r.addWidget(QLabel("Ceiling:")); r.addWidget(self.s_lim_ceil)
        r.addWidget(QLabel("Release:")); r.addWidget(self.s_lim_rel); r.addStretch(1)
        lim_fade_row.addLayout(r)
        
        r=QHBoxLayout()
        self.s_fade_in = spin(0,5000,0,50," ms",0)
        self.s_fade_out = spin(0,5000,0,50," ms",0)
        r.addWidget(QLabel("Fade in:")); r.addWidget(self.s_fade_in)
        r.addWidget(QLabel("Fade out:")); r.addWidget(self.s_fade_out); r.addStretch(1)
        lim_fade_row.addLayout(r)
        fl5.addRow(lim_fade_row)
        tab4_layout.addWidget(gl)
        tab4_layout.addStretch()
        self.tabs.addTab(tab4, "🔊 Level")
        
        # ============ TAB 5: Batch ============
        tab5 = QWidget()
        tab5_layout = QVBoxLayout(tab5)
        
        gb = QGroupBox("Batch Queue")
        bl = QVBoxLayout(gb)
        bor = QHBoxLayout(); self.batch_out_dir = QLineEdit()
        self.batch_out_dir.setPlaceholderText("Optional: output directory for batch (defaults to CWD)")
        bbo = QPushButton("Browse Folder..."); bbo.clicked.connect(self.browse_batch_output)
        bor.addWidget(QLabel("Output Folder:")); bor.addWidget(self.batch_out_dir,1)
        bor.addWidget(bbo); bl.addLayout(bor)
        
        self.queue_list = QListWidget()
        self.queue_list.setDragDropMode(QListWidget.InternalMove)
        self.queue_list.setDefaultDropAction(Qt.MoveAction)
        bl.addWidget(self.queue_list)
        
        br1 = QHBoxLayout()
        b_add = QPushButton("📄 Add Files..."); b_add.setToolTip("Add individual audio files"); b_add.clicked.connect(self.batch_add); br1.addWidget(b_add)
        b_folder = QPushButton("📁 Add Folder..."); b_folder.setToolTip("Recursively scan a folder for audio files"); b_folder.clicked.connect(self.batch_add_folder); br1.addWidget(b_folder)
        b_pattern = QPushButton("🔍 Add by Pattern..."); b_pattern.setToolTip("Use glob patterns like E:\\path\\**\\*.wav"); b_pattern.clicked.connect(self.batch_add_pattern); br1.addWidget(b_pattern)
        b_paste = QPushButton("📋 Paste Paths"); b_paste.setToolTip("Paste paths from clipboard (handles quotes, URLs, etc.)"); b_paste.clicked.connect(self.batch_paste_paths); br1.addWidget(b_paste)
        b_remove = QPushButton("❌ Remove"); b_remove.clicked.connect(self.batch_remove); br1.addWidget(b_remove)
        b_clear = QPushButton("🗑 Clear"); b_clear.clicked.connect(self.batch_clear); br1.addWidget(b_clear)
        b_validate = QPushButton("✓ Validate"); b_validate.clicked.connect(self.validate_batch); br1.addWidget(b_validate)
        br1.addStretch(1)
        self.batch_run_btn = QPushButton("▶ Process Queue")
        self.batch_run_btn.setToolTip("Ctrl+Shift+R")
        self.batch_run_btn.clicked.connect(self.start_batch); br1.addWidget(self.batch_run_btn)
        bl.addLayout(br1)
        
        help_lbl = QLabel("💡 Tip: Drag & drop files or folders directly onto this window. Paste paths with Ctrl+V.")
        help_lbl.setStyleSheet("color: #a6adc8; font-size: 11px; padding: 4px;")
        help_lbl.setWordWrap(True)
        bl.addWidget(help_lbl)
        
        tab5_layout.addWidget(gb)
        self.tabs.addTab(tab5, "📦 Batch")
        
        # ============ TAB 6: Log & Run ============
        tab6 = QWidget()
        tab6_layout = QVBoxLayout(tab6)
        
        sr = QHBoxLayout()
        be = QPushButton("Export JSON..."); be.clicked.connect(self.export_settings); sr.addWidget(be)
        bimp = QPushButton("Import JSON..."); bimp.clicked.connect(self.import_settings); sr.addWidget(bimp)
        blog = QPushButton("Export Log..."); blog.clicked.connect(self.export_log); sr.addWidget(blog)
        sr.addStretch(1)
        self.reset_btn = QPushButton("↺ Defaults"); self.reset_btn.clicked.connect(self.reset_defaults)
        sr.addWidget(self.reset_btn)
        tab6_layout.addLayout(sr)
        
        rr = QHBoxLayout()
        self.run_btn = QPushButton("Process"); self.run_btn.setMinimumHeight(40)
        self.run_btn.setToolTip("Ctrl+R"); self.run_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel"); self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_job)
        self.cancel_btn.setStyleSheet("background-color: #f38ba8; color: #1e1e2e; font-weight: bold;")
        self.progress = QProgressBar(); self.progress.setVisible(False); self.progress.setMinimumWidth(200)
        rr.addWidget(self.run_btn,0); rr.addWidget(self.cancel_btn,0); rr.addWidget(self.progress,1)
        tab6_layout.addLayout(rr)
        
        ir2 = QHBoxLayout()
        self.step_label = QLabel(""); self.step_label.setStyleSheet("color:#a6e3a1;font-weight:bold;")
        self.step_label.setMinimumWidth(260)
        self.time_info_label = QLabel(""); self.time_info_label.setStyleSheet("color:#a6adc8;")
        self.time_info_label.setAlignment(Qt.AlignRight)
        ir2.addWidget(self.step_label,1); ir2.addWidget(self.time_info_label,0)
        tab6_layout.addLayout(ir2)
        self._time_timer = QTimer(self); self._time_timer.setInterval(500)
        self._time_timer.timeout.connect(self._update_time_info)
        
        self.log_box = QPlainTextEdit(); self.log_box.setReadOnly(True)
        tab6_layout.addWidget(self.log_box,1)
        
        self.tabs.addTab(tab6, "📋 Log & Run")
        
        # Shortcuts
        sc = QShortcut(QKeySequence(Qt.Key_Space), self); sc.setContext(Qt.WindowShortcut)
        sc.activated.connect(self.toggle_play_guarded)
        self.run_btn.setShortcut("Ctrl+R"); self.preview_btn.setShortcut("Ctrl+P")
        self.batch_run_btn.setShortcut("Ctrl+Shift+R"); bi.setShortcut("Ctrl+O")
        QShortcut(QKeySequence("Ctrl+V"), self).activated.connect(self.batch_paste_paths)
        QShortcut(QKeySequence("Ctrl+Shift+A"), self).activated.connect(self.batch_add_folder)

        # Widget registry
        self._widgets = {
            "fmt":self.fmt_combo,"bitrate":self.br_combo,"method":self.method_combo,
            "demucs/mode":self.d_mode,
            "demucs/model":self.demucs_model,"demucs/stems":self.demucs_stems,
            "demucs/segment":self.demucs_segment,"demucs/shifts":self.demucs_shifts,
            "demucs/device":self.demucs_device,"demucs/clip":self.demucs_clip,
            "level/boost":self.boost,"level/norm":self.norm,
            "level/norm_mode":self.norm_mode,"level/norm_target":self.norm_target,
            "level/norm_max":self.norm_max,"level/norm_ceil":self.norm_ceil,
            "nr/en":self.s_denoise,"nr/mode":self.s_den_mode,"nr/amt":self.s_den_amt,
            "nr/nfft":self.s_den_nfft,"nr/nstd":self.s_den_nstd,
            "gate/en":self.s_gate,"gate/thr":self.s_gate_thr,"gate/ratio":self.s_gate_ratio,
            "gate/atk":self.s_gate_atk,"gate/rel":self.s_gate_rel,
            "eq/hpf_en":self.s_hpf,"eq/hpf_freq":self.s_hpf_freq,"eq/hpf_slope":self.s_hpf_slope,
            "eq/lshelf_en":self.s_lshelf,"eq/lshelf_freq":self.s_lshelf_freq,
            "eq/lshelf_gain":self.s_lshelf_gain,"eq/lshelf_q":self.s_lshelf_q,
            "eq/mud_en":self.s_mud,"eq/mud_freq":self.s_mud_freq,
            "eq/mud_gain":self.s_mud_gain,"eq/mud_q":self.s_mud_q,
            "eq/pres_en":self.s_pres,"eq/pres_freq":self.s_pres_freq,
            "eq/pres_gain":self.s_pres_gain,"eq/pres_q":self.s_pres_q,
            "eq/hshelf_en":self.s_hshelf,"eq/hshelf_freq":self.s_hshelf_freq,
            "eq/hshelf_gain":self.s_hshelf_gain,"eq/hshelf_q":self.s_hshelf_q,
            "sat/amt":self.s_sat,
            "ds/en":self.s_deess,"ds/freq":self.s_deess_freq,"ds/thr":self.s_deess_thr,
            "ds/amt":self.s_deess_amt,"ds/ratio":self.s_deess_ratio,
            "ds/atk":self.s_deess_atk,"ds/rel":self.s_deess_rel,
            "cp/en":self.s_comp,"cp/thr":self.s_comp_thr,"cp/ratio":self.s_comp_ratio,
            "cp/atk":self.s_comp_atk,"cp/rel":self.s_comp_rel,"cp/knee":self.s_comp_knee,
            "cp/makeup":self.s_comp_makeup,"cp/look":self.s_comp_look,
            "cp/auto_mu":self.s_comp_auto_mu,
            "lim/en":self.s_lim,"lim/ceil":self.s_lim_ceil,"lim/rel":self.s_lim_rel,
            "fade/in":self.s_fade_in,"fade/out":self.s_fade_out,
        }
        self._defaults = {k: self._get(w) for k, w in self._widgets.items()}

        eq_widgets = (self.s_hpf,self.s_hpf_freq,self.s_hpf_slope,
            self.s_lshelf,self.s_lshelf_freq,self.s_lshelf_gain,self.s_lshelf_q,
            self.s_mud,self.s_mud_freq,self.s_mud_gain,self.s_mud_q,
            self.s_pres,self.s_pres_freq,self.s_pres_gain,self.s_pres_q,
            self.s_hshelf,self.s_hshelf_freq,self.s_hshelf_gain,self.s_hshelf_q)
        for w in eq_widgets:
            if isinstance(w, QComboBox): w.currentIndexChanged.connect(self.update_eq_curve)
            elif isinstance(w, QCheckBox): w.toggled.connect(self.update_eq_curve)
            else: w.valueChanged.connect(self.update_eq_curve)

        self.load_settings()
        self._load_recent_files()
        self._refresh_recent_combo()
        geo = self.settings.value("ui/geometry")
        if geo:
            try: self.restoreGeometry(geo)
            except: pass
        self.update_eq_curve()
        self.update_file_info()
        self.update_estimated_size()
        self.refresh_model_status()
        self.log(f"Ready. Output will be saved to: {os.getcwd()}")
        self.log(f"AI models cached in: {AI_CACHE_DIR}")

    def check_demucs(self):
        if shutil.which("demucs"):
            self.demucs_status.setText("✓ demucs: found"); self.demucs_status.setStyleSheet("color:#a6e3a1;")
        else:
            self.demucs_status.setText("✗ demucs: NOT installed (pip install demucs)"); self.demucs_status.setStyleSheet("color:#f38ba8;")
    
    def check_ffmpeg(self):
        if shutil.which("ffmpeg"):
            self.ffmpeg_status.setText("✓ ffmpeg: found"); self.ffmpeg_status.setStyleSheet("color:#a6e3a1;")
        else:
            self.ffmpeg_status.setText("✗ ffmpeg: not found"); self.ffmpeg_status.setStyleSheet("color:#fab387;")

    # ------------------------------------------------------------------
    # Model Cache Management
    # ------------------------------------------------------------------
    def refresh_model_status(self):
        info = get_model_cache_info()
        if not info["exists"]:
            self.model_status_label.setText(f"⚠ Cache dir not found: {AI_CACHE_DIR}")
            self.model_status_label.setStyleSheet("color: #fab387; font-size: 11px;")
            return
        
        cached_models = []
        for model_key in DEMUCS_MODELS:
            if is_model_cached(model_key):
                cached_models.append(model_key)
        
        if cached_models:
            total = info["total_size_mb"]
            self.model_status_label.setText(
                f"✅ {len(cached_models)}/{len(DEMUCS_MODELS)} models cached "
                f"({total:.0f} MB total): {', '.join(cached_models)}"
            )
            self.model_status_label.setStyleSheet("color: #a6e3a1; font-size: 11px;")
        else:
            self.model_status_label.setText(
                f"⚠ No models cached yet. Click 'Download' to fetch models to {AI_CACHE_DIR}"
            )
            self.model_status_label.setStyleSheet("color: #fab387; font-size: 11px;")

    def open_cache_folder(self):
        os.makedirs(AI_CACHE_DIR, exist_ok=True)
        os.startfile(AI_CACHE_DIR)

    def download_selected_model(self):
        model_name = self.dl_model_combo.currentData()
        if not model_name:
            return
        
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "Processing is running. Wait for it to finish.")
            return
        
        self.dl_model_btn.setEnabled(False)
        self.dl_all_btn.setEnabled(False)
        self.dl_progress.setVisible(True)
        self.dl_progress.setValue(0)
        self.dl_progress.setRange(0, 100)
        
        self._dl_thread = ModelDownloadThread(model_name)
        self._dl_thread.log_msg.connect(self.log)
        self._dl_thread.progress.connect(self.dl_progress.setValue)
        self._dl_thread.finished_ok.connect(self._on_model_dl_done)
        self._dl_thread.failed.connect(self._on_model_dl_error)
        self._dl_thread.start()

    def download_all_models(self):
        reply = QMessageBox.question(
            self, "Download All Models",
            f"This will download all {len(DEMUCS_MODELS)} models to:\n{AI_CACHE_DIR}\n\n"
            f"Total size: ~4 GB. This may take 10-30 minutes.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return
        
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Busy", "Processing is running. Wait for it to finish.")
            return
        
        self.dl_model_btn.setEnabled(False)
        self.dl_all_btn.setEnabled(False)
        self.dl_progress.setVisible(True)
        self.dl_progress.setValue(0)
        self.dl_progress.setRange(0, 0)
        
        self._dl_thread = ModelDownloadThread(list(DEMUCS_MODELS.keys()))
        self._dl_thread.log_msg.connect(self.log)
        self._dl_thread.progress.connect(self.dl_progress.setValue)
        self._dl_thread.finished_ok.connect(self._on_model_dl_done)
        self._dl_thread.failed.connect(self._on_model_dl_error)
        self._dl_thread.start()

    def _on_model_dl_done(self, msg):
        self.dl_model_btn.setEnabled(True)
        self.dl_all_btn.setEnabled(True)
        self.dl_progress.setVisible(False)
        self.log(f"✅ {msg}")
        self.refresh_model_status()
        QMessageBox.information(self, "Download Complete", msg)

    def _on_model_dl_error(self, msg):
        self.dl_model_btn.setEnabled(True)
        self.dl_all_btn.setEnabled(True)
        self.dl_progress.setVisible(False)
        self.log(f"❌ {msg}")
        self.refresh_model_status()

    @staticmethod
    def _get(w):
        if isinstance(w,QDoubleSpinBox): return w.value()
        if isinstance(w,QCheckBox): return w.isChecked()
        if isinstance(w,QComboBox): return w.currentIndex()
        if isinstance(w,QLineEdit): return w.text()
        return None
    
    @staticmethod
    def _set(w,v):
        if v is None: return
        try:
            if isinstance(w,QDoubleSpinBox): w.setValue(float(v))
            elif isinstance(w,QCheckBox): w.setChecked(v if isinstance(v,bool) else str(v).lower() in ("true","1"))
            elif isinstance(w,QComboBox): w.setCurrentIndex(int(v))
            elif isinstance(w,QLineEdit): w.setText(str(v))
        except: pass

    def load_settings(self):
        for k,w in self._widgets.items():
            self._set(w, self.settings.value(k))
    
    def save_settings(self):
        for k,w in self._widgets.items(): self.settings.setValue(k, self._get(w))
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self.settings.remove("files/input")
        self.settings.remove("files/output")
    
    def reset_defaults(self):
        for k,v in self._defaults.items(): self._set(self._widgets[k],v)
        self.log("All settings reset to defaults.")
    
    def export_settings(self):
        path,_ = QFileDialog.getSaveFileName(self,"Export","voiceboost_settings.json","JSON (*.json)")
        if not path: return
        data = {k:self._get(w) for k,w in self._widgets.items()}
        try:
            with open(path,"w",encoding="utf-8") as f: json.dump(data,f,indent=2)
            self.log(f"Settings exported: {path}")
        except OSError as e: QMessageBox.warning(self,"Export failed",str(e))
    
    def import_settings(self):
        path,_ = QFileDialog.getOpenFileName(self,"Import","","JSON (*.json)")
        if not path or not os.path.isfile(path): return
        try:
            with open(path,"r",encoding="utf-8") as f: data=json.load(f)
        except Exception as e: QMessageBox.warning(self,"Import failed",str(e)); return
        n=0
        for k,val in data.items():
            if k in self._widgets: self._set(self._widgets[k],val); n+=1
        self.log(f"Imported {n} settings from {os.path.basename(path)}")
    
    def export_log(self):
        path,_ = QFileDialog.getSaveFileName(self,"Export Log","voicebooster_log.txt","Text (*.txt)")
        if not path: return
        try:
            shutil.copy(LOG_FILE, path)
            self.log(f"Log exported: {path}")
        except Exception as e:
            QMessageBox.warning(self,"Export failed",str(e))

    def fmt_changed(self,_): 
        self.br_combo.setEnabled(self.fmt_combo.currentData()=="mp3")
        self.fix_out_ext()
        self.update_estimated_size()
    
    def fix_out_ext(self):
        w = ".mp3" if self.fmt_combo.currentData()=="mp3" else ".wav"
        p = self.output_edit.text().strip()
        if not p: return
        b,e = os.path.splitext(p)
        if e.lower() in (".wav",".mp3") and e.lower()!=w: self.output_edit.setText(b+w)
    
    def browse_input(self):
        p,_ = QFileDialog.getOpenFileName(self,"Select input","","Audio (*.wav *.aif *.aiff *.mp3 *.flac *.ogg *.oga *.m4a *.opus *.wma);;All (*)")
        if p and os.path.isfile(p) and os.access(p,os.R_OK):
            self._set_input_path(p)
        elif p: self.log(f"Error: Cannot read {p}")
    
    def _set_input_path(self, p):
        self.input_edit.setText(p)
        self.output_edit.setText(auto_out_path(p, self.fmt_combo.currentData()))
        self._add_recent_file(p)
        self.update_file_info()
        self.update_estimated_size()
    
    def browse_output(self):
        p,_ = QFileDialog.getSaveFileName(self,"Save output",self.output_edit.text() or os.getcwd(),"Audio (*.wav *.mp3)")
        if p: 
            self.output_edit.setText(p)
            self.update_estimated_size()
    
    def browse_batch_output(self):
        p = QFileDialog.getExistingDirectory(self,"Batch output folder", os.getcwd())
        if p: self.batch_out_dir.setText(p)

    # ------------------------------------------------------------------
    # Recent files management
    # ------------------------------------------------------------------
    def _add_recent_file(self, path):
        path = os.path.abspath(path)
        if path in self._recent_files:
            self._recent_files.remove(path)
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[:MAX_RECENT_FILES]
        self.settings.setValue("recent_files", self._recent_files)
        self._refresh_recent_combo()
    
    def _load_recent_files(self):
        stored = self.settings.value("recent_files", [])
        if isinstance(stored, str):
            stored = [stored]
        self._recent_files = [p for p in stored if os.path.isfile(p)][:MAX_RECENT_FILES]
    
    def _refresh_recent_combo(self):
        self.recent_combo.blockSignals(True)
        self.recent_combo.clear()
        self.recent_combo.addItem("— Recent files —", None)
        for p in self._recent_files:
            display = f"{os.path.basename(p)}  ({os.path.dirname(p)})"
            if len(display) > 80:
                display = f"...{display[-77:]}"
            self.recent_combo.addItem(display, p)
        self.recent_combo.blockSignals(False)
    
    def _on_recent_selected(self, idx):
        path = self.recent_combo.currentData()
        if path and os.path.isfile(path):
            self._set_input_path(path)
        self.recent_combo.blockSignals(True)
        self.recent_combo.setCurrentIndex(0)
        self.recent_combo.blockSignals(False)
    
    # ------------------------------------------------------------------
    # Enhanced batch file adding
    # ------------------------------------------------------------------
    def log(self,m): self.log_box.appendPlainText(m); logging.info(m)

    def _categorized_add(self, paths, source_label=""):
        if not paths:
            self.log(f"{source_label}No paths provided.")
            return
        
        have = {self.queue_list.item(i).text() for i in range(self.queue_list.count())}
        
        added = 0
        reasons = {"wrong_ext": 0, "not_found": 0, "not_readable": 0, "duplicate": 0, "not_file": 0}
        
        for raw_p in paths:
            p = _clean_single_path(raw_p) if isinstance(raw_p, str) else raw_p
            if not p:
                continue
            p_abs = os.path.abspath(p)
            
            if os.path.isdir(p_abs):
                sub_files = scan_folder(p_abs, recursive=True)
                self.log(f"  📁 Expanding folder: {os.path.basename(p_abs)} ({len(sub_files)} audio files)")
                for sf_path in sub_files:
                    sf_abs = os.path.abspath(sf_path)
                    if sf_abs in have:
                        reasons["duplicate"] += 1
                    else:
                        self.queue_list.addItem(sf_abs)
                        have.add(sf_abs)
                        added += 1
                continue
            
            if not os.path.exists(p_abs):
                reasons["not_found"] += 1
                continue
            if not os.path.isfile(p_abs):
                reasons["not_file"] += 1
                continue
            if not os.access(p_abs, os.R_OK):
                reasons["not_readable"] += 1
                continue
            if not p_abs.lower().endswith(INPUT_EXTS):
                reasons["wrong_ext"] += 1
                continue
            if p_abs in have:
                reasons["duplicate"] += 1
                continue
            
            self.queue_list.addItem(p_abs)
            have.add(p_abs)
            added += 1
        
        total_in = len(paths)
        skipped = total_in - added
        
        msg_parts = [f"{source_label}Queue: +{added} added, {skipped} skipped. Total: {self.queue_list.count()}"]
        if any(reasons.values()):
            details = []
            if reasons["wrong_ext"]: details.append(f"{reasons['wrong_ext']} wrong extension")
            if reasons["duplicate"]: details.append(f"{reasons['duplicate']} already in queue")
            if reasons["not_found"]: details.append(f"{reasons['not_found']} not found")
            if reasons["not_readable"]: details.append(f"{reasons['not_readable']} not readable")
            if reasons["not_file"]: details.append(f"{reasons['not_file']} not a file")
            msg_parts.append("  Skipped: " + ", ".join(details))
        
        self.log("\n".join(msg_parts))

    def batch_add(self):
        p,_ = QFileDialog.getOpenFileNames(self,"Add audio files","","Audio (*.wav *.aif *.aiff *.mp3 *.flac *.ogg *.oga *.m4a *.opus *.wma);;All (*)")
        self._categorized_add(p, "Add Files: ")
    
    def batch_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to scan", os.getcwd())
        if not folder:
            return
        
        reply = QMessageBox.question(
            self, "Scan subfolders?",
            f"Scan subfolders of:\n{folder}\n\nClick 'Yes' for recursive scan (all subfolders).\nClick 'No' for top-level folder only.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes
        )
        if reply == QMessageBox.Cancel:
            return
        recursive = (reply == QMessageBox.Yes)
        
        self.log(f"📁 Scanning folder: {folder} ({'recursive' if recursive else 'top-level only'})...")
        found = scan_folder(folder, recursive=recursive)
        if not found:
            self.log(f"  ⚠ No audio files found in {folder}")
            QMessageBox.information(self, "No files found", f"No supported audio files found in:\n{folder}")
            return
        
        self.log(f"  Found {len(found)} audio file(s). Adding to queue...")
        self._categorized_add(found, "Add Folder: ")

    def batch_add_pattern(self):
        examples = "E:\\Videos\\**\\*.wav\nE:\\Videos\\*.mp3\nE:\\Videos\\day_??_*.flac"
        pattern, ok = QInputDialog.getText(
            self, "Add by Pattern",
            f"Enter glob pattern (supports * and ** for recursive):\n\nExamples:\n{examples}",
            text=""
        )
        if not ok or not pattern.strip():
            return
        
        self.log(f"🔍 Expanding pattern: {pattern}")
        matches = expand_glob_pattern(pattern)
        if not matches:
            self.log(f"  ⚠ No audio files matched pattern: {pattern}")
            QMessageBox.information(self, "No matches", f"No audio files matched:\n{pattern}")
            return
        
        self.log(f"  Matched {len(matches)} file(s). Adding to queue...")
        self._categorized_add(matches, "Add Pattern: ")
    
    def batch_paste_paths(self):
        t = QApplication.clipboard().text()
        if not t or not t.strip():
            self.log("📋 Clipboard is empty.")
            QMessageBox.information(self, "Clipboard Empty", "No text found in clipboard.")
            return
        
        paths = parse_path_list(t)
        if not paths:
            self.log("📋 No valid paths found in clipboard text.")
            return
        
        self.log(f"📋 Parsed {len(paths)} path(s) from clipboard. Adding to queue...")
        self._categorized_add(paths, "Paste: ")
    
    def batch_remove(self):
        for i in self.queue_list.selectedItems(): self.queue_list.takeItem(self.queue_list.row(i))
    
    def batch_clear(self): 
        if self.queue_list.count() == 0:
            return
        reply = QMessageBox.question(self, "Clear Queue", 
                                     f"Remove all {self.queue_list.count()} items from queue?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.queue_list.clear()
            self.log("🗑 Queue cleared.")
    
    def validate_batch(self):
        if self.queue_list.count() == 0:
            QMessageBox.information(self, "Validate", "Queue is empty.")
            return
        valid = 0
        reasons = {"not_found": 0, "not_readable": 0, "wrong_ext": 0}
        invalid_samples = []
        for i in range(self.queue_list.count()):
            path = self.queue_list.item(i).text()
            if not os.path.exists(path):
                reasons["not_found"] += 1
                if len(invalid_samples) < 5: invalid_samples.append(path)
            elif not os.path.isfile(path):
                reasons["not_found"] += 1
                if len(invalid_samples) < 5: invalid_samples.append(path)
            elif not os.access(path, os.R_OK):
                reasons["not_readable"] += 1
                if len(invalid_samples) < 5: invalid_samples.append(path)
            elif not path.lower().endswith(INPUT_EXTS):
                reasons["wrong_ext"] += 1
                if len(invalid_samples) < 5: invalid_samples.append(path)
            else:
                valid += 1
        
        if valid == self.queue_list.count():
            QMessageBox.information(self, "Validation", f"✓ All {valid} files are valid and readable.")
            self.log(f"✓ Validation passed: {valid} files OK")
        else:
            msg = f"✓ {valid} valid, ✗ {self.queue_list.count() - valid} invalid:\n\n"
            if reasons["not_found"]: msg += f"  • {reasons['not_found']} not found\n"
            if reasons["not_readable"]: msg += f"  • {reasons['not_readable']} not readable\n"
            if reasons["wrong_ext"]: msg += f"  • {reasons['wrong_ext']} wrong extension\n"
            msg += "\nExamples:\n"
            for p in invalid_samples:
                msg += f"  • {os.path.basename(p)}\n"
            QMessageBox.warning(self, "Validation", msg)

    def start_batch(self):
        if self.worker and self.worker.isRunning(): QMessageBox.information(self,"Busy","Running."); return
        if self.queue_list.count()==0: QMessageBox.information(self,"Batch","Empty queue. Add files first."); return
        if self.method_combo.currentData()=="demucs" and not shutil.which("demucs"):
            QMessageBox.warning(self,"Missing","demucs not installed."); return
        fmt = self.fmt_combo.currentData(); bd = self.batch_out_dir.text().strip()
        if bd and not os.path.isdir(bd): QMessageBox.warning(self,"Bad dir",bd); return
        jobs = []
        for i in range(self.queue_list.count()):
            inp = self.queue_list.item(i).text(); n=os.path.splitext(os.path.basename(inp))[0]
            ext = ".mp3" if fmt=="mp3" else ".wav"
            out = os.path.join(bd, f"{n}_voiceboost{ext}") if bd and os.path.isdir(bd) else auto_out_path(inp, fmt)
            jobs.append((inp,out))
        ex = [o for _,o in jobs if os.path.isfile(o)]
        if ex and QMessageBox.question(self,"Overwrite?",f"{len(ex)} output file(s) already exist. Continue?",QMessageBox.Yes|QMessageBox.No)!=QMessageBox.Yes: return
        self._batching=True; self._batch_failed=[]; self._set_ui_busy(True); self._start_job_tracking()
        self.log("="*60); self.log(f"Batch: {len(jobs)} file(s)")
        p = self.collect_params(); p["batch"]=jobs; self._launch_worker(p)
    
    def on_file_done(self,p,ok):
        if not ok: self._batch_failed.append(p)
        self.log(("OK: " if ok else "FAILED: ")+os.path.basename(p))

    def load_play_source(self):
        if not getattr(self,"player",None): return
        k = self.play_src.currentData()
        p = self.input_edit.text().strip() if k=="input" else self.output_edit.text().strip()
        if k=="input" and p and (not os.path.isfile(p) or not os.access(p,os.R_OK)):
            self.play_btn.setEnabled(False); self.stop_btn.setEnabled(False); return
        if p and os.path.isfile(p):
            try: 
                self.player.setSource(QUrl.fromLocalFile(p))
                self.play_btn.setEnabled(True); self.stop_btn.setEnabled(True)
                if k == "input":
                    self.update_file_info()
            except: 
                self.play_btn.setEnabled(False); self.stop_btn.setEnabled(False)
        else:
            self.player.setSource(QUrl()); self.play_btn.setEnabled(False); self.stop_btn.setEnabled(False)
            self.seek.setRange(0,0); self.time_lbl.setText("0:00 / 0:00")
        if hasattr(self,"ab_btn"): self.ab_btn.setEnabled(os.path.isfile(self.input_edit.text().strip()) and os.path.isfile(self.output_edit.text().strip()))
        if hasattr(self,"preview_btn"): self.preview_btn.setEnabled(os.path.isfile(self.input_edit.text().strip()))
        if hasattr(self,"_spec_timer"): self._spec_timer.start()
    
    def toggle_play_guarded(self):
        if isinstance(self.focusWidget(),FOCUS_BLOCKERS): return
        self.toggle_play()
    
    def toggle_play(self):
        if not self.player: return
        if self.player.playbackState()==QMediaPlayer.PlayingState: self.player.pause()
        else: self.player.play()
    
    def stop_playback(self):
        if self.player: self.player.stop(); self.player.setPosition(0)
    
    def ab_swap(self):
        if not self.player: return
        o = 0 if self.play_src.currentIndex()==1 else 1
        p = self.input_edit.text().strip() if o==0 else self.output_edit.text().strip()
        if not(p and os.path.isfile(p)): return
        wp = self.player.playbackState()==QMediaPlayer.PlayingState; pos=self.player.position()
        self.play_src.blockSignals(True); self.play_src.setCurrentIndex(o); self.play_src.blockSignals(False)
        self.load_play_source(); self.player.setPosition(pos)
        if wp: self.player.play()
    
    def on_playstate(self,s): self.play_btn.setText("⏸ Pause" if s==QMediaPlayer.PlayingState else "▶ Play")
    
    def on_media_status(self,s):
        if s==QMediaPlayer.LoadedMedia and self._pending_seek is not None: 
            self.player.setPosition(self._pending_seek); self._pending_seek=None
        if s==QMediaPlayer.EndOfMedia: 
            self.player.setPosition(0); self.seek.setValue(0)
    
    def on_position(self,p):
        if not self.seek.isSliderDown(): self.seek.setValue(p)
        self.time_lbl.setText(f"{ms_to_str(p)} / {ms_to_str(self.player.duration())}")
    
    def on_duration(self,d): self.seek.setRange(0,max(int(d),1))

    def collect_params(self):
        return {
            "input":self.input_edit.text().strip(),"output":self.output_edit.text().strip(),
            "method":self.method_combo.currentData(),"fmt":self.fmt_combo.currentData(),
            "bitrate":self.br_combo.currentData(),"boost":self.boost.value(),
            "normalize":self.norm.isChecked(),"norm_mode":self.norm_mode.currentData(),
            "norm_target":self.norm_target.value(),"norm_max":self.norm_max.value(),
            "norm_ceil":self.norm_ceil.value(),
            "mode":self.d_mode.currentData(),
            "demucs_model":self.demucs_model.currentData(),
            "demucs_stems":self.demucs_stems.currentData(),
            "demucs_segment":int(self.demucs_segment.value()),
            "demucs_shifts":int(self.demucs_shifts.value()),
            "demucs_device":self.demucs_device.currentData(),
            "demucs_clip":self.demucs_clip.currentData(),
            "denoise":self.s_denoise.isChecked(),"den_mode":self.s_den_mode.currentData(),
            "den_amt":self.s_den_amt.value(),"den_nfft":self.s_den_nfft.value(),
            "den_nstd":self.s_den_nstd.value(),
            "gate":self.s_gate.isChecked(),"gate_thr":self.s_gate_thr.value(),
            "gate_ratio":self.s_gate_ratio.value(),"gate_atk":self.s_gate_atk.value(),
            "gate_rel":self.s_gate_rel.value(),
            "hpf":self.s_hpf.isChecked(),"hpf_freq":self.s_hpf_freq.value(),
            "hpf_slope":self.s_hpf_slope.currentData(),
            "lshelf":self.s_lshelf.isChecked(),"lshelf_freq":self.s_lshelf_freq.value(),
            "lshelf_gain":self.s_lshelf_gain.value(),"lshelf_q":self.s_lshelf_q.value(),
            "mud":self.s_mud.isChecked(),"mud_freq":self.s_mud_freq.value(),
            "mud_gain":self.s_mud_gain.value(),"mud_q":self.s_mud_q.value(),
            "pres":self.s_pres.isChecked(),"pres_freq":self.s_pres_freq.value(),
            "pres_gain":self.s_pres_gain.value(),"pres_q":self.s_pres_q.value(),
            "hshelf":self.s_hshelf.isChecked(),"hshelf_freq":self.s_hshelf_freq.value(),
            "hshelf_gain":self.s_hshelf_gain.value(),"hshelf_q":self.s_hshelf_q.value(),
            "sat_amt":self.s_sat.value(),
            "deess":self.s_deess.isChecked(),"deess_freq":self.s_deess_freq.value(),
            "deess_thr":self.s_deess_thr.value(),"deess_amt":self.s_deess_amt.value(),
            "deess_ratio":self.s_deess_ratio.value(),"deess_atk":self.s_deess_atk.value(),
            "deess_rel":self.s_deess_rel.value(),
            "comp":self.s_comp.isChecked(),"comp_thr":self.s_comp_thr.value(),
            "comp_ratio":self.s_comp_ratio.value(),"comp_atk":self.s_comp_atk.value(),
            "comp_rel":self.s_comp_rel.value(),"comp_knee":self.s_comp_knee.value(),
            "comp_makeup":self.s_comp_makeup.value(),"comp_look":self.s_comp_look.value(),
            "comp_auto_mu":self.s_comp_auto_mu.isChecked(),
            "limiter":self.s_lim.isChecked(),"lim_ceil":self.s_lim_ceil.value(),
            "lim_rel":self.s_lim_rel.value(),
            "fade_in":self.s_fade_in.value(),"fade_out":self.s_fade_out.value(),
        }

    def _start_job_tracking(self):
        self._job_start_time = time.time()
        self.step_label.setText("Starting..."); self.step_label.setStyleSheet("color:#a6e3a1;font-weight:bold;")
        self.time_info_label.setText(""); self._time_timer.start()
    
    def _update_time_info(self):
        if self._job_start_time is None: return
        el = time.time()-self._job_start_time; pct = self.progress.value()
        if pct>0 and self.progress.maximum()>0:
            rem = max(0, el/(pct/100.0)-el)
            self.time_info_label.setText(f"Elapsed: {sec_to_str(el)}  ·  ETA: {sec_to_str(rem)}")
        else: self.time_info_label.setText(f"Elapsed: {sec_to_str(el)}")
    
    def _set_ui_busy(self,b):
        self.run_btn.setEnabled(not b); self.batch_run_btn.setEnabled(not b)
        self.cancel_btn.setEnabled(b)
        self.preview_btn.setEnabled(not b and os.path.isfile(self.input_edit.text().strip()))
        self.progress.setVisible(b); self.progress.setValue(0)
    
    def _on_step_info(self,t,c):
        cm = {"green":"#a6e3a1","blue":"#89b4fa","orange":"#fab387","red":"#f38ba8"}
        self.step_label.setText(t); self.step_label.setStyleSheet(f"color:{cm.get(c,'#a6e3a1')};font-weight:bold;")
    
    def _launch_worker(self,p):
        if p.get("batch") or (p["method"]=="demucs" and shutil.which("demucs") and not p.get("snippet")):
            self.progress.setRange(0,100)
        else: self.progress.setRange(0,0)
        self.progress.setValue(0); self.worker = Worker(p)
        p["_pct"] = self.worker.percent.emit
        self.worker.progress.connect(self.log); self.worker.percent.connect(self.progress.setValue)
        self.worker.step_info.connect(self._on_step_info); self.worker.file_done.connect(self.on_file_done)
        self.worker.finished_ok.connect(self.on_done); self.worker.failed.connect(self.on_error)
        self.worker.start()

    def start(self):
        if self.worker and self.worker.isRunning(): QMessageBox.information(self,"Busy","Running."); return
        if self.method_combo.currentData()=="demucs" and not shutil.which("demucs"):
            QMessageBox.warning(self,"Missing","demucs not installed."); return
        self.fix_out_ext(); ip=self.input_edit.text().strip(); op=self.output_edit.text().strip()
        if not ip or not os.path.isfile(ip): QMessageBox.warning(self,"Error","Select a valid input."); return
        if not op: QMessageBox.warning(self,"Error","Set output path."); return
        self._previewing=False; self._batching=False; self._set_ui_busy(True); self._start_job_tracking()
        self.log("="*60); self._launch_worker(self.collect_params())
    
    def start_preview(self):
        if self.worker and self.worker.isRunning(): QMessageBox.information(self,"Busy","Running."); return
        ip = self.input_edit.text().strip()
        if not ip or not os.path.isfile(ip): QMessageBox.warning(self,"Error","Load input first."); return
        sm = self.player.position() if self.player else 0; ss=sm/1000.0
        base = os.path.splitext(self.output_edit.text().strip() or ip)[0]; pp=base+"_preview.wav"
        p = self.collect_params(); p["output"]=pp; p["fmt"]="wav"; p["snippet"]=(ss,15.0)
        self._previewing=True; self.preview_info={"start_ms":int(sm),"path":pp}
        self._set_ui_busy(True); self._start_job_tracking()
        self.log("="*60); self.log(f"Preview: {ss:.1f}s - {ss+15:.1f}s"); self._launch_worker(p)
    
    def play_original_snippet(self):
        if not(self.player and self.preview_info): return
        ip = self.input_edit.text().strip()
        if ip and os.path.isfile(ip):
            self._pending_seek=self.preview_info["start_ms"]
            self.player.setSource(QUrl.fromLocalFile(ip)); self.player.play()
    
    def _finish_job(self):
        self._time_timer.stop(); self._job_start_time=None; self.progress.setVisible(False)
        self.progress.setRange(0,100); self.run_btn.setEnabled(True); self.batch_run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.preview_btn.setEnabled(os.path.isfile(self.input_edit.text().strip()))
        self.step_label.setText("✓ Done"); self.step_label.setStyleSheet("color:#a6e3a1;font-weight:bold;")
        self.time_info_label.setText("")
    
    def cancel_job(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.log("Cancelling job...")
            self.step_label.setText("Cancelling..."); self.step_label.setStyleSheet("color:#fab387;font-weight:bold;")
    
    def on_done(self,r):
        self._finish_job()
        if self._previewing:
            self._previewing=False; self.cmp_btn.setEnabled(True); self.log("Preview done.")
            if self.player: self.player.setSource(QUrl.fromLocalFile(r)); self.player.play(); return
        if self._batching:
            self._batching=False; failed=set(self._batch_failed)
            for i in range(self.queue_list.count()-1,-1,-1):
                if self.queue_list.item(i).text() not in failed: self.queue_list.takeItem(i)
            self.log("Batch complete."); QMessageBox.information(self,"Batch finished",r); return
        self.log("Done.")
        try: self._spec_cache.pop(os.path.abspath(r),None)
        except: pass
        self.play_src.blockSignals(True); self.play_src.setCurrentIndex(1); self.play_src.blockSignals(False)
        self.load_play_source(); QMessageBox.information(self,"Finished",f"Saved:\n{r}")
    
    def on_error(self,m):
        self._finish_job(); self._previewing=False; self._batching=False
        self.step_label.setText("✗ Error"); self.step_label.setStyleSheet("color:#f38ba8;font-weight:bold;")
        self.log(f"ERROR: {m}"); logging.error(f"Failed: {m}"); QMessageBox.critical(self,"Failed",m)
    
    def update_file_info(self):
        path = self.input_edit.text().strip()
        if not path or not os.path.isfile(path):
            self.file_info_label.setText(""); return
        try:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            with sf.SoundFile(path) as f:
                sr = f.samplerate; ch = f.channels; dur = f.frames / sr
            self.file_info_label.setText(f"{dur/60:.1f} min · {sr} Hz · {ch} ch · {size_mb:.1f} MB")
        except:
            self.file_info_label.setText("Unable to read file info")
    
    def update_estimated_size(self):
        path = self.input_edit.text().strip()
        if not path or not os.path.isfile(path):
            self.estimated_size_label.setText(""); return
        try:
            with sf.SoundFile(path) as f:
                dur = f.frames / f.samplerate
            fmt = self.fmt_combo.currentData()
            if fmt == "wav":
                est_mb = dur * 48000 * 2 * 3 / (1024 * 1024)
            else:
                br = self.br_combo.currentData()
                est_mb = dur * br * 1000 / 8 / (1024 * 1024)
            self.estimated_size_label.setText(f"Estimated output: ~{est_mb:.1f} MB")
        except:
            self.estimated_size_label.setText("")
    
    # ------------------------------------------------------------------
    # Enhanced drag & drop (handles files AND folders)
    # ------------------------------------------------------------------
    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
    
    def dropEvent(self,e):
        urls = e.mimeData().urls()
        if not urls:
            return
        
        files = []
        folders = []
        for u in urls:
            local = u.toLocalFile()
            if not local:
                continue
            if os.path.isdir(local):
                folders.append(local)
            elif os.path.isfile(local):
                files.append(local)
        
        total_items = len(files) + len(folders)
        want_batch = (total_items > 1) or (e.modifiers() & Qt.ShiftModifier) or (len(folders) > 0 and len(files) == 0)
        
        if want_batch:
            all_paths = list(files)
            for folder in folders:
                all_paths.append(folder)
            self._categorized_add(all_paths, "Drop: ")
            self.tabs.setCurrentIndex(4)
        else:
            if files:
                p = files[0]
                if p.lower().endswith(INPUT_EXTS) and os.access(p, os.R_OK):
                    self._set_input_path(p)
                else:
                    self.log(f"Skipped: {p} (not a supported audio file)")
            elif folders:
                reply = QMessageBox.question(
                    self, "Folder dropped",
                    f"Scan folder for audio files?\n{folders[0]}",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    found = scan_folder(folders[0], recursive=True)
                    if found:
                        self._categorized_add(found, "Drop Folder: ")
                        self.tabs.setCurrentIndex(4)
                    else:
                        self.log(f"⚠ No audio files found in {folders[0]}")

    def _cpf(self,t): return self.input_edit.text().strip() if t=="in" else self.output_edit.text().strip()
    
    def analyze_current_spectrum(self):
        t = "in" if self.play_src.currentData()=="input" else "out"; p=self._cpf(t)
        setter = self.spectrum.set_input if t=="in" else self.spectrum.set_output
        if not(p and os.path.isfile(p)): setter(None,None); return
        ap = os.path.abspath(p)
        if ap in self._spec_cache: setter(*self._spec_cache[ap]); return
        if len(self._spec_cache)>8: self._spec_cache.clear()
        old = self._spec_worker
        if old and old.isRunning():
            try: old.ready.disconnect(self.on_spectrum_ready)
            except: pass
            self._spec_keepalive.append(old)
            self._spec_keepalive = [t for t in self._spec_keepalive if t.isRunning()]
        self._spec_worker = SpectrumWorker(t,p); self._spec_worker.ready.connect(self.on_spectrum_ready); self._spec_worker.start()
    
    def on_spectrum_ready(self,t,p,f,d):
        if f is None: return
        try: same = os.path.abspath(p)==os.path.abspath(self._cpf(t))
        except: same=False
        if not same: return
        self._spec_cache[os.path.abspath(p)] = (f,d)
        (self.spectrum.set_input if t=="in" else self.spectrum.set_output)(f,d)
    
    def update_eq_curve(self,*_):
        if not hasattr(self,"spectrum"): return
        f,d = predicted_eq_response(self.collect_params()); self.spectrum.set_curve(f,d)

    def closeEvent(self,e):
        self.save_settings()
        if self.player: self.player.stop()
        for t in [self.worker,self._spec_worker]+list(self._spec_keepalive):
            if t and t.isRunning(): t.terminate(); t.wait(2000)
        if self._dl_thread and self._dl_thread.isRunning():
            self._dl_thread.terminate()
            self._dl_thread.wait(2000)
        logging.info("Closed."); e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv); win = MainWindow(); win.show(); sys.exit(app.exec())