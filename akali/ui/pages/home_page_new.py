"""Главная страница (refactored) — Arc Reactor + Info Panel.

Компоненты:
  • Reactor — центральный виджет с pulsing arc reactor визуализацией
  • Status display — текущее состояние (listening, processing, error)
  • Info panel — микрофон, модель, RAM, задержка
  • Control buttons — start/stop/reindex
  • Spoken text display — распознанный текст в реальном времени
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                                QVBoxLayout, QWidget)

from ..icons import IconSet, led_icon, COLOR_GREEN, COLOR_YELLOW, COLOR_RED


class ReactorWidget(QWidget):
    """Arc Reactor визуализация (pulsing circle с ring)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("reactorWidget")
        self.setMinimumSize(200, 200)

        # Пульсирующая анимация
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(50)  # 20 FPS
        self._pulse_timer.timeout.connect(self.update)

        self._pulse_phase = 0
        self._is_active = False

    def start_pulse(self) -> None:
        """Начать пульсацию."""
        self._is_active = True
        self._pulse_timer.start()
        self.update()

    def stop_pulse(self) -> None:
        """Остановить пульсацию."""
        self._is_active = False
        self._pulse_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:
        """Рисуем arc reactor."""
        from PySide6.QtGui import QBrush, QPainter, QPen
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        radius = min(w, h) // 3

        # Фон
        painter.fillRect(self.rect(), QColor("#0D1117"))

        if self._is_active:
            # Пульсирующие кольца
            alpha_base = int(100 + 50 * math.sin(self._pulse_phase * 0.1))
            for i in range(3):
                r = radius + i * 10
                alpha = max(0, alpha_base - i * 30)
                ring_color = QColor("#00D4FF")
                ring_color.setAlpha(alpha)
                pen = QPen(ring_color, 2)
                painter.setPen(pen)
                painter.drawEllipse(cx - r, cy - r, 2*r, 2*r)

            # Центральная сфера
            painter.fillEllipse(cx - radius // 2, cy - radius // 2,
                                radius, radius, QColor("#00D4FF"))

            # Свечение
            glow_color = QColor(255, 255, 255, 80)
            painter.fillEllipse(cx - radius // 3, cy - radius // 3,
                                radius // 2, radius // 2, glow_color)

            self._pulse_phase += 1
        else:
            # Неактивный режим — dim circle
            pen = QPen(QColor("#30363D"), 2)
            painter.setPen(pen)
            painter.drawEllipse(cx - radius, cy - radius, 2*radius, 2*radius)

        painter.end()


class InfoPanel(QFrame):
    """Информационная панель (микрофон, модель, RAM, статус)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("infoPanel")
        self.setFrameShape(QFrame.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        # ── Mic + Model (горизонтально) ──
        row1 = QHBoxLayout()
        row1.setSpacing(16)

        mic_icon = led_icon(10, COLOR_GREEN, active=False)
        mic_label = QLabel("Микрофон")
        mic_label.setStyleSheet("color: #E6EDF3; font-size: 10px;")
        self.mic_status = QLabel("не инициализирован")
        self.mic_status.setStyleSheet("color: #8B949E; font-size: 9px;")
        mic_col = QVBoxLayout()
        mic_col.setContentsMargins(0, 0, 0, 0)
        mic_col.setSpacing(2)
        mic_col.addWidget(mic_label)
        mic_col.addWidget(self.mic_status)
        row1.addLayout(mic_col)

        model_label = QLabel("Модель")
        model_label.setStyleSheet("color: #E6EDF3; font-size: 10px;")
        self.model_status = QLabel("—")
        self.model_status.setStyleSheet("color: #8B949E; font-size: 9px;")
        model_col = QVBoxLayout()
        model_col.setContentsMargins(0, 0, 0, 0)
        model_col.setSpacing(2)
        model_col.addWidget(model_label)
        model_col.addWidget(self.model_status)
        row1.addLayout(model_col)

        row1.addStretch()
        layout.addLayout(row1)

        # ── RAM + Ping (горизонтально) ──
        row2 = QHBoxLayout()
        row2.setSpacing(16)

        ram_label = QLabel("RAM")
        ram_label.setStyleSheet("color: #E6EDF3; font-size: 10px;")
        self.ram_status = QLabel("—")
        self.ram_status.setStyleSheet("color: #00D4FF; font-size: 9px; font-weight: bold;")
        ram_col = QVBoxLayout()
        ram_col.setContentsMargins(0, 0, 0, 0)
        ram_col.setSpacing(2)
        ram_col.addWidget(ram_label)
        ram_col.addWidget(self.ram_status)
        row2.addLayout(ram_col)

        ping_label = QLabel("Ping")
        ping_label.setStyleSheet("color: #E6EDF3; font-size: 10px;")
        self.ping_status = QLabel("—")
        self.ping_status.setStyleSheet("color: #8B949E; font-size: 9px;")
        ping_col = QVBoxLayout()
        ping_col.setContentsMargins(0, 0, 0, 0)
        ping_col.setSpacing(2)
        ping_col.addWidget(ping_label)
        ping_col.addWidget(self.ping_status)
        row2.addLayout(ping_col)

        row2.addStretch()
        layout.addLayout(row2)

        self.setLayout(layout)

    @Slot(str)
    def set_mic_status(self, status: str) -> None:
        self.mic_status.setText(status)

    @Slot(str)
    def set_model_status(self, model: str) -> None:
        self.model_status.setText(model[:20] if model else "—")

    @Slot(str)
    def set_ram_status(self, ram: str) -> None:
        self.ram_status.setText(ram)

    @Slot(str)
    def set_ping_status(self, ping: str) -> None:
        self.ping_status.setText(ping)


class HomePage(QWidget):
    """Главная страница (refactored) с Arc Reactor и панелями управления."""

    start_clicked = Signal()
    stop_clicked = Signal()
    reindex_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("homePage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(12)

        # ── Reactor (центр) ──
        self.reactor = ReactorWidget()
        layout.addWidget(self.reactor, 1, Qt.AlignCenter)

        # ── Status text ──
        self.status_label = QLabel("Инициализация...")
        self.status_label.setObjectName("reactorStatus")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("""
            QLabel#reactorStatus {
                color: #00D4FF;
                font-size: 12px;
                font-weight: bold;
                margin: 8px 0px;
            }
        """)
        layout.addWidget(self.status_label)

        # ── Spoken text display ──
        self.spoken_label = QLabel("")
        self.spoken_label.setWordWrap(True)
        self.spoken_label.setAlignment(Qt.AlignCenter)
        self.spoken_label.setStyleSheet("""
            QLabel {
                color: #8B949E;
                font-size: 10px;
                margin: 0px 12px;
                padding: 6px;
                border-radius: 4px;
                background-color: #161B22;
                min-height: 30px;
            }
        """)
        layout.addWidget(self.spoken_label)

        # ── Control buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.setContentsMargins(12, 0, 12, 0)

        self.btn_start = QPushButton("🎙 Слушать")
        self.btn_start.setObjectName("primaryButton")
        self.btn_start.clicked.connect(self.start_clicked.emit)

        self.btn_stop = QPushButton("⏹ Остановить")
        self.btn_stop.setObjectName("secondaryButton")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)

        self.btn_reindex = QPushButton("🔄 Переиндекс")
        self.btn_reindex.setObjectName("secondaryButton")
        self.btn_reindex.clicked.connect(self.reindex_clicked.emit)

        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addWidget(self.btn_reindex)

        layout.addLayout(btn_layout)

        # ── Info panel ──
        self.info_panel = InfoPanel()
        layout.addWidget(self.info_panel)

        self.setLayout(layout)
        self._state = "stopped"

    @Slot(str)
    def set_state(self, state: str) -> None:
        """Обновляет состояние (listening, processing, error и т.д.)."""
        self._state = state
        self.status_label.setText(self._state_to_text(state))

        # Управляем кнопками и анимацией
        if state in ("listening", "waiting_command"):
            self.reactor.start_pulse()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
        elif state == "processing":
            self.reactor.start_pulse()
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
        elif state in ("stopped", "error"):
            self.reactor.stop_pulse()
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
        else:
            self.reactor.stop_pulse()

    @Slot(str)
    def show_spoken(self, text: str) -> None:
        """Показывает распознанный текст в реальном времени."""
        self.spoken_label.setText(f"« {text} »")

    @Slot(float)
    def set_level(self, level: float) -> None:
        """Обновляет уровень микрофона (можно использовать для визуализации)."""
        pass  # Пока не используется, но зарезервировано

    @staticmethod
    def _state_to_text(state: str) -> str:
        """Переводит состояние в читаемый текст."""
        mapping = {
            "starting": "🚀 Запуск...",
            "listening": "🎤 Слушаю...",
            "waiting_command": "⏳ Жду команду...",
            "processing": "⚙️ Обрабатываю...",
            "reindexing": "🔄 Переиндексирую...",
            "recovering": "🔧 Восстанавливаю...",
            "stopped": "⏸ Готов",
            "error": "❌ Ошибка",
        }
        return mapping.get(state, state)
