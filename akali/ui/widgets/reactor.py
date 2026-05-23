"""Главный визуальный элемент: «арк-реактор» голосового ассистента.

Слои рисования (от дальнего к ближнему):
  0. Radial background glow — мягкое фоновое свечение за реактором
  1. Outer glow halos     — 3 полупрозрачных эллипса за внешним ободом
  2. Outer tick ring      — 60 коротких тиков + 6 гексагональных меток
  3. Three concentric rings
  4. Inner dashed ring (counter-clockwise)
  5. Glowing core

Реагирует на:
  • set_level(0..1) — уровень микрофона → пульс ядра и яркость колец
  • set_state(str)  — цвет акцента: cyan/amber/green/red/grey/indigo

30 FPS через QTimer — баланс плавности и CPU.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QPainter, QPen,
                            QRadialGradient)
from PySide6.QtWidgets import QWidget


STATE_COLORS: dict[str, QColor] = {
    "starting":        QColor(255, 200, 80),    # янтарь
    "listening":       QColor(0, 220, 255),     # cyan — основной режим
    "waiting_command": QColor(255, 200, 80),    # янтарь
    "processing":      QColor(120, 255, 180),   # бирюзово-зелёный
    "reindexing":      QColor(120, 160, 255),   # индиго
    "recovering":      QColor(255, 90, 90),     # красный
    "stopped":         QColor(80, 110, 140),    # серо-синий
    "error":           QColor(255, 90, 90),     # красный
}

# Гексагональные тики: 6 вершин правильного шестиугольника
_HEX_ANGLES = [i * 60.0 for i in range(6)]


class Reactor(QWidget):
    """Анимированный «реактор» с реакцией на уровень микрофона."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self._angle = 0.0
        self._level = 0.0
        self._level_smooth = 0.0
        self._pulse = 0.0
        self._state = "stopped"

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)   # ~30 FPS

    # ── Внешний API ──────────────────────────────────────────────────
    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    def set_state(self, state: str) -> None:
        self._state = state if state in STATE_COLORS else "stopped"
        self.update()

    # ── Анимация ─────────────────────────────────────────────────────
    def _tick(self) -> None:
        self._angle = (self._angle + 0.6) % 360
        if self._level > self._level_smooth:
            self._level_smooth += (self._level - self._level_smooth) * 0.45
        else:
            self._level_smooth += (self._level - self._level_smooth) * 0.10
        t = self._angle / 360.0
        self._pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 1.2)
        self.update()

    # ── Рисование ────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        size = min(self.width(), self.height())
        cx = self.width() / 2
        cy = self.height() / 2
        r_outer = size / 2 - 8

        accent = STATE_COLORS.get(self._state, STATE_COLORS["stopped"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 0. Фоновое радиальное свечение
        self._draw_radial_bg(p, cx, cy, r_outer, accent)

        # 1. Outer glow halos
        self._draw_outer_glow(p, cx, cy, r_outer, accent)

        # 2. Outer rotating tick ring (+ hex markers)
        self._draw_tick_ring(p, cx, cy, r_outer, accent)

        # 3. Three concentric rings
        for i, ratio in enumerate([0.78, 0.62, 0.48]):
            self._draw_ring(p, cx, cy, r_outer * ratio, accent, alpha=145 - i * 28)

        # 4. Inner dashed counter-rotating ring
        self._draw_dashed_ring(p, cx, cy, r_outer * 0.40, accent, direction=-1)

        # 5. Glowing core
        self._draw_core(p, cx, cy, r_outer * 0.30, accent)

        p.end()

    # ── Слои ─────────────────────────────────────────────────────────

    def _draw_radial_bg(self, p: QPainter, cx: float, cy: float,
                        r: float, accent: QColor) -> None:
        """Мягкое фоновое радиальное свечение за реактором."""
        r_bg = r * 1.55
        glow = QRadialGradient(QPointF(cx, cy), r_bg)
        alpha = int(12 + 8 * self._pulse + 10 * self._level_smooth)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), alpha))
        glow.setColorAt(0.6, QColor(accent.red(), accent.green(), accent.blue(), alpha // 3))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), r_bg, r_bg)

    def _draw_outer_glow(self, p: QPainter, cx: float, cy: float,
                         r: float, accent: QColor) -> None:
        """Три полупрозрачных кольца-ореола снаружи основного обода."""
        for offset, alpha in [(8, 45), (17, 22), (28, 10)]:
            r_h = r + offset
            glow_alpha = int(alpha + alpha * 0.5 * self._pulse)
            pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), glow_alpha), 1.2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r_h, r_h)

    def _draw_tick_ring(self, p: QPainter, cx: float, cy: float,
                        r: float, accent: QColor) -> None:
        """Внешний обод: 60 коротких тиков + 6 гексагональных меток."""
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle)

        base_alpha = 80 + int(60 * self._pulse) + int(80 * self._level_smooth)
        base_alpha = min(220, base_alpha)

        # Окружность обода
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 70), 1.2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)

        # 60 коротких тиков
        n_ticks = 60
        for i in range(n_ticks):
            angle_deg = (i / n_ticks) * 360.0
            highlight = (i % 5 == 0)
            length = r * (0.085 if highlight else 0.040)
            a_rad = math.radians(angle_deg)
            x1 = math.cos(a_rad) * (r - length)
            y1 = math.sin(a_rad) * (r - length)
            x2 = math.cos(a_rad) * r
            y2 = math.sin(a_rad) * r
            alpha = base_alpha if highlight else int(base_alpha * 0.50)
            color = QColor(accent.red(), accent.green(), accent.blue(),
                           max(50, min(255, alpha)))
            p.setPen(QPen(color, 2.0 if highlight else 1.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # 6 гексагональных меток (длиннее, ярче)
        hex_alpha = min(255, int(base_alpha * 1.3))
        hex_color = QColor(accent.red(), accent.green(), accent.blue(), hex_alpha)
        p.setPen(QPen(hex_color, 2.5, Qt.SolidLine, Qt.RoundCap))
        for angle_deg in _HEX_ANGLES:
            length = r * 0.15
            a_rad = math.radians(angle_deg)
            x1 = math.cos(a_rad) * (r - length)
            y1 = math.sin(a_rad) * (r - length)
            x2 = math.cos(a_rad) * r
            y2 = math.sin(a_rad) * r
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        p.restore()

    def _draw_ring(self, p: QPainter, cx: float, cy: float, r: float,
                   accent: QColor, alpha: int) -> None:
        """Одно концентрическое кольцо со свечением."""
        thickness = 1.8 + 1.5 * self._level_smooth
        glow_alpha = max(0, alpha + int(80 * self._level_smooth))
        color = QColor(accent.red(), accent.green(), accent.blue(), min(255, glow_alpha))
        p.setPen(QPen(color, thickness))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_dashed_ring(self, p: QPainter, cx: float, cy: float, r: float,
                          accent: QColor, direction: int) -> None:
        """Пунктирное вращающееся кольцо в противоход."""
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle * direction * 1.4)
        color = QColor(accent.red(), accent.green(), accent.blue(), 170)
        pen = QPen(color, 1.4)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([2, 4])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)
        p.restore()

    def _draw_core(self, p: QPainter, cx: float, cy: float, r_core: float,
                   accent: QColor) -> None:
        """Светящееся ядро с радиальным градиентом."""
        pulse = 0.6 + 0.25 * self._pulse + 0.7 * self._level_smooth
        actual_r = r_core * min(1.45, pulse)

        # Внешнее свечение
        glow_r = actual_r * 2.6
        glow = QRadialGradient(QPointF(cx, cy), glow_r)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 120))
        glow.setColorAt(0.40, QColor(accent.red(), accent.green(), accent.blue(), 40))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # Ядро — белый центр → акцентный цвет → прозрачность
        core = QRadialGradient(QPointF(cx, cy), actual_r)
        core.setColorAt(0.0, QColor(255, 255, 255, 235))
        core.setColorAt(0.35, QColor(min(255, accent.red() + 80),
                                      min(255, accent.green() + 80),
                                      min(255, accent.blue() + 80), 235))
        core.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 80))
        p.setBrush(QBrush(core))
        p.drawEllipse(QPointF(cx, cy), actual_r, actual_r)

        # Внутреннее «солнце»
        inner_r = actual_r * 0.35
        inner = QRadialGradient(QPointF(cx, cy), inner_r)
        inner.setColorAt(0.0, QColor(255, 255, 255, 245))
        inner.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(inner))
        p.drawEllipse(QPointF(cx, cy), inner_r, inner_r)
