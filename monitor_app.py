#!/usr/bin/env python3
"""System Monitor: live CPU, memory, network, disks, temperatures, GPU and processes."""
import sys

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
                               QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from core.system import SYSINFO, sampler
from core.ui import T, Bar, Card, Sparkline, fmt_bytes, fmt_duration, fmt_rate, label, make_app


class CoreGrid(QWidget):
    """One small cell per CPU thread, filled from the bottom by its load."""

    def __init__(self):
        super().__init__()
        self.values = []
        self.setObjectName("clear")
        self.setMinimumHeight(40)

    def set(self, values):
        self.values = values
        cols = max(1, self.width() // 40)
        rows = (len(values) + cols - 1) // cols
        self.setMinimumHeight(rows * 38)
        self.update()

    def paintEvent(self, _):
        if not self.values:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cols = max(1, self.width() // 40)
        cw = (self.width() - (cols - 1) * 4) / cols
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        for i, v in enumerate(self.values):
            x, y = (i % cols) * (cw + 4), (i // cols) * 38
            r = QRectF(x, y, cw, 34)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T["panel2"]))
            p.drawRoundedRect(r, 5, 5)
            fill = QColor(T["c1"])
            fill.setAlphaF(0.85)
            p.setBrush(fill)
            p.setClipRect(QRectF(x, y + 34 * (1 - v / 100), cw, 34 * v / 100))
            p.drawRoundedRect(r, 5, 5)
            p.setClipping(False)
            p.setPen(QColor(T["text"]))
            p.drawText(r, Qt.AlignCenter, str(round(v)))


def stat_row(left, right_widget):
    row = QHBoxLayout()
    row.addWidget(label(left, "muted"))
    row.addStretch()
    row.addWidget(right_widget)
    return row


class Monitor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("System Monitor")
        self.resize(1280, 880)
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(24, 20, 24, 24)
        outer.setSpacing(16)

        outer.addWidget(label("System Monitor", "title"))
        outer.addWidget(label(f"{SYSINFO['cpuModel']} · {SYSINFO['os']} · kernel {SYSINFO['kernel']} · {SYSINFO['hostname']}", "muted"))

        # Top tiles
        tiles = QGridLayout()
        tiles.setSpacing(16)
        cpu = Card("CPU")
        self.cpu_val, self.load_lbl, self.cpu_bar = label("–", "big"), label("", "muted"), Bar()
        r = QHBoxLayout(); r.addWidget(self.cpu_val); r.addStretch(); r.addWidget(self.load_lbl)
        cpu.lay.addLayout(r); cpu.lay.addWidget(self.cpu_bar)
        mem = Card("Memory")
        self.mem_val, self.mem_detail, self.mem_bar, self.swap = label("–", "big"), label("", "muted"), Bar(), label("", "muted")
        r = QHBoxLayout(); r.addWidget(self.mem_val); r.addStretch(); r.addWidget(self.mem_detail)
        mem.lay.addLayout(r); mem.lay.addWidget(self.mem_bar); mem.lay.addWidget(self.swap)
        net = Card("Network")
        self.rx, self.tx = label("–", "big"), label("–", "big")
        for w in (self.rx, self.tx):
            w.setStyleSheet("font-size: 16pt")
        r = QHBoxLayout(); r.addWidget(self.rx); r.addStretch(); r.addWidget(label("download", "muted")); net.lay.addLayout(r)
        r = QHBoxLayout(); r.addWidget(self.tx); r.addStretch(); r.addWidget(label("upload", "muted")); net.lay.addLayout(r)
        sysc = Card("System")
        self.uptime, self.procs, self.bat = label("–"), label("–"), label("–")
        sysc.lay.addLayout(stat_row("Uptime", self.uptime))
        sysc.lay.addLayout(stat_row("Processes", self.procs))
        self.bat_row = QWidget(); self.bat_row.setObjectName("clear")
        self.bat_row.setLayout(stat_row("Battery", self.bat)); self.bat_row.layout().setContentsMargins(0, 0, 0, 0)
        self.bat_row.hide()
        sysc.lay.addWidget(self.bat_row)
        for i, c in enumerate((cpu, mem, net, sysc)):
            tiles.addWidget(c, 0, i)
        outer.addLayout(tiles)

        # Charts
        charts = QHBoxLayout(); charts.setSpacing(16)
        usage = Card("CPU & memory · last 2 minutes")
        self.usage_chart = Sparkline(maximum=100)
        usage.lay.addWidget(self.usage_chart)
        usage.lay.addWidget(self.legend([("c1", "CPU %"), ("c3", "Memory %")]))
        self.cores = CoreGrid()
        usage.lay.addWidget(self.cores)
        netc = Card("Network · last 2 minutes")
        self.net_chart = Sparkline()
        netc.lay.addWidget(self.net_chart)
        self.peak = label("", "muted")
        netc.lay.addWidget(self.legend([("c2", "Download"), ("c1", "Upload")], self.peak))
        self.gpu_box = QWidget(); self.gpu_box.setObjectName("clear")
        g = QVBoxLayout(self.gpu_box); g.setContentsMargins(0, 10, 0, 0)
        self.gpu_name, self.gpu_util, self.gpu_mem, self.gpu_misc = label(""), label("–"), label("–"), label("–")
        self.gpu_name.setStyleSheet("font-weight: 600")
        self.gpu_util_bar, self.gpu_mem_bar = Bar(), Bar()
        g.addWidget(label("GPU", "h2")); g.addWidget(self.gpu_name)
        g.addLayout(stat_row("Utilisation", self.gpu_util)); g.addWidget(self.gpu_util_bar)
        g.addLayout(stat_row("VRAM", self.gpu_mem)); g.addWidget(self.gpu_mem_bar)
        g.addLayout(stat_row("Temp · power", self.gpu_misc))
        self.gpu_box.hide()
        netc.lay.addWidget(self.gpu_box)
        netc.lay.addStretch()
        charts.addWidget(usage, 3); charts.addWidget(netc, 2)
        outer.addLayout(charts)

        # Processes, disks, temps
        bottom = QHBoxLayout(); bottom.setSpacing(16)
        pc = Card("Top processes")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["PID", "Name", "CPU", "Memory"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("QTableWidget { border: 0; }")
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(390)
        pc.lay.addWidget(self.table)
        right = QVBoxLayout(); right.setSpacing(16)
        self.disk_card = Card("Disks")
        self.disk_rows = QVBoxLayout(); self.disk_card.lay.addLayout(self.disk_rows)
        self.temp_card = Card("Temperatures")
        self.temp_grid = QGridLayout(); self.temp_grid.setSpacing(8); self.temp_card.lay.addLayout(self.temp_grid)
        right.addWidget(self.disk_card); right.addWidget(self.temp_card); right.addStretch()
        bottom.addWidget(pc, 3); bottom.addLayout(right, 2)
        outer.addLayout(bottom)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(body)
        self.setCentralWidget(scroll)

        self.disk_widgets = {}
        self.temp_labels = []
        self.timer = QTimer(self, interval=1000, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def legend(self, items, extra=None):
        w = QWidget(); w.setObjectName("clear")
        lay = QHBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(14)
        for key, text in items:
            lbl = QLabel(f'<span style="color:{T[key]}">━</span> {text}')
            lbl.setObjectName("muted")
            lay.addWidget(lbl)
        lay.addStretch()
        if extra:
            lay.addWidget(extra)
        return w

    def refresh(self):
        s = sampler().get()
        if "cpu" not in s:
            return
        self.cpu_val.setText(f"{s['cpu']['total']:.1f}%")
        self.load_lbl.setText("load " + "  ".join(f"{x:.2f}" for x in s["load"]))
        self.cpu_bar.set(s["cpu"]["total"])
        m = s["mem"]
        mp = 100 * m["used"] / m["total"]
        self.mem_val.setText(f"{mp:.1f}%")
        self.mem_detail.setText(f"{fmt_bytes(m['used'])} / {fmt_bytes(m['total'])}")
        self.mem_bar.set(mp)
        self.swap.setText(f"Swap {fmt_bytes(m['swapUsed'])} / {fmt_bytes(m['swapTotal'])}" if m["swapTotal"] else "No swap")
        self.rx.setText(fmt_rate(s["net"]["rx"]))
        self.tx.setText(fmt_rate(s["net"]["tx"]))
        self.uptime.setText(fmt_duration(s["uptime"]))
        self.procs.setText(str(s["procCount"]))
        if s.get("battery"):
            self.bat_row.show()
            self.bat.setText(f"{s['battery']['percent']}% · {s['battery']['status']}")

        hist = s["history"]
        self.usage_chart.set([([p["cpu"] for p in hist], "c1", True), ([p["mem"] for p in hist], "c3", False)])
        self.net_chart.set([([p["rx"] for p in hist], "c2", True), ([p["tx"] for p in hist], "c1", False)])
        self.peak.setText("peak " + fmt_rate(max([0] + [max(p["rx"], p["tx"]) for p in hist])))
        self.cores.set(s["cpu"]["cores"])

        gpus = s.get("gpus") or []
        self.gpu_box.setVisible(bool(gpus))
        if gpus:
            g = gpus[0]
            self.gpu_name.setText(g["name"])
            self.gpu_util.setText(f"{g['util']:.0f}%" if g["util"] is not None else "–")
            self.gpu_util_bar.set(g["util"] or 0)
            if g["memTotal"]:
                self.gpu_mem.setText(f"{g['memUsed'] / 1024:.1f} / {g['memTotal'] / 1024:.1f} GB")
                self.gpu_mem_bar.set(100 * g["memUsed"] / g["memTotal"])
            power = f"{g['power']:.0f} W" if g["power"] is not None else "–"
            self.gpu_misc.setText(f"{g['temp']:.0f}°C · {power}" if g["temp"] is not None else power)

        top = s["top"]
        self.table.setRowCount(len(top))
        for i, p in enumerate(top):
            for c, text in enumerate((str(p["pid"]), p["name"], f"{p['cpu']:.1f}%", fmt_bytes(p["rss"]))):
                item = QTableWidgetItem(text)
                if c != 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    item.setForeground(QColor(T["muted"]))
                self.table.setItem(i, c, item)

        for d in s.get("disks", []):
            if d["mount"] not in self.disk_widgets:
                head = QHBoxLayout()
                name = QLabel(f"<b>{d['mount']}</b> <span style='color:{T['muted']}'>{d['fs']}</span>")
                used = label("", "muted")
                head.addWidget(name); head.addStretch(); head.addWidget(used)
                bar = Bar()
                self.disk_rows.addLayout(head); self.disk_rows.addWidget(bar)
                self.disk_widgets[d["mount"]] = (used, bar)
            used, bar = self.disk_widgets[d["mount"]]
            used.setText(f"{fmt_bytes(d['used'])} / {fmt_bytes(d['total'])}")
            bar.set(100 * d["used"] / d["total"])

        temps = s.get("temps", [])[:12]
        self.temp_card.setVisible(bool(temps))
        while len(self.temp_labels) < len(temps):
            lbl = QLabel()
            lbl.setStyleSheet(f"background: {T['panel2']}; border-radius: 8px; padding: 6px 10px;")
            i = len(self.temp_labels)
            self.temp_grid.addWidget(lbl, i // 3, i % 3)
            self.temp_labels.append(lbl)
        for lbl, t in zip(self.temp_labels, temps):
            color = T["bad"] if t["c"] >= 85 else T["warn"] if t["c"] >= 70 else T["text"]
            lbl.setText(f"<span style='color:{T['muted']};font-size:8pt'>{t['label']}</span><br>"
                        f"<span style='color:{color};font-size:14pt;font-weight:600'>{t['c']:.0f}°C</span>")


if __name__ == "__main__":
    app = make_app("System Monitor", "≋")
    win = Monitor()
    win.show()
    sys.exit(app.exec())
