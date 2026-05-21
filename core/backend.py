"""Чистая логика ассистента «Акали».

Не зависит от UI и аудио (sounddevice, vosk). Может быть импортирован в
тестах и аудио-worker'е. Хранит curated/auto-команды, кэш эмбеддингов,
выполняет fuzzy/vector-поиск и запуск команд через subprocess.

Использование:
    core = AssistantCore(base_dir="/path/to/akali")
    core.reload()                       # парсит commands.txt + auto_commands.json + строит вектор-кэш
    match = core.fuzzy_match("сверни окно")
    if not match.cmd:
        match = core.vector_search("сверни окно")
    if match.cmd:
        result = core.execute(match.cmd)
"""
from __future__ import annotations

import difflib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

import ollama


# === Пороги по умолчанию ===
SIMILARITY_THRESHOLD = 0.70
VECTOR_THRESHOLD = 0.55
WAKE_THRESHOLD = 0.75
ACTIVE_WINDOW_SECONDS = 5.0

# === Дефолтные слова и фразы ===
DEFAULT_WAKE_WORDS = ("компьютер", "ассистент", "акали")
DEFAULT_REINDEX_TRIGGERS = (
    "переиндексируй",
    "обнови команд",
    "пересканируй систем",
)
DEFAULT_VECTOR_MODEL = "all-minilm"


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


@dataclass
class MatchResult:
    """Результат поиска команды по голосовой фразе."""
    cmd: Optional[str] = None
    confidence: float = 0.0
    trigger: str = ""
    method: str = ""  # "fuzzy", "vector" или ""

    @property
    def found(self) -> bool:
        return self.cmd is not None


@dataclass
class ReloadStats:
    """Сводка после reload(): сколько команд и векторов получилось."""
    commands_total: int = 0
    curated_count: int = 0
    auto_count: int = 0
    vectors_reused: int = 0
    vectors_built: int = 0
    vector_total: int = 0
    auto_sources: dict = field(default_factory=dict)


class AssistantCore:
    """Координатор: база команд, поиск, выполнение, реиндекс."""

    def __init__(self, base_dir: Optional[str] = None,
                 vector_model: str = DEFAULT_VECTOR_MODEL):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.commands_file = os.path.join(self.base_dir, "commands.txt")
        self.auto_commands_file = os.path.join(self.base_dir, "auto_commands.json")
        self.vector_cache_file = os.path.join(self.base_dir, "vector_cache.json")
        self.indexer_script = os.path.join(self.base_dir, "system_indexer.py")
        self.vector_model = vector_model

        # Изменяемые настройки (UI может править)
        self.fuzzy_threshold = SIMILARITY_THRESHOLD
        self.vector_threshold = VECTOR_THRESHOLD
        self.wake_threshold = WAKE_THRESHOLD
        self.wake_words = list(DEFAULT_WAKE_WORDS)
        self.reindex_triggers = list(DEFAULT_REINDEX_TRIGGERS)

        # Текущее состояние
        self.commands_db: dict[str, list[str]] = {}
        self.vector_cache: list[dict] = []
        self.auto_sources: dict = {}
        self._curated_count = 0
        self._auto_count = 0

    # ---------- Embedding ----------
    def get_embedding(self, text: str) -> list[float]:
        try:
            response = ollama.embeddings(model=self.vector_model, prompt=text)
            return response.get('embedding', [])
        except Exception:
            return []

    @staticmethod
    def cosine_similarity(v1, v2) -> float:
        if not v1 or not v2:
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        mag1 = math.sqrt(sum(a * a for a in v1))
        mag2 = math.sqrt(sum(b * b for b in v2))
        if mag1 == 0 or mag2 == 0:
            return 0.0
        return dot / (mag1 * mag2)

    # ---------- База команд ----------
    def _parse_commands_txt(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        if not os.path.exists(self.commands_file):
            return result
        with open(self.commands_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '->' not in line:
                    continue
                cmd, triggers_str = line.split('->', 1)
                cmd = cmd.strip()
                triggers = [t.strip().lower() for t in triggers_str.split(',') if t.strip()]
                if cmd and triggers:
                    result[cmd] = triggers
        return result

    def _parse_auto_commands_json(self) -> tuple[dict[str, list[str]], dict]:
        if not os.path.exists(self.auto_commands_file):
            return {}, {}
        try:
            with open(self.auto_commands_file, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}, {}
        db: dict[str, list[str]] = {}
        for item in payload.get("items", []):
            cmd = item.get("command")
            trigger = item.get("trigger")
            if not cmd or not trigger:
                continue
            if cmd not in db:
                db[cmd] = []
            if trigger not in db[cmd]:
                db[cmd].append(trigger)
        return db, payload.get("sources", {}) or {}

    def build_commands_db(self) -> dict[str, list[str]]:
        """Сливает auto + curated, curated имеет приоритет в конфликтах."""
        auto, sources = self._parse_auto_commands_json()
        curated = self._parse_commands_txt()
        merged = dict(auto)
        for cmd, triggers in curated.items():
            if cmd in merged:
                seen = set(triggers)
                extra = [t for t in merged[cmd] if t not in seen]
                merged[cmd] = triggers + extra
            else:
                merged[cmd] = triggers
        self.auto_sources = sources
        self._curated_count = len(curated)
        self._auto_count = len(auto)
        return merged

    # ---------- Векторный кэш ----------
    def load_or_build_vector_cache(self, commands_dict: dict[str, list[str]]) -> tuple[int, int]:
        """Восстанавливает векторы из кэша и достраивает новые. Возвращает (reused, built)."""
        cached: dict[tuple[str, str], list[float]] = {}
        if os.path.exists(self.vector_cache_file):
            try:
                with open(self.vector_cache_file, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                if payload.get("model") == self.vector_model:
                    for item in payload.get("items", []):
                        cached[(item["command"], item["trigger"])] = item["vector"]
            except (json.JSONDecodeError, KeyError, TypeError, OSError):
                cached = {}

        cache: list[dict] = []
        reused = 0
        built = 0
        for cmd, triggers in commands_dict.items():
            for trigger in triggers:
                key = (cmd, trigger)
                if key in cached:
                    cache.append({"command": cmd, "trigger": trigger, "vector": cached[key]})
                    reused += 1
                else:
                    vec = self.get_embedding(trigger)
                    if vec:
                        cache.append({"command": cmd, "trigger": trigger, "vector": vec})
                        built += 1

        if built > 0 or len(cache) != len(cached):
            try:
                tmp_path = self.vector_cache_file + ".tmp"
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump({"model": self.vector_model, "items": cache}, f, ensure_ascii=False)
                os.replace(tmp_path, self.vector_cache_file)
            except OSError:
                pass

        self.vector_cache = cache
        return reused, built

    # ---------- Lifecycle ----------
    def reload(self) -> ReloadStats:
        """Полная пересборка: парс commands → парс auto → построить векторы."""
        self.commands_db = self.build_commands_db()
        reused, built = self.load_or_build_vector_cache(self.commands_db)
        return ReloadStats(
            commands_total=len(self.commands_db),
            curated_count=self._curated_count,
            auto_count=self._auto_count,
            vectors_reused=reused,
            vectors_built=built,
            vector_total=len(self.vector_cache),
            auto_sources=dict(self.auto_sources),
        )

    # ---------- Поиск ----------
    def fuzzy_match(self, text: str) -> MatchResult:
        text = text.lower()
        best_cmd: Optional[str] = None
        best_trigger = ""
        max_ratio = 0.0
        for cmd, triggers in self.commands_db.items():
            for trigger in triggers:
                ratio = difflib.SequenceMatcher(None, text, trigger).ratio()
                if ratio > max_ratio:
                    max_ratio = ratio
                    best_cmd = cmd
                    best_trigger = trigger
        if max_ratio >= self.fuzzy_threshold:
            return MatchResult(cmd=best_cmd, confidence=max_ratio,
                               trigger=best_trigger, method="fuzzy")
        return MatchResult(confidence=max_ratio, trigger=best_trigger)

    def vector_search(self, text: str) -> MatchResult:
        user_vec = self.get_embedding(text)
        if not user_vec:
            return MatchResult()
        best_cmd: Optional[str] = None
        best_trigger = ""
        max_score = 0.0
        for item in self.vector_cache:
            score = self.cosine_similarity(user_vec, item["vector"])
            if score > max_score:
                max_score = score
                best_cmd = item["command"]
                best_trigger = item["trigger"]
        if max_score >= self.vector_threshold:
            return MatchResult(cmd=best_cmd, confidence=max_score,
                               trigger=best_trigger, method="vector")
        return MatchResult(confidence=max_score, trigger=best_trigger)

    def find(self, text: str) -> MatchResult:
        """Сначала fuzzy, если не нашёл — vector."""
        m = self.fuzzy_match(text)
        if m.found:
            return m
        return self.vector_search(text)

    # ---------- Wake-word ----------
    def detect_wake_word(self, words: list[str]) -> int:
        """Возвращает индекс слова, где сработал wake-word, или -1."""
        for i, word in enumerate(words):
            for ww in self.wake_words:
                if difflib.SequenceMatcher(None, word, ww).ratio() >= self.wake_threshold:
                    return i
        return -1

    def is_reindex_phrase(self, text: str) -> bool:
        low = text.lower()
        return any(rt in low for rt in self.reindex_triggers)

    # ---------- Выполнение ----------
    def execute(self, cmd: str, timeout: float = 15.0) -> CommandResult:
        """Запускает команду через subprocess. & в конце → фоновый процесс."""
        is_background = cmd.strip().endswith('&')
        try:
            if is_background:
                subprocess.Popen(cmd, shell=True,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return CommandResult(cmd=cmd, is_background=True)
            proc = subprocess.run(cmd, shell=True, capture_output=True,
                                  text=True, timeout=timeout)
            return CommandResult(
                cmd=cmd,
                stdout=proc.stdout.strip() if proc.stdout else "",
                stderr=proc.stderr.strip() if proc.stderr else "",
                returncode=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(cmd=cmd, timed_out=True,
                                 error=f"Таймаут {timeout:.0f}с")
        except Exception as e:
            return CommandResult(cmd=cmd, error=str(e))

    # ---------- Реиндекс ----------
    def reindex_system(self, timeout: float = 120.0) -> dict:
        """Зовёт system_indexer.py как subprocess, потом перестраивает базу.

        Возвращает dict: {ok: bool, error: str, stats: ReloadStats|None, stdout, stderr}.
        """
        result = {"ok": False, "error": "", "stats": None, "stdout": "", "stderr": ""}
        if not os.path.exists(self.indexer_script):
            result["error"] = f"system_indexer.py не найден: {self.indexer_script}"
            return result
        try:
            proc = subprocess.run(
                [sys.executable, self.indexer_script, "--quiet"],
                capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            result["error"] = f"индексатор не уложился в {timeout:.0f}с"
            return result
        result["stdout"] = proc.stdout.strip()
        result["stderr"] = proc.stderr.strip()
        if proc.returncode != 0:
            result["error"] = f"индексатор код {proc.returncode}: {proc.stderr.strip()[:200]}"
            return result
        stats = self.reload()
        result["ok"] = True
        result["stats"] = stats
        return result
