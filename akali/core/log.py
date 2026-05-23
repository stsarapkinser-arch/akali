"""Единый logging-setup для Akali.

Логи идут в stdout терминала с человеческим форматом. Никакой вкладки в
UI больше нет — пользователь смотрит лог там, где запустил приложение.

Уровни:
  INFO  — нормальные события (распознал, выполнил, обновился)
  WARN  — что-то восстановимо плохое (нет интернета, модель не нашлась)
  ERROR — реальные сбои (не загрузился Vosk, упал git pull)
  DEBUG — детали пайплайна (включается переменной AKALI_DEBUG=1)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Mapping

# ANSI-цвета для терминала. Если stdout не TTY, цвета отключаются.
_ANSI = {
    "DEBUG":    "\033[2;37m",   # тусклый серый
    "INFO":     "\033[0;36m",   # cyan
    "WARNING":  "\033[1;33m",   # жёлтый
    "ERROR":    "\033[1;31m",   # красный
    "CRITICAL": "\033[1;41m",   # белый на красном
}
_RESET = "\033[0m"

_LEVEL_LABEL: Mapping[str, str] = {
    "DEBUG":    "·",
    "INFO":     "i",
    "WARNING":  "!",
    "ERROR":    "x",
    "CRITICAL": "X",
}


class _HumanFormatter(logging.Formatter):
    """Компактный, разноцветный, без timestamp-шума."""

    def __init__(self, use_color: bool):
        super().__init__()
        self._color = use_color

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        label = _LEVEL_LABEL.get(record.levelname, "?")
        msg = record.getMessage()
        # Сокращаем имя модуля: akali.core.query_router → query_router
        name = record.name.rsplit(".", 1)[-1]
        line = f"{ts} {label} [{name}] {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if self._color:
            tint = _ANSI.get(record.levelname, "")
            line = f"{tint}{line}{_RESET}"
        return line


_configured = False


def setup(level: int | None = None) -> None:
    """Настраивает root-logger один раз за процесс."""
    global _configured
    if _configured:
        return
    _configured = True

    if level is None:
        level = logging.DEBUG if os.environ.get("AKALI_DEBUG") else logging.INFO

    use_color = sys.stdout.isatty()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_HumanFormatter(use_color=use_color))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Шумные библиотеки придушить
    for noisy in ("urllib3", "google", "httpx", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def banner(version: str) -> None:
    """Печатает приветственный баннер."""
    bar = "─" * 56
    log = logging.getLogger("akali")
    log.info(bar)
    log.info("  Akali v%s — голосовой ассистент", version)
    log.info("  Kali Linux · KDE Plasma 6 · Wayland · Intel N100")
    log.info(bar)
