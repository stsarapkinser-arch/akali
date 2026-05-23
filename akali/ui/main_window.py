"""Главное окно ассистента (refactored).

Архитектура:
    • Frameless window (без системного декора)
    • Drag-to-move за верхней панелью (реализовано в HeaderBar)
    • Modern dark theme (Cyber Arc)
    • System Tray integration (minimize/restore)
    • QStackedWidget с модульными страницами
    • Dynamic icons из icons.py

Окно НЕ владеет AudioWorker'ом; координатор сводит сигналы.
"""
from __future__ import annotations

import datetime
from typing import Optional

from PySide6.QtCore import QSettings, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QFrame, QLabel, QMainWindow, QMessageBox,
                                QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__ as AKALI_VERSION
from ..core.backend import AssistantCore
from ..core.updater import UpdateResult
from .icons import IconSet
from .pages import CommandsPage, HomePage, LogPage, SettingsPage
from .widgets import HeaderBar, StatusRow, ModernHeaderBar

# Оптимизированные размеры (responsive, но с фиксированным aspect ratio)
WINDOW_WIDTH = 420
WINDOW_HEIGHT = 720
WINDOW_MIN_WIDTH = 380
WINDOW_MIN_HEIGHT = 600


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


class MainWindow(QMainWindow):
    """Главное окно (frameless, modern dark theme, system tray).

    Сигналы:
        start_requested, stop_requested, reindex_requested — управление аудио
        reload_core_requested — перезагрузить базу команд
        update_requested — проверить обновления
        show_requested, quit_requested — управление окном
    """

    # Команды от UI к координатору
    start_requested = Signal()
    stop_requested = Signal()
    reindex_requested = Signal()
    reload_core_requested = Signal()
    update_requested = Signal()
    show_requested = Signal()
    quit_requested = Signal()

    def __init__(self, core: AssistantCore, settings: QSettings, repo_dir: str,
                 icon: Optional[QIcon] = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir

        # Используем динамические иконки, если не передана явно
        self._icon = icon or IconSet.reactor()

        self.setWindowTitle("Akali — голосовой ассистент")
        self.setWindowIcon(self._icon)

        # Frameless окно (без системного декора)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

        # Размеры с поддержкой ресайза (но с минимальными границами)
        self.setGeometry(100, 100, WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        # Поддержка drag-to-move буде реализована в HeaderBar через mouse events

        # Флаг: сворачивать ли в трей вместо закрытия
        self._minimize_to_tray = False

        # Загружаем стиль
        self._load_stylesheet()

        self._build()
        self._wire()

    def _load_stylesheet(self) -> None:
        """Загружает app.qss."""
        try:
            qss_path = __file__.replace("main_window.py", "app.qss")
            with open(qss_path, encoding="utf-8") as f:
                stylesheet = f.read()
            QApplication.instance().setStyleSheet(stylesheet)
        except Exception as e:
            print(f"Warning: Failed to load stylesheet: {e}")

    # ── Сборка UI ────────────────────────────────────────────────────
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("centralBg")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Modern Header (с поддержкой drag-to-move) ──
        self.header = ModernHeaderBar(AKALI_VERSION, parent=self)
        self.header.setObjectName("headerBar")
        outer.addWidget(self.header)

        # Разделитель
        sep_top = QFrame()
        sep_top.setObjectName("topSeparator")
        sep_top.setFrameShape(QFrame.HLine)
        sep_top.setMaximumHeight(1)
        outer.addWidget(sep_top)

        # ── Страницы (QStackedWidget) ──
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.home_page = HomePage()
        self.commands_page = CommandsPage(self._core)
        self.settings_page = SettingsPage(self._core, self._settings, self._repo_dir)
        self.log_page = LogPage()

        self._page_index = {
            "home":     self.stack.addWidget(self.home_page),
            "commands": self.stack.addWidget(self.commands_page),
            "settings": self.stack.addWidget(self.settings_page),
            "log":      self.stack.addWidget(self.log_page),
        }
        outer.addWidget(self.stack, 1)

        # ── Footer (Status Row) ──
        sep_bot = QFrame()
        sep_bot.setObjectName("bottomSeparator")
        sep_bot.setFrameShape(QFrame.HLine)
        sep_bot.setMaximumHeight(1)
        outer.addWidget(sep_bot)

        self.status_row = StatusRow()
        outer.addWidget(self.status_row)

        self.setCentralWidget(central)

        # Оптимизация памяти: явно удаляем при закрытии
        self.destroyed.connect(self._cleanup)

    def _cleanup(self) -> None:
        """Удаляет объекты для предотвращения утечек памяти."""
        self.home_page.deleteLater()
        self.commands_page.deleteLater()
        self.settings_page.deleteLater()
        self.log_page.deleteLater()
        self.status_row.deleteLater()

    def set_minimize_to_tray(self, value: bool) -> None:
        """Устанавливает режим: сворачивать в трей вместо закрытия."""
        self._minimize_to_tray = value

    def closeEvent(self, event):
        """Переопределяем closeEvent для свёртывания в трей вместо выхода."""
        if self._minimize_to_tray:
            self.hide()
            event.ignore()
        else:
            event.accept()

    # ── Связи ────────────────────────────────────────────────────────
    def _wire(self) -> None:
        self.header.tab_clicked.connect(self._switch_page)
        self.home_page.start_clicked.connect(self.start_requested.emit)
        self.home_page.stop_clicked.connect(self.stop_requested.emit)
        self.home_page.reindex_clicked.connect(self.reindex_requested.emit)
        self.commands_page.reindex_requested.connect(self.reindex_requested.emit)
        self.settings_page.reload_requested.connect(self.reload_core_requested.emit)
        self.settings_page.update_requested.connect(self.update_requested.emit)

    @Slot(str)
    def _switch_page(self, key: str) -> None:
        idx = self._page_index.get(key)
        if idx is not None:
            self.stack.setCurrentIndex(idx)
            self.header.set_active(key)

    # ── Слоты со стороны AudioWorker'а ──────────────────────────────
    @Slot(str)
    def on_status(self, status: str) -> None:
        self.home_page.set_state(status)
        self.log_page.append(f"[STATE] {status}")

    @Slot(str)
    def on_text(self, text: str) -> None:
        self.home_page.show_spoken(text)
        self.log_page.append(f"🎙 «{text}»")

    @Slot(str)
    def on_partial_text(self, text: str) -> None:
        # Partial-результат показываем «на лету», но в лог не пишем — спам
        self.home_page.show_spoken(text)

    @Slot()
    def on_wake(self) -> None:
        self.log_page.append("🔔 Wake-word")

    @Slot(str, str, float, str)
    def on_command_matched(self, spoken: str, cmd: str, conf: float, method: str) -> None:
        self.log_page.append(f"▶ {method.upper()} {conf:.2f}  «{spoken}» → {cmd}")

    @Slot(str, object)
    def on_command_executed(self, cmd: str, result) -> None:
        if getattr(result, "is_background", False):
            self.log_page.append("   ↳ запущено в фоне")
            return
        rc = getattr(result, "returncode", 0)
        if getattr(result, "timed_out", False):
            self.log_page.append("   ↳ таймаут")
        elif getattr(result, "error", ""):
            self.log_page.append(f"   ↳ ошибка: {result.error}")
        elif rc != 0:
            stderr = (getattr(result, "stderr", "") or "")[:200]
            self.log_page.append(f"   ↳ rc={rc} {stderr}")
        else:
            out = (getattr(result, "stdout", "") or "")[:200]
            self.log_page.append(f"   ↳ {out or 'ок'}")

    @Slot(str, float)
    def on_no_match(self, spoken: str, max_conf: float) -> None:
        self.log_page.append(f"✕ «{spoken}» (max={max_conf:.2f})")

    @Slot(str)
    def on_error(self, msg: str) -> None:
        self.log_page.append(f"⚠ {msg}")

    @Slot(str)
    def on_fatal_error(self, msg: str) -> None:
        self.log_page.append(f"💀 {msg}")
        self.home_page.set_state("error")
        QMessageBox.critical(self, "Фатальная ошибка", msg)

    @Slot(float)
    def on_level(self, level: float) -> None:
        self.home_page.set_level(level)

    @Slot(bool, str, object)
    def on_reindex_done(self, ok: bool, err: str, stats) -> None:
        if ok:
            n = getattr(stats, "commands_total", 0) if stats else 0
            v = getattr(stats, "vector_total", 0) if stats else 0
            srcs = getattr(stats, "auto_sources", {}) if stats else {}
            src_str = ", ".join(f"{k}={vv}" for k, vv in srcs.items() if vv) or "—"
            self.log_page.append(f"✓ Реиндекс ок: {n} команд / {v} векторов · auto: {src_str}")
        else:
            self.log_page.append(f"⚠ Реиндекс не удался: {err}")
        self.commands_page.refresh()

    @Slot(object)
    def on_update_result(self, result: UpdateResult) -> None:
        if not result.ok:
            self.log_page.append(f"⚠ git pull: {result.error}")
            QMessageBox.warning(self, "Обновление не удалось", result.error)
            return
        if result.already_up_to_date:
            self.log_page.append("✓ git pull: всё актуально")
            QMessageBox.information(self, "Уже последняя версия",
                                    "Локальная копия уже на свежем main.")
            return
        commits = "\n".join(f"  {sha} {msg}" for sha, msg in result.pulled_commits) or "  (нет деталей)"
        files = "\n".join(f"  {f}" for f in result.changed_files) or "  (нет деталей)"
        self.log_page.append(
            f"✓ git pull: {len(result.pulled_commits)} коммитов\n{commits}\nИзменены файлы:\n{files}")
        msg = (
            f"Подтянуто коммитов: {len(result.pulled_commits)}\n\n{commits}\n\n"
            f"Изменённые файлы:\n{files}\n\n"
            + ("Рекомендую перезапустить приложение, чтобы изменения вступили в силу."
               if result.needs_restart else "Можно продолжать работу."))
        QMessageBox.information(self, "Обновление получено", msg)

    @Slot()
    def reload_complete(self) -> None:
        self.commands_page.refresh()
        self.log_page.append("✓ База перезагружена")

    # ── Состояние нижней строки статусов ────────────────────────────
    def set_mic_subtitle(self, text: str) -> None:
        self.status_row.mic.set_subtitle(text)

    def set_brain_subtitle(self, text: str) -> None:
        self.status_row.brain.set_subtitle(text)

    def set_resources_subtitle(self, text: str) -> None:
        self.status_row.resources.set_subtitle(text)
