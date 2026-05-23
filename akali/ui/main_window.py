"""Главное окно ассистента — координатор страниц.

Архитектура:
    • Frameless window (без системного декора, drag в ModernHeaderBar)
    • QStackedWidget с тремя страницами: Главная, Команды, Настройки
    • Тёмная футуристичная тема (Cyber Arc)
    • System Tray (minimize/restore)

Все логи идут через `logging` в stdout, поэтому вкладки «Лог» больше нет.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QSettings, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QMainWindow, QMessageBox,
                                QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__ as AKALI_VERSION
from ..core.backend import AssistantCore
from ..core.updater import UpdateResult
from .icons import IconSet
from .pages import CommandsPage, HomePage, SettingsPage
from .widgets import ModernHeaderBar, StatusRow

log = logging.getLogger(__name__)

WINDOW_WIDTH = 420
WINDOW_HEIGHT = 720
WINDOW_MIN_WIDTH = 380
WINDOW_MIN_HEIGHT = 600


class MainWindow(QMainWindow):
    """Главное окно: header + страницы + status row."""

    # Команды от UI к координатору
    start_requested = Signal()
    stop_requested = Signal()
    reindex_requested = Signal()
    reload_core_requested = Signal()
    update_requested = Signal(bool)        # force
    check_update_requested = Signal()
    show_requested = Signal()
    quit_requested = Signal()
    run_command_requested = Signal(str)        # запуск команды кликом из CommandsPage
    edit_commands_requested = Signal()         # запрос пересборки базы после редактирования

    def __init__(self, core: AssistantCore, settings: QSettings, repo_dir: str,
                 icon: Optional[QIcon] = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir
        self._icon = icon or IconSet.reactor()

        self.setWindowTitle("Akali — голосовой ассистент")
        self.setWindowIcon(self._icon)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setGeometry(100, 100, WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        self._minimize_to_tray = False

        self._load_stylesheet()
        self._build()
        self._wire()

    def _load_stylesheet(self) -> None:
        from .. import paths
        try:
            stylesheet = paths.APP_STYLESHEET.read_text(encoding="utf-8")
            QApplication.instance().setStyleSheet(stylesheet)
        except OSError as e:
            log.warning("Не удалось загрузить таблицу стилей: %s", e)

    # ── Сборка UI ─────────────────────────────────────────────
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("centralBg")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = ModernHeaderBar(AKALI_VERSION, parent=self)
        self.header.setObjectName("headerBar")
        outer.addWidget(self.header)

        sep_top = QFrame()
        sep_top.setObjectName("topSeparator")
        sep_top.setFrameShape(QFrame.HLine)
        sep_top.setMaximumHeight(1)
        outer.addWidget(sep_top)

        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.home_page = HomePage()
        self.commands_page = CommandsPage(self._core)
        self.settings_page = SettingsPage(self._core, self._settings, self._repo_dir)

        self._page_index = {
            "home":     self.stack.addWidget(self.home_page),
            "commands": self.stack.addWidget(self.commands_page),
            "settings": self.stack.addWidget(self.settings_page),
        }
        outer.addWidget(self.stack, 1)

        sep_bot = QFrame()
        sep_bot.setObjectName("bottomSeparator")
        sep_bot.setFrameShape(QFrame.HLine)
        sep_bot.setMaximumHeight(1)
        outer.addWidget(sep_bot)

        self.status_row = StatusRow()
        outer.addWidget(self.status_row)

        self.setCentralWidget(central)

    def set_minimize_to_tray(self, value: bool) -> None:
        self._minimize_to_tray = value

    def closeEvent(self, event):  # noqa: N802
        if self._minimize_to_tray:
            self.hide()
            event.ignore()
        else:
            event.accept()

    # ── Связи ─────────────────────────────────────────────────
    def _wire(self) -> None:
        self.header.tab_clicked.connect(self._switch_page)
        self.home_page.start_clicked.connect(self.start_requested.emit)
        self.home_page.stop_clicked.connect(self.stop_requested.emit)
        self.home_page.reindex_clicked.connect(self.reindex_requested.emit)
        self.commands_page.reindex_requested.connect(self.reindex_requested.emit)
        self.commands_page.run_command.connect(self.run_command_requested.emit)
        self.commands_page.commands_changed.connect(self._on_commands_changed)
        self.settings_page.reload_requested.connect(self.reload_core_requested.emit)
        self.settings_page.update_requested.connect(self.update_requested.emit)
        self.settings_page.check_update_requested.connect(self.check_update_requested.emit)

    @Slot(str)
    def _switch_page(self, key: str) -> None:
        idx = self._page_index.get(key)
        if idx is not None:
            self.stack.setCurrentIndex(idx)
            self.header.set_active(key)

    @Slot()
    def _on_commands_changed(self) -> None:
        """Юзер отредактировал commands.txt через UI — просим coordinator перезагрузить."""
        self.reload_core_requested.emit()

    # ── Слоты со стороны AudioWorker'а ────────────────────────
    @Slot(str)
    def on_status(self, status: str) -> None:
        self.home_page.set_state(status)
        active = status in ("listening", "waiting_command", "processing",
                            "starting", "recovering")
        self.status_row.set_mic_active(active)
        self.status_row.set_brain_active(status == "processing")

    @Slot(str)
    def on_text(self, text: str) -> None:
        self.home_page.show_spoken(text, is_partial=False)

    @Slot(str)
    def on_partial_text(self, text: str) -> None:
        self.home_page.show_spoken(text, is_partial=True)

    @Slot()
    def on_wake(self) -> None:
        pass  # лог рисуется в audio_worker через logging

    @Slot(str, str, float, str)
    def on_command_matched(self, spoken: str, cmd: str, conf: float, method: str) -> None:
        self.home_page.show_confidence(conf, method)

    @Slot(str, object)
    def on_command_executed(self, cmd: str, result) -> None:
        # Логи рисует audio_worker и core.execute; UI просто показывает toast.
        rc = getattr(result, "returncode", 0)
        if getattr(result, "is_background", False):
            self.home_page.show_toast(f"▶ {cmd[:40]}")
        elif getattr(result, "error", "") or rc != 0:
            self.home_page.show_toast(f"⚠ {(getattr(result, 'error', '') or 'rc=' + str(rc))[:60]}")
        else:
            self.home_page.show_toast("ок")

    @Slot(str, float)
    def on_no_match(self, spoken: str, max_conf: float) -> None:
        self.home_page.show_toast(f"не понял: {spoken[:30]}")

    @Slot(str)
    def on_error(self, msg: str) -> None:
        # Восстановимые ошибки уже залогированы в audio_worker; не мусорим UI.
        self.home_page.show_toast(f"⚠ {msg[:60]}")

    @Slot(str)
    def on_fatal_error(self, msg: str) -> None:
        log.error("Фатальная ошибка: %s", msg)
        self.home_page.set_state("error")
        QMessageBox.critical(self, "Фатальная ошибка", msg)

    @Slot(float)
    def on_level(self, level: float) -> None:
        self.home_page.set_level(level)
        self.status_row.set_mic_level(level)

    @Slot(bool, str, object)
    def on_reindex_done(self, ok: bool, err: str, stats) -> None:
        if ok:
            n = getattr(stats, "commands_total", 0) if stats else 0
            self.home_page.show_toast(f"индекс пересобран: {n} команд")
        else:
            self.home_page.show_toast(f"реиндекс не удался: {err[:40]}")
        self.commands_page.refresh()

    @Slot(object)
    def on_update_result(self, result: UpdateResult) -> None:
        if not result.ok:
            QMessageBox.warning(self, "Обновление не удалось", result.error)
            return
        if result.already_up_to_date:
            QMessageBox.information(self, "Уже последняя версия",
                                    "Локальная копия уже на свежем remote.")
            return
        commits = "\n".join(f"  {sha} {msg}" for sha, msg in result.pulled_commits) or "  (нет деталей)"
        files = "\n".join(f"  {f}" for f in result.changed_files) or "  (нет деталей)"
        extra = []
        if result.stashed:
            extra.append("Локальные правки спрятаны в git stash.")
        if result.forced_reset:
            extra.append("Был выполнен reset --hard на remote.")
        msg = (
            f"Подтянуто коммитов: {len(result.pulled_commits)}\n\n{commits}\n\n"
            f"Изменённые файлы:\n{files}\n\n"
            + ("\n".join(extra) + "\n\n" if extra else "")
            + ("Перезапускаюсь через секунду…"
               if result.needs_restart else "Можно продолжать работу.")
        )
        QMessageBox.information(self, "Обновление получено", msg)

    @Slot()
    def reload_complete(self) -> None:
        self.commands_page.refresh()
        self.home_page.show_toast("База перезагружена")

    # ── Status row helpers ────────────────────────────────────
    def set_mic_subtitle(self, text: str) -> None:
        self.status_row.mic.set_subtitle(text)

    def set_brain_subtitle(self, text: str) -> None:
        self.status_row.brain.set_subtitle(text)

    def set_resources_subtitle(self, text: str) -> None:
        self.status_row.resources.set_subtitle(text)
