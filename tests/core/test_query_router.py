"""Tests for akali.core.query_router — hybrid cache/embed/LLM pipeline."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from akali.core import query_router as qr
from akali.core import safety
from akali.core.query_router import QueryRouter


pytestmark = pytest.mark.slow  # real FastEmbed


@pytest.fixture
def min_db():
    """Минимальная база команд для роутера."""
    return {
        "ls -la": ["покажи файлы", "листинг каталога"],
        "date": ["текущее время", "какая дата"],
        "firefox &": ["открой браузер", "запусти браузер"],
    }


@pytest.fixture
def router_factory(tmp_path, min_db, min_commands, mocker):
    """Создаёт изолированный QueryRouter с моками LLM-клиентов."""
    def _make(llm_mode="auto", gemini_ok=True, ollama_ok=True,
              gemini_cmd="echo gemini", ollama_cmd="echo ollama",
              has_internet=True):
        mock_check = mocker.patch.object(qr, "check_internet",
                                         return_value=has_internet)

        gemini = MagicMock()
        gemini.has_key = True
        gemini.last_error = "" if gemini_ok else "test failure"
        gemini.query.return_value = gemini_cmd if gemini_ok else None

        ollama = MagicMock()
        ollama.query.return_value = ollama_cmd if ollama_ok else None

        mocker.patch.object(qr, "GeminiClient", return_value=gemini)
        mocker.patch.object(qr, "OllamaClient", return_value=ollama)

        cache_file = tmp_path / "qc.json"
        r = QueryRouter(cache_file=cache_file, gemini_api_key="fake-key",
                        llm_mode=llm_mode)
        r.load_db(min_db, min_commands)
        return r, gemini, ollama
    return _make


# ── Базовая логика инициализации ──────────────────────────────────

def test_default_constants():
    assert qr.SEMANTIC_THRESHOLD == 0.85
    assert qr.LLM_MODE_AUTO in qr.LLM_MODES
    assert qr.LLM_MODE_GEMINI in qr.LLM_MODES
    assert qr.LLM_MODE_OLLAMA in qr.LLM_MODES


def test_set_llm_mode_valid(router_factory):
    r, _, _ = router_factory()
    r.set_llm_mode(qr.LLM_MODE_OLLAMA)
    assert r.llm_mode == qr.LLM_MODE_OLLAMA


def test_set_llm_mode_invalid(router_factory):
    r, _, _ = router_factory()
    r.set_llm_mode("invalid-mode")
    assert r.llm_mode == qr.LLM_MODE_AUTO  # не поменялось


def test_set_gemini_key(router_factory, mocker):
    r, _, _ = router_factory()
    r.set_gemini_key("new-key")  # не должно крашиться


def test_unknown_llm_mode_falls_back_to_auto(tmp_path, mocker):
    mocker.patch.object(qr, "check_internet", return_value=False)
    mocker.patch.object(qr, "GeminiClient")
    mocker.patch.object(qr, "OllamaClient")
    r = QueryRouter(cache_file=tmp_path / "qc.json", llm_mode="garbage")
    assert r.llm_mode == qr.LLM_MODE_AUTO


# ── Гарантия исключения power-команд из индекса ───────────────────

def test_load_db_excludes_power_commands(tmp_path, mocker, min_commands):
    mocker.patch.object(qr, "check_internet", return_value=True)
    mocker.patch.object(qr, "GeminiClient")
    mocker.patch.object(qr, "OllamaClient")
    r = QueryRouter(cache_file=tmp_path / "qc.json")
    db = {
        "ls": ["листинг"],
        "systemctl poweroff": ["выключи компьютер"],
        "systemctl reboot": ["перезагрузи"],
    }
    r.load_db(db, min_commands)
    # 1 нормальная + 2 силовых ⇒ только 1 в индексе
    assert len(r._db_cmds) == 1
    assert "systemctl poweroff" not in r._db_cmds


def test_load_db_handles_embed_failure(tmp_path, mocker, min_commands):
    mocker.patch.object(qr, "check_internet", return_value=True)
    mocker.patch.object(qr, "GeminiClient")
    mocker.patch.object(qr, "OllamaClient")
    r = QueryRouter(cache_file=tmp_path / "qc.json")
    # Мокаем EmbedCache.load_db чтобы сломаться
    mocker.patch.object(r._embed, "load_db", side_effect=RuntimeError("boom"))
    r.load_db({"ls": ["листинг"]}, min_commands)
    assert r._db_embs is None
    assert r._db_cmds == []


# ── route() ────────────────────────────────────────────────────────

def test_route_returns_none_for_short_text(router_factory):
    r, _, _ = router_factory()
    assert r.route("a") is None
    assert r.route("") is None


def test_route_cache_hit_skips_embed_and_llm(router_factory):
    r, gemini, ollama = router_factory()
    r._cache.put("привет команда", "echo cached", "test")
    cmd = r.route("привет команда")
    assert cmd == "echo cached"
    gemini.query.assert_not_called()
    ollama.query.assert_not_called()


def test_route_semantic_hit(router_factory):
    r, gemini, ollama = router_factory()
    # «показать каталог» близко к «покажи файлы»
    cmd = r.route("покажи файлы")  # exact trigger → должно быть semantic hit
    assert cmd == "ls -la"
    gemini.query.assert_not_called()
    ollama.query.assert_not_called()


def test_route_semantic_cached_after_hit(router_factory):
    r, _, _ = router_factory()
    r.route("покажи файлы")
    assert r._cache.get("покажи файлы") == "ls -la"


def test_route_semantic_miss_falls_to_gemini(router_factory):
    r, gemini, ollama = router_factory(gemini_cmd="echo from-gemini")
    cmd = r.route("совершенно непохожая фраза без аналога в базе зеленые человечки")
    assert cmd == "echo from-gemini"
    gemini.query.assert_called_once()
    ollama.query.assert_not_called()


def test_route_gemini_fails_fallback_to_ollama_in_auto(router_factory):
    r, gemini, ollama = router_factory(
        gemini_ok=False,
        ollama_cmd="echo from-ollama",
    )
    cmd = r.route("совершенно непохожая фраза без аналога в базе абра кадабра")
    assert cmd == "echo from-ollama"
    gemini.query.assert_called_once()
    ollama.query.assert_called_once()


def test_route_gemini_only_mode_no_fallback(router_factory):
    r, gemini, ollama = router_factory(
        llm_mode=qr.LLM_MODE_GEMINI,
        gemini_ok=False,
    )
    cmd = r.route("совершенно непохожая фраза без аналога в базе зюзя люся")
    assert cmd is None
    gemini.query.assert_called_once()
    ollama.query.assert_not_called()


def test_route_ollama_only_skips_gemini(router_factory):
    r, gemini, ollama = router_factory(
        llm_mode=qr.LLM_MODE_OLLAMA,
        ollama_cmd="echo from-ollama",
    )
    cmd = r.route("совершенно непохожая фраза без аналога вампир)")
    assert cmd == "echo from-ollama"
    gemini.query.assert_not_called()
    ollama.query.assert_called_once()


def test_route_no_internet_skips_gemini_in_auto(router_factory):
    r, gemini, ollama = router_factory(
        has_internet=False,
        ollama_cmd="echo offline",
    )
    cmd = r.route("совершенно непохожая фраза без аналога абрвалг")
    assert cmd == "echo offline"
    gemini.query.assert_not_called()
    ollama.query.assert_called_once()


def test_route_no_internet_gemini_only_returns_none(router_factory):
    r, gemini, _ = router_factory(
        llm_mode=qr.LLM_MODE_GEMINI,
        has_internet=False,
    )
    cmd = r.route("совершенно непохожая фраза без аналога зюзя кобра")
    assert cmd is None
    gemini.query.assert_not_called()


def test_route_no_gemini_key_in_auto_uses_ollama(router_factory, mocker):
    r, gemini, ollama = router_factory(ollama_cmd="echo from-ollama")
    gemini.has_key = False
    cmd = r.route("совершенно непохожая фраза без аналога джулия")
    assert cmd == "echo from-ollama"
    gemini.query.assert_not_called()


def test_route_no_gemini_key_in_gemini_only_returns_none(router_factory):
    r, gemini, _ = router_factory(llm_mode=qr.LLM_MODE_GEMINI)
    gemini.has_key = False
    cmd = r.route("совершенно непохожая фраза без аналога джулия")
    assert cmd is None


def test_route_blocks_power_command_from_llm(router_factory):
    r, gemini, _ = router_factory(gemini_cmd="systemctl poweroff")
    cmd = r.route("совершенно непохожая фраза без аналога мунспот")
    assert cmd is None


def test_route_blocks_dangerous_command_from_llm(router_factory):
    r, gemini, _ = router_factory(gemini_cmd="rm -rf /")
    notifications = []
    safety.set_notifier(lambda *a: notifications.append(a))
    try:
        cmd = r.route("совершенно непохожая фраза без аналога мунспот")
        assert cmd is None
        assert len(notifications) == 1
    finally:
        safety.set_notifier(None)


def test_route_empty_llm_response_ignored(router_factory):
    r, gemini, ollama = router_factory(
        gemini_cmd="echo error",
        ollama_cmd="echo error",
    )
    cmd = r.route("совершенно непохожая фраза без аналога космос")
    assert cmd is None


def test_route_both_llm_fail(router_factory):
    r, _, _ = router_factory(gemini_ok=False, ollama_ok=False)
    cmd = r.route("совершенно непохожая фраза без аналога ракета")
    assert cmd is None


# ── Внутренние методы ─────────────────────────────────────────────

def test_gate_blocks_power_command(tmp_path, mocker, min_commands):
    mocker.patch.object(qr, "check_internet", return_value=True)
    mocker.patch.object(qr, "GeminiClient")
    mocker.patch.object(qr, "OllamaClient")
    r = QueryRouter(cache_file=tmp_path / "qc.json")
    assert not r._gate("systemctl poweroff", "gemini")


def test_gate_blocks_dangerous(tmp_path, mocker):
    mocker.patch.object(qr, "check_internet", return_value=True)
    mocker.patch.object(qr, "GeminiClient")
    mocker.patch.object(qr, "OllamaClient")
    r = QueryRouter(cache_file=tmp_path / "qc.json")
    assert not r._gate("rm -rf /", "ollama")


def test_gate_passes_safe(tmp_path, mocker):
    mocker.patch.object(qr, "check_internet", return_value=True)
    mocker.patch.object(qr, "GeminiClient")
    mocker.patch.object(qr, "OllamaClient")
    r = QueryRouter(cache_file=tmp_path / "qc.json")
    assert r._gate("ls -la", "semantic")


def test_semantic_search_with_no_db(tmp_path, mocker):
    mocker.patch.object(qr, "check_internet", return_value=True)
    mocker.patch.object(qr, "GeminiClient")
    mocker.patch.object(qr, "OllamaClient")
    r = QueryRouter(cache_file=tmp_path / "qc.json")
    assert r._semantic_search("anything") is None


def test_semantic_search_handles_embed_exception(router_factory, mocker):
    r, _, _ = router_factory()
    mocker.patch.object(r._embed, "embed_query",
                        side_effect=RuntimeError("boom"))
    assert r._semantic_search("anything") is None


def test_cache_size_property(router_factory):
    r, _, _ = router_factory()
    initial = r.cache_size
    r._cache.put("x", "echo x", "test")
    assert r.cache_size == initial + 1


def test_try_gemini_returns_error_on_exception(router_factory):
    r, gemini, _ = router_factory()
    gemini.query.side_effect = RuntimeError("network failure")
    cmd, err = r._try_gemini("test")
    assert cmd is None
    assert "network" in err.lower() or err


def test_try_ollama_returns_none_on_exception(router_factory):
    r, _, ollama = router_factory()
    ollama.query.side_effect = RuntimeError("crashed")
    assert r._try_ollama("test") is None
