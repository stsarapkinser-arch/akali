"""Центральные пути проекта.

Один источник истины: куда смотреть за commands.txt, vector_cache.json,
auto_commands.json, иконкой, стилями, скриптом индексатора. Меняем здесь
— меняется везде.
"""
from __future__ import annotations

import os
from pathlib import Path


# ── Корни ────────────────────────────────────────────────────────────
PACKAGE_DIR: Path = Path(__file__).resolve().parent
PROJECT_ROOT: Path = PACKAGE_DIR.parent

# ── Файлы баз и кэшей (живут в корне, чтобы их было удобно
#    редактировать и видеть в git status) ─────────────────────────────
COMMANDS_TXT: Path = PROJECT_ROOT / "commands.txt"
AUTO_COMMANDS_JSON: Path = PROJECT_ROOT / "auto_commands.json"
VECTOR_CACHE_JSON: Path = PROJECT_ROOT / "vector_cache.json"
QUERY_CACHE_JSON: Path = PROJECT_ROOT / "query_cache.json"

# ── Внешние скрипты, которые ассистент дёргает subprocess'ом ─────────
INDEXER_SCRIPT: Path = PROJECT_ROOT / "system_indexer.py"

# ── Vosk-модель ──────────────────────────────────────────────────────
DEFAULT_VOSK_MODEL_DIR: Path = PROJECT_ROOT / "model"

# ── UI-ресурсы (живут внутри пакета) ─────────────────────────────────
UI_RESOURCES_DIR: Path = PACKAGE_DIR / "ui" / "resources"
APP_STYLESHEET: Path = UI_RESOURCES_DIR / "app.qss"
APP_ICON: Path = UI_RESOURCES_DIR / "icon.svg"


def as_str(p: Path) -> str:
    """Удобный шорткат для legacy-кода, ожидающего str-пути."""
    return os.fspath(p)
