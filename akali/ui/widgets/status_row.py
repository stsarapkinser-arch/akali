"""Нижняя строка статусов: «Микрофон», «Нейросети», «Ресурсы».

Каждый чип — цветной индикатор + заголовок + подзаголовок. Сам по себе
ничего не делает, просто показывает текст; родитель апдейтит подзаголовки.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                                QWidget)


class _Dot(QWidget):
    """Цветной кружок 12×12 с лёгким свечением."""

    def __init__(self, color: QColor, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(14, 14)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # Внешнее свечение
        glow = QColor(self._color)
        glow.setAlpha(80)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(0, 0, 14, 14)
        # Само ядро
        p.setBrush(self._color)
        p.drawEllipse(3, 3, 8, 8)


class StatusChip(QWidget):
    """Одна колонка статуса: dot + title + subtitle."""

    def __init__(self, color: QColor, title: str, subtitle: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        dot_wrap = QVBoxLayout()
        dot_wrap.setContentsMargins(0, 4, 0, 0)
        dot_wrap.addWidget(_Dot(color))
        dot_wrap.addStretch(1)
        outer.addLayout(dot_wrap)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        self._title = QLabel(title)
        self._title.setObjectName("chipTitle")
        self._title.setStyleSheet(
            f"color: rgb({color.red()},{color.green()},{color.blue()});"
            "font-weight: 600;"
            "font-size: 14px;"
        )
        text_box.addWidget(self._title)

        self._subtitle = QLabel(subtitle)
        self._subtitle.setObjectName("chipSubtitle")
        self._subtitle.setWordWrap(True)
        text_box.addWidget(self._subtitle)

        outer.addLayout(text_box, 1)

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)


class StatusRow(QWidget):
    """Готовая строка с тремя стандартными колонками."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(28)

        self.mic = StatusChip(QColor(78, 220, 120), "Микрофон", "—")
        self.brain = StatusChip(QColor(255, 170, 60), "Нейросети", "Vosk + Ollama")
        self.resources = StatusChip(QColor(80, 180, 255), "Ресурсы", "—")

        layout.addWidget(self.mic, 1)
        layout.addWidget(self.brain, 1)
        layout.addWidget(self.resources, 1)
