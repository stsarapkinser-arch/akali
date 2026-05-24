"""Парсинг и слияние источников команд.

Два источника:
  • commands.txt           — ручные/курируемые алиасы.
  • auto_commands.json     — индекс системы из system_indexer.py.

При совпадении команды curated имеет приоритет: его триггеры идут
первыми, авто-триггеры дописываются в хвост (без дубликатов).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


CommandsDict = dict[str, list[str]]


@dataclass
class CommandsBundle:
    """Результат слияния curated + auto."""
    merged: CommandsDict = field(default_factory=dict)
    curated_count: int = 0
    auto_count: int = 0
    auto_sources: dict = field(default_factory=dict)


def parse_curated(commands_txt: str | os.PathLike) -> CommandsDict:
    """Читает commands.txt → {bash_cmd: [trigger, ...]}."""
    result: CommandsDict = {}
    path = Path(commands_txt)
    if not path.exists():
        return result
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "->" not in line:
                continue
            cmd, triggers_str = line.split("->", 1)
            cmd = cmd.strip()
            triggers = [t.strip().lower() for t in triggers_str.split(",") if t.strip()]
            if cmd and triggers:
                result[cmd] = triggers
    return result


def parse_auto(auto_json: str | os.PathLike) -> tuple[CommandsDict, dict]:
    """Читает auto_commands.json → ({bash_cmd: [trigger, ...]}, sources_dict)."""
    path = Path(auto_json)
    if not path.exists():
        return {}, {}
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}, {}
    db: CommandsDict = {}
    for item in payload.get("items", []):
        cmd = item.get("command")
        trigger = item.get("trigger")
        if not cmd or not trigger:
            continue
        bucket = db.setdefault(cmd, [])
        if trigger not in bucket:
            bucket.append(trigger)
    return db, payload.get("sources", {}) or {}


def merge(curated: CommandsDict, auto: CommandsDict) -> CommandsDict:
    """Сливает auto + curated. Curated идёт первым в списке триггеров."""
    merged: CommandsDict = dict(auto)
    for cmd, triggers in curated.items():
        if cmd in merged:
            seen = set(triggers)
            extra = [t for t in merged[cmd] if t not in seen]
            merged[cmd] = triggers + extra
        else:
            merged[cmd] = list(triggers)
    return merged


def build_commands_bundle(
    commands_txt: str | os.PathLike,
    auto_json: str | os.PathLike,
) -> CommandsBundle:
    """Готовит итоговый CommandsBundle для AssistantCore."""
    curated = parse_curated(commands_txt)
    auto, sources = parse_auto(auto_json)
    return CommandsBundle(
        merged=merge(curated, auto),
        curated_count=len(curated),
        auto_count=len(auto),
        auto_sources=sources,
    )
