"""Helmet-interior HUD graphics for Jarvis: visor overlay, boot sequence, compass and side tapes,
angled holographic panels and the central targeting reticle. Everything is drawn with QPainter."""
import math
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

HUD = "#5fe0ff"
HUD_DIM = "#1d6f8c"
WARM = "#ffb347"
BAD = "#ff5d6c"
MINT = "#7ce9c8"

FONTS = Path(__file__).resolve().parent.parent / "fonts"


def load_fonts():
    for f in FONTS.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))


def faded(color, alpha):
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def hud_font(size_px, family="Orbitron", bold=True, spacing=1.5):
    f = QFont(family)
    f.setPixelSize(int(size_px))
    f.setBold(bold)
    f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def glow_line(p, a, b, color, width=1.5, glow=0.25):
    p.setPen(QPen(faded(color, glow), width * 4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(a, b)
    p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(a, b)


# ---------------------------------------------------------------- panels

class HudPanel(QFrame):
    """Translucent holographic panel. Side panels are drawn as slight trapezoids, as if on curved glass."""

    def __init__(self, title, side="center"):
        super().__init__()
        self.side = side
        self.title = title.upper()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 34, 16, 14)
        self.lay.setSpacing(6)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def shape_polygon(self):
        w, h, c, skew = self.width() - 1, self.height() - 1, 14, 6
        tl, tr, br, bl = QPointF(0, 0), QPointF(w, 0), QPointF(w, h), QPointF(0, h)
        if self.side == "left":  # inner (right) edge sits further away
            tr, br = QPointF(w, skew), QPointF(w, h - skew)
        elif self.side == "right":
            tl, bl = QPointF(0, skew), QPointF(0, h - skew)
        return QPolygonF([tl + QPointF(c, 0), tr, br - QPointF(0, c), br - QPointF(c, 0), bl, tl + QPointF(0, c)])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        poly = self.shape_polygon()
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, faded("#0b3a52", 0.42))
        g.setColorAt(1, faded("#051823", 0.30))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawPolygon(poly)
        p.setPen(QPen(faded(HUD, 0.35), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)
        # bright corner brackets
        pts = [poly[i] for i in range(poly.count())]
        p.setPen(QPen(faded(HUD, 0.95), 2))
        for i in (0, 3):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            d = b - a
            length = math.hypot(d.x(), d.y()) or 1
            p.drawLine(a, a + d * (min(40, length) / length))
        # title tab
        p.setFont(hud_font(10, spacing=3))
        p.setPen(QColor(HUD))
        p.drawText(QRectF(18, 9, self.width() - 36, 18), Qt.AlignLeft | Qt.AlignVCenter, "▸ " + self.title)
        p.setPen(QPen(faded(HUD, 0.25), 1))
        p.drawLine(QPointF(16, 29), QPointF(self.width() - 16, 29))


# ---------------------------------------------------------------- reticle

class Reticle(QWidget):
    """Central targeting reticle. Gauge arcs show CPU, memory and disk; it reacts to Jarvis's state."""

    def __init__(self):
        super().__init__()
        self.cpu = self.mem = self.disk = 0.0
        self.shown = [0.0, 0.0, 0.0]
        self.state = "idle"  # idle | thinking | speaking | offline
        self.ear = "off"  # off | standby | listening | transcribing
        self.level = 0.0
        self.shown_level = 0.0
        self.t0 = time.time()
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_TranslucentBackground)
        QTimer(self, interval=33, timeout=self.update).start()

    def paintEvent(self, _):
        t = time.time() - self.t0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        p.translate(self.width() / 2, self.height() / 2)
        s = side / 520
        p.scale(s, s)
        speed = {"thinking": 4.0, "speaking": 1.6}.get(self.state, 1.0)
        accent = QColor(WARM) if self.state == "thinking" else QColor(BAD) if self.state == "offline" else QColor(HUD)

        def arc(r, start, span, color, width, glow=0.22):
            rect = QRectF(-r, -r, 2 * r, 2 * r)
            p.setBrush(Qt.NoBrush)
            if glow:
                p.setPen(QPen(faded(color, glow), width * 3.5, Qt.SolidLine, Qt.FlatCap))
                p.drawArc(rect, int(start * 16), int(span * 16))
            p.setPen(QPen(color, width, Qt.SolidLine, Qt.FlatCap))
            p.drawArc(rect, int(start * 16), int(span * 16))

        def ring(r, width, color, dash=None, angle=0.0):
            p.save()
            p.rotate(angle)
            pen = QPen(color, width)
            if dash:
                pen.setDashPattern(dash)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(0, 0), r, r)
            p.restore()

        # Listening ring
        self.shown_level += (self.level - self.shown_level) * 0.35
        if self.ear == "standby":
            ring(252, 1.5, faded(MINT, 0.3 + 0.4 * self.shown_level), [6, 12], -t * 6)
        elif self.ear == "listening":
            ring(252, 2 + 10 * self.shown_level, faded(MINT, 0.55 + 0.45 * self.shown_level))
        elif self.ear == "transcribing":
            ring(252, 3, faded(MINT, 0.9), [14, 8], t * 160)

        # Outer bracket arcs with gaps at the crosshair positions
        for k in range(4):
            arc(232, k * 90 + 12, 66, faded(accent, 0.75), 2)
        # Crosshair spokes and ticks
        for k in range(4):
            a = math.radians(k * 90)
            c, s_ = math.cos(a), math.sin(a)
            glow_line(p, QPointF(212 * c, 212 * s_), QPointF(258 * c, 258 * s_), accent, 1.5)
        p.save()
        p.rotate(t * 6 * speed)
        p.setPen(QPen(faded(accent, 0.55), 1))
        for i in range(72):
            a = math.radians(i * 5)
            r1 = 218 if i % 6 else 212
            p.drawLine(QPointF(r1 * math.cos(a), r1 * math.sin(a)), QPointF(224 * math.cos(a), 224 * math.sin(a)))
        p.restore()
        # Orbiting chevrons
        p.save()
        p.rotate(-t * 20 * speed)
        p.setBrush(faded(accent, 0.85))
        p.setPen(Qt.NoPen)
        for k in range(3):
            p.save()
            p.rotate(k * 120)
            p.drawPolygon(QPolygonF([QPointF(0, -246), QPointF(-7, -258), QPointF(7, -258)]))
            p.restore()
        p.restore()

        # Gauges ease toward their targets: CPU, memory, disk
        for i, target in enumerate((self.cpu, self.mem, self.disk)):
            self.shown[i] += (target - self.shown[i]) * 0.12
        for (r, w, color), val in zip(((190, 9, accent), (168, 6, QColor(MINT)), (150, 4, QColor(WARM))), self.shown):
            arc(r, 90, -300, faded(color, 0.13), w, glow=0)  # track (300° sweep, open at the bottom left)
            arc(r, 90, -300 * val / 100, color, w)
        # Gauge labels sit in the open gap at the top left, each level with its own arc's start
        p.setFont(hud_font(10, spacing=1))
        for (r, color, name), val in zip(((190, accent, "CPU"), (168, QColor(MINT), "MEM"), (150, QColor(WARM), "DSK")), self.shown):
            p.setPen(color)
            p.drawText(QRectF(-r * 0.0 - 112, -r - 8, 104, 16), Qt.AlignRight | Qt.AlignVCenter, f"{name} {round(val)}%")

        ring(128, 1, faded(accent, 0.5), [3, 6], t * 30 * speed)
        ring(116, 1, faded(accent, 0.25), None)

        # Speaking: radial voice bars
        if self.state == "speaking":
            p.setPen(QPen(faded(accent, 0.8), 3, Qt.SolidLine, Qt.RoundCap))
            for i in range(48):
                a = math.radians(i * 7.5)
                amp = 6 + 14 * abs(math.sin(t * 9 + i * 0.7) * math.sin(t * 3.1 + i * 0.23))
                p.drawLine(QPointF(100 * math.cos(a), 100 * math.sin(a)), QPointF((100 - amp) * math.cos(a), (100 - amp) * math.sin(a)))

        # Core: faint glass disc with a breathing glow, not a solid light
        pulse = 0.5 + 0.5 * math.sin(t * (6 if self.state == "thinking" else 1.6))
        g = QRadialGradient(QPointF(0, 0), 96)
        g.setColorAt(0, faded(accent, 0.10 + 0.08 * pulse))
        g.setColorAt(0.8, faded(accent, 0.04))
        g.setColorAt(1, faded(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), 96, 96)
        # centre cross
        for a, b in ((QPointF(-18, 0), QPointF(-6, 0)), (QPointF(6, 0), QPointF(18, 0)), (QPointF(0, -18), QPointF(0, -6)), (QPointF(0, 6), QPointF(0, 18))):
            glow_line(p, a, b, accent, 1.2, 0.2)

        p.setFont(hud_font(40, spacing=2))
        p.setPen(QColor("#e9fbff"))
        p.drawText(QRectF(-120, -86, 240, 50), Qt.AlignCenter, f"{round(self.shown[0])}%")
        label = {"thinking": "PROCESSING", "speaking": "SPEAKING", "offline": "LLM OFFLINE"}.get(self.state)
        if not label:
            label = {"listening": "LISTENING", "transcribing": "ANALYSING"}.get(self.ear, "CPU LOAD")
        p.setFont(hud_font(12, spacing=4))
        p.setPen(QColor(BAD) if self.state == "offline" else accent)
        p.drawText(QRectF(-120, 30, 240, 24), Qt.AlignCenter, label)


# ---------------------------------------------------------------- backdrop

class Backdrop(QWidget):
    """Window background: deep space-blue glass, a compass tape along the top and vertical tapes at the sides."""

    def __init__(self):
        super().__init__()
        self.setObjectName("backdrop")
        self.cache = None
        self.heading = 0.0
        self.left_value, self.left_label = 0.0, "CPU %"
        self.right_value, self.right_label = 0.0, "TEMP °C"
        self.t0 = time.time()
        self.overlay = HelmetOverlay(self)
        QTimer(self, interval=50, timeout=self.animate).start()

    def resizeEvent(self, e):
        self.cache = None
        self.overlay.setGeometry(self.rect())
        self.overlay.raise_()
        m = self.layout()
        if m:
            w, h = self.width(), self.height()
            m.setContentsMargins(int(w * 0.055), int(h * 0.075), int(w * 0.055), int(h * 0.06))
        super().resizeEvent(e)

    def tape_rects(self):
        w, h = self.width(), self.height()
        top = QRect(int(w * 0.32), int(h * 0.024), int(w * 0.36), int(h * 0.036))
        left = QRect(int(w * 0.012), int(h * 0.28), int(w * 0.035), int(h * 0.44))
        right = QRect(int(w * 0.953), int(h * 0.28), int(w * 0.035), int(h * 0.44))
        return top, left, right

    def animate(self):
        self.heading = (time.time() - self.t0) * 3 % 360
        for r in self.tape_rects():
            self.update(r.adjusted(-4, -4, 4, 4))

    def background(self):
        if self.cache is None or self.cache.size() != self.size():
            pm = QPixmap(self.size())
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            g = QRadialGradient(QPointF(w / 2, h * 0.45), max(w, h) * 0.75)
            g.setColorAt(0, QColor("#0c2536"))
            g.setColorAt(0.55, QColor("#061420"))
            g.setColorAt(1, QColor("#020609"))
            p.fillRect(pm.rect(), g)
            # faint hex grid
            p.setPen(QPen(faded(HUD, 0.035), 1))
            r = 26
            for row in range(int(h / (r * 1.5)) + 2):
                for col in range(int(w / (r * 1.732)) + 2):
                    cx = col * r * 1.732 + (row % 2) * r * 0.866
                    cy = row * r * 1.5
                    pts = [QPointF(cx + r * math.cos(math.radians(60 * k + 30)), cy + r * math.sin(math.radians(60 * k + 30))) for k in range(6)]
                    p.drawPolygon(QPolygonF(pts))
            p.end()
            self.cache = pm
        return self.cache

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(e.rect(), self.background(), e.rect())
        p.setRenderHint(QPainter.Antialiasing)
        top, left, right = self.tape_rects()
        if e.rect().intersects(top):
            self.paint_compass(p, top)
        if e.rect().intersects(left):
            self.paint_tape(p, left, self.left_value, self.left_label, True)
        if e.rect().intersects(right):
            self.paint_tape(p, right, self.right_value, self.right_label, False)

    def paint_compass(self, p, r):
        p.save()
        p.setClipRect(r)
        cx = r.center().x()
        px_per_deg = r.width() / 90
        p.setFont(hud_font(10, spacing=1))
        names = {0: "N", 45: "NE", 90: "E", 135: "SE", 180: "S", 225: "SW", 270: "W", 315: "NW"}
        for d in range(-50, 51):
            deg = int(self.heading) + d
            x = cx + (deg - self.heading) * px_per_deg
            dd = deg % 360
            fade = 1 - abs(x - cx) / (r.width() / 2)
            if dd % 5:
                continue
            tall = dd % 15 == 0
            p.setPen(QPen(faded(HUD, 0.8 * fade), 1.2))
            p.drawLine(QPointF(x, r.bottom() - (14 if tall else 7)), QPointF(x, r.bottom()))
            if tall:
                p.setPen(faded(HUD, 0.95 * fade))
                p.drawText(QRectF(x - 20, r.top(), 40, 16), Qt.AlignCenter, names.get(dd, f"{dd:03d}"))
        p.restore()
        p.setPen(QPen(QColor(HUD), 1))
        p.setBrush(QColor(HUD))
        p.drawPolygon(QPolygonF([QPointF(cx, r.bottom() + 2), QPointF(cx - 6, r.bottom() + 10), QPointF(cx + 6, r.bottom() + 10)]))
        p.setPen(QPen(faded(HUD, 0.35), 1))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))

    def paint_tape(self, p, r, value, label, left):
        p.save()
        p.setFont(hud_font(9, spacing=1))
        cy = r.center().y()
        x_edge = r.right() if left else r.left()
        sign = -1 if left else 1
        px_per_unit = r.height() / 60
        p.setClipRect(r.adjusted(0, 0, 0, 0))
        for u in range(int(value) - 35, int(value) + 36):
            if u % 2:
                continue
            y = cy - (u - value) * px_per_unit
            fade = 1 - abs(y - cy) / (r.height() / 2)
            major = u % 10 == 0
            p.setPen(QPen(faded(HUD, 0.8 * fade), 1.2))
            p.drawLine(QPointF(x_edge, y), QPointF(x_edge + sign * (16 if major else 7), y))
            if major and 0 <= u <= 100:
                p.setPen(faded(HUD, fade))
                tx = QRectF(x_edge + sign * 18 - (36 if left else 0), y - 8, 36, 16)
                p.drawText(tx, (Qt.AlignRight if left else Qt.AlignLeft) | Qt.AlignVCenter, str(u))
        p.restore()
        # pointer box with the live value
        box = QRectF(x_edge + sign * 2 - (48 if left else 0), cy - 11, 48, 22)
        p.setPen(QPen(QColor(HUD), 1.2))
        p.setBrush(faded("#04121b", 0.85))
        p.drawRect(box)
        p.setFont(hud_font(11))
        p.setPen(QColor("#e9fbff"))
        p.drawText(box, Qt.AlignCenter, f"{value:.0f}")
        p.setFont(hud_font(9, spacing=2))
        p.setPen(QColor(HUD))
        p.drawText(QRectF(r.left() - 10, r.top() - 22, r.width() + 20, 16), Qt.AlignCenter, label)


# ---------------------------------------------------------------- helmet overlay

BOOT_LINES = ["J.A.R.V.I.S.  INITIALISING", "VISOR DISPLAY ............ OK", "SENSOR ARRAY ............. OK",
              "NEURAL LINK .............. OK", "AUDIO I/O ................ OK", "ALL SYSTEMS ONLINE"]


class HelmetOverlay(QWidget):
    """Drawn over everything: the helmet's interior edges, glass glare, scanlines, and the boot-up sequence."""

    BOOT = 3.4

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.cache = None
        self.boot_start = time.time()
        self.boot_timer = QTimer(self, interval=30, timeout=self.boot_tick)
        self.boot_timer.start()

    def boot_tick(self):
        if time.time() - self.boot_start > self.BOOT:
            self.boot_timer.stop()
        self.update()

    def resizeEvent(self, e):
        self.cache = None
        super().resizeEvent(e)

    def visor(self):
        if self.cache is not None and self.cache.size() == self.size():
            return self.cache
        w, h = max(1, self.width()), max(1, self.height())
        # Draw soft shapes small, then scale up smoothly for a natural blur.
        sw, sh = max(8, w // 8), max(8, h // 8)
        img = QImage(sw, sh, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        # Visor opening: a superellipse; everything outside is helmet interior
        path = QPainterPath()
        n, a, b = 4.2, sw * 0.505, sh * 0.53
        pts = []
        for i in range(181):
            th = 2 * math.pi * i / 180
            c, s = math.cos(th), math.sin(th)
            pts.append(QPointF(sw / 2 + a * math.copysign(abs(c) ** (2 / n), c), sh * 0.5 + b * math.copysign(abs(s) ** (2 / n), s)))
        path.addPolygon(QPolygonF(pts))
        outer = QPainterPath()
        outer.addRect(QRectF(-4, -4, sw + 8, sh + 8))
        mask = outer.subtracted(path)
        p.fillPath(mask, QColor(1, 3, 5, 245))
        # Warm metallic reflections of the helmet interior along the rim
        for cx, cy, rad, col, al in ((0.0, 0.5, 0.22, "#8a2c12", 0.55), (1.0, 0.5, 0.22, "#8a2c12", 0.55),
                                     (0.04, 0.08, 0.16, "#c98a3c", 0.45), (0.96, 0.08, 0.16, "#c98a3c", 0.45),
                                     (0.5, 1.02, 0.25, "#5a1c0c", 0.5), (0.5, -0.04, 0.18, "#6b2410", 0.35)):
            g = QRadialGradient(QPointF(cx * sw, cy * sh), rad * sw)
            g.setColorAt(0, faded(col, al))
            g.setColorAt(1, faded(col, 0))
            p.fillPath(mask, g)
        # Inner glow just inside the visor edge
        p.setPen(QPen(faded(HUD, 0.18), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()
        big = QPixmap.fromImage(img.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        p = QPainter(big)
        p.setRenderHint(QPainter.Antialiasing)
        # Glass glare: a faint diagonal sheen at the top left
        g = QLinearGradient(QPointF(w * 0.05, h * 0.0), QPointF(w * 0.35, h * 0.45))
        g.setColorAt(0, faded("#bff3ff", 0.07))
        g.setColorAt(0.5, faded("#bff3ff", 0.02))
        g.setColorAt(1, faded("#bff3ff", 0))
        glare = QPainterPath()
        glare.moveTo(w * 0.08, h * 0.05)
        glare.quadTo(w * 0.25, h * 0.02, w * 0.42, h * 0.06)
        glare.lineTo(w * 0.1, h * 0.42)
        glare.closeSubpath()
        p.fillPath(glare, g)
        # Scanlines
        p.setPen(QPen(QColor(0, 0, 0, 22), 1))
        for y in range(0, h, 3):
            p.drawLine(0, y, w, y)
        p.end()
        self.cache = big
        return big

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(e.rect(), self.visor(), e.rect())
        t = time.time() - self.boot_start
        if t < self.BOOT:
            self.paint_boot(p, t)

    def paint_boot(self, p, t):
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # Visor powers on: black, a bright horizontal line, then the view opens from the centre
        open_t = max(0.0, min(1.0, (t - 0.5) / 0.9))
        ease = 1 - (1 - open_t) ** 3
        cover = QPainterPath()
        cover.addRect(QRectF(0, 0, w, h))
        if ease > 0:
            hole = QPainterPath()
            hole.addEllipse(QPointF(w / 2, h / 2), w * 0.75 * ease, h * 0.75 * ease)
            cover = cover.subtracted(hole)
        fade = 1.0 if t < 2.6 else max(0.0, 1 - (t - 2.6) / 0.8)
        p.fillPath(cover, QColor(0, 0, 0, int(255 * fade)))
        if t < 0.9:
            k = min(1.0, t / 0.4)
            glow_line(p, QPointF(w / 2 - w * 0.4 * k, h / 2), QPointF(w / 2 + w * 0.4 * k, h / 2), QColor(HUD), 2, 0.4)
        # Boot text, typed line by line on a dark plate so it reads over the panels
        p.setFont(hud_font(15, spacing=3))
        bw, bh = 560, 26 * len(BOOT_LINES) + 36
        x, y = (w - bw) / 2 + 28, h * 0.30 + 40
        plate = QRectF((w - bw) / 2, h * 0.30, bw, bh)
        p.setPen(QPen(faded(HUD, 0.5 * fade), 1))
        p.setBrush(faded("#020a10", 0.88 * fade))
        p.drawRect(plate)
        chars = int(max(0.0, t - 0.3) * 70)
        for line in BOOT_LINES:
            if chars <= 0:
                break
            shown = line[:chars]
            chars -= len(line) + 6
            p.setPen(faded(MINT if line.endswith("ONLINE") else HUD, fade))
            p.drawText(QPointF(x, y), shown)
            y += 26
