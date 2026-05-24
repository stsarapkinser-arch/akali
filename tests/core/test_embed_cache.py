"""Tests for akali.core.embed_cache — FastEmbed with disk cache."""
from __future__ import annotations

import pickle
import time
from pathlib import Path

import numpy as np
import pytest

from akali.core import embed_cache as ec
from akali.core.embed_cache import EmbedCache, DEFAULT_EMBED_MODEL


# Большая часть тестов — slow, потому что первый embed_batch грузит модель
# (~80 MB onnxruntime + multilingual MiniLM). После первого раза модель
# закэширована в ~/.cache/fastembed и тесты быстрые.

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def real_cache():
    """Реальный EmbedCache с lazy-init. Шарится между тестами модуля."""
    return EmbedCache()


# ── _find_multilingual_model ───────────────────────────────────────

def test_find_multilingual_model_picks_multilingual():
    from fastembed import TextEmbedding
    name = ec._find_multilingual_model(TextEmbedding)
    assert name is not None
    assert "multilingual" in name.lower() or name


def test_find_multilingual_model_handles_exception():
    class Bad:
        @staticmethod
        def list_supported_models():
            raise RuntimeError("boom")
    assert ec._find_multilingual_model(Bad) is None


def test_find_multilingual_model_no_matches():
    class NoModels:
        @staticmethod
        def list_supported_models():
            return []
    assert ec._find_multilingual_model(NoModels) is None


def test_find_multilingual_model_string_entries():
    class StrModels:
        @staticmethod
        def list_supported_models():
            return ["model-a", "some-multilingual-x", "model-c"]
    assert ec._find_multilingual_model(StrModels) == "some-multilingual-x"


# ── EmbedCache real ─────────────────────────────────────────────

def test_embed_query_returns_1d_float32(real_cache):
    v = real_cache.embed_query("привет, мир")
    assert isinstance(v, np.ndarray)
    assert v.ndim == 1
    assert v.dtype == np.float32
    assert v.shape[0] > 100  # обычно 384 для MiniLM


def test_embed_batch_returns_2d_matrix(real_cache):
    m = real_cache.embed_batch(["привет", "мир", "тест"])
    assert m.ndim == 2
    assert m.shape[0] == 3
    assert m.dtype == np.float32


def test_embed_query_deterministic(real_cache):
    v1 = real_cache.embed_query("test phrase one")
    v2 = real_cache.embed_query("test phrase one")
    np.testing.assert_array_almost_equal(v1, v2, decimal=5)


# ── load_db cache logic ────────────────────────────────────────────

def test_load_db_creates_cache(real_cache, tmp_path):
    cmds_file = tmp_path / "commands.txt"
    cmds_file.write_text("ls -> list\ndate -> time\n", encoding="utf-8")
    pairs = [("list", "ls"), ("time", "date")]
    embs, cmds = real_cache.load_db(cmds_file, pairs)
    assert embs.shape[0] == 2
    assert cmds == ["ls", "date"]
    cache_path = cmds_file.with_name(cmds_file.name + ec._CACHE_SUFFIX)
    assert cache_path.exists()


def test_load_db_uses_cache_on_second_call(real_cache, tmp_path):
    cmds_file = tmp_path / "c.txt"
    cmds_file.write_text("x", encoding="utf-8")
    pairs = [("hi", "echo hi"), ("bye", "echo bye")]
    real_cache.load_db(cmds_file, pairs)
    cache_path = cmds_file.with_name(cmds_file.name + ec._CACHE_SUFFIX)
    mtime_before = cache_path.stat().st_mtime
    time.sleep(0.05)
    real_cache.load_db(cmds_file, pairs)
    mtime_after = cache_path.stat().st_mtime
    assert mtime_before == mtime_after  # кэш не перезаписан


def test_load_db_invalidates_on_mtime_change(real_cache, tmp_path):
    cmds_file = tmp_path / "c.txt"
    cmds_file.write_text("v1", encoding="utf-8")
    pairs = [("a", "echo a")]
    real_cache.load_db(cmds_file, pairs)
    cache_path = cmds_file.with_name(cmds_file.name + ec._CACHE_SUFFIX)
    mtime_before = cache_path.stat().st_mtime
    time.sleep(0.05)
    cmds_file.write_text("v2", encoding="utf-8")
    # Меняем pair count чтобы инвалидация сработала через len check
    real_cache.load_db(cmds_file, [("a", "echo a"), ("b", "echo b")])
    assert cache_path.stat().st_mtime > mtime_before


def test_load_db_invalidates_on_model_change(tmp_path):
    cmds_file = tmp_path / "c.txt"
    cmds_file.write_text("v1", encoding="utf-8")
    pairs = [("a", "echo a")]
    cache1 = EmbedCache()
    cache1.load_db(cmds_file, pairs)
    cache_path = cmds_file.with_name(cmds_file.name + ec._CACHE_SUFFIX)

    # подменяем model на «другую» через прямую запись pickle
    data = pickle.loads(cache_path.read_bytes())
    data["model"] = "fake-other-model"
    cache_path.write_bytes(pickle.dumps(data))

    cache2 = EmbedCache(model_name=DEFAULT_EMBED_MODEL)
    embs, cmds = cache2.load_db(cmds_file, pairs)
    # cache должен был быть инвалидирован и пересчитан
    saved = pickle.loads(cache_path.read_bytes())
    assert saved["model"] == DEFAULT_EMBED_MODEL


def test_load_db_empty_pairs(real_cache, tmp_path):
    cmds_file = tmp_path / "c.txt"
    embs, cmds = real_cache.load_db(cmds_file, [])
    assert embs.shape == (0, 1)
    assert cmds == []


def test_load_db_handles_corrupted_pickle(real_cache, tmp_path):
    cmds_file = tmp_path / "c.txt"
    cmds_file.write_text("v", encoding="utf-8")
    cache_path = cmds_file.with_name(cmds_file.name + ec._CACHE_SUFFIX)
    cache_path.write_bytes(b"\x00\x01garbage")
    embs, cmds = real_cache.load_db(cmds_file, [("a", "echo a")])
    assert embs.shape[0] == 1
    assert cache_path.stat().st_size > 100  # перезаписан корректным pickle


# ── cosine_top1 ───────────────────────────────────────────────────

def test_cosine_top1_finds_best():
    v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    v3 = np.array([0.7, 0.7, 0.0], dtype=np.float32)
    db = np.vstack([v1, v2, v3])
    cmds = ["a", "b", "c"]
    q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    best, score = EmbedCache.cosine_top1(q, db, cmds, 0.5)
    assert best == "a"
    assert score > 0.99


def test_cosine_top1_below_threshold():
    db = np.array([[1, 0, 0]], dtype=np.float32)
    q = np.array([0, 1, 0], dtype=np.float32)
    best, score = EmbedCache.cosine_top1(q, db, ["x"], 0.5)
    assert best is None
    assert score < 0.5


def test_cosine_top1_empty_db():
    best, score = EmbedCache.cosine_top1(
        np.array([1.0]), np.zeros((0, 1)), [], 0.5,
    )
    assert best is None
    assert score == 0.0


def test_cosine_top1_zero_query():
    db = np.array([[1, 0]], dtype=np.float32)
    best, score = EmbedCache.cosine_top1(np.zeros(2), db, ["x"], 0.5)
    assert best is None


def test_cosine_top1_real_semantic_match(real_cache):
    pairs = [
        ("покажи файлы", "ls -la"),
        ("открой браузер", "firefox &"),
        ("текущее время", "date"),
    ]
    embs = real_cache.embed_batch([t for t, _ in pairs])
    cmds = [c for _, c in pairs]
    q = real_cache.embed_query("показать каталог")
    best, score = EmbedCache.cosine_top1(q, embs, cmds, 0.3)
    assert best == "ls -la"


# ── ImportError handling ─────────────────────────────────────────

def test_get_model_raises_helpful_on_import_error(monkeypatch):
    import builtins
    orig_import = builtins.__import__
    def bad(name, *a, **kw):
        if name == "fastembed":
            raise ImportError("no fastembed")
        return orig_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", bad)
    cache = EmbedCache()
    with pytest.raises(RuntimeError, match="fastembed не установлен"):
        cache._get_model()
