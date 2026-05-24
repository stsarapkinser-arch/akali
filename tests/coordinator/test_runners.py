"""Tests for runner classes in akali.app — fire-and-emit QThread workers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from akali import app as akali_app
from akali.core import system_check, updater


class SignalSpy:
    """Минимальный QSignalSpy — собирает все эмиссии в список tuples."""
    def __init__(self, signal):
        self.calls: list[tuple] = []
        signal.connect(lambda *args: self.calls.append(args))


pytestmark = pytest.mark.qt


# ── CheckRunner ────────────────────────────────────────────────

def test_check_runner_emits_version_info(qapp, mocker, tmp_path):
    fake_info = updater.VersionInfo(current_sha="abc", branch="main")
    mocker.patch.object(akali_app, "get_version_info", return_value=fake_info)
    runner = akali_app.CheckRunner(str(tmp_path))
    spy = SignalSpy(runner.finished)
    runner.run()
    assert len(spy.calls) == 1
    assert spy.calls[0][0] is fake_info


def test_check_runner_handles_exception(qapp, mocker, tmp_path):
    mocker.patch.object(akali_app, "get_version_info",
                        side_effect=RuntimeError("boom"))
    runner = akali_app.CheckRunner(str(tmp_path))
    spy = SignalSpy(runner.finished)
    runner.run()
    assert len(spy.calls) == 1
    result = spy.calls[0][0]
    assert isinstance(result, updater.VersionInfo)
    assert "boom" in result.error


# ── UpdateRunner ───────────────────────────────────────────────

def test_update_runner_force_true(qapp, mocker, tmp_path):
    fake_result = updater.UpdateResult(ok=True)
    mock_pull = mocker.patch.object(akali_app, "git_pull",
                                    return_value=fake_result)
    runner = akali_app.UpdateRunner(str(tmp_path), force=True)
    spy = SignalSpy(runner.finished)
    runner.run()
    assert len(spy.calls) == 1
    assert spy.calls[0][0] is fake_result
    mock_pull.assert_called_once()
    # force передан
    call = mock_pull.call_args
    assert call.kwargs.get("force") is True or (
        len(call.args) >= 2 and True in call.args
    )


def test_update_runner_force_false(qapp, mocker, tmp_path):
    fake_result = updater.UpdateResult(ok=False, error="dirty")
    mocker.patch.object(akali_app, "git_pull", return_value=fake_result)
    runner = akali_app.UpdateRunner(str(tmp_path), force=False)
    spy = SignalSpy(runner.finished)
    runner.run()
    assert spy.calls[0][0] is fake_result


def test_update_runner_handles_exception(qapp, mocker, tmp_path):
    mocker.patch.object(akali_app, "git_pull",
                        side_effect=RuntimeError("boom"))
    runner = akali_app.UpdateRunner(str(tmp_path), force=False)
    spy = SignalSpy(runner.finished)
    runner.run()
    assert len(spy.calls) == 1
    res = spy.calls[0][0]
    assert isinstance(res, updater.UpdateResult)
    assert not res.ok
    assert "boom" in res.error


# ── SystemCheckRunner ──────────────────────────────────────────

def test_system_check_runner_emits_report(qapp, mocker):
    fake_report = system_check.SystemReport(items=[
        system_check.CheckResult("pkg", True, "ok"),
    ])
    mocker.patch.object(system_check, "run_all_checks", return_value=fake_report)
    runner = akali_app.SystemCheckRunner(gemini_key="fake")
    spy = SignalSpy(runner.finished)
    runner.run()
    assert spy.calls[0][0] is fake_report


def test_system_check_runner_handles_exception(qapp, mocker):
    mocker.patch.object(system_check, "run_all_checks",
                        side_effect=RuntimeError("boom"))
    runner = akali_app.SystemCheckRunner(gemini_key=None)
    spy = SignalSpy(runner.finished)
    runner.run()
    assert len(spy.calls) == 1
    report = spy.calls[0][0]
    assert isinstance(report, system_check.SystemReport)
    assert any("boom" in it.message for it in report.items)


# ── AutoInstallRunner ──────────────────────────────────────────

def test_auto_install_runner_emits_report(qapp, mocker):
    fake_report = system_check.SystemReport(items=[
        system_check.CheckResult("x", True),
    ])
    mocker.patch.object(system_check, "auto_install_safe",
                        return_value=fake_report)
    runner = akali_app.AutoInstallRunner(gemini_key=None)
    spy_progress = SignalSpy(runner.progress)
    spy_finished = SignalSpy(runner.finished)
    runner.run()
    assert spy_finished.calls[0][0] is fake_report


def test_auto_install_runner_progress_forwarded(qapp, mocker):
    """Прогресс из auto_install_safe должен пробрасываться через runner."""
    captured_progress_fn = []
    def fake_install(api_key, progress):
        captured_progress_fn.append(progress)
        progress("step 1")
        progress("step 2")
        return system_check.SystemReport()
    mocker.patch.object(system_check, "auto_install_safe", side_effect=fake_install)
    runner = akali_app.AutoInstallRunner(gemini_key=None)
    spy = SignalSpy(runner.progress)
    runner.run()
    assert len(spy.calls) == 2
    assert spy.calls[0][0] == "step 1"


def test_auto_install_runner_handles_exception(qapp, mocker):
    mocker.patch.object(system_check, "auto_install_safe",
                        side_effect=RuntimeError("boom"))
    runner = akali_app.AutoInstallRunner(gemini_key=None)
    spy = SignalSpy(runner.finished)
    runner.run()
    assert len(spy.calls) == 1
    report = spy.calls[0][0]
    assert isinstance(report, system_check.SystemReport)
    assert any("boom" in it.message for it in report.items)


# ── GeminiTestRunner ───────────────────────────────────────────

def test_gemini_test_runner_success(qapp, mocker):
    mocker.patch.object(system_check, "check_gemini",
                        return_value=system_check.CheckResult(
                            "Gemini API", True, "ключ принят"))
    runner = akali_app.GeminiTestRunner("fake-key")
    spy = SignalSpy(runner.finished)
    runner.run()
    assert spy.calls[0] == (True, "ключ принят")


def test_gemini_test_runner_failure(qapp, mocker):
    mocker.patch.object(system_check, "check_gemini",
                        return_value=system_check.CheckResult(
                            "Gemini API", False, "ключ отвергнут"))
    runner = akali_app.GeminiTestRunner("bad")
    spy = SignalSpy(runner.finished)
    runner.run()
    assert spy.calls[0] == (False, "ключ отвергнут")


def test_gemini_test_runner_handles_exception(qapp, mocker):
    mocker.patch.object(system_check, "check_gemini",
                        side_effect=RuntimeError("crash"))
    runner = akali_app.GeminiTestRunner("x")
    spy = SignalSpy(runner.finished)
    runner.run()
    assert len(spy.calls) == 1
    ok, msg = spy.calls[0]
    assert ok is False
    assert "crash" in msg
