"""Главное окно ассистента.

Состав:
    HeaderBar           — верхний бар с логотипом и вкладками.
    QStackedWidget      — переключаемые страницы.
    [HomePage, CommandsPage, SettingsPage, LogPage]
    Footer (QLabel)     — копирайт + GitHub.

Окно НЕ владеет AudioWorker'ом; внешний координатор (`AkaliApp`) сводит
сигналы аудио к слотам окна и обратно.
"""
from __future__ import annotations

import datetime
from typing import Optional

from PySide6.QtCore import QSettings, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QFrame, QLabel, QMainWindow, QMessageBox,
                                QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__ as AKALI_VERSION
from ..core.backend import AssistantCore
from ..core.updater import UpdateResult
from .pages import CommandsPage, HomePage, LogPage, SettingsPage
from .widgets import HeaderBar


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


class MainWindow(QMainWindow):
    """Собирает страницы и пробрасывает события дальше."""

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
        self._icon = icon or QIcon()
        self.setWindowTitle("Akali — голосовой ассистент")
        self.setWindowIcon(self._icon)
        self.setMinimumSize(960, 720)

        self._build()
        self._wire()

    # ── Сборка ───────────────────────────────────────────────────────
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("centralBg")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ──
        self.header = HeaderBar(self._icon, AKALI_VERSION)
        outer.addWidget(self.header)
        sep_top = QFrame()
        sep_top.setObjectName("topSeparator")
        sep_top.setFrameShape(QFrame.HLine)
        outer.addWidget(sep_top)

        # ── Pages ──
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

        # ── Footer ──
        sep_bot = QFrame()
        sep_bot.setObjectName("bottomSeparator")
        sep_bot.setFrameShape(QFrame.HLine)
        outer.addWidget(sep_bot)
        self.footer = QLabel(
            f"© {datetime.datetime.now().year}  Akali  ·  "
            f"<a href='https://github.com/stsarapkinser-arch/akali'>github.com/stsarapkinser-arch/akali</a>"
        )
        self.footer.setObjectName("footer")
        self.footer.setOpenExternalLinks(True)
        self.footer.setAlignment(Qt.AlignHCenter)
        outer.addWidget(self.footer)

        self.setCentralWidget(central)

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

    # ── Состояние трея ──────────────────────────────────────────────
    def set_mic_subtitle(self, text: str) -> None:
        self.home_page.set_mic_subtitle(text)

    def set_brain_subtitle(self, text: str) -> None:
        self.home_page.set_brain_subtitle(text)

    def set_resources_subtitle(self, text: str) -> None:
        self.home_page.set_resources_subtitle(text)
