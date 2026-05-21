#!/usr/bin/env python3
"""Akali — голосовой ассистент для Kali Linux / KDE Plasma 6.

Запуск:
    python3 akali.py

GUI-приложение на PySide6 с системным треем. Работает с микрофоном через
sounddevice, распознаёт речь Vosk-ом, ищет команды fuzzy- и векторным
поиском (ollama), плюс — самостоятельно индексирует .desktop-приложения
и KWin-шорткаты системы (см. system_indexer.py).

Старый CLI-режим больше не поддерживается — это полноценное приложение.
"""
from __future__ import annotations

import os
import signal
import sys

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from core.audio_worker import AudioWorker
from core.backend import AssistantCore
from core.updater import UpdateResult, pull as git_pull
from ui.main_window import MainWindow
from ui.tray import TrayController


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_DIR = os.path.join(HERE, "model")
DEFAULT_REPO_DIR = HERE
ICON_PATH = os.path.join(HERE, "assets", "icon.svg")
STYLES_PATH = os.path.join(HERE, "ui", "styles.qss")


# ============================================================
# Воркер обновления (выполняет git pull в отдельном потоке)
# ============================================================
class UpdateRunner(QObject):
    finished = Signal(object)  # UpdateResult

    def __init__(self, repo_dir: str, parent: QObject | None = None):
        super().__init__(parent)
        self._repo_dir = repo_dir

    @Slot()
    def run(self):
        try:
            result = git_pull(self._repo_dir)
        except Exception as e:
            result = UpdateResult(ok=False, error=f"Неожиданная ошибка: {e}")
        self.finished.emit(result)


# ============================================================
# Application — главный координатор
# ============================================================
class AkaliApp(QObject):
    """Связывает core, AudioWorker, MainWindow и трей."""

    def __init__(self, qapp: QApplication):
        super().__init__()
        self._qapp = qapp
        self._settings = QSettings("akali", "akali")
        self._icon = self._load_icon()
        self._repo_dir = self._settings.value("repo_dir", DEFAULT_REPO_DIR) or DEFAULT_REPO_DIR
        self._model_dir = self._settings.value("vosk_model_dir", DEFAULT_MODEL_DIR) or DEFAULT_MODEL_DIR

        # === Backend ===
        self._core = AssistantCore(base_dir=HERE)
        self._restore_core_settings()

        # === Audio thread (создаём, но не стартуем) ===
        self._audio_thread = QThread()
        self._audio_thread.setObjectName("akali-audio")
        self._audio_worker = AudioWorker(self._core, self._model_dir)
        self._audio_worker.moveToThread(self._audio_thread)
        self._is_listening = False

        # === UI ===
        self._window = MainWindow(self._core, self._settings, self._repo_dir, icon=self._icon)
        self._tray = TrayController(self._icon, qapp)

        # === Update worker (создаётся по требованию) ===
        self._update_thread: QThread | None = None
        self._update_runner: UpdateRunner | None = None

        self._wire()
        self._initial_load()

    # ----------------------------------------------------------------
    def _load_icon(self) -> QIcon:
        if os.path.exists(ICON_PATH):
            return QIcon(ICON_PATH)
        return QIcon()

    def _restore_core_settings(self):
        s = self._settings
        try:
            self._core.fuzzy_threshold = float(s.value("fuzzy_threshold", self._core.fuzzy_threshold))
            self._core.vector_threshold = float(s.value("vector_threshold", self._core.vector_threshold))
            self._core.wake_threshold = float(s.value("wake_threshold", self._core.wake_threshold))
        except (TypeError, ValueError):
            pass
        wake = s.value("wake_words")
        if isinstance(wake, str) and wake.strip():
            self._core.wake_words = [w.strip() for w in wake.split(",") if w.strip()]
        rt = s.value("reindex_triggers")
        if isinstance(rt, str) and rt.strip():
            self._core.reindex_triggers = [r.strip() for r in rt.split(",") if r.strip()]

    def _initial_load(self):
        # Загружаем базу в основном потоке (быстро, ~ 1 сек без аудио)
        stats = self._core.reload()
        srcs = ", ".join(f"{k}={v}" for k, v in stats.auto_sources.items() if v) or "—"
        self._window.log_tab.append(
            f"⚙ Загружена база: {stats.commands_total} команд "
            f"(curated={stats.curated_count}, auto={stats.auto_count}), "
            f"{stats.vector_total} векторов (built={stats.vectors_built}, reused={stats.vectors_reused}) "
            f"· auto: {srcs}")
        self._window.commands_tab.refresh()

    # ----------------------------------------------------------------
    def _wire(self):
        w = self._window
        a = self._audio_worker
        t = self._tray

        # UI → action
        w.start_requested.connect(self.start_listening)
        w.stop_requested.connect(self.stop_listening)
        w.reindex_requested.connect(a.request_reindex)
        w.reload_core_requested.connect(self._reload_core)
        w.settings_tab.update_requested.connect(self.run_update)

        # Tray → action
        t.start_clicked.connect(self.start_listening)
        t.stop_clicked.connect(self.stop_listening)
        t.reindex_clicked.connect(a.request_reindex)
        t.update_clicked.connect(self.run_update)
        t.show_window_clicked.connect(self.show_window)
        t.quit_clicked.connect(self.quit)

        # Audio → UI
        a.status_changed.connect(w.on_status)
        a.status_changed.connect(self._on_status_for_tray)
        a.text_recognized.connect(w.on_text)
        a.wake_word_detected.connect(w.on_wake)
        a.wake_word_detected.connect(self._wake_for_tray)
        a.command_matched.connect(w.on_command_matched)
        a.command_executed.connect(w.on_command_executed)
        a.no_match.connect(w.on_no_match)
        a.error.connect(w.on_error)
        a.fatal_error.connect(w.on_fatal_error)
        a.fatal_error.connect(self._on_fatal_error)
        a.level_changed.connect(w.on_level)
        a.reindex_done.connect(w.on_reindex_done)
        a.stopped.connect(self._on_audio_stopped)

        # Audio thread lifecycle
        self._audio_thread.started.connect(a.start_listening)

        # Application quit
        self._qapp.aboutToQuit.connect(self._shutdown)

    # ----------------------------------------------------------------
    @Slot()
    def start_listening(self):
        if self._is_listening:
            return
        self._is_listening = True
        self._tray.set_listening(True)
        self._audio_thread.start()

    @Slot()
    def stop_listening(self):
        if not self._is_listening:
            return
        self._audio_worker.request_stop()

    @Slot()
    def _on_audio_stopped(self):
        self._is_listening = False
        self._tray.set_listening(False)
        self._audio_thread.quit()
        self._audio_thread.wait(2000)

    @Slot(str)
    def _on_status_for_tray(self, state: str):
        # Лёгкая индикация в тултипе
        labels = {
            "listening": "Слушаю",
            "waiting_command": "Жду команду",
            "processing": "Выполняю",
            "reindexing": "Реиндексирую",
            "recovering": "Восстанавливаю аудио",
            "stopped": "Остановлен",
            "starting": "Запускаюсь",
        }
        self._tray.tray.setToolTip(f"Akali — {labels.get(state, state)}")

    @Slot()
    def _wake_for_tray(self):
        # Лёгкая нотификация (можно отключить если будет шумно)
        pass

    @Slot(str)
    def _on_fatal_error(self, msg: str):
        self._is_listening = False
        self._tray.set_listening(False)
        if self._audio_thread.isRunning():
            self._audio_thread.quit()
            self._audio_thread.wait(2000)

    @Slot()
    def _reload_core(self):
        try:
            self._core.reload()
            self._window.reload_complete()
        except Exception as e:
            self._window.log_tab.append(f"⚠ Перезагрузка не удалась: {e}")

    @Slot()
    def show_window(self):
        if self._window.isMinimized():
            self._window.showNormal()
        else:
            self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    @Slot()
    def quit(self):
        self._qapp.quit()

    @Slot()
    def _shutdown(self):
        # Остановить аудио
        if self._is_listening:
            self._audio_worker.request_stop()
            self._audio_thread.quit()
            self._audio_thread.wait(2000)
        # Сохранить настройки последний раз
        try:
            self._settings.sync()
        except Exception:
            pass

    # ----------------------------------------------------------------
    @Slot()
    def run_update(self):
        if self._update_thread and self._update_thread.isRunning():
            self._window.log_tab.append("⏳ Обновление уже выполняется…")
            return
        repo = self._window.settings_tab.repo_dir or self._repo_dir
        self._window.log_tab.append(f"⏳ git pull в {repo}…")
        thread = QThread()
        runner = UpdateRunner(repo)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.finished.connect(self._on_update_finished)
        runner.finished.connect(thread.quit)
        runner.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._update_thread = thread
        self._update_runner = runner
        thread.start()

    @Slot(object)
    def _on_update_finished(self, result: UpdateResult):
        self._window.on_update_result(result)
        self._update_thread = None
        self._update_runner = None


# ============================================================
# main()
# ============================================================
def _apply_stylesheet(app: QApplication):
    if os.path.exists(STYLES_PATH):
        with open(STYLES_PATH, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


def main():
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("Akali")
    qapp.setApplicationDisplayName("Akali — голосовой ассистент")
    qapp.setOrganizationName("Akali")
    qapp.setQuitOnLastWindowClosed(False)  # окно закрыли → живём в трее
    _apply_stylesheet(qapp)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Системный трей недоступен",
                             "Akali требует системный трей. На KDE Plasma он включён "
                             "по умолчанию. Под GNOME/Wayland установи расширение TopIconsFix.")
        sys.exit(1)

    app = AkaliApp(qapp)
    app.show_window()

    # Корректное завершение по Ctrl+C в терминале
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
