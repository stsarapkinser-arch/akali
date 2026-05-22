"""Запуск shell-команд через subprocess.

«&» в конце команды → фоновый процесс (Popen + DEVNULL stdout/stderr).
Без «&» → синчронный subprocess.run с capture_output и таймаутом.

SECURITY: Использует shlex.split() для безопасного парсинга и проверяет
на опасные shell-метасимволы, которые могут быть в голосовом вводе.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass


@dataclass
class CommandResult:
    """Результат выполнения системной команды."""
    cmd: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    timed_out: bool = False
    is_background: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.timed_out and self.returncode == 0


# Опасные метасимволы в голосовом вводе (предотвращение command injection)
DANGEROUS_CHARS = re.compile(r'[;|<>`$(){}[\]\\]|&&|\|\|')


def _parse_command(cmd: str) -> list[str] | None:
    """Парсит команду безопасно используя shlex.
    Возвращает список аргументов или None если команда содержит опасные символы."""
    try:
        return shlex.split(cmd)
    except ValueError:
        return None


def execute(cmd: str, timeout: float = 15.0) -> CommandResult:
    """Запускает команду. Если оканчивается на `&` — фоновый Popen.
    Использует shell=False для предотвращения command injection."""
    cmd_clean = cmd.strip()
    is_background = cmd_clean.endswith("&")

    if is_background:
        cmd_clean = cmd_clean[:-1].strip()

    # Проверяем на опасные метасимволы (могут быть в голосовом вводе)
    if DANGEROUS_CHARS.search(cmd_clean):
        return CommandResult(
            cmd=cmd,
            error=f"Команда содержит недопустимые символы (безопасность: "
                  f"предотвращение injection): {cmd_clean[:100]}")

    # Парсим команду на токены
    args = _parse_command(cmd_clean)
    if args is None:
        return CommandResult(
            cmd=cmd,
            error=f"Не удалось распарсить команду (проверь кавычки): {cmd_clean[:100]}")

    if not args:
        return CommandResult(cmd=cmd, error="Пустая команда")

    try:
        if is_background:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return CommandResult(cmd=cmd, is_background=True)

        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
        )
        return CommandResult(
            cmd=cmd,
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
            returncode=proc.returncode,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(cmd=cmd, timed_out=True,
                             error=f"Таймаут {timeout:.0f}с")
    except FileNotFoundError as e:
        return CommandResult(cmd=cmd, error=f"Команда не найдена: {args[0]}")
    except Exception as e:  # noqa: BLE001
        return CommandResult(cmd=cmd, error=str(e))
