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
    """Arc Reactor визуализация (яркие пульсирующие кольца + вращающиеся сегменты)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("reactorWidget")
        self.setMinimumSize(240, 240)

        # Пульсирующая анимация (60 FPS)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(16)
        self._pulse_timer.timeout.connect(self.update)

        self._pulse_phase = 0
        self._rotation_phase = 0
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
        """Рисуем arc reactor с яркой анимацией."""
        from PySide6.QtGui import QBrush, QPainter, QPen
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        base_radius = min(w, h) // 3.5

        # Фон
        painter.fillRect(self.rect(), QColor("#0D1117"))

        if self._is_active:
            # === ВНЕШНИЕ ПУЛЬСИРУЮЩИЕ КОЛЬЦА (5 слоёв) ===
            pulse_intensity = 0.5 + 0.5 * math.sin(self._pulse_phase * 0.08)

            for i in range(5):
                r = base_radius + i * 12
                alpha = int(255 * pulse_intensity * (1 - i / 5))
                ring_color = QColor("#00D4FF")
                ring_color.setAlpha(alpha)
                pen = QPen(ring_color, 3 if i < 2 else 2)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(cx - r, cy - r, 2*r, 2*r)

            # === ВРАЩАЮЩИЕСЯ СЕГМЕНТЫ (как на реальном Arc Reactor) ===
            segment_radius = base_radius + 5
            num_segments = 12
            segment_angle = 360 / num_segments
            rotation = (self._rotation_phase * 3) % 360

            for i in range(num_segments):
                angle = (i * segment_angle + rotation) * math.pi / 180

                if i % 2 == 0:
                    seg_color = QColor("#00D4FF")
                    seg_alpha = 200
                else:
                    seg_color = QColor("#00A8CC")
                    seg_alpha = 100

                seg_color.setAlpha(seg_alpha)
                pen = QPen(seg_color, 4)
                painter.setPen(pen)

                x1 = cx + segment_radius * math.cos(angle)
                y1 = cy + segment_radius * math.sin(angle)
                x2 = cx + (segment_radius + 8) * math.cos(angle)
                y2 = cy + (segment_radius + 8) * math.sin(angle)
                painter.drawLine(int(x1), int(y1), int(x2), int(y2))

            # === ЦЕНТРАЛЬНАЯ СФЕРА (яркая и светящаяся) ===
            core_radius = int(base_radius * 0.6)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#00FFFF")))
            painter.drawEllipse(cx - core_radius, cy - core_radius,
                               2 * core_radius, 2 * core_radius)

            inner_glow = int(core_radius * 0.5)
            glow1 = QColor(255, 255, 255, 150)
            painter.setBrush(QBrush(glow1))
            painter.drawEllipse(cx - inner_glow, cy - inner_glow,
                               2 * inner_glow, 2 * inner_glow)

            center_point = int(core_radius * 0.25)
            painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
            painter.drawEllipse(cx - center_point, cy - center_point,
                               2 * center_point, 2 * center_point)

            # === ВНЕШНЕЕ СВЕЧЕНИЕ (soft glow эффект) ===
            glow_radius = base_radius + 35
            for glow_layer in range(3, 0, -1):
                glow_color = QColor("#00D4FF")
                glow_alpha = int(80 / (glow_layer + 1))
                glow_color.setAlpha(glow_alpha)
                pen = QPen(glow_color, 1)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(cx - glow_radius - glow_layer * 3,
                                   cy - glow_radius - glow_layer * 3,
                                   2 * (glow_radius + glow_layer * 3),
                                   2 * (glow_radius + glow_layer * 3))

            self._pulse_phase += 1
            self._rotation_phase += 1
        else:
            # Неактивный режим — тусклый реактор
            pen = QPen(QColor("#30363D"), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            for i in range(3):
                r = base_radius + i * 12
                painter.drawEllipse(cx - r, cy - r, 2*r, 2*r)

            painter.setBrush(QBrush(QColor("#1F2937")))
            core_radius = int(base_radius * 0.6)
            painter.drawEllipse(cx - core_radius, cy - core_radius,
                               2 * core_radius, 2 * core_radius)

        painter.end()


class InfoPanel(QFrame):
    """Информационная панель (микрофон, модель, RAM, статус) — красивая и современная."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("infoPanel")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame#infoPanel {
                background-color: rgba(22, 27, 34, 0.4);
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 12px;
                margin: 4px 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # ── Mic + Model (горизонтально) ──
        row1 = QHBoxLayout()
        row1.setSpacing(20)

        # Микрофон
        mic_label = QLabel("🎤 Микрофон")
        mic_label.setStyleSheet("""
            color: #00FFFF;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 1px;
        """)
        self.mic_status = QLabel("инициализация...")
        self.mic_status.setStyleSheet("""
            color: #8B949E;
            font-size: 9px;
            padding: 4px 8px;
            background-color: rgba(13, 17, 23, 0.5);
            border-radius: 3px;
        """)
        mic_col = QVBoxLayout()
        mic_col.setContentsMargins(0, 0, 0, 0)
        mic_col.setSpacing(3)
        mic_col.addWidget(mic_label)
        mic_col.addWidget(self.mic_status)
        row1.addLayout(mic_col)

        # Модель
        model_label = QLabel("🧠 Модель")
        model_label.setStyleSheet("""
            color: #00FFFF;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 1px;
        """)
        self.model_status = QLabel("—")
        self.model_status.setStyleSheet("""
            color: #8B949E;
            font-size: 9px;
            padding: 4px 8px;
            background-color: rgba(13, 17, 23, 0.5);
            border-radius: 3px;
        """)
        model_col = QVBoxLayout()
        model_col.setContentsMargins(0, 0, 0, 0)
        model_col.setSpacing(3)
        model_col.addWidget(model_label)
        model_col.addWidget(self.model_status)
        row1.addLayout(model_col)

        row1.addStretch()
        layout.addLayout(row1)

        # ── RAM + Ping (горизонтально) ──
        row2 = QHBoxLayout()
        row2.setSpacing(20)

        # RAM
        ram_label = QLabel("💾 RAM")
        ram_label.setStyleSheet("""
            color: #00FFFF;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 1px;
        """)
        self.ram_status = QLabel("—")
        self.ram_status.setStyleSheet("""
            color: #00D4FF;
            font-size: 10px;
            font-weight: bold;
            padding: 4px 8px;
            background-color: rgba(13, 17, 23, 0.5);
            border-radius: 3px;
        """)
        ram_col = QVBoxLayout()
        ram_col.setContentsMargins(0, 0, 0, 0)
        ram_col.setSpacing(3)
        ram_col.addWidget(ram_label)
        ram_col.addWidget(self.ram_status)
        row2.addLayout(ram_col)

        # Ping
        ping_label = QLabel("⚡ Ping")
        ping_label.setStyleSheet("""
            color: #00FFFF;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 1px;
        """)
        self.ping_status = QLabel("—")
        self.ping_status.setStyleSheet("""
            color: #8B949E;
            font-size: 9px;
            padding: 4px 8px;
            background-color: rgba(13, 17, 23, 0.5);
            border-radius: 3px;
        """)
        ping_col = QVBoxLayout()
        ping_col.setContentsMargins(0, 0, 0, 0)
        ping_col.setSpacing(3)
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
                color: #00FFFF;
                font-size: 13px;
                font-weight: bold;
                letter-spacing: 2px;
                margin: 12px 0px;
                padding: 8px 0px;
                border-bottom: 2px solid #00D4FF;
            }
        """)
        layout.addWidget(self.status_label)

        # ── Spoken text display ──
        self.spoken_label = QLabel("")
        self.spoken_label.setWordWrap(True)
        self.spoken_label.setAlignment(Qt.AlignCenter)
        self.spoken_label.setStyleSheet("""
            QLabel {
                color: #00FFFF;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                margin: 0px 12px;
                padding: 10px 12px;
                border-radius: 6px;
                background-color: rgba(22, 27, 34, 0.6);
                border: 1px solid #30363D;
                min-height: 32px;
            }
        """)
        layout.addWidget(self.spoken_label)

        # ── Control buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.setContentsMargins(12, 8, 12, 0)

        self.btn_start = QPushButton("🎙  СЛУШАТЬ")
        self.btn_start.setObjectName("primaryButton")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.clicked.connect(self.start_clicked.emit)

        self.btn_stop = QPushButton("⏹  СТОП")
        self.btn_stop.setObjectName("secondaryButton")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)

        self.btn_reindex = QPushButton("🔄  ИНДЕКС")
        self.btn_reindex.setObjectName("secondaryButton")
        self.btn_reindex.setMinimumHeight(40)
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
