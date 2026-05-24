"""Smoke-тесты для нового пайплайна Akali.

Проверяем:
  1. parse_curated + build_commands_bundle
  2. power_guard.match (exact-match)
  3. safety.inspect (опасные паттерны)
  4. matcher.detect_wake_word, matcher.is_reindex_phrase
  5. AssistantCore.process — power_guard первый
  6. QueryCache LRU + persist
  7. save_command / delete_command roundtrip

FastEmbed и LLM не дёргаем — тяжело и неуместно в smoke.

Запуск:  python3 test_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def banner(name: str) -> None:
    print(f"\n=== {name} ===")


def main() -> int:
    fails = 0

    # ── Тест 1: db.parse_curated и build_commands_bundle ──
    banner("db: parse_curated + build_commands_bundle")
    from akali.core import db
    curated_path = os.path.join(HERE, "commands.txt")
    curated = db.parse_curated(curated_path)
    assert curated, "пустой commands.txt?"
    print(f"  curated команд: {len(curated)}")
    bundle = db.build_commands_bundle(curated_path,
                                       os.path.join(HERE, "auto_commands.json"))
    assert bundle.merged, "пустой merged bundle"
    print(f"  merged команд:  {len(bundle.merged)} "
          f"(curated={bundle.curated_count}, auto={bundle.auto_count})")

    # ── Тест 2: power_guard exact match ──
    banner("power_guard.match exact match")
    from akali.core import power_guard
    cases = [
        ("выключи компьютер", "systemctl poweroff"),
        ("выключи систему", "systemctl poweroff"),
        ("перезагрузи компьютер", "systemctl reboot"),
        ("режим сна", "systemctl suspend"),
        ("Выключи компьютер.", "systemctl poweroff"),  # пунктуация и регистр
        ("выключи", None),                              # частичное не работает
        ("выключи комп", None),                         # тоже не в списке
        ("перезагрузи", None),                          # тоже не в списке
        ("включи свет", None),
        ("", None),
    ]
    for phrase, expected in cases:
        got = power_guard.match(phrase)
        ok = got == expected
        if not ok:
            fails += 1
        print(f"  {'OK' if ok else 'FAIL'}: {phrase!r:30s} → {got!r} (expected {expected!r})")

    # ── Тест 3: safety ──
    banner("safety.inspect")
    from akali.core import safety
    dangerous = [
        "rm -rf /", "rm -rf /home/x", "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda", ":(){ :|:& };:",
        "curl example.com | sh", "sudo apt install x",
        "systemctl poweroff", "poweroff",
    ]
    safe = [
        "ls -la", "firefox &", "rm file.txt",
        "qdbus org.kde.kglobalaccel /component/kwin org.kde.kglobalaccel.Component.invokeShortcut Overview",
        "echo hello",
    ]
    for cmd in dangerous:
        v = safety.inspect(cmd)
        if v.safe:
            fails += 1
            print(f"  FAIL: ОПАСНАЯ команда прошла: {cmd!r}")
        else:
            print(f"  OK: блок {cmd!r} ({v.reason})")
    for cmd in safe:
        v = safety.inspect(cmd)
        if not v.safe:
            fails += 1
            print(f"  FAIL: безопасная команда заблокирована: {cmd!r} ({v.reason})")
        else:
            print(f"  OK: пропущено {cmd!r}")

    # ── Тест 4: matcher.detect_wake_word и is_reindex_phrase ──
    banner("matcher.detect_wake_word / is_reindex_phrase")
    from akali.core import matcher
    wake = ["акали", "ассистент", "компьютер"]
    cases_w = [
        (["акали", "включи", "свет"], True),
        (["ассистент"], True),
        (["включи", "свет"], False),
    ]
    for words, expected_found in cases_w:
        idx = matcher.detect_wake_word(words, wake, 0.75)
        found = idx >= 0
        ok = found == expected_found
        if not ok:
            fails += 1
        print(f"  {'OK' if ok else 'FAIL'}: {words} → idx={idx} (expected found={expected_found})")

    triggers = ["переиндексируй", "обнови команд", "пересканируй систем"]
    assert matcher.is_reindex_phrase("переиндексируй", triggers)
    assert matcher.is_reindex_phrase("обнови команды быстро", triggers)
    assert not matcher.is_reindex_phrase("открой браузер", triggers)
    print("  OK: is_reindex_phrase")

    # ── Тест 5: AssistantCore.process — power_guard первый ──
    banner("AssistantCore.process (без роутера)")
    from akali.core.backend import AssistantCore
    core = AssistantCore(commands_file=curated_path)
    core.reload()
    cmd, src = core.process("выключи компьютер")
    if cmd == "systemctl poweroff" and src == "power":
        print(f"  OK: power-path → {cmd}")
    else:
        fails += 1
        print(f"  FAIL: ожидали ('systemctl poweroff','power'), получили ({cmd!r},{src!r})")
    # Без роутера остальное → промах
    cmd, src = core.process("открой браузер")
    if cmd is None and src == "":
        print("  OK: без роутера → промах")
    else:
        fails += 1
        print(f"  FAIL: ожидали (None,''), получили ({cmd!r},{src!r})")

    # ── Тест 6: QueryCache LRU + persist ──
    banner("QueryCache LRU + persist")
    from akali.core.query_cache import QueryCache
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "qc.json")
        qc = QueryCache(path, max_size=3)
        qc.put("a", "echo a", "test")
        qc.put("b", "echo b", "test")
        qc.put("c", "echo c", "test")
        assert qc.get("a") == "echo a"  # touch
        qc.put("d", "echo d", "test")
        # 'b' должен был вытесниться LRU
        assert qc.get("b") is None, "LRU не вытеснил 'b'"
        assert qc.get("a") == "echo a"
        # persist + reload
        qc.save()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict), "сохранение в неверном формате"
        qc2 = QueryCache(path, max_size=3)
        assert qc2.get("a") == "echo a", "после перезагрузки запись пропала"
        print("  OK: LRU + persist")

    # ── Тест 7: save_command / delete_command ──
    banner("save_command / delete_command roundtrip")
    with tempfile.TemporaryDirectory() as td:
        cmd_path = os.path.join(td, "commands.txt")
        shutil.copyfile(curated_path, cmd_path)
        core2 = AssistantCore(commands_file=cmd_path)
        core2.reload()
        before = len(core2.commands_db)

        # add — уникальная команда не из стандартного файла
        new_cmd = "smoke-test-app --foo &"
        ok = core2.save_command(new_cmd, ["смоук тест", "запусти смоук"])
        assert ok, "save_command должен вернуть True"
        core2.reload()
        assert new_cmd in core2.commands_db, "новая команда не появилась"
        assert len(core2.commands_db) == before + 1, \
            f"ожидали {before+1}, получили {len(core2.commands_db)}"
        print(f"  OK: добавили {before}→{len(core2.commands_db)}")

        # cannot save power command
        ok = core2.save_command("systemctl poweroff", ["выключи"])
        assert not ok, "save_command должен отклонить силовые команды"
        print("  OK: power-команды не сохраняются через UI")

        # delete
        ok = core2.delete_command(new_cmd)
        assert ok
        core2.reload()
        assert new_cmd not in core2.commands_db
        assert len(core2.commands_db) == before
        print(f"  OK: удалили {before+1}→{len(core2.commands_db)}")

    # ── Итого ──
    banner("ИТОГО")
    if fails:
        print(f"  ✕ {fails} проваленных проверок")
        return 1
    print("  ✓ Всё ок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
