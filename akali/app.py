"""AkaliApp — координатор приложения.

Связывает в одно целое:
  • AssistantCore (база команд + роутер) — главный пайплайн.
  • AudioWorker в отдельном QThread (микрофон + Vosk + dispatch).
  • MainWindow и TrayController (UI).
  • TTS (piper / espeak-ng) — голосовой ответ.
  • UpdateRunner в отдельном QThread (git fetch/pull/reset).
  • SystemCheckRunner для проверки Ollama, piper, Vosk, Gemini.
  • Авто-проверка обновлений по таймеру (опционально).

Главные принципы:
  • Никаких крашей — main() обёрнут в try/except.
  • Логи — в stdout через `logging`.
  • После обновления, если изменились .py/.qss — graceful restart os.execv.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import (
    QObject, QSettings, QThread, QTimer, Signal, Slot,
    qInstallMessageHandler, QtMsgType,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import __version__ as AKALI_VERSION
from . import paths
from .core import log as log_setup
from .core import safety, system_check
from .core.audio_worker import AudioWorker
from .core.backend import AssistantCore
from .core.tts import TextToSpeech
from .core.updater import UpdateResult, VersionInfo, pull as git_pull, get_version_info
from .ui.main_window import MainWindow
from .ui.tray import TrayController

log = logging.getLogger(__name__)


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

    def __init__(self, repo_dir: str, force: bool, parent: QObject | None = None):
        super().__init__(parent)
        self._repo_dir = repo_dir
        self._force = force

    @Slot()
    def run(self) -> None:
        try:
            result = git_pull(self._repo_dir, force=self._force)
        except Exception as e:  # noqa: BLE001
            result = UpdateResult(ok=False, error=f"Неожиданная ошибка: {e}")
        self.finished.emit(result)


# ============================================================
# SystemCheckRunner — проверяет компоненты
# ============================================================
class SystemCheckRunner(QObject):
    finished = Signal(object)  # SystemReport

    def __init__(self, gemini_key: str | None, parent: QObject | None = None):
        super().__init__(parent)
        self._gemini_key = gemini_key

    @Slot()
    def run(self) -> None:
        try:
            report = system_check.run_all_checks(self._gemini_key)
        except Exception as e:  # noqa: BLE001
            log.error("system_check упал: %s", e)
            report = system_check.SystemReport(
                items=[system_check.CheckResult(
                    "system_check", False, f"исключение: {e}", False)])
        self.finished.emit(report)


# ============================================================
# AutoInstallRunner — на старте: проверяет всё и доставляет
# безопасные компоненты (pip-пакеты, vosk-модель, голос piper)
# ============================================================
class AutoInstallRunner(QObject):
    progress = Signal(str)
    finished = Signal(object)  # SystemReport (после второй проверки)

    def __init__(self, gemini_key: str | None, parent: QObject | None = None):
        super().__init__(parent)
        self._gemini_key = gemini_key

    @Slot()
    def run(self) -> None:
        try:
            report = system_check.auto_install_safe(
                api_key=self._gemini_key,
                progress=self.progress.emit,
            )
        except Exception as e:  # noqa: BLE001
            log.error("auto_install_safe упал: %s", e)
            report = system_check.SystemReport(
                items=[system_check.CheckResult(
                    "auto_install_safe", False, f"исключение: {e}", False)])
        self.finished.emit(report)


# ============================================================
# GeminiTestRunner — проверка API key
# ============================================================
class GeminiTestRunner(QObject):
    finished = Signal(bool, str)

    def __init__(self, api_key: str, parent: QObject | None = None):
        super().__init__(parent)
        self._api_key = api_key

    @Slot()
    def run(self) -> None:
        try:
            cr = system_check.check_gemini(self._api_key)
            self.finished.emit(cr.ok, cr.message)
        except Exception as e:  # noqa: BLE001
            self.finished.emit(False, f"Ошибка теста: {e}")


# ============================================================
# AkaliApp — главный координатор
# ============================================================
class AkaliApp(QObject):
    """Соединяет core, AudioWorker, MainWindow, трей, TTS, updater."""

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

        # === TTS ===
        tts_enabled = str(self._settings.value("tts_enabled", "true")).lower() in ("1", "true", "yes")
        tts_voice = str(self._settings.value("tts_voice", "auto") or "auto")
        tts_piper_voice = str(self._settings.value(
            "tts_piper_voice", system_check.PIPER_DEFAULT_VOICE,
        ) or system_check.PIPER_DEFAULT_VOICE)
        self._tts = TextToSpeech(
            enabled=tts_enabled, prefer=tts_voice,
            piper_voice=tts_piper_voice,
        )

        # === Safety notifier ===
        safety.set_notifier(self._on_safety_violation)

        # === Audio thread (создан, не стартован) ===
        self._audio_thread = QThread()
        self._audio_thread.setObjectName("akali-audio")
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
        self._check_thread: QThread | None = None
        self._check_runner: CheckRunner | None = None
        self._sys_check_thread: QThread | None = None
        self._sys_check_runner: SystemCheckRunner | None = None
        self._gemini_test_thread: QThread | None = None
        self._gemini_test_runner: GeminiTestRunner | None = None
        self._auto_install_thread: QThread | None = None
        self._auto_install_runner: AutoInstallRunner | None = None

        # === Auto-update timer ===
        # Status row убран — resource poll больше не нужен.

        self._auto_update_timer = QTimer(self)
        self._auto_update_timer.setSingleShot(False)
        self._auto_update_timer.timeout.connect(self._on_auto_update_tick)

        self._wire()
        self._initial_load()
        self._apply_auto_update_setting(int(self._settings.value("auto_update_minutes", 0) or 0))

        # === Старт автоустановки безопасных компонентов в фоне.
        # Сразу же — но через event-loop, чтобы UI успел отрисоваться.
        QTimer.singleShot(200, self._kickoff_auto_install)

        # === Авто-старт прослушивания: Кнопки «Слушать» больше нет —
        # Акали сразу начинает слушать. Через QTimer, чтобы UI и трей
        # успели инициализироваться.
        QTimer.singleShot(300, self.start_listening)

    # ------------------------------------------------------------
    def _restore_core_settings(self) -> None:
        s = self._settings
        try:
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
        try:
            stats = self._core.reload()
        except Exception as e:  # noqa: BLE001
            log.error("Не удалось прочитать базу команд: %s", e)
            stats = None

        if stats:
            srcs = ", ".join(f"{k}={v}" for k, v in stats.auto_sources.items() if v) or "—"
            log.info("База команд: %d (curated=%d, auto=%d) · auto: %s",
                     stats.commands_total, stats.curated_count, stats.auto_count, srcs)

        gemini_key = str(self._settings.value("gemini_api_key", "") or "").strip()
        llm_mode = str(self._settings.value("llm_mode", "auto") or "auto")
        try:
            self._core.init_router(
                gemini_api_key=gemini_key or None,
                llm_mode=llm_mode,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("QueryRouter инициализация: %s", e)

    # ------------------------------------------------------------
    def _wire(self) -> None:
        w = self._window
        a = self._audio_worker
        t = self._tray

        # UI → action (вкладок «Команды» и «Настройки» больше нет —
        # сигналы оставлены ради совместимости с трейем и start/stop).
        w.start_requested.connect(self.start_listening)
        w.stop_requested.connect(self.stop_listening)
        w.reindex_requested.connect(a.request_reindex)
        w.reload_core_requested.connect(self._reload_core)
        w.update_requested.connect(self.run_update)
        w.check_update_requested.connect(self.run_check)
        w.show_requested.connect(self.show_window)
        w.quit_requested.connect(self.quit)
        w.run_command_requested.connect(self._on_run_command)

        # Tray → action
        t.start_clicked.connect(self.start_listening)
        t.stop_clicked.connect(self.stop_listening)
        t.reindex_clicked.connect(a.request_reindex)
        t.update_clicked.connect(lambda: self.run_update(False))
        t.show_window_clicked.connect(self.show_window)
        t.quit_clicked.connect(self.quit)

        # Audio → UI
        a.status_changed.connect(w.on_status)
        a.status_changed.connect(self._on_status_for_tray)
        a.text_recognized.connect(w.on_text)
        a.partial_text.connect(w.on_partial_text)
        a.wake_word_detected.connect(w.on_wake)
        a.command_matched.connect(w.on_command_matched)
        a.command_matched.connect(self._on_command_matched_voice)
        a.command_executed.connect(w.on_command_executed)
        a.no_match.connect(w.on_no_match)
        a.no_match.connect(self._on_no_match_voice)
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
        log.info("Микрофон: %s", info)

    @Slot(str)
    def _on_status_for_tray(self, state: str) -> None:
        labels = {
            "listening": "Слушаю", "waiting_command": "Жду команду",
            "processing": "Выполняю", "reindexing": "Реиндексирую",
            "recovering": "Восстанавливаю аудио", "stopped": "Остановлен",
            "starting": "Запускаюсь", "error": "Ошибка",
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
            log.error("Перезагрузка не удалась: %s", e)

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
        """Graceful shutdown ВСЕХ ресурсов. Каждый шаг изолирован в try/except,
        чтобы один сбой не помешал остановке остального.
        """
        # 1. Таймеры
        for tmr_attr in ("_auto_update_timer",):
            try:
                tmr = getattr(self, tmr_attr, None)
                if tmr is not None and tmr.isActive():
                    tmr.stop()
            except Exception:  # noqa: BLE001
                pass

        # 2. Audio worker
        try:
            if self._is_listening:
                self._audio_worker.request_stop()
            if self._audio_thread is not None:
                self._audio_thread.quit()
                self._audio_thread.wait(2500)
        except Exception:  # noqa: BLE001
            pass

        # 3. Все runner-потоки (update/check/sys-check/auto-install/gemini-test)
        for attr in ("_update_thread", "_check_thread", "_sys_check_thread",
                     "_auto_install_thread", "_gemini_test_thread"):
            try:
                thread = getattr(self, attr, None)
                if thread is not None and thread.isRunning():
                    thread.quit()
                    thread.wait(2500)
            except Exception:  # noqa: BLE001
                pass

        # 4. TTS
        try:
            self._tts.shutdown()
        except Exception:  # noqa: BLE001
            pass

        # 5. Сбросить настройки на диск
        try:
            self._settings.sync()
        except Exception:  # noqa: BLE001
            pass

    # ── Update flow ────────────────────────────────────────
    @Slot()
    def run_check(self) -> None:
        if (self._check_thread and self._check_thread.isRunning()) or \
           (self._update_thread and self._update_thread.isRunning()):
            return
        repo = self._repo_dir
        log.info("Проверка обновлений в %s…", repo)
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
        try:
            if info.error:
                log.warning("Проверка обновлений: %s", info.error)
            elif getattr(info, "has_updates", False):
                log.info("Доступно %d новых коммитов",
                         getattr(info, "commits_behind", 0))
            else:
                log.info("Версия актуальна")
        except Exception as e:  # noqa: BLE001
            log.error("on_check_finished упал: %s", e)
        self._check_thread = None
        self._check_runner = None

    @Slot(bool)
    def run_update(self, force: bool = False) -> None:
        if self._update_thread and self._update_thread.isRunning():
            log.info("Обновление уже идёт, жди…")
            return
        if self._check_thread and self._check_thread.isRunning():
            log.info("Дождись проверки…")
            return
        repo = self._repo_dir
        log.info("git pull (force=%s) в %s…", force, repo)
        thread = QThread()
        thread.setObjectName("akali-update")
        runner = UpdateRunner(repo, force)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.finished.connect(self._on_update_finished)
        runner.finished.connect(thread.quit)
        runner.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_update_refs)
        self._update_thread = thread
        self._update_runner = runner
        thread.start()

    @Slot()
    def _clear_update_refs(self) -> None:
        self._update_thread = None
        self._update_runner = None

    @Slot(object)
    def _on_update_finished(self, result: UpdateResult) -> None:
        # Каждый шаг изолирован. Никаких raise наружу — иначе Qt свалится.
        try:
            self._window.on_update_result(result)
        except Exception as e:  # noqa: BLE001
            log.error("on_update_result (window) упал: %s", e)
        # thread/runner будут обнулены через thread.finished → _clear_update_refs

        if result.ok and result.needs_restart:
            log.info("Изменения требуют перезапуска — перезапускаю через 1.5с…")
            QTimer.singleShot(1500, self._restart_app)
        elif result.ok and result.already_up_to_date:
            log.info("Версия уже актуальна.")
        elif result.ok:
            log.info("Подтянуто %d коммита(ов). Перезапуск не нужен.",
                     len(result.pulled_commits))
        else:
            log.warning("Обновление не удалось: %s", result.error)

    def _restart_app(self) -> None:
        """Graceful restart: запускаем НОВЫЙ процесс через subprocess.Popen,
        затем чисто гасим текущий через qapp.quit(). Это безопаснее os.execv,
        потому что Qt успевает корректно отработать деструкторы (закрыть Wayland
        сокеты, аудио стримы, GL контексты). Если Popen упал — пробуем execv
        как фоллбэк, но не валим процесс при провале.
        """
        # 1. Сначала аккуратно гасим всё на стороне Qt/потоков
        try:
            self._shutdown()
        except Exception as e:  # noqa: BLE001
            log.error("shutdown перед рестартом упал: %s", e)

        # 2. Запускаем новый процесс
        python = sys.executable
        argv = [python] + list(sys.argv)
        try:
            log.info("Перезапускаю: %s", " ".join(argv))
            # start_new_session=True — отвязываем новый процесс от текущей
            # сессии, чтобы он пережил наш exit
            subprocess.Popen(  # noqa: S603
                argv,
                cwd=self._repo_dir,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as e:
            log.error("Popen для рестарта упал: %s. Пробую os.execv…", e)
            try:
                os.execv(python, argv)
                return  # никогда не возвращается
            except OSError as e2:
                log.error("os.execv тоже упал: %s", e2)
                try:
                    QMessageBox.warning(
                        self._window, "Перезапуск",
                        f"Не удалось перезапуститься: {e2}\n"
                        "Закрой и запусти приложение вручную.")
                except Exception:  # noqa: BLE001
                    pass
                return  # остаёмся жить в старой версии

        # 3. Завершаем старый процесс через event-loop
        try:
            self._qapp.quit()
        except Exception:  # noqa: BLE001
            os._exit(0)

    # ── Auto-update timer ──────────────────────────────────
    @Slot(int)
    def _apply_auto_update_setting(self, minutes: int) -> None:
        if minutes <= 0:
            if self._auto_update_timer.isActive():
                self._auto_update_timer.stop()
                log.info("Авто-проверка обновлений выключена.")
            return
        self._auto_update_timer.setInterval(int(minutes * 60_000))
        if not self._auto_update_timer.isActive():
            self._auto_update_timer.start()
        log.info("Авто-проверка обновлений: каждые %d мин.", minutes)

    @Slot()
    def _on_auto_update_tick(self) -> None:
        if (self._check_thread and self._check_thread.isRunning()) or \
           (self._update_thread and self._update_thread.isRunning()):
            return
        log.debug("Автоматическая проверка обновлений…")
        self.run_check()

    # ── TTS / Safety / Gemini test / system_check ───────────
    @Slot(bool, str, str)
    def _on_tts_settings_changed(self, enabled: bool, voice: str,
                                  piper_voice: str) -> None:
        self._tts.set_enabled(enabled)
        self._tts.set_voice(voice, piper_voice=piper_voice or None)
        log.info("TTS: enabled=%s, engine=%s, голос=%s",
                 enabled, voice, piper_voice)

    def _on_safety_violation(self, source: str, command: str, reason: str) -> None:
        """Callback от safety.report — показывает уведомление."""
        log.warning("Safety: %s от %s — заблокировано (%s)", command, source, reason)
        try:
            self._window.home_page.show_toast(f"⛔ {source}: критичная команда заблокирована")
        except Exception:  # noqa: BLE001
            pass

    @Slot(str)
    def _on_gemini_test(self, api_key: str) -> None:
        if self._gemini_test_thread and self._gemini_test_thread.isRunning():
            return
        thread = QThread()
        runner = GeminiTestRunner(api_key)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.finished.connect(self._on_gemini_test_done)
        runner.finished.connect(thread.quit)
        runner.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._gemini_test_thread = thread
        self._gemini_test_runner = runner
        thread.start()

    @Slot(bool, str)
    def _on_gemini_test_done(self, ok: bool, message: str) -> None:
        if ok:
            log.info("Gemini API: %s", message)
        else:
            log.warning("Gemini API недоступен: %s", message)
        self._gemini_test_thread = None
        self._gemini_test_runner = None

    @Slot()
    def _on_system_check(self) -> None:
        if self._sys_check_thread and self._sys_check_thread.isRunning():
            return
        api_key = str(self._settings.value("gemini_api_key", "") or "").strip() or None
        thread = QThread()
        runner = SystemCheckRunner(api_key)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.finished.connect(self._on_system_check_done)
        runner.finished.connect(thread.quit)
        runner.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._sys_check_thread = thread
        self._sys_check_runner = runner
        thread.start()

    @Slot(object)
    def _on_system_check_done(self, report) -> None:
        try:
            lines = []
            for cr in report.items:
                mark = "✓" if cr.ok else ("⚠" if cr.fixable else "✕")
                lines.append(f"{mark} {cr.name}: {cr.message}")
            system_check.print_report(report)
        except Exception as e:  # noqa: BLE001
            log.error("print_report упал: %s", e)
        self._sys_check_thread = None
        self._sys_check_runner = None

    # ── Авто-установка на старте + прогрев TTS ──────────────
    @Slot()
    def _kickoff_auto_install(self) -> None:
        """Запускает фоновую проверку и доустановку безопасного.
        После завершения — пересоздаёт TTS и прогревает кэш фраз.
        """
        if self._auto_install_thread and self._auto_install_thread.isRunning():
            return
        api_key = str(self._settings.value("gemini_api_key", "") or "").strip() or None
        log.info("Старт фоновой автопроверки компонентов…")
        thread = QThread()
        thread.setObjectName("akali-auto-install")
        runner = AutoInstallRunner(api_key)
        runner.moveToThread(thread)
        thread.started.connect(runner.run)
        runner.progress.connect(self._on_auto_install_progress)
        runner.finished.connect(self._on_auto_install_done)
        runner.finished.connect(thread.quit)
        runner.finished.connect(runner.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_auto_install_refs)
        self._auto_install_thread = thread
        self._auto_install_runner = runner
        thread.start()

    @Slot()
    def _clear_auto_install_refs(self) -> None:
        self._auto_install_thread = None
        self._auto_install_runner = None

    @Slot(str)
    def _on_auto_install_progress(self, message: str) -> None:
        try:
            self._window.home_page.show_toast(message[:80])
        except Exception:  # noqa: BLE001
            pass

    @Slot(object)
    def _on_auto_install_done(self, report) -> None:
        try:
            n_missing = sum(1 for it in report.items if not it.ok)
            log.info("Автопроверка готова: %d/%d компонентов в порядке",
                     len(report.items) - n_missing, len(report.items))
        except Exception:  # noqa: BLE001
            pass
        # ВАЖНО: пересоздание TTS и прогрев откладываем на следующий event-loop
        # tick через QTimer.singleShot(0). Это (а) даёт Qt-у завершить уборку
        # QThread/QObject без гонок, (б) изолирует возможные сегфолты в
        # onnxruntime/piper от текущего слота.
        QTimer.singleShot(0, self._post_auto_install_refresh)

    @Slot()
    def _post_auto_install_refresh(self) -> None:
        """Пост-этап после auto-install. Запускается в чистом тике event-loop,
        вне контекста worker-thread-finished слота. Каждый шаг изолирован
        в try/except, чтобы один баг не валил процесс."""
        # Если piper или espeak только что появились — пересоздать TTS.
        try:
            from .core.tts import TextToSpeech as _TTS  # noqa: WPS433
            prev_engine = self._tts.engine_name
            tts_enabled = str(self._settings.value("tts_enabled", "true")).lower() in ("1", "true", "yes")
            tts_voice = str(self._settings.value("tts_voice", "auto") or "auto")
            tts_piper_voice = str(self._settings.value(
                "tts_piper_voice", system_check.PIPER_DEFAULT_VOICE,
            ) or system_check.PIPER_DEFAULT_VOICE)
            new_tts = _TTS(
                enabled=tts_enabled, prefer=tts_voice,
                piper_voice=tts_piper_voice,
            )
            old_tts = self._tts
            self._tts = new_tts
            try:
                old_tts.shutdown()
            except Exception:  # noqa: BLE001
                pass
            if new_tts.engine_name != prev_engine:
                log.info("TTS: движок обновился %r → %r",
                         prev_engine, new_tts.engine_name)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось пересоздать TTS: %s", e)

        # Прогреваем кэш частых фраз (фон, без блокировки)
        try:
            if self._tts.is_enabled():
                self._tts.prewarm(blocking=False)
            else:
                log.debug("TTS prewarm пропущен: движок не активен")
        except Exception as e:  # noqa: BLE001
            log.warning("TTS prewarm не запущен: %s", e)

    # ── Click-to-run из CommandsPage ────────────────────────
    @Slot(str)
    def _on_run_command(self, cmd: str) -> None:
        # Safety filter тоже здесь
        verdict = safety.inspect(cmd)
        if not verdict.safe:
            safety.report("ui-click", cmd, verdict)
            return
        try:
            result = self._core.execute(cmd)
            self._window.on_command_executed(cmd, result)
        except Exception as e:  # noqa: BLE001
            log.error("Ошибка запуска %r: %s", cmd, e)
        short = cmd[:40] + ("…" if len(cmd) > 40 else "")
        self._window.home_page.show_toast(f"▶ {short}")

    # ── Голосовые отклики через TTS ─────────────────────────
    @Slot(str, str, float, str)
    def _on_command_matched_voice(self, spoken: str, cmd: str, conf: float, method: str) -> None:
        if not self._tts.enabled:
            return
        if method == "power":
            self._tts.say("Выполняю команду питания")
        elif method == "cache":
            self._tts.say("Сделано")
        elif method == "semantic":
            self._tts.say("Понял, выполняю")
        elif method in ("gemini", "ollama", "router"):
            self._tts.say("Запускаю")

    @Slot(str, float)
    def _on_no_match_voice(self, spoken: str, max_conf: float) -> None:
        if self._tts.enabled:
            self._tts.say("Не понял команду")


# ============================================================
# main()
# ============================================================
def _apply_stylesheet(app: QApplication) -> None:
    if paths.APP_STYLESHEET.exists():
        try:
            app.setStyleSheet(paths.APP_STYLESHEET.read_text(encoding="utf-8"))
        except OSError as e:
            log.warning("Не удалось загрузить таблицу стилей: %s", e)


def _qt_message_handler(mode: QtMsgType, _ctx, message: str) -> None:
    """Перенаправляет Qt-сообщения (warnings, fatals) в наш logger.
    Без этого Qt пишет напрямую в stderr, миксуя со stdout-логами."""
    qt_log = logging.getLogger("qt")
    if mode == QtMsgType.QtFatalMsg:
        qt_log.error("Qt FATAL: %s", message)
    elif mode == QtMsgType.QtCriticalMsg:
        qt_log.error("Qt CRITICAL: %s", message)
    elif mode == QtMsgType.QtWarningMsg:
        qt_log.warning("Qt: %s", message)
    elif mode == QtMsgType.QtInfoMsg:
        qt_log.info("Qt: %s", message)
    else:
        qt_log.debug("Qt: %s", message)


def main() -> int:
    log_setup.setup()
    log_setup.banner(AKALI_VERSION)
    qInstallMessageHandler(_qt_message_handler)

    try:
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
    except Exception as e:  # noqa: BLE001
        log.error("Akali упал с исключением: %s", e, exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
