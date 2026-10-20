"""Jarvis's ears: microphone capture through PipeWire/PulseAudio and local Whisper transcription.

Two ways to talk:
- listen_once(): record one request, stopping by itself after a pause in speech.
- hands-free: listen continuously and only act on speech that starts with the wake word "Jarvis".
Nothing is sent anywhere; audio stays in memory and is transcribed on this machine.
"""
import difflib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque

import numpy as np

RATE = 16000
FRAME = 1280  # 80 ms: the frame size the wake-word model expects
WAKE_THRESHOLD = 0.5
GREETINGS = {"hey", "hi", "ok", "okay", "yo", "hay", "a", "hello"}
# How Whisper tends to spell "Jarvis" when it mishears it
JARVIS_FORMS = ["jarvis", "jarvas", "jarves", "javis", "jervis", "jarvi", "charvis", "garvis", "jarvus", "jarviss"]
# Whisper sometimes "hears" these in silence or noise; ignore them when they are the whole utterance.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching", "bye", ".", "okay"}


def recorder_cmd():
    if shutil.which("pw-record"):
        return ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"]
    if shutil.which("parecord"):
        return ["parecord", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1"]
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
    return None


def _similar_to_jarvis(word, loose=False):
    w = re.sub(r"[^a-z]", "", word.lower())
    if len(w) < 4:
        return False
    best = max(difflib.SequenceMatcher(None, w, form).ratio() for form in JARVIS_FORMS)
    return best >= (0.6 if loose else 0.8)


def split_wake(text, loose=False):
    """If text starts with the wake phrase ("Hey Jarvis", "Jarvis", "OK Jarvis" and close mishearings),
    return (True, rest of text); otherwise (False, text). loose=True when the wake model already fired."""
    words = text.split()
    i = 0
    while i < len(words) and i < 2 and re.sub(r"[^a-z]", "", words[i].lower()) in GREETINGS:
        i += 1
    for j in range(i, min(i + 2, len(words))):
        if _similar_to_jarvis(words[j], loose):
            rest = " ".join(words[j + 1:]).lstrip(" ,.!?:;-")
            return True, rest
    return False, text


def _wake_model():
    """openWakeWord's pretrained "hey jarvis" model, or None if the package is missing."""
    try:
        import openwakeword
        from openwakeword.model import Model
    except ImportError:
        return None
    path = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models", "hey_jarvis_v0.1.onnx")
    if not os.path.exists(path):
        return None
    return Model(wakeword_model_paths=[path])


_chime_path = None


def chime():
    """Short rising two-tone beep so you know Jarvis is listening."""
    global _chime_path
    player = next((p for p in (["pw-play"], ["paplay"], ["aplay", "-q"]) if shutil.which(p[0])), None)
    if not player:
        return
    if _chime_path is None:
        t = np.arange(int(0.09 * 22050)) / 22050
        env = np.sin(np.pi * t / t[-1]) ** 2
        tone = np.concatenate([np.sin(2 * np.pi * 880 * t) * env, np.sin(2 * np.pi * 1320 * t) * env]) * 0.25
        fd, _chime_path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-chime-")
        os.close(fd)
        with wave.open(_chime_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes((tone * 32767).astype(np.int16).tobytes())
    subprocess.Popen(player + [_chime_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Listener:
    """Callbacks run on a worker thread: on_state(str), on_level(float 0-1), on_text(str), on_wake(), on_error(str),
    on_ignored(str) for speech heard in hands-free mode without the wake word."""

    def __init__(self, on_state, on_level, on_text, on_wake, on_error, model="small.en", on_ignored=None):
        self.on_state, self.on_level, self.on_text, self.on_wake, self.on_error = on_state, on_level, on_text, on_wake, on_error
        self.on_ignored = on_ignored or (lambda text: None)
        self._wake = None
        self.model_name = model
        self._model = None
        self._model_lock = threading.Lock()
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self.mode = None  # None | "once" | "handsfree"
        self.paused = False  # set while Jarvis is speaking or thinking
        self._force_end = threading.Event()

    @property
    def available(self):
        return recorder_cmd() is not None

    # ---------------------------------------------------------------- model
    def preload(self):
        def load():
            self._get_model()
            if self._wake is None:
                self._wake = _wake_model() or False
        threading.Thread(target=load, daemon=True).start()

    def set_model(self, name):
        if name != self.model_name:
            with self._model_lock:
                self.model_name, self._model = name, None

    def _get_model(self):
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            return self._model

    def transcribe(self, audio):
        segs, _ = self._get_model().transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                               initial_prompt="Jarvis, what's my CPU? Disable the mod. Add a task.",
                                               condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()

    # ---------------------------------------------------------------- control
    def listen_once(self):
        """Record a single request. Calling it again while recording ends the recording early."""
        if self.mode == "once":
            self._force_end.set()
            return
        self._start("once")

    def set_handsfree(self, on):
        if on and self.mode != "handsfree":
            self._start("handsfree")
        elif not on and self.mode == "handsfree":
            self.stop()

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self.mode = None

    def _start(self, mode):
        self.stop()
        cmd = recorder_cmd()
        if not cmd:
            self.on_error("No audio recorder found. Install pipewire (pw-record) or pulseaudio-utils (parecord).")
            return
        self._stop.clear()
        self._force_end.clear()
        self.mode = mode
        self._thread = threading.Thread(target=self._run, args=(mode, cmd), daemon=True)
        self._thread.start()

    # ---------------------------------------------------------------- capture loop
    # Hands-free wakes two ways: the "hey Jarvis" model fires, or a transcribed utterance starts with
    # something close to "Jarvis". Speech without either is reported through on_ignored.
    def _run(self, mode, cmd):
        if mode == "handsfree" and self._wake is None:
            self._wake = _wake_model() or False
        wake = self._wake if mode == "handsfree" else None
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except OSError as e:
            self.on_error(f"Could not open the microphone: {e}")
            self.mode = None
            return
        self.on_state("listening" if mode == "once" else "standby")
        noise = 0.004
        pre = deque(maxlen=20)  # 1.6 s of audio kept from before speech starts
        speech, loud_run, quiet_run = [], 0, 0
        in_speech = False
        started = time.time()
        woken = mode == "once"  # hands-free: the wake word was heard and a request is expected
        wake_deadline = 0.0
        cooldown_until = 0.0
        buf = b""
        if wake:
            wake.reset()
        try:
            while not self._stop.is_set():
                chunk = self._proc.stdout.read(FRAME * 2)
                if not chunk:
                    break
                buf += chunk
                if len(buf) < FRAME * 2:
                    continue
                pcm = np.frombuffer(buf[:FRAME * 2], np.int16)
                buf = buf[FRAME * 2:]
                if self.paused:
                    pre.clear()
                    speech, in_speech, loud_run, quiet_run = [], False, 0, 0
                    if wake:
                        wake.reset()
                    continue
                frame = pcm.astype(np.float32) / 32768
                now = time.time()
                rms = float(np.sqrt(np.mean(frame ** 2)))
                self.on_level(min(1.0, rms / 0.08))

                # 1. Dedicated wake-word model ("hey Jarvis")
                if wake and not woken and now > cooldown_until:
                    if max(wake.predict(pcm).values()) >= WAKE_THRESHOLD:
                        woken, wake_deadline, cooldown_until = True, now + 7, now + 2
                        chime()
                        self.on_wake()
                        self.on_state("listening")
                        if not in_speech:  # the request may already be under way
                            in_speech, speech, quiet_run = True, list(pre), 0
                elif wake and woken:
                    wake.predict(pcm)  # keep the model's audio history current

                # 2. Voice activity: cut speech into utterances
                threshold = max(0.010, noise * 3.0)
                if not in_speech:
                    noise = 0.97 * noise + 0.03 * min(rms, 0.05)
                    pre.append(frame)
                    loud_run = loud_run + 1 if rms > threshold else 0
                    if loud_run >= 2:
                        in_speech, speech, quiet_run = True, list(pre)[-6:], 0
                    elif mode == "once" and now - started > 8:
                        return  # nobody spoke
                    elif mode == "handsfree" and woken and now > wake_deadline:
                        woken = False
                        self.on_state("standby")
                    continue
                speech.append(frame)
                quiet_run = quiet_run + 1 if rms < threshold * 0.7 else 0
                seconds = len(speech) * FRAME / RATE
                if quiet_run < 11 and seconds < 20 and not self._force_end.is_set():  # ~0.9 s pause ends it
                    continue
                self._force_end.clear()
                in_speech = False
                audio = np.concatenate(speech)
                speech, loud_run = [], 0
                if seconds < 0.4:
                    continue

                # 3. Transcribe the utterance
                self.on_state("transcribing")
                text = self.transcribe(audio)
                clean = text.lower().strip(" .!?,")
                if mode == "once":
                    if clean not in HALLUCINATIONS:
                        self.on_text(text)
                    return
                has_wake, rest = split_wake(text, loose=woken)
                if clean in HALLUCINATIONS:
                    self.on_state("listening" if woken else "standby")
                    continue
                if woken or has_wake:
                    request = rest if has_wake else text
                    if len(re.sub(r"\W", "", request)) > 1:
                        woken = False
                        self.on_text(request)
                        self.on_state("standby")
                    else:  # only the wake word: wait a few seconds for the request
                        if not woken:
                            chime()
                            self.on_wake()
                        woken, wake_deadline = True, time.time() + 7
                        self.on_state("listening")
                else:
                    self.on_ignored(text)
                    self.on_state("standby")
        except Exception as e:
            self.on_error(f"Voice input stopped: {type(e).__name__}: {e}")
        finally:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
            if mode == "once" and self.mode == "once":
                self.mode = None
            if self._stop.is_set() or mode == "once":
                self.on_state("idle")
