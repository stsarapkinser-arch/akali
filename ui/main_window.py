"""Главное окно ассистента «Акали» (PySide6).

Содержит четыре вкладки:
  1) Главная — крупный статус, уровень микрофона, последние события.
  2) Команды — поиск по объединённой базе curated + auto.
  3) Настройки — пороги, пути, обновление из репо.
  4) Лог — текстовый журнал всех событий.

Само окно НЕ запускает аудио — это делает entry-point (akali.py), он же
владеет AudioWorker и пробрасывает сигналы сюда.
"""
from __future__ import annotations

import datetime
import os
import sys
from typing import Optional

from PySide6.QtCore import Qt, QSettings, QTimer, Signal, Slot
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.backend import AssistantCore
from core.updater import UpdateResult, pull as git_pull


STATUS_LABELS = {
    "starting": ("Запуск…", "waiting"),
    "listening": ("Слушаю", "listening"),
    "waiting_command": ("Жду команду", "waiting"),
    "processing": ("Выполняю", "processing"),
    "reindexing": ("Реиндексирую систему", "reindexing"),
    "recovering": ("Восстанавливаю аудио", "recovering"),
    "stopped": ("Остановлен", "stopped"),
    "error": ("Ошибка", "error"),
}


def _fmt_time() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


# ============================================================
# Вкладка «Главная»
# ============================================================
class HomeTab(QWidget):
    """Большой статус + уровень микрофона + лента последних команд."""

    start_clicked = Signal()
    stop_clicked = Signal()
    reindex_clicked = Signal()
    open_commands_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._listening = False
        self._build()
        self._update_button_state()
        self._set_status("stopped")

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(18)

        # Header: badge + title
        header = QHBoxLayout()
        header.setSpacing(12)
        self.status_badge = QLabel("Остановлен")
        self.status_badge.setObjectName("statusBadge")
        header.addWidget(self.status_badge)
        header.addStretch(1)
        outer.addLayout(header)

        self.big_status = QLabel("Готов к работе")
        self.big_status.setObjectName("bigStatus")
        outer.addWidget(self.big_status)

        # Mic level
        lvl_row = QHBoxLayout()
        lvl_row.setSpacing(8)
        lvl_row.addWidget(QLabel("Микрофон"))
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        lvl_row.addWidget(self.level_bar, 1)
        outer.addLayout(lvl_row)

        # Recent events
        outer.addWidget(QLabel("Последние команды"))
        self.recent_list = QListWidget()
        self.recent_list.setAlternatingRowColors(False)
        outer.addWidget(self.recent_list, 1)

        # Bottom bar with buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_start_stop = QPushButton("Слушать")
        self.btn_start_stop.setProperty("accent", True)
        self.btn_start_stop.setMinimumWidth(150)
        self.btn_start_stop.clicked.connect(self._on_start_stop)
        btn_row.addWidget(self.btn_start_stop)

        self.btn_reindex = QPushButton("Переиндексировать")
        self.btn_reindex.clicked.connect(self.reindex_clicked.emit)
        btn_row.addWidget(self.btn_reindex)

        btn_row.addStretch(1)

        self.btn_browse_commands = QPushButton("База команд →")
        self.btn_browse_commands.clicked.connect(self.open_commands_clicked.emit)
        btn_row.addWidget(self.btn_browse_commands)
        outer.addLayout(btn_row)

    # --- Public slots ---
    @Slot(str)
    def set_status(self, state: str):
        self._set_status(state)

    def _set_status(self, state: str):
        label, prop = STATUS_LABELS.get(state, (state, "stopped"))
        self.status_badge.setText(label)
        self.status_badge.setProperty("state", prop)
        # Reapply stylesheet so :state[…] property selector picks up the change
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)
        if state == "stopped":
            self.big_status.setText("Готов к работе")
            self._listening = False
        elif state == "starting":
            self.big_status.setText("Запускаю аудио и Vosk…")
        elif state == "listening":
            self.big_status.setText("Слушаю — скажите wake-word")
            self._listening = True
        elif state == "waiting_command":
            self.big_status.setText("Внимание! Произнесите команду")
            self._listening = True
        elif state == "processing":
            self.big_status.setText("Обрабатываю команду…")
            self._listening = True
        elif state == "reindexing":
            self.big_status.setText("Переиндексирую систему…")
        elif state == "recovering":
            self.big_status.setText("Перезапускаю аудиосервер…")
        elif state == "error":
            self.big_status.setText("Ошибка аудио — см. лог")
        self._update_button_state()

    @Slot(float)
    def set_level(self, level: float):
        self.level_bar.setValue(int(max(0, min(1, level)) * 100))

    def add_recognized_text(self, text: str):
        item = QListWidgetItem(f"[{_fmt_time()}] 🎙  «{text}»")
        item.setForeground(Qt.gray)
        self.recent_list.insertItem(0, item)
        self._trim_recent()

    def add_command_match(self, spoken: str, cmd: str, confidence: float, method: str):
        glyph = "▶" if method == "fuzzy" else "≈"
        item = QListWidgetItem(
            f"[{_fmt_time()}] {glyph}  «{spoken}» → {cmd}   ({method} {confidence:.2f})")
        self.recent_list.insertItem(0, item)
        self._trim_recent()

    def add_no_match(self, spoken: str, max_conf: float):
        item = QListWidgetItem(
            f"[{_fmt_time()}] ✕  «{spoken}» — не нашёл (max={max_conf:.2f})")
        item.setForeground(Qt.gray)
        self.recent_list.insertItem(0, item)
        self._trim_recent()

    def _trim_recent(self, keep: int = 100):
        while self.recent_list.count() > keep:
            self.recent_list.takeItem(self.recent_list.count() - 1)

    # --- Buttons ---
    def _on_start_stop(self):
        if self._listening:
            self.stop_clicked.emit()
        else:
            self.start_clicked.emit()

    def _update_button_state(self):
        if self._listening:
            self.btn_start_stop.setText("Остановить")
            self.btn_start_stop.setProperty("accent", False)
            self.btn_start_stop.setProperty("danger", True)
        else:
            self.btn_start_stop.setText("Слушать")
            self.btn_start_stop.setProperty("accent", True)
            self.btn_start_stop.setProperty("danger", False)
        self.btn_start_stop.style().unpolish(self.btn_start_stop)
        self.btn_start_stop.style().polish(self.btn_start_stop)


# ============================================================
# Вкладка «Команды»
# ============================================================
class CommandsTab(QWidget):
    """Список всех команд (curated + auto) с фильтром."""

    def __init__(self, core: AssistantCore, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._core = core
        self._build()
        self.refresh()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Поиск по команде или триггеру…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        top_row.addWidget(self.filter_edit, 1)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("hint")
        top_row.addWidget(self.summary_label)
        outer.addLayout(top_row)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Команда", "Триггеры"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        outer.addWidget(self.table, 1)

    def refresh(self):
        rows = []
        for cmd in sorted(self._core.commands_db.keys()):
            triggers = self._core.commands_db[cmd]
            rows.append((cmd, ", ".join(triggers)))
        self.table.setRowCount(len(rows))
        for i, (cmd, triggers) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(cmd))
            self.table.setItem(i, 1, QTableWidgetItem(triggers))
        n = len(rows)
        total_triggers = sum(len(v) for v in self._core.commands_db.values())
        srcs = self._core.auto_sources
        src_str = ", ".join(f"{k}={v}" for k, v in srcs.items() if v) or "—"
        self.summary_label.setText(
            f"{n} команд · {total_triggers} триггеров · auto: {src_str}")
        self._apply_filter(self.filter_edit.text())

    def _apply_filter(self, q: str):
        q = q.strip().lower()
        for row in range(self.table.rowCount()):
            cmd = self.table.item(row, 0).text().lower()
            trg = self.table.item(row, 1).text().lower()
            hidden = bool(q) and (q not in cmd and q not in trg)
            self.table.setRowHidden(row, hidden)


# ============================================================
# Вкладка «Настройки»
# ============================================================
class SettingsTab(QWidget):
    """Пороги fuzzy/vector/wake + пути + обновление из репо."""

    update_requested = Signal()
    reload_requested = Signal()

    def __init__(self, core: AssistantCore, settings: QSettings,
                 repo_dir: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir
        self._build()
        self._load_into_widgets()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # --- Thresholds group ---
        thr = QGroupBox("Пороги распознавания")
        thr_form = QFormLayout(thr)
        self.spin_fuzzy = self._make_threshold_spin()
        self.spin_vector = self._make_threshold_spin()
        self.spin_wake = self._make_threshold_spin()
        thr_form.addRow("Fuzzy (текст)", self.spin_fuzzy)
        thr_form.addRow("Vector (смысл)", self.spin_vector)
        thr_form.addRow("Wake-word", self.spin_wake)
        outer.addWidget(thr)

        # --- Words & triggers ---
        words = QGroupBox("Активационные слова и реиндекс")
        wf = QFormLayout(words)
        self.edit_wake = QLineEdit()
        self.edit_wake.setPlaceholderText("через запятую: акали, ассистент, компьютер")
        self.edit_reindex = QLineEdit()
        self.edit_reindex.setPlaceholderText("через запятую: переиндексируй, обнови команд")
        wf.addRow("Wake-words", self.edit_wake)
        wf.addRow("Реиндекс-фразы", self.edit_reindex)
        outer.addWidget(words)

        # --- Paths ---
        paths = QGroupBox("Пути")
        pf = QFormLayout(paths)
        self.edit_repo = QLineEdit(self._repo_dir)
        repo_row = QHBoxLayout()
        repo_row.addWidget(self.edit_repo, 1)
        btn_browse = QPushButton("…")
        btn_browse.setMaximumWidth(40)
        btn_browse.clicked.connect(self._browse_repo)
        repo_row.addWidget(btn_browse)
        repo_wrap = QWidget()
        repo_wrap.setLayout(repo_row)
        pf.addRow("Папка репозитория", repo_wrap)
        outer.addWidget(paths)

        # --- Actions ---
        actions = QHBoxLayout()
        self.btn_apply = QPushButton("Применить и пересобрать кэш")
        self.btn_apply.setProperty("accent", True)
        self.btn_apply.clicked.connect(self._apply)
        actions.addWidget(self.btn_apply)

        self.btn_update = QPushButton("Обновить из репо (git pull)")
        self.btn_update.clicked.connect(self.update_requested.emit)
        actions.addWidget(self.btn_update)

        actions.addStretch(1)
        outer.addLayout(actions)

        outer.addStretch(1)

    def _make_threshold_spin(self) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(0.0, 1.0)
        s.setSingleStep(0.05)
        s.setDecimals(2)
        return s

    def _browse_repo(self):
        path = QFileDialog.getExistingDirectory(self, "Папка проекта", self._repo_dir)
        if path:
            self.edit_repo.setText(path)

    def _load_into_widgets(self):
        self.spin_fuzzy.setValue(self._core.fuzzy_threshold)
        self.spin_vector.setValue(self._core.vector_threshold)
        self.spin_wake.setValue(self._core.wake_threshold)
        self.edit_wake.setText(", ".join(self._core.wake_words))
        self.edit_reindex.setText(", ".join(self._core.reindex_triggers))

    def _apply(self):
        self._core.fuzzy_threshold = self.spin_fuzzy.value()
        self._core.vector_threshold = self.spin_vector.value()
        self._core.wake_threshold = self.spin_wake.value()
        wake = [w.strip().lower() for w in self.edit_wake.text().split(",") if w.strip()]
        reindex = [r.strip().lower() for r in self.edit_reindex.text().split(",") if r.strip()]
        if wake:
            self._core.wake_words = wake
        if reindex:
            self._core.reindex_triggers = reindex
        self._settings.setValue("fuzzy_threshold", self._core.fuzzy_threshold)
        self._settings.setValue("vector_threshold", self._core.vector_threshold)
        self._settings.setValue("wake_threshold", self._core.wake_threshold)
        self._settings.setValue("wake_words", ",".join(self._core.wake_words))
        self._settings.setValue("reindex_triggers", ",".join(self._core.reindex_triggers))
        self._settings.setValue("repo_dir", self.edit_repo.text().strip() or self._repo_dir)
        self._repo_dir = self.edit_repo.text().strip() or self._repo_dir
        self.reload_requested.emit()

    @property
    def repo_dir(self) -> str:
        return self.edit_repo.text().strip() or self._repo_dir


# ============================================================
# Вкладка «Лог»
# ============================================================
class LogTab(QWidget):
    """Просто текстовое поле, накапливающее события."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_clear = QPushButton("Очистить")
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        outer.addLayout(row)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(2000)
        outer.addWidget(self.text, 1)

    def append(self, line: str):
        self.text.appendPlainText(f"[{_fmt_time()}] {line}")

    def _clear(self):
        self.text.clear()


# ============================================================
# Главное окно
# ============================================================
class MainWindow(QMainWindow):
    """Связывает 4 вкладки и AudioWorker."""

    start_requested = Signal()
    stop_requested = Signal()
    reindex_requested = Signal()
    reload_core_requested = Signal()

    def __init__(self, core: AssistantCore, settings: QSettings, repo_dir: str,
                 icon: Optional[QIcon] = None, parent=None):
        super().__init__(parent)
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir
        self.setWindowTitle("Akali — голосовой ассистент")
        self.setMinimumSize(820, 600)
        if icon:
            self.setWindowIcon(icon)

        self._build()
        self._wire_internal_signals()

    def _build(self):
        tabs = QTabWidget()
        self.home_tab = HomeTab()
        self.commands_tab = CommandsTab(self._core)
        self.settings_tab = SettingsTab(self._core, self._settings, self._repo_dir)
        self.log_tab = LogTab()
        tabs.addTab(self.home_tab, "Главная")
        tabs.addTab(self.commands_tab, "Команды")
        tabs.addTab(self.settings_tab, "Настройки")
        tabs.addTab(self.log_tab, "Лог")
        self.setCentralWidget(tabs)
        self.tabs = tabs

    def _wire_internal_signals(self):
        self.home_tab.start_clicked.connect(self.start_requested.emit)
        self.home_tab.stop_clicked.connect(self.stop_requested.emit)
        self.home_tab.reindex_clicked.connect(self.reindex_requested.emit)
        self.home_tab.open_commands_clicked.connect(
            lambda: self.tabs.setCurrentWidget(self.commands_tab))
        self.settings_tab.reload_requested.connect(self.reload_core_requested.emit)

    # === Внешние слоты, дёргаются из AudioWorker ===
    @Slot(str)
    def on_status(self, status: str):
        self.home_tab.set_status(status)
        self.log_tab.append(f"[STATE] {status}")

    @Slot(str)
    def on_text(self, text: str):
        self.home_tab.add_recognized_text(text)
        self.log_tab.append(f"🎙 «{text}»")

    @Slot()
    def on_wake(self):
        self.log_tab.append("🔔 Wake-word")

    @Slot(str, str, float, str)
    def on_command_matched(self, spoken: str, cmd: str, conf: float, method: str):
        self.home_tab.add_command_match(spoken, cmd, conf, method)
        self.log_tab.append(f"▶ {method.upper()} {conf:.2f}  «{spoken}» → {cmd}")

    @Slot(str, object)
    def on_command_executed(self, cmd: str, result):
        # result is a CommandResult dataclass
        if getattr(result, "is_background", False):
            self.log_tab.append(f"   ↳ запущено в фоне")
            return
        rc = getattr(result, "returncode", 0)
        if getattr(result, "timed_out", False):
            self.log_tab.append(f"   ↳ таймаут")
        elif getattr(result, "error", ""):
            self.log_tab.append(f"   ↳ ошибка: {result.error}")
        elif rc != 0:
            self.log_tab.append(f"   ↳ rc={rc} {getattr(result, 'stderr', '')[:200]}")
        else:
            out = getattr(result, "stdout", "")
            if out:
                self.log_tab.append(f"   ↳ {out[:200]}")
            else:
                self.log_tab.append(f"   ↳ ок")

    @Slot(str, float)
    def on_no_match(self, spoken: str, max_conf: float):
        self.home_tab.add_no_match(spoken, max_conf)
        self.log_tab.append(f"✕ «{spoken}» (max={max_conf:.2f})")

    @Slot(str)
    def on_error(self, msg: str):
        self.log_tab.append(f"⚠ {msg}")

    @Slot(str)
    def on_fatal_error(self, msg: str):
        self.log_tab.append(f"💀 {msg}")
        QMessageBox.critical(self, "Фатальная ошибка", msg)
        self.home_tab.set_status("error")

    @Slot(float)
    def on_level(self, level: float):
        self.home_tab.set_level(level)

    @Slot(bool, str, object)
    def on_reindex_done(self, ok: bool, err: str, stats):
        if ok:
            cmd_total = getattr(stats, "commands_total", 0) if stats else 0
            vec_total = getattr(stats, "vector_total", 0) if stats else 0
            srcs = getattr(stats, "auto_sources", {}) if stats else {}
            src_str = ", ".join(f"{k}={v}" for k, v in srcs.items() if v) or "—"
            self.log_tab.append(
                f"✓ Реиндекс ок: {cmd_total} команд / {vec_total} векторов · auto: {src_str}")
        else:
            self.log_tab.append(f"⚠ Реиндекс не удался: {err}")
        # Обновляем таблицу команд
        self.commands_tab.refresh()

    @Slot(object)
    def on_update_result(self, result: UpdateResult):
        if not result.ok:
            self.log_tab.append(f"⚠ git pull: {result.error}")
            QMessageBox.warning(self, "Обновление не удалось", result.error)
            return
        if result.already_up_to_date:
            self.log_tab.append("✓ git pull: всё актуально")
            QMessageBox.information(self, "Уже последняя версия", "Локальная копия уже на свежем main.")
            return
        commits = "\n".join(f"  {sha} {msg}" for sha, msg in result.pulled_commits) or "  (нет деталей)"
        files = "\n".join(f"  {f}" for f in result.changed_files) or "  (нет деталей)"
        self.log_tab.append(
            f"✓ git pull: {len(result.pulled_commits)} коммитов\n{commits}\nИзменены файлы:\n{files}")
        msg = (
            f"Подтянуто коммитов: {len(result.pulled_commits)}\n\n"
            f"{commits}\n\nИзменённые файлы:\n{files}\n\n"
            + ("Рекомендую перезапустить приложение, чтобы изменения вступили в силу."
               if result.needs_restart else "Можно продолжать работу."))
        QMessageBox.information(self, "Обновление получено", msg)

    @Slot()
    def reload_complete(self):
        """Бэкенд перестроен — обновляем таблицу команд."""
        self.commands_tab.refresh()
        self.log_tab.append("✓ База перезагружена")
