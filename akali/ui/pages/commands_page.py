"""Страница «Команды»: таблица всей базы (curated + auto) с фильтром."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                                QPushButton, QTableWidget, QTableWidgetItem,
                                QVBoxLayout, QWidget)

from ...core.backend import AssistantCore


class CommandsPage(QWidget):
    """Поиск по команде или триггеру + сводка по источникам."""

    reindex_requested = Signal()

    def __init__(self, core: AssistantCore, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("commandsPage")
        self._core = core
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Поиск по команде или триггеру…")
        self._filter.textChanged.connect(self._apply_filter)
        top.addWidget(self._filter, 1)

        self._summary = QLabel("")
        self._summary.setObjectName("hint")
        top.addWidget(self._summary)

        self._btn_reindex = QPushButton("Реиндекс")
        self._btn_reindex.setObjectName("secondaryBtn")
        self._btn_reindex.setCursor(Qt.PointingHandCursor)
        self._btn_reindex.clicked.connect(self.reindex_requested.emit)
        top.addWidget(self._btn_reindex)
        outer.addLayout(top)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Команда", "Триггеры"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        outer.addWidget(self._table, 1)

    def refresh(self) -> None:
        rows = [(cmd, ", ".join(triggers))
                for cmd, triggers in sorted(self._core.commands_db.items())]
        self._table.setRowCount(len(rows))
        for i, (cmd, triggers) in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(cmd))
            self._table.setItem(i, 1, QTableWidgetItem(triggers))
        total_triggers = sum(len(v) for v in self._core.commands_db.values())
        srcs = self._core.auto_sources
        src_str = ", ".join(f"{k}={v}" for k, v in srcs.items() if v) or "—"
        self._summary.setText(
            f"{len(rows)} команд · {total_triggers} триггеров · auto: {src_str}")
        self._apply_filter(self._filter.text())

    def _apply_filter(self, q: str) -> None:
        q = q.strip().lower()
        for row in range(self._table.rowCount()):
            cmd = self._table.item(row, 0).text().lower()
            trg = self._table.item(row, 1).text().lower()
            hidden = bool(q) and (q not in cmd and q not in trg)
            self._table.setRowHidden(row, hidden)
