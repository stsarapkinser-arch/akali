"""Страница настроек (refactored) — Card-based UI с группировкой.

Структура:
  • Карточки (QFrame с border-radius) для каждой группы
  • Группы: "Нейросети", "Аудио", "Система"
  • Custom toggle switches для bool параметров
  • Стилизованные input fields (моноширинный шрифт)
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSettings, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame,
                                QHBoxLayout, QLabel, QLineEdit, QPushButton,
                                QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..icons import IconSet
from ...core.backend import AssistantCore


class CardFrame(QFrame):
    """Карточка с закруглёнными углами и оформлением."""

    def __init__(self, title: str, description: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # Заголовок карточки
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        title_label.setStyleSheet("""
            QLabel#cardTitle {
                color: #00D4FF;
                font-size: 12px;
                font-weight: bold;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        layout.addWidget(title_label)

        # Описание (если есть)
        if description:
            desc_label = QLabel(description)
            desc_label.setObjectName("cardDescription")
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("""
                QLabel#cardDescription {
                    color: #8B949E;
                    font-size: 10px;
                    font-family: "JetBrains Mono", monospace;
                }
            """)
            layout.addWidget(desc_label)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        layout.addLayout(self.content_layout)

        self.setLayout(layout)

    def add_setting(self, label: str, widget: QWidget) -> None:
        """Добавляет строку с подписью и виджетом."""
        row_layout = QHBoxLayout()
        row_layout.setSpacing(12)

        label_widget = QLabel(label)
        label_widget.setStyleSheet("""
            QLabel {
                color: #E6EDF3;
                font-size: 10px;
                font-family: "JetBrains Mono", monospace;
                min-width: 140px;
            }
        """)

        row_layout.addWidget(label_widget)
        row_layout.addWidget(widget, 1)
        self.content_layout.addLayout(row_layout)


class ToggleSwitch(QCheckBox):
    """Custom toggle switch вместо обычного checkbox."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet("""
            QCheckBox {
                spacing: 8px;
                color: #E6EDF3;
            }
            QCheckBox::indicator {
                width: 32px;
                height: 18px;
                border-radius: 9px;
                border: none;
                background-color: #30363D;
            }
            QCheckBox::indicator:checked {
                background-color: #3FB950;
            }
            QCheckBox::indicator:hover {
                background-color: #1D8EE6;
            }
        """)


class SettingsPage(QWidget):
    """Страница настроек (card-based, modern design)."""

    reload_requested = Signal()
    update_requested = Signal()

    def __init__(self, core: AssistantCore, settings: QSettings, repo_dir: str,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(0)

        # Scroll area для контента
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                background-color: #0D1117;
                border: none;
            }
        """)

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 12, 0, 12)
        content_layout.setSpacing(16)

        # ════════════════════════════════════════════════════════════════════
        # НЕЙРОСЕТИ
        # ════════════════════════════════════════════════════════════════════
        ai_card = CardFrame("🤖 НЕЙРОСЕТИ", "Модели для распознавания и генерации")

        # Embedding модель
        embed_model_combo = QComboBox()
        embed_model_combo.addItems([
            "paraphrase-multilingual-MiniLM-L12-v2",
            "all-minilm-l12-v2",
        ])
        embed_model_combo.setMaximumWidth(250)
        embed_model_combo.setStyleSheet("""
            QComboBox {
                background-color: #161B22;
                color: #E6EDF3;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px;
                font-size: 9px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        ai_card.add_setting("FastEmbed модель", embed_model_combo)

        # Semantic threshold
        semantic_spin = QDoubleSpinBox()
        semantic_spin.setRange(0.0, 1.0)
        semantic_spin.setValue(0.82)
        semantic_spin.setSingleStep(0.01)
        semantic_spin.setMaximumWidth(100)
        semantic_spin.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #161B22;
                color: #E6EDF3;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px;
                font-size: 9px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        ai_card.add_setting("Порог семантики", semantic_spin)

        # Ollama модель
        ollama_input = QLineEdit()
        ollama_input.setText("qwen2.5-coder:1.5b")
        ollama_input.setMaximumWidth(200)
        ollama_input.setStyleSheet("""
            QLineEdit {
                background-color: #161B22;
                color: #E6EDF3;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px;
                font-size: 9px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        ai_card.add_setting("Ollama модель", ollama_input)

        content_layout.addWidget(ai_card)

        # ════════════════════════════════════════════════════════════════════
        # АУДИО
        # ════════════════════════════════════════════════════════════════════
        audio_card = CardFrame("🎙️ АУДИО", "Микрофон и обработка звука")

        # Device selector
        device_combo = QComboBox()
        device_combo.addItems(["Default (PipeWire)", "ALSA", "PulseAudio"])
        device_combo.setMaximumWidth(200)
        device_combo.setStyleSheet("""
            QComboBox {
                background-color: #161B22;
                color: #E6EDF3;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px;
                font-size: 9px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        audio_card.add_setting("Устройство ввода", device_combo)

        # Vosk модель
        vosk_input = QLineEdit()
        vosk_input.setText("vosk-model-small-ru-0.22")
        vosk_input.setMaximumWidth(220)
        vosk_input.setStyleSheet("""
            QLineEdit {
                background-color: #161B22;
                color: #E6EDF3;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px;
                font-size: 8px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        audio_card.add_setting("Vosk модель (директория)", vosk_input)

        # Sample rate
        rate_spin = QSpinBox()
        rate_spin.setRange(8000, 48000)
        rate_spin.setValue(16000)
        rate_spin.setSingleStep(1000)
        rate_spin.setMaximumWidth(100)
        rate_spin.setSuffix(" Hz")
        rate_spin.setStyleSheet("""
            QSpinBox {
                background-color: #161B22;
                color: #E6EDF3;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 6px;
                font-size: 9px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        audio_card.add_setting("Частота дискретизации", rate_spin)

        content_layout.addWidget(audio_card)

        # ════════════════════════════════════════════════════════════════════
        # СИСТЕМА
        # ════════════════════════════════════════════════════════════════════
        system_card = CardFrame("⚙️ СИСТЕМА", "Параметры приложения и обновления")

        # Автозапуск
        autostart_toggle = ToggleSwitch()
        autostart_toggle.setText("Автозапуск в фоне")
        system_card.add_setting("", autostart_toggle)

        # Минимизация в трей
        minimize_toggle = ToggleSwitch()
        minimize_toggle.setChecked(True)
        minimize_toggle.setText("Минимизировать в трей")
        system_card.add_setting("", minimize_toggle)

        # Темная тема (всегда включена)
        theme_label = QLabel("Тема: Кибер-Аркадия (тёмная)")
        theme_label.setStyleSheet("""
            QLabel {
                color: #8B949E;
                font-size: 9px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        system_card.add_setting("", theme_label)

        content_layout.addWidget(system_card)

        # ════════════════════════════════════════════════════════════════════
        # ACTION BUTTONS
        # ════════════════════════════════════════════════════════════════════
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        btn_reload = QPushButton("♻️ Перезагрузить")
        btn_reload.setObjectName("primaryButton")
        btn_reload.clicked.connect(self.reload_requested.emit)

        btn_update = QPushButton("⬇️ Обновить")
        btn_update.setObjectName("secondaryButton")
        btn_update.clicked.connect(self.update_requested.emit)

        btn_layout.addWidget(btn_reload)
        btn_layout.addWidget(btn_update)
        btn_layout.addStretch()

        content_layout.addLayout(btn_layout)
        content_layout.addStretch()

        scroll.setWidget(content_widget)
        layout.addWidget(scroll)

        self.setLayout(layout)

    def refresh(self) -> None:
        """Освежить данные настроек."""
        pass
