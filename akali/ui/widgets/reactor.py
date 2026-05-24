"""Главный визуальный элемент: «арк-реактор» голосового ассистента.

Дизайн вдохновлён арк-реактором Iron Man — мягкое голубое свечение,
сегментированное внешнее кольцо, концентрические тонкие кольца и яркое
белое ядро с большим cyan-halo. Цветовая палитра «живая»: акцент
медленно перетекает между несколькими близкими cyan/light-blue тонами,
поэтому реактор выглядит как настоящий энергоисточник, а не как
статичная картинка.

Слои рисования (от дальнего к ближнему):
  0. Soft outer halo       — большое радиальное cyan-свечение
  1. Outermost tick ring   — короткие тики по периметру
  2. Soft ring glow        — несколько мягких голубых halo вокруг колец
  3. Mid glow ring         — мягкое размытое сияние под главным кольцом
  4. Main segmented ring   — толстое яркое кольцо с 8 «блоками» вырезов
  5. Inner concentric rings — 2 тонких кольца
  6. Halo ring             — мягкий ореол вокруг ядра
  7. Glowing core          — яркое белое ядро с cyan-аурой
  8. Inner core sun        — крошечное супер-яркое солнце в центре

Реагирует на:
  • set_level(0..1) — уровень внешнего звука → пульс ядра, размер core,
    интенсивность halo, насыщенность rings glow
  • set_state(str)  — цвет акцента (через «живую» палитру)

~30 FPS через QTimer — баланс плавности и CPU.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import (QBrush, QColor, QPainter, QPen, QRadialGradient)
from PySide6.QtWidgets import QWidget, QSizePolicy


# Базовые цвета по состоянию. Для «listening» / «stopped» используем
# не один цвет, а палитру близких cyan/light-blue тонов — реактор
# мягко переливается между ними и выглядит «живым».
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

# «Живая» палитра для cyan-режимов (listening / stopped). Реактор плавно
# перетекает по этим тонам — это даёт ощущение пульсирующей энергии,
# как на референсе Iron Man.
_CYAN_PALETTE: list[QColor] = [
    QColor(0, 220, 255),     # базовый cyan
    QColor(120, 240, 255),   # светлый, почти белый cyan
    QColor(60, 200, 255),    # чуть голубее
    QColor(80, 230, 255),    # промежуточный
]

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


def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    """Линейная интерполяция между двумя QColor."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red()   * (1 - t) + b.red()   * t),
        int(a.green() * (1 - t) + b.green() * t),
        int(a.blue()  * (1 - t) + b.blue()  * t),
        int(a.alpha() * (1 - t) + b.alpha() * t),
    )


class Reactor(QWidget):
    """Анимированный «арк-реактор» с реакцией на уровень микрофона."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # Больше и «здоровее» — реактор занимает больше места по
        # вертикали и горизонтали. Минимальный размер чуть выше старого
        # максимума, максимум — заметно крупнее.
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Прозрачный фон — иначе бывает виден тёмный прямоугольник «подложки»
        # вокруг круга реактора (системная палитра/QSS), особенно на KDE.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self._angle = 0.0
        self._level = 0.0
        self._level_smooth = 0.0
        self._pulse = 0.0
        self._breath = 0.0
        # Фаза «живой» палитры — медленно меняется, перебирая
        # _CYAN_PALETTE по кругу.
        self._palette_phase = 0.0
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
        # Медленное вращение для эффекта «работы».
        speed = 0.25 if self._state in ("listening", "stopped") else 0.5
        self._angle = (self._angle + speed) % 360

        # Плавное сглаживание уровня микрофона. Атак быстрый, decay
        # помедленнее — реактор живо реагирует на звук, но не «дёргается».
        if self._level > self._level_smooth:
            self._level_smooth += (self._level - self._level_smooth) * 0.55
        else:
            self._level_smooth += (self._level - self._level_smooth) * 0.12

        # Пульсирующее «дыхание» core (≈1 Гц) и halo (≈0.3 Гц).
        t = self._angle / 360.0
        self._pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 0.8)
        self._breath = 0.5 + 0.5 * math.sin(t * 2 * math.pi * 0.25)

        # «Живая» палитра: фаза медленно растёт, проходя по _CYAN_PALETTE
        # за ~9 секунд (один полный круг). От уровня микрофона немного
        # ускоряем — громкий звук «оживляет» цвет быстрее.
        self._palette_phase = (
            self._palette_phase
            + 0.0030 * (1.0 + 1.6 * self._level_smooth)
        ) % 1.0

        self.update()

    # ── Цвет ─────────────────────────────────────────────────────────
    def _accent(self) -> QColor:
        """Текущий «живой» цвет акцента.

        Для cyan-режимов (listening/stopped) интерполируем между
        соседними тонами _CYAN_PALETTE — палитра дышит. Для остальных
        состояний возвращаем фиксированный цвет состояния.
        """
        base_state = STATE_COLORS.get(self._state, STATE_COLORS["listening"])
        if self._state not in ("listening", "stopped"):
            return base_state

        n = len(_CYAN_PALETTE)
        pos = self._palette_phase * n
        idx = int(pos) % n
        nxt = (idx + 1) % n
        t = pos - int(pos)
        # Cosine-сглаживание для более органичного перетекания.
        t = 0.5 - 0.5 * math.cos(t * math.pi)
        return _lerp_color(_CYAN_PALETTE[idx], _CYAN_PALETTE[nxt], t)

    # ── Рисование ────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:  # noqa: N802
        size = min(self.width(), self.height())
        cx = self.width() / 2
        cy = self.height() / 2
        # Чуть меньше отступ от краёв, чтобы реактор выглядел крупнее.
        r_outer = size / 2 - 10

        accent = self._accent()

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 0. Soft outer halo (фоновое свечение далеко за реактором)
        self._draw_outer_halo(p, cx, cy, r_outer, accent)

        # 1. Outermost tick ring — короткие тики по периметру
        self._draw_tick_ring(p, cx, cy, r_outer, accent)

        # 2. Soft blue ring glow — несколько мягких голубых halo вокруг
        # колец. Слой делает свечение явно «голубым и мягким», даже когда
        # state даёт другой акцент.
        self._draw_ring_soft_glow(p, cx, cy, r_outer * 0.80, accent)

        # 3. Mid glow ring (мягкий фон под главным кольцом)
        self._draw_mid_glow(p, cx, cy, r_outer * 0.80, accent)

        # 4. Main segmented bright ring (главное яркое кольцо с 8 вырезами)
        self._draw_segmented_ring(p, cx, cy, r_outer * 0.80, accent)

        # 5. Inner concentric rings (тонкие)
        self._draw_thin_ring(p, cx, cy, r_outer * 0.55, accent, alpha=220, width=2.2)
        self._draw_thin_ring(p, cx, cy, r_outer * 0.42, accent, alpha=190, width=1.8)

        # 6. Halo ring вокруг ядра (большая мягкая cyan-«дымка»)
        self._draw_core_halo(p, cx, cy, r_outer * 0.34, accent)

        # 7. Многослойное ядро (как на референсе: концентрические кольца
        # с тонким тёмным контуром, яркая cyan-масса и белая точка в центре).
        self._draw_core(p, cx, cy, r_outer * 0.26, accent)

        p.end()

    # ── Слои ─────────────────────────────────────────────────────────

    def _draw_outer_halo(self, p: QPainter, cx: float, cy: float,
                          r: float, accent: QColor) -> None:
        """Большое мягкое свечение за реактором — задаёт «aura» картинки.

        Расширено и усилено: реактор выглядит как настоящий энергетический
        источник, ambient звук заметно поднимает яркость.
        """
        r_halo = r * 1.70
        grad = QRadialGradient(QPointF(cx, cy), r_halo)
        intensity = 0.45 + 0.25 * self._breath + 0.55 * self._level_smooth
        grad.setColorAt(0.00, _rgba(accent, int(46 * intensity)))
        grad.setColorAt(0.40, _rgba(accent, int(28 * intensity)))
        grad.setColorAt(0.75, _rgba(accent, int(10 * intensity)))
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
        base_alpha = 150 + int(80 * self._level_smooth)
        for i in range(n_ticks):
            angle_deg = (i / n_ticks) * 360.0
            major = (i % 8 == 0)
            length = r * (0.060 if major else 0.032)
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

    def _draw_ring_soft_glow(self, p: QPainter, cx: float, cy: float,
                              r: float, accent: QColor) -> None:
        """Мягкое голубое свечение вокруг главного кольца.

        Несколько широких полупрозрачных halo-колец слегка bluer, чем
        акцент — это и есть «голубое мягкое свечение», которое
        запрашивается в задаче. Уровень микрофона усиливает интенсивность.
        """
        # Делаем колер чуть «голубее» акцента — даже если accent чисто
        # cyan, эти halo будут отдавать в blue.
        blueish = QColor(
            max(0, accent.red() - 30),
            max(0, accent.green() - 10),
            min(255, accent.blue() + 20),
            accent.alpha(),
        )
        # Несколько мягких halo на разных радиусах.
        layers = [
            (r * 1.18, 70, 22.0),
            (r * 1.10, 95, 16.0),
            (r * 1.02, 75, 12.0),
            (r * 0.94, 55, 10.0),
        ]
        intensity = 0.85 + 0.20 * self._breath + 0.55 * self._level_smooth
        p.setBrush(Qt.NoBrush)
        for ring_r, ring_alpha, width in layers:
            alpha = min(220, int(ring_alpha * intensity))
            pen = QPen(_rgba(blueish, alpha), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)

    def _draw_mid_glow(self, p: QPainter, cx: float, cy: float,
                        r: float, accent: QColor) -> None:
        """Мягкое размытое сияние под главным сегментированным кольцом."""
        for ring_r, ring_alpha in [
            (r + 14, 60),
            (r + 6, 110),
            (r - 6, 140),
            (r - 14, 90),
        ]:
            alpha = int(ring_alpha * (0.75 + 0.30 * self._pulse +
                                       0.55 * self._level_smooth))
            alpha = min(230, alpha)
            pen = QPen(_rgba(_shift_rgb(accent, 50), alpha), 3.8)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)

    def _draw_segmented_ring(self, p: QPainter, cx: float, cy: float,
                              r: float, accent: QColor) -> None:
        """Главное яркое кольцо с 8 «блоками»-вырезами."""
        p.save()
        p.translate(cx, cy)

        # Кольцо стало чуть толще — реактор выглядит «здоровее».
        thickness = max(10.0, r * 0.15)
        bright = _shift_rgb(accent, 70)

        intensity = 0.90 + 0.10 * self._pulse + 0.20 * self._level_smooth
        intensity = min(1.0, intensity)
        ring_alpha = int(255 * intensity)

        # Сам ring: рисуем 8 ярких дуг между «блоками»
        segment_step = 360.0 / _RING_SEGMENTS  # 45°
        bright_arc = segment_step - _BLOCK_ARC_DEG

        rect = QRectF(-r, -r, 2 * r, 2 * r)
        pen_bright = QPen(_rgba(bright, ring_alpha), thickness,
                           Qt.SolidLine, Qt.FlatCap)
        p.setPen(pen_bright)
        p.setBrush(Qt.NoBrush)
        for i in range(_RING_SEGMENTS):
            start_deg = i * segment_step + _BLOCK_ARC_DEG / 2
            p.drawArc(rect, int(start_deg * 16), int(bright_arc * 16))

        # «Блоки» — тёмные прямоугольники, имитирующие сегменты-отсечки.
        block_color = QColor(8, 20, 32, 240)
        block_outline = _rgba(accent, 100)
        for i in range(_RING_SEGMENTS):
            center_deg = i * segment_step
            a_rad = math.radians(center_deg)
            bx = math.cos(a_rad) * r
            by = math.sin(a_rad) * r

            bw = thickness * 1.30
            bh = thickness * 0.95
            p.save()
            p.translate(bx, by)
            p.rotate(center_deg + 90)
            block_rect = QRectF(-bw / 2, -bh / 2, bw, bh)
            p.setPen(QPen(block_outline, 1.0))
            p.setBrush(QBrush(block_color))
            p.drawRoundedRect(block_rect, 1.2, 1.2)
            p.restore()

        # Тонкое contour-кольцо изнутри и снаружи.
        p.setPen(QPen(_rgba(_shift_rgb(accent, 90), 170), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), r + thickness / 2 + 0.5,
                       r + thickness / 2 + 0.5)
        p.drawEllipse(QPointF(0, 0), r - thickness / 2 - 0.5,
                       r - thickness / 2 - 0.5)

        p.restore()

    def _draw_thin_ring(self, p: QPainter, cx: float, cy: float, r: float,
                         accent: QColor, alpha: int, width: float) -> None:
        """Тонкое концентрическое кольцо с лёгким голубым подсветом."""
        glow_alpha = min(255, alpha + int(70 * self._level_smooth))
        color = _rgba(_shift_rgb(accent, 30), glow_alpha)
        p.setPen(QPen(color, width))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

    def _draw_core_halo(self, p: QPainter, cx: float, cy: float,
                         r: float, accent: QColor) -> None:
        """Мягкий ореол вокруг ядра — большое cyan-свечение.

        Заметно крупнее и ярче, чем раньше: ядро «дышит» и заполняет
        внутреннее пространство колец светом.
        """
        halo_r = r * 1.85
        grad = QRadialGradient(QPointF(cx, cy), halo_r)
        intensity = 0.65 + 0.25 * self._pulse + 0.50 * self._level_smooth
        grad.setColorAt(0.00, _rgba(_shift_rgb(accent, 90), int(210 * intensity)))
        grad.setColorAt(0.30, _rgba(_shift_rgb(accent, 30), int(120 * intensity)))
        grad.setColorAt(0.70, _rgba(accent, int(35 * intensity)))
        grad.setColorAt(1.00, _rgba(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

    def _draw_core(self, p: QPainter, cx: float, cy: float, r_core: float,
                    accent: QColor) -> None:
        """Многослойная серцевина как на референсе Iron-Man-реактора.

        Структура (от внешнего слоя к центру):
          • bright cyan-белая «масса» ядра с радиальным градиентом
          • тонкое тёмное contour-кольцо вокруг этой массы (рамка)
          • внутри — меньший концентрический круг (более яркий)
          • тонкое тёмное contour-кольцо вокруг внутреннего круга
          • маленький белый «зрачок» с очень ярким центром

        Размер каждого слоя слегка пульсирует от self._pulse и громкости,
        но пропорции сохраняются — серцевина всегда выглядит «собранной»,
        как на референсе.
        """
        pulse = 0.92 + 0.10 * self._pulse + 0.30 * self._level_smooth
        actual_r = r_core * min(1.25, pulse)

        # Тёмный цвет «рамок» — приглушённый cyan, почти antrakit-blue.
        dark_outline = QColor(
            max(0, accent.red() - 80),
            max(0, accent.green() - 80),
            max(0, accent.blue() - 30),
            235,
        )

        # === Слой 1: внешняя масса ядра (cyan-белая) ===========================
        # Радиальный градиент: ярко-белый центр → насыщенный cyan ободок.
        outer_grad = QRadialGradient(QPointF(cx, cy), actual_r)
        outer_grad.setColorAt(0.00, QColor(255, 255, 255, 255))
        outer_grad.setColorAt(0.45, _rgba(_shift_rgb(accent, 120), 250))
        outer_grad.setColorAt(0.85, _rgba(_shift_rgb(accent, 40), 230))
        outer_grad.setColorAt(1.00, _rgba(accent, 200))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(outer_grad))
        p.drawEllipse(QPointF(cx, cy), actual_r, actual_r)

        # Тонкое тёмное contour-кольцо вокруг внешней массы.
        p.setPen(QPen(dark_outline, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), actual_r, actual_r)

        # === Слой 2: средний концентрический круг ==============================
        # Меньший круг внутри. На референсе он явно отделён от внешней
        # массы тонкой тёмной чертой и сам по себе чуть ярче.
        mid_r = actual_r * 0.62
        mid_grad = QRadialGradient(QPointF(cx, cy), mid_r)
        mid_grad.setColorAt(0.00, QColor(255, 255, 255, 255))
        mid_grad.setColorAt(0.55, _rgba(_shift_rgb(accent, 160), 250))
        mid_grad.setColorAt(1.00, _rgba(_shift_rgb(accent, 80), 235))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(mid_grad))
        p.drawEllipse(QPointF(cx, cy), mid_r, mid_r)

        # Тонкое тёмное contour-кольцо вокруг среднего круга.
        p.setPen(QPen(dark_outline, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), mid_r, mid_r)

        # === Слой 3: маленький белый «зрачок» в центре =========================
        # Размер реагирует на громкость — растёт от внешних звуков.
        sun_r = actual_r * (0.30 + 0.10 * self._level_smooth)
        sun = QRadialGradient(QPointF(cx, cy), sun_r)
        sun.setColorAt(0.00, QColor(255, 255, 255, 255))
        sun.setColorAt(0.40, QColor(255, 255, 255, 240))
        sun.setColorAt(0.85, _rgba(_shift_rgb(accent, 200), 200))
        sun.setColorAt(1.00, _rgba(_shift_rgb(accent, 120), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sun))
        p.drawEllipse(QPointF(cx, cy), sun_r, sun_r)
