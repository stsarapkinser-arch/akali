"""Запуск shell-команд через subprocess.

«&» в конце команды → фоновый процесс (Popen + DEVNULL stdout/stderr).
Без «&» → синхронный subprocess.run с capture_output и таймаутом.
"""
from __future__ import annotations

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


def execute(cmd: str, timeout: float = 15.0) -> CommandResult:
    """Запускает команду. Если оканчивается на `&` — фоновый Popen."""
    is_background = cmd.strip().endswith("&")
    try:
        if is_background:
            subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return CommandResult(cmd=cmd, is_background=True)
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
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
    except Exception as e:  # noqa: BLE001
        return CommandResult(cmd=cmd, error=str(e))
