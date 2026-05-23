"""Точный матчер команд питания (poweroff / reboot / suspend / hibernate).

Эти команды НИКОГДА не идут через семантику или LLM: только полное (после
нормализации регистра + пробелов) совпадение распознанной фразы с одной
из заранее заданных. Это защита от случайного выключения системы из-за
шума или галлюцинаций модели.

Если хочешь добавить новую фразу — допиши её в `_PHRASES`. Триггеры не
читаются из `commands.txt`, чтобы редактирование командного файла не
могло случайно расширить «зону поражения».
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PowerAction:
    """Одно действие питания с фиксированной командой и точным списком фраз."""
    bash: str
    phrases: frozenset[str]


# Все фразы должны быть строго в нижнем регистре, без знаков пунктуации,
# без слова-активатора («акали»), пробелы внутри схлопнуты.
_ACTIONS: tuple[PowerAction, ...] = (
    PowerAction(
        bash="systemctl poweroff",
        phrases=frozenset({
            "выключи компьютер",
            "выключи систему",
            "выключи питание",
            "выключение системы",
            "завершение работы",
        }),
    ),
    PowerAction(
        bash="systemctl reboot",
        phrases=frozenset({
            "перезагрузи компьютер",
            "перезагрузи систему",
            "перезагрузка системы",
            "перезагрузка компьютера",
        }),
    ),
    PowerAction(
        bash="systemctl suspend",
        phrases=frozenset({
            "уйди в сон",
            "режим сна",
            "спящий режим",
            "усыпи компьютер",
        }),
    ),
    PowerAction(
        bash="systemctl hibernate",
        phrases=frozenset({
            "гибернация",
            "уйди в гибернацию",
            "режим гибернации",
        }),
    ),
)

# Точечные регулярки для нормализации
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+", re.UNICODE)


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text


def match(text: str) -> str | None:
    """Возвращает bash-команду питания при полном совпадении, иначе None."""
    if not text:
        return None
    norm = _normalize(text)
    for action in _ACTIONS:
        if norm in action.phrases:
            log.info("power_guard: точное совпадение «%s» → %s", norm, action.bash)
            return action.bash
    return None


def is_power_command(command: str) -> bool:
    """True если bash-команда — это операция питания (для блока в LLM/semantic)."""
    if not command:
        return False
    cmd = command.strip().lower()
    return any(cmd == action.bash.lower() for action in _ACTIONS) or any(
        cmd.startswith(prefix) for prefix in (
            "systemctl poweroff", "systemctl reboot",
            "systemctl suspend", "systemctl hibernate",
            "poweroff", "reboot", "shutdown", "halt", "init 0", "init 6",
        )
    )


def all_phrases() -> list[tuple[str, str]]:
    """Список всех (фраза, bash-команда) для отображения в UI."""
    return [(phrase, action.bash) for action in _ACTIONS for phrase in sorted(action.phrases)]
