"""Сопоставление голосовой фразы и команды.

Два механизма поиска:
  • fuzzy_match   — difflib.SequenceMatcher по триггерам.
  • vector_search — cosine по эмбеддингам (ollama) из VectorCache.

И тот, и другой возвращают MatchResult с уверенностью; если ниже
заданного порога — found=False, но max-уверенность всё равно записана
(полезно для логов «почему не нашли»).
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class MatchResult:
    """Что нашли (или не нашли) по голосовой фразе."""
    cmd: Optional[str] = None
    confidence: float = 0.0
    trigger: str = ""
    method: str = ""  # "fuzzy", "vector" или ""

    @property
    def found(self) -> bool:
        return self.cmd is not None


def cosine_similarity(v1, v2) -> float:
    """Косинусная похожесть двух векторов; граничные случаи дают 0.0."""
    if not v1 or not v2:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def fuzzy_match(text: str, commands_db: dict[str, list[str]],
                threshold: float) -> MatchResult:
    """Возвращает лучший fuzzy-матч по триггерам."""
    text = text.lower()
    best_cmd: Optional[str] = None
    best_trigger = ""
    max_ratio = 0.0
    for cmd, triggers in commands_db.items():
        for trigger in triggers:
            ratio = difflib.SequenceMatcher(None, text, trigger).ratio()
            if ratio > max_ratio:
                max_ratio = ratio
                best_cmd = cmd
                best_trigger = trigger
    if max_ratio >= threshold:
        return MatchResult(cmd=best_cmd, confidence=max_ratio,
                           trigger=best_trigger, method="fuzzy")
    return MatchResult(confidence=max_ratio, trigger=best_trigger)


def vector_search(user_vec: list[float], cache_items: Iterable[dict],
                  threshold: float) -> MatchResult:
    """user_vec — эмбеддинг входной фразы; cache_items — items из VectorCache."""
    if not user_vec:
        return MatchResult()
    best_cmd: Optional[str] = None
    best_trigger = ""
    max_score = 0.0
    for item in cache_items:
        score = cosine_similarity(user_vec, item.get("vector", []))
        if score > max_score:
            max_score = score
            best_cmd = item.get("command")
            best_trigger = item.get("trigger", "")
    if max_score >= threshold:
        return MatchResult(cmd=best_cmd, confidence=max_score,
                           trigger=best_trigger, method="vector")
    return MatchResult(confidence=max_score, trigger=best_trigger)


def detect_wake_word(words: list[str], wake_words: Iterable[str],
                     threshold: float) -> int:
    """Возвращает индекс слова, на котором сработал wake-word, или -1."""
    for i, word in enumerate(words):
        for ww in wake_words:
            if difflib.SequenceMatcher(None, word, ww).ratio() >= threshold:
                return i
    return -1


def is_reindex_phrase(text: str, triggers: Iterable[str]) -> bool:
    low = text.lower()
    return any(rt in low for rt in triggers)
