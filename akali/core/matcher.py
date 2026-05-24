"""Утилиты сопоставления для wake-word и reindex-фраз.

Логика поиска команд по голосу полностью переехала в `query_router.py`
(FastEmbed + кэш + LLM). Здесь остались только хелперы для распознавания
«слова-активатора» и фраз «переиндексируй».
"""
from __future__ import annotations

import difflib
from typing import Iterable


def detect_wake_word(words: list[str], wake_words: Iterable[str],
                     threshold: float = 0.75) -> int:
    """Возвращает индекс слова, на котором сработал wake-word, или -1.

    Сравниваем каждое слово фразы с каждым wake-word через SequenceMatcher;
    если максимум превысил `threshold` — возвращаем индекс. Эта функция
    специально использует difflib, а не семантику, чтобы wake-word работал
    мгновенно (без эмбеддингов и без LLM).
    """
    for i, word in enumerate(words):
        for ww in wake_words:
            if difflib.SequenceMatcher(None, word, ww).ratio() >= threshold:
                return i
    return -1


def is_reindex_phrase(text: str, triggers: Iterable[str]) -> bool:
    """True если в тексте есть один из триггеров переиндексации."""
    low = text.lower()
    return any(rt in low for rt in triggers)
