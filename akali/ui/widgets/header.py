"""Верхний бар с логотипом, версией и кнопками-вкладками.

Виджет рендерит:
  [icon]  AKALI  v0.3.0         [⌘ Команды]  [⚙ Настройки]  [≡ Лог]

Кнопки-вкладки — обычные QPushButton с pressable/checked состоянием,
объединены в QButtonGroup; родитель ловит сигнал tab_clicked(name).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                                QSizePolicy, QVBoxLayout, QWidget)


class HeaderBar(QWidget):
    """Брендинг + кнопки-вкладки."""

    tab_clicked = Signal(str)  # "home" | "commands" | "settings" | "log"

    def __init__(self, icon: QIcon, version: str,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(64)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 8, 18, 8)
        outer.setSpacing(14)

        # --- Логотип + brand ---
        logo = QLabel()
        logo.setFixedSize(38, 38)
        logo.setPixmap(icon.pixmap(QSize(38, 38)))
        logo.setObjectName("brandLogo")
        outer.addWidget(logo)

        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(0, 0, 0, 0)
        brand_box.setSpacing(0)
        self._brand = QLabel("AKALI")
        self._brand.setObjectName("brand")
        self._version = QLabel(f"v{version}  BETA")
        self._version.setObjectName("brandVersion")
        brand_box.addWidget(self._brand)
        brand_box.addWidget(self._version)
        outer.addLayout(brand_box)

        outer.addStretch(1)

        # --- Вкладки-кнопки ---
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in (
            ("home", "Главная"),
            ("commands", "Команды"),
            ("settings", "Настройки"),
            ("log", "Лог"),
        ):
            btn = QPushButton(text)
            btn.setObjectName("navTab")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _, k=key: self.tab_clicked.emit(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            outer.addWidget(btn)

        # Активная по умолчанию — главная
        self._buttons["home"].setChecked(True)

    def set_active(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)
