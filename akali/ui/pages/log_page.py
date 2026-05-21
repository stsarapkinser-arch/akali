"""Страница «Лог»: накопительный текстовый журнал событий."""
from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QPlainTextEdit, QPushButton,
                                QVBoxLayout, QWidget)


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


class LogPage(QWidget):
    """Простой rolling-log на 2000 строк."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("logPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._btn_clear = QPushButton("Очистить")
        self._btn_clear.setObjectName("secondaryBtn")
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        self._btn_clear.clicked.connect(self._clear)
        row.addWidget(self._btn_clear)
        row.addStretch(1)
        outer.addLayout(row)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(2000)
        outer.addWidget(self._text, 1)

    def append(self, line: str) -> None:
        self._text.appendPlainText(f"[{_ts()}] {line}")

    def _clear(self) -> None:
        self._text.clear()
