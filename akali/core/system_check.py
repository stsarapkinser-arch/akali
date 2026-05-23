"""Проверка и доустановка ВСЕХ зависимостей Akali.

Что покрываем:
    • Python-пакеты: vosk, sounddevice, fastembed, piper-tts, google-genai
    • Системные бинари: piper, espeak-ng, ollama, паплеер (paplay/pw-play/aplay),
                          curl, qdbus
    • Vosk-модель (vosk-model-small-ru-0.22, ~50 МБ) — скачать и распаковать.
    • Голос Piper (ru_RU-irina-medium, ~60 МБ) — скачать .onnx + .onnx.json.
    • Ollama-модель `qwen2.5-coder:1.5b` (~1 ГБ) — pull.
    • Gemini API — ping с ключом.

Принципы:
    • Идемпотентность. Что есть — оставляем, чего нет — пытаемся поставить.
    • Без sudo. Системные бинари ставим только через pip (piper-tts) или
      пишем человеческие инструкции для остальных (espeak-ng/ollama/curl).
    • Каждый шаг логируется через `logging` в stdout с человеческой подписью.
    • НЕ блокируем UI — все долгие действия идут из QThread (`SystemCheckRunner`).

Главные точки входа:
    • `run_all_checks(api_key)`  — собирает SystemReport. Не ставит.
    • `install_missing(report)` — ставит то, что `fixable=True`.
    • `auto_install_safe()`      — ставит ТОЛЬКО безопасные вещи (модели/pip).
                                    Эта функция дёргается app.py на старте.
"""
from __future__ import annotations

import importlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.error import URLError
from urllib.request import urlopen

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

VOSK_MODEL_NAME = "vosk-model-small-ru-0.22"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"

# Python-пакеты, нужные для работы (имя_import → pip-имя)
REQUIRED_PIP_PACKAGES: list[tuple[str, str]] = [
    ("vosk", "vosk"),
    ("sounddevice", "sounddevice"),
    ("fastembed", "fastembed"),
    ("PySide6", "PySide6"),
]
# Опциональные: без них работает, но фичи деградируют
OPTIONAL_PIP_PACKAGES: list[tuple[str, str]] = [
    ("google.genai", "google-genai"),       # Gemini cloud путь
    ("piper", "piper-tts"),                  # piper Python wrapper
    ("ollama", "ollama"),                    # python-клиент для Ollama
]


@dataclass
class CheckResult:
    """Результат одной проверки."""
    name: str
    ok: bool
    message: str = ""
    fixable: bool = False
    auto_safe: bool = False     # можно ли молча доустановить без sudo


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

    @property
    def auto_safe_count(self) -> int:
        return sum(1 for item in self.items if not item.ok and item.auto_safe)


# Опциональный callback для прогрессов установки (UI ставит свой).
ProgressFn = Callable[[str], None]


def _emit(progress: Optional[ProgressFn], msg: str) -> None:
    log.info(msg)
    if progress is not None:
        try:
            progress(msg)
        except Exception:  # noqa: BLE001
            pass


def _download(url: str, dest: Path, progress: Optional[ProgressFn] = None,
              timeout: float = 600.0) -> bool:
    """Скачивает url → dest атомарно. Возвращает True/False, не кидает."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        _emit(progress, f"Скачиваю {dest.name}…")
        with urlopen(url, timeout=timeout) as r, tmp.open("wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            downloaded = 0
            last_report = 0
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total and downloaded - last_report > total // 20:
                    pct = downloaded * 100 // total
                    _emit(progress, f"  {dest.name}: {pct}%")
                    last_report = downloaded
        os.replace(tmp, dest)
        _emit(progress, f"Готово: {dest.name} ({dest.stat().st_size // 1024} КБ)")
        return True
    except (URLError, OSError, TimeoutError) as e:
        _emit(progress, f"Ошибка скачивания {dest.name}: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# ── Проверки =============================================================

def check_pip_package(import_name: str, pip_name: str,
                       required: bool = True) -> CheckResult:
    """Пытается импортнуть пакет; если нет — fixable=True (через pip)."""
    try:
        importlib.import_module(import_name)
        return CheckResult(f"pip: {pip_name}", True, "доступен",
                           fixable=False, auto_safe=False)
    except ImportError:
        return CheckResult(
            f"pip: {pip_name}",
            False,
            f"{'обязательный' if required else 'опциональный'} пакет не установлен",
            fixable=True, auto_safe=True,
        )


def check_vosk_model() -> CheckResult:
    model_dir = paths.DEFAULT_VOSK_MODEL_DIR
    if model_dir.is_dir() and (model_dir / "am" / "final.mdl").exists():
        return CheckResult("Vosk модель", True,
                           f"найдена в {model_dir}",
                           fixable=False, auto_safe=False)
    return CheckResult(
        "Vosk модель", False,
        f"не найдена. Будет скачана {VOSK_MODEL_NAME} (~50 МБ).",
        fixable=True, auto_safe=True,
    )


def check_ollama_binary() -> CheckResult:
    if shutil.which("ollama"):
        return CheckResult("Ollama (бинарь)", True, "в PATH",
                           fixable=False, auto_safe=False)
    # ollama install требует sudo — не auto_safe
    return CheckResult(
        "Ollama (бинарь)", False,
        "не установлен. Скачать с https://ollama.com/install.sh (требует sudo).",
        fixable=True, auto_safe=False,
    )


def check_ollama_model() -> CheckResult:
    if not shutil.which("ollama"):
        return CheckResult(f"Ollama модель {OLLAMA_MODEL}", False,
                           "ollama не установлен", fixable=False, auto_safe=False)
    try:
        proc = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(f"Ollama модель {OLLAMA_MODEL}", False,
                           f"не удалось опросить ollama: {e}",
                           fixable=True, auto_safe=False)
    if proc.returncode != 0:
        return CheckResult(f"Ollama модель {OLLAMA_MODEL}", False,
                           f"ollama list вернул код {proc.returncode}",
                           fixable=True, auto_safe=False)
    if OLLAMA_MODEL.split(":")[0] in proc.stdout:
        return CheckResult(f"Ollama модель {OLLAMA_MODEL}", True,
                           "найдена в ollama list",
                           fixable=False, auto_safe=False)
    # 1ГБ — не качаем молча; пусть юзер кликнет
    return CheckResult(f"Ollama модель {OLLAMA_MODEL}", False,
                       "не загружена (~1 ГБ). Установить кнопкой.",
                       fixable=True, auto_safe=False)


def check_piper_binary() -> CheckResult:
    if shutil.which("piper"):
        return CheckResult("Piper (бинарь)", True, "в PATH",
                           fixable=False, auto_safe=False)
    # piper-tts ставится через pip — это безопасно
    return CheckResult(
        "Piper (бинарь)", False,
        "не установлен. Будет поставлен через pip install piper-tts.",
        fixable=True, auto_safe=True,
    )


def check_piper_voice() -> CheckResult:
    missing = []
    if not PIPER_VOICE_FILE.exists():
        missing.append(PIPER_VOICE_FILE.name)
    if not PIPER_VOICE_JSON.exists():
        missing.append(PIPER_VOICE_JSON.name)
    if missing:
        return CheckResult(
            "Piper голос (ru-irina)", False,
            f"не найден: {', '.join(missing)}. Будет скачан (~60 МБ).",
            fixable=True, auto_safe=True,
        )
    return CheckResult("Piper голос (ru-irina)", True,
                       f"{PIPER_VOICE_FILE.name} готов",
                       fixable=False, auto_safe=False)


def check_espeak() -> CheckResult:
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return CheckResult("espeak-ng (фоллбэк TTS)", True, "в PATH",
                           fixable=False, auto_safe=False)
    return CheckResult(
        "espeak-ng (фоллбэк TTS)", False,
        "не установлен. Поставь: sudo apt install espeak-ng (требует sudo).",
        fixable=True, auto_safe=False,
    )


def check_audio_player() -> CheckResult:
    """Проигрыватель: paplay (PulseAudio) / pw-play (PipeWire) / aplay (ALSA)."""
    for name in ("pw-play", "paplay", "aplay"):
        if shutil.which(name):
            return CheckResult(f"Аудио-плеер ({name})", True, "в PATH",
                               fixable=False, auto_safe=False)
    return CheckResult(
        "Аудио-плеер", False,
        "ни pw-play/paplay/aplay не найден. Без него TTS не звучит.",
        fixable=False, auto_safe=False,
    )


def check_qdbus() -> CheckResult:
    """qdbus используется для управления окнами KWin."""
    for name in ("qdbus", "qdbus-qt6", "qdbus6"):
        if shutil.which(name):
            return CheckResult(f"qdbus ({name})", True, "в PATH",
                               fixable=False, auto_safe=False)
    return CheckResult(
        "qdbus", False,
        "не найден. Некоторые KWin-команды будут недоступны.",
        fixable=True, auto_safe=False,
    )


def check_curl() -> CheckResult:
    if shutil.which("curl"):
        return CheckResult("curl", True, "в PATH",
                           fixable=False, auto_safe=False)
    return CheckResult(
        "curl", False,
        "не найден. Понадобится для установки Ollama.",
        fixable=False, auto_safe=False,
    )


def check_gemini(api_key: str | None) -> CheckResult:
    if not api_key:
        return CheckResult(
            "Gemini API", False,
            "ключ не задан (см. Настройки → Gemini API key).",
            fixable=False, auto_safe=False,
        )
    # Используем НОВУЮ библиотеку google-genai (старая deprecated)
    try:
        from google import genai  # type: ignore  # noqa: WPS433
    except ImportError:
        return CheckResult(
            "Gemini API", False,
            "google-genai не установлен (pip install google-genai).",
            fixable=True, auto_safe=True,
        )
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="ping",
            config={"temperature": 0, "max_output_tokens": 4},
        )
        _ = getattr(resp, "text", "")
        return CheckResult("Gemini API", True, "ключ принят",
                           fixable=False, auto_safe=False)
    except Exception as e:  # noqa: BLE001
        return CheckResult("Gemini API", False, f"ключ отвергнут: {e}",
                           fixable=False, auto_safe=False)


def run_all_checks(gemini_api_key: str | None = None) -> SystemReport:
    """Полная диагностика. Не имеет побочных эффектов."""
    report = SystemReport()
    # Системные бинари
    report.items.append(check_curl())
    report.items.append(check_audio_player())
    report.items.append(check_qdbus())
    # Python-пакеты
    for imp, pip in REQUIRED_PIP_PACKAGES:
        report.items.append(check_pip_package(imp, pip, required=True))
    for imp, pip in OPTIONAL_PIP_PACKAGES:
        report.items.append(check_pip_package(imp, pip, required=False))
    # Модели и движки
    report.items.append(check_vosk_model())
    report.items.append(check_piper_binary())
    report.items.append(check_piper_voice())
    report.items.append(check_espeak())
    report.items.append(check_ollama_binary())
    report.items.append(check_ollama_model())
    if gemini_api_key:
        report.items.append(check_gemini(gemini_api_key))
    return report


def print_report(report: SystemReport) -> None:
    """Печатает отчёт человеческим языком в stdout."""
    log.info("=== Проверка зависимостей Akali ===")
    for item in report.items:
        prefix = "✓" if item.ok else ("⚠" if item.fixable else "✗")
        log.info("  %s %s — %s", prefix, item.name, item.message)
    n_fix = report.fixable_count
    n_auto = report.auto_safe_count
    if n_fix:
        log.info("Можно установить кнопкой «Установить недостающее»: %d (из них без sudo: %d)",
                 n_fix, n_auto)


# ── Установки (auto_safe=True) ==========================================

def install_pip_package(pip_name: str,
                         progress: Optional[ProgressFn] = None) -> CheckResult:
    """pip install --user pip_name. Без sudo."""
    _emit(progress, f"pip install --user {pip_name}…")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "--quiet",
             pip_name],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-300:]
            return CheckResult(f"pip: {pip_name}", False,
                               f"pip код {proc.returncode}: {tail}",
                               fixable=True, auto_safe=False)
        _emit(progress, f"Установлен {pip_name}.")
        return CheckResult(f"pip: {pip_name}", True, "установлен",
                           fixable=False, auto_safe=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(f"pip: {pip_name}", False,
                           f"pip упал: {e}", fixable=True, auto_safe=False)


def install_vosk_model(progress: Optional[ProgressFn] = None) -> CheckResult:
    """Скачивает и распаковывает vosk-model-small-ru-0.22 в model/."""
    target = paths.DEFAULT_VOSK_MODEL_DIR
    if target.is_dir() and (target / "am" / "final.mdl").exists():
        return CheckResult("Vosk модель", True, "уже установлена",
                           fixable=False, auto_safe=False)
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / f"{VOSK_MODEL_NAME}.zip"
        if not _download(VOSK_MODEL_URL, zip_path, progress):
            return CheckResult("Vosk модель", False,
                               "не удалось скачать zip-архив",
                               fixable=True, auto_safe=True)
        _emit(progress, f"Распаковываю {zip_path.name}…")
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(td)
        except (zipfile.BadZipFile, OSError) as e:
            return CheckResult("Vosk модель", False,
                               f"архив битый: {e}",
                               fixable=True, auto_safe=True)
        extracted = Path(td) / VOSK_MODEL_NAME
        if not extracted.exists():
            # модель могла распаковаться в подкаталог с другим именем
            for entry in Path(td).iterdir():
                if entry.is_dir() and (entry / "am" / "final.mdl").exists():
                    extracted = entry
                    break
        if not (extracted / "am" / "final.mdl").exists():
            return CheckResult("Vosk модель", False,
                               "распаковка не дала корректную структуру",
                               fixable=True, auto_safe=True)
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(extracted), str(target))
        except OSError as e:
            return CheckResult("Vosk модель", False,
                               f"не удалось переместить: {e}",
                               fixable=True, auto_safe=True)
    _emit(progress, f"Vosk модель установлена в {target}")
    return CheckResult("Vosk модель", True, f"установлена в {target}",
                       fixable=False, auto_safe=False)


def install_piper_voice(progress: Optional[ProgressFn] = None) -> CheckResult:
    """Скачивает голос ru_RU-irina-medium (.onnx + .onnx.json)."""
    PIPER_VOICE_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        (PIPER_VOICE_FILE, f"{PIPER_VOICE_URL_BASE}/ru_RU-irina-medium.onnx"),
        (PIPER_VOICE_JSON, f"{PIPER_VOICE_URL_BASE}/ru_RU-irina-medium.onnx.json"),
    ]
    for dest, url in targets:
        if dest.exists():
            continue
        if not _download(url, dest, progress):
            return CheckResult("Piper голос (ru-irina)", False,
                               f"не удалось скачать {dest.name}",
                               fixable=True, auto_safe=True)
    return CheckResult("Piper голос (ru-irina)", True, "голос ru-irina готов",
                       fixable=False, auto_safe=False)


def install_ollama(progress: Optional[ProgressFn] = None) -> CheckResult:
    """Запускает официальный installer Ollama (требует sudo!)."""
    if shutil.which("ollama"):
        return CheckResult("Ollama (бинарь)", True, "уже установлен",
                           fixable=False, auto_safe=False)
    if not shutil.which("curl"):
        return CheckResult("Ollama (бинарь)", False,
                           "нужен curl", fixable=False, auto_safe=False)
    _emit(progress, "Скачиваю официальный installer Ollama…")
    try:
        installer = paths.PROJECT_ROOT / ".ollama-install.sh"
        proc = subprocess.run(
            ["curl", "-fsSL", "-o", str(installer),
             "https://ollama.com/install.sh"],
            capture_output=True, timeout=60,
        )
        if proc.returncode != 0:
            return CheckResult("Ollama (бинарь)", False,
                               f"curl вернул {proc.returncode}",
                               fixable=True, auto_safe=False)
        _emit(progress, "Запускаю installer (потребуется sudo)…")
        proc = subprocess.run(["sh", str(installer)], timeout=600)
        installer.unlink(missing_ok=True)
        if proc.returncode != 0:
            return CheckResult("Ollama (бинарь)", False,
                               f"installer вернул {proc.returncode}",
                               fixable=True, auto_safe=False)
        _emit(progress, "Ollama установлен.")
        return CheckResult("Ollama (бинарь)", True, "установлен",
                           fixable=False, auto_safe=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult("Ollama (бинарь)", False,
                           f"ошибка установки: {e}",
                           fixable=True, auto_safe=False)


def pull_ollama_model(progress: Optional[ProgressFn] = None) -> CheckResult:
    """`ollama pull qwen2.5-coder:1.5b` — скачать модель (~1 ГБ)."""
    if not shutil.which("ollama"):
        return CheckResult(f"Ollama модель {OLLAMA_MODEL}", False,
                           "ollama не установлен",
                           fixable=False, auto_safe=False)
    _emit(progress, f"Скачиваю модель {OLLAMA_MODEL} (~1 ГБ)…")
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
            now = time.monotonic()
            if now - last_emit > 1.0:
                _emit(progress, line[:120])
                last_emit = now
        proc.wait(timeout=1800)
        if proc.returncode != 0:
            return CheckResult(f"Ollama модель {OLLAMA_MODEL}", False,
                               f"ollama pull вернул {proc.returncode}",
                               fixable=True, auto_safe=False)
        _emit(progress, f"Модель {OLLAMA_MODEL} готова.")
        return CheckResult(f"Ollama модель {OLLAMA_MODEL}", True, "скачана",
                           fixable=False, auto_safe=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        return CheckResult(f"Ollama модель {OLLAMA_MODEL}", False,
                           f"ошибка установки: {e}",
                           fixable=True, auto_safe=False)


def install_missing(report: SystemReport,
                    progress: Optional[ProgressFn] = None,
                    only_auto_safe: bool = False) -> list[CheckResult]:
    """Доустанавливает помеченное fixable=True.

    Если only_auto_safe=True — ставит только то, что auto_safe=True
    (т.е. без sudo, без огромных скачиваний без согласия).
    """
    results: list[CheckResult] = []
    fixers: dict[str, Callable[[Optional[ProgressFn]], CheckResult]] = {
        "Vosk модель": install_vosk_model,
        "Piper голос (ru-irina)": install_piper_voice,
        "Ollama (бинарь)": install_ollama,
        f"Ollama модель {OLLAMA_MODEL}": pull_ollama_model,
    }
    # pip-фиксеры строим динамически
    for imp, pip in (*REQUIRED_PIP_PACKAGES, *OPTIONAL_PIP_PACKAGES):
        key = f"pip: {pip}"
        fixers[key] = (lambda p, name=pip: install_pip_package(name, p))

    for item in report.items:
        if item.ok or not item.fixable:
            continue
        if only_auto_safe and not item.auto_safe:
            continue
        fix = fixers.get(item.name)
        if fix is None:
            continue
        _emit(progress, f"→ Чиню: {item.name}")
        results.append(fix(progress))
    return results


def auto_install_safe(api_key: str | None = None,
                       progress: Optional[ProgressFn] = None) -> SystemReport:
    """Удобный one-shot: проверить → доустановить безопасное → проверить ещё раз.

    «Безопасное» = pip-пакеты + Vosk-модель + голос piper. Никакого sudo и
    никаких 1+ ГБ моделей без согласия.
    """
    report = run_all_checks(api_key)
    print_report(report)
    if report.auto_safe_count == 0:
        return report
    log.info("Доустанавливаю %d безопасных компонента(ов)…",
             report.auto_safe_count)
    install_missing(report, progress=progress, only_auto_safe=True)
    # Перепроверяем — статусы могли поменяться
    report2 = run_all_checks(api_key)
    print_report(report2)
    return report2
