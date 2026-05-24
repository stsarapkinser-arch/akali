"""Страница «Команды»: фильтр + категорийные пилюли + список карточек.

    ╭────────────────────────────────────────────────╮
    │ КОМАНДЫ                                        │
    │ 125 команд · 430 триггеров                     │
    │ [🔍 Поиск…                              ]      │
    │ [Все] [рабочий стол] [аудио] [сеть] …          │
    ├────────────────────────────────────────────────┤
    │ ╭──────────────────────────────────────────╮   │
    │ │ ▸ firefox                           [▶]  │   │
    │ │   фаерфокс, браузер                     │   │
    │ ╰──────────────────────────────────────────╯   │
    └────────────────────────────────────────────────┘
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFormLayout,
                                QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                                QPushButton, QScrollArea, QSizePolicy, QTextEdit,
                                QVBoxLayout, QWidget)

from ...core.backend import AssistantCore
from ...core import power_guard

log = logging.getLogger(__name__)


class CommandCard(QFrame):
    """Одна команда: cmd + триггеры + кнопки запуска/редактирования/удаления."""

    run_clicked = Signal(str)
    edit_clicked = Signal(str)
    delete_clicked = Signal(str)

    def __init__(self, cmd: str, triggers: list[str], category: str = "прочее",
                 editable: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("commandCard")
        self.setFrameShape(QFrame.NoFrame)
        self._cmd = cmd
        self._category = category
        self._triggers = list(triggers)
        self._triggers_text = ", ".join(triggers)

        # Передаём категорию как dynamic property для QSS (левая полоска)
        self.setProperty("category", category)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 8, 7)
        row.setSpacing(6)

        # Текстовая часть
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        lbl_cmd = QLabel(cmd)
        lbl_cmd.setObjectName("cardCmd")
        lbl_cmd.setWordWrap(False)
        text_col.addWidget(lbl_cmd)

        lbl_triggers = QLabel(self._triggers_text)
        lbl_triggers.setObjectName("cardTriggers")
        lbl_triggers.setWordWrap(False)
        text_col.addWidget(lbl_triggers)

        row.addLayout(text_col, 1)

        # Кнопка запуска
        btn_run = QPushButton("▶")
        btn_run.setObjectName("runBtn")
        btn_run.setFixedSize(22, 22)
        btn_run.setCursor(Qt.PointingHandCursor)
        btn_run.setToolTip(f"Выполнить: {cmd[:60]}")
        btn_run.clicked.connect(lambda: self.run_clicked.emit(self._cmd))
        row.addWidget(btn_run, 0, Qt.AlignVCenter)

        if editable:
            btn_edit = QPushButton("✎")
            btn_edit.setObjectName("secondaryBtn")
            btn_edit.setFixedSize(22, 22)
            btn_edit.setCursor(Qt.PointingHandCursor)
            btn_edit.setToolTip("Редактировать триггеры")
            btn_edit.clicked.connect(lambda: self.edit_clicked.emit(self._cmd))
            row.addWidget(btn_edit, 0, Qt.AlignVCenter)

            btn_del = QPushButton("✕")
            btn_del.setObjectName("dangerBtn")
            btn_del.setFixedSize(22, 22)
            btn_del.setCursor(Qt.PointingHandCursor)
            btn_del.setToolTip("Удалить из commands.txt")
            btn_del.clicked.connect(lambda: self.delete_clicked.emit(self._cmd))
            row.addWidget(btn_del, 0, Qt.AlignVCenter)

    @property
    def triggers(self) -> list[str]:
        return list(self._triggers)

    @property
    def haystack(self) -> str:
        return f"{self._cmd}\n{self._triggers_text}\n{self._category}".lower()

    @property
    def category(self) -> str:
        return self._category


class _CategoryBar(QWidget):
    """Горизонтальная полоска пилюль категорий."""

    category_selected = Signal(str)   # "" = все

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("categoryBar")
        self.setFixedHeight(34)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setFixedHeight(34)
        self._scroll.setWidgetResizable(False)

        self._inner = QWidget()
        self._inner.setObjectName("categoryBar")
        self._layout = QHBoxLayout(self._inner)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setSpacing(5)
        self._layout.addStretch(1)

        self._scroll.setWidget(self._inner)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

    def set_categories(self, categories: list[str]) -> None:
        """Пересобирает пилюли."""
        # Очищаем старые
        for btn in self._group.buttons():
            self._group.removeButton(btn)
            self._layout.removeWidget(btn)
            btn.deleteLater()

        all_cats = ["все"] + sorted(categories)
        for cat in all_cats:
            btn = QPushButton(cat.upper())
            btn.setObjectName("categoryPill")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(24)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self._group.addButton(btn)
            # Вставляем перед stretch
            self._layout.insertWidget(self._layout.count() - 1, btn)
            btn.clicked.connect(
                lambda _checked, c=cat: self.category_selected.emit(
                    "" if c == "все" else c
                )
            )

        # Активируем «все»
        if self._group.buttons():
            self._group.buttons()[0].setChecked(True)

        # Пересчитываем ширину inner
        n = len(all_cats)
        self._inner.setFixedWidth(max(n * 86 + 16, 200))


class CommandEditDialog(QDialog):
    """Диалог редактирования/добавления команды."""

    def __init__(self, cmd: str = "", triggers: list[str] | None = None,
                 cmd_editable: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Команда" if cmd else "Новая команда")
        self.setModal(True)
        self.setMinimumWidth(420)

        form = QFormLayout()
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)

        self.cmd_edit = QLineEdit(cmd)
        self.cmd_edit.setPlaceholderText("например: firefox &")
        self.cmd_edit.setEnabled(cmd_editable)
        form.addRow("bash-команда:", self.cmd_edit)

        self.triggers_edit = QTextEdit()
        self.triggers_edit.setPlaceholderText(
            "фразы через запятую или с новой строки\n"
            "например: фаерфокс, открой браузер")
        self.triggers_edit.setPlainText(", ".join(triggers or []))
        self.triggers_edit.setFixedHeight(120)
        form.addRow("Голосовые фразы:", self.triggers_edit)

        self.hint = QLabel(
            "Силовые команды (poweroff/reboot/suspend/hibernate) "
            "добавлять нельзя — они в power_guard с exact-match.")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("pageSubtitle")
        form.addRow(self.hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addLayout(form)
        outer.addWidget(buttons)

    def values(self) -> tuple[str, list[str]]:
        cmd = self.cmd_edit.text().strip()
        raw = self.triggers_edit.toPlainText()
        triggers: list[str] = []
        # Принимаем и запятые, и переводы строк
        for chunk in raw.replace("\n", ",").split(","):
            t = chunk.strip()
            if t and t not in triggers:
                triggers.append(t)
        return cmd, triggers


class CommandsPage(QWidget):
    """Поиск + категории + список карточек + редактор команд."""

    reindex_requested = Signal()
    run_command = Signal(str)
    commands_changed = Signal()              # сообщает координатору перезагрузить базу

    def __init__(self, core: AssistantCore, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("commandsPage")
        self._core = core
        self._cards: list[CommandCard] = []
        self._active_category = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 8)
        outer.setSpacing(6)

        title = QLabel("Команды")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        self._summary = QLabel("")
        self._summary.setObjectName("pageSubtitle")
        self._summary.setWordWrap(True)
        outer.addWidget(self._summary)

        # Строка поиска + кнопки реиндекса/добавления
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Поиск…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._filter, 1)

        self._btn_add = QPushButton("➕")
        self._btn_add.setObjectName("secondaryBtn")
        self._btn_add.setFixedWidth(36)
        self._btn_add.setCursor(Qt.PointingHandCursor)
        self._btn_add.setToolTip("Добавить новую команду")
        self._btn_add.clicked.connect(self._on_add_clicked)
        search_row.addWidget(self._btn_add)

        self._btn_reindex = QPushButton("⟳")
        self._btn_reindex.setObjectName("secondaryBtn")
        self._btn_reindex.setFixedWidth(36)
        self._btn_reindex.setCursor(Qt.PointingHandCursor)
        self._btn_reindex.setToolTip("Переиндексировать систему")
        self._btn_reindex.clicked.connect(self.reindex_requested.emit)
        search_row.addWidget(self._btn_reindex)
        outer.addLayout(search_row)

        # Полоска категорий
        self._cat_bar = _CategoryBar(self)
        self._cat_bar.category_selected.connect(self._on_category)
        outer.addWidget(self._cat_bar)

        # Скролл-список карточек
        self._scroll = QScrollArea()
        self._scroll.setObjectName("cardScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._list_host = QWidget()
        self._list_host.setObjectName("cardList")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 2, 0, 0)
        self._list_layout.setSpacing(3)
        self._list_layout.addStretch(1)
        self._scroll.setWidget(self._list_host)
        outer.addWidget(self._scroll, 1)

    def refresh(self) -> None:
        # Чистим старые карточки
        for card in self._cards:
            self._list_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        # Собираем категории
        categories: set[str] = set()
        for cmd in self._core.commands_db:
            cat = AssistantCore.category_of(cmd)
            categories.add(cat)

        self._cat_bar.set_categories(sorted(categories))

        # Создаём карточки. auto_commands (из system_indexer) — не редактируем.
        auto_cmds = set(self._core.auto_sources.keys()) if isinstance(
            self._core.auto_sources, dict) else set()
        for cmd, triggers in sorted(self._core.commands_db.items()):
            cat = AssistantCore.category_of(cmd)
            editable = cmd not in auto_cmds
            card = CommandCard(cmd, triggers, cat, editable=editable)
            card.run_clicked.connect(self.run_command.emit)
            card.edit_clicked.connect(self._on_edit_clicked)
            card.delete_clicked.connect(self._on_delete_clicked)
            self._cards.append(card)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

        total_triggers = sum(len(v) for v in self._core.commands_db.values())
        srcs = self._core.auto_sources
        src_str = ", ".join(f"{k}={v}" for k, v in srcs.items() if v) or "—"
        self._summary.setText(
            f"{len(self._cards)} команд · {total_triggers} триггеров  ·  auto: {src_str}")

        self._apply_filter(self._filter.text())

    def _on_category(self, cat: str) -> None:
        self._active_category = cat
        self._apply_filter(self._filter.text())

    def _apply_filter(self, q: str) -> None:
        q = q.strip().lower()
        for card in self._cards:
            match_text = (not q) or (q in card.haystack)
            match_cat  = (not self._active_category) or (card.category == self._active_category)
            card.setVisible(match_text and match_cat)

    # ── Редактирование ─────────────────────────────────────
    def _on_add_clicked(self) -> None:
        dlg = CommandEditDialog(parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        cmd, triggers = dlg.values()
        if not cmd or not triggers:
            QMessageBox.warning(self, "Не сохранено",
                                "Нужно указать и команду, и хотя бы одну фразу.")
            return
        if power_guard.is_power_command(cmd):
            QMessageBox.warning(
                self, "Запрещено",
                "Силовые команды (poweroff/reboot/suspend/hibernate) "
                "хранятся отдельно в power_guard.py с exact-match. "
                "Так шум и галлюцинации LLM не выключат систему.")
            return
        if self._core.save_command(cmd, triggers):
            self.commands_changed.emit()
        else:
            QMessageBox.warning(self, "Ошибка",
                                "Не удалось сохранить — проверь права на commands.txt.")

    def _on_edit_clicked(self, cmd: str) -> None:
        existing_triggers = self._core.commands_db.get(cmd, [])
        dlg = CommandEditDialog(cmd, existing_triggers, cmd_editable=False, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        new_cmd, new_triggers = dlg.values()
        if not new_triggers:
            QMessageBox.warning(self, "Не сохранено",
                                "Нужна хотя бы одна голосовая фраза.")
            return
        if self._core.save_command(cmd, new_triggers):
            self.commands_changed.emit()
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось сохранить.")

    def _on_delete_clicked(self, cmd: str) -> None:
        reply = QMessageBox.question(
            self, "Удалить команду?",
            f"Удалить из commands.txt?\n\n{cmd}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if self._core.delete_command(cmd):
            self.commands_changed.emit()
        else:
            QMessageBox.warning(self, "Не удалено",
                                "Команда не найдена в commands.txt (возможно auto-команда).")
