"""Современная заголовочная панель.

ModernHeaderBar:
  • Frameless-окно — drag-to-move за пустой областью хедера
  • Логотип AKALI с версией
  • 4 вкладки (checkable, QButtonGroup) — активная через :checked в QSS
  • Кнопки minimize / close справа
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel,
                                QPushButton, QSizePolicy, QVBoxLayout, QWidget)


class _AkaliLogo(QWidget):
    """Логотип AKALI — нарисованный QPainter (текст + декоративные линии)."""

    def __init__(self, version: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._version = version
        self.setFixedSize(80, 44)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        cyan = QColor(0, 212, 255)
        dim  = QColor(0, 212, 255, 60)

        # Декоративная линия слева от текста
        p.setPen(QPen(dim, 1.2))
        p.drawLine(0, 12, 4, 12)

        # "AKALI" — основной текст
        font = p.font()
        font.setFamily("JetBrains Mono, Fira Code, monospace")
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        p.setFont(font)
        p.setPen(QPen(cyan, 1))
        p.drawText(6, 17, "AKALI")

        # Версия
        font.setPixelSize(8)
        font.setWeight(QFont.Weight.Normal)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        p.setFont(font)
        p.setPen(QPen(QColor(50, 100, 130), 1))
        p.drawText(6, 30, f"v{self._version}")

        # Декоративная нижняя линия
        p.setPen(QPen(dim, 1.0))
        p.drawLine(6, 35, 70, 35)

        # Маленькие «тики» на концах
        p.setPen(QPen(cyan, 1.5))
        p.drawLine(4, 33, 4, 37)
        p.drawLine(72, 33, 72, 37)

        p.end()


class ModernHeaderBar(QFrame):
    """Современная панель заголовка (frameless + drag-to-move + вкладки)."""

    tab_clicked = Signal(str)

    def __init__(self, version: str = "0.1.0", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFrameShape(QFrame.NoFrame)
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedHeight(56)

        self._version = version
        self._drag_position: QPoint | None = None
        self._active_tab = "home"
        self._tabs: dict[str, QPushButton] = {}
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        self._build()

    # ── Сборка ──────────────────────────────────────────────────────
    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(0)

        # ── Лого ──
        logo = _AkaliLogo(self._version, self)
        logo.setCursor(Qt.OpenHandCursor)
        layout.addWidget(logo)

        layout.addSpacing(4)

        # ── Вкладки ──
        tabs_config = [
            ("home",     "ГЛАВНАЯ"),
            ("commands", "КОМАНДЫ"),
            ("settings", "НАСТРОЙКИ"),
            ("log",      "ЛОГ"),
        ]
        for key, label in tabs_config:
            btn = self._make_tab(key, label)
            layout.addWidget(btn)
            self._tabs[key] = btn
            self._btn_group.addButton(btn)

        layout.addStretch(1)

        # ── Кнопки управления окном ──
        btn_min = QPushButton("—", self)
        btn_min.setObjectName("windowCtrlBtn")
        btn_min.setFixedSize(28, 24)
        btn_min.setCursor(Qt.PointingHandCursor)
        btn_min.setToolTip("Свернуть")
        btn_min.clicked.connect(lambda: self.window().showMinimized())
        layout.addWidget(btn_min)

        layout.addSpacing(2)

        btn_close = QPushButton("✕", self)
        btn_close.setObjectName("windowCtrlBtn")
        btn_close.setProperty("close", "true")
        btn_close.setFixedSize(28, 24)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setToolTip("Закрыть")
        btn_close.clicked.connect(lambda: self.window().close())
        layout.addWidget(btn_close)

        self._ctrl_btns = [btn_min, btn_close]

        # Активируем первую вкладку
        self._tabs["home"].setChecked(True)

    def _make_tab(self, key: str, label: str) -> QPushButton:
        btn = QPushButton(label, self)
        btn.setObjectName("tabButton")
        btn.setFlat(True)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(56)
        btn.clicked.connect(lambda _checked, k=key: self.tab_clicked.emit(k))
        return btn

    # ── Публичный API ────────────────────────────────────────────────
    def set_active(self, key: str) -> None:
        if key in self._tabs:
            self._tabs[key].setChecked(True)
            self._active_tab = key

    # ── Drag-to-move ─────────────────────────────────────────────────
    def _is_control(self, widget) -> bool:
        return isinstance(widget, QPushButton)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            widget = self.childAt(event.pos())
            if not self._is_control(widget):
                self._drag_position = (
                    event.globalPosition().toPoint()
                    - self.window().frameGeometry().topLeft()
                )
                event.accept()
            else:
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() == Qt.LeftButton and self._drag_position is not None:
            self.window().move(
                event.globalPosition().toPoint() - self._drag_position
            )
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_position = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        # Нижняя граница — мягкое cyan свечение
        p.setPen(QPen(QColor(0, 212, 255, 45), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        p.end()
