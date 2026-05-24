"""Главный (и единственный) экран ассистента: реактор + статус + тост.

Раскладка:
    +---- 420 wide ----------------+
    |      AKALI v0.3.0            |  branding (top)
    |  ---- thin cyan line ----    |
    |      +--- reactor ---+       |
    |      |   320x320     |       |
    |      +---------------+       |
    |         СЛУШАЮ               |  bigStatus (color-synced)
    |   Скажите "Акали" и команду  |  hint
    |  ---- thin separator ----    |
    |  [====confidence bar====]    |  (2.5s timeout)
    +------------------------------+
    [   toast overlay (временный)  ]
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                                QVBoxLayout, QWidget)

from ... import __version__
from ..widgets import Reactor


STATE_TEXT = {
    "starting":         "ЗАПУСК",
    "listening":        "СЛУШАЮ",
    "waiting_command":  "ЖДУ КОМАНДУ",
    "processing":       "ВЫПОЛНЯЮ",
    "reindexing":       "РЕИНДЕКС",
    "recovering":       "ВОССТАНОВЛЕНИЕ",
    "stopped":          "ГОТОВ",
    "error":            "ОШИБКА",
}

STATE_HINT = {
    "starting":         "Загружаю Vosk и подключаю микрофон...",
    "listening":        'Скажите "Акали" и команду',
    "waiting_command":  "Произнесите команду...",
    "processing":       "Команда найдена, запускаю",
    "reindexing":       "Сканирую .desktop и KWin",
    "recovering":       "Перезапуск PipeWire / PulseAudio",
    "stopped":          "Ожидание запуска прослушивания",
    "error":            "Смотри лог в терминале",
}

STATE_ACCENT_CSS = {
    "starting":        "#ffc850",
    "listening":       "#00dcff",
    "waiting_command": "#ffc850",
    "processing":      "#78ffb4",
    "reindexing":      "#78a0ff",
    "recovering":      "#ff5a5a",
    "stopped":         "#00dcff",
    "error":           "#ff5a5a",
}

_CONF_COLORS = {
    "fuzzy":  QColor(255, 190, 60),
    "vector": QColor(0, 212, 255),
    "llm":    QColor(100, 220, 150),
}

_METHOD_LABELS = {
    "fuzzy":  "FUZZY",
    "vector": "VECTOR",
    "llm":    "LLM",
}


class _ThinSeparator(QFrame):
    """Тонкая декоративная линия с мягким cyan-оттенком."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("homeSeparator")
        self.setFrameShape(QFrame.HLine)
        self.setFixedHeight(1)


class _ConfidenceBar(QWidget):
    """Горизонтальная планка уверенности распознавания с glow-эффектом."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._conf = 0.0
        self._color = QColor(0, 212, 255)
        self.setFixedSize(240, 6)
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
            p.setBrush(QColor(8, 16, 28))
            p.drawRoundedRect(0, 0, w, h, 3, 3)
            fill = int(w * self._conf)
            if fill > 2:
                p.setBrush(self._color)
                p.drawRoundedRect(0, 0, fill, h, 3, 3)
                glow = QColor(self._color)
                glow.setAlpha(35)
                p.setBrush(glow)
                p.drawRoundedRect(0, 0, min(w, fill + 6), h + 2, 3, 3)
        finally:
            p.end()


class HomePage(QWidget):
    """Экран ассистента: реактор + большой статус + toast."""

    start_clicked   = Signal()
    stop_clicked    = Signal()
    reindex_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("homePage")
        self._current_state = "stopped"
        self._build()
        self.set_state("stopped")

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 16)
        outer.setSpacing(0)

        # -- Branding --
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.addStretch(1)

        self._brand = QLabel(f"AKALI  v{__version__}")
        self._brand.setObjectName("homeBrand")
        self._brand.setAlignment(Qt.AlignCenter)
        brand_row.addWidget(self._brand)
        brand_row.addStretch(1)
        outer.addLayout(brand_row)

        outer.addSpacing(6)
        outer.addWidget(_ThinSeparator())
        outer.addSpacing(4)

        outer.addStretch(1)

        # -- Reactor --
        center = QHBoxLayout()
        center.addStretch(1)
        self._reactor = Reactor()
        self._reactor.setMinimumSize(360, 360)
        self._reactor.setMaximumSize(440, 440)
        self._reactor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        center.addWidget(self._reactor, 0, Qt.AlignHCenter)
        center.addStretch(1)
        outer.addLayout(center, 1)

        outer.addSpacing(10)

        # -- Status text --
        self._state_text = QLabel("")
        self._state_text.setObjectName("bigStatus")
        self._state_text.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._state_text)

        outer.addSpacing(4)

        # -- Hint --
        self._hint = QLabel("")
        self._hint.setObjectName("hint")
        self._hint.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._hint)

        outer.addSpacing(10)
        outer.addWidget(_ThinSeparator())
        outer.addSpacing(8)

        # -- Confidence bar + method --
        conf_row = QHBoxLayout()
        conf_row.setContentsMargins(0, 0, 0, 0)
        conf_row.addStretch(1)

        self._conf_method = QLabel("")
        self._conf_method.setObjectName("confidenceMethod")
        self._conf_method.hide()
        conf_row.addWidget(self._conf_method)

        conf_row.addSpacing(8)

        self._conf_bar = _ConfidenceBar(self)
        conf_row.addWidget(self._conf_bar)

        conf_row.addSpacing(8)

        self._conf_lbl = QLabel("")
        self._conf_lbl.setObjectName("confidenceLabel")
        self._conf_lbl.hide()
        conf_row.addWidget(self._conf_lbl)

        conf_row.addStretch(1)
        outer.addLayout(conf_row)

        outer.addStretch(2)

        # -- Toast (overlay, не в layout) --
        self._toast = QLabel("", self)
        self._toast.setObjectName("toastFrame")
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.hide()

    # -- Public API --
    @Slot(float)
    def set_level(self, level: float) -> None:
        self._reactor.set_level(level)

    @Slot(str)
    def set_state(self, state: str) -> None:
        self._current_state = state
        self._reactor.set_state(state)
        self._state_text.setText(STATE_TEXT.get(state, state))
        self._hint.setText(STATE_HINT.get(state, ""))
        accent = STATE_ACCENT_CSS.get(state, "#00dcff")
        self._state_text.setStyleSheet(f"color: {accent};")

    @Slot(str)
    def show_spoken(self, _text: str, is_partial: bool = False) -> None:
        pass

    @Slot(float, str)
    def show_confidence(self, conf: float, method: str) -> None:
        self._conf_bar.set_confidence(conf, method)
        self._conf_bar.show()
        self._conf_method.setText(_METHOD_LABELS.get(method, method.upper()))
        self._conf_method.show()
        pct = f"{conf * 100:.0f}%"
        self._conf_lbl.setText(pct)
        self._conf_lbl.show()
        QTimer.singleShot(2500, self, self._hide_confidence)

    @Slot(str)
    def show_llm_response(self, _text: str) -> None:
        pass

    @Slot(str)
    def show_toast(self, text: str) -> None:
        self._toast.setText(f"  {text}  ")
        self._toast.show()
        self._toast.raise_()
        QTimer.singleShot(2500, self, self._hide_toast)

    def set_mic_subtitle(self, _text: str) -> None:
        pass

    def set_brain_subtitle(self, _text: str) -> None:
        pass

    def set_resources_subtitle(self, _text: str) -> None:
        pass

    # -- Private --
    def _hide_confidence(self) -> None:
        self._conf_bar.hide()
        self._conf_lbl.hide()
        self._conf_method.hide()

    def _hide_toast(self) -> None:
        self._toast.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        tw, th = 340, 38
        tx = (self.width() - tw) // 2
        ty = self.height() - th - 16
        self._toast.setGeometry(tx, ty, tw, th)
