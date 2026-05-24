"""Tests for system_indexer.py — desktop/KWin/PATH scanner."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import system_indexer as si


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "desktop"


# ── _clean_exec ───────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("firefox %u", "firefox"),
    ("kate %F", "kate"),
    ("foo %U bar", "foo  bar"),  # двойной пробел между токенами
    ("plain-cmd", "plain-cmd"),
    ("cmd %f %u %F %U", "cmd"),
])
def test_clean_exec(raw, expected):
    result = si._clean_exec(raw)
    # допускаем нормализацию пробелов
    assert result.strip() == expected.strip() or " ".join(result.split()) == " ".join(expected.split())


# ── _normalize_trigger ────────────────────────────────────────

def test_normalize_trigger_lowers_and_strips():
    assert si._normalize_trigger("  Открой Браузер  ") == "открой браузер"


def test_normalize_trigger_collapses_whitespace():
    assert si._normalize_trigger("foo   bar\tbaz") == "foo bar baz"


# ── parse_desktop_file ────────────────────────────────────────

def test_parse_desktop_firefox_fixture():
    entry = si.parse_desktop_file(str(FIXTURES / "Firefox.desktop"))
    assert entry is not None
    assert "firefox" in entry["command"]
    assert "&" in entry["command"]  # GUI приложения с фоном
    # Русские триггеры должны быть первыми
    assert any("огненный лис" in t or "огнен" in t for t in entry["triggers"])
    assert entry["source"] == "desktop"


def test_parse_desktop_konsole_fixture():
    entry = si.parse_desktop_file(str(FIXTURES / "Konsole.desktop"))
    assert entry is not None
    assert "konsole" in entry["command"]
    triggers = entry["triggers"]
    assert any("консоль" in t for t in triggers)


def test_parse_desktop_missing_file():
    assert si.parse_desktop_file("/nonexistent-path-zzz") is None


def test_parse_desktop_nodisplay(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text(
        "[Desktop Entry]\nName=Hidden\nExec=foo\nNoDisplay=true\n",
        encoding="utf-8",
    )
    assert si.parse_desktop_file(str(p)) is None


def test_parse_desktop_hidden(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text(
        "[Desktop Entry]\nName=Hidden\nExec=foo\nHidden=true\n",
        encoding="utf-8",
    )
    assert si.parse_desktop_file(str(p)) is None


def test_parse_desktop_non_application(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text(
        "[Desktop Entry]\nName=Link\nURL=http://x\nType=Link\n",
        encoding="utf-8",
    )
    assert si.parse_desktop_file(str(p)) is None


def test_parse_desktop_no_exec(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text(
        "[Desktop Entry]\nName=Foo\nType=Application\n",
        encoding="utf-8",
    )
    assert si.parse_desktop_file(str(p)) is None


def test_parse_desktop_terminal_true_wraps_konsole(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text(
        "[Desktop Entry]\nName=Htop\nType=Application\n"
        "Exec=htop\nTerminal=true\n",
        encoding="utf-8",
    )
    entry = si.parse_desktop_file(str(p))
    assert entry is not None
    assert "konsole -e" in entry["command"]


def test_parse_desktop_no_section(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text("# just a comment\n", encoding="utf-8")
    assert si.parse_desktop_file(str(p)) is None


def test_parse_desktop_triggers_uniqueness(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text(
        "[Desktop Entry]\nName=Foo\nName[ru]=Foo\nType=Application\nExec=foo\n",
        encoding="utf-8",
    )
    entry = si.parse_desktop_file(str(p))
    assert entry is not None
    # «foo» нормализуется одинаково для ru и en — дублей быть не должно
    assert len(entry["triggers"]) == len(set(entry["triggers"]))


def test_parse_desktop_skips_too_short_triggers(tmp_path):
    p = tmp_path / "x.desktop"
    p.write_text(
        "[Desktop Entry]\nName=A\nType=Application\nExec=foo\n",
        encoding="utf-8",
    )
    # «a» длиной 1 — короче MIN_TRIGGER_LEN=2 → должен пропустить
    entry = si.parse_desktop_file(str(p))
    assert entry is None


# ── collect_desktop_apps ──────────────────────────────────────

def test_collect_desktop_apps_from_fixture_dir(monkeypatch):
    monkeypatch.setattr(si, "DESKTOP_DIRS", [str(FIXTURES)])
    items = si.collect_desktop_apps(verbose=False)
    assert len(items) == 2
    assert any("firefox" in it["command"] for it in items)
    assert any("konsole" in it["command"] for it in items)


def test_collect_desktop_apps_deduplicates_by_command(monkeypatch, tmp_path):
    # Кладём два .desktop с одинаковым Exec
    (tmp_path / "a.desktop").write_text(
        "[Desktop Entry]\nName=One\nType=Application\nExec=mycmd\n",
        encoding="utf-8")
    (tmp_path / "b.desktop").write_text(
        "[Desktop Entry]\nName=Two\nType=Application\nExec=mycmd\n",
        encoding="utf-8")
    monkeypatch.setattr(si, "DESKTOP_DIRS", [str(tmp_path)])
    items = si.collect_desktop_apps(verbose=False)
    assert len(items) == 1


def test_collect_desktop_apps_ignores_missing_dirs(monkeypatch):
    monkeypatch.setattr(si, "DESKTOP_DIRS", ["/nonexistent-zzz-123"])
    assert si.collect_desktop_apps(verbose=False) == []


def test_collect_desktop_apps_skips_non_desktop_files(monkeypatch, tmp_path):
    (tmp_path / "readme.txt").write_text("hello")
    (tmp_path / "x.desktop").write_text(
        "[Desktop Entry]\nName=MyApp\nType=Application\nExec=myapp\n",
        encoding="utf-8")
    monkeypatch.setattr(si, "DESKTOP_DIRS", [str(tmp_path)])
    items = si.collect_desktop_apps(verbose=False)
    assert len(items) == 1


# ── list_kwin_shortcuts ──────────────────────────────────────

def test_list_kwin_shortcuts_no_qdbus(monkeypatch):
    monkeypatch.setattr(si.shutil, "which", lambda x: None)
    assert si.list_kwin_shortcuts(verbose=False) == []


def test_list_kwin_shortcuts_qdbus_error(monkeypatch):
    monkeypatch.setattr(si.shutil, "which",
                        lambda x: "/usr/bin/qdbus" if "qdbus" in x else None)
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="x")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert si.list_kwin_shortcuts(verbose=False) == []


def test_list_kwin_shortcuts_parses_output(monkeypatch):
    monkeypatch.setattr(si.shutil, "which",
                        lambda x: "/usr/bin/qdbus" if "qdbus" in x else None)
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="Window Minimize\nOverview\nshow_desktop\n_internal_x\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    items = si.list_kwin_shortcuts(verbose=False)
    # Window Minimize (с пробелом) и Overview (с заглавной) проходят фильтр
    # show_desktop и _internal_x — отсекаются
    assert any("Window Minimize" in i["command"] for i in items)
    assert any("Overview" in i["command"] for i in items)


def test_list_kwin_shortcuts_timeout(monkeypatch):
    monkeypatch.setattr(si.shutil, "which",
                        lambda x: "/usr/bin/qdbus" if "qdbus" in x else None)
    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="qdbus", timeout=5)
    monkeypatch.setattr(subprocess, "run", boom)
    assert si.list_kwin_shortcuts(verbose=False) == []


# ── index_path_binaries ──────────────────────────────────────

def test_index_path_binaries_with_limit(monkeypatch, tmp_path):
    # Создаём пустую директорию с парой бинарей
    bin_a = tmp_path / "fake_bin_a"
    bin_b = tmp_path / "fake_bin_b"
    bin_a.write_text("#!/bin/sh\necho a"); bin_a.chmod(0o755)
    bin_b.write_text("#!/bin/sh\necho b"); bin_b.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(si.shutil, "which", lambda x: None)  # без whatis
    items = si.index_path_binaries(verbose=False, limit=10)
    names = {it["command"] for it in items}
    assert "fake_bin_a" in names
    assert "fake_bin_b" in names


def test_index_path_binaries_skips_pam_(monkeypatch, tmp_path):
    skip = tmp_path / "pam_unix"
    skip.write_text("#!/bin/sh\ntrue"); skip.chmod(0o755)
    keep = tmp_path / "normal_bin"
    keep.write_text("#!/bin/sh\ntrue"); keep.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(si.shutil, "which", lambda x: None)
    items = si.index_path_binaries(verbose=False)
    names = {it["command"] for it in items}
    assert "pam_unix" not in names
    assert "normal_bin" in names


def test_index_path_binaries_handles_no_path(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(si.shutil, "which", lambda x: None)
    items = si.index_path_binaries(verbose=False)
    assert items == []


# ── items_to_payload ─────────────────────────────────────────

def test_items_to_payload_structure():
    items = [
        {"command": "firefox &", "triggers": ["браузер", "огненный лис"],
         "source": "desktop"},
    ]
    payload = si.items_to_payload(items, {"desktop": 1})
    assert payload["version"] == 1
    assert "generated_at" in payload
    assert payload["sources"] == {"desktop": 1}
    assert len(payload["items"]) == 2  # один command, два trigger
    for item in payload["items"]:
        assert item["command"] == "firefox &"
        assert item["source"] == "desktop"


def test_items_to_payload_empty():
    payload = si.items_to_payload([], {"desktop": 0, "kwin": 0, "binary": 0})
    assert payload["items"] == []
    assert payload["version"] == 1


def test_items_to_payload_generated_at_iso():
    payload = si.items_to_payload([], {})
    import datetime
    # должно парситься как ISO
    datetime.datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))


# ── main() через subprocess ───────────────────────────────────

@pytest.mark.slow
def test_main_runs_and_creates_output(tmp_path):
    out = tmp_path / "out.json"
    proc = subprocess.run(
        [sys.executable, "system_indexer.py", "-o", str(out), "--quiet"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert "items" in payload
    assert "sources" in payload


def test_main_with_binaries_limit(tmp_path):
    out = tmp_path / "out.json"
    proc = subprocess.run(
        [sys.executable, "system_indexer.py", "-o", str(out),
         "--binaries", "--binary-limit", "5", "--quiet"],
        capture_output=True, text=True, timeout=60,
    )
    # exit 0 даже если нет ничего
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["sources"].get("binary", 0) >= 0


# ── Константы ────────────────────────────────────────────────

def test_min_trigger_len():
    assert si.MIN_TRIGGER_LEN >= 2


def test_desktop_dirs_includes_standards():
    assert any("/usr/share/applications" in d for d in si.DESKTOP_DIRS)
    assert any("~/.local" in d or ".local/share" in d for d in si.DESKTOP_DIRS)
