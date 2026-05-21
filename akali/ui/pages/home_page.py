"""Главный экран: компактный, в стиле Jarvis-виджета.

Раскладка (для окна ~400×720):

    ┌──── 400 wide ─────┐
    │ [statusBadge]     │  верхняя строка
    │                   │
    │   ┌─ reactor ─┐   │
    │   │  240×240  │   │
    │   └───────────┘   │
    │      СЛУШАЮ       │  bigStatus
    │   Скажите Акали   │  hint
    │                   │
    │   ┌── SЛУШАТЬ ──┐ │  primary
    │   ┌─Переиндекс─┐  │  secondary
    │                   │
    │   « последняя »   │  spokenText
    └───────────────────┘
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                                QSizePolicy, QVBoxLayout, QWidget)

from ..widgets import Reactor


STATE_TEXT = {
    "starting":         "Запуск…",
    "listening":        "Слушаю",
    "waiting_command":  "Жду команду",
    "processing":       "Выполняю",
    "reindexing":       "Реиндекс",
    "recovering":       "Восстанавливаю",
    "stopped":          "Готов",
    "error":            "Ошибка",
}

STATE_HINT = {
    "starting":         "Загружаю Vosk и подключаю микрофон…",
    "listening":        "Скажите «Акали» и команду",
    "waiting_command":  "Произнесите команду…",
    "processing":       "Команда найдена, запускаю",
    "reindexing":       "Сканирую `.desktop` и KWin",
    "recovering":       "Перезапуск PipeWire / PulseAudio",
    "stopped":          "Нажмите «Слушать», чтобы активировать",
    "error":            "См. вкладку «Лог»",
}


class HomePage(QWidget):
    """Экран ассистента: реактор + большая кнопка + статус."""

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
        outer.setContentsMargins(16, 8, 16, 12)
        outer.setSpacing(6)

        # Бейдж статуса — узкая полоска сверху
        top = QHBoxLayout()
        top.setSpacing(0)
        top.addStretch(1)
        self._badge = QLabel("Готов")
        self._badge.setObjectName("statusBadge")
        self._badge.setAlignment(Qt.AlignCenter)
        top.addWidget(self._badge)
        top.addStretch(1)
        outer.addLayout(top)

        # ── Центральный реактор ──
        center = QHBoxLayout()
        center.addStretch(1)
        self._reactor = Reactor()
        self._reactor.setMinimumSize(240, 240)
        self._reactor.setMaximumSize(280, 280)
        self._reactor.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        center.addWidget(self._reactor, 0, Qt.AlignHCenter)
        center.addStretch(1)
        outer.addLayout(center)

        # Большая надпись + подсказка
        self._state_text = QLabel("")
        self._state_text.setObjectName("bigStatus")
        self._state_text.setAlignment(Qt.AlignHCenter)
        outer.addWidget(self._state_text)

        self._hint = QLabel("")
        self._hint.setObjectName("hint")
        self._hint.setAlignment(Qt.AlignHCenter)
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)

        outer.addSpacing(6)

        # Главная кнопка — на всю ширину
        self._btn_listen = QPushButton("Слушать")
        self._btn_listen.setObjectName("primaryBtn")
        self._btn_listen.setCursor(Qt.PointingHandCursor)
        self._btn_listen.setMinimumHeight(40)
        self._btn_listen.clicked.connect(self._on_listen_clicked)
        outer.addWidget(self._btn_listen)

        # Вторичная — реиндекс
        self._btn_reindex = QPushButton("Переиндексировать")
        self._btn_reindex.setObjectName("secondaryBtn")
        self._btn_reindex.setCursor(Qt.PointingHandCursor)
        self._btn_reindex.setMinimumHeight(32)
        self._btn_reindex.clicked.connect(self.reindex_clicked.emit)
        outer.addWidget(self._btn_reindex)

        outer.addStretch(1)

        # Лента «последнее распознано» внизу
        sep = QFrame()
        sep.setObjectName("homeSeparator")
        sep.setFrameShape(QFrame.HLine)
        outer.addWidget(sep)

        self._spoken = QLabel("")
        self._spoken.setObjectName("spokenText")
        self._spoken.setWordWrap(True)
        self._spoken.setAlignment(Qt.AlignHCenter)
        self._spoken.setMinimumHeight(28)
        outer.addWidget(self._spoken)

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
        # Ограничиваем длину, чтобы не разрывало раскладку
        if len(text) > 64:
            text = text[:61] + "…"
        self._spoken.setText(f"«{text}»")

    # Заглушки чтобы внешний код мог звать их даже без status_row
    def set_mic_subtitle(self, _text: str) -> None:
        pass

    def set_brain_subtitle(self, _text: str) -> None:
        pass

    def set_resources_subtitle(self, _text: str) -> None:
        pass

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
