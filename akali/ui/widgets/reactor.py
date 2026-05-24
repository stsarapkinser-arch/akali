"""Главный визуальный элемент: «арк-реактор» голосового ассистента.

Дизайн вдохновлён арк-реактором Iron Man — яркое cyan-свечение с
сегментированным внешним кольцом и мягким halo.

Слои рисования (от дальнего к ближнему):
  0. Soft outer halo       — большое радиальное cyan-свечение
  1. Outermost tick ring   — ~96 коротких тиков по периметру
  2. Mid glow ring         — мягкое размытое сияние под главным кольцом
  3. Main segmented ring   — толстое яркое кольцо с 8 «блоками» вырезов
  4. Inner concentric rings — 2 тонких кольца
  5. Halo ring             — мягкий ореол вокруг ядра
  6. Glowing core          — яркое белое ядро с cyan-aurum-аурой
  7. Inner core sun        — крошечное супер-яркое солнце в центре

Реагирует на:
  • set_level(0..1) — уровень микрофона → пульс ядра и интенсивность glow
  • set_state(str)  — цвет акцента: cyan/amber/green/red/grey/indigo

30 FPS через QTimer — баланс плавности и CPU.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import (QBrush, QColor, QPainter, QPainterPath, QPen,
                            QRadialGradient)
from PySide6.QtWidgets import QWidget


STATE_COLORS: dict[str, QColor] = {
    "starting":        QColor(255, 200, 80),    # янтарь
    "listening":       QColor(0, 220, 255),     # cyan — основной режим
    "waiting_command": QColor(255, 200, 80),    # янтарь
    "processing":      QColor(120, 255, 180),   # бирюзово-зелёный
    "reindexing":      QColor(120, 160, 255),   # индиго
    "recovering":      QColor(255, 90, 90),     # красный
    "stopped":         QColor(0, 220, 255),     # cyan тусклее
    "error":           QColor(255, 90, 90),     # красный
}

# Кол-во «блоков» (вырезов) на главном кольце — как на референсе Iron Man.
_RING_SEGMENTS = 8
# Угловая ширина одного блока в градусах.
_BLOCK_ARC_DEG = 16.0


def _rgba(c: QColor, alpha: int) -> QColor:
    """Хелпер: тот же RGB, новая alpha."""
    return QColor(c.red(), c.green(), c.blue(), max(0, min(255, alpha)))


def _shift_rgb(c: QColor, delta: int) -> QColor:
    """Сдвинуть RGB-компоненты в сторону белого (delta>0) или тёмного (delta<0)."""
    return QColor(
        max(0, min(255, c.red() + delta)),
        max(0, min(255, c.green() + delta)),
        max(0, min(255, c.blue() + delta)),
        c.alpha(),
    )


class Reactor(QWidget):
    """Анимированный «арк-реактор» с реакцией на уровень микрофона."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(320, 320)
        self._angle = 0.0
        self._level = 0.0
        self._level_smooth = 0.0
        self._pulse = 0.0
        self._breath = 0.0
        self._state = "listening"  # дефолтный визуал — голубой как на референсе

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)   # ~30 FPS

    # ── Внешний API ──────────────────────────────────────────────────
    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    def set_state(self, state: str) -> None:
        self._state = state if state in STATE_COLORS else "listening"
        self.update()

    # ── Анимация ─────────────────────────────────────────────────────
    def _tick(self) -> None:
        # Медленное вращение для эффекта «работы», но не такое, чтобы
        # отвлекать. Главное — пульс ядра.
        speed = 0.25 if self._state in ("listening", "stopped") else 0.5
        self._angle = (self._angle + speed) % 360

        # Плавное сглаживание уровня микрофона
        if self._level > self._level_smooth:
            self._level_smooth += (self._level - self._level_smooth) * 0.45
        else:
            self._level_smooth += (self._level - self._level_smooth) * 0.10

        # Пульсирующее «дыхание» core — независимая медленная волна (1 Гц)
        t = self._angle / 360.0
        self._pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 0.8)
        # Дополнительная медленная нота для halo (0.3 Гц)
        self._breath = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 0.25)
        self.update()

    # ── Рисование ────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        size = min(self.width(), self.height())
        cx = self.width() / 2
        cy = self.height() / 2
        r_outer = size / 2 - 12

        accent = STATE_COLORS.get(self._state, STATE_COLORS["listening"])

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 0. Soft outer halo (фоновое свечение далеко за реактором)
        self._draw_outer_halo(p, cx, cy, r_outer, accent)

        # 1. Outermost tick ring — короткие тики по периметру
        self._draw_tick_ring(p, cx, cy, r_outer, accent)

        # 2. Mid glow ring (мягкий фон под главным кольцом)
        self._draw_mid_glow(p, cx, cy, r_outer * 0.78, accent)

        # 3. Main segmented bright ring (главное яркое кольцо с 8 вырезами)
        self._draw_segmented_ring(p, cx, cy, r_outer * 0.78, accent)

        # 4. Inner concentric rings (тонкие)
        self._draw_thin_ring(p, cx, cy, r_outer * 0.52, accent, alpha=210, width=2.0)
        self._draw_thin_ring(p, cx, cy, r_outer * 0.34, accent, alpha=170, width=1.6)

        # 5. Halo ring вокруг ядра (мягкая cyan-«дымка»)
        self._draw_core_halo(p, cx, cy, r_outer * 0.30, accent)

        # 6. Glowing core (главное яркое ядро)
        self._draw_core(p, cx, cy, r_outer * 0.16, accent)

        p.end()

    # ── Слои ─────────────────────────────────────────────────────────

    def _draw_outer_halo(self, p: QPainter, cx: float, cy: float,
                          r: float, accent: QColor) -> None:
        """Большое мягкое свечение за реактором — задаёт «aura» картинки."""
        r_halo = r * 1.45
        grad = QRadialGradient(QPointF(cx, cy), r_halo)
        intensity = 0.35 + 0.20 * self._breath + 0.35 * self._level_smooth
        grad.setColorAt(0.00, _rgba(accent, int(28 * intensity)))
        grad.setColorAt(0.45, _rgba(accent, int(18 * intensity)))
        grad.setColorAt(0.80, _rgba(accent, int(6 * intensity)))
        grad.setColorAt(1.00, _rgba(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), r_halo, r_halo)

    def _draw_tick_ring(self, p: QPainter, cx: float, cy: float,
                         r: float, accent: QColor) -> None:
        """Внешнее кольцо с короткими тиками по всему периметру."""
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle)

        n_ticks = 96
        base_alpha = 140 + int(60 * self._level_smooth)
        for i in range(n_ticks):
            angle_deg = (i / n_ticks) * 360.0
            major = (i % 8 == 0)
            length = r * (0.055 if major else 0.030)
            a_rad = math.radians(angle_deg)
            x1 = math.cos(a_rad) * (r - length)
            y1 = math.sin(a_rad) * (r - length)
            x2 = math.cos(a_rad) * r
            y2 = math.sin(a_rad) * r
            alpha = base_alpha if major else int(base_alpha * 0.55)
            color = _rgba(accent, alpha)
            p.setPen(QPen(color, 1.8 if major else 1.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        p.restore()

    def _draw_mid_glow(self, p: QPainter, cx: float, cy: float,
                        r: float, accent: QColor) -> None:
        """Мягкое размытое сияние под главным сегментированным кольцом."""
        for ring_r, ring_alpha in [
            (r + 14, 50),
            (r + 6, 90),
            (r - 6, 120),
            (r - 14, 80),
        ]:
            alpha = int(ring_alpha * (0.70 + 0.30 * self._pulse +
                                       0.50 * self._level_smooth))
            alpha = min(220, alpha)
            pen = QPen(_rgba(_shift_rgb(accent, 40), alpha), 3.5)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)

    def _draw_segmented_ring(self, p: QPainter, cx: float, cy: float,
                              r: float, accent: QColor) -> None:
        """Главное яркое кольцо с 8 «блоками»-вырезами."""
        p.save()
        p.translate(cx, cy)

        thickness = max(8.0, r * 0.13)
        bright = _shift_rgb(accent, 60)

        intensity = 0.85 + 0.10 * self._pulse + 0.20 * self._level_smooth
        intensity = min(1.0, intensity)
        ring_alpha = int(255 * intensity)

        # Сам ring: рисуем 8 ярких дуг между «блоками»
        segment_step = 360.0 / _RING_SEGMENTS  # 45°
        # Угол яркой дуги = segment_step - block_arc
        bright_arc = segment_step - _BLOCK_ARC_DEG

        rect = QRectF(-r, -r, 2 * r, 2 * r)
        pen_bright = QPen(_rgba(bright, ring_alpha), thickness,
                           Qt.SolidLine, Qt.FlatCap)
        p.setPen(pen_bright)
        p.setBrush(Qt.NoBrush)
        for i in range(_RING_SEGMENTS):
            start_deg = i * segment_step + _BLOCK_ARC_DEG / 2
            # Qt.drawArc хочет 16'-th degree; angles в Qt считаются против
            # часовой, начало — от 3 часов.
            p.drawArc(rect, int(start_deg * 16), int(bright_arc * 16))

        # «Блоки» — тёмные прямоугольники, имитирующие сегменты-отсечки.
        # Рисуем как маленькие тёмные прямоугольники по углам разрывов.
        block_color = QColor(8, 20, 32, 240)
        block_outline = _rgba(accent, 90)
        for i in range(_RING_SEGMENTS):
            center_deg = i * segment_step
            a_rad = math.radians(center_deg)
            bx = math.cos(a_rad) * r
            by = math.sin(a_rad) * r

            bw = thickness * 1.25
            bh = thickness * 0.92
            p.save()
            p.translate(bx, by)
            p.rotate(center_deg + 90)  # ориентируем по радиусу
            block_rect = QRectF(-bw / 2, -bh / 2, bw, bh)
            p.setPen(QPen(block_outline, 1.0))
            p.setBrush(QBrush(block_color))
            p.drawRoundedRect(block_rect, 1.2, 1.2)
            p.restore()

        # Тонкое contour-кольцо изнутри и снаружи для resolved-look
        p.setPen(QPen(_rgba(_shift_rgb(accent, 80), 160), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r + thickness / 2 + 0.5,
                       r + thickness / 2 + 0.5)
        p.drawEllipse(QPointF(0, 0), r - thickness / 2 - 0.5,
                       r - thickness / 2 - 0.5)

        p.restore()

    def _draw_thin_ring(self, p: QPainter, cx: float, cy: float, r: float,
                         accent: QColor, alpha: int, width: float) -> None:
        """Тонкое концентрическое кольцо."""
        glow_alpha = min(255, alpha + int(60 * self._level_smooth))
        color = _rgba(_shift_rgb(accent, 30), glow_alpha)
        p.setPen(QPen(color, width))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_core_halo(self, p: QPainter, cx: float, cy: float,
                         r: float, accent: QColor) -> None:
        """Мягкий ореол вокруг ядра — большое cyan-свечение."""
        halo_r = r * 1.6
        grad = QRadialGradient(QPointF(cx, cy), halo_r)
        intensity = 0.55 + 0.20 * self._pulse + 0.40 * self._level_smooth
        grad.setColorAt(0.00, _rgba(_shift_rgb(accent, 80), int(180 * intensity)))
        grad.setColorAt(0.35, _rgba(accent, int(80 * intensity)))
        grad.setColorAt(0.75, _rgba(accent, int(20 * intensity)))
        grad.setColorAt(1.00, _rgba(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

    def _draw_core(self, p: QPainter, cx: float, cy: float, r_core: float,
                    accent: QColor) -> None:
        """Светящееся ядро — яркий белый центр с cyan-аурой."""
        pulse = 0.85 + 0.10 * self._pulse + 0.30 * self._level_smooth
        actual_r = r_core * min(1.25, pulse)

        # Главная масса ядра: белый центр → накачанный accent → исходный accent
        core = QRadialGradient(QPointF(cx, cy), actual_r)
        core.setColorAt(0.00, QColor(255, 255, 255, 255))
        core.setColorAt(0.30, _rgba(_shift_rgb(accent, 120), 245))
        core.setColorAt(0.70, _rgba(_shift_rgb(accent, 60), 220))
        core.setColorAt(1.00, _rgba(accent, 140))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(core))
        p.drawEllipse(QPointF(cx, cy), actual_r, actual_r)

        # Тонкое contour-кольцо вокруг ядра (как «зеница»)
        contour = _rgba(_shift_rgb(accent, -40), 200)
        p.setPen(QPen(contour, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), actual_r * 0.92, actual_r * 0.92)

        # Внутреннее «солнце» — крошечное супер-яркое пятно
        sun_r = actual_r * 0.35
        sun = QRadialGradient(QPointF(cx, cy), sun_r)
        sun.setColorAt(0.0, QColor(255, 255, 255, 255))
        sun.setColorAt(0.6, QColor(255, 255, 255, 180))
        sun.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(sun))
        p.drawEllipse(QPointF(cx, cy), sun_r, sun_r)
