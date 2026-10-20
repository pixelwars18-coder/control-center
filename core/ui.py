"""Shared Qt theme, formatting helpers and small widgets."""
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

LIGHT = {
    "bg": "#f6f7f9", "panel": "#ffffff", "panel2": "#f0f2f5", "border": "#e2e5ea", "text": "#16191f",
    "muted": "#687082", "accent": "#2f6fed", "accentSoft": "#e5edfd", "good": "#1f9d55", "warn": "#d68a00",
    "bad": "#d64545", "c1": "#2f6fed", "c2": "#12a594", "c3": "#d6409f",
}
DARK = {
    "bg": "#0f1115", "panel": "#171a21", "panel2": "#1e222b", "border": "#2a2f3a", "text": "#e8eaf0",
    "muted": "#8b93a5", "accent": "#6c9cff", "accentSoft": "#1d2a45", "good": "#3ecf7f", "warn": "#f0b429",
    "bad": "#ff6b6b", "c1": "#6c9cff", "c2": "#2fd3bd", "c3": "#f47fc7",
}
T = dict(DARK)


def fmt_bytes(n, digits=1):
    if n is None:
        return "–"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while abs(n) >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.0f} {units[i]}" if i == 0 else f"{n:.{digits}f} {units[i]}"


def fmt_rate(n):
    return fmt_bytes(n) + "/s"


def fmt_duration(s):
    d, h, m = int(s // 86400), int(s % 86400 // 3600), int(s % 3600 // 60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m" if h else f"{m}m"


def level_color(pct):
    return T["bad"] if pct >= 90 else T["warn"] if pct >= 75 else T["accent"]


def stylesheet(t):
    return f"""
    QWidget {{ background: {t['bg']}; color: {t['text']}; font-size: 10pt; }}
    QMainWindow, QDialog {{ background: {t['bg']}; }}
    QFrame#card {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 12px; }}
    QFrame#card QLabel, QFrame#card QWidget#clear {{ background: transparent; }}
    QLabel#h2 {{ color: {t['muted']}; font-size: 8.5pt; font-weight: 600; letter-spacing: 1px; }}
    QLabel#big {{ font-size: 22pt; font-weight: 650; }}
    QLabel#muted {{ color: {t['muted']}; }}
    QLabel#title {{ font-size: 16pt; font-weight: 650; }}
    QPushButton {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 6px 14px; }}
    QPushButton:hover {{ background: {t['panel2']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton#primary {{ background: {t['accent']}; border-color: {t['accent']}; color: white; }}
    QPushButton#danger {{ color: {t['bad']}; }}
    QLineEdit, QTextEdit, QPlainTextEdit {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 6px 8px; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
    QListWidget, QTreeWidget, QTableWidget {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 10px; outline: 0; }}
    QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {t['accentSoft']}; color: {t['accent']}; }}
    QTreeWidget::item, QTableWidget::item {{ padding: 4px; }}
    QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {t['accentSoft']}; color: {t['text']}; }}
    QHeaderView::section {{ background: {t['panel']}; color: {t['muted']}; border: 0; border-bottom: 1px solid {t['border']}; padding: 6px; font-weight: 600; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}
    QToolTip {{ background: {t['panel']}; color: {t['text']}; border: 1px solid {t['border']}; }}
    """


def make_app(name, icon_glyph="●"):
    """Create the QApplication with the shared theme following the system light/dark setting."""
    QApplication.setApplicationName(name)
    QApplication.setDesktopFileName(name.lower().replace(" ", "-"))
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont("Inter")
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)

    def apply():
        dark = app.styleHints().colorScheme() != Qt.ColorScheme.Light
        T.clear()
        T.update(DARK if dark else LIGHT)
        app.setStyleSheet(stylesheet(T))
        for w in app.allWidgets():
            w.update()
    app.styleHints().colorSchemeChanged.connect(lambda _: apply())
    apply()
    app.setWindowIcon(glyph_icon(icon_glyph))
    return app


def glyph_icon(glyph, bg="#2f6fed"):
    pm = QPixmap(128, 128)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(bg))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(4, 4, 120, 120), 28, 28)
    p.setPen(QColor("white"))
    f = QFont()
    f.setPixelSize(72)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, glyph)
    p.end()
    return QIcon(pm)


class Card(QFrame):
    """Rounded panel with an optional small uppercase heading."""

    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)
        if title:
            h = QLabel(title.upper())
            h.setObjectName("h2")
            self.lay.addWidget(h)


def label(text="", kind=None):
    lbl = QLabel(text)
    if kind:
        lbl.setObjectName(kind)
    return lbl


class Bar(QWidget):
    """Thin rounded usage bar that turns amber and red as it fills."""

    def __init__(self, parent=None, height=8):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedHeight(height)
        self.setObjectName("clear")

    def set(self, pct):
        self.value = max(0.0, min(100.0, pct or 0))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["panel2"]))
        p.drawRoundedRect(r, rad, rad)
        if self.value > 0:
            p.setBrush(QColor(level_color(self.value)))
            p.drawRoundedRect(QRectF(0, 0, max(r.height(), r.width() * self.value / 100), r.height()), rad, rad)


class Sparkline(QWidget):
    """Rolling line chart. series: list of (values, color_key, filled)."""

    def __init__(self, parent=None, points=120, maximum=None):
        super().__init__(parent)
        self.series = []
        self.points = points
        self.maximum = maximum
        self.setMinimumHeight(130)
        self.setObjectName("clear")

    def set(self, series):
        self.series = series
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(T["border"]), 1))
        for i in range(1, 4):
            y = round(h * i / 4) + 0.5
            p.drawLine(QPointF(0, y), QPointF(w, y))
        top = self.maximum or max([1] + [v for vals, _, _ in self.series for v in vals])
        for vals, key, filled in self.series:
            if len(vals) < 2:
                continue
            n = len(vals)
            pts = [QPointF(w - (n - 1 - i) / (self.points - 1) * w, h - 2 - min(v, top) / top * (h - 4)) for i, v in enumerate(vals)]
            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            color = QColor(T[key])
            if filled:
                area = QPainterPath(path)
                area.lineTo(pts[-1].x(), h)
                area.lineTo(pts[0].x(), h)
                area.closeSubpath()
                fill = QColor(color)
                fill.setAlphaF(0.13)
                p.fillPath(area, fill)
            p.setPen(QPen(color, 2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
