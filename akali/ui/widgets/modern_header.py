"""Современная заголовочная панель с поддержкой drag-to-move.

ModernHeaderBar:
  • Без системного декора (frameless окно → нужен свой)
  • Drag-to-move за верхней панелью
  • Динамические иконки вкладок
  • Лого + версия приложения
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                                QSizePolicy, QVBoxLayout, QWidget)

from ..icons import IconSet


class ModernHeaderBar(QFrame):
    """Современная панель заголовка (frameless + drag-to-move + вкладки)."""

    tab_clicked = Signal(str)  # Передаёт ключ вкладки ("home", "commands", и т.д.)

    def __init__(self, version: str = "0.1.0", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.OpenHandCursor)

        self._version = version
        self._drag_position: QPoint | None = None
        self._active_tab = "home"
        self._tabs: dict[str, QPushButton] = {}

        self._build()
        self._wire()

    def _build(self) -> None:
        """Строит layout: Лого | Вкладки | (пусто для drag-to-move)."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(16)

        # ── Левая часть: Лого + Версия ──
        logo_layout = QVBoxLayout()
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(2)

        logo_label = QLabel("⬤ AKALI")
        logo_label.setObjectName("headerLogo")
        logo_label.setStyleSheet("""
            QLabel#headerLogo {
                color: #00D4FF;
                font-weight: bold;
                font-size: 13px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        logo_layout.addWidget(logo_label)

        version_label = QLabel(f"v{self._version}")
        version_label.setObjectName("headerVersion")
        version_label.setStyleSheet("""
            QLabel#headerVersion {
                color: #8B949E;
                font-size: 8px;
                font-family: "JetBrains Mono", monospace;
            }
        """)
        logo_layout.addWidget(version_label)
        logo_layout.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(logo_layout)
        left_widget.setMaximumWidth(100)
        layout.addWidget(left_widget)

        # ── Центральная часть: Вкладки ──
        tabs_layout = QHBoxLayout()
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(8)

        tabs_config = [
            ("home", "🏠 Главная", IconSet.home()),
            ("commands", "⚡ Команды", IconSet.commands()),
            ("settings", "⚙️ Настройки", IconSet.settings()),
            ("log", "📋 Логи", IconSet.log()),
        ]

        for key, label, icon in tabs_config:
            btn = self._create_tab_button(key, label, icon)
            tabs_layout.addWidget(btn)
            self._tabs[key] = btn

        tabs_widget = QWidget()
        tabs_widget.setLayout(tabs_layout)
        layout.addWidget(tabs_widget, 1)

        # ── Правая часть: Пусто (для drag-to-move) ──
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(spacer, 1)

        self.setLayout(layout)

    def _create_tab_button(self, key: str, label: str, icon: QIcon) -> QPushButton:
        """Создаёт кнопку вкладки с иконкой."""
        btn = QPushButton(label)
        btn.setObjectName("tabButton")
        btn.setIcon(icon)
        btn.setIconSize((__import__("PySide6.QtCore", fromlist=["QSize"]).QSize(16, 16)))
        btn.setFlat(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton#tabButton {
                background-color: transparent;
                color: #8B949E;
                border: none;
                padding: 6px 12px;
                margin: 0px 2px;
                font-size: 11px;
                border-radius: 4px;
                font-family: "JetBrains Mono", monospace;
            }
            QPushButton#tabButton:hover {
                background-color: #161B22;
                color: #00D4FF;
            }
            QPushButton#tabButton.active {
                color: #00D4FF;
                border-bottom: 2px solid #00D4FF;
                padding-bottom: 4px;
            }
        """)

        btn.clicked.connect(lambda: self.tab_clicked.emit(key))
        return btn

    def _wire(self) -> None:
        """Подключаем сигналы."""
        for key, btn in self._tabs.items():
            if key == "home":
                btn.setProperty("active", True)
                btn.style().polish(btn)

    def set_active(self, key: str) -> None:
        """Устанавливает активную вкладку."""
        if self._active_tab in self._tabs:
            self._tabs[self._active_tab].setProperty("active", False)
            self._tabs[self._active_tab].style().polish(self._tabs[self._active_tab])

        self._active_tab = key
        if key in self._tabs:
            self._tabs[key].setProperty("active", True)
            self._tabs[key].style().polish(self._tabs[key])

    # ── Drag-to-move ──────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Начинаем drag, если клик не на кнопку."""
        if event.button() == Qt.LeftButton:
            # Проверяем, клик не на кнопку
            widget = self.childAt(event.pos())
            if not isinstance(widget, QPushButton) and widget is not None:
                # Клик на пустую область → начинаем drag
                self._drag_position = event.globalPos() - self.window().frameGeometry().topLeft()
                event.accept()
            else:
                # Клик на кнопку → пропускаем
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Перемещаем окно при drag."""
        if event.buttons() == Qt.LeftButton and self._drag_position is not None:
            self.window().move(event.globalPos() - self._drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Завершаем drag."""
        self._drag_position = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        """Рисуем bottom border для header."""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#30363D"), 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()
