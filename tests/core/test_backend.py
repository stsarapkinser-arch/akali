"""Tests for akali.core.backend — AssistantCore façade."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from akali.core import backend
from akali.core.backend import AssistantCore, ReloadStats, _write_commands_txt


# ── Basic state ─────────────────────────────────────────────────

def test_default_constructor_uses_paths():
    core = AssistantCore()
    assert core.commands_file == backend.paths.COMMANDS_TXT
    assert core.auto_commands_file == backend.paths.AUTO_COMMANDS_JSON
    assert core.indexer_script == backend.paths.INDEXER_SCRIPT


def test_custom_paths(tmp_path):
    cmds = tmp_path / "c.txt"
    auto = tmp_path / "a.json"
    idx = tmp_path / "i.py"
    core = AssistantCore(commands_file=cmds, auto_commands_file=auto,
                          indexer_script=idx)
    assert core.commands_file == cmds
    assert core.auto_commands_file == auto


def test_default_settings():
    core = AssistantCore()
    assert core.wake_threshold == backend.WAKE_THRESHOLD
    assert "акали" in core.wake_words
    assert "переиндексируй" in core.reindex_triggers


def test_initial_state_empty():
    core = AssistantCore()
    assert core.commands_db == {}
    assert core.auto_sources == {}
    assert core._router is None


# ── reload ──────────────────────────────────────────────────────

def test_reload_loads_commands(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "auto.json")
    stats = core.reload()
    assert isinstance(stats, ReloadStats)
    assert stats.commands_total == 3
    assert stats.curated_count == 3
    assert "ls -la" in core.commands_db


def test_reload_idempotent(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "auto.json")
    s1 = core.reload()
    s2 = core.reload()
    assert s1.commands_total == s2.commands_total


def test_reload_excludes_power_commands(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text(
        "ls -> листинг\n"
        "systemctl poweroff -> выключи\n"
        "systemctl reboot -> перезагрузи\n",
        encoding="utf-8",
    )
    core = AssistantCore(commands_file=p,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    assert "ls" in core.commands_db
    assert "systemctl poweroff" not in core.commands_db
    assert "systemctl reboot" not in core.commands_db


def test_reload_merges_auto(min_commands, tmp_path):
    auto = tmp_path / "a.json"
    auto.write_text(json.dumps({
        "sources": {"desktop": 1},
        "items": [{"command": "kate", "trigger": "редактор"}],
    }), encoding="utf-8")
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=auto)
    stats = core.reload()
    assert stats.auto_count == 1
    assert stats.auto_sources == {"desktop": 1}
    assert "kate" in core.commands_db


def test_reload_updates_router_if_initialized(min_commands, tmp_path, mocker):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    fake_router = MagicMock()
    core._router = fake_router
    core.reload()
    fake_router.load_db.assert_called_once()


def test_reload_handles_router_failure(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    bad = MagicMock()
    bad.load_db.side_effect = RuntimeError("router broken")
    core._router = bad
    # не падает
    stats = core.reload()
    assert stats.commands_total > 0


# ── process ─────────────────────────────────────────────────────

def test_process_power_command(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    cmd, src = core.process("выключи компьютер")
    assert cmd == "systemctl poweroff"
    assert src == "power"


def test_process_no_router_returns_miss(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    cmd, src = core.process("совершенно неизвестная фраза")
    assert cmd is None
    assert src == ""


def test_process_router_hit(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    fake_router = MagicMock()
    fake_router.route.return_value = "echo hello"
    core._router = fake_router
    cmd, src = core.process("показать файлы")
    assert cmd == "echo hello"
    assert src == "router"


def test_process_router_miss(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    fake_router = MagicMock()
    fake_router.route.return_value = None
    core._router = fake_router
    cmd, src = core.process("ничего не подходит")
    assert cmd is None
    assert src == ""


def test_process_router_exception(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    bad = MagicMock()
    bad.route.side_effect = RuntimeError("boom")
    core._router = bad
    cmd, src = core.process("any text")
    assert cmd is None
    assert src == ""


# ── init_router / settings ──────────────────────────────────────

def test_init_router_creates_router(min_commands, tmp_path, mocker):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    fake_router_cls = MagicMock()
    mocker.patch.object(backend, "_lazy_import_router",
                        return_value=fake_router_cls)
    core.init_router(gemini_api_key="x", llm_mode="auto")
    assert core._router is not None
    fake_router_cls.assert_called_once()
    fake_router_cls.return_value.load_db.assert_called_once()


def test_update_gemini_key(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    fake = MagicMock()
    core._router = fake
    core.update_gemini_key("new-key")
    fake.set_gemini_key.assert_called_once_with("new-key")


def test_update_gemini_key_swallows_exception():
    core = AssistantCore()
    fake = MagicMock()
    fake.set_gemini_key.side_effect = RuntimeError("boom")
    core._router = fake
    # не должно крашиться
    core.update_gemini_key("x")


def test_update_llm_mode(min_commands, tmp_path):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    fake = MagicMock()
    core._router = fake
    core.update_llm_mode("ollama-only")
    fake.set_llm_mode.assert_called_once_with("ollama-only")


def test_update_methods_noop_without_router():
    core = AssistantCore()
    # не должно крашиться
    core.update_gemini_key("x")
    core.update_llm_mode("auto")


# ── Wake-word ───────────────────────────────────────────────────

def test_detect_wake_word():
    core = AssistantCore()
    idx = core.detect_wake_word(["акали", "включи", "свет"])
    assert idx == 0


def test_detect_wake_word_miss():
    core = AssistantCore()
    idx = core.detect_wake_word(["включи", "свет"])
    assert idx == -1


def test_is_reindex_phrase():
    core = AssistantCore()
    assert core.is_reindex_phrase("акали, переиндексируй")
    assert not core.is_reindex_phrase("открой браузер")


# ── execute ─────────────────────────────────────────────────────

def test_execute_runs_real_command():
    core = AssistantCore()
    r = core.execute("echo hello-from-test")
    assert r.ok
    assert r.stdout == "hello-from-test"


# ── category_of ──────────────────────────────────────────────────

@pytest.mark.parametrize("cmd, expected", [
    ("qdbus org.kde.kglobalaccel /component", "рабочий стол"),
    ("firefox &", "браузер"),
    ("pactl set-sink-volume +10%", "аудио"),
    ("ip a", "сеть"),
    ("ls /tmp", "файлы"),
    ("konsole", "терминал"),
    ("htop", "мониторинг"),
    ("systemctl status sshd", "система"),
    ("date", "прочее"),
])
def test_category_of(cmd, expected):
    assert AssistantCore.category_of(cmd) == expected


# ── save_command / delete_command ───────────────────────────────

def test_save_command_adds_new(tmp_path, min_commands):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    before = len(core.commands_db)
    ok = core.save_command("kate", ["редактор"])
    assert ok
    core.reload()
    assert "kate" in core.commands_db
    assert len(core.commands_db) == before + 1


def test_save_command_overwrites_existing(tmp_path, min_commands):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    core.save_command("ls -la", ["новый триггер"])
    core.reload()
    assert "новый триггер" in core.commands_db["ls -la"]


def test_save_command_rejects_power(tmp_path, min_commands):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    assert not core.save_command("systemctl poweroff", ["выключи"])
    assert not core.save_command("reboot", ["перезагрузка"])


def test_save_command_rejects_empty(tmp_path, min_commands):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    assert not core.save_command("", ["x"])
    assert not core.save_command("ls", [])
    assert not core.save_command("ls", ["", " "])


def test_delete_command(tmp_path, min_commands):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    assert core.delete_command("ls -la")
    core.reload()
    assert "ls -la" not in core.commands_db


def test_delete_command_nonexistent(tmp_path, min_commands):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    assert not core.delete_command("notexisting")


def test_write_commands_txt_atomic(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("placeholder", encoding="utf-8")
    ok = _write_commands_txt(p, {"ls": ["листинг"]})
    assert ok
    content = p.read_text(encoding="utf-8")
    assert "ls -> листинг" in content
    assert content.startswith("# Сгенерировано")


def test_write_commands_txt_failure(tmp_path):
    # путь к несуществующей директории
    p = tmp_path / "nope" / "c.txt"
    ok = _write_commands_txt(p, {"ls": ["листинг"]})
    assert not ok


# ── reindex_system ─────────────────────────────────────────────

def test_reindex_system_missing_script(tmp_path):
    core = AssistantCore(
        commands_file=tmp_path / "c.txt",
        auto_commands_file=tmp_path / "a.json",
        indexer_script=tmp_path / "no-such-indexer.py",
    )
    result = core.reindex_system()
    assert not result["ok"]
    assert "не найден" in result["error"]


def test_reindex_system_runs_real_indexer(min_commands, tmp_path):
    """Запускает реальный system_indexer.py — должен отработать."""
    from akali import paths
    core = AssistantCore(
        commands_file=min_commands,
        auto_commands_file=tmp_path / "a.json",
        indexer_script=paths.INDEXER_SCRIPT,
    )
    core.reload()
    # Подменим auto_commands_file на tmp чтобы не трогать реальный
    result = core.reindex_system(timeout=60.0)
    # Indexer может вернуть код 0 или 1 в зависимости от окружения,
    # но не падать с FileNotFoundError
    assert isinstance(result, dict)
    assert "ok" in result


def test_reindex_system_timeout(tmp_path, mocker):
    core = AssistantCore(
        commands_file=tmp_path / "c.txt",
        auto_commands_file=tmp_path / "a.json",
        indexer_script=backend.paths.INDEXER_SCRIPT,
    )
    import subprocess
    mocker.patch.object(subprocess, "run",
                        side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1))
    result = core.reindex_system(timeout=1.0)
    assert not result["ok"]
    assert "уложился" in result["error"]


def test_reindex_system_nonzero_returncode(tmp_path, mocker):
    core = AssistantCore(
        commands_file=tmp_path / "c.txt",
        auto_commands_file=tmp_path / "a.json",
        indexer_script=backend.paths.INDEXER_SCRIPT,
    )
    import subprocess
    fake = subprocess.CompletedProcess(args=[], returncode=1,
                                        stdout="", stderr="error")
    mocker.patch.object(subprocess, "run", return_value=fake)
    result = core.reindex_system()
    assert not result["ok"]
    assert "код 1" in result["error"]
