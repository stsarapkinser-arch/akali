"""Гибридный роутер запросов с многоуровневым кэшированием.

Пайплайн обработки распознанной фразы:

    ШАГ 0  Быстрый кэш (LRU, exact-match)
           ↓ промах
    ШАГ 1  Семантический поиск FastEmbed (CPU, multilingual)
           ↓ ниже порога
    ШАГ 2  Проверка интернета (TCP 8.8.8.8:53 ≤1.5 сек, кэш 30 сек)
           ↓
    ШАГ 3А Нет сети  → OllamaClient (qwen2.5-coder:1.5b, локально)
    ШАГ 3Б Есть сеть → GeminiClient (gemini-2.5-flash, облако)
           ↓ валидный ответ
    ШАГ 4  Запись в LRU-кэш

Перед выдачей наружу команда проходит через safety-фильтр и
power_guard-блок (силовые команды через семантику/LLM запрещены —
только точный exact-match phrase→cmd через `power_guard.match`).

Класс не зависит от Qt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import power_guard, safety
from .embed_cache import EmbedCache, DEFAULT_EMBED_MODEL
from .llm_client import GeminiClient, OllamaClient, check_internet
from .query_cache import QueryCache

log = logging.getLogger(__name__)

# ── Пороги и размеры ─────────────────────────────────────────
SEMANTIC_THRESHOLD = 0.85    # ТЗ: >85% для семантики
CACHE_MAX_ENTRIES = 1000
EMBED_MODEL = DEFAULT_EMBED_MODEL


class QueryRouter:
    """Гибридный маршрутизатор: кэш → FastEmbed → LLM (local/cloud)."""

    def __init__(
        self,
        cache_file: Path,
        gemini_api_key: Optional[str] = None,
        semantic_threshold: float = SEMANTIC_THRESHOLD,
        embed_model: str = EMBED_MODEL,
    ):
        self._cache = QueryCache(cache_file, max_size=CACHE_MAX_ENTRIES)
        self._embed = EmbedCache(model_name=embed_model)
        self._ollama = OllamaClient()
        self._gemini = GeminiClient(gemini_api_key)
        self.semantic_threshold = semantic_threshold

        # Эмбеддинговая база (заполняется через load_db)
        self._db_embs = None      # np.ndarray (N, D) | None
        self._db_cmds: list[str] = []
        self._commands_file: Optional[Path] = None

    # ── Управление состоянием ─────────────────────────────────
    def set_gemini_key(self, api_key: Optional[str]) -> None:
        """Меняет ключ Gemini в работающем роутере."""
        self._gemini = GeminiClient(api_key)

    def load_db(
        self,
        commands_db: dict[str, list[str]],
        commands_file: Path,
    ) -> None:
        """Строит/перезагружает индекс FastEmbed из базы команд.

        Силовые команды (poweroff/reboot/suspend/hibernate) из базы НЕ
        попадают в семантический индекс — они работают только через
        `power_guard.match` с exact-match.
        """
        self._commands_file = commands_file
        pairs: list[tuple[str, str]] = []
        for cmd, triggers in commands_db.items():
            if power_guard.is_power_command(cmd):
                continue
            for trigger in triggers:
                pairs.append((trigger, cmd))
        try:
            self._db_embs, self._db_cmds = self._embed.load_db(commands_file, pairs)
            log.info("Семантический индекс: %d триггеров", len(pairs))
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось построить FastEmbed-индекс: %s", e)
            self._db_embs = None
            self._db_cmds = []

    # ── Главный метод ─────────────────────────────────────────
    def route(self, text: str) -> Optional[str]:
        """Прогоняет запрос через пайплайн. Возвращает bash-команду или None."""
        text = (text or "").strip()
        if len(text) < 3:
            return None

        # ── ШАГ 0: Быстрый кэш ──
        cached = self._cache.get(text)
        if cached:
            log.debug("ШАГ 0 кэш: %r → %s", text, cached)
            if not self._gate(cached, "cache"):
                return None
            return cached

        # ── ШАГ 1: FastEmbed ──
        semantic_cmd = self._semantic_search(text)
        if semantic_cmd:
            log.debug("ШАГ 1 semantic: %r → %s", text, semantic_cmd)
            if not self._gate(semantic_cmd, "semantic"):
                return None
            self._cache.put(text, semantic_cmd, "semantic")
            return semantic_cmd

        # ── ШАГ 2: Сеть ──
        has_internet = check_internet()
        log.debug("ШАГ 2 сеть: %s", "есть" if has_internet else "нет")

        # ── ШАГ 3: LLM ──
        llm_cmd, source = self._query_llm(text, has_internet)
        if not llm_cmd or llm_cmd.strip() in ("echo error", ""):
            log.debug("ШАГ 3 %s: пусто или echo error", source)
            return None

        if not self._gate(llm_cmd, source):
            return None

        # ── ШАГ 4: Кэш ──
        self._cache.put(text, llm_cmd, source)
        log.debug("ШАГ 4 кэш: %r → %s (источник %s)", text, llm_cmd, source)
        return llm_cmd

    # ── Проверки безопасности ─────────────────────────────────
    def _gate(self, command: str, source: str) -> bool:
        """Возвращает False если команду нельзя выпускать наружу."""
        if power_guard.is_power_command(command):
            log.warning("Источник %r вернул силовую команду %r — заблокировано "
                        "(power: только exact-match через power_guard)",
                        source, command)
            return False
        verdict = safety.inspect(command)
        if not verdict.safe:
            safety.report(source, command, verdict)
            return False
        return True

    # ── Внутренние методы ─────────────────────────────────────
    def _semantic_search(self, text: str) -> Optional[str]:
        if self._db_embs is None or len(self._db_cmds) == 0:
            return None
        try:
            qvec = self._embed.embed_query(text)
            cmd, score = EmbedCache.cosine_top1(
                qvec, self._db_embs, self._db_cmds, self.semantic_threshold
            )
            if cmd:
                log.debug("FastEmbed score=%.3f для %r", score, text)
            return cmd
        except Exception as e:  # noqa: BLE001
            log.warning("FastEmbed ошибка: %s", e)
            return None

    def _query_llm(self, text: str, has_internet: bool) -> tuple[Optional[str], str]:
        # Путь Б: Gemini (если есть интернет и ключ настроен)
        if has_internet and self._gemini and self._gemini.has_key:
            cmd = self._gemini.query(text)
            if cmd:
                return cmd, "gemini"
        # Путь А: Ollama (локально, всегда)
        cmd = self._ollama.query(text)
        return cmd, "ollama"

    # ── Удобства для UI ───────────────────────────────────────
    @property
    def cache_size(self) -> int:
        return len(self._cache)
