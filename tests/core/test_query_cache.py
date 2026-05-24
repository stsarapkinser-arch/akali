"""Tests for akali.core.query_cache — LRU disk-backed cache."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from akali.core.query_cache import QueryCache, _Entry, _norm


def test_norm_strips_and_lowers():
    assert _norm("  Привет  ") == "привет"
    assert _norm("HELLO") == "hello"
    assert _norm("") == ""


def test_put_get_roundtrip(tmp_path):
    qc = QueryCache(tmp_path / "c.json", max_size=10)
    qc.put("query a", "echo a", "test")
    assert qc.get("query a") == "echo a"


def test_get_normalizes_key(tmp_path):
    qc = QueryCache(tmp_path / "c.json")
    qc.put("Hello", "x", "src")
    assert qc.get("hello") == "x"
    assert qc.get("  HELLO  ") == "x"


def test_get_returns_none_for_missing(tmp_path):
    qc = QueryCache(tmp_path / "c.json")
    assert qc.get("nope") is None


def test_lru_evicts_oldest(tmp_path):
    qc = QueryCache(tmp_path / "c.json", max_size=3)
    qc.put("a", "1", "s")
    qc.put("b", "2", "s")
    qc.put("c", "3", "s")
    qc.put("d", "4", "s")  # должен вытеснить "a"
    assert qc.get("a") is None
    assert qc.get("b") == "2"
    assert qc.get("c") == "3"
    assert qc.get("d") == "4"


def test_lru_get_touches_recency(tmp_path):
    qc = QueryCache(tmp_path / "c.json", max_size=3)
    qc.put("a", "1", "s")
    qc.put("b", "2", "s")
    qc.put("c", "3", "s")
    qc.get("a")  # touch
    qc.put("d", "4", "s")  # должен вытеснить "b", а не "a"
    assert qc.get("a") == "1"
    assert qc.get("b") is None


def test_put_existing_key_overwrites_and_refreshes(tmp_path):
    qc = QueryCache(tmp_path / "c.json", max_size=2)
    qc.put("a", "1", "s")
    qc.put("b", "2", "s")
    qc.put("a", "1-new", "s")
    qc.put("c", "3", "s")  # должен вытеснить "b", не "a"
    assert qc.get("a") == "1-new"
    assert qc.get("b") is None
    assert qc.get("c") == "3"


def test_persist_to_disk(tmp_path):
    p = tmp_path / "c.json"
    qc = QueryCache(p)
    qc.put("a", "echo a", "test")
    qc.put("b", "echo b", "test")
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "a" in data
    assert data["a"]["command"] == "echo a"
    assert data["a"]["source"] == "test"


def test_reload_from_disk(tmp_path):
    p = tmp_path / "c.json"
    qc1 = QueryCache(p, max_size=5)
    qc1.put("a", "1", "s1")
    qc1.put("b", "2", "s2")
    qc1.save()

    qc2 = QueryCache(p, max_size=5)
    assert qc2.get("a") == "1"
    assert qc2.get("b") == "2"


def test_atomic_write_via_tmp(tmp_path):
    p = tmp_path / "c.json"
    qc = QueryCache(p)
    qc.put("a", "1", "s")
    # tmp файл удаляется после flush
    assert not (tmp_path / "c.json.tmp").exists()
    assert p.exists()


def test_corrupted_json_starts_empty(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    qc = QueryCache(p)
    assert qc.get("anything") is None
    assert len(qc) == 0


def test_partial_entry_fields_handled(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "a": {"command": "cmd", "source": "s"},  # без ts, hits
    }), encoding="utf-8")
    qc = QueryCache(p)
    assert qc.get("a") == "cmd"


def test_hits_counter_increments(tmp_path):
    qc = QueryCache(tmp_path / "c.json")
    qc.put("a", "1", "s")
    qc.get("a")
    qc.get("a")
    qc.get("a")
    qc.save()
    data = json.loads((tmp_path / "c.json").read_text())
    assert data["a"]["hits"] == 3


def test_len(tmp_path):
    qc = QueryCache(tmp_path / "c.json")
    assert len(qc) == 0
    qc.put("a", "1", "s")
    qc.put("b", "2", "s")
    assert len(qc) == 2


def test_entry_dataclass_defaults():
    e = _Entry(command="cmd", source="src")
    assert e.command == "cmd"
    assert e.source == "src"
    assert e.hits == 0
    assert e.ts > 0
