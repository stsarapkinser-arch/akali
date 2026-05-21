"""Компактная нижняя строка статусов.

Для узкого окна — однострочный мини-баннер:

    🟢 Микрофон: PipeWire  ·  🟡 Vosk + Ollama  ·  🔵 142 MB

Каждый чип — цветная точка + один лейбл (subtitle). Все три в один
QHBoxLayout без пустого места, шрифт мелкий.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QWidget)


class _Dot(QWidget):
    """Цветной кружок 10×10 с лёгким свечением."""

    def __init__(self, color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(10, 10)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        glow = QColor(self._color)
        glow.setAlpha(70)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(0, 0, 10, 10)
        p.setBrush(self._color)
        p.drawEllipse(2, 2, 6, 6)


class StatusChip(QWidget):
    """Точка + один компактный текст."""

    def __init__(self, color: QColor, text: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(_Dot(color))
        self._label = QLabel(text)
        self._label.setObjectName("chipSubtitle")
        self._label.setStyleSheet(
            f"color: rgb({color.red()},{color.green()},{color.blue()}); "
            "font-size: 10px;"
        )
        row.addWidget(self._label, 1)

    def set_subtitle(self, text: str) -> None:
        self._label.setText(text)


class StatusRow(QWidget):
    """Узкая нижняя плашка с тремя индикаторами."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusRow")
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 14, 4)
        layout.setSpacing(14)

        self.mic = StatusChip(QColor(78, 220, 120), "—")
        self.brain = StatusChip(QColor(255, 170, 60), "Vosk + Ollama")
        self.resources = StatusChip(QColor(80, 180, 255), "—")

        layout.addWidget(self.mic, 2)
        layout.addWidget(self.brain, 2)
        layout.addWidget(self.resources, 1)
