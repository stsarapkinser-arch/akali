"""Компактный верхний бар.

Раскладка (для узкого вертикального окна ~400px):

  ┌──────────────────────────────────────────────┐
  │ ◉ AKALI  v0.3.0 BETA               ─  ✕     │   ← 44px
  ├──────────────────────────────────────────────┤
  │  [🏠]   [📋]   [⚙]   [📜]                   │   ← 48px, иконочные вкладки
  └──────────────────────────────────────────────┘

Кнопки-вкладки — иконочные, фиксированной ширины, объединены в
QButtonGroup; родитель ловит сигнал tab_clicked(name).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                                QSizePolicy, QVBoxLayout, QWidget)


# Юникод-иконки. На KDE Plasma они отрисуются нативным emoji-фонтом
# (Noto Color Emoji), на других DE — fallback на чёрно-белые.
TAB_ICONS = {
    "home":     "⌂",
    "commands": "≣",
    "settings": "⚙",
    "log":      "≡",
}
TAB_LABELS = {
    "home":     "Главная",
    "commands": "Команды",
    "settings": "Настройки",
    "log":      "Лог",
}


class HeaderBar(QWidget):
    """Брендинг + иконочные вкладки."""

    tab_clicked = Signal(str)  # "home" | "commands" | "settings" | "log"

    def __init__(self, icon: QIcon, version: str,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(96)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 6, 14, 0)
        outer.setSpacing(2)

        # ── Brand row ─────────────────────────────────────────────
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)

        logo = QLabel()
        logo.setFixedSize(28, 28)
        logo.setPixmap(icon.pixmap(QSize(28, 28)))
        logo.setObjectName("brandLogo")
        brand_row.addWidget(logo)

        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(0, 0, 0, 0)
        brand_box.setSpacing(0)
        self._brand = QLabel("AKALI")
        self._brand.setObjectName("brand")
        self._version = QLabel(f"v{version} BETA")
        self._version.setObjectName("brandVersion")
        brand_box.addWidget(self._brand)
        brand_box.addWidget(self._version)
        brand_row.addLayout(brand_box)
        brand_row.addStretch(1)
        outer.addLayout(brand_row)

        outer.addStretch(1)

        # ── Tabs row ──────────────────────────────────────────────
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key in ("home", "commands", "settings", "log"):
            btn = QPushButton(TAB_ICONS[key])
            btn.setObjectName("navTab")
            btn.setToolTip(TAB_LABELS[key])
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(38)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, k=key: self.tab_clicked.emit(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            tabs_row.addWidget(btn, 1)
        outer.addLayout(tabs_row)

        # Активная по умолчанию — главная
        self._buttons["home"].setChecked(True)

    def set_active(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)
