"""Smoke-тесты для akali.core.

Подменяем ollama (его нет на CI и в окружениях разработки), затем
проверяем парсинг commands.txt, мёрж с auto_commands.json, дисковый
кэш, fuzzy_match, cosine_similarity и реиндекс через subprocess.

Запуск:  python3 test_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# --- Фейк ollama ---
_embed_calls = {"count": 0}


def _fake_embed(model=None, prompt=None):
    _embed_calls["count"] += 1
    h = abs(hash(prompt or ""))
    return {"embedding": [
        (h & 0xFF) / 255.0,
        ((h >> 8) & 0xFF) / 255.0,
        ((h >> 16) & 0xFF) / 255.0,
        ((h >> 24) & 0xFF) / 255.0,
    ]}


def _install_fake_ollama():
    fake = types.ModuleType("ollama")
    fake.embeddings = _fake_embed
    sys.modules["ollama"] = fake


def _reset():
    _embed_calls["count"] = 0
    for name in list(sys.modules):
        if name == "akali" or name.startswith("akali."):
            sys.modules.pop(name, None)


def _load_core():
    """Импортирует akali.core.backend.AssistantCore с уже установленным фейком ollama."""
    from akali.core.backend import AssistantCore  # noqa: WPS433
    return AssistantCore(
        commands_file=os.path.join(HERE, "commands.txt"),
        auto_commands_file=os.path.join(HERE, "auto_commands.json"),
        vector_cache_file=os.path.join(HERE, "vector_cache.json"),
        indexer_script=os.path.join(HERE, "system_indexer.py"),
    )


# ============================================================
def main():
    _install_fake_ollama()

    # Чистим артефакты предыдущих прогонов
    for fname in ("vector_cache.json", "auto_commands.json"):
        path = os.path.join(HERE, fname)
        if os.path.exists(path):
            os.remove(path)

    # ============================================================
    # Тест 1: первая сборка строит и сохраняет кэш
    # ============================================================
    _reset()
    _embed_calls["count"] = 0
    core = _load_core()
    stats1 = core.reload()
    assert stats1.vector_total > 0, "первая сборка не дала ни одного вектора"
    assert _embed_calls["count"] == stats1.vector_total, \
        f"ожидали {stats1.vector_total} вызовов ollama, было {_embed_calls['count']}"
    assert os.path.exists(core.vector_cache_file), "кэш не сохранился на диск"
    print(f"[1] Первая сборка: {stats1.vector_total} векторов, {_embed_calls['count']} вызовов ollama.")

    # ============================================================
    # Тест 2: второй прогон — полное попадание в кэш, 0 вызовов
    # ============================================================
    _reset()
    _embed_calls["count"] = 0
    core2 = _load_core()
    stats2 = core2.reload()
    assert stats2.vector_total == stats1.vector_total, "размер кэша расходится"
    assert _embed_calls["count"] == 0, \
        f"второй прогон должен попасть в кэш, было {_embed_calls['count']} вызовов"
    print(f"[2] Кэш-хит: 0 вызовов ollama на {stats2.vector_total} векторов.")

    # ============================================================
    # Тест 3: смена векторной модели инвалидирует кэш
    # ============================================================
    _reset()
    _embed_calls["count"] = 0
    core3 = _load_core()
    core3.vector_model = "totally-different-model"
    stats3 = core3.reload()
    assert _embed_calls["count"] == stats3.vector_total, \
        "смена модели должна форсировать пересчёт всех векторов"
    print(f"[3] Смена модели → {_embed_calls['count']} новых эмбеддингов.")

    # ============================================================
    # Тест 4: битый JSON → восстанавливается, не падает
    # ============================================================
    cache_path = os.path.join(HERE, "vector_cache.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write("{not_a_json::")
    _reset()
    _embed_calls["count"] = 0
    core4 = _load_core()
    stats4 = core4.reload()
    assert stats4.vector_total > 0, "после битого JSON всё ещё должны построить кэш"
    assert _embed_calls["count"] == stats4.vector_total, \
        "после битого JSON должны пересчитать все векторы"
    print(f"[4] Битый JSON → восстановили {_embed_calls['count']} векторов.")

    # ============================================================
    # Тест 5: fuzzy_match находит точные триггеры
    # ============================================================
    _reset()
    _embed_calls["count"] = 0
    core5 = _load_core()
    core5.reload()
    # У нас в commands.txt должно быть "скрыть окно" → qdbus invokeShortcut Window Minimize
    m = core5.fuzzy_match("скрыть окно")
    assert m.found, f"fuzzy_match не нашёл 'скрыть окно' (max={m.confidence:.2f})"
    assert "qdbus" in m.cmd or "kglobalaccel" in m.cmd, \
        f"ожидали qdbus-команду, было: {m.cmd}"
    print(f"[5] fuzzy «скрыть окно» → {m.cmd[:40]}…  ({m.confidence:.2f})")

    # ============================================================
    # Тест 6: cosine_similarity
    # ============================================================
    from akali.core.backend import AssistantCore as AC
    assert AC.cosine_similarity([1, 0, 0], [1, 0, 0]) == 1.0
    assert AC.cosine_similarity([1, 0, 0], [0, 1, 0]) == 0.0
    assert AC.cosine_similarity([], [1, 2, 3]) == 0.0
    assert AC.cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0
    print("[6] cosine_similarity ок (1.0, 0.0, граничные).")

    # ============================================================
    # Тест 7: миграция xdotool → qdbus прошла, в базе хватает qdbus-команд
    # ============================================================
    qdbus_cmds = [c for c in core5.commands_db.keys() if "qdbus" in c]
    assert len(qdbus_cmds) >= 4, \
        f"qdbus-команд должно быть не меньше 4, нашли {len(qdbus_cmds)}"
    print(f"[7] В базе {len(qdbus_cmds)} qdbus-команд — миграция xdotool применена.")

    # ============================================================
    # Тест 8: API AssistantCore содержит все обещанные методы
    # ============================================================
    required = ("reload", "fuzzy_match", "vector_search", "find",
                "build_commands_db", "load_or_build_vector_cache",
                "execute", "reindex_system", "detect_wake_word",
                "is_reindex_phrase")
    for name in required:
        assert hasattr(AC, name), f"AssistantCore.{name} отсутствует"
    print(f"[8] API AssistantCore: все {len(required)} методов на месте.")

    # ============================================================
    # Тест 9: мёрж auto+curated с приоритетом curated
    # ============================================================
    # Готовим auto_commands.json с пересекающейся командой
    auto_payload = {
        "version": 1,
        "sources": {"desktop": 1, "kwin": 0, "binary": 0},
        "items": [
            # Совпадает с curated: должна перебиться curated триггерами
            {"command": "firefox-esr &", "trigger": "auto-trigger-firefox"},
            # Уникальная auto-команда
            {"command": "echo auto-only-cmd &", "trigger": "уникальный авто триггер"},
        ],
    }
    auto_path = os.path.join(HERE, "auto_commands.json")
    with open(auto_path, "w", encoding="utf-8") as f:
        json.dump(auto_payload, f, ensure_ascii=False)

    _reset()
    _embed_calls["count"] = 0
    core9 = _load_core()
    stats9 = core9.reload()
    assert "echo auto-only-cmd &" in core9.commands_db, "уникальная auto-команда не попала в базу"
    assert "firefox-esr &" in core9.commands_db, "curated команда исчезла после мёржа"
    triggers = core9.commands_db["firefox-esr &"]
    assert triggers[0] != "auto-trigger-firefox", \
        f"первым должен идти curated-триггер, а не auto ({triggers[:3]})"
    assert "auto-trigger-firefox" in triggers, "auto-триггер должен добавиться в хвост"
    print(f"[9] Мёрж: {stats9.commands_total} команд (curated={stats9.curated_count}, "
          f"auto={stats9.auto_count}), curated приоритет ок.")
    os.remove(auto_path)

    # ============================================================
    # Тест 10: reindex_system дёргает system_indexer.py (подменим на echo)
    # ============================================================
    _reset()
    core10 = _load_core()
    core10.reload()
    # Подменяем путь к скрипту на маленький фейковый, который создаёт пустой auto_commands.json
    fake_script = os.path.join(HERE, "_fake_indexer.py")
    with open(fake_script, "w", encoding="utf-8") as f:
        f.write(
            "import json, os\n"
            "p = os.path.join(os.path.dirname(__file__), 'auto_commands.json')\n"
            "with open(p, 'w', encoding='utf-8') as fp:\n"
            "    json.dump({'version': 1, 'sources': {'desktop': 0}, 'items': []}, fp)\n"
        )
    try:
        core10.indexer_script = fake_script
        res = core10.reindex_system(timeout=10)
        assert res["ok"], f"reindex не отработал: {res}"
        assert res["stats"] is not None, "stats должен быть заполнен"
        print(f"[10] reindex_system: subprocess + reload отработали ок.")
    finally:
        if os.path.exists(fake_script):
            os.remove(fake_script)
        # Удаляем артефакт фейкового индекса
        if os.path.exists(auto_path):
            os.remove(auto_path)

    # ============================================================
    # Тест 11: detect_wake_word и is_reindex_phrase
    # ============================================================
    _reset()
    core11 = _load_core()
    core11.reload()
    assert core11.detect_wake_word(["акали", "открой", "браузер"]) == 0
    assert core11.detect_wake_word(["открой", "ассистент", "браузер"]) == 1
    assert core11.detect_wake_word(["просто", "команда"]) == -1
    assert core11.is_reindex_phrase("переиндексируй систему")
    assert core11.is_reindex_phrase("обнови команды пожалуйста")
    assert not core11.is_reindex_phrase("открой браузер")
    print("[11] detect_wake_word + is_reindex_phrase ок.")

    print()
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
