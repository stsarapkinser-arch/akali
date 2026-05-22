"""Гибридный роутер запросов с многоуровневым кэшированием.

Пайплайн обработки распознанной фразы:

    ШАГ 0  Быстрый кэш (LRU, exact-match)
           ↓ промах
    ШАГ 1  Семантический поиск FastEmbed (CPU, multilingual)
           ↓ ниже порога
    ШАГ 2  Проверка интернета (socket 8.8.8.8:53, ≤1.5 сек)
           ↓
    ШАГ 3А Нет сети  → OllamaClient (qwen2.5-coder:1.5b, локально)
    ШАГ 3Б Есть сеть → GeminiClient (gemini-2.5-flash, облако)
           ↓ валидный ответ
    ШАГ 4  Запись в LRU-кэш

Класс не зависит от Qt и не импортирует его — используется из core.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .embed_cache import EmbedCache, DEFAULT_EMBED_MODEL
from .llm_client import GeminiClient, OllamaClient, check_internet
from .query_cache import QueryCache

log = logging.getLogger(__name__)

# ── Пороги и размеры ─────────────────────────────────────────
SEMANTIC_THRESHOLD = 0.82    # минимальная косинусная похожесть для матча (0.72 давало ложные срабатывания)
CACHE_MAX_ENTRIES = 1000     # максимум записей в LRU-кэше
EMBED_MODEL = DEFAULT_EMBED_MODEL

# Команды, затрагивающие питание/сеанс — только exact match, ни embed, ни LLM
POWER_COMMANDS: frozenset[str] = frozenset({
    "systemctl poweroff",
    "systemctl reboot",
    "systemctl hibernate",
    "systemctl suspend",
    "shutdown -h now",
    "reboot",
    "poweroff",
})


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
        # GeminiClient сам ищет ключ в .env если gemini_api_key=None
        self._gemini = GeminiClient(gemini_api_key)
        self.semantic_threshold = semantic_threshold

        # Эмбеддинговая база (заполняется через load_db)
        self._db_embs = None      # np.ndarray (N, D) | None
        self._db_cmds: list[str] = []
        self._commands_file: Optional[Path] = None

    # ── Загрузка базы ──────────────────────────────────────────
    def load_db(
        self,
        commands_db: dict[str, list[str]],
        commands_file: Path,
    ) -> None:
        """Строит/перезагружает индекс FastEmbed из базы команд.

        Args:
            commands_db:   {bash_cmd: [trigger, ...]}
            commands_file: путь к commands.txt (для инвалидации кэша по mtime)
        """
        self._commands_file = commands_file
        pairs: list[tuple[str, str]] = [
            (trigger, cmd)
            for cmd, triggers in commands_db.items()
            for trigger in triggers
        ]
        try:
            self._db_embs, self._db_cmds = self._embed.load_db(commands_file, pairs)
            log.info("QueryRouter: загружено %d триггеров в индекс FastEmbed", len(pairs))
        except Exception as e:  # noqa: BLE001
            log.warning("QueryRouter: не удалось построить FastEmbed-индекс: %s", e)
            self._db_embs = None
            self._db_cmds = []

    # ── Главный метод ─────────────────────────────────────────
    def route(self, text: str) -> Optional[str]:
        """Обрабатывает запрос через полный пайплайн. Возвращает bash-команду или None.

        ШАГ 0 → ШАГ 1 → ШАГ 2 → ШАГ 3 → ШАГ 4
        """
        text = text.strip()
        if not text or len(text) < 3:
            return None

        # ── ШАГ 0: Быстрый кэш ────────────────────────────────
        cached = self._cache.get(text)
        if cached:
            log.debug("ШАГ 0 (кэш): %r → %s", text, cached)
            return cached

        # ── ШАГ 1: FastEmbed семантический поиск ──────────────
        semantic_cmd = self._semantic_search(text)
        if semantic_cmd:
            log.debug("ШАГ 1 (semantic): %r → %s", text, semantic_cmd)
            # Семантические совпадения тоже кэшируем для следующего раза
            self._cache.put(text, semantic_cmd, "semantic")
            return semantic_cmd

        # ── ШАГ 2: Проверка сети ──────────────────────────────
        has_internet = check_internet()
        log.debug("ШАГ 2 (network): %s", "есть" if has_internet else "нет")

        # ── ШАГ 3: Маршрутизация к LLM ────────────────────────
        llm_cmd, source = self._query_llm(text, has_internet)

        if not llm_cmd or llm_cmd.strip() in ("echo error", ""):
            log.debug("ШАГ 3 (%s): нет ответа или echo error", source)
            return None

        # Блокируем силовые команды из LLM/семантики — только explicit exact match
        if llm_cmd.strip() in POWER_COMMANDS:
            log.warning("QueryRouter: LLM вернул силовую команду %r — заблокировано", llm_cmd)
            return None

        log.debug("ШАГ 3 (%s): %r → %s", source, text, llm_cmd)

        # ── ШАГ 4: Запись в кэш ───────────────────────────────
        self._cache.put(text, llm_cmd, source)
        return llm_cmd

    # ── Внутренние методы ─────────────────────────────────────
    def _semantic_search(self, text: str) -> Optional[str]:
        """FastEmbed поиск по базе. Возвращает команду или None."""
        if self._db_embs is None or len(self._db_cmds) == 0:
            return None
        try:
            qvec = self._embed.embed_query(text)
            cmd, score = EmbedCache.cosine_top1(
                qvec, self._db_embs, self._db_cmds, self.semantic_threshold
            )
            if cmd:
                # Силовые команды не отдаём через семантику — только explicit exact match
                if cmd.strip() in POWER_COMMANDS:
                    log.warning(
                        "QueryRouter: семантика нашла силовую команду %r (score=%.3f) — заблокировано",
                        cmd, score,
                    )
                    return None
                log.debug("FastEmbed score=%.3f для %r", score, text)
            return cmd
        except Exception as e:  # noqa: BLE001
            log.warning("FastEmbed ошибка: %s", e)
            return None

    def _query_llm(self, text: str, has_internet: bool) -> tuple[Optional[str], str]:
        """Отправляет запрос в нужную LLM. Возвращает (команда | None, источник)."""
        # Путь Б: Gemini (если есть интернет и ключ настроен)
        if has_internet and self._gemini:
            cmd = self._gemini.query(text)
            if cmd:
                return cmd, "gemini"

        # Путь А: Ollama (локально, всегда)
        cmd = self._ollama.query(text)
        return cmd, "local_ai"
