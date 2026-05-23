"""AkaliApp — координатор приложения.

Связывает в одно целое:
  • AssistantCore (база команд),
  • AudioWorker в отдельном QThread (микрофон + Vosk),
  • MainWindow и TrayController (UI),
  • UpdateRunner в отдельном QThread (git pull).

Не содержит бизнес-логики — только проводка сигналов между подсистемами.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import paths
from .core.audio_worker import AudioWorker
from .core.backend import AssistantCore
from .core.updater import UpdateResult, VersionInfo, pull as git_pull, get_version_info
from .ui.main_window import MainWindow
from .ui.tray import TrayController


# ============================================================
# CheckRunner — проверяет наличие обновлений (только fetch)
# ============================================================
class CheckRunner(QObject):
    finished = Signal(object)  # VersionInfo

    def __init__(self, repo_dir: str, parent: QObject | None = None):
        super().__init__(parent)
        self._repo_dir = repo_dir

    @Slot()
    def run(self) -> None:
        try:
            info = get_version_info(self._repo_dir)
        except Exception as e:  # noqa: BLE001
            info = VersionInfo(error=f"Неожиданная ошибка: {e}")
        self.finished.emit(info)


# ============================================================
# UpdateRunner — выполняет git pull в фоне
# ============================================================
class UpdateRunner(QObject):
    finished = Signal(object)  # UpdateResult

    def __init__(self, repo_dir: str, parent: QObject | None = None):
        super().__init__(parent)
        self._repo_dir = repo_dir

    @Slot()
    def run(self) -> None:
        try:
            result = git_pull(self._repo_dir)
        except Exception as e:  # noqa: BLE001
            result = UpdateResult(ok=False, error=f"Неожиданная ошибка: {e}")
        self.finished.emit(result)


# ============================================================
# LlmFallbackRunner — запрашивает LLM в фоне при no_match
# ============================================================
class LlmFallbackRunner(QObject):
    finished = Signal(str, str)  # query, response

    def __init__(self, core: AssistantCore, query: str,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._core = core
        self._query = query

    @Slot()
    def run(self) -> None:
        try:
            response = self._core.route_with_llm(self._query) or ""
        except Exception:  # noqa: BLE001
            response = ""
        self.finished.emit(self._query, response)


# ============================================================
# AkaliApp — главный координатор
# ============================================================
class AkaliApp(QObject):
    """Соединяет core, AudioWorker, MainWindow и трей."""

    def __init__(self, qapp: QApplication):
        super().__init__()
        self._qapp = qapp
        self._settings = QSettings("akali", "akali")
        self._icon = QIcon(paths.as_str(paths.APP_ICON)) if paths.APP_ICON.exists() else QIcon()
        self._repo_dir = str(self._settings.value("repo_dir", paths.PROJECT_ROOT) or paths.PROJECT_ROOT)
        self._model_dir = str(self._settings.value("vosk_model_dir", paths.DEFAULT_VOSK_MODEL_DIR)
                              or paths.DEFAULT_VOSK_MODEL_DIR)

        # === Backend ===
        self._core = AssistantCore()
        self._restore_core_settings()

        # === Audio thread (создан, не стартован) ===
        self._audio_thread = QThread()
        self._audio_thread.setObjectName("akali-audio")
        # Сохранённый индекс устройства (None = PortAudio default)
        dev_raw = self._settings.value("audio_device", None)
        device_index: int | None = None
        if isinstance(dev_raw, int):
            device_index = dev_raw
        elif isinstance(dev_raw, str) and dev_raw.strip().lstrip("-").isdigit():
            device_index = int(dev_raw)
        self._audio_worker = AudioWorker(self._core, self._model_dir,
                                          device_index=device_index)
        self._audio_worker.moveToThread(self._audio_thread)
        self._is_listening = False

        # === UI ===
        self._window = MainWindow(self._core, self._settings, self._repo_dir, icon=self._icon)
        self._tray = TrayController(self._icon, qapp)
        self._window.set_minimize_to_tray(True)

        # === Update worker ===
        self._update_thread: QThread | None = None
        self._update_runner: UpdateRunner | None = None

        # === Check worker (только fetch, без pull) ===
        self._check_thread: QThread | None = None
        self._check_runner: CheckRunner | None = None

        # === LLM fallback worker ===
        self._llm_thread: QThread | None = None
        self._llm_runner: LlmFallbackRunner | None = None

        # === Resource poll timer ===
        self._resource_timer = QTimer(self)
        self._resource_timer.setInterval(2000)
        self._resource_timer.timeout.connect(self._update_resources)

        self._wire()
        self._initial_load()
        self._update_resources()  # первая выдача статусных подзаголовков
        self._resource_timer.start()

    # ------------------------------------------------------------
    def _restore_core_settings(self) -> None:
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

    def _initial_load(self) -> None:
        stats = self._core.reload()
        srcs = ", ".join(f"{k}={v}" for k, v in stats.auto_sources.items() if v) or "—"
        self._window.log_page.append(
            f"⚙ Загружена база: {stats.commands_total} команд "
            f"(curated={stats.curated_count}, auto={stats.auto_count}), "
            f"{stats.vector_total} векторов "
            f"(built={stats.vectors_built}, reused={stats.vectors_reused}) "
            f"· auto: {srcs}")
        self._window.commands_page.refresh()
        self._window.set_brain_subtitle(
            f"Vosk + Ollama ({self._core.vector_model})")

        # Инициализируем QueryRouter в фоне (не блокируем запуск)
        gemini_key = str(self._settings.value("gemini_api_key", "") or "").strip()
        try:
            self._core.init_router(gemini_api_key=gemini_key or None)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------
    def _wire(self) -> None:
        w = self._window
        a = self._audio_worker
        t = self._tray

        # UI → action
        w.start_requested.connect(self.start_listening)
        w.stop_requested.connect(self.stop_listening)
        w.reindex_requested.connect(a.request_reindex)
        w.reload_core_requested.connect(self._reload_core)
        w.update_requested.connect(self.run_update)
        w.check_update_requested.connect(self.run_check)
        w.show_requested.connect(self.show_window)
        w.quit_requested.connect(self.quit)
        w.settings_page.device_changed.connect(a.set_device)
        w.run_command_requested.connect(self._on_run_command)
        w.llm_fallback_requested.connect(self._on_llm_fallback_requested)

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
        a.partial_text.connect(w.on_partial_text)
        a.wake_word_detected.connect(w.on_wake)
        a.command_matched.connect(w.on_command_matched)
        a.command_executed.connect(w.on_command_executed)
        a.no_match.connect(w.on_no_match)
        a.error.connect(w.on_error)
        a.fatal_error.connect(w.on_fatal_error)
        a.fatal_error.connect(self._on_fatal_error)
        a.level_changed.connect(w.on_level)
        a.reindex_done.connect(w.on_reindex_done)
        a.device_info.connect(self._on_device_info)
        a.stopped.connect(self._on_audio_stopped)

        # Audio thread lifecycle
        self._audio_thread.started.connect(a.start_listening)

        # Application quit
        self._qapp.aboutToQuit.connect(self._shutdown)

    # ------------------------------------------------------------
    @Slot()
    def start_listening(self) -> None:
        if self._is_listening:
            return
        self._is_listening = True
        self._tray.set_listening(True)
        self._audio_thread.start()

    @Slot()
    def stop_listening(self) -> None:
        if not self._is_listening:
            return
        self._audio_worker.request_stop()

    @Slot()
    def _on_audio_stopped(self) -> None:
        self._is_listening = False
        self._tray.set_listening(False)
        self._audio_thread.quit()
        self._audio_thread.wait(2000)

    @Slot(str)
    def _on_device_info(self, info: str) -> None:
        self._window.set_mic_subtitle(info[:40])
        self._window.log_page.append(f"🎤 Микрофон: {info}")

    @Slot(str)
    def _on_status_for_tray(self, state: str) -> None:
        labels = {
            "listening": "Слушаю",
            "waiting_command": "Жду команду",
            "processing": "Выполняю",
            "reindexing": "Реиндексирую",
            "recovering": "Восстанавливаю аудио",
            "stopped": "Остановлен",
            "starting": "Запускаюсь",
            "error": "Ошибка",
        }
        self._tray.tray.setToolTip(f"Akali — {labels.get(state, state)}")

    @Slot(str)
    def _on_fatal_error(self, msg: str) -> None:
        self._is_listening = False
        self._tray.set_listening(False)
        if self._audio_thread.isRunning():
            self._audio_thread.quit()
            self._audio_thread.wait(2000)

    @Slot()
    def _reload_core(self) -> None:
        try:
            self._core.reload()
            self._window.reload_complete()
        except Exception as e:  # noqa: BLE001
            self._window.log_page.append(f"⚠ Перезагрузка не удалась: {e}")

    @Slot()
    def show_window(self) -> None:
        if self._window.isMinimized():
            self._window.showNormal()
        else:
            self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    @Slot()
    def quit(self) -> None:
        self._qapp.quit()

    @Slot()
    def _shutdown(self) -> None:
        if self._is_listening:
            self._audio_worker.request_stop()
            self._audio_thread.quit()
            self._audio_thread.wait(2000)
        try:
            self._settings.sync()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------
    @Slot()
    def run_check(self) -> None:
        """Проверяет наличие обновлений (только fetch, без pull)."""
        if self._check_thread and self._check_thread.isRunning():
            return
        if self._update_thread and self._update_thread.isRunning():
            return
        repo = self._window.settings_page.repo_dir or self._repo_dir
        self._window.log_page.append(f"🔍 Проверка обновлений в {repo}…")
        thread = QThread()
        runner = CheckRunner(repo)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.finished.connect(self._on_check_finished)
        runner.finished.connect(thread.quit)
        runner.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._check_thread = thread
        self._check_runner = runner
        thread.start()

    @Slot(object)
    def _on_check_finished(self, info: VersionInfo) -> None:
        self._window.settings_page.on_version_info(info)
        self._check_thread = None
        self._check_runner = None
        if info.has_updates:
            self._window.log_page.append(
                f"⬇ Доступно {info.commits_behind} новых коммитов")
        elif not info.error:
            self._window.log_page.append("✓ Версия актуальна")
        else:
            self._window.log_page.append(f"⚠ Проверка обновлений: {info.error}")

    @Slot()
    def run_update(self) -> None:
        if self._update_thread and self._update_thread.isRunning():
            self._window.log_page.append("⏳ Обновление уже выполняется…")
            return
        if self._check_thread and self._check_thread.isRunning():
            self._window.log_page.append("⏳ Дождись завершения проверки…")
            return
        repo = self._window.settings_page.repo_dir or self._repo_dir
        self._window.log_page.append(f"⏳ git pull в {repo}…")
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
    def _on_update_finished(self, result: UpdateResult) -> None:
        self._window.on_update_result(result)
        self._window.settings_page.on_update_result(result)
        self._update_thread = None
        self._update_runner = None

    # ------------------------------------------------------------
    # Click-to-run из CommandsPage
    # ------------------------------------------------------------
    @Slot(str)
    def _on_run_command(self, cmd: str) -> None:
        try:
            result = self._core.execute(cmd)
            self._window.on_command_executed(cmd, result)
        except Exception as e:  # noqa: BLE001
            self._window.log_page.append(f"⚠ Ошибка запуска: {e}")
        short = cmd[:40] + ("…" if len(cmd) > 40 else "")
        self._window.home_page.show_toast(f"▶ {short}")

    # ------------------------------------------------------------
    # LLM-фоллбэк при no_match
    # ------------------------------------------------------------
    @Slot(str)
    def _on_llm_fallback_requested(self, query: str) -> None:
        if not query.strip():
            return
        if self._llm_thread and self._llm_thread.isRunning():
            return  # уже в работе
        thread = QThread()
        runner = LlmFallbackRunner(self._core, query)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.finished.connect(self._on_llm_fallback_done)
        runner.finished.connect(thread.quit)
        runner.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_llm_thread_done)
        self._llm_thread = thread
        self._llm_runner = runner
        thread.start()

    @Slot()
    def _on_llm_thread_done(self) -> None:
        self._llm_thread = None
        self._llm_runner = None

    @Slot(str, str)
    def _on_llm_fallback_done(self, query: str, response: str) -> None:
        if response.strip():
            self._window.home_page.show_llm_response(response)
            self._window.log_page.append(f"🤖 LLM «{query}»: {response[:120]}")

    # ------------------------------------------------------------
    # Подписи под нижней строкой статусов
    # ------------------------------------------------------------
    def _update_resources(self) -> None:
        # RAM текущего процесса (без сторонних библиотек)
        try:
            with open(f"/proc/{os.getpid()}/status", "r", encoding="utf-8") as f:
                rss_kb = 0
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break
            mb = rss_kb / 1024.0
            self._window.set_resources_subtitle(f"RAM {mb:.0f} MB")
        except OSError:
            self._window.set_resources_subtitle(socket.gethostname())

        # Микрофон обновляется по сигналу device_info из AudioWorker'а;
        # тут только показываем «Не слушает», когда поток выключен.
        if not self._is_listening:
            self._window.set_mic_subtitle("Не слушает")


# ============================================================
# main()
# ============================================================
def _apply_stylesheet(app: QApplication) -> None:
    if paths.APP_STYLESHEET.exists():
        app.setStyleSheet(paths.APP_STYLESHEET.read_text(encoding="utf-8"))


def main() -> int:
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("Akali")
    qapp.setApplicationDisplayName("Akali — голосовой ассистент")
    qapp.setOrganizationName("Akali")
    qapp.setQuitOnLastWindowClosed(False)
    _apply_stylesheet(qapp)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(
            None, "Системный трей недоступен",
            "Akali требует системный трей. На KDE Plasma он включён по умолчанию. "
            "Под GNOME/Wayland установи расширение TopIconsFix.")
        return 1

    app = AkaliApp(qapp)
    app.show_window()

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
