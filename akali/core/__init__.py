"""Чистая бизнес-логика ассистента (без Qt и без аудио-устройств).

Можно безопасно импортировать в тестах, в CLI-скриптах и в UI.
"""
from .backend import AssistantCore, ReloadStats
from .matcher import MatchResult, cosine_similarity
from .executor import CommandResult

__all__ = [
    "AssistantCore",
    "ReloadStats",
    "MatchResult",
    "CommandResult",
    "cosine_similarity",
]

# QueryRouter and related classes are imported lazily to avoid loading
# heavy dependencies (fastembed, google-generativeai) at startup.
# Use: from akali.core.query_router import QueryRouter
