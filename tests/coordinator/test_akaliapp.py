"""Tests for AkaliApp coordinator — init, signal wiring, lifecycle."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from akali import app as akali_app
from akali.core import safety, system_check, updater


pytestmark = pytest.mark.qt


@pytest.fixture
def isolated_app(qapp, mocker, tmp_path, vosk_model_dir):
    """Создаёт AkaliApp с mock'нутыми тяжёлыми зависимостями.

    Замокано:
      • MainWindow, TrayController, TextToSpeech (UI-объекты не создаются)
      • AssistantCore.init_router (иначе грузится FastEmbed на каждый тест → OOM)
      • QSettings (in-memory dict)
      • QTimer.singleShot (запрещает автостарт прослушки и автоинсталл)
    """
    mock_window = MagicMock()
    mock_window.home_page = MagicMock()
    mock_window.isMinimized.return_value = False
    mock_tray = MagicMock()
    mock_tts = MagicMock()
    mock_tts.engine_name = "espeak-ng"
    mock_tts.enabled = True
    mock_tts.is_enabled.return_value = True

    mocker.patch.object(akali_app, "MainWindow", return_value=mock_window)
    mocker.patch.object(akali_app, "TrayController", return_value=mock_tray)
    mocker.patch.object(akali_app, "TextToSpeech", return_value=mock_tts)

    # Главное: убираем FastEmbed-инициализацию из _initial_load
    mocker.patch("akali.core.backend.AssistantCore.init_router")

    # Изолируем QSettings
    settings_data = {
        "repo_dir": str(tmp_path),
        "vosk_model_dir": str(vosk_model_dir),
        "tts_enabled": "true",
        "tts_voice": "espeak",
        "tts_piper_voice": system_check.PIPER_DEFAULT_VOICE,
        "audio_device": None,
        "wake_threshold": 0.75,
        "wake_words": "акали,ассистент,компьютер",
        "reindex_triggers": "переиндексируй",
        "gemini_api_key": "",
        "llm_mode": "auto",
        "auto_update_minutes": 0,
    }
    settings = MagicMock()
    settings.value = lambda key, default=None: settings_data.get(key, default)
    settings.sync = MagicMock()
    mocker.patch.object(akali_app, "QSettings", return_value=settings)

    # Запрет автостарта (микрофон/auto-install)
    mocker.patch.object(akali_app.QTimer, "singleShot")

    app = akali_app.AkaliApp(qapp)
    yield app, mock_window, mock_tray, mock_tts, settings_data

    try:
        app._shutdown()
    except Exception:
        pass


# ── Init ───────────────────────────────────────────────────────

def test_akaliapp_initializes(isolated_app):
    app, window, tray, tts, _ = isolated_app
    assert app._core is not None
    assert app._window is window
    assert app._tray is tray
    assert app._tts is tts
    assert app._audio_thread is not None
    assert app._audio_worker is not None


def test_akaliapp_loads_commands(isolated_app):
    app, _, _, _, _ = isolated_app
    # _initial_load вызван в __init__
    assert len(app._core.commands_db) > 0


def test_akaliapp_registers_safety_notifier(isolated_app, mocker):
    # safety._notifier должен быть проставлен
    assert safety._notifier is not None


def test_akaliapp_window_minimize_to_tray_called(isolated_app):
    _, window, _, _, _ = isolated_app
    window.set_minimize_to_tray.assert_called_once_with(True)


# ── start_listening / stop_listening ───────────────────────────

def test_start_listening_starts_thread(isolated_app):
    app, _, tray, _, _ = isolated_app
    app.start_listening()
    assert app._is_listening
    tray.set_listening.assert_called_with(True)
    # cleanup — попросим остановиться
    app._audio_worker.request_stop()
    app._audio_thread.quit()
    app._audio_thread.wait(2000)


def test_start_listening_idempotent(isolated_app):
    app, _, tray, _, _ = isolated_app
    app._is_listening = True  # форсируем «уже слушает»
    tray.set_listening.reset_mock()
    app.start_listening()
    # Не должен заново start_listening
    tray.set_listening.assert_not_called()


def test_stop_listening_requests_stop(isolated_app):
    app, _, _, _, _ = isolated_app
    app._is_listening = True
    mock_worker = MagicMock()
    app._audio_worker = mock_worker
    app.stop_listening()
    mock_worker.request_stop.assert_called_once()


def test_stop_listening_noop_when_not_listening(isolated_app):
    app, _, _, _, _ = isolated_app
    app._is_listening = False
    mock_worker = MagicMock()
    app._audio_worker = mock_worker
    app.stop_listening()
    mock_worker.request_stop.assert_not_called()


# ── _on_safety_violation ───────────────────────────────────────

def test_on_safety_violation_shows_toast(isolated_app):
    app, window, _, _, _ = isolated_app
    app._on_safety_violation("gemini", "rm -rf /", "rm root")
    window.home_page.show_toast.assert_called_once()
    args, _ = window.home_page.show_toast.call_args
    assert "gemini" in args[0]
    assert "критичная" in args[0] or "блок" in args[0]


def test_on_safety_violation_swallows_toast_error(isolated_app):
    app, window, _, _, _ = isolated_app
    window.home_page.show_toast.side_effect = RuntimeError("boom")
    # не должно крашиться
    app._on_safety_violation("ollama", "rm -rf /", "x")


# ── _on_run_command ────────────────────────────────────────────

def test_on_run_command_blocks_dangerous(isolated_app):
    app, window, _, _, _ = isolated_app
    app._on_run_command("rm -rf /")
    # window.on_command_executed не вызывался для опасной команды
    window.on_command_executed.assert_not_called()


def test_on_run_command_executes_safe(isolated_app):
    app, window, _, _, _ = isolated_app
    app._on_run_command("echo run-test-marker")
    window.on_command_executed.assert_called()
    cmd, result = window.on_command_executed.call_args[0]
    assert cmd == "echo run-test-marker"
    assert result.stdout == "run-test-marker"


def test_on_run_command_shows_toast(isolated_app):
    app, window, _, _, _ = isolated_app
    window.home_page.show_toast.reset_mock()
    app._on_run_command("echo x")
    window.home_page.show_toast.assert_called()


def test_on_run_command_truncates_long_cmd_in_toast(isolated_app):
    app, window, _, _, _ = isolated_app
    long = "echo " + "a" * 100
    app._on_run_command(long)
    toast_arg = window.home_page.show_toast.call_args[0][0]
    assert "…" in toast_arg


# ── _apply_auto_update_setting ────────────────────────────────

def test_apply_auto_update_zero_stops_timer(isolated_app):
    app, _, _, _, _ = isolated_app
    app._auto_update_timer.start(60_000)
    assert app._auto_update_timer.isActive()
    app._apply_auto_update_setting(0)
    assert not app._auto_update_timer.isActive()


def test_apply_auto_update_starts_timer(isolated_app):
    app, _, _, _, _ = isolated_app
    app._apply_auto_update_setting(5)
    assert app._auto_update_timer.isActive()
    assert app._auto_update_timer.interval() == 5 * 60_000
    app._auto_update_timer.stop()


# ── Голосовые отклики ────────────────────────────────────────

def test_on_command_matched_voice_power(isolated_app):
    app, _, _, tts, _ = isolated_app
    app._on_command_matched_voice("выключи", "systemctl poweroff", 1.0, "power")
    tts.say.assert_called_with("Выполняю команду питания")


def test_on_command_matched_voice_cache(isolated_app):
    app, _, _, tts, _ = isolated_app
    app._on_command_matched_voice("x", "ls", 1.0, "cache")
    tts.say.assert_called_with("Сделано")


def test_on_command_matched_voice_semantic(isolated_app):
    app, _, _, tts, _ = isolated_app
    app._on_command_matched_voice("x", "ls", 1.0, "semantic")
    tts.say.assert_called_with("Понял, выполняю")


def test_on_command_matched_voice_router(isolated_app):
    app, _, _, tts, _ = isolated_app
    app._on_command_matched_voice("x", "ls", 1.0, "router")
    tts.say.assert_called_with("Запускаю")


def test_on_command_matched_voice_disabled_skips(isolated_app):
    app, _, _, tts, _ = isolated_app
    tts.enabled = False
    tts.say.reset_mock()
    app._on_command_matched_voice("x", "ls", 1.0, "router")
    tts.say.assert_not_called()


def test_on_no_match_voice(isolated_app):
    app, _, _, tts, _ = isolated_app
    app._on_no_match_voice("foo", 0.0)
    tts.say.assert_called_with("Не понял команду")


def test_on_no_match_voice_disabled(isolated_app):
    app, _, _, tts, _ = isolated_app
    tts.enabled = False
    tts.say.reset_mock()
    app._on_no_match_voice("foo", 0.0)
    tts.say.assert_not_called()


# ── _shutdown ───────────────────────────────────────────────

def test_shutdown_stops_tts(isolated_app):
    app, _, _, tts, _ = isolated_app
    app._shutdown()
    tts.shutdown.assert_called_once()


def test_shutdown_idempotent(isolated_app):
    app, _, _, _, _ = isolated_app
    app._shutdown()
    app._shutdown()  # не должно крашиться


def test_shutdown_stops_timers(isolated_app):
    app, _, _, _, _ = isolated_app
    app._auto_update_timer.start(60_000)
    assert app._auto_update_timer.isActive()
    app._shutdown()
    assert not app._auto_update_timer.isActive()


def test_shutdown_isolates_tts_exception(isolated_app):
    app, _, _, tts, _ = isolated_app
    tts.shutdown.side_effect = RuntimeError("boom")
    app._shutdown()  # не должно крашиться


# ── _on_auto_update_tick ───────────────────────────────────────

def test_auto_update_tick_calls_run_check(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mock = mocker.patch.object(app, "run_check")
    app._on_auto_update_tick()
    mock.assert_called_once()


def test_auto_update_tick_skips_if_check_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    app._check_thread = MagicMock()
    app._check_thread.isRunning.return_value = True
    mock = mocker.patch.object(app, "run_check")
    app._on_auto_update_tick()
    mock.assert_not_called()


def test_auto_update_tick_skips_if_update_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    app._update_thread = MagicMock()
    app._update_thread.isRunning.return_value = True
    mock = mocker.patch.object(app, "run_check")
    app._on_auto_update_tick()
    mock.assert_not_called()


# ── _on_check_finished ─────────────────────────────────────────

def test_on_check_finished_no_updates(isolated_app):
    app, _, _, _, _ = isolated_app
    info = updater.VersionInfo(current_sha="abc", branch="main", has_updates=False)
    app._on_check_finished(info)
    assert app._check_thread is None


def test_on_check_finished_with_updates(isolated_app):
    app, _, _, _, _ = isolated_app
    info = updater.VersionInfo(has_updates=True, commits_behind=3)
    app._on_check_finished(info)
    assert app._check_thread is None


def test_on_check_finished_with_error(isolated_app):
    app, _, _, _, _ = isolated_app
    info = updater.VersionInfo(error="network")
    app._on_check_finished(info)
    assert app._check_thread is None


# ── _on_update_finished ───────────────────────────────────────

def test_on_update_finished_ok(isolated_app):
    app, window, _, _, _ = isolated_app
    result = updater.UpdateResult(ok=True, already_up_to_date=True)
    app._on_update_finished(result)
    window.on_update_result.assert_called_once_with(result)


def test_on_update_finished_handles_window_exception(isolated_app):
    app, window, _, _, _ = isolated_app
    window.on_update_result.side_effect = RuntimeError("boom")
    result = updater.UpdateResult(ok=True)
    app._on_update_finished(result)  # не должно крашиться


def test_on_update_finished_failed(isolated_app):
    app, window, _, _, _ = isolated_app
    result = updater.UpdateResult(ok=False, error="git failed")
    app._on_update_finished(result)
    window.on_update_result.assert_called_once_with(result)


# ── _restart_app ───────────────────────────────────────────────

def test_restart_app_calls_popen(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mock_popen = mocker.patch.object(akali_app.subprocess, "Popen")
    mock_quit = mocker.patch.object(app._qapp, "quit")
    app._restart_app()
    mock_popen.assert_called_once()
    # start_new_session=True
    assert mock_popen.call_args.kwargs.get("start_new_session") is True
    mock_quit.assert_called_once()


def test_restart_app_falls_back_to_execv(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mocker.patch.object(akali_app.subprocess, "Popen",
                        side_effect=OSError("popen broken"))
    mock_execv = mocker.patch.object(akali_app.os, "execv")
    app._restart_app()
    mock_execv.assert_called_once()


# ── _on_device_info ───────────────────────────────────────────

def test_on_device_info_logs(isolated_app, caplog):
    app, _, _, _, _ = isolated_app
    import logging
    with caplog.at_level(logging.INFO):
        app._on_device_info("USB Mic @ 48000Hz")
    assert any("USB Mic" in r.message for r in caplog.records)


# ── _on_status_for_tray ───────────────────────────────────────

def test_on_status_for_tray_updates_tooltip(isolated_app):
    app, _, tray, _, _ = isolated_app
    app._on_status_for_tray("listening")
    tray.tray.setToolTip.assert_called()
    arg = tray.tray.setToolTip.call_args[0][0]
    assert "Слушаю" in arg


def test_on_status_for_tray_unknown_state(isolated_app):
    app, _, tray, _, _ = isolated_app
    app._on_status_for_tray("weird-state")
    arg = tray.tray.setToolTip.call_args[0][0]
    assert "weird-state" in arg


# ── show_window / quit ─────────────────────────────────────────

def test_show_window_calls_show(isolated_app):
    app, window, _, _, _ = isolated_app
    app.show_window()
    window.show.assert_called()
    window.raise_.assert_called()
    window.activateWindow.assert_called()


def test_show_window_handles_minimized(isolated_app):
    app, window, _, _, _ = isolated_app
    window.isMinimized.return_value = True
    app.show_window()
    window.showNormal.assert_called()


def test_quit_calls_qapp_quit(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mock_quit = mocker.patch.object(app._qapp, "quit")
    app.quit()
    mock_quit.assert_called_once()


# ── _reload_core ───────────────────────────────────────────────

def test_reload_core_calls_reload(isolated_app, mocker):
    app, window, _, _, _ = isolated_app
    mock = mocker.patch.object(app._core, "reload",
                                return_value=MagicMock(commands_total=5))
    app._reload_core()
    mock.assert_called_once()
    window.reload_complete.assert_called()


def test_reload_core_handles_exception(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mocker.patch.object(app._core, "reload", side_effect=RuntimeError("boom"))
    app._reload_core()  # не должно крашиться


# ── run_check / run_update — реальная QThread жизнь ─────────────

def test_run_check_starts_thread(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    fake_info = updater.VersionInfo(branch="main", current_sha="abc")
    mocker.patch.object(akali_app, "get_version_info", return_value=fake_info)
    app.run_check()
    assert app._check_thread is not None
    # ждём финиша
    app._check_thread.wait(2000)


def test_run_check_skips_if_already_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    app._check_thread = fake_thread
    mock_get = mocker.patch.object(akali_app, "get_version_info")
    app.run_check()
    # Не должен повторно вызвать get_version_info
    mock_get.assert_not_called()


def test_run_check_skips_if_update_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    app._update_thread = MagicMock()
    app._update_thread.isRunning.return_value = True
    mock_get = mocker.patch.object(akali_app, "get_version_info")
    app.run_check()
    mock_get.assert_not_called()


def test_run_update_skips_if_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    app._update_thread = fake_thread
    mock_pull = mocker.patch.object(akali_app, "git_pull")
    app.run_update(force=False)
    mock_pull.assert_not_called()


def test_run_update_skips_if_check_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    app._check_thread = MagicMock()
    app._check_thread.isRunning.return_value = True
    mock_pull = mocker.patch.object(akali_app, "git_pull")
    app.run_update(force=False)
    mock_pull.assert_not_called()


def test_run_update_starts_thread(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    fake_result = updater.UpdateResult(ok=True, already_up_to_date=True)
    mocker.patch.object(akali_app, "git_pull", return_value=fake_result)
    app.run_update(force=False)
    assert app._update_thread is not None
    app._update_thread.wait(2000)


def test_on_update_finished_triggers_restart_if_needed(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mock_restart = mocker.patch.object(app, "_restart_app")
    # singleShot замокан в фикстуре, поэтому _restart_app напрямую не вызовется,
    # но мы проверим, что singleShot был дёрнут с задержкой
    mock_single = mocker.patch.object(akali_app.QTimer, "singleShot")
    result = updater.UpdateResult(ok=True, needs_restart=True)
    app._on_update_finished(result)
    mock_single.assert_called()


# ── Gemini test / system check ─────────────────────────────────

def test_on_gemini_test_starts_thread(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mocker.patch.object(system_check, "check_gemini",
                       return_value=system_check.CheckResult("Gemini API", True, "ok"))
    app._on_gemini_test("fake-key")
    assert app._gemini_test_thread is not None
    app._gemini_test_thread.wait(2000)


def test_on_gemini_test_skips_when_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    app._gemini_test_thread = MagicMock()
    app._gemini_test_thread.isRunning.return_value = True
    mock = mocker.patch.object(system_check, "check_gemini")
    app._on_gemini_test("k")
    mock.assert_not_called()


def test_on_gemini_test_done_logs_ok(isolated_app, caplog):
    app, _, _, _, _ = isolated_app
    import logging
    with caplog.at_level(logging.INFO):
        app._on_gemini_test_done(True, "ok")
    assert any("Gemini API: ok" in r.message for r in caplog.records)


def test_on_gemini_test_done_logs_failure(isolated_app, caplog):
    app, _, _, _, _ = isolated_app
    import logging
    with caplog.at_level(logging.WARNING):
        app._on_gemini_test_done(False, "rejected")
    assert any("rejected" in r.message for r in caplog.records)


def test_on_system_check_starts_thread(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mocker.patch.object(system_check, "run_all_checks",
                       return_value=system_check.SystemReport())
    app._on_system_check()
    assert app._sys_check_thread is not None
    app._sys_check_thread.wait(2000)


def test_on_system_check_skips_when_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    app._sys_check_thread = MagicMock()
    app._sys_check_thread.isRunning.return_value = True
    mock = mocker.patch.object(system_check, "run_all_checks")
    app._on_system_check()
    mock.assert_not_called()


def test_on_system_check_done_clears_refs(isolated_app):
    app, _, _, _, _ = isolated_app
    report = system_check.SystemReport(items=[
        system_check.CheckResult("a", True, "ok"),
        system_check.CheckResult("b", False, "missing", fixable=True),
    ])
    app._on_system_check_done(report)
    assert app._sys_check_thread is None


# ── _kickoff_auto_install ──────────────────────────────────────

def test_kickoff_auto_install_starts_thread(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mocker.patch.object(system_check, "auto_install_safe",
                       return_value=system_check.SystemReport())
    app._kickoff_auto_install()
    assert app._auto_install_thread is not None
    app._auto_install_thread.wait(2000)


def test_kickoff_auto_install_skips_when_running(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    app._auto_install_thread = MagicMock()
    app._auto_install_thread.isRunning.return_value = True
    mock = mocker.patch.object(system_check, "auto_install_safe")
    app._kickoff_auto_install()
    mock.assert_not_called()


def test_on_auto_install_progress_shows_toast(isolated_app):
    app, window, _, _, _ = isolated_app
    window.home_page.show_toast.reset_mock()
    app._on_auto_install_progress("installing piper-tts")
    window.home_page.show_toast.assert_called_once()


def test_on_auto_install_done_schedules_refresh(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    mock_single = mocker.patch.object(akali_app.QTimer, "singleShot")
    report = system_check.SystemReport()
    app._on_auto_install_done(report)
    mock_single.assert_called()


def test_post_auto_install_refresh_swaps_tts(isolated_app, mocker):
    app, _, _, _, _ = isolated_app
    old_tts = app._tts
    new_tts = MagicMock()
    new_tts.engine_name = "piper"
    new_tts.is_enabled.return_value = True
    mocker.patch("akali.core.tts.TextToSpeech", return_value=new_tts)
    app._post_auto_install_refresh()
    assert app._tts is new_tts
    old_tts.shutdown.assert_called_once()


# ── TTS settings ───────────────────────────────────────────────

def test_on_tts_settings_changed(isolated_app):
    app, _, _, tts, _ = isolated_app
    app._on_tts_settings_changed(True, "espeak", "")
    tts.set_enabled.assert_called_with(True)
    tts.set_voice.assert_called_with("espeak", piper_voice=None)


# ── Module-level helpers ─────────────────────────────────────

def test_apply_stylesheet_loads_qss(qapp, mocker, tmp_path):
    qss = tmp_path / "app.qss"
    qss.write_text("QMainWindow { background: black; }", encoding="utf-8")
    mocker.patch.object(akali_app.paths, "APP_STYLESHEET", qss)
    mock_app = MagicMock()
    akali_app._apply_stylesheet(mock_app)
    mock_app.setStyleSheet.assert_called_once()


def test_apply_stylesheet_missing_file(mocker, tmp_path):
    mocker.patch.object(akali_app.paths, "APP_STYLESHEET",
                        tmp_path / "missing.qss")
    mock_app = MagicMock()
    akali_app._apply_stylesheet(mock_app)  # не должно крашиться
    mock_app.setStyleSheet.assert_not_called()


def test_qt_message_handler_logs(caplog):
    from PySide6.QtCore import QtMsgType
    import logging
    with caplog.at_level(logging.WARNING, logger="qt"):
        akali_app._qt_message_handler(QtMsgType.QtWarningMsg, None, "test warning")
    assert any("test warning" in r.message for r in caplog.records)


def test_qt_message_handler_fatal(caplog):
    from PySide6.QtCore import QtMsgType
    import logging
    with caplog.at_level(logging.ERROR, logger="qt"):
        akali_app._qt_message_handler(QtMsgType.QtFatalMsg, None, "fatal!")
    assert any("FATAL" in r.message for r in caplog.records)


def test_qt_message_handler_critical(caplog):
    from PySide6.QtCore import QtMsgType
    import logging
    with caplog.at_level(logging.ERROR, logger="qt"):
        akali_app._qt_message_handler(QtMsgType.QtCriticalMsg, None, "crit")
    assert any("CRITICAL" in r.message for r in caplog.records)


def test_qt_message_handler_info_and_debug():
    from PySide6.QtCore import QtMsgType
    # Просто проверка что не крашится
    akali_app._qt_message_handler(QtMsgType.QtInfoMsg, None, "info")
    akali_app._qt_message_handler(QtMsgType.QtDebugMsg, None, "debug")
