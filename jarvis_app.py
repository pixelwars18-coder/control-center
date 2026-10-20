#!/usr/bin/env python3
"""Jarvis: a heads-up assistant driven by a local LLM (Ollama) that can control this computer."""
import html
import math
import re
import sys
import threading
import time

from PySide6.QtCore import QFileSystemWatcher, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QRadialGradient, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from core import agent, store
from core.system import sampler
from core.listen import Listener
from core.voice import Voice
from core.hud import Backdrop, HudPanel, Reticle, load_fonts
from core.ui import fmt_bytes, fmt_duration, fmt_rate, glyph_icon, make_app

HUD = "#5fe0ff"
WARM = "#ffb347"
BAD = "#ff5d6c"
MINT = "#7ce9c8"
MUTED = "#6d97ad"

STYLE = f"""
QWidget {{ background: transparent; color: #d7f3ff; font-family: Rajdhani; font-size: 12pt; font-weight: 500; }}
QMainWindow {{ background: #020609; }}
QDialog, QMessageBox {{ background: #06121b; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#clock {{ color: #e9fbff; font-family: Orbitron; font-size: 30pt; font-weight: 700; }}
QLabel#title {{ color: {HUD}; font-family: Orbitron; font-size: 14pt; font-weight: 800; letter-spacing: 6px; }}
QPushButton {{ background: rgba(95, 224, 255, 0.05); border: 1px solid rgba(95, 224, 255, 0.45); color: {HUD};
               padding: 6px 12px; font-family: Orbitron; font-weight: 700; letter-spacing: 1px; font-size: 7.5pt; }}
QPushButton:hover {{ background: rgba(95, 224, 255, 0.16); border-color: {HUD}; color: #e9fbff; }}
QPushButton:disabled {{ color: #2b5568; border-color: #12324a; }}
QPushButton#stop {{ color: {BAD}; border-color: rgba(255, 93, 108, 0.6); }}
QPushButton:checked, QPushButton[live="true"] {{ background: rgba(124, 233, 200, 0.18); color: {MINT}; border-color: {MINT}; }}
QLineEdit {{ background: rgba(2, 10, 16, 0.55); border: 0; border-bottom: 1px solid rgba(95, 224, 255, 0.5);
             padding: 7px 10px; color: #e9fbff; font-size: 13pt; selection-background-color: #1d6f8c; }}
QLineEdit:focus {{ border-bottom: 2px solid {HUD}; background: rgba(95, 224, 255, 0.06); }}
QComboBox {{ background: #04101a; border: 1px solid #1d6f8c; padding: 6px 10px; }}
QTextBrowser {{ background: transparent; border: 0; font-size: 12.5pt; }}
QListWidget {{ background: transparent; border: 0; outline: 0; }}
QListWidget::item {{ padding: 4px 2px; border-bottom: 1px solid rgba(95, 224, 255, 0.10); }}
QListWidget::item:selected {{ background: rgba(95, 224, 255, 0.10); color: #e9fbff; }}
QListWidget::indicator {{ width: 12px; height: 12px; border: 1px solid {HUD}; background: transparent; }}
QListWidget::indicator:checked {{ background: {HUD}; }}
QCheckBox {{ spacing: 8px; }}
QScrollBar:vertical {{ background: transparent; width: 6px; }}
QScrollBar::handle:vertical {{ background: rgba(95, 224, 255, 0.3); min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: #06121b; color: #d7f3ff; border: 1px solid {HUD}; }}
"""


def panel(title, side="center"):
    f = HudPanel(title, side)
    return f, f.lay


def md_to_html(text):
    """Small Markdown subset for chat replies: code blocks, inline code, bold, lists, line breaks."""
    parts = re.split(r"```(?:\w+)?\n?(.*?)```", text, flags=re.S)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f'<pre style="color:#a9dff3;background:#06111b;padding:6px">{html.escape(part.rstrip())}</pre>')
            continue
        t = html.escape(part)
        t = re.sub(r"`([^`]+)`", r'<code style="color:#a9dff3">\1</code>', t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?m)^\s*[-*] ", "• ", t)
        out.append(t.replace("\n", "<br>"))
    return "".join(out)




class Bar(QWidget):
    def __init__(self):
        super().__init__()
        self.v = 0
        self.setFixedHeight(4)

    def set(self, v):
        self.v = max(0, min(100, v or 0))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0f2638"))
        c = QColor(BAD if self.v >= 90 else WARM if self.v >= 75 else HUD)
        p.fillRect(QRectF(0, 0, self.width() * self.v / 100, self.height()), c)


class AgentWorker(QThread):
    token = Signal(str)
    tool = Signal(str, dict, object)
    finished_text = Signal(str)
    failed = Signal(str)
    confirm_needed = Signal(str, str)

    def __init__(self, brain, text):
        super().__init__()
        self.brain, self.text = brain, text
        self.cancel = False
        self._answer = False
        self._event = threading.Event()

    def answer(self, ok):
        self._answer = ok
        self._event.set()

    def _confirm(self, cmd, reason):
        self._event.clear()
        self.confirm_needed.emit(cmd, reason)
        self._event.wait()
        return self._answer

    def run(self):
        try:
            out = self.brain.run(self.text, self.token.emit, lambda n, a, r: self.tool.emit(n, a, r),
                                 self._confirm, lambda: self.cancel)
            self.finished_text.emit(out)
        except agent.OllamaError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class SettingsDialog(QDialog):
    def __init__(self, parent, settings, models):
        super().__init__(parent)
        self.setWindowTitle("Jarvis settings")
        self.setMinimumWidth(440)
        f = QFormLayout(self)
        self.name = QLineEdit(settings["name"])
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems(models or [])
        self.model.setCurrentText(settings["model"])
        self.url = QLineEdit(settings["ollama"])
        self.lat = QLineEdit("" if settings["lat"] is None else str(settings["lat"]))
        self.lon = QLineEdit("" if settings["lon"] is None else str(settings["lon"]))
        self.lat.setPlaceholderText("e.g. 45.50")
        self.lon.setPlaceholderText("e.g. -73.57")
        self.voice = QCheckBox("Read replies aloud")
        self.voice.setChecked(settings["voice"])
        self.fx = QCheckBox("AI voice effect (shimmer and room reverb)")
        self.fx.setChecked(settings.get("voiceFx", True))
        self.stt = QComboBox()
        self.stt.addItems(["base.en", "small.en", "medium.en"])
        self.stt.setCurrentText(settings.get("sttModel", "small.en"))
        self.stt.setToolTip("Bigger models understand you better but take longer. small.en is a good balance.")
        self.handsfree = QCheckBox("Hands-free: always listen for “Hey Jarvis”")
        self.handsfree.setChecked(settings.get("handsFree", False))
        self.shell = QCheckBox("Let Jarvis propose shell commands (each one asks you first)")
        self.shell.setChecked(settings["allowShell"])
        f.addRow("Call me", self.name)
        f.addRow("Model", self.model)
        f.addRow("Ollama URL", self.url)
        f.addRow("Latitude", self.lat)
        f.addRow("Longitude", self.lon)
        f.addRow(self.voice)
        f.addRow(self.fx)
        f.addRow("Speech model", self.stt)
        f.addRow(self.handsfree)
        f.addRow(self.shell)
        b = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        f.addRow(b)

    def values(self):
        def num(s):
            s = s.strip()
            return float(s) if s else None
        return {"name": self.name.text().strip() or "Sir", "model": self.model.currentText().strip(),
                "ollama": self.url.text().strip().rstrip("/"), "lat": num(self.lat.text()), "lon": num(self.lon.text()),
                "voice": self.voice.isChecked(), "voiceFx": self.fx.isChecked(), "allowShell": self.shell.isChecked(),
                "sttModel": self.stt.currentText(), "handsFree": self.handsfree.isChecked()}


class Jarvis(QMainWindow):
    speaking_changed = Signal(bool)
    bg_result = Signal(object, object)
    ear_state = Signal(str)
    ear_level = Signal(float)
    heard = Signal(str)
    woke = Signal()
    ear_error = Signal(str)
    ignored = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S.")
        self.resize(1440, 900)
        self.setStyleSheet(STYLE)
        self.setMinimumSize(1180, 760)
        self.brain = agent.Agent()
        self.worker = None
        self.messages = []  # {"role": "you"|"jarvis"|"tool"|"system", "text": str}
        self.models = None
        self.bg_result.connect(lambda callback, result: callback(result))
        self.speaking_changed.connect(self.on_speaking)
        self.voice = Voice(on_state=self.speaking_changed.emit)
        self.ear_state.connect(self.on_ear_state)
        self.ear_level.connect(lambda v: setattr(self.reactor, "level", v))
        self.heard.connect(self.on_heard)
        self.woke.connect(self.on_woke)
        self.ear_error.connect(lambda e: self.add_message("system", e, speak=False))
        self.ears = Listener(self.ear_state.emit, self.ear_level.emit, self.heard.emit, self.woke.emit, self.ear_error.emit,
                             model=store.jarvis_state()["settings"].get("sttModel", "small.en"), on_ignored=self.ignored.emit)
        self.ignored.connect(self.on_ignored)

        load_fonts()
        root = Backdrop()
        self.backdrop = root
        grid = QHBoxLayout(root)
        grid.setSpacing(22)

        # ---- left column
        left = QVBoxLayout()
        left.setSpacing(14)
        p, lay = panel("Local time", "left")
        self.clock = QLabel()
        self.clock.setObjectName("clock")
        self.date = QLabel()
        self.date.setObjectName("muted")
        lay.addWidget(self.clock)
        lay.addWidget(self.date)
        left.addWidget(p)
        p, lay = panel("Atmospherics", "left")
        self.weather = QLabel("Set your location in Settings to see the weather.")
        self.weather.setObjectName("muted")
        self.weather.setWordWrap(True)
        lay.addWidget(self.weather)
        left.addWidget(p)
        p, lay = panel("Diagnostics", "left")
        self.diag = {}
        for key, name in (("cpu", "CPU"), ("mem", "Memory"), ("disk", "Primary disk"), ("gpu", "GPU")):
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            val = QLabel("–")
            val.setStyleSheet(f"color: {HUD}; font-weight: 600")
            row.addStretch()
            row.addWidget(val)
            bar = Bar()
            lay.addLayout(row)
            lay.addWidget(bar)
            self.diag[key] = (val, bar)
        self.diag_text = {}
        for key, name in (("net", "Net ↓ / ↑"), ("temp", "Hottest sensor"), ("up", "Uptime")):
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            val = QLabel("–")
            val.setStyleSheet(f"color: {HUD}; font-weight: 600")
            row.addStretch()
            row.addWidget(val)
            lay.addLayout(row)
            self.diag_text[key] = val
        left.addWidget(p)
        left.addStretch()
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(290)
        grid.addWidget(lw)

        # ---- centre
        center = QVBoxLayout()
        center.setSpacing(8)
        top = QHBoxLayout()
        title = QLabel("J.A.R.V.I.S.")
        title.setObjectName("title")
        self.llm_status = QLabel("Connecting to Ollama…")
        self.llm_status.setObjectName("muted")
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings)
        top.addWidget(title)
        top.addSpacing(12)
        top.addWidget(self.llm_status)
        top.addStretch()
        for text, app in (("Monitor", "monitor"), ("Mods", "mods")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, a=app: agent.open_app(a))
            top.addWidget(b)
        top.addWidget(settings_btn)
        center.addLayout(top)
        self.reactor = Reticle()
        center.addWidget(self.reactor, 5)
        comms, comms_lay = panel("Comms")
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        comms_lay.addWidget(self.chat, 1)
        center.addWidget(comms, 4)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Jarvis anything, e.g. “how's my PC doing?” or “disable CheatMenu in Demo Game”")
        self.input.returnPressed.connect(self.send)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.hide()
        self.mic_btn = QPushButton("Mic")
        self.mic_btn.setToolTip("Speak a request (Ctrl+Space). It stops by itself when you pause.")
        self.mic_btn.clicked.connect(self.toggle_mic)
        self.hf_btn = QPushButton("Hands-free")
        self.hf_btn.setCheckable(True)
        self.hf_btn.setToolTip("Keep listening. Say “Hey Jarvis” (or “Jarvis, …”) followed by your request.")
        self.hf_btn.toggled.connect(self.set_handsfree)
        row.addWidget(self.input, 1)
        row.addWidget(self.mic_btn)
        row.addWidget(self.hf_btn)
        row.addWidget(self.send_btn)
        row.addWidget(self.stop_btn)
        self.ear_hint = QLabel()
        self.ear_hint.setObjectName("muted")
        self.ear_hint.setStyleSheet("font-size: 10.5pt")
        self.ear_hint.hide()
        self.ear_hint_timer = QTimer(self, singleShot=True, interval=7000, timeout=self.ear_hint.hide)
        comms_lay.addWidget(self.ear_hint)
        comms_lay.addLayout(row)
        chips = QHBoxLayout()
        chips.addStretch()
        for text, prompt in (("Briefing", "Give me a quick briefing: time, weather, system health and my open tasks."),
                             ("Status", "How is my computer doing?"),
                             ("Processes", "What's using the most CPU and memory right now?"),
                             ("Tasks", "What's on my task list?")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, q=prompt: self.send(q))
            chips.addWidget(b)
        new = QPushButton("New chat")
        new.clicked.connect(self.new_chat)
        chips.addWidget(new)
        chips.addStretch()
        comms_lay.addLayout(chips)
        grid.addLayout(center, 1)

        # ---- right column
        right = QVBoxLayout()
        right.setSpacing(14)
        p, lay = panel("Protocols", "right")
        self.protocol_grid = QGridLayout()
        self.protocol_grid.setSpacing(6)
        lay.addLayout(self.protocol_grid)
        hint = QLabel("Edit data/commands.json to add your own.")
        hint.setObjectName("muted")
        hint.setStyleSheet("font-size: 9pt")
        lay.addWidget(hint)
        right.addWidget(p)
        p, lay = panel("Tasks", "right")
        self.tasks = QListWidget()
        self.tasks.itemChanged.connect(self.task_toggled)
        self.tasks.itemDoubleClicked.connect(lambda it: (store.remove_task(it.data(Qt.UserRole)), self.load_state()))
        self.tasks.setToolTip("Tick to complete · double-click to delete")
        lay.addWidget(self.tasks)
        row = QHBoxLayout()
        self.task_in = QLineEdit()
        self.task_in.setPlaceholderText("New task")
        self.task_in.returnPressed.connect(self.add_task)
        clear = QPushButton("Clear done")
        clear.clicked.connect(lambda: (store.clear_done_tasks(), self.load_state()))
        row.addWidget(self.task_in, 1)
        row.addWidget(clear)
        lay.addLayout(row)
        right.addWidget(p, 1)
        p, lay = panel("Notes", "right")
        self.notes = QListWidget()
        self.notes.setWordWrap(True)
        self.notes.itemDoubleClicked.connect(lambda it: (store.remove_note(it.data(Qt.UserRole)), self.load_state()))
        self.notes.setToolTip("Double-click to delete")
        lay.addWidget(self.notes)
        self.note_in = QLineEdit()
        self.note_in.setPlaceholderText("Quick note")
        self.note_in.returnPressed.connect(self.add_note)
        lay.addWidget(self.note_in)
        right.addWidget(p, 1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(310)
        grid.addWidget(rw)
        self.setCentralWidget(root)

        # ---- timers and watchers
        QTimer(self, interval=1000, timeout=self.tick).start()
        QTimer(self, interval=15 * 60 * 1000, timeout=self.load_weather).start()
        QTimer(self, interval=20000, timeout=self.check_llm).start()
        self.watcher = QFileSystemWatcher([str(store.DATA)], self)
        self.watcher.directoryChanged.connect(lambda _: self.state_debounce.start())
        self.state_debounce = QTimer(self, singleShot=True, interval=250, timeout=self.load_state)
        self.render_chat_later = QTimer(self, singleShot=True, interval=60, timeout=self.render_chat)

        self.tick()
        self.load_state()
        self.load_weather()
        self.check_llm(warm=True)
        h = time.localtime().tm_hour
        part = "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 18 else "evening"
        self.add_message("jarvis", f"Good {part}, {self.settings['name']}. All systems are online. How can I help?")
        self.input.setFocus()
        QShortcut(QKeySequence("Ctrl+Space"), self, activated=self.toggle_mic)
        for b in self.findChildren(QPushButton):
            b.setText(b.text().upper())
        if not self.ears.available:
            for b in (self.mic_btn, self.hf_btn):
                b.setEnabled(False)
                b.setToolTip("No microphone recorder found (pw-record or parecord).")
        else:
            self.ears.preload()
            if self.settings.get("handsFree"):
                self.hf_btn.setChecked(True)

    # ---------------------------------------------------------------- background helpers
    def background(self, fn, callback):
        """Run fn on a daemon thread and hand its result to callback on the UI thread.
        Daemon threads simply end with the app, so closing during a slow call is safe."""
        def work():
            try:
                result = fn()
            except Exception as e:
                result = {"error": str(e)}
            try:
                self.bg_result.emit(callback, result)
            except RuntimeError:
                pass  # window already closed
        threading.Thread(target=work, daemon=True).start()

    def check_llm(self, warm=False):
        s = store.jarvis_state()["settings"]

        def probe():
            models = agent.ollama_models(s["ollama"])
            if warm and models and s["model"] in models:
                agent.warm_up(s["ollama"], s["model"])
            return models

        def done(models):
            self.models = models if isinstance(models, list) else None
            if self.models is None:
                self.llm_status.setText(f'<span style="color:{BAD}">● Ollama offline</span> · start it with: systemctl start ollama')
                if self.reactor.state != "thinking":
                    self.reactor.state = "offline"
            elif s["model"] not in self.models:
                self.llm_status.setText(f'<span style="color:{WARM}">● Model {s["model"]} not installed</span> · run: ollama pull {s["model"]}')
            else:
                self.llm_status.setText(f'<span style="color:{MINT}">●</span> {s["model"]} · running locally')
                if self.reactor.state == "offline":
                    self.reactor.state = "idle"
        if warm:
            self.llm_status.setText(f"Loading {s['model']}…")
        self.background(probe, done)

    # ---------------------------------------------------------------- state
    @property
    def settings(self):
        return store.jarvis_state()["settings"]

    def load_state(self):
        st = store.jarvis_state()
        self.tasks.blockSignals(True)
        self.tasks.clear()
        for t in st["todos"]:
            it = QListWidgetItem(t["text"])
            it.setData(Qt.UserRole, t["id"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if t["done"] else Qt.Unchecked)
            if t["done"]:
                f = it.font()
                f.setStrikeOut(True)
                it.setFont(f)
                it.setForeground(QColor(MUTED))
            self.tasks.addItem(it)
        if not st["todos"]:
            it = QListWidgetItem("No tasks. Enjoy it.")
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor(MUTED))
            self.tasks.addItem(it)
        self.tasks.blockSignals(False)

        self.notes.clear()
        for n in st["notes"]:
            it = QListWidgetItem(f"{n['text']}\n{time.strftime('%b %d, %H:%M', time.localtime(n['at']))}")
            it.setData(Qt.UserRole, n["id"])
            self.notes.addItem(it)

        while self.protocol_grid.count():
            self.protocol_grid.takeAt(0).widget().deleteLater()
        for i, c in enumerate(store.protocols()):
            b = QPushButton(c["label"].upper())
            b.setStyleSheet("text-align: left; font-family: Rajdhani; font-size: 10pt; font-weight: 700; letter-spacing: 1px; padding: 5px 8px")
            b.clicked.connect(lambda _=False, pid=c["id"]: self.run_protocol(pid))
            self.protocol_grid.addWidget(b, i // 2, i % 2)

    def task_toggled(self, item):
        store.set_task_done(item.data(Qt.UserRole), item.checkState() == Qt.Checked)
        self.load_state()

    def add_task(self):
        if self.task_in.text().strip():
            store.add_task(self.task_in.text())
            self.task_in.clear()
            self.load_state()

    def add_note(self):
        if self.note_in.text().strip():
            store.add_note(self.note_in.text())
            self.note_in.clear()
            self.load_state()

    def run_protocol(self, pid):
        label = next((c["label"] for c in store.protocols() if c["id"] == pid), pid)
        self.add_message("you", f"Run protocol: {label}")

        def done(r):
            if "error" in r:
                self.add_message("jarvis", r["error"])
            else:
                status = "done" if r["exit_code"] == 0 else f"finished with exit code {r['exit_code']}"
                self.add_message("jarvis", f"{r['protocol']}: {status}.\n```\n{r['output'] or '(no output)'}\n```", speak=False)
        self.background(lambda: agent.run_protocol(pid), done)

    # ---------------------------------------------------------------- diagnostics
    def tick(self):
        now = time.localtime()
        self.clock.setText(time.strftime("%H:%M", now) + f'<span style="font-size:14pt;color:{HUD}">:{now.tm_sec:02d}</span>')
        self.date.setText(time.strftime("%A, %B %d %Y", now))
        s = sampler().get()
        if "cpu" not in s:
            return
        m = s["mem"]
        mp = 100 * m["used"] / m["total"]
        disk = next((d for d in s.get("disks", []) if d["mount"] == "/"), (s.get("disks") or [None])[0])
        dp = 100 * disk["used"] / disk["total"] if disk else 0
        self.reactor.cpu, self.reactor.mem, self.reactor.disk = s["cpu"]["total"], mp, dp
        self.diag["cpu"][0].setText(f"{s['cpu']['total']:.1f}%")
        self.diag["cpu"][1].set(s["cpu"]["total"])
        self.diag["mem"][0].setText(f"{fmt_bytes(m['used'])} / {fmt_bytes(m['total'], 0)}")
        self.diag["mem"][1].set(mp)
        self.diag["disk"][0].setText(f"{dp:.0f}% of {fmt_bytes(disk['total'], 0)}" if disk else "–")
        self.diag["disk"][1].set(dp)
        g = (s.get("gpus") or [None])[0]
        self.diag["gpu"][0].setText(f"{g['util']:.0f}% · {g['temp']:.0f}°C" if g and g["util"] is not None else "–")
        self.diag["gpu"][1].set(g["util"] if g else 0)
        self.diag_text["net"].setText(f"{fmt_rate(s['net']['rx'])} / {fmt_rate(s['net']['tx'])}")
        temps = s.get("temps") or []
        hot = max(temps, key=lambda t: t["c"]) if temps else None
        self.diag_text["temp"].setText(f"{hot['c']:.0f}°C" if hot else "–")
        self.diag_text["up"].setText(fmt_duration(s["uptime"]))
        self.backdrop.left_value = s["cpu"]["total"]
        self.backdrop.right_value = hot["c"] if hot else 0

    def load_weather(self):
        def done(w):
            if "error" in w:
                self.weather.setText(w["error"].replace("Ask the person to add", "Add your").replace("in Jarvis Settings", "in Settings"))
                return
            n, d = w["now"], w["today"]
            self.weather.setText(
                f'<span style="font-size:28pt;color:{HUD};font-weight:600">{round(n["temp_c"])}°C</span><br>'
                f'<span style="color:white;font-size:13pt">{n["conditions"].capitalize()}</span><br>'
                f'Feels like {round(n["feels_like_c"])}° · high {round(d["high_c"])}° / low {round(d["low_c"])}°<br>'
                f'Rain {d["rain_chance"]}% · wind {round(n["wind_kmh"])} km/h · humidity {n["humidity"]}%')
        self.background(agent.get_weather, done)

    # ---------------------------------------------------------------- chat
    def add_message(self, role, text, speak=True):
        self.messages.append({"role": role, "text": text})
        self.render_chat()
        if role == "jarvis" and speak:
            self.speak(text)

    def render_chat(self):
        parts = []
        for m in self.messages[-80:]:
            if m["role"] == "you":
                parts.append(f'<p style="color:#ffe2bd;margin:10px 0 2px"><b style="color:{WARM}">YOU</b>&nbsp; {html.escape(m["text"])}</p>')
            elif m["role"] == "tool":
                parts.append(f'<p style="color:{MUTED};font-size:9.5pt;margin:0 0 0 14px">⚙ {html.escape(m["text"])}</p>')
            elif m["role"] == "system":
                parts.append(f'<p style="color:{BAD};margin:6px 0">{html.escape(m["text"])}</p>')
            else:
                body = md_to_html(m["text"]) or f'<span style="color:{MUTED}">…</span>'
                parts.append(f'<p style="margin:4px 0 8px"><b style="color:{HUD}">JARVIS</b>&nbsp; {body}</p>')
        self.chat.setHtml("".join(parts))
        sb = self.chat.verticalScrollBar()
        sb.setValue(sb.maximum())

    def send(self, text=None):
        text = (text if isinstance(text, str) else self.input.text()).strip()
        if not text or self.worker:
            return
        self.input.clear()
        self.voice.stop()
        self.add_message("you", text)
        self.messages.append({"role": "jarvis", "text": ""})
        self.render_chat()
        self.set_busy(True)
        w = AgentWorker(self.brain, text)
        w.token.connect(self.on_token)
        w.tool.connect(self.on_tool)
        w.confirm_needed.connect(self.on_confirm)
        w.finished_text.connect(self.on_done)
        w.failed.connect(self.on_failed)
        w.finished.connect(self.worker_finished)
        self.worker = w
        w.start()

    def current_reply(self):
        if not self.messages or self.messages[-1]["role"] != "jarvis":
            self.messages.append({"role": "jarvis", "text": ""})
        return self.messages[-1]

    def on_token(self, tok):
        self.current_reply()["text"] += tok
        if not self.render_chat_later.isActive():
            self.render_chat_later.start()

    def on_tool(self, name, args, result):
        if self.messages and self.messages[-1]["role"] == "jarvis" and not self.messages[-1]["text"].strip():
            self.messages.pop()
        arg_text = ", ".join(f"{k}={v!r}" for k, v in args.items())
        failed = isinstance(result, dict) and ("error" in result or result.get("declined"))
        self.messages.append({"role": "tool", "text": f"{name}({arg_text})" + ("  → failed" if failed else "")})
        self.messages.append({"role": "jarvis", "text": ""})
        self.render_chat()
        if name in ("add_task", "complete_task", "remove_task", "add_note"):
            self.load_state()

    def on_confirm(self, cmd, reason):
        box = QMessageBox(self)
        box.setWindowTitle("Jarvis wants to run a command")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"<b>Run this command on your computer?</b><pre>{html.escape(cmd)}</pre>"
                    f"<p>Reason given: {html.escape(reason or 'none')}</p>")
        run = box.addButton("Run", QMessageBox.AcceptRole)
        box.addButton("Don't run", QMessageBox.RejectRole)
        box.exec()
        ok = box.clickedButton() is run
        self.messages.insert(len(self.messages) - 1 if self.messages[-1]["role"] == "jarvis" else len(self.messages),
                             {"role": "tool", "text": f"$ {cmd}  ({'approved' if ok else 'declined'})"})
        self.render_chat()
        if self.worker:
            self.worker.answer(ok)

    def on_done(self, text):
        reply = self.current_reply()
        if not reply["text"].strip():
            reply["text"] = text.strip() or "Done."
        self.render_chat()
        self.speak(reply["text"])

    def on_failed(self, err):
        if self.messages and self.messages[-1]["role"] == "jarvis" and not self.messages[-1]["text"].strip():
            self.messages.pop()
        self.add_message("system", err, speak=False)
        self.check_llm()

    def worker_finished(self):
        self.worker = None
        self.set_busy(False)
        self.load_state()

    def stop(self):
        self.voice.stop()
        if self.worker:
            self.worker.cancel = True
            self.worker.answer(False)

    def set_busy(self, busy):
        self.send_btn.setVisible(not busy)
        self.stop_btn.setVisible(busy)
        if busy:
            self.reactor.state = "thinking"
        elif self.reactor.state == "thinking":
            self.reactor.state = "idle"
        self.update_ear_pause()

    def new_chat(self):
        if self.worker:
            return
        self.brain.reset()
        self.messages = []
        self.add_message("jarvis", "Fresh start. What do you need?", speak=False)

    # ---------------------------------------------------------------- voice
    def speak(self, text):
        st = self.settings
        if not st["voice"] or not self.voice.available:
            return
        self.voice.fx = st.get("voiceFx", True)
        self.voice.stop()
        self.voice.say(text)

    def on_speaking(self, speaking):
        if speaking:
            self.reactor.state = "speaking"
        elif self.reactor.state == "speaking":
            self.reactor.state = "idle"
        self.update_ear_pause()

    # ---------------------------------------------------------------- ears
    def update_ear_pause(self):
        """Don't listen while Jarvis is thinking or talking, so it never hears itself."""
        if self.worker or self.reactor.state == "speaking":
            self.ears.paused = True
        else:
            # small delay so the end of Jarvis's own speech isn't picked up
            QTimer.singleShot(400, lambda: setattr(self.ears, "paused", bool(self.worker) or self.reactor.state == "speaking"))

    def toggle_mic(self):
        if not self.ears.available:
            return
        if self.hf_btn.isChecked():
            self.hf_btn.setChecked(False)
        self.voice.stop()  # talking over Jarvis interrupts it
        self.ears.paused = False
        self.ears.listen_once()

    def set_handsfree(self, on):
        self.ears.set_handsfree(on)
        if on:
            self.update_ear_pause()
        if bool(self.settings.get("handsFree")) != on:
            store.update_settings(handsFree=on)

    def on_ear_state(self, state):
        self.reactor.ear = "off" if state == "idle" else state
        if state in ("idle", "standby"):
            self.reactor.level = 0.0
        live = state in ("listening", "transcribing")
        self.mic_btn.setProperty("live", live and not self.hf_btn.isChecked())
        self.mic_btn.setText({"listening": "LISTENING…", "transcribing": "HEARING…"}.get(state, "MIC") if not self.hf_btn.isChecked() else "MIC")
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)
        if state == "idle" and self.hf_btn.isChecked() and self.ears.mode is None:
            self.ears.set_handsfree(True)  # a single request ended; go back to hands-free

    def on_ignored(self, text):
        self.ear_hint.setText(f"Heard “{text}”, but no wake word. Start with “Hey Jarvis”.")
        self.ear_hint.show()
        self.ear_hint_timer.start()

    def on_woke(self):
        self.ear_hint.hide()
        self.voice.stop()
        self.ears.paused = False

    def on_heard(self, text):
        text = text.strip()
        if not text:
            return
        if self.worker:
            self.input.setText(text)  # busy: leave it ready to send
            return
        self.send(text)

    # ---------------------------------------------------------------- settings
    def open_settings(self):
        dlg = SettingsDialog(self, self.settings, self.models)
        if dlg.exec():
            try:
                values = dlg.values()
            except ValueError:
                QMessageBox.warning(self, "Settings", "Latitude and longitude must be numbers.")
                return
            store.update_settings(**values)
            self.ears.set_model(values["sttModel"])
            if self.hf_btn.isChecked() != values["handsFree"] and self.ears.available:
                self.hf_btn.setChecked(values["handsFree"])
            self.load_weather()
            self.check_llm(warm=True)

    def closeEvent(self, e):
        if self.worker:
            self.worker.cancel = True
            self.worker.answer(False)
            self.worker.wait(2000)
        self.voice.stop()
        self.ears.stop()
        super().closeEvent(e)


if __name__ == "__main__":
    app = make_app("Jarvis", "J")
    app.setWindowIcon(glyph_icon("J", "#0b6b8a"))
    win = Jarvis()
    win.show()
    sys.exit(app.exec())
