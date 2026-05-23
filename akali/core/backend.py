"""Фасад AssistantCore — единое состояние для UI и audio_worker.

После рефакторинга это тонкий объект, не делающий «тяжёлой работы»:
    • commands.txt + auto_commands.json → словарь команд          (db.py)
    • exact-match power-фразы → poweroff/reboot/etc              (power_guard.py)
    • fast cache → FastEmbed → LLM (ollama/gemini) → safety       (query_router.py)
    • запуск команды через subprocess (без shell)                  (executor.py)
    • переиндексация системы                                      (system_indexer.py)

Старый fuzzy/ollama-vector путь убран по ТЗ: голос идёт строго через
power_guard → QueryRouter. Никаких параллельных эвристик.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from .. import paths
from . import db, matcher, power_guard
from .executor import CommandResult, execute

log = logging.getLogger(__name__)


def _lazy_import_router():
    from .query_router import QueryRouter  # noqa: WPS433
    return QueryRouter


# === Конфигурация по умолчанию ===
WAKE_THRESHOLD = 0.75
ACTIVE_WINDOW_SECONDS = 5.0
DEFAULT_WAKE_WORDS = ("компьютер", "ассистент", "акали")
DEFAULT_REINDEX_TRIGGERS = (
    "переиндексируй",
    "обнови команд",
    "пересканируй систем",
)


@dataclass
class ReloadStats:
    """Что получилось после reload() — для отображения в UI/логах."""
    commands_total: int = 0
    curated_count: int = 0
    auto_count: int = 0
    auto_sources: dict = field(default_factory=dict)


class AssistantCore:
    """Состояние ассистента: база команд, роутер, настройки."""

    def __init__(
        self,
        commands_file: str | os.PathLike = paths.COMMANDS_TXT,
        auto_commands_file: str | os.PathLike = paths.AUTO_COMMANDS_JSON,
        indexer_script: str | os.PathLike = paths.INDEXER_SCRIPT,
    ):
        self.commands_file = Path(commands_file)
        self.auto_commands_file = Path(auto_commands_file)
        self.indexer_script = Path(indexer_script)

        # Настройки (UI может править через QSettings)
        self.wake_threshold = WAKE_THRESHOLD
        self.energy_threshold: float = 0.0
        self.wake_words = list(DEFAULT_WAKE_WORDS)
        self.reindex_triggers = list(DEFAULT_REINDEX_TRIGGERS)

        # Состояние
        self.commands_db: dict[str, list[str]] = {}
        self.auto_sources: dict = {}
        self._curated_count = 0
        self._auto_count = 0

        # Роутер (создаётся отдельно через init_router)
        self._router: Optional[object] = None

    # ── Категоризация команд ─────────────────────────────────
    _CATEGORY_RULES: list[tuple[str, list[str]]] = [
        ("рабочий стол", ["qdbus", "kwin", "plasma", "kglobalaccel"]),
        ("браузер",      ["firefox", "chromium", "xdg-open http", "google-chrome"]),
        ("аудио",        ["pactl", "playerctl", "amixer", "volume", "mute", "pw-cli"]),
        ("сеть",         ["ip ", "ping", "nmcli", "iwconfig", "ss ", "netstat",
                          "traceroute", "mtr", "nslookup", "dig ", "arp"]),
        ("файлы",        ["dolphin", "thunar", "ls ", "find ", "mkdir",
                          "du ", "df ", "tree", "lsblk"]),
        ("терминал",     ["konsole", "alacritty", "kitty", "bash -c", "zsh -c"]),
        ("мониторинг",   ["htop", "btop", "top ", "nvtop", "sensors", "ps ", "iostat"]),
        ("система",      ["systemctl", "journalctl", "pkill", "kill ", "dmesg",
                          "lspci", "lsusb", "uname", "uptime", "who "]),
    ]

    @staticmethod
    def category_of(cmd: str) -> str:
        lower = cmd.lower()
        for category, patterns in AssistantCore._CATEGORY_RULES:
            if any(p in lower for p in patterns):
                return category
        return "прочее"

    # ── Жизненный цикл ────────────────────────────────────────
    def reload(self) -> ReloadStats:
        """Перечитать commands.txt + auto_commands.json и пересобрать индекс."""
        bundle = db.build_commands_bundle(self.commands_file, self.auto_commands_file)
        # Силовые команды убираем из основной базы — их обрабатывает power_guard.
        self.commands_db = {
            cmd: triggers
            for cmd, triggers in bundle.merged.items()
            if not power_guard.is_power_command(cmd)
        }
        self.auto_sources = bundle.auto_sources
        self._curated_count = bundle.curated_count
        self._auto_count = bundle.auto_count

        # Пересобираем семантический индекс если роутер уже создан
        if self._router is not None:
            try:
                self._router.load_db(self.commands_db, self.commands_file)
            except Exception as e:  # noqa: BLE001
                log.warning("Перестройка семантического индекса не удалась: %s", e)

        return ReloadStats(
            commands_total=len(self.commands_db),
            curated_count=self._curated_count,
            auto_count=self._auto_count,
            auto_sources=dict(self.auto_sources),
        )

    def init_router(self, gemini_api_key: Optional[str] = None) -> None:
        """Создаёт QueryRouter (lazy — чтобы тесты могли не тащить fastembed)."""
        QueryRouter = _lazy_import_router()
        self._router = QueryRouter(
            cache_file=paths.QUERY_CACHE_JSON,
            gemini_api_key=gemini_api_key,
        )
        if self.commands_db:
            self._router.load_db(self.commands_db, self.commands_file)

    def update_gemini_key(self, api_key: Optional[str]) -> None:
        if self._router is not None:
            try:
                self._router.set_gemini_key(api_key)
            except Exception:  # noqa: BLE001
                pass

    # ── Главный пайплайн ──────────────────────────────────────
    def process(self, text: str) -> Tuple[Optional[str], str]:
        """Полный пайплайн: power_guard → QueryRouter.

        Возвращает (bash_команда, источник). Источник — один из:
        'power', 'cache', 'semantic', 'gemini', 'ollama', '' (промах).
        """
        # Уровень 0: силовая команда → exact-match
        power_cmd = power_guard.match(text)
        if power_cmd:
            return power_cmd, "power"

        if self._router is None:
            return None, ""

        try:
            cmd = self._router.route(text)
        except Exception as e:  # noqa: BLE001
            log.warning("QueryRouter упал: %s", e)
            return None, ""
        if cmd is None:
            return None, ""
        # Источник логируется внутри роутера. Снаружи знаем только что это «router».
        return cmd, "router"

    # ── Wake-word ────────────────────────────────────────────
    def detect_wake_word(self, words: list[str]) -> int:
        return matcher.detect_wake_word(words, self.wake_words, self.wake_threshold)

    def is_reindex_phrase(self, text: str) -> bool:
        return matcher.is_reindex_phrase(text, self.reindex_triggers)

    # ── Выполнение ────────────────────────────────────────────
    def execute(self, cmd: str, timeout: float = 15.0) -> CommandResult:
        return execute(cmd, timeout=timeout)

    # ── Редактирование commands.txt из UI ─────────────────────
    def save_command(self, cmd: str, triggers: list[str]) -> bool:
        """Добавляет/обновляет команду в commands.txt. Перезагружает базу."""
        cmd = cmd.strip()
        triggers = [t.strip() for t in triggers if t.strip()]
        if not cmd or not triggers:
            return False
        if power_guard.is_power_command(cmd):
            log.warning("save_command: попытка добавить силовую команду %r — "
                        "отказано (power_guard).", cmd)
            return False
        return _write_commands_txt(self.commands_file,
                                   {**db.parse_curated(self.commands_file),
                                    cmd: triggers})

    def delete_command(self, cmd: str) -> bool:
        existing = db.parse_curated(self.commands_file)
        if cmd not in existing:
            return False
        existing.pop(cmd, None)
        return _write_commands_txt(self.commands_file, existing)

    # ── Реиндекс системы ────────────────────────────────────
    def reindex_system(self, timeout: float = 120.0) -> dict:
        """Зовёт system_indexer.py, потом перестраивает базу."""
        result: dict = {"ok": False, "error": "", "stats": None,
                        "stdout": "", "stderr": ""}
        indexer = Path(self.indexer_script)
        if not indexer.exists():
            result["error"] = f"system_indexer.py не найден: {indexer}"
            return result
        try:
            proc = subprocess.run(
                [sys.executable, os.fspath(indexer), "--quiet"],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            result["error"] = f"индексатор не уложился в {timeout:.0f}с"
            return result
        result["stdout"] = (proc.stdout or "").strip()
        result["stderr"] = (proc.stderr or "").strip()
        if proc.returncode != 0:
            result["error"] = f"индексатор код {proc.returncode}: {result['stderr'][:200]}"
            return result
        result["stats"] = self.reload()
        result["ok"] = True
        return result


def _write_commands_txt(path: Path, commands: dict[str, list[str]]) -> bool:
    """Атомарно перезаписывает commands.txt в формате `cmd -> trig1, trig2`."""
    try:
        lines = ["# Сгенерировано Akali UI. Можно редактировать вручную.\n",
                 "# Формат: bash_команда -> фраза_1, фраза_2\n",
                 "# Силовые команды (poweroff/reboot/suspend/hibernate) НЕ хранятся\n",
                 "# здесь — они в power_guard.py с exact-match.\n\n"]
        for cmd in sorted(commands):
            triggers = commands[cmd]
            lines.append(f"{cmd} -> {', '.join(triggers)}\n")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.error("Не удалось записать %s: %s", path, e)
        return False
