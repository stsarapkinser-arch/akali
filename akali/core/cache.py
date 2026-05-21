"""Дисковый кэш эмбеддингов.

Файл `vector_cache.json` содержит:
    {"model": "all-minilm", "items": [
        {"command": "...", "trigger": "...", "vector": [...]}
    ]}

Кэш инвалидируется при смене модели. Запись атомарная (`.tmp` →
`os.replace`).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


EmbeddingFn = Callable[[str], list[float]]


@dataclass
class CacheStats:
    reused: int = 0
    built: int = 0
    total: int = 0


class VectorCache:
    """Поверх vector_cache.json — read/build/write."""

    def __init__(self, cache_file: str | os.PathLike, vector_model: str):
        self.cache_file = Path(cache_file)
        self.vector_model = vector_model
        self.items: list[dict] = []

    # ---------- IO ----------
    def _read_cached_vectors(self) -> dict[tuple[str, str], list[float]]:
        if not self.cache_file.exists():
            return {}
        try:
            with self.cache_file.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if payload.get("model") != self.vector_model:
                return {}
            return {
                (it["command"], it["trigger"]): it["vector"]
                for it in payload.get("items", [])
                if "command" in it and "trigger" in it and "vector" in it
            }
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            return {}

    def _write_atomic(self) -> None:
        try:
            tmp = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(
                    {"model": self.vector_model, "items": self.items},
                    f, ensure_ascii=False,
                )
            os.replace(tmp, self.cache_file)
        except OSError:
            # Не критично — кэш просто не сохранится, продолжаем работу.
            pass

    # ---------- Build ----------
    def build(self, commands_db: dict[str, list[str]],
              embedder: EmbeddingFn) -> CacheStats:
        """Достраивает кэш: переиспользует совпавшие, эмбеддит новые."""
        cached = self._read_cached_vectors()
        items: list[dict] = []
        reused = 0
        built = 0
        for cmd, triggers in commands_db.items():
            for trigger in triggers:
                key = (cmd, trigger)
                if key in cached:
                    items.append({"command": cmd, "trigger": trigger,
                                  "vector": cached[key]})
                    reused += 1
                else:
                    vec = embedder(trigger)
                    if vec:
                        items.append({"command": cmd, "trigger": trigger,
                                      "vector": vec})
                        built += 1
        self.items = items
        if built > 0 or len(items) != len(cached):
            self._write_atomic()
        return CacheStats(reused=reused, built=built, total=len(items))
