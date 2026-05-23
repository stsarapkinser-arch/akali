"""Главный экран: реактор + статус + кнопки управления.

Раскладка (окно ~420):

    ┌──── 420 wide ─────────────────┐
    │                               │
    │      ┌─── reactor ───┐        │
    │      │   320×320     │        │
    │      └───────────────┘        │
    │         СЛУШАЮ                │  bigStatus
    │   Скажите «Акали» и команду   │  hint
    │  [====confidence bar====]     │  (2.5s timeout)
    │                               │
    │   ┌───── СЛУШАТЬ ──────┐      │  primaryBtn
    │   ┌─ Переиндексировать ┐      │  secondaryBtn
    │                               │
    │───────────────────────────────│
    │  «последнее» [⎘]              │  spokenText + copy
    │  ╔══════════════════════╗     │  llmPanel (hidden)
    │  ║ AI ОТВЕТ: ...        ║     │
    │  ╚══════════════════════╝     │
    └───────────────────────────────┘
    [  toast overlay (временный)  ]
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
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
    "reindexing":       "Сканирую .desktop и KWin",
    "recovering":       "Перезапуск PipeWire / PulseAudio",
    "stopped":          "Нажмите «Слушать», чтобы активировать",
    "error":            "См. вкладку «Лог»",
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
    """Экран ассистента: реактор + большой статус + кнопки."""

    start_clicked   = Signal()
    stop_clicked    = Signal()
    reindex_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("homePage")
        self._listening = False
        self._last_spoken = ""
        self._build()
        self.set_state("stopped")

    # ── Сборка ──────────────────────────────────────────────────────
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 6, 16, 10)
        outer.setSpacing(5)

        # ── Реактор ──
        center = QHBoxLayout()
        center.addStretch(1)
        self._reactor = Reactor()
        self._reactor.setMinimumSize(280, 280)
        self._reactor.setMaximumSize(320, 320)
        self._reactor.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        center.addWidget(self._reactor, 0, Qt.AlignHCenter)
        center.addStretch(1)
        outer.addLayout(center)

        # ── Статус + подсказка ──
        self._state_text = QLabel("")
        self._state_text.setObjectName("bigStatus")
        self._state_text.setAlignment(Qt.AlignHCenter)
        outer.addWidget(self._state_text)

        self._hint = QLabel("")
        self._hint.setObjectName("hint")
        self._hint.setAlignment(Qt.AlignHCenter)
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)

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

        outer.addSpacing(4)

        # ── Кнопки ──
        self._btn_listen = QPushButton("Слушать")
        self._btn_listen.setObjectName("primaryBtn")
        self._btn_listen.setCursor(Qt.PointingHandCursor)
        self._btn_listen.setMinimumHeight(40)
        self._btn_listen.clicked.connect(self._on_listen_clicked)
        outer.addWidget(self._btn_listen)

        self._btn_reindex = QPushButton("Переиндексировать")
        self._btn_reindex.setObjectName("secondaryBtn")
        self._btn_reindex.setCursor(Qt.PointingHandCursor)
        self._btn_reindex.setMinimumHeight(32)
        self._btn_reindex.clicked.connect(self.reindex_clicked.emit)
        outer.addWidget(self._btn_reindex)

        outer.addStretch(1)

        # ── Разделитель ──
        sep = QFrame()
        sep.setObjectName("homeSeparator")
        sep.setFrameShape(QFrame.HLine)
        outer.addWidget(sep)

        # ── Spoken text + кнопка копирования ──
        spoken_row = QHBoxLayout()
        spoken_row.setContentsMargins(0, 2, 0, 0)
        spoken_row.setSpacing(4)

        self._spoken = QLabel("")
        self._spoken.setObjectName("spokenText")
        self._spoken.setWordWrap(False)
        self._spoken.setMinimumHeight(24)
        self._spoken.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        spoken_row.addWidget(self._spoken, 1)

        self._btn_copy = QPushButton("⎘")
        self._btn_copy.setObjectName("copyBtn")
        self._btn_copy.setFixedSize(22, 22)
        self._btn_copy.setCursor(Qt.PointingHandCursor)
        self._btn_copy.setToolTip("Копировать")
        self._btn_copy.clicked.connect(self._copy_spoken)
        spoken_row.addWidget(self._btn_copy)
        outer.addLayout(spoken_row)

        # ── LLM-панель ──
        self._llm_panel = QFrame(self)
        self._llm_panel.setObjectName("llmPanel")
        self._llm_panel.setFrameShape(QFrame.NoFrame)
        llm_v = QVBoxLayout(self._llm_panel)
        llm_v.setContentsMargins(10, 8, 10, 8)
        llm_v.setSpacing(4)

        llm_title = QLabel("AI ОТВЕТ")
        llm_title.setObjectName("llmTitle")
        llm_v.addWidget(llm_title)

        self._llm_text = QLabel("")
        self._llm_text.setObjectName("llmText")
        self._llm_text.setWordWrap(True)
        llm_v.addWidget(self._llm_text)

        self._llm_panel.hide()
        outer.addWidget(self._llm_panel)

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
        self._listening = state in (
            "listening", "waiting_command", "processing",
            "recovering", "starting", "reindexing",
        )
        self._update_buttons()

    @Slot(str)
    def show_spoken(self, text: str, is_partial: bool = False) -> None:
        if len(text) > 64:
            text = text[:61] + "…"
        if is_partial:
            self._spoken.setProperty("partial", "true")
            self._spoken.setText(f"«{text}▌»")
        else:
            self._last_spoken = text
            self._spoken.setProperty("partial", "false")
            self._spoken.setText(f"«{text}»")
        self._spoken.style().unpolish(self._spoken)
        self._spoken.style().polish(self._spoken)

    @Slot(float, str)
    def show_confidence(self, conf: float, method: str) -> None:
        self._conf_bar.set_confidence(conf, method)
        self._conf_bar.show()
        self._conf_lbl.show()
        QTimer.singleShot(2500, self, self._hide_confidence)

    @Slot(str)
    def show_llm_response(self, text: str) -> None:
        if not text.strip():
            return
        if len(text) > 300:
            text = text[:297] + "…"
        self._llm_text.setText(text)
        self._llm_panel.show()

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

    def _copy_spoken(self) -> None:
        text = self._last_spoken or self._spoken.text().strip("«»")
        if text:
            QGuiApplication.clipboard().setText(text)

    def _hide_confidence(self) -> None:
        self._conf_bar.hide()
        self._conf_lbl.hide()

    def _hide_toast(self) -> None:
        self._toast.hide()

    # ── Toast позиционирование ───────────────────────────────────────
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Toast в нижней части страницы, центрирован
        tw, th = 300, 36
        tx = (self.width() - tw) // 2
        ty = self.height() - th - 10
        self._toast.setGeometry(tx, ty, tw, th)
