"""LRU-кэш запросов с атомарной персистентностью на диск.

Структура query_cache.json:
    {"нормализованный запрос": {"command": "...", "source": "gemini|local_ai|semantic",
                                "ts": 1234.5, "hits": 3}, ...}
"""
from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path

CACHE_MAX_DEFAULT = 1000


@dataclass
class _Entry:
    command: str
    source: str   # "gemini" | "local_ai" | "semantic"
    ts: float = field(default_factory=time.time)
    hits: int = 0


class QueryCache:
    """LRU-кэш: ключ = нормализованный запрос, значение = bash-команда."""

    def __init__(self, cache_file: Path, max_size: int = CACHE_MAX_DEFAULT):
        self._file = cache_file
        self._max = max_size
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._load()

    # ── public ────────────────────────────────────────────────
    def get(self, query: str) -> str | None:
        """Возвращает команду по запросу или None. Обновляет LRU-позицию."""
        key = _norm(query)
        entry = self._data.get(key)
        if entry is None:
            return None
        self._data.move_to_end(key)
        entry.hits += 1
        return entry.command

    def put(self, query: str, command: str, source: str) -> None:
        """Сохраняет связку запрос→команда. Вытесняет старейший при переполнении."""
        key = _norm(query)
        self._data[key] = _Entry(command=command, source=source)
        self._data.move_to_end(key)
        if len(self._data) > self._max:
            self._data.popitem(last=False)
        self._flush()

    def __len__(self) -> int:
        return len(self._data)

    # ── persistence ───────────────────────────────────────────
    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            for k, v in raw.items():
                self._data[k] = _Entry(**v)
        except (json.JSONDecodeError, TypeError, KeyError, OSError):
            pass

    def _flush(self) -> None:
        try:
            tmp = self._file.with_suffix(".json.tmp")
            payload = {k: asdict(v) for k, v in self._data.items()}
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._file)
        except OSError:
            pass


def _norm(text: str) -> str:
    return text.strip().lower()
