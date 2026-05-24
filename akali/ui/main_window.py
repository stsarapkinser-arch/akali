"""Главное окно ассистента — минималистичный экран с реактором.

Архитектура:
    • Нативный системный декор окна (KDE/GNOME/whatever DE рисует tit
      lebar) — drag/minimize/maximize/close через WM, без кастомных
      кнопок в шапке.
    • Только одна страница — HomePage. Никаких вкладок «Команды» и
      «Настройки» (управление вынесено в QSettings, обновление через
      трей).
    • Никакого status-row внизу.
    • Стиль `Cyber Arc` остаётся для самой страницы.

Все логи идут через `logging` в stdout — UI-вкладки «Лог» нет.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QSettings, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox,
                                QVBoxLayout, QWidget)

from ..core.backend import AssistantCore
from ..core.updater import UpdateResult
from .icons import IconSet
from .pages import HomePage

log = logging.getLogger(__name__)

WINDOW_WIDTH = 420
WINDOW_HEIGHT = 720
WINDOW_MIN_WIDTH = 380
WINDOW_MIN_HEIGHT = 600


class MainWindow(QMainWindow):
    """Главное окно: только HomePage. Без вкладок, без status row."""

    # Команды от UI к координатору (большинство сигналов сохранены ради
    # совместимости с app.py, чтобы не править кучу connect'ов; часть
    # просто не эмитится — ничего страшного).
    start_requested = Signal()
    stop_requested = Signal()
    reindex_requested = Signal()
    reload_core_requested = Signal()
    update_requested = Signal(bool)        # force
    check_update_requested = Signal()
    show_requested = Signal()
    quit_requested = Signal()
    run_command_requested = Signal(str)
    edit_commands_requested = Signal()

    def __init__(self, core: AssistantCore, settings: QSettings, repo_dir: str,
                 icon: Optional[QIcon] = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._core = core
        self._settings = settings
        self._repo_dir = repo_dir
        self._icon = icon or IconSet.reactor()

        self.setWindowTitle("Akali")
        self.setWindowIcon(self._icon)
        # Нативный декор окна — без FramelessWindowHint, без StaysOnTop.
        self.setGeometry(100, 100, WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        self._minimize_to_tray = False

        self._load_stylesheet()
        self._build()

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

        self.home_page = HomePage()
        outer.addWidget(self.home_page, 1)

        self.setCentralWidget(central)

    def set_minimize_to_tray(self, value: bool) -> None:
        self._minimize_to_tray = value

    def closeEvent(self, event):  # noqa: N802
        if self._minimize_to_tray:
            self.hide()
            event.ignore()
        else:
            event.accept()

    # ── Слоты со стороны AudioWorker'а ────────────────────────
    @Slot(str)
    def on_status(self, status: str) -> None:
        self.home_page.set_state(status)

    @Slot(str)
    def on_text(self, _text: str) -> None:
        # Распознанный текст больше не выводится в UI — только в логах
        # (audio_worker уже логирует).
        pass

    @Slot(str)
    def on_partial_text(self, _text: str) -> None:
        pass

    @Slot()
    def on_wake(self) -> None:
        pass  # лог рисуется в audio_worker

    @Slot(str, str, float, str)
    def on_command_matched(self, _spoken: str, _cmd: str, conf: float,
                            method: str) -> None:
        self.home_page.show_confidence(conf, method)

    @Slot(str, object)
    def on_command_executed(self, cmd: str, result) -> None:
        rc = getattr(result, "returncode", 0)
        if getattr(result, "is_background", False):
            self.home_page.show_toast(f"▶ {cmd[:40]}")
        elif getattr(result, "error", "") or rc != 0:
            err = getattr(result, "error", "") or f"rc={rc}"
            self.home_page.show_toast(f"⚠ {err[:60]}")
        else:
            self.home_page.show_toast("ок")

    @Slot(str, float)
    def on_no_match(self, spoken: str, _max_conf: float) -> None:
        self.home_page.show_toast(f"не понял: {spoken[:30]}")

    @Slot(str)
    def on_error(self, msg: str) -> None:
        self.home_page.show_toast(f"⚠ {msg[:60]}")

    @Slot(str)
    def on_fatal_error(self, msg: str) -> None:
        log.error("Фатальная ошибка: %s", msg)
        self.home_page.set_state("error")
        QMessageBox.critical(self, "Фатальная ошибка", msg)

    @Slot(float)
    def on_level(self, level: float) -> None:
        self.home_page.set_level(level)

    @Slot(bool, str, object)
    def on_reindex_done(self, ok: bool, err: str, stats) -> None:
        if ok:
            n = getattr(stats, "commands_total", 0) if stats else 0
            self.home_page.show_toast(f"индекс пересобран: {n} команд")
        else:
            self.home_page.show_toast(f"реиндекс не удался: {err[:40]}")

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
        self.home_page.show_toast("База перезагружена")

    # ── Совместимость со старым API (no-op) ───────────────────
    # Эти методы оставлены для обратной совместимости с app.py — теперь
    # они просто ничего не делают, т.к. нижней панели больше нет.
    def set_mic_subtitle(self, _text: str) -> None:
        pass

    def set_brain_subtitle(self, _text: str) -> None:
        pass

    def set_resources_subtitle(self, _text: str) -> None:
        pass
