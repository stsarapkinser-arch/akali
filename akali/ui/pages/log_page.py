"""Страница «Лог»: компактный rolling-log + панель управления.

Раскладка:

    Журнал событий                              ← pageTitle
    ────────────────────────────────────────    ← pageSubtitle
    [Очистить]    [Скопировать]
    ┌──────────────────────────────────────┐
    │ [12:34:56] ⚙ Загружена база: …       │   ← QPlainTextEdit, mono
    │ [12:34:57] ▶ FUZZY 0.92 «…» → cmd   │
    │ …                                    │
    └──────────────────────────────────────┘
"""
from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                                QVBoxLayout, QWidget)


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


class LogPage(QWidget):
    """Простой rolling-log на 2000 строк."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("logPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        title = QLabel("Журнал событий")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        self._subtitle = QLabel("Последние 2000 событий")
        self._subtitle.setObjectName("pageSubtitle")
        outer.addWidget(self._subtitle)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._btn_clear = QPushButton("Очистить")
        self._btn_clear.setObjectName("secondaryBtn")
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        self._btn_clear.clicked.connect(self._clear)
        row.addWidget(self._btn_clear)

        self._btn_copy = QPushButton("Скопировать")
        self._btn_copy.setObjectName("secondaryBtn")
        self._btn_copy.setCursor(Qt.PointingHandCursor)
        self._btn_copy.clicked.connect(self._copy)
        row.addWidget(self._btn_copy)
        row.addStretch(1)
        outer.addLayout(row)

        self._text = QPlainTextEdit()
        self._text.setObjectName("logView")
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(2000)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        outer.addWidget(self._text, 1)

    def append(self, line: str) -> None:
        self._text.appendPlainText(f"[{_ts()}] {line}")

    def _clear(self) -> None:
        self._text.clear()

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._text.toPlainText())
