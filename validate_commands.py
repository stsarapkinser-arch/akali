"""Прогоняет commands.txt и проверяет, что все упоминаемые бинари
доступны в PATH (через shutil.which). Цель — быстро увидеть, каких
пакетов не хватает на текущей машине.

Запуск:  python3 validate_commands.py [-q]
Флаги:   -q / --quiet — печатать только отсутствующие.
Exit-код: 0 если все бинари на месте, 1 если есть пропуски.
"""
import argparse
import os
import re
import shlex
import shutil
import sys
from collections import defaultdict

COMMANDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands.txt")

# Команды, которые НЕ являются программами, а bash-builtin / shell-конструкциями.
# Их проверять через shutil.which бессмысленно.
SHELL_BUILTINS = {"echo", "cd", "pwd", "exit", ":", "true", "false", "[", "test"}

# Простой парсер пайплайнов: режем по |, ;, &&, ||
PIPE_SPLIT = re.compile(r"\s*(?:\|\||&&|\||;)\s*")


def parse_commands(path):
    """Возвращает список (raw_command_line, trigger_phrases)."""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "->" not in stripped:
                continue
            cmd_part, triggers_part = stripped.split("->", 1)
            cmd = cmd_part.strip().rstrip("&").strip()
            triggers = [t.strip() for t in triggers_part.split(",") if t.strip()]
            items.append((cmd, triggers))
    return items


def extract_binaries(cmd_line):
    """Из bash-строки достаём все бинари: учитываем пайпы, ;, &&, ||."""
    binaries = []
    for segment in PIPE_SPLIT.split(cmd_line):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            # Незакрытые кавычки и пр. — пропускаем
            continue
        # Пропускаем env-присваивания типа `FOO=bar cmd ...`
        i = 0
        while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("="):
            i += 1
        if i >= len(tokens):
            continue
        binary = tokens[i]
        # Уже абсолютный путь — проверяем существование
        if "/" in binary:
            binaries.append(binary)
        else:
            binaries.append(binary)
    return binaries


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="печатать только отсутствующие бинари")
    args = ap.parse_args()

    if not os.path.exists(COMMANDS_FILE):
        print(f"[!] Файл не найден: {COMMANDS_FILE}", file=sys.stderr)
        return 2

    items = parse_commands(COMMANDS_FILE)
    if not items:
        print("[!] В commands.txt не нашли ни одной команды.", file=sys.stderr)
        return 2

    total_triggers = sum(len(t) for _, t in items)
    print(f"[i] Команд в базе: {len(items)}, синонимов: {total_triggers}.\n")

    bin_to_commands = defaultdict(list)
    for cmd, _ in items:
        for binary in extract_binaries(cmd):
            bin_to_commands[binary].append(cmd)

    present = {}
    missing = {}
    for binary, cmds in bin_to_commands.items():
        if binary in SHELL_BUILTINS:
            continue
        if "/" in binary:
            ok = os.path.exists(binary) and os.access(binary, os.X_OK)
            location = binary if ok else None
        else:
            location = shutil.which(binary)
            ok = location is not None
        if ok:
            present[binary] = (location, cmds)
        else:
            missing[binary] = cmds

    if not args.quiet:
        print(f"=== НАЙДЕНЫ ({len(present)}) ===")
        for binary in sorted(present):
            location, cmds = present[binary]
            print(f"  [\u2713] {binary:<25} -> {location}  ({len(cmds)} команд)")

    print(f"\n=== ОТСУТСТВУЮТ ({len(missing)}) ===")
    if not missing:
        print("  (всё на месте)")
    else:
        for binary in sorted(missing):
            cmds = missing[binary]
            example = cmds[0]
            extra = f"  (+{len(cmds) - 1} ещё)" if len(cmds) > 1 else ""
            print(f"  [\u2717] {binary:<25} \u2014 пример: `{example}`{extra}")
        print("\nПодсказка по доставке (Kali):")
        print("  sudo apt update")
        print("  sudo apt install -y kali-linux-default kali-tools-top10")
        print("  sudo apt install -y playerctl mtr-tiny lm-sensors tree btop")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
