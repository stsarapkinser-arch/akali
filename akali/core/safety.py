"""Фильтр опасных bash-команд от LLM и семантики.

Сюда попадают все команды, которые роутер собирается выполнить. Если
команда матчит хотя бы один регулярный паттерн ниже — она блокируется и
наружу уходит событие через `safety_event`, чтобы UI и логи показали
«модель X пыталась подсунуть опасную команду».

Здесь намеренно консервативный список: только однозначно деструктивные
паттерны (удаление корня, форматирование, fork-bomb, выкачка скриптов в
shell). Обычные `rm file.txt`, `dd if=/dev/zero of=image.bin`,
`pkill` остаются разрешёнными.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Регулярки опасных паттернов. Все ищутся в нижнем регистре по полной команде.
_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    # rm -rf / или rm -rf /path  (любые опасные флаги -r/-f в любом порядке)
    (r"\brm\s+(?:-[a-z]*[rf]+[a-z]*\s+)+(?:/|/\*|\$home|~)(?:\s|$|;|&|\|)",
     "рекурсивное удаление корня / домашней директории"),
    (r"\brm\s+(?:-[a-z]*[rf]+[a-z]*\s+)+/[a-z]\S*",
     "рекурсивное удаление абсолютного пути"),
    (r"\bmkfs\.[a-z0-9]+\b",
     "форматирование файловой системы"),
    (r"\bdd\s+.*\bof=/dev/(sd|nvme|mmcblk|hd|vd)",
     "запись dd на блочное устройство"),
    (r">\s*/dev/(sd|nvme|mmcblk|hd|vd)",
     "перенаправление в блочное устройство"),
    (r"\b(shred|wipefs)\s+.*/dev/",
     "уничтожение данных на устройстве"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
     "fork bomb"),
    (r"(curl|wget)\s+[^\s|]+\s*\|\s*(bash|sh|zsh|dash|fish|python|perl)",
     "выполнение скачанного кода в shell"),
    (r"\bchmod\s+-r\s+0+\s+/",
     "массовое обнуление прав от корня"),
    (r"\bchown\s+-r\s+\S+\s+/(\s|$)",
     "массовая смена владельца от корня"),
    (r"\b(userdel|groupdel)\s+(root|0)\b",
     "удаление root-пользователя"),
    # systemctl poweroff/reboot/halt — это путь питания, должен идти через
    # power_guard с exact-match. От LLM такая команда — подозрительно.
    (r"\b(systemctl|service)\s+(poweroff|reboot|halt|stop)\b",
     "выключение/перезагрузка через systemctl (только через power_guard)"),
    (r"\b(?:^|\s)(poweroff|reboot|halt|shutdown)(?:\s|$|;|&|\|)",
     "выключение/перезагрузка системы (только через power_guard)"),
    (r"\bcrontab\s+-r\b",
     "удаление всех crontab"),
    # sudo — отрезаем целиком: ассистент не должен делать sudo автоматически
    (r"\bsudo\b",
     "эскалация привилегий через sudo"),
    (r">\s*/etc/(passwd|shadow)\b",
     "перезапись /etc/passwd|shadow"),
)

_COMPILED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), reason)
    for pat, reason in _DANGEROUS_PATTERNS
)


@dataclass(frozen=True)
class SafetyVerdict:
    """Результат проверки команды через safety-фильтр."""
    safe: bool
    reason: str = ""
    pattern: str = ""


def inspect(command: str) -> SafetyVerdict:
    """Проверяет команду. Возвращает SafetyVerdict.

    Команды, которые приходят сюда от LLM, уже прошли через `_strip_fences`
    и обычно одна строка. Проверяем как есть (без shlex), потому что
    регулярки ловят и подстановки, и pipe-цепочки.
    """
    if not command or not command.strip():
        return SafetyVerdict(safe=True)
    lowered = command.lower().strip()
    for pattern, reason in _COMPILED:
        if pattern.search(lowered):
            return SafetyVerdict(safe=False, reason=reason, pattern=pattern.pattern)
    return SafetyVerdict(safe=True)


# Тип обработчика «оповестить пользователя». Регистрируется приложением.
NotifierFn = Callable[[str, str, str], None]   # source, command, reason
_notifier: Optional[NotifierFn] = None


def set_notifier(fn: Optional[NotifierFn]) -> None:
    """Регистрирует обработчик уведомлений (UI ставит свой)."""
    global _notifier
    _notifier = fn


def report(source: str, command: str, verdict: SafetyVerdict) -> None:
    """Логирует и оповещает UI, что модель прислала опасную команду."""
    msg = (
        f"🚨 Модель {source!r} попыталась выполнить опасную команду "
        f"({verdict.reason}): {command!r}"
    )
    log.warning(msg)
    fn = _notifier
    if fn is not None:
        try:
            fn(source, command, verdict.reason)
        except Exception as e:  # noqa: BLE001
            log.debug("safety notifier raised: %s", e)
