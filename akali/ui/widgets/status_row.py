"""Компактная нижняя строка статусов.

    🟢 Микрофон: PipeWire  [========  ]  ·  🟡 Vosk + Ollama  ·  🔵 142 MB

Микрофонный чип содержит анимированную полоску уровня (_MicLevelBar).
Точки мигают при активном состоянии (500ms QTimer).
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QWidget)


class _MicLevelBar(QWidget):
    """Горизонтальная полоска уровня микрофона 44×7px."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._level = 0.0
        self.setFixedSize(44, 7)

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            # Фон
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(10, 20, 35))
            p.drawRoundedRect(0, 0, w, h, 3, 3)
            # Заполнение
            fill_w = int(w * self._level)
            if fill_w > 2:
                p.setBrush(QColor(0, 212, 255, 200))
                p.drawRoundedRect(0, 0, fill_w, h, 3, 3)
                # Свечение — чуть шире с малой альфой
                p.setBrush(QColor(0, 212, 255, 40))
                p.drawRoundedRect(0, 0, min(w, fill_w + 3), h, 3, 3)
        finally:
            p.end()


class _AnimDot(QWidget):
    """Цветной кружок с миганием при активном состоянии."""

    def __init__(self, color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self._active = False
        self.setFixedSize(10, 10)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            if self._active:
                # Мигание: 2 Гц — используем time, перерисовка идёт от QTimer
                blink = (int(time.monotonic() * 2.5) % 2) == 0
                alpha = 220 if blink else 90
            else:
                alpha = 110
            color = QColor(self._color)
            color.setAlpha(alpha)

            # Ореол
            glow = QColor(self._color)
            glow.setAlpha(40 if self._active else 20)
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(0, 0, 10, 10)
            # Точка
            p.setBrush(color)
            p.drawEllipse(2, 2, 6, 6)
        finally:
            p.end()


class StatusChip(QWidget):
    """Точка + название + (опционально) уровень микрофона."""

    def __init__(self, color: QColor, title: str = "", subtitle: str = "",
                 show_level_bar: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)

        self._dot = _AnimDot(color, self)
        row.addWidget(self._dot)

        col_widget = QWidget(self)
        col = QHBoxLayout(col_widget)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        self._title_lbl = QLabel(title, self)
        self._title_lbl.setObjectName("chipTitle")
        col.addWidget(self._title_lbl)

        self._subtitle_lbl = QLabel(subtitle, self)
        self._subtitle_lbl.setObjectName("chipSubtitle")
        col.addWidget(self._subtitle_lbl)

        if show_level_bar:
            self._level_bar = _MicLevelBar(self)
            col.addWidget(self._level_bar)
        else:
            self._level_bar = None

        row.addWidget(col_widget, 1)

    def set_subtitle(self, text: str) -> None:
        self._subtitle_lbl.setText(text)

    def set_level(self, level: float) -> None:
        if self._level_bar is not None:
            self._level_bar.set_level(level)

    def set_active(self, active: bool) -> None:
        self._dot.set_active(active)


class StatusRow(QWidget):
    """Узкая нижняя плашка с тремя индикаторами."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusRow")
        self.setFixedHeight(32)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 14, 4)
        layout.setSpacing(14)

        self.mic = StatusChip(
            QColor(78, 220, 120), title="МИК", subtitle="—",
            show_level_bar=True, parent=self,
        )
        self.brain = StatusChip(
            QColor(255, 170, 60), title="НЕЙРО", subtitle="Vosk + Ollama",
            parent=self,
        )
        self.resources = StatusChip(
            QColor(80, 180, 255), title="ОЗУ", subtitle="—",
            parent=self,
        )

        layout.addWidget(self.mic, 3)
        layout.addWidget(self.brain, 3)
        layout.addWidget(self.resources, 2)

        # 500ms таймер — перерисовывает точки для мигания
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._refresh_dots)
        self._blink_timer.start(200)

    def set_mic_level(self, level: float) -> None:
        self.mic.set_level(level)

    def set_mic_active(self, active: bool) -> None:
        self.mic.set_active(active)

    def set_brain_active(self, active: bool) -> None:
        self.brain.set_active(active)

    def _refresh_dots(self) -> None:
        self.mic._dot.update()
        self.brain._dot.update()
        self.resources._dot.update()
