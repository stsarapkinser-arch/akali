"""Фасад AssistantCore — единый объект для UI и audio_worker.

Сам не делает «тяжёлой работы»: парсингом командой ведает `db.py`,
векторами — `cache.py`, поиском — `matcher.py`, выполнением — `executor.py`.
Тут только склейка и состояние (текущая база команд, текущий кэш,
пороги, wake-words).
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import ollama

from .. import paths
from . import db, matcher
from .cache import VectorCache
from .executor import CommandResult, execute
from .matcher import MatchResult


def _lazy_import_router():
    from .query_router import QueryRouter  # noqa: WPS433
    return QueryRouter


# === Пороги по умолчанию ===
SIMILARITY_THRESHOLD = 0.70
VECTOR_THRESHOLD = 0.55
WAKE_THRESHOLD = 0.75
ACTIVE_WINDOW_SECONDS = 5.0

DEFAULT_WAKE_WORDS = ("компьютер", "ассистент", "акали")
DEFAULT_REINDEX_TRIGGERS = (
    "переиндексируй",
    "обнови команд",
    "пересканируй систем",
)
DEFAULT_VECTOR_MODEL = "all-minilm"


@dataclass
class ReloadStats:
    commands_total: int = 0
    curated_count: int = 0
    auto_count: int = 0
    vectors_reused: int = 0
    vectors_built: int = 0
    vector_total: int = 0
    auto_sources: dict = field(default_factory=dict)


class AssistantCore:
    """Состояние ассистента: база команд, кэш, настройки поиска."""

    def __init__(self,
                 commands_file: str | os.PathLike = paths.COMMANDS_TXT,
                 auto_commands_file: str | os.PathLike = paths.AUTO_COMMANDS_JSON,
                 vector_cache_file: str | os.PathLike = paths.VECTOR_CACHE_JSON,
                 indexer_script: str | os.PathLike = paths.INDEXER_SCRIPT,
                 vector_model: str = DEFAULT_VECTOR_MODEL):
        self.commands_file = Path(commands_file)
        self.auto_commands_file = Path(auto_commands_file)
        self.vector_cache_file = Path(vector_cache_file)
        self.indexer_script = Path(indexer_script)
        self.vector_model = vector_model

        # Настройки (UI может править)
        self.fuzzy_threshold = SIMILARITY_THRESHOLD
        self.vector_threshold = VECTOR_THRESHOLD
        self.wake_threshold = WAKE_THRESHOLD
        self.wake_words = list(DEFAULT_WAKE_WORDS)
        self.reindex_triggers = list(DEFAULT_REINDEX_TRIGGERS)

        # Состояние
        self.commands_db: dict[str, list[str]] = {}
        self.auto_sources: dict = {}
        self._curated_count = 0
        self._auto_count = 0
        self._cache = VectorCache(self.vector_cache_file, self.vector_model)

        # Ollama availability check (кэшируется)
        self._ollama_available: bool | None = None
        self._ollama_error_logged = False

        # QueryRouter (инициализируется отдельно через init_router)
        self._router: Optional[object] = None

    # ── Совместимость с тестами / старым кодом ───────────────────────
    @property
    def base_dir(self) -> str:
        return os.fspath(self.commands_file.parent)

    @property
    def vector_cache(self) -> list[dict]:
        return self._cache.items

    @staticmethod
    def cosine_similarity(v1, v2) -> float:
        return matcher.cosine_similarity(v1, v2)

    def build_commands_db(self) -> dict[str, list[str]]:
        bundle = db.build_commands_bundle(self.commands_file, self.auto_commands_file)
        self.auto_sources = bundle.auto_sources
        self._curated_count = bundle.curated_count
        self._auto_count = bundle.auto_count
        return bundle.merged

    def load_or_build_vector_cache(self, commands_dict: dict[str, list[str]]) -> tuple[int, int]:
        """Возвращает (reused, built); сохраняет items в self._cache."""
        # Если у Core поменялся vector_model, переинициализируем cache
        if self._cache.vector_model != self.vector_model:
            self._cache = VectorCache(self.vector_cache_file, self.vector_model)
        stats = self._cache.build(commands_dict, self.get_embedding)
        return stats.reused, stats.built

    # ── Эмбеддинги ────────────────────────────────────────────────────
    def get_embedding(self, text: str) -> list[float]:
        try:
            resp = ollama.embeddings(model=self.vector_model, prompt=text)
            return resp.get("embedding", [])
        except Exception as e:  # noqa: BLE001
            # Логируем ошибку один раз при первом отказе Ollama
            if not self._ollama_error_logged:
                print(
                    f"⚠️ Ollama ошибка (vector-поиск отключён): {e}\n"
                    f"   Используется только fuzzy-matching.\n"
                    f"   Для включения: ollama pull {self.vector_model} && ollama serve",
                    file=sys.stderr)
                self._ollama_error_logged = True
            return []

    # ── Lifecycle ────────────────────────────────────────────────────
    def reload(self) -> ReloadStats:
        """Полная пересборка: парс commands → парс auto → построить векторы."""
        self.commands_db = self.build_commands_db()
        reused, built = self.load_or_build_vector_cache(self.commands_db)
        if self._router is not None:
            try:
                self._router.load_db(self.commands_db, self.commands_file)
            except Exception:  # noqa: BLE001
                pass
        return ReloadStats(
            commands_total=len(self.commands_db),
            curated_count=self._curated_count,
            auto_count=self._auto_count,
            vectors_reused=reused,
            vectors_built=built,
            vector_total=len(self.vector_cache),
            auto_sources=dict(self.auto_sources),
        )

    def init_router(self, gemini_api_key: Optional[str] = None) -> None:
        """Инициализирует QueryRouter. Вызывается из app.py после reload()."""
        QueryRouter = _lazy_import_router()
        self._router = QueryRouter(
            cache_file=paths.QUERY_CACHE_JSON,
            gemini_api_key=gemini_api_key,
        )
        if self.commands_db:
            self._router.load_db(self.commands_db, self.commands_file)

    def route_with_llm(self, text: str) -> Optional[str]:
        """Пропускает запрос через QueryRouter (кэш → FastEmbed → LLM)."""
        if self._router is None:
            return None
        try:
            return self._router.route(text)
        except Exception:  # noqa: BLE001
            return None

    # ── Поиск ────────────────────────────────────────────────────────
    def fuzzy_match(self, text: str) -> MatchResult:
        return matcher.fuzzy_match(text, self.commands_db, self.fuzzy_threshold)

    def vector_search(self, text: str) -> MatchResult:
        return matcher.vector_search(self.get_embedding(text),
                                     self.vector_cache,
                                     self.vector_threshold)

    def find(self, text: str) -> MatchResult:
        m = self.fuzzy_match(text)
        if m.found:
            return m
        return self.vector_search(text)

    # ── Wake-word ────────────────────────────────────────────────────
    def detect_wake_word(self, words: list[str]) -> int:
        return matcher.detect_wake_word(words, self.wake_words, self.wake_threshold)

    def is_reindex_phrase(self, text: str) -> bool:
        return matcher.is_reindex_phrase(text, self.reindex_triggers)

    # ── Выполнение ────────────────────────────────────────────────────
    def execute(self, cmd: str, timeout: float = 15.0) -> CommandResult:
        return execute(cmd, timeout=timeout)

    # ── Реиндекс системы ─────────────────────────────────────────────
    def reindex_system(self, timeout: float = 120.0) -> dict:
        """Зовёт system_indexer.py, потом перестраивает базу."""
        result: dict = {"ok": False, "error": "", "stats": None,
                        "stdout": "", "stderr": ""}
        indexer = Path(self.indexer_script)
        if not indexer.exists():
            result["error"] = f"system_indexer.py не найден: {indexer}"
            return result
        try:
            proc = subprocess.run(
                [sys.executable, os.fspath(indexer), "--quiet"],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            result["error"] = f"индексатор не уложился в {timeout:.0f}с"
            return result
        result["stdout"] = (proc.stdout or "").strip()
        result["stderr"] = (proc.stderr or "").strip()
        if proc.returncode != 0:
            result["error"] = f"индексатор код {proc.returncode}: {result['stderr'][:200]}"
            return result
        result["stats"] = self.reload()
        result["ok"] = True
        return result
