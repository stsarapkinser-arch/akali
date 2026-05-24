"""Tests for akali.core.executor — bash command execution with safety."""
from __future__ import annotations

import os
import subprocess
import time

import pytest

from akali.core import executor


def test_simple_echo():
    r = executor.execute("echo hello")
    assert r.ok
    assert r.stdout == "hello"
    assert r.returncode == 0
    assert not r.is_background


def test_nonexistent_command():
    r = executor.execute("nonexistent_binary_xyz_123")
    assert not r.ok
    assert "не найдена" in r.error.lower() or r.error


def test_failed_command():
    r = executor.execute("ls /this/path/does/not/exist/anywhere")
    assert not r.ok
    assert r.returncode != 0


def test_command_result_ok_property():
    r = executor.CommandResult(cmd="x", returncode=0)
    assert r.ok
    r = executor.CommandResult(cmd="x", returncode=1)
    assert not r.ok
    r = executor.CommandResult(cmd="x", returncode=0, error="boom")
    assert not r.ok
    r = executor.CommandResult(cmd="x", returncode=0, timed_out=True)
    assert not r.ok


@pytest.mark.parametrize("cmd", [
    "echo a; rm -rf /",
    "echo a | tee /tmp/x",
    "echo a > /tmp/x",
    "echo a < /tmp/x",
    "echo `whoami`",
    "echo $(id)",
    "true && false",
    "true || false",
    "ls $(pwd)",
    "echo {a,b}",
    "echo [abc]",
    "echo \\$x",
])
def test_dangerous_chars_blocked(cmd):
    r = executor.execute(cmd)
    assert not r.ok
    assert "недопустимые символы" in r.error.lower() or "injection" in r.error.lower()


def test_background_command_does_not_block():
    start = time.time()
    r = executor.execute("sleep 0.5 &")
    elapsed = time.time() - start
    assert r.is_background
    assert elapsed < 0.3, f"background blocked main thread for {elapsed}s"


def test_timeout():
    r = executor.execute("sleep 2", timeout=0.2)
    assert r.timed_out
    assert not r.ok
    assert "таймаут" in r.error.lower()


def test_env_is_passed():
    os.environ["AKALI_TEST_VAR"] = "echoed-value"
    try:
        r = executor.execute("printenv AKALI_TEST_VAR")
        assert r.stdout == "echoed-value"
    finally:
        del os.environ["AKALI_TEST_VAR"]


def test_dbus_session_bus_passed(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/test/dbus")
    r = executor.execute("printenv DBUS_SESSION_BUS_ADDRESS")
    assert r.stdout == "unix:path=/test/dbus"


def test_empty_command_after_strip():
    r = executor.execute("   ")
    assert not r.ok
    assert "пустая" in r.error.lower() or r.error


def test_unparseable_quotes():
    r = executor.execute('echo "unclosed')
    assert not r.ok
    assert "распарсить" in r.error.lower() or r.error


def test_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "marker.txt").write_text("home_ok")
    r = executor.execute("cat ~/marker.txt")
    assert r.ok
    assert r.stdout == "home_ok"


def test_parse_command_returns_none_for_bad_quotes():
    assert executor._parse_command('"unclosed') is None


def test_parse_command_expands_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    args = executor._parse_command("ls ~/foo")
    assert args == ["ls", str(tmp_path / "foo")]


def test_background_strips_trailing_ampersand():
    # Запустим что-то невредное в фон и подождём чтобы не висело
    r = executor.execute("true &")
    assert r.is_background
    assert r.cmd == "true &"
    time.sleep(0.05)  # дать time для очистки zombie
