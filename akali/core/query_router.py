"""Гибридный роутер запросов с многоуровневым кэшированием.

Пайплайн обработки распознанной фразы:

    ШАГ 0  Быстрый кэш (LRU, exact-match)
           ↓ промах
    ШАГ 1  Семантический поиск FastEmbed (CPU, multilingual)
           ↓ ниже порога
    ШАГ 2  Выбор LLM по режиму + состоянию сети:
             auto         → Gemini → в случае падения фоллбэк на Ollama
             gemini-only  → только Gemini
             ollama-only  → только Ollama
           ↓ валидный ответ
    ШАГ 3  Запись в LRU-кэш

На шаге 2 любой сбой Gemini (регион, rate-limit, 4xx/5xx, timeout)
явно логируется и в auto-режиме приводит к прозрачному переключению
на локальную модель. Пользователь видит причину в логах.

Перед выдачей наружу команда проходит через safety-фильтр и
power_guard-блок (силовые команды через семантику/LLM запрещены —
только точный exact-match phrase→cmd через `power_guard.match`).

Класс не зависит от Qt.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import log as log_mod
from . import power_guard, safety
from .embed_cache import EmbedCache, DEFAULT_EMBED_MODEL
from .llm_client import GeminiClient, OllamaClient, check_internet
from .query_cache import QueryCache

log = logging.getLogger(__name__)

# LLM-режимы
LLM_MODE_AUTO = "auto"            # Gemini → при ошибке fallback на Ollama
LLM_MODE_GEMINI = "gemini-only"   # только Gemini; пал — возвращаем None
LLM_MODE_OLLAMA = "ollama-only"   # только Ollama
LLM_MODES = (LLM_MODE_AUTO, LLM_MODE_GEMINI, LLM_MODE_OLLAMA)

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
        llm_mode: str = LLM_MODE_AUTO,
    ):
        self._cache = QueryCache(cache_file, max_size=CACHE_MAX_ENTRIES)
        self._embed = EmbedCache(model_name=embed_model)
        self._ollama = OllamaClient()
        self._gemini = GeminiClient(gemini_api_key)
        self.semantic_threshold = semantic_threshold
        self._llm_mode = llm_mode if llm_mode in LLM_MODES else LLM_MODE_AUTO

        # Эмбеддинговая база (заполняется через load_db)
        self._db_embs = None      # np.ndarray (N, D) | None
        self._db_cmds: list[str] = []
        self._commands_file: Optional[Path] = None

    # ── Управление состоянием ─────────────────────────────────
    def set_gemini_key(self, api_key: Optional[str]) -> None:
        """Меняет ключ Gemini в работающем роутере."""
        self._gemini = GeminiClient(api_key)

    def set_llm_mode(self, mode: str) -> None:
        """Меняет LLM-режим налету: auto / gemini-only / ollama-only."""
        if mode not in LLM_MODES:
            log.warning("Неизвестный LLM-режим %r, остаюсь на %r", mode, self._llm_mode)
            return
        if mode != self._llm_mode:
            log.info("LLM-режим: %s → %s", self._llm_mode, mode)
            self._llm_mode = mode

    @property
    def llm_mode(self) -> str:
        return self._llm_mode

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

        log_mod.section(log, f"Маршрутизация: {text[:60]!r}")

        # ── ШАГ 0: Быстрый кэш ──
        cached = self._cache.get(text)
        if cached:
            log_mod.step(log, "кэш hit: %s", cached)
            if not self._gate(cached, "cache"):
                return None
            return cached
        log_mod.step(log, "кэш miss")

        # ── ШАГ 1: FastEmbed ──
        semantic_cmd = self._semantic_search(text)
        if semantic_cmd:
            log_mod.step(log, "semantic hit: %s", semantic_cmd)
            if not self._gate(semantic_cmd, "semantic"):
                return None
            self._cache.put(text, semantic_cmd, "semantic")
            return semantic_cmd
        log_mod.step(log, "semantic miss (< %.2f)", self.semantic_threshold)

        # ── ШАГ 2: LLM (с фоллбэком) ──
        llm_cmd, source = self._query_llm_with_fallback(text)
        if not llm_cmd or llm_cmd.strip() in ("echo error", ""):
            log_mod.step(log, "%s: пусто/echo error — отклонено", source)
            return None

        if not self._gate(llm_cmd, source):
            return None

        # ── ШАГ 3: Кэш ──
        self._cache.put(text, llm_cmd, source)
        log_mod.success(log, "[%s] %s", source, llm_cmd)
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

    def _query_llm_with_fallback(self, text: str) -> tuple[Optional[str], str]:
        """Умный LLM-роутинг с явными фоллбэками.

        Режимы:
          • LLM_MODE_OLLAMA          → сразу локальная модель
          • LLM_MODE_GEMINI          → только Gemini, никакого fallback'а
          • LLM_MODE_AUTO (default) → сначала Gemini, при проблеме → Ollama

        Каждая попытка логируется. При падении Gemini пишется явная причина.
        """
        mode = self._llm_mode

        if mode == LLM_MODE_OLLAMA:
            log_mod.step(log, "режим=ollama-only, обращаюсь к локальной модели")
            return self._try_ollama(text), "ollama"

        # 1) Сеть?
        has_internet = check_internet()
        if not has_internet:
            if mode == LLM_MODE_GEMINI:
                log_mod.error_box(
                    log, "Gemini недоступен",
                    reason="нет интернета (TCP 8.8.8.8:53 не отвечает)",
                    suggestion="подключи интернет или переключись на режим ollama-only",
                )
                return None, "gemini"
            log_mod.step(log, "сети нет → пропускаю Gemini, иду к Ollama")
            return self._try_ollama(text), "ollama"

        # 2) Ключ есть?
        if not (self._gemini and self._gemini.has_key):
            if mode == LLM_MODE_GEMINI:
                log_mod.error_box(
                    log, "Gemini недоступен",
                    reason="не задан API-ключ (Настройки → Gemini API key)",
                    suggestion="пропиши ключ или переключись на режим ollama-only",
                )
                return None, "gemini"
            log_mod.step(log, "Gemini ключ не задан → иду к Ollama")
            return self._try_ollama(text), "ollama"

        # 3) Дёргаем Gemini, ловим ошибки
        gemini_cmd, gemini_err = self._try_gemini(text)
        if gemini_cmd:
            return gemini_cmd, "gemini"

        # 4) Gemini отвалился. Что делать?
        if mode == LLM_MODE_GEMINI:
            log_mod.error_box(
                log, "Gemini не вернул команду",
                reason=gemini_err or "пустой ответ",
                suggestion="переключись на режим ollama-only",
            )
            return None, "gemini"

        # auto-режим: переключаемся на Ollama
        log_mod.warn_box(
            log, "Gemini недоступен → переключаюсь на локальную модель",
            body=gemini_err or "пустой ответ",
        )
        ollama_cmd = self._try_ollama(text)
        if ollama_cmd:
            return ollama_cmd, "ollama"
        log_mod.error_box(
            log, "Обе модели не ответили",
            reason=f"gemini: {gemini_err or 'пусто'}; ollama: пустой ответ",
            suggestion="проверь `ollama serve` и наличие модели",
        )
        return None, "ollama"

    def _try_gemini(self, text: str) -> tuple[Optional[str], str]:
        """Возвращает (команда, причина-ошибки). Команда=None при любом обломе."""
        log_mod.step(log, "пробую Gemini (gemini-2.5-flash)…")
        try:
            cmd = self._gemini.query(text)
        except Exception as e:  # noqa: BLE001
            reason, _ = log_mod.format_error(e)
            log_mod.step(log, "gemini исключение: %s", reason)
            return None, reason
        if cmd:
            log_mod.step(log, "gemini ответил: %s", cmd[:80])
            return cmd, ""
        reason = getattr(self._gemini, "last_error", "") or "пустой ответ"
        log_mod.step(log, "gemini нет ответа: %s", reason)
        return None, reason

    def _try_ollama(self, text: str) -> Optional[str]:
        log_mod.step(log, "пробую Ollama (qwen2.5-coder:1.5b)…")
        try:
            cmd = self._ollama.query(text)
        except Exception as e:  # noqa: BLE001
            reason, _ = log_mod.format_error(e)
            log_mod.step(log, "ollama исключение: %s", reason)
            return None
        if cmd:
            log_mod.step(log, "ollama ответил: %s", cmd[:80])
            return cmd
        log_mod.step(log, "ollama нет ответа")
        return None

    # ── Удобства для UI ───────────────────────────────────────
    @property
    def cache_size(self) -> int:
        return len(self._cache)
