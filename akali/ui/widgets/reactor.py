"""Главный визуальный элемент: «арк-реактор» голосового ассистента.

Кастомный QWidget с paintEvent: рисует пульсирующее ядро, концентрические
кольца и медленно вращающийся внешний обод с тиками. Реагирует на:

  • уровень микрофона (set_level) — пульс ядра и яркость колец;
  • состояние (set_state) — цвет акцента меняется: cyan (слушаю),
    зелёный (выполняю), янтарный (жду команду), красный (ошибка),
    серый (остановлен), индиго (реиндекс/обновление).

Анимация работает на QTimer 30 FPS, не зависит от микрофона.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (QPointF, QRectF, QTimer, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QPainter, QPen,
                            QRadialGradient)
from PySide6.QtWidgets import QWidget


STATE_COLORS: dict[str, QColor] = {
    "starting":        QColor(255, 200, 80),   # янтарь
    "listening":       QColor(0, 220, 255),    # cyan — основной режим
    "waiting_command": QColor(255, 200, 80),   # янтарь
    "processing":      QColor(120, 255, 180),  # бирюзово-зелёный
    "reindexing":      QColor(120, 160, 255),  # индиго
    "recovering":      QColor(255, 90, 90),    # красный
    "stopped":         QColor(120, 130, 150),  # серый
    "error":           QColor(255, 90, 90),    # красный
}


class Reactor(QWidget):
    """Анимированный «реактор» с реакцией на уровень микрофона."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self._angle = 0.0
        self._level = 0.0          # 0..1 — текущий уровень микрофона
        self._level_smooth = 0.0   # экспоненциально-сглаженный
        self._pulse = 0.0          # автономный пульс «дыхания» 0..1
        self._state = "stopped"

        # 30 FPS — этого хватает для плавной анимации без жора CPU
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ── Внешний API ─────────────────────────────────────────────────
    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    def set_state(self, state: str) -> None:
        self._state = state if state in STATE_COLORS else "stopped"
        self.update()

    # ── Анимация ─────────────────────────────────────────────────────
    def _tick(self) -> None:
        self._angle = (self._angle + 0.6) % 360
        # Сглаживание уровня (атака быстрая, релиз медленный)
        if self._level > self._level_smooth:
            self._level_smooth += (self._level - self._level_smooth) * 0.45
        else:
            self._level_smooth += (self._level - self._level_smooth) * 0.10
        # Автопульсация «дыхания» — синусоида 0..1, период ~2.5с
        t = self._angle / 360.0
        self._pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 1.2)
        self.update()

    # ── Рисование ────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        size = min(self.width(), self.height())
        cx = self.width() / 2
        cy = self.height() / 2
        r_outer = size / 2 - 6

        accent = STATE_COLORS.get(self._state, STATE_COLORS["stopped"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # === 1. Outer rotating tick ring =========================
        self._draw_tick_ring(p, cx, cy, r_outer, accent)

        # === 2. Three concentric rings ===========================
        ring_radii = [r_outer * 0.78, r_outer * 0.62, r_outer * 0.48]
        for i, r in enumerate(ring_radii):
            self._draw_ring(p, cx, cy, r, accent, alpha=140 - i * 25)

        # === 3. Inner dashed rotating ring (counter direction) ===
        self._draw_dashed_ring(p, cx, cy, r_outer * 0.40, accent, direction=-1)

        # === 4. Glowing core =====================================
        self._draw_core(p, cx, cy, r_outer * 0.30, accent)

    def _draw_tick_ring(self, p: QPainter, cx: float, cy: float,
                        r: float, accent: QColor) -> None:
        """Внешний обод с 60 короткими тиками, вращается."""
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle)
        # Слабое мерцание ободка — синусоидальная альфа
        base_alpha = 80 + int(60 * self._pulse) + int(80 * self._level_smooth)
        base_alpha = min(220, base_alpha)
        # Сама окружность
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 90), 1.4)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)
        # Тиков 60 штук
        n_ticks = 60
        for i in range(n_ticks):
            angle = (i / n_ticks) * 360.0
            highlight = (i % 5 == 0)
            length = r * (0.085 if highlight else 0.045)
            tip = r
            tail = r - length
            a_rad = math.radians(angle)
            x1 = math.cos(a_rad) * tail
            y1 = math.sin(a_rad) * tail
            x2 = math.cos(a_rad) * tip
            y2 = math.sin(a_rad) * tip
            alpha = base_alpha if highlight else int(base_alpha * 0.55)
            color = QColor(accent.red(), accent.green(), accent.blue(),
                           max(60, min(255, alpha)))
            pen = QPen(color, 2.2 if highlight else 1.2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.restore()

    def _draw_ring(self, p: QPainter, cx: float, cy: float, r: float,
                   accent: QColor, alpha: int) -> None:
        """Одно концентрическое кольцо со свечением, толщина зависит от уровня."""
        thickness = 2.0 + 1.5 * self._level_smooth
        glow_alpha = max(0, alpha + int(80 * self._level_smooth))
        color = QColor(accent.red(), accent.green(), accent.blue(), glow_alpha)
        pen = QPen(color, thickness)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_dashed_ring(self, p: QPainter, cx: float, cy: float, r: float,
                          accent: QColor, direction: int) -> None:
        """Пунктирное вращающееся кольцо в противоход."""
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle * direction * 1.4)
        color = QColor(accent.red(), accent.green(), accent.blue(), 180)
        pen = QPen(color, 1.6)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([2, 4])
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r, r)
        p.restore()

    def _draw_core(self, p: QPainter, cx: float, cy: float, r_core: float,
                   accent: QColor) -> None:
        """Светящееся ядро с радиальным градиентом."""
        # Пульсация ядра — пропорционально уровню + автопульс
        pulse = 0.6 + 0.25 * self._pulse + 0.7 * self._level_smooth
        actual_r = r_core * min(1.45, pulse)

        # Внешнее свечение (большой полупрозрачный радиальный градиент)
        glow_r = actual_r * 2.4
        glow = QRadialGradient(QPointF(cx, cy), glow_r)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 110))
        glow.setColorAt(0.45, QColor(accent.red(), accent.green(), accent.blue(), 35))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # Само ядро — белый центр → цвет → прозрачность по краю
        core = QRadialGradient(QPointF(cx, cy), actual_r)
        core.setColorAt(0.0, QColor(255, 255, 255, 230))
        core.setColorAt(0.35, QColor(min(255, accent.red() + 80),
                                     min(255, accent.green() + 80),
                                     min(255, accent.blue() + 80), 230))
        core.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 80))
        p.setBrush(QBrush(core))
        p.drawEllipse(QPointF(cx, cy), actual_r, actual_r)

        # Лёгкое внутреннее «солнце»
        inner_r = actual_r * 0.35
        inner = QRadialGradient(QPointF(cx, cy), inner_r)
        inner.setColorAt(0.0, QColor(255, 255, 255, 240))
        inner.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(inner))
        p.drawEllipse(QPointF(cx, cy), inner_r, inner_r)
