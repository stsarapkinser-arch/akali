"""Tests for akali.core.db — curated + auto command sources."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from akali.core import db


def test_parse_curated_minimal(min_commands):
    d = db.parse_curated(min_commands)
    assert "ls -la" in d
    assert "date" in d
    assert "firefox &" in d
    assert d["ls -la"] == ["покажи файлы", "листинг каталога"]


def test_parse_curated_ignores_comments_and_blanks(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text(
        "# header comment\n"
        "\n"
        "ls -> файлы\n"
        "\n"
        "# another comment\n"
        "date -> время\n",
        encoding="utf-8",
    )
    d = db.parse_curated(p)
    assert list(d.keys()) == ["ls", "date"]


def test_parse_curated_skips_malformed(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text(
        "good -> trigger\n"
        "no_arrow_here\n"
        "empty triggers ->\n"
        "-> no_command\n",
        encoding="utf-8",
    )
    d = db.parse_curated(p)
    assert d == {"good": ["trigger"]}


def test_parse_curated_lowercases_triggers(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("ls -> ПОКАЖИ Файлы\n", encoding="utf-8")
    d = db.parse_curated(p)
    assert d["ls"] == ["покази файлы"] or d["ls"] == ["покажи файлы"]  # lowercased


def test_parse_curated_missing_file_returns_empty(tmp_path):
    assert db.parse_curated(tmp_path / "missing.txt") == {}


def test_parse_curated_real_file():
    # Используем реальный commands.txt из репо
    from akali import paths
    d = db.parse_curated(paths.COMMANDS_TXT)
    assert len(d) > 50  # должно быть много команд
    # exec-команды должны существовать
    assert all(isinstance(triggers, list) and triggers for triggers in d.values())


def test_parse_auto_minimal(tmp_path):
    p = tmp_path / "auto.json"
    p.write_text(json.dumps({
        "version": 1,
        "sources": {"desktop": 2},
        "items": [
            {"command": "firefox", "trigger": "браузер"},
            {"command": "firefox", "trigger": "файрфокс"},
            {"command": "konsole", "trigger": "консоль"},
        ],
    }), encoding="utf-8")
    d, sources = db.parse_auto(p)
    assert d == {"firefox": ["браузер", "файрфокс"], "konsole": ["консоль"]}
    assert sources == {"desktop": 2}


def test_parse_auto_missing_file(tmp_path):
    d, src = db.parse_auto(tmp_path / "nope.json")
    assert d == {}
    assert src == {}


def test_parse_auto_corrupted_json(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    d, src = db.parse_auto(p)
    assert d == {}
    assert src == {}


def test_parse_auto_missing_items_key(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("{}", encoding="utf-8")
    d, src = db.parse_auto(p)
    assert d == {}
    assert src == {}


def test_parse_auto_skips_items_without_command(tmp_path):
    p = tmp_path / "auto.json"
    p.write_text(json.dumps({
        "items": [
            {"command": "ls", "trigger": "файлы"},
            {"trigger": "no command"},
            {"command": "date"},  # no trigger
            {"command": "", "trigger": "empty cmd"},
        ],
    }), encoding="utf-8")
    d, _ = db.parse_auto(p)
    assert d == {"ls": ["файлы"]}


def test_merge_curated_priority():
    curated = {"firefox": ["открой браузер"]}
    auto = {"firefox": ["fr браузер"], "konsole": ["консоль"]}
    merged = db.merge(curated, auto)
    # curated триггеры должны идти первыми
    assert merged["firefox"][0] == "открой браузер"
    assert "fr браузер" in merged["firefox"]
    # auto добавляется
    assert merged["konsole"] == ["консоль"]


def test_merge_no_duplicate_triggers():
    curated = {"ls": ["files", "list"]}
    auto = {"ls": ["files", "showme"]}
    merged = db.merge(curated, auto)
    assert merged["ls"].count("files") == 1


def test_merge_empty_inputs():
    assert db.merge({}, {}) == {}
    assert db.merge({"a": ["x"]}, {}) == {"a": ["x"]}
    assert db.merge({}, {"a": ["x"]}) == {"a": ["x"]}


def test_build_commands_bundle(min_commands, tmp_path):
    auto_path = tmp_path / "auto.json"
    auto_path.write_text(json.dumps({
        "sources": {"desktop": 1},
        "items": [{"command": "kate", "trigger": "редактор"}],
    }), encoding="utf-8")
    bundle = db.build_commands_bundle(min_commands, auto_path)
    assert isinstance(bundle, db.CommandsBundle)
    assert bundle.curated_count == 3
    assert bundle.auto_count == 1
    assert bundle.auto_sources == {"desktop": 1}
    assert "kate" in bundle.merged
    assert "ls -la" in bundle.merged


def test_build_commands_bundle_auto_missing(min_commands, tmp_path):
    bundle = db.build_commands_bundle(min_commands, tmp_path / "nope.json")
    assert bundle.curated_count == 3
    assert bundle.auto_count == 0
    assert bundle.auto_sources == {}
