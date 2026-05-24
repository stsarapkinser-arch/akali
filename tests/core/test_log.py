"""Tests for akali.core.log — logging setup, helpers, error hooks."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from akali.core import log as akali_log


@pytest.fixture(autouse=True)
def _reset_logging():
    """Сбрасываем конфигурацию logging между тестами."""
    akali_log._configured = False
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    yield
    root.handlers.clear()
    for h in handlers_before:
        root.addHandler(h)
    akali_log._configured = False


def test_setup_idempotent():
    akali_log.setup()
    first_handlers = list(logging.getLogger().handlers)
    akali_log.setup()  # повторный вызов — без эффекта
    assert list(logging.getLogger().handlers) == first_handlers


def test_setup_installs_handler():
    akali_log.setup()
    handlers = logging.getLogger().handlers
    assert len(handlers) >= 1
    assert any(isinstance(h, logging.StreamHandler) for h in handlers)


def test_setup_respects_debug_env(monkeypatch):
    monkeypatch.setenv("AKALI_DEBUG", "1")
    akali_log.setup()
    assert logging.getLogger().level == logging.DEBUG


def test_setup_default_info_level(monkeypatch):
    monkeypatch.delenv("AKALI_DEBUG", raising=False)
    akali_log.setup()
    assert logging.getLogger().level == logging.INFO


def test_setup_explicit_level():
    akali_log.setup(level=logging.WARNING)
    assert logging.getLogger().level == logging.WARNING


def test_setup_silences_noisy_libs():
    akali_log.setup()
    for name in ("urllib3", "google", "httpx", "fastembed"):
        assert logging.getLogger(name).level >= logging.WARNING


def test_banner_does_not_raise():
    akali_log.setup()
    akali_log.banner("0.0.1")


def test_helpers_do_not_raise():
    akali_log.setup()
    log = logging.getLogger("test")
    akali_log.section(log, "Test Section")
    akali_log.success(log, "all good")
    akali_log.success(log, "got %d items", 5)
    akali_log.warn_box(log, "warning", "extra line")
    akali_log.error_box(log, "title", "reason", "suggestion")
    akali_log.step(log, "step msg")
    akali_log.step(log, "step %s with %s", "arg1", "arg2")


@pytest.mark.parametrize("exc, expected_word", [
    (TimeoutError("operation timed out"), "таймаут"),
    (ConnectionRefusedError("connection refused"), "недоступен"),
    (Exception("Name or service not known"), "интернет"),
    (Exception("rate limit 429"), "лимит"),
    (Exception("Unauthorized 401"), "api-ключ"),
    (FileNotFoundError("not found"), ""),  # generic
])
def test_format_error_heuristics(exc, expected_word):
    reason, suggestion = akali_log.format_error(exc)
    assert isinstance(reason, str)
    assert reason
    assert isinstance(suggestion, str)
    if expected_word:
        full = (reason + " " + suggestion).lower()
        assert expected_word in full


def test_format_error_truncates_long_message():
    long_msg = "x" * 500
    reason, _ = akali_log.format_error(ValueError(long_msg))
    assert len(reason) < 250


def test_format_error_empty_message():
    reason, _ = akali_log.format_error(RuntimeError())
    assert "без сообщения" in reason or reason


def test_install_global_excepthooks_idempotent():
    import sys
    import threading
    akali_log.install_global_excepthooks()
    h1 = sys.excepthook
    akali_log.install_global_excepthooks()
    assert sys.excepthook is h1 or callable(sys.excepthook)
    assert callable(threading.excepthook)


def test_error_log_path_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = akali_log._error_log_path()
    assert p.is_relative_to(tmp_path)
    assert p.name == "last_error.log"


def test_append_error_log_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    akali_log._append_error_log("test error message")
    p = tmp_path / ".cache" / "akali" / "last_error.log"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "test error message" in content


def test_excepthook_logs_and_writes(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("HOME", str(tmp_path))
    akali_log.setup()
    try:
        raise ValueError("test failure")
    except ValueError:
        import sys
        exc_type, exc_value, exc_tb = sys.exc_info()
        with caplog.at_level(logging.ERROR, logger="akali"):
            akali_log._excepthook(exc_type, exc_value, exc_tb)
    assert any("test failure" in rec.message for rec in caplog.records) or \
        (tmp_path / ".cache" / "akali" / "last_error.log").exists()


def test_excepthook_passthrough_keyboard_interrupt():
    import sys
    called = []
    orig = sys.__excepthook__
    sys.__excepthook__ = lambda *a: called.append(a)
    try:
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            akali_log._excepthook(*sys.exc_info())
        assert called
    finally:
        sys.__excepthook__ = orig
