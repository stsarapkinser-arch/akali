"""Проверка и доустановка зависимостей Akali при старте.

Что проверяется (в порядке):
    1. Vosk-модель для русского (`model/` в корне проекта).
    2. Бинарь `ollama` в PATH.
    3. Модель `qwen2.5-coder:1.5b` в `ollama list`.
    4. Бинарь `piper` (TTS) и файл голоса.
    5. Доступность Gemini API при наличии ключа.

Каждый шаг:
    • Понятный лог в stdout.
    • Возвращает структурированный отчёт.
    • НЕ блокирует запуск приложения — отсутствие LLM/TTS просто
      деградирует функционал, ассистент стартует.

Доустановка (Ollama, модель, piper-голос) делается ТОЛЬКО по явному
запросу из UI кнопкой «Установить недостающее», не молча. На N100
скачивать 1+ ГБ без согласия — плохо.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import paths

log = logging.getLogger(__name__)

# === Артефакты, к которым привязаны проверки ============================
OLLAMA_MODEL = "qwen2.5-coder:1.5b"
PIPER_VOICE_DIR = paths.PROJECT_ROOT / "voices"
PIPER_VOICE_FILE = PIPER_VOICE_DIR / "ru_RU-irina-medium.onnx"
PIPER_VOICE_JSON = PIPER_VOICE_DIR / "ru_RU-irina-medium.onnx.json"
PIPER_VOICE_URL_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium"
)


@dataclass
class CheckResult:
    """Результат одной проверки."""
    name: str
    ok: bool
    message: str = ""
    fixable: bool = False


@dataclass
class SystemReport:
    """Суммарный отчёт проверок."""
    items: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(item.ok for item in self.items)

    @property
    def fixable_count(self) -> int:
        return sum(1 for item in self.items if not item.ok and item.fixable)


# Опциональный callback для прогрессов установки (UI ставит свой).
ProgressFn = Callable[[str], None]


def _emit(progress: Optional[ProgressFn], msg: str) -> None:
    log.info(msg)
    if progress is not None:
        try:
            progress(msg)
        except Exception:  # noqa: BLE001
            pass


# ── Проверки =============================================================

def check_vosk_model() -> CheckResult:
    model_dir = paths.DEFAULT_VOSK_MODEL_DIR
    if model_dir.is_dir() and (model_dir / "am" / "final.mdl").exists():
        return CheckResult("Vosk модель", True,
                           f"найдена в {model_dir}", fixable=False)
    return CheckResult(
        "Vosk модель", False,
        "не найдена. Скачай vosk-model-small-ru-0.22 и распакуй "
        f"под именем 'model/' в {paths.PROJECT_ROOT}",
        fixable=False,
    )


def check_ollama_binary() -> CheckResult:
    if shutil.which("ollama"):
        return CheckResult("Ollama", True, "бинарь в PATH", fixable=False)
    return CheckResult(
        "Ollama", False,
        "не установлен. Будет установлен по запросу.",
        fixable=True,
    )


def check_ollama_model() -> CheckResult:
    if not shutil.which("ollama"):
        return CheckResult(f"Модель {OLLAMA_MODEL}", False,
                           "ollama не установлен", fixable=False)
    try:
        proc = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(f"Модель {OLLAMA_MODEL}", False,
                           f"не удалось опросить ollama: {e}", fixable=True)
    if proc.returncode != 0:
        return CheckResult(f"Модель {OLLAMA_MODEL}", False,
                           f"ollama list вернул код {proc.returncode}", fixable=True)
    if OLLAMA_MODEL.split(":")[0] in proc.stdout:
        return CheckResult(f"Модель {OLLAMA_MODEL}", True,
                           "найдена в ollama list", fixable=False)
    return CheckResult(f"Модель {OLLAMA_MODEL}", False,
                       "не загружена. Будет установлена по запросу.", fixable=True)


def check_piper() -> CheckResult:
    if not shutil.which("piper"):
        return CheckResult("Piper TTS", False,
                           "не установлен. Голос будет деградирован до espeak-ng.",
                           fixable=True)
    if not PIPER_VOICE_FILE.exists():
        return CheckResult("Piper voice (ru-irina)", False,
                           f"не найден {PIPER_VOICE_FILE.name}. Будет скачан по запросу.",
                           fixable=True)
    return CheckResult("Piper TTS", True, f"голос {PIPER_VOICE_FILE.name} готов",
                       fixable=False)


def check_gemini(api_key: str | None) -> CheckResult:
    if not api_key:
        return CheckResult(
            "Gemini API", False,
            "ключ не задан (см. Настройки → Gemini API key). "
            "Без ключа используется только локальная Ollama.",
            fixable=False,
        )
    try:
        import google.generativeai as genai  # noqa: WPS433
    except ImportError:
        return CheckResult("Gemini API", False,
                           "google-generativeai не установлен (pip install)",
                           fixable=False)
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        # Лёгкий ping: один токен, чисто на разрешение ключа.
        resp = model.generate_content(
            "ping",
            generation_config={"temperature": 0, "max_output_tokens": 4},
        )
        _ = getattr(resp, "text", "")
        return CheckResult("Gemini API", True, "ключ принят", fixable=False)
    except Exception as e:  # noqa: BLE001
        return CheckResult("Gemini API", False, f"ключ отвергнут: {e}",
                           fixable=False)


def run_all_checks(gemini_api_key: str | None = None) -> SystemReport:
    """Выполняет все проверки. Не имеет побочных эффектов (никаких установок)."""
    report = SystemReport()
    report.items.append(check_vosk_model())
    report.items.append(check_ollama_binary())
    report.items.append(check_ollama_model())
    report.items.append(check_piper())
    if gemini_api_key:
        report.items.append(check_gemini(gemini_api_key))
    return report


def print_report(report: SystemReport) -> None:
    """Печатает отчёт человеческим языком в stdout."""
    for item in report.items:
        prefix = "✓" if item.ok else "✗"
        log.info("%s %s — %s", prefix, item.name, item.message)


# ── Доустановка =========================================================
#
# ВНИМАНИЕ: пользователь должен явно подтвердить через UI. Эти функции —
# для использования только из кнопки «Установить недостающее».

def install_ollama(progress: Optional[ProgressFn] = None) -> CheckResult:
    """Скачивает и запускает официальный installer Ollama."""
    if shutil.which("ollama"):
        return CheckResult("Ollama", True, "уже установлен", fixable=False)
    _emit(progress, "Скачиваю официальный installer Ollama…")
    try:
        # Скачиваем скрипт во временный файл, потом запускаем — так
        # пользователь может перепроверить если что-то пошло не так.
        installer = paths.PROJECT_ROOT / ".ollama-install.sh"
        with open(installer, "wb") as f:
            proc = subprocess.run(
                ["curl", "-fsSL", "https://ollama.com/install.sh"],
                stdout=f, stderr=subprocess.PIPE, timeout=60,
            )
        if proc.returncode != 0:
            return CheckResult("Ollama", False,
                               f"curl вернул {proc.returncode}: {proc.stderr.decode(errors='ignore')[:120]}",
                               fixable=True)
        _emit(progress, "Запускаю installer (нужен sudo)…")
        proc = subprocess.run(["sh", str(installer)], timeout=600)
        installer.unlink(missing_ok=True)
        if proc.returncode != 0:
            return CheckResult("Ollama", False,
                               f"installer вернул {proc.returncode}", fixable=True)
        _emit(progress, "Ollama установлен.")
        return CheckResult("Ollama", True, "установлен", fixable=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult("Ollama", False, f"ошибка установки: {e}",
                           fixable=True)


def pull_ollama_model(progress: Optional[ProgressFn] = None) -> CheckResult:
    """`ollama pull qwen2.5-coder:1.5b` — скачать модель."""
    if not shutil.which("ollama"):
        return CheckResult(f"Модель {OLLAMA_MODEL}", False,
                           "ollama не установлен", fixable=False)
    _emit(progress, f"Скачиваю модель {OLLAMA_MODEL} (~1.5 ГБ)…")
    try:
        proc = subprocess.Popen(
            ["ollama", "pull", OLLAMA_MODEL],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        last_emit = time.monotonic()
        for line in proc.stdout or ():
            line = line.rstrip()
            if not line:
                continue
            # Эмитим не чаще раза в секунду — иначе UI зальёт прогрессом
            now = time.monotonic()
            if now - last_emit > 1.0:
                _emit(progress, line[:120])
                last_emit = now
        proc.wait(timeout=1800)
        if proc.returncode != 0:
            return CheckResult(f"Модель {OLLAMA_MODEL}", False,
                               f"ollama pull вернул {proc.returncode}", fixable=True)
        _emit(progress, f"Модель {OLLAMA_MODEL} готова.")
        return CheckResult(f"Модель {OLLAMA_MODEL}", True, "скачана", fixable=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(f"Модель {OLLAMA_MODEL}", False,
                           f"ошибка установки: {e}", fixable=True)


def install_piper_voice(progress: Optional[ProgressFn] = None) -> CheckResult:
    """Скачивает голос ru_RU-irina-medium для piper."""
    PIPER_VOICE_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        (PIPER_VOICE_FILE, f"{PIPER_VOICE_URL_BASE}/ru_RU-irina-medium.onnx"),
        (PIPER_VOICE_JSON, f"{PIPER_VOICE_URL_BASE}/ru_RU-irina-medium.onnx.json"),
    ]
    for dest, url in targets:
        if dest.exists():
            continue
        _emit(progress, f"Скачиваю {dest.name}…")
        try:
            proc = subprocess.run(
                ["curl", "-fL", "-o", str(dest), url],
                capture_output=True, timeout=600,
            )
            if proc.returncode != 0:
                return CheckResult("Piper voice", False,
                                   f"curl вернул {proc.returncode}",
                                   fixable=True)
        except (subprocess.TimeoutExpired, OSError) as e:
            return CheckResult("Piper voice", False,
                               f"ошибка скачивания: {e}", fixable=True)
    _emit(progress, "Голос Piper готов.")
    return CheckResult("Piper voice", True, "голос ru-irina готов", fixable=False)


def install_missing(report: SystemReport,
                    progress: Optional[ProgressFn] = None) -> list[CheckResult]:
    """Доустанавливает то, что помечено fixable=True. Возвращает новые проверки."""
    results: list[CheckResult] = []
    fixers = {
        "Ollama": install_ollama,
        f"Модель {OLLAMA_MODEL}": pull_ollama_model,
        "Piper voice (ru-irina)": install_piper_voice,
    }
    for item in report.items:
        if item.ok or not item.fixable:
            continue
        fix = fixers.get(item.name)
        if fix is None:
            continue
        results.append(fix(progress))
    return results
