"""Главный (и единственный) экран ассистента: реактор + статус + тост.

Раскладка:
    ┌──── 420 wide ─────────────────┐
    │      ┌─── reactor ───┐        │
    │      │   320×320     │        │
    │      └───────────────┘        │
    │         СЛУШАЮ                │  bigStatus
    │   Скажите «Акали» и команду   │  hint
    │  [====confidence bar====]     │  (2.5s timeout, скрывается)
    └───────────────────────────────┘
    [   toast overlay (временный)  ]

Никаких кнопок «Слушать» / «Переиндексировать», никакого отображения
распознанной речи, никакого AI-ответа. Акали слушает по умолчанию,
команды просто выполняются.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                                QWidget)

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
    "reindexing":       "Сканирую .desktop и KWin",
    "recovering":       "Перезапуск PipeWire / PulseAudio",
    "stopped":          "Ожидание запуска прослушивания",
    "error":            "Смотри лог в терминале",
}

# Цвета планки уверенности по методу
_CONF_COLORS = {
    "fuzzy":  QColor(255, 190, 60),   # янтарь
    "vector": QColor(0, 212, 255),    # cyan
    "llm":    QColor(100, 220, 150),  # зелёный
}


class _ConfidenceBar(QWidget):
    """Горизонтальная планка уверенности распознавания 160×8px."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._conf = 0.0
        self._color = QColor(0, 212, 255)
        self.setFixedSize(160, 8)
        self.hide()

    def set_confidence(self, conf: float, method: str) -> None:
        self._conf = max(0.0, min(1.0, conf))
        self._color = _CONF_COLORS.get(method, QColor(0, 212, 255))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            p.setPen(Qt.NoPen)
            # Track
            p.setBrush(QColor(10, 20, 35))
            p.drawRoundedRect(0, 0, w, h, 4, 4)
            # Fill
            fill = int(w * self._conf)
            if fill > 2:
                p.setBrush(self._color)
                p.drawRoundedRect(0, 0, fill, h, 4, 4)
                # Glow
                glow = QColor(self._color)
                glow.setAlpha(50)
                p.setBrush(glow)
                p.drawRoundedRect(0, 0, min(w, fill + 4), h, 4, 4)
        finally:
            p.end()


class HomePage(QWidget):
    """Экран ассистента: реактор + большой статус + toast.

    Без кнопок управления (start/stop/reindex) — Акали слушает по
    умолчанию. Сигналы оставлены ради совместимости с MainWindow, но
    больше не эмитятся.
    """

    start_clicked   = Signal()  # совместимость
    stop_clicked    = Signal()  # совместимость
    reindex_clicked = Signal()  # совместимость

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("homePage")
        self._build()
        self.set_state("stopped")

    # ── Сборка ──────────────────────────────────────────────────────
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        outer.addStretch(1)

        # ── Реактор ──
        # Реактор увеличен и стал выше, чтобы выглядеть как настоящий
        # арк-реактор Iron Man (см. референс). По вертикали отдаём ему
        # столько места, сколько есть.
        center = QHBoxLayout()
        center.addStretch(1)
        self._reactor = Reactor()
        self._reactor.setMinimumSize(360, 360)
        self._reactor.setMaximumSize(440, 440)
        self._reactor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center.addWidget(self._reactor, 0, Qt.AlignHCenter)
        center.addStretch(1)
        # stretch=1 — реактор расширяется по высоте до максимума 440.
        outer.addLayout(center, 1)

        # ── Статус + подсказка (скрыты по запросу — реактор говорит сам за себя) ──
        # Лейблы остаются в дереве виджетов как пустые / hidden, чтобы
        # set_state(...) не падал и существующий внешний код не сломался.
        self._state_text = QLabel("", self)
        self._state_text.setObjectName("bigStatus")
        self._state_text.hide()

        self._hint = QLabel("", self)
        self._hint.setObjectName("hint")
        self._hint.hide()

        # ── Планка уверенности ──
        conf_row = QHBoxLayout()
        conf_row.setContentsMargins(0, 0, 0, 0)
        conf_row.addStretch(1)

        self._conf_lbl = QLabel("уверенность")
        self._conf_lbl.setObjectName("confidenceLabel")
        self._conf_lbl.hide()
        conf_row.addWidget(self._conf_lbl)

        conf_row.addSpacing(6)

        self._conf_bar = _ConfidenceBar(self)
        conf_row.addWidget(self._conf_bar)
        conf_row.addStretch(1)
        outer.addLayout(conf_row)

        outer.addStretch(2)

        # ── Toast (overlay, не в layout) ──
        self._toast = QLabel("", self)
        self._toast.setObjectName("toastFrame")
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.hide()

    # ── Внешний API ─────────────────────────────────────────────────
    @Slot(float)
    def set_level(self, level: float) -> None:
        self._reactor.set_level(level)

    @Slot(str)
    def set_state(self, state: str) -> None:
        self._reactor.set_state(state)
        self._state_text.setText(STATE_TEXT.get(state, state))
        self._hint.setText(STATE_HINT.get(state, ""))

    @Slot(str)
    def show_spoken(self, _text: str, is_partial: bool = False) -> None:
        # Распознанная речь больше не отображается на главном экране —
        # только в логах. Метод-стуб для обратной совместимости.
        pass

    @Slot(float, str)
    def show_confidence(self, conf: float, method: str) -> None:
        self._conf_bar.set_confidence(conf, method)
        self._conf_bar.show()
        self._conf_lbl.show()
        QTimer.singleShot(2500, self, self._hide_confidence)

    @Slot(str)
    def show_llm_response(self, _text: str) -> None:
        # LLM-ответ больше не выводим в UI — только в логах.
        pass

    @Slot(str)
    def show_toast(self, text: str) -> None:
        self._toast.setText(text)
        self._toast.show()
        self._toast.raise_()
        QTimer.singleShot(2000, self, self._hide_toast)

    # Заглушки для совместимости с внешним кодом
    def set_mic_subtitle(self, _text: str) -> None:
        pass

    def set_brain_subtitle(self, _text: str) -> None:
        pass

    def set_resources_subtitle(self, _text: str) -> None:
        pass

    # ── Приватные ────────────────────────────────────────────────────
    def _hide_confidence(self) -> None:
        self._conf_bar.hide()
        self._conf_lbl.hide()

    def _hide_toast(self) -> None:
        self._toast.hide()

    # ── Toast позиционирование ───────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Toast в нижней части страницы, центрирован
        tw, th = 300, 36
        tx = (self.width() - tw) // 2
        ty = self.height() - th - 14
        self._toast.setGeometry(tx, ty, tw, th)
