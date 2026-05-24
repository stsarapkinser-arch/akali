"""Чистая бизнес-логика ассистента (без Qt и без аудио-устройств).

Можно безопасно импортировать в тестах, в CLI-скриптах и в UI.

Тяжёлые модули (QueryRouter с fastembed, llm_client с google-generativeai,
system_check, audio_worker) импортируются лениво из мест использования.
"""
from .backend import AssistantCore, ReloadStats
from .executor import CommandResult

__all__ = [
    "AssistantCore",
    "ReloadStats",
    "CommandResult",
]
