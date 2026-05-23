"""Единый logging-setup для Akali.

Логи идут в stdout терминала с человеческим форматом. Никакой вкладки в
UI больше нет — пользователь смотрит лог там, где запустил приложение.

Уровни:
  INFO  — нормальные события (распознал, выполнил, обновился)
  WARN  — что-то восстановимо плохое (нет интернета, модель не нашлась)
  ERROR — реальные сбои (не загрузился Vosk, упал git pull)
  DEBUG — детали пайплайна (включается переменной AKALI_DEBUG=1)

Здесь же — глобальные exception hook'и, чтобы любое необработанное
исключение (включая из не-main потоков) логировалось, а не валило процесс.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path
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


def _error_log_path() -> Path:
    """~/.cache/akali/last_error.log — куда пишем стектрейсы необработанных
    исключений, чтобы потом можно было разобраться даже без терминала."""
    d = Path.home() / ".cache" / "akali"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d / "last_error.log"


def _append_error_log(text: str) -> None:
    try:
        with open(_error_log_path(), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n"))
            f.write(text)
            f.write("\n" + "─" * 70 + "\n")
    except OSError:
        pass


def _excepthook(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
    """Глобальный хук для main-thread. Логирует, но НЕ убивает процесс."""
    if issubclass(exc_type, KeyboardInterrupt):
        # Ctrl+C — даём ему пройти штатно
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log = logging.getLogger("akali")
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.error("Необработанное исключение в main-thread:\n%s", tb)
    _append_error_log(f"MAIN-THREAD\n{tb}")


def _thread_excepthook(args) -> None:  # noqa: ANN001
    """Глобальный хук для не-main потоков (threading)."""
    if args.exc_type is SystemExit:
        return
    log = logging.getLogger("akali")
    tb = "".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback))
    name = args.thread.name if args.thread else "<unknown>"
    log.error("Необработанное исключение в потоке %r:\n%s", name, tb)
    _append_error_log(f"THREAD {name}\n{tb}")


def install_global_excepthooks() -> None:
    """Устанавливает sys.excepthook + threading.excepthook.
    Идемпотентно. Должно вызываться сразу после setup()."""
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


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

    # Сразу же устанавливаем глобальные защиты от необработанных исключений.
    install_global_excepthooks()


def banner(version: str) -> None:
    """Печатает приветственный баннер."""
    bar = "─" * 56
    log = logging.getLogger("akali")
    log.info(bar)
    log.info("  Akali v%s — голосовой ассистент", version)
    log.info("  Kali Linux · KDE Plasma 6 · Wayland · Intel N100")
    log.info(bar)
