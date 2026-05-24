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

# Базовые ANSI-формы (для helper-функций log.section/success/warn_box)
BOLD = "\033[1m"
DIM = "\033[2m"
BR_CYAN = "\033[1;96m"      # bold bright cyan — section headers
BR_GREEN = "\033[1;92m"     # bold bright green — success
BR_YELLOW = "\033[1;93m"    # bold bright yellow — warnings
BR_RED = "\033[1;91m"       # bold bright red — errors
BR_MAGENTA = "\033[1;95m"   # bold bright magenta — debug-blocks
BG_RED = "\033[1;97;41m"    # white on red — critical errors

_LEVEL_LABEL: Mapping[str, str] = {
    "DEBUG":    "·",
    "INFO":     "i",
    "WARNING":  "!",
    "ERROR":    "x",
    "CRITICAL": "X",
}

_use_color = False


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

    global _use_color
    _use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_HumanFormatter(use_color=_use_color))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Шумные библиотеки придушить.
    # google-genai в новой версии логирует "AFC is enabled with max remote calls"
    # на каждом запросе через 'google_genai.models' — заткнём.
    for noisy in ("urllib3", "google", "google_genai", "google_genai.models",
                  "httpx", "hpack", "filelock", "fastembed"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Сразу же устанавливаем глобальные защиты от необработанных исключений.
    install_global_excepthooks()


def banner(version: str) -> None:
    """Печатает приветственный баннер."""
    bar = "━" * 56
    log = logging.getLogger("akali")
    if _use_color:
        log.info(f"{BR_CYAN}{bar}{_RESET}")
        log.info(f"{BR_CYAN}  Akali v{version} — голосовой ассистент{_RESET}")
        log.info(f"{DIM}  Kali Linux · KDE Plasma 6 · Wayland · Intel N100{_RESET}")
        log.info(f"{BR_CYAN}{bar}{_RESET}")
    else:
        log.info(bar)
        log.info("  Akali v%s — голосовой ассистент", version)
        log.info("  Kali Linux · KDE Plasma 6 · Wayland · Intel N100")
        log.info(bar)


# ── Helper-функции для красивого вывода ─────────────────────────
# Все они идут в обычный logger.info, но с ANSI-разметкой если включено
# раскрашивание. В файле/без TTY они печатают plain text.

def section(logger: logging.Logger, title: str, char: str = "─") -> None:
    """Печатает блок-заголовок:  ▶ Маршрутизация ──────────────────
    Используется для визуального разделения этапов pipeline.
    """
    fill = char * max(4, 56 - len(title) - 4)
    if _use_color:
        logger.info(f"{BR_CYAN}▶ {title} {fill}{_RESET}")
    else:
        logger.info("▶ %s %s", title, fill)


def success(logger: logging.Logger, msg: str, *args) -> None:
    """Зелёная success-строка с галочкой."""
    formatted = msg % args if args else msg
    if _use_color:
        logger.info(f"{BR_GREEN}✓ {formatted}{_RESET}")
    else:
        logger.info("✓ %s", formatted)


def warn_box(logger: logging.Logger, title: str, body: str = "") -> None:
    """Жёлтый блок с предупреждением, дополнительные строки в body
    выводятся отступом снизу. Каждая строка body — отдельный log-call.
    """
    if _use_color:
        logger.warning(f"{BR_YELLOW}⚠ {title}{_RESET}")
    else:
        logger.warning("⚠ %s", title)
    if body:
        for line in body.splitlines():
            if line.strip():
                if _use_color:
                    logger.warning(f"  {DIM}└ {line}{_RESET}")
                else:
                    logger.warning("  └ %s", line)


def error_box(logger: logging.Logger, title: str, reason: str = "",
              suggestion: str = "") -> None:
    """Красный блок ошибки с человеческим reason и подсказкой что делать.
    Сырой stack trace тут НЕ показываем — он попадает только в DEBUG и
    в ~/.cache/akali/last_error.log.
    """
    if _use_color:
        logger.error(f"{BR_RED}✗ {title}{_RESET}")
        if reason:
            logger.error(f"  {DIM}└ причина: {reason}{_RESET}")
        if suggestion:
            logger.error(f"  {DIM}└ совет: {suggestion}{_RESET}")
    else:
        logger.error("✗ %s", title)
        if reason:
            logger.error("  └ причина: %s", reason)
        if suggestion:
            logger.error("  └ совет: %s", suggestion)


def step(logger: logging.Logger, msg: str, *args) -> None:
    """Тусклая debug-подобная строка с префиксом « · ».
    Используется внутри блока (section) для перечисления шагов:
        ▶ Маршрутизация ──────
           · cache miss
           · semantic miss (score=0.43)
           · gemini: error 400
           · fallback → ollama
    Печатается через info, чтобы было видно по умолчанию.
    """
    formatted = msg % args if args else msg
    if _use_color:
        logger.info(f"  {DIM}· {formatted}{_RESET}")
    else:
        logger.info("  · %s", formatted)


def format_error(exc: BaseException) -> tuple[str, str]:
    """Конвертирует исключение в (reason, suggestion).
    Reason — короткое человеческое описание.
    Suggestion — что попробовать сделать.
    Полный стектрейс пишется только в ~/.cache/akali/last_error.log.
    """
    name = type(exc).__name__
    msg = str(exc) or "<без сообщения>"
    short = msg if len(msg) < 200 else msg[:200] + "…"
    # Эвристики под частые случаи. low_full включает и имя класса, и
    # текст — чтобы TimeoutError ловился даже с пустым/несвязным сообщением.
    low = msg.lower()
    low_full = (name + " " + msg).lower()
    if "timeout" in low_full or "timed out" in low_full:
        return (f"таймаут: {short}", "проверь сеть или подними таймаут")
    if "connection" in low and ("refused" in low or "reset" in low):
        return (f"{name}: {short}", "сервер недоступен — проверь что он запущен")
    if "name or service not known" in low or "nodename nor servname" in low:
        return (f"{name}: {short}", "нет интернета или DNS не отвечает")
    if "failed_precondition" in low and "location" in low:
        return (f"{name}: API закрыто для региона",
                "включи VPN или используй локальную модель")
    if "unauthorized" in low or "401" in low or "403" in low:
        return (f"{name}: {short}", "проверь API-ключ / пермишены")
    if "rate limit" in low or "429" in low:
        return (f"{name}: лимит запросов", "подожди или используй локальную модель")
    if "404" in low or "not found" in low:
        return (f"{name}: ресурс не найден", "проверь URL/имя модели")
    return (f"{name}: {short}", "")
