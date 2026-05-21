"""Главный экран: статус, центральный реактор, нижняя строка статусов.

Раскладка (схематично):

    ┌────────────────────────────────────────────────┐
    │                                                │
    │   [статус-бейдж]                       [текст] │
    │                                                │
    │                                                │
    │               ┌──── REACTOR ────┐              │
    │               │                 │              │
    │               │      core       │              │
    │               │                 │              │
    │               └─────────────────┘              │
    │                                                │
    │      [старт/стоп]    [переиндексировать]       │
    │                                                │
    │   🟢 Микрофон   🟡 Нейросети   🔵 Ресурсы      │
    └────────────────────────────────────────────────┘
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                                QSizePolicy, QVBoxLayout, QWidget)

from ..widgets import Reactor, StatusRow


STATE_TEXT = {
    "starting":         "Запуск…",
    "listening":        "Слушаю",
    "waiting_command":  "Жду команду",
    "processing":       "Выполняю",
    "reindexing":       "Реиндексирую систему",
    "recovering":       "Восстанавливаю аудио",
    "stopped":          "Готов к работе",
    "error":            "Ошибка",
}

STATE_HINT = {
    "starting":         "Загружаю Vosk и подключаю микрофон…",
    "listening":        "Скажите «Акали» и команду",
    "waiting_command":  "Произнесите команду в течение 5 секунд",
    "processing":       "Команда найдена, запускаю…",
    "reindexing":       "Сканирую `.desktop` и KWin",
    "recovering":       "Перезапускаю PipeWire / PulseAudio",
    "stopped":          "Нажмите «Слушать», чтобы активировать ассистент",
    "error":            "См. вкладку «Лог»",
}


class HomePage(QWidget):
    """Экран ассистента в стиле Iron-Man / Jarvis."""

    start_clicked = Signal()
    stop_clicked = Signal()
    reindex_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("homePage")
        self._listening = False
        self._build()
        self.set_state("stopped")

    # ── Сборка ────────────────────────────────────────────────────────
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 8, 20, 16)
        outer.setSpacing(8)

        # === Верхняя строка: бейдж статуса слева, hint справа =========
        top = QHBoxLayout()
        top.setSpacing(12)
        self._badge = QLabel("Готов")
        self._badge.setObjectName("statusBadge")
        top.addWidget(self._badge)
        top.addStretch(1)
        self._spoken = QLabel("")
        self._spoken.setObjectName("spokenText")
        self._spoken.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self._spoken)
        outer.addLayout(top)

        # === Центральный реактор ====================================
        center = QHBoxLayout()
        center.addStretch(1)
        self._reactor = Reactor()
        self._reactor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._reactor.setMinimumSize(360, 360)
        center.addWidget(self._reactor, 0, Qt.AlignHCenter)
        center.addStretch(1)
        outer.addLayout(center, 1)

        # Подпись под реактором
        self._state_text = QLabel("")
        self._state_text.setObjectName("bigStatus")
        self._state_text.setAlignment(Qt.AlignHCenter)
        outer.addWidget(self._state_text)

        self._hint = QLabel("")
        self._hint.setObjectName("hint")
        self._hint.setAlignment(Qt.AlignHCenter)
        outer.addWidget(self._hint)

        # === Кнопки управления ======================================
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch(1)
        self._btn_listen = QPushButton("Слушать")
        self._btn_listen.setObjectName("primaryBtn")
        self._btn_listen.setMinimumWidth(160)
        self._btn_listen.setCursor(Qt.PointingHandCursor)
        self._btn_listen.clicked.connect(self._on_listen_clicked)
        btn_row.addWidget(self._btn_listen)

        self._btn_reindex = QPushButton("Переиндексировать")
        self._btn_reindex.setObjectName("secondaryBtn")
        self._btn_reindex.setCursor(Qt.PointingHandCursor)
        self._btn_reindex.clicked.connect(self.reindex_clicked.emit)
        btn_row.addWidget(self._btn_reindex)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        # === Нижняя строка статусов =================================
        outer.addSpacing(4)
        separator = QFrame()
        separator.setObjectName("homeSeparator")
        separator.setFrameShape(QFrame.HLine)
        outer.addWidget(separator)

        self._status_row = StatusRow()
        outer.addWidget(self._status_row)

    # ── Внешний API ───────────────────────────────────────────────────
    @Slot(float)
    def set_level(self, level: float) -> None:
        self._reactor.set_level(level)

    @Slot(str)
    def set_state(self, state: str) -> None:
        self._reactor.set_state(state)
        self._badge.setText(STATE_TEXT.get(state, state))
        self._badge.setProperty("state", state)
        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)
        self._state_text.setText(STATE_TEXT.get(state, state))
        self._hint.setText(STATE_HINT.get(state, ""))
        self._listening = state in (
            "listening", "waiting_command", "processing", "recovering",
            "starting", "reindexing",
        )
        self._update_buttons()

    @Slot(str)
    def show_spoken(self, text: str) -> None:
        self._spoken.setText(f"«{text}»")

    def set_mic_subtitle(self, text: str) -> None:
        self._status_row.mic.set_subtitle(text)

    def set_brain_subtitle(self, text: str) -> None:
        self._status_row.brain.set_subtitle(text)

    def set_resources_subtitle(self, text: str) -> None:
        self._status_row.resources.set_subtitle(text)

    # ── Поведение кнопок ─────────────────────────────────────────────
    def _on_listen_clicked(self) -> None:
        if self._listening:
            self.stop_clicked.emit()
        else:
            self.start_clicked.emit()

    def _update_buttons(self) -> None:
        if self._listening:
            self._btn_listen.setText("Остановить")
            self._btn_listen.setProperty("danger", True)
        else:
            self._btn_listen.setText("Слушать")
            self._btn_listen.setProperty("danger", False)
        self._btn_listen.style().unpolish(self._btn_listen)
        self._btn_listen.style().polish(self._btn_listen)
