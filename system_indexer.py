"""Авто-индексатор системы для ассистента «Акали».

Собирает команды из трёх источников и пишет их в `auto_commands.json`,
который потом подхватывает `app.py` поверх `commands.txt`:

1. `.desktop`-файлы (`/usr/share/applications/`, `~/.local/share/applications/`):
   GUI-приложения с готовыми именами и описаниями, в т.ч. русскими
   (`Name[ru]`, `GenericName[ru]`, `Comment[ru]`).

2. KWin shortcuts через qdbus: все горячие клавиши оконного менеджера
   (сворачивание/тайлинг/переключение столов/кастомные).

3. Бинари из `$PATH` (опционально, по флагу `--binaries`): описания из
   `whatis`. По умолчанию выключено, т.к. много шума и описания только
   на английском.

Запуск:
    python3 system_indexer.py                # desktop + KWin
    python3 system_indexer.py --binaries     # + сканер $PATH (медленно)
    python3 system_indexer.py --quiet        # без подробного лога
    python3 system_indexer.py -o other.json  # указать файл вывода

Результат:
    auto_commands.json   {"version":1, "items":[{"command":"...", "trigger":"..."}]}
"""
from __future__ import annotations

import argparse
import configparser
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Iterable

OUTPUT_FILE = "auto_commands.json"

DESKTOP_DIRS = [
    "/usr/share/applications",
    "/usr/local/share/applications",
    os.path.expanduser("~/.local/share/applications"),
    "/var/lib/flatpak/exports/share/applications",
]

# %f %u %F %U %i %c %k %v %m — placeholder'ы из freedesktop-спеки.
EXEC_PLACEHOLDER_RE = re.compile(r"\s*%[fFuUickdDnNvm]\b")

# Бинари, которые скан $PATH должен пропустить (мусор / опасное / служебное).
BINARY_SKIP_PATTERNS = [
    re.compile(r"^\["),                       # тест-команда [
    re.compile(r"^x86_64-linux-gnu-"),       # тулчейновские префиксы
    re.compile(r"^i686-linux-gnu-"),
    re.compile(r"^arm-linux-gnueabihf-"),
    re.compile(r"-old$"),
    re.compile(r"\.([0-9]+|so|orig|dpkg-.+)$"),
    re.compile(r"^update-"),                  # admin-скрипты
    re.compile(r"^pam_"),
]

# Минимальная длина «нормального» триггера, чтобы не мусорить базу
# короткими ID типа "fn" или "id".
MIN_TRIGGER_LEN = 2


def _clean_exec(exec_line: str) -> str:
    """Убирает freedesktop-плейсхолдеры из Exec-строки."""
    cleaned = EXEC_PLACEHOLDER_RE.sub("", exec_line).strip()
    return cleaned


def _normalize_trigger(t: str) -> str:
    """Чистит триггер от двойных пробелов, переводит в нижний регистр."""
    return re.sub(r"\s+", " ", t).strip().lower()


def parse_desktop_file(path: str) -> dict | None:
    """Парсит .desktop-файл, возвращает запись или None если файл нужно пропустить."""
    parser = configparser.RawConfigParser(strict=False)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            parser.read_file(f)
    except (configparser.Error, OSError):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    entry = parser["Desktop Entry"]

    if entry.get("NoDisplay", "false").strip().lower() == "true":
        return None
    if entry.get("Hidden", "false").strip().lower() == "true":
        return None
    if entry.get("Type", "Application").strip().lower() != "application":
        return None
    if entry.get("Terminal", "false").strip().lower() == "true":
        # Запуск терминальных приложений требует обёртки konsole -e — пропускаем,
        # т.к. сходу не знаем, какая Exec-строка корректно стартует через konsole.
        pass  # не пропускаем, обернём ниже сами

    exec_line = entry.get("Exec", "").strip()
    if not exec_line:
        return None
    exec_clean = _clean_exec(exec_line)
    if not exec_clean:
        return None

    # Все возможные имена/комментарии. Берём ru-варианты в приоритете.
    name_ru = entry.get("Name[ru]")
    name_en = entry.get("Name")
    generic_ru = entry.get("GenericName[ru]")
    generic_en = entry.get("GenericName")
    comment_ru = entry.get("Comment[ru]")
    comment_en = entry.get("Comment")

    needs_terminal = entry.get("Terminal", "false").strip().lower() == "true"
    if needs_terminal:
        # TUI-приложения нужно открывать в новой konsole
        command = f"konsole -e {exec_clean} &"
    else:
        command = f"{exec_clean} &"

    triggers: list[str] = []
    for candidate in (name_ru, generic_ru, comment_ru, name_en, generic_en, comment_en):
        if not candidate:
            continue
        normalized = _normalize_trigger(candidate)
        if len(normalized) < MIN_TRIGGER_LEN:
            continue
        if normalized not in triggers:
            triggers.append(normalized)

    if not triggers:
        return None

    return {
        "command": command,
        "triggers": triggers,
        "source": "desktop",
        "_meta": {
            "file": path,
            "categories": entry.get("Categories", ""),
        },
    }


def collect_desktop_apps(verbose: bool = True) -> list[dict]:
    """Сканирует все стандартные каталоги с .desktop-файлами."""
    seen_execs: set[str] = set()
    results: list[dict] = []
    for directory in DESKTOP_DIRS:
        if not os.path.isdir(directory):
            continue
        for fname in sorted(os.listdir(directory)):
            if not fname.endswith(".desktop"):
                continue
            full = os.path.join(directory, fname)
            entry = parse_desktop_file(full)
            if entry is None:
                continue
            if entry["command"] in seen_execs:
                continue
            seen_execs.add(entry["command"])
            results.append(entry)
    if verbose:
        print(f"  [desktop] найдено приложений: {len(results)}")
    return results


def list_kwin_shortcuts(verbose: bool = True) -> list[dict]:
    """Получает список всех KWin shortcuts через qdbus."""
    qdbus_bin = shutil.which("qdbus") or shutil.which("qdbus6") or shutil.which("qdbus-qt6")
    if not qdbus_bin:
        if verbose:
            print("  [kwin] qdbus не найден — пропускаю шорткаты")
        return []
    try:
        proc = subprocess.run(
            [qdbus_bin, "org.kde.kglobalaccel", "/component/kwin",
             "org.kde.kglobalaccel.Component.shortcutNames"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        if verbose:
            print(f"  [kwin] ошибка qdbus: {e}")
        return []
    if proc.returncode != 0:
        if verbose:
            print(f"  [kwin] qdbus вернул код {proc.returncode}; KWin не запущен?")
        return []

    items: list[dict] = []
    for line in proc.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        # Фильтр внутренних ID (lowercase_with_underscores, без пробелов).
        # Человекочитаемые шорткаты обычно начинаются с заглавной и содержат пробелы.
        if "_" in name and " " not in name:
            continue
        if not name[0].isupper() and " " not in name:
            continue
        command = f'qdbus org.kde.kglobalaccel /component/kwin invokeShortcut "{name}" &'
        trigger = _normalize_trigger(name)
        items.append({
            "command": command,
            "triggers": [trigger],
            "source": "kwin",
        })
    if verbose:
        print(f"  [kwin] найдено шорткатов: {len(items)}")
    return items


def index_path_binaries(verbose: bool = True, limit: int | None = None) -> list[dict]:
    """Сканирует $PATH и обогащает каждый бинарь описанием из whatis."""
    whatis_bin = shutil.which("whatis")
    if not whatis_bin and verbose:
        print("  [bin] whatis не найден — берём только имена бинарей")

    seen: set[str] = set()
    candidates: list[str] = []
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for name in sorted(entries):
            if name in seen:
                continue
            if any(p.search(name) for p in BINARY_SKIP_PATTERNS):
                continue
            full = os.path.join(directory, name)
            if not os.path.isfile(full) or not os.access(full, os.X_OK):
                continue
            seen.add(name)
            candidates.append(name)
    if limit is not None:
        candidates = candidates[:limit]

    results: list[dict] = []
    for name in candidates:
        description = None
        if whatis_bin:
            try:
                proc = subprocess.run([whatis_bin, name], capture_output=True,
                                      text=True, timeout=2)
                if proc.returncode == 0:
                    first_line = proc.stdout.strip().split("\n", 1)[0]
                    # "nmap (1) - Network exploration tool ..."
                    if " - " in first_line:
                        description = first_line.split(" - ", 1)[1].strip()
            except (subprocess.TimeoutExpired, OSError):
                pass

        triggers: list[str] = [name.lower()]
        if description and len(description) >= MIN_TRIGGER_LEN:
            triggers.append(_normalize_trigger(description))

        results.append({
            "command": name,
            "triggers": triggers,
            "source": "binary",
        })
    if verbose:
        print(f"  [bin] проиндексировано бинарей: {len(results)}")
    return results


def items_to_payload(items: Iterable[dict], extra_sources: dict[str, int]) -> dict:
    """Превращает список собранных записей в JSON-структуру."""
    flat = []
    for it in items:
        cmd = it["command"]
        for trigger in it["triggers"]:
            flat.append({"command": cmd, "trigger": trigger, "source": it.get("source", "?")})
    return {
        "version": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "sources": extra_sources,
        "items": flat,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--output", default=OUTPUT_FILE,
                    help=f"путь к выходному JSON (по умолчанию {OUTPUT_FILE})")
    ap.add_argument("--binaries", action="store_true",
                    help="дополнительно индексировать $PATH (медленнее, шумнее)")
    ap.add_argument("--binary-limit", type=int, default=None,
                    help="ограничить число бинарей для теста")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="меньше вывода")
    args = ap.parse_args()

    verbose = not args.quiet
    if verbose:
        print("⏳ Индексирую систему...")

    desktop_items = collect_desktop_apps(verbose=verbose)
    kwin_items = list_kwin_shortcuts(verbose=verbose)
    binary_items: list[dict] = []
    if args.binaries:
        binary_items = index_path_binaries(verbose=verbose, limit=args.binary_limit)

    all_items = desktop_items + kwin_items + binary_items
    payload = items_to_payload(all_items, {
        "desktop": len(desktop_items),
        "kwin": len(kwin_items),
        "binary": len(binary_items),
    })

    output_path = args.output
    if not os.path.isabs(output_path):
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), output_path)
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"❌ Не могу записать {output_path}: {e}", file=sys.stderr)
        return 2

    total_triggers = len(payload["items"])
    if verbose:
        print(f"\n✅ Записано {len(all_items)} команд / {total_triggers} триггеров в {output_path}")
        print(f"   Источники: {payload['sources']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
