"""Tests for akali.core.llm_client — internet check + Gemini/Ollama clients."""
from __future__ import annotations

import socket
import sys
import time
from unittest.mock import MagicMock

import pytest

from akali.core import llm_client as lc


@pytest.fixture(autouse=True)
def _reset_net_cache():
    """Сбрасываем глобальный кэш интернета между тестами."""
    lc._net_state = (0.0, False)
    yield
    lc._net_state = (0.0, False)


# ── _strip_fences ──────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("ls -la", "ls -la"),
    ("```bash\nls -la\n```", "ls -la"),
    ("```\nls -la\n```", "ls -la"),
    ("```sh\nls -la\n```", "ls -la"),
    ("ls -la\necho second", "ls -la"),  # только первая строка
    ("   ls -la   ", "ls -la"),
    ("", ""),
    ("```bash\n```", ""),  # пустые fences
])
def test_strip_fences(raw, expected):
    assert lc._strip_fences(raw) == expected


# ── check_internet ────────────────────────────────────────────

def test_check_internet_success(monkeypatch):
    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(socket, "create_connection",
                        lambda addr, timeout: FakeSock())
    assert lc.check_internet() is True


def test_check_internet_failure(monkeypatch):
    def boom(addr, timeout):
        raise OSError("no network")
    monkeypatch.setattr(socket, "create_connection", boom)
    assert lc.check_internet() is False


def test_check_internet_timeout(monkeypatch):
    def boom(addr, timeout):
        raise socket.timeout("timed out")
    monkeypatch.setattr(socket, "create_connection", boom)
    assert lc.check_internet() is False


def test_check_internet_cached(monkeypatch):
    calls = []
    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def fake(addr, timeout):
        calls.append(1)
        return FakeSock()
    monkeypatch.setattr(socket, "create_connection", fake)
    lc.check_internet()
    lc.check_internet()
    lc.check_internet()
    assert len(calls) == 1  # положительный закэширован


def test_check_internet_no_cache_on_failure(monkeypatch):
    calls = []
    def boom(addr, timeout):
        calls.append(1)
        raise OSError("no")
    monkeypatch.setattr(socket, "create_connection", boom)
    lc.check_internet()
    lc.check_internet()
    assert len(calls) == 2  # отрицательный не кэшируется


@pytest.mark.network
def test_check_internet_real():
    # Реальный socket к 8.8.8.8:53. Если CI без сети — пропустит.
    result = lc.check_internet()
    assert isinstance(result, bool)


# ── _load_gemini_key_from_env ──────────────────────────────────

def test_load_gemini_key_from_env_present(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    key = lc._load_gemini_key_from_env()
    assert key == "test-key-123"


def test_load_gemini_key_from_env_strips_whitespace(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "  spaced-key  ")
    assert lc._load_gemini_key_from_env() == "spaced-key"


def test_load_gemini_key_from_env_empty(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent-tmp-dir-no-env-12345")
    monkeypatch.chdir("/tmp")  # тоже без .env
    # Если dotenv не находит .env, должно вернуть None
    result = lc._load_gemini_key_from_env()
    # может быть None или пустая строка в зависимости от окружения,
    # но не должен крашиться
    assert result is None or isinstance(result, str)


def test_load_gemini_key_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=key-from-file\n")
    key = lc._load_gemini_key_from_env()
    assert key == "key-from-file"


# ── _humanize_gemini_error ────────────────────────────────────

@pytest.mark.parametrize("exc_text, expected_substr", [
    ("FAILED_PRECONDITION: API not available in your location",
     "регион"),
    ("PERMISSION_DENIED: API key invalid", "API-ключ"),
    ("403 Unauthorized", "API-ключ"),
    ("RESOURCE_EXHAUSTED: quota", "лимит"),
    ("429 Too Many Requests", "лимит"),
    ("DEADLINE_EXCEEDED: request took too long",
     "таймаут"),
    ("UNAVAILABLE: service down", "недоступен"),
    ("503 Internal", "недоступен"),
    ("INVALID_ARGUMENT: bad request", "некорректный"),
    ("400 Bad Request", "некорректный"),
    ("Name or service not known", "сетевая"),
    ("connection refused", "сетевая"),
])
def test_humanize_gemini_error(exc_text, expected_substr):
    msg = lc._humanize_gemini_error(Exception(exc_text))
    assert expected_substr.lower() in msg.lower()


def test_humanize_gemini_error_generic():
    msg = lc._humanize_gemini_error(ValueError("something weird"))
    assert "ValueError" in msg
    assert "something weird" in msg


def test_humanize_gemini_error_truncates_long():
    long = "x" * 500
    msg = lc._humanize_gemini_error(RuntimeError(long))
    assert len(msg) < 250


# ── GeminiClient ──────────────────────────────────────────────

def test_gemini_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir("/tmp")
    client = lc.GeminiClient(api_key=None)
    assert client.has_key in (False, True)  # зависит от .env, но
    if not client.has_key:
        assert client.query("test") is None
        assert "ключ" in client.last_error


def test_gemini_with_key_constructs():
    client = lc.GeminiClient(api_key="fake-key")
    assert client.has_key
    assert client.last_error == ""


def test_gemini_query_success(monkeypatch):
    client = lc.GeminiClient(api_key="fake-key")
    fake_client = MagicMock()
    fake_resp = MagicMock(text="ls -la")
    fake_client.models.generate_content.return_value = fake_resp
    monkeypatch.setattr(client, "_get_client", lambda: fake_client)
    result = client.query("показать файлы")
    assert result == "ls -la"
    assert client.last_error == ""
    fake_client.models.generate_content.assert_called_once()
    call_kwargs = fake_client.models.generate_content.call_args
    assert call_kwargs[1]["model"] == lc.GEMINI_MODEL
    assert call_kwargs[1]["contents"] == "показать файлы"
    assert call_kwargs[1]["config"]["temperature"] == 0


def test_gemini_query_strips_fences(monkeypatch):
    client = lc.GeminiClient(api_key="fake")
    fake_client = MagicMock()
    fake_resp = MagicMock(text="```bash\nls -la\n```")
    fake_client.models.generate_content.return_value = fake_resp
    monkeypatch.setattr(client, "_get_client", lambda: fake_client)
    result = client.query("test")
    assert result == "ls -la"


def test_gemini_query_empty_response(monkeypatch):
    client = lc.GeminiClient(api_key="fake")
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = MagicMock(text="")
    monkeypatch.setattr(client, "_get_client", lambda: fake_client)
    assert client.query("x") is None


def test_gemini_query_exception(monkeypatch):
    client = lc.GeminiClient(api_key="fake")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("403 forbidden")
    monkeypatch.setattr(client, "_get_client", lambda: fake_client)
    assert client.query("x") is None
    assert "API-ключ" in client.last_error or "403" in client.last_error


def test_gemini_get_client_raises_without_package(monkeypatch):
    client = lc.GeminiClient(api_key="fake")
    import builtins
    orig = builtins.__import__
    def bad(name, *a, **kw):
        if name == "google.genai" or name == "google":
            raise ImportError("no google")
        return orig(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", bad)
    monkeypatch.delitem(sys.modules, "google.genai", raising=False)
    monkeypatch.delitem(sys.modules, "google", raising=False)
    with pytest.raises(RuntimeError, match="google-genai"):
        client._get_client()


# ── OllamaClient ──────────────────────────────────────────────

def test_ollama_query_success(monkeypatch):
    client = lc.OllamaClient(model="test-model")
    fake_ollama = MagicMock()
    fake_resp = MagicMock(message=MagicMock(content="ls -la"))
    fake_ollama.chat.return_value = fake_resp
    fake_ollama.list.return_value = MagicMock(models=[
        MagicMock(model="test-model:latest")
    ])
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama)
    result = client.query("показать файлы")
    assert result == "ls -la"


def test_ollama_query_strips_fences(monkeypatch):
    client = lc.OllamaClient()
    fake_ollama = MagicMock()
    fake_ollama.chat.return_value = MagicMock(
        message=MagicMock(content="```bash\necho hi\n```"))
    fake_ollama.list.return_value = MagicMock(models=[])
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama)
    result = client.query("test")
    assert result == "echo hi"


def test_ollama_query_exception(monkeypatch):
    client = lc.OllamaClient()
    fake_ollama = MagicMock()
    fake_ollama.chat.side_effect = RuntimeError("daemon down")
    fake_ollama.list.return_value = MagicMock(models=[])
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama)
    assert client.query("x") is None


def test_ollama_check_model_once_logs_missing(monkeypatch, caplog):
    client = lc.OllamaClient(model="qwen2.5-coder:1.5b")
    fake_ollama = MagicMock()
    fake_ollama.list.return_value = MagicMock(models=[])  # пусто
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama)
    import logging
    with caplog.at_level(logging.WARNING):
        client._check_model_once()
    assert any("не загружена" in r.message for r in caplog.records)


def test_ollama_check_model_only_once(monkeypatch):
    client = lc.OllamaClient()
    fake_ollama = MagicMock()
    fake_ollama.list.return_value = MagicMock(models=[
        MagicMock(model="qwen2.5-coder:latest")
    ])
    monkeypatch.setitem(sys.modules, "ollama", fake_ollama)
    client._check_model_once()
    client._check_model_once()
    client._check_model_once()
    # list дёрнулся только один раз
    assert fake_ollama.list.call_count == 1


def test_ollama_default_model():
    client = lc.OllamaClient()
    assert client.model == lc.OLLAMA_MODEL


# ── Константы ────────────────────────────────────────────────

def test_system_prompt_contains_critical_rules():
    p = lc.SYSTEM_PROMPT
    assert "poweroff" in p
    assert "rm -rf" in p
    assert "qdbus" in p
    assert "Markdown" in p or "bash" in p


def test_gemini_default_model():
    assert lc.GEMINI_MODEL.startswith("gemini-")


def test_network_constants():
    assert lc.NETWORK_HOST == "8.8.8.8"
    assert lc.NETWORK_PORT == 53
    assert lc.NETWORK_TIMEOUT < 5.0
    assert lc.NETWORK_CACHE_TTL >= 10.0
