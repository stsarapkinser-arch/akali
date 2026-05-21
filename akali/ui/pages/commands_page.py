"""Страница «Команды»: компактный список карточек с фильтром.

В узком окне таблица 2×N работает плохо — переключаюсь на скроллируемый
список карточек, по одной на команду:

    ╭──────────────────────────────────────╮
    │ ▸ firefox                            │  ← cmd, моноширинный
    │   фаерфокс, браузер, открой браузер  │  ← триггеры
    ╰──────────────────────────────────────╯
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QScrollArea, QSizePolicy,
                                QVBoxLayout, QWidget)

from ...core.backend import AssistantCore


class CommandCard(QFrame):
    """Одна команда: cmd сверху, триггеры снизу."""

    def __init__(self, cmd: str, triggers: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("commandCard")
        self.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        self._cmd = cmd
        self._triggers_text = ", ".join(triggers)

        self._lbl_cmd = QLabel(cmd)
        self._lbl_cmd.setObjectName("cardCmd")
        self._lbl_cmd.setWordWrap(True)
        layout.addWidget(self._lbl_cmd)

        self._lbl_triggers = QLabel(self._triggers_text)
        self._lbl_triggers.setObjectName("cardTriggers")
        self._lbl_triggers.setWordWrap(True)
        layout.addWidget(self._lbl_triggers)

    @property
    def haystack(self) -> str:
        return f"{self._cmd}\n{self._triggers_text}".lower()


class CommandsPage(QWidget):
    """Поиск + список карточек + сводка."""

    reindex_requested = Signal()

    def __init__(self, core: AssistantCore, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("commandsPage")
        self._core = core
        self._cards: list[CommandCard] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8)

        # Заголовок
        title = QLabel("Команды")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        self._summary = QLabel("")
        self._summary.setObjectName("pageSubtitle")
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        # Поиск
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Поиск…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        outer.addWidget(self._filter)

        # Кнопка реиндекса
        self._btn_reindex = QPushButton("Переиндексировать систему")
        self._btn_reindex.setObjectName("secondaryBtn")
        self._btn_reindex.setCursor(Qt.PointingHandCursor)
        self._btn_reindex.clicked.connect(self.reindex_requested.emit)
        outer.addWidget(self._btn_reindex)

        # Скролл со списком карточек
        self._scroll = QScrollArea()
        self._scroll.setObjectName("cardScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._list_host = QWidget()
        self._list_host.setObjectName("cardList")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 4, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        outer.addWidget(self._scroll, 1)

    def refresh(self) -> None:
        # Сначала чистим
        for card in self._cards:
            self._list_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        # Затем вставляем (перед stretch)
        for cmd, triggers in sorted(self._core.commands_db.items()):
            card = CommandCard(cmd, triggers)
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

        total_triggers = sum(len(v) for v in self._core.commands_db.values())
        srcs = self._core.auto_sources
        src_str = ", ".join(f"{k}={v}" for k, v in srcs.items() if v) or "—"
        self._summary.setText(
            f"{len(self._cards)} команд · {total_triggers} триггеров\nauto: {src_str}")
        self._apply_filter(self._filter.text())

    def _apply_filter(self, q: str) -> None:
        q = q.strip().lower()
        if not q:
            for c in self._cards:
                c.setVisible(True)
            return
        for c in self._cards:
            c.setVisible(q in c.haystack)
