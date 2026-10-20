"""Jarvis's voice: local neural speech (Piper, British male) with a light "AI butler" treatment.

Everything runs offline. Speech is synthesised sentence by sentence on a worker thread so
the first words play quickly, and stop() cuts it off at once.
"""
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

VOICES = Path(__file__).resolve().parent.parent / "voices"
DEFAULT_MODEL = VOICES / "en_GB-alan-medium.onnx"
PLAYERS = (["pw-play"], ["paplay"], ["aplay", "-q"])


def _player():
    return next((p for p in PLAYERS if shutil.which(p[0])), None)


def _resample(x, factor):
    """Stretch audio by factor (>1 lowers pitch and slows slightly when played at the original rate)."""
    n = int(len(x) * factor)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _comb(x, delay, feedback):
    y = np.copy(x)
    for i in range(delay, len(y), delay):
        end = min(i + delay, len(y))
        y[i:end] += feedback * y[i - delay:end - delay]
    return y


def butler_fx(x, rate):
    """Measured, slightly deeper delivery with a faint digital shimmer and a small-room reverb."""
    x = _resample(x, 1.035)  # about half a semitone lower and a touch slower
    # High-pass at ~80 Hz to keep it clean
    a = np.exp(-2 * np.pi * 80 / rate)
    hp = np.empty_like(x)
    prev_x = prev_y = 0.0
    for i, v in enumerate(x):  # one-pole filter; fast enough for sentence-sized clips
        prev_y = a * (prev_y + v - prev_x)
        prev_x = v
        hp[i] = prev_y
    x = hp
    # Chorus shimmer: a copy delayed 9–13 ms with a slow wobble
    t = np.arange(len(x)) / rate
    d = (0.011 + 0.002 * np.sin(2 * np.pi * 0.6 * t)) * rate
    idx = np.clip(np.arange(len(x)) - d, 0, len(x) - 1)
    shimmer = np.interp(idx, np.arange(len(x)), x)
    x = x + 0.18 * shimmer
    # Small-room reverb (parallel combs), tail padded so it can ring out
    x = np.concatenate([x, np.zeros(int(0.25 * rate), dtype=np.float32)])
    wet = sum(_comb(x, int(rate * ms / 1000), fb) for ms, fb in ((29.7, 0.55), (37.1, 0.5), (41.1, 0.45), (43.7, 0.4))) / 4
    x = x + 0.22 * wet
    peak = np.max(np.abs(x)) or 1.0
    return (x / peak * 0.89).astype(np.float32)


def speakable(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*`#_>|]", "", text)
    text = re.sub(r"\bJ\.A\.R\.V\.I\.S\.", "Jarvis", text)
    text = text.replace("°C", " degrees").replace("%", " percent").replace("GB", " gigabytes")
    return re.sub(r"\s+", " ", text).strip()


class Voice:
    def __init__(self, model=DEFAULT_MODEL, on_state=None):
        self.model_path = Path(model)
        self.on_state = on_state or (lambda speaking: None)
        self.fx = True
        self._voice = None
        self._q = queue.Queue()
        self._gen = 0
        self._proc = None
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def available(self):
        return self.model_path.exists() and _player() is not None

    def _load(self):
        if self._voice is None:
            from piper import PiperVoice
            self._voice = PiperVoice.load(str(self.model_path))
        return self._voice

    def say(self, text):
        text = speakable(text)
        if not text or not self.available:
            return
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        gen = self._gen
        for s in sentences:
            self._q.put((gen, s))
        self._q.put((gen, None))

    def stop(self):
        self._gen += 1
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def _loop(self):
        speaking = False
        while True:
            gen, sentence = self._q.get()
            if gen != self._gen:
                continue
            if sentence is None:
                if speaking and self._q.empty():
                    speaking = False
                    self.on_state(False)
                continue
            try:
                from piper import SynthesisConfig
                voice = self._load()
                cfg = SynthesisConfig(length_scale=1.06, noise_scale=0.55, noise_w_scale=0.6)
                audio = np.concatenate([c.audio_float_array for c in voice.synthesize(sentence, syn_config=cfg)])
                rate = voice.config.sample_rate
                if self.fx:
                    audio = butler_fx(audio, rate)
                if gen != self._gen:
                    continue
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    path = f.name
                with wave.open(path, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
                if not speaking:
                    speaking = True
                    self.on_state(True)
                with self._lock:
                    self._proc = subprocess.Popen(_player() + [path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._proc.wait()
                Path(path).unlink(missing_ok=True)
            except Exception:
                if speaking:
                    speaking = False
                    self.on_state(False)
            if gen != self._gen and speaking:
                speaking = False
                self.on_state(False)
