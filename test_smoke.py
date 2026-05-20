"""Smoke-тест для app.py: проверяем парсинг commands.txt, дисковый кэш,
fuzzy_match и cosine_similarity. Тяжёлые зависимости (ollama, vosk,
sounddevice) подменяются фейками до импорта app.

Запуск: python3 test_smoke.py
"""
import importlib
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# --- Счётчик вызовов ollama, чтобы убедиться, что кэш реально работает ---
_embed_calls = {"count": 0}


def _fake_embed(model=None, prompt=None):
    _embed_calls["count"] += 1
    # Детерминированный «вектор» из хэша строки — для тестов важна только
    # стабильность, не математическая корректность.
    h = abs(hash(prompt))
    return {"embedding": [
        (h & 0xFF) / 255.0,
        ((h >> 8) & 0xFF) / 255.0,
        ((h >> 16) & 0xFF) / 255.0,
        ((h >> 24) & 0xFF) / 255.0,
    ]}


def _install_fakes():
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.embeddings = _fake_embed
    sys.modules["ollama"] = fake_ollama

    fake_vosk = types.ModuleType("vosk")

    class _FakeModel:
        def __init__(self, *a, **kw):
            pass

    class _FakeRecognizer:
        def __init__(self, *a, **kw):
            pass

        def AcceptWaveform(self, _b):
            return False

        def Result(self):
            return '{"text": ""}'

    fake_vosk.Model = _FakeModel
    fake_vosk.KaldiRecognizer = _FakeRecognizer
    fake_vosk.SetLogLevel = lambda *_a, **_kw: None
    sys.modules["vosk"] = fake_vosk

    fake_sd = types.ModuleType("sounddevice")

    class _FakePortAudioError(Exception):
        pass

    class _FakeStream:
        def __enter__(self):
            # Сразу «нажимаем Ctrl+C» — app.py поймает KeyboardInterrupt
            # и выйдет через sys.exit(0). Мы ловим это в _import_app().
            raise KeyboardInterrupt

        def __exit__(self, *_a):
            return False

    fake_sd.PortAudioError = _FakePortAudioError
    fake_sd.RawInputStream = lambda **_kw: _FakeStream()
    sys.modules["sounddevice"] = fake_sd


def _reset_app():
    """Удаляем app из sys.modules и счётчик, чтобы прогнать импорт заново."""
    sys.modules.pop("app", None)
    _embed_calls["count"] = 0


def _import_app():
    """Импортируем app. Аудио-цикл гейтится через if __name__ == '__main__',
    поэтому импорт ничего не запускает и не блокирует."""
    import app  # noqa: F401
    return sys.modules["app"]


def main():
    cache_path = os.path.join(HERE, "vector_cache.json")
    if os.path.exists(cache_path):
        os.remove(cache_path)

    _install_fakes()

    # Чистим auto_commands.json от предыдущих прогонов, чтобы тест был воспроизводим
    auto_path = os.path.join(HERE, "auto_commands.json")
    if os.path.exists(auto_path):
        os.remove(auto_path)

    # --- 1-й запуск: кэш отсутствует, все триггеры эмбеддятся заново ---
    _reset_app()
    app = _import_app()

    assert os.path.exists(cache_path), "Кэш-файл должен создаться после первого запуска"
    with open(cache_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["model"] == app.VECTOR_MODEL, "В кэше должна быть записана модель"
    total_triggers = sum(len(t) for t in app.commands_db.values())
    assert len(payload["items"]) == total_triggers, (
        f"В кэше {len(payload['items'])} векторов, ожидали {total_triggers}"
    )
    first_run_calls = _embed_calls["count"]
    assert first_run_calls == total_triggers, (
        f"Первый запуск должен звать ollama ровно {total_triggers} раз, было {first_run_calls}"
    )
    print(f"[1] Первый запуск: построено {first_run_calls} векторов, кэш сохранён.")

    # --- 2-й запуск: кэш должен полностью переиспользоваться (0 вызовов ollama) ---
    _reset_app()
    importlib.invalidate_caches()
    app2 = _import_app()

    assert _embed_calls["count"] == 0, (
        f"При полном попадании в кэш ollama звать НЕ должны, было {_embed_calls['count']}"
    )
    assert len(app2.vector_cache) == total_triggers
    print(f"[2] Второй запуск: 0 вызовов ollama, переиспользовано {len(app2.vector_cache)} векторов.")

    # --- 3-й запуск: меняем модель в кэше, ждём полной инвалидации ---
    with open(cache_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    payload["model"] = "some-old-model"
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    _reset_app()
    app3 = _import_app()

    assert _embed_calls["count"] == total_triggers, (
        "Смена модели должна инвалидировать весь кэш"
    )
    print(f"[3] Смена модели: кэш пересобран ({_embed_calls['count']} вызовов).")

    # --- 4-й запуск: повреждаем кэш-файл, ждём аккуратного fallback ---
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    _reset_app()
    app4 = _import_app()

    assert _embed_calls["count"] == total_triggers, "Битый кэш — пересобираем всё"
    assert os.path.exists(cache_path) and os.path.getsize(cache_path) > 100
    print(f"[4] Битый кэш: восстановлен ({_embed_calls['count']} вызовов).")

    # --- Проверка fuzzy_match и cosine_similarity ---
    # Берём первый триггер первой команды и подсовываем его как пользовательский ввод.
    first_cmd = next(iter(app4.commands_db.keys()))
    first_trigger = app4.commands_db[first_cmd][0]
    matched, ratio = app4.fuzzy_match(first_trigger, app4.commands_db)
    assert matched == first_cmd, f"fuzzy_match должен попасть в {first_cmd}, попал в {matched}"
    assert ratio >= 0.99, f"Точное совпадение должно дать ratio≈1.0, было {ratio}"
    print(f"[5] fuzzy_match: '{first_trigger}' -> {matched} (ratio={ratio:.2f})")

    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]
    assert abs(app4.cosine_similarity(v1, v2) - 1.0) < 1e-9
    assert abs(app4.cosine_similarity(v1, v3) - 0.0) < 1e-9
    assert app4.cosine_similarity([], v1) == 0.0
    assert app4.cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0
    print("[6] cosine_similarity: identical=1.0, orthogonal=0.0, edge cases OK.")

    # --- Парсер commands.txt: убеждаемся, что qdbus-строки распарсились ---
    qdbus_cmds = [c for c in app4.commands_db if c.startswith("qdbus")]
    assert len(qdbus_cmds) >= 4, f"Должно быть ≥4 qdbus-команд, нашли {len(qdbus_cmds)}"
    minimize = [c for c in qdbus_cmds if "Window Minimize" in c]
    assert minimize, "Не нашли qdbus-команду свернуть окно"
    print(f"[7] commands.txt: {len(qdbus_cmds)} qdbus-команд, миграция xdotool->qdbus прошла.")

    # --- attempt_audio_recovery / build_commands_db / reindex_system живы ---
    assert callable(app4.attempt_audio_recovery), "attempt_audio_recovery отсутствует"
    assert callable(app4.reindex_system), "reindex_system отсутствует"
    assert callable(app4.build_commands_db), "build_commands_db отсутствует"
    # Авто-индекс отсутствует — база равна curated.
    assert app4.load_auto_commands() == {}
    print("[8] Новые функции (audio-recovery / reindex / build_commands_db) на месте.")

    # --- Мерж auto+curated: пишем фейковый auto_commands.json и проверяем объединение ---
    fake_auto = {
        "version": 1,
        "generated_at": "2026-05-20T00:00:00+00:00",
        "sources": {"desktop": 1, "kwin": 0, "binary": 0},
        "items": [
            {"command": "konsole &", "trigger": "второй синоним для konsole", "source": "desktop"},
            {"command": "xeyes &", "trigger": "покажи глаза", "source": "desktop"},
        ],
    }
    with open(auto_path, "w", encoding="utf-8") as f:
        json.dump(fake_auto, f, ensure_ascii=False)
    _reset_app()
    if os.path.exists(cache_path):
        os.remove(cache_path)
    app5 = _import_app()
    # Новая команда из auto должна появиться
    assert "xeyes &" in app5.commands_db
    assert "покажи глаза" in app5.commands_db["xeyes &"]
    # При конфликте «konsole &» (она в commands.txt) curated приоритетнее, но auto-триггер
    # должен быть в конце списка.
    konsole_triggers = app5.commands_db["konsole &"]
    assert "второй синоним для konsole" in konsole_triggers
    assert konsole_triggers[0] != "второй синоним для konsole", "curated должен быть первым"
    print(f"[9] Мерж auto+curated: {len(app5.commands_db)} команд (+2 из auto), приоритет curated.")

    # --- attempt_audio_recovery не должен падать на отсутствии systemctl/pulseaudio ---
    # Просто вызываем и убеждаемся, что вернётся без исключения.
    # Патчим time.sleep в app, чтобы не ждать 2 сек.
    original_sleep = app5.time.sleep
    app5.time.sleep = lambda *_a, **_kw: None
    try:
        app5.attempt_audio_recovery()
    finally:
        app5.time.sleep = original_sleep
    print("[10] attempt_audio_recovery() отработал без исключений (игнорировал отсутствующие бинари).")

    # --- Реиндексация на лету: подменяем subprocess.run, убеждаемся, что вызывается ---
    captured = {}
    original_run = app5.subprocess.run

    def _fake_run(args, **kwargs):
        captured["args"] = args
        # Ничего не меняем, имитируем успех
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    app5.subprocess.run = _fake_run
    try:
        app5.reindex_system()
    finally:
        app5.subprocess.run = original_run
    assert "args" in captured, "reindex_system не вызвал subprocess.run"
    assert any("system_indexer" in str(a) for a in captured["args"]), \
        f"reindex_system должен был вызвать system_indexer, вызвал: {captured['args']}"
    print("[11] reindex_system() корректно зовёт system_indexer.py.")

    # --- Чистим за собой ---
    if os.path.exists(cache_path):
        os.remove(cache_path)
    if os.path.exists(auto_path):
        os.remove(auto_path)
    print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✓")


if __name__ == "__main__":
    main()
