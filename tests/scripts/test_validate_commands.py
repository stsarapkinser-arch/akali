"""Tests for validate_commands.py — bin-availability checker for commands.txt."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import validate_commands as vc


# ── parse_commands ───────────────────────────────────────────

def test_parse_commands_basic(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text(
        "# comment\n"
        "ls -la -> файлы, список\n"
        "\n"
        "date -> время\n",
        encoding="utf-8",
    )
    items = vc.parse_commands(str(p))
    assert len(items) == 2
    cmds = [c for c, _ in items]
    assert "ls -la" in cmds
    assert "date" in cmds


def test_parse_commands_strips_trailing_ampersand(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("firefox & -> браузер\n", encoding="utf-8")
    items = vc.parse_commands(str(p))
    assert items[0][0] == "firefox"


def test_parse_commands_skips_no_arrow(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text(
        "ls -la -> файлы\n"
        "garbage no arrow\n"
        "date -> время\n",
        encoding="utf-8",
    )
    items = vc.parse_commands(str(p))
    assert len(items) == 2


def test_parse_commands_real_file():
    items = vc.parse_commands(vc.COMMANDS_FILE)
    assert len(items) > 50
    for cmd, triggers in items:
        assert cmd
        assert all(t for t in triggers)


def test_parse_commands_skips_blank_triggers(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("foo -> ,  , bar  \n", encoding="utf-8")
    items = vc.parse_commands(str(p))
    assert items[0][1] == ["bar"]


# ── extract_binaries ─────────────────────────────────────────

@pytest.mark.parametrize("cmd, expected", [
    ("ls -la", ["ls"]),
    ("ls -la | grep foo", ["ls", "grep"]),
    ("cd /tmp; ls", ["cd", "ls"]),
    ("true && echo ok", ["true", "echo"]),
    ("true || false", ["true", "false"]),
    ("/usr/bin/firefox", ["/usr/bin/firefox"]),
    ("FOO=bar ls", ["ls"]),  # env-присваивания пропускаются
    ("FOO=bar BAZ=qux cmd arg", ["cmd"]),
    ("", []),
])
def test_extract_binaries(cmd, expected):
    assert vc.extract_binaries(cmd) == expected


def test_extract_binaries_handles_quotes():
    bins = vc.extract_binaries('echo "hello world"')
    assert bins == ["echo"]


def test_extract_binaries_handles_bad_quotes():
    # Незакрытая кавычка → segment пропускается, не падает
    bins = vc.extract_binaries('echo "unclosed | wc -l')
    # хотя бы не падает
    assert isinstance(bins, list)


def test_extract_binaries_complex_pipeline():
    bins = vc.extract_binaries("ls /tmp | grep .txt | wc -l")
    assert bins == ["ls", "grep", "wc"]


# ── SHELL_BUILTINS ──────────────────────────────────────────

def test_shell_builtins_contains_common():
    assert "echo" in vc.SHELL_BUILTINS
    assert "cd" in vc.SHELL_BUILTINS
    assert "pwd" in vc.SHELL_BUILTINS
    assert "true" in vc.SHELL_BUILTINS


# ── main() через subprocess ──────────────────────────────────

def test_main_runs_with_real_commands_txt(tmp_path):
    """Запускает validate_commands.py — exit 0 или 1, но не падает."""
    proc = subprocess.run(
        [sys.executable, "validate_commands.py", "-q"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode in (0, 1), proc.stderr
    assert "Команд в базе" in proc.stdout


def test_main_verbose_mode():
    proc = subprocess.run(
        [sys.executable, "validate_commands.py"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode in (0, 1)
    assert "НАЙДЕНЫ" in proc.stdout
    assert "ОТСУТСТВУЮТ" in proc.stdout


def test_main_missing_commands_file(tmp_path, monkeypatch):
    """Подменяем COMMANDS_FILE на несуществующий путь — exit 2."""
    bad = tmp_path / "no-such.txt"
    env = dict(__import__("os").environ)
    # Запускаем подмену через установку через -c
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "import validate_commands as v; "
        f"v.COMMANDS_FILE = '{bad}'; "
        "sys.exit(v.main())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 2
    assert "Файл не найден" in proc.stderr


def test_main_empty_commands_file(tmp_path, monkeypatch):
    empty = tmp_path / "empty.txt"
    empty.write_text("# only comment\n", encoding="utf-8")
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "import validate_commands as v; "
        f"v.COMMANDS_FILE = '{empty}'; "
        "sys.exit(v.main())"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 2


# ── Constants ────────────────────────────────────────────────

def test_commands_file_is_absolute_path():
    import os
    assert os.path.isabs(vc.COMMANDS_FILE)


def test_pipe_split_regex():
    # PIPE_SPLIT режет на пайпы, ;, &&, ||
    parts = vc.PIPE_SPLIT.split("a | b ; c && d || e")
    parts = [p.strip() for p in parts if p.strip()]
    assert parts == ["a", "b", "c", "d", "e"]
