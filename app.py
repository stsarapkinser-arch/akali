import sys
import os
import subprocess
import json
import time
import math
import difflib
import sounddevice as sd
from vosk import Model, KaldiRecognizer, SetLogLevel
import ollama

SetLogLevel(-1)

# === НАСТРОЙКИ СИСТЕМЫ ===
HERE = os.path.dirname(os.path.abspath(__file__))
COMMANDS_FILE = os.path.join(HERE, "commands.txt")
VECTOR_CACHE_FILE = os.path.join(HERE, "vector_cache.json")
AUTO_COMMANDS_FILE = os.path.join(HERE, "auto_commands.json")
INDEXER_SCRIPT = os.path.join(HERE, "system_indexer.py")
VOSK_MODEL_DIR = os.path.join(HERE, "model")

# Настройки поиска (Текстовое совпадение)
SIMILARITY_THRESHOLD = 0.70  
WAKE_WORDS = ["компьютер", "ассистент", "акали"]
WAKE_THRESHOLD = 0.75
ACTIVE_WINDOW = 5.0

# Голосовые фразы, запускающие реиндексацию системы без перезапуска app.py.
# Проверяются через вхождение подстроки, поэтому хватит и короткого корня.
REINDEX_TRIGGERS = (
    "переиндексируй",
    "обнови команд",
    "пересканируй систем",
)

# Настройки авто-восстановления аудио
AUDIO_MAX_RETRIES = 5
AUDIO_INITIAL_BACKOFF = 2.0

# Настройки поиска (Векторный смысл)
VECTOR_MODEL = "all-minilm"
VECTOR_THRESHOLD = 0.55

# === ФУНКЦИИ ВЕКТОРНОЙ МАТЕМАТИКИ ===
def get_embedding(text):
    try:
        response = ollama.embeddings(model=VECTOR_MODEL, prompt=text)
        return response.get('embedding', [])
    except Exception as e:
        print(f"❌ Ошибка вектора: {e}")
        return []

def cosine_similarity(v1, v2):
    if not v1 or not v2: return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0: return 0.0
    return dot_product / (mag1 * mag2)

# === ПАРСЕРЫ БАЗЫ КОМАНД ===
def load_commands():
    """Парсим ручной commands.txt."""
    commands_dict = {}
    if not os.path.exists(COMMANDS_FILE):
        return {}
    with open(COMMANDS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '->' in line:
                cmd, triggers_str = line.split('->', 1)
                cmd = cmd.strip()
                triggers = [t.strip().lower() for t in triggers_str.split(',')]
                commands_dict[cmd] = triggers
    return commands_dict

def load_auto_commands():
    """Подхватываем auto_commands.json, собранный system_indexer.py."""
    if not os.path.exists(AUTO_COMMANDS_FILE):
        return {}
    try:
        with open(AUTO_COMMANDS_FILE, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  Не могу прочитать {AUTO_COMMANDS_FILE}: {e}")
        return {}
    db = {}
    for item in payload.get("items", []):
        cmd = item.get("command")
        trigger = item.get("trigger")
        if not cmd or not trigger:
            continue
        if cmd not in db:
            db[cmd] = []
        if trigger not in db[cmd]:
            db[cmd].append(trigger)
    if db:
        sources = payload.get("sources", {})
        details = ", ".join(f"{k}={v}" for k, v in sources.items() if v) or "?"
        print(f"📂 Авто-индекс: {len(db)} команд ({details}).")
    return db

def build_commands_db():
    """Собираем итоговую базу: auto-индекс + curated commands.txt.

    При конфликте (одна и та же команда) триггеры объединяются, curated идёт
    первым (выше приоритет). Авто-база служит «сетью безопасности», чтобы доступен был
    весь системный софт (.desktop, KWin-шорткаты, $PATH).
    """
    auto = load_auto_commands()
    curated = load_commands()
    merged = dict(auto)
    for cmd, triggers in curated.items():
        if cmd in merged:
            seen = set(triggers)
            extra = [t for t in merged[cmd] if t not in seen]
            merged[cmd] = triggers + extra
        else:
            merged[cmd] = triggers
    return merged

def load_or_build_vector_cache(commands_dict):
    """Дисковый кэш эмбеддингов.

    При запуске пытаемся загрузить векторы из VECTOR_CACHE_FILE и переиспользуем
    их для (command, trigger), которые всё ещё присутствуют в базе. Для новых
    триггеров зовём ollama, удалённые просто отбрасываются.

    Кэш полностью инвалидируется, если изменилась модель VECTOR_MODEL или файл
    повреждён. В конце сохраняем обновлённый кэш обратно на диск.
    """
    cached_vectors = {}
    if os.path.exists(VECTOR_CACHE_FILE):
        try:
            with open(VECTOR_CACHE_FILE, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            if payload.get("model") != VECTOR_MODEL:
                print(f"♻️  Модель в кэше ({payload.get('model')}) отличается от {VECTOR_MODEL} — пересчитываю.")
            else:
                for item in payload.get("items", []):
                    cached_vectors[(item["command"], item["trigger"])] = item["vector"]
                print(f"📦 Кэш загружен: {len(cached_vectors)} векторов из {VECTOR_CACHE_FILE}.")
        except (json.JSONDecodeError, KeyError, TypeError, OSError) as e:
            print(f"⚠️  Кэш повреждён ({e}) — пересчитываю.")
            cached_vectors = {}

    cache = []
    reused = 0
    built = 0
    for cmd, triggers in commands_dict.items():
        for trigger in triggers:
            key = (cmd, trigger)
            if key in cached_vectors:
                cache.append({"command": cmd, "trigger": trigger, "vector": cached_vectors[key]})
                reused += 1
            else:
                vec = get_embedding(trigger)
                if vec:
                    cache.append({"command": cmd, "trigger": trigger, "vector": vec})
                    built += 1

    if built > 0 or len(cache) != len(cached_vectors):
        try:
            # Атомарная запись: пишем в .tmp и делаем rename. Иначе
            # Ctrl+C посреди json.dump оставит битый файл.
            tmp_path = VECTOR_CACHE_FILE + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump({"model": VECTOR_MODEL, "items": cache}, f, ensure_ascii=False)
            os.replace(tmp_path, VECTOR_CACHE_FILE)
            print(f"💾 Кэш сохранён: переиспользовано {reused}, построено {built}, всего {len(cache)}.")
        except OSError as e:
            print(f"⚠️  Не удалось сохранить кэш: {e}")

    return cache, reused, built

print("⏳ Чтение базы команд (curated + auto-index)...")
commands_db = build_commands_db()

print("⏳ Векторизация базы для нейросети (дисковый кэш + достройка)...")
start_cache_time = time.time()
vector_cache, _reused, _built = load_or_build_vector_cache(commands_db)
print(f"✅ Векторизация ({len(vector_cache)} фраз) завершена за {time.time() - start_cache_time:.2f} сек.")

def reindex_system():
    """Горячая реиндексация: зовём system_indexer.py и перестраиваем векторы."""
    global commands_db, vector_cache
    if not os.path.exists(INDEXER_SCRIPT):
        print(f"⚠️  system_indexer.py не найден рядом с app.py ({INDEXER_SCRIPT})")
        return
    print("🔄 Запускаю system_indexer.py...")
    try:
        proc = subprocess.run([sys.executable, INDEXER_SCRIPT, "--quiet"],
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("⚠️  Индексатор не уложился в 120с — прерываю.")
        return
    if proc.returncode != 0:
        print(f"⚠️  Индексатор вернул код {proc.returncode}: {proc.stderr.strip()[:200]}")
        return
    commands_db = build_commands_db()
    vector_cache, _, _ = load_or_build_vector_cache(commands_db)
    print(f"✅ Реиндексация завершена: {len(commands_db)} команд, {len(vector_cache)} фраз.")

# === ЛОГИКА ПОИСКА ===
def fuzzy_match(user_text, commands_dict):
    user_text = user_text.lower()
    best_cmd, max_ratio = None, 0.0
    for cmd, triggers in commands_dict.items():
        for trigger in triggers:
            ratio = difflib.SequenceMatcher(None, user_text, trigger).ratio()
            if ratio > max_ratio:
                max_ratio = ratio
                best_cmd = cmd
    if max_ratio >= SIMILARITY_THRESHOLD: return best_cmd, max_ratio
    return None, max_ratio

def vector_search(user_text):
    user_vec = get_embedding(user_text)
    if not user_vec: return None, 0.0, ""

    best_cmd, max_score, best_trigger = None, 0.0, ""
    for item in vector_cache:
        score = cosine_similarity(user_vec, item["vector"])
        if score > max_score:
            max_score = score
            best_cmd = item["command"]
            best_trigger = item["trigger"]

    if max_score >= VECTOR_THRESHOLD: return best_cmd, max_score, best_trigger
    return None, max_score, best_trigger

# === АВТО-ВОССТАНОВЛЕНИЕ АУДИО ===
def attempt_audio_recovery():
    """Пытаемся «отлипить» PipeWire/PulseAudio. Шаги идут от минимальных к радикальным.

    Современные Kali/Plasma 6 используют PipeWire, старые — PulseAudio. Пробуем
    оба. Ошибки отдельных команд игнорируются — важен только итоговый ретрай.
    """
    recovery_steps = [
        ["systemctl", "--user", "restart", "pipewire-pulse"],
        ["systemctl", "--user", "restart", "wireplumber"],
        ["systemctl", "--user", "restart", "pipewire"],
        ["pulseaudio", "-k"],
    ]
    for cmd in recovery_steps:
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            continue
    # Даём сервису время подняться обратно.
    time.sleep(2.0)

# === РАСПОЗНАВАНИЕ ===
# Vosk-модель и сам аудио-цикл инициализируются только при прямом запуске,
# чтобы тесты могли импортировать модуль без микрофона/модели.
model = None
recognizer = None

def run_audio_loop():
    """Основной цикл прослушки. Вынесён в функцию, чтобы обёртываться retry-логикой."""
    global commands_db, vector_cache
    active_until = 0
    with sd.RawInputStream(samplerate=16000, blocksize=16000, dtype='int16', channels=1) as stream:
        while True:
            data, status = stream.read(8000)
            if not recognizer.AcceptWaveform(bytes(data)):
                continue
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            if len(text) < 3:
                continue

            current_time = time.time()
            words = text.split()
            wake_word_found = False
            wake_word_index = -1
            command_text = ""

            for i, word in enumerate(words):
                for ww in WAKE_WORDS:
                    if difflib.SequenceMatcher(None, word, ww).ratio() >= WAKE_THRESHOLD:
                        wake_word_found = True
                        wake_word_index = i
                        break
                if wake_word_found:
                    break

            if wake_word_found:
                active_until = current_time + ACTIVE_WINDOW
                command_text = " ".join(words[wake_word_index + 1:]).strip()
                if not command_text:
                    print("\n🔔 Слушаю...")
                    continue
            elif current_time < active_until:
                command_text = text
                active_until = 0
            else:
                continue

            if len(command_text) < 3:
                continue
            active_until = 0

            print(f"\n🗣 Запрос: {command_text}")

            # Специальный случай: реиндекс без перезапуска.
            low = command_text.lower()
            if any(rt in low for rt in REINDEX_TRIGGERS):
                reindex_system()
                continue

            # ЭТАП 1 и 2: Поиск
            cmd, confidence = fuzzy_match(command_text, commands_db)
            if cmd:
                print(f"⚡ Точное сходство: {int(confidence*100)}%.")
            else:
                cmd, vector_score, trigger_matched = vector_search(command_text)
                if cmd:
                    print(f"💡 Векторный смысл (Синоним: '{trigger_matched}'). Схожесть: {int(vector_score*100)}%.")
                else:
                    print(f"🤷 Команда не найдена. (Вектор: {int(vector_score*100)}%)")
                    continue

            # ЭТАП 3: Выполнение
            if cmd:
                print(f"🚀 Выполняю: {cmd}")
                try:
                    if cmd.strip().endswith('&'):
                        subprocess.Popen(cmd, shell=True,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                        print("🖥️  [UI / Фоновый процесс запущен]")
                    else:
                        process = subprocess.run(cmd, shell=True, capture_output=True,
                                                 text=True, timeout=15)
                        if process.stdout:
                            print(f"📄 Результат:\n{'-'*40}\n{process.stdout.strip()[:1000]}\n{'-'*40}")
                        if process.stderr:
                            print(f"⚠️ Лог: {process.stderr.strip()[:200]}")
                except subprocess.TimeoutExpired:
                    print("⏱ Процесс остановлен (таймаут 15 сек).")
                except Exception as e:
                    print(f"⚠️ Ошибка: {e}")

def main():
    """Запуск аудио-цикла с авто-восстановлением при PortAudioError."""
    global model, recognizer
    print("⏳ Запуск аудио-движка (Vosk)...")
    if not os.path.isdir(VOSK_MODEL_DIR):
        print(f"❌ Нет каталога с Vosk-моделью: {VOSK_MODEL_DIR}")
        print("   Скачай vosk-model-small-ru-0.22 и распакуй рядом с app.py под именем 'model'.")
        sys.exit(1)
    try:
        model = Model(VOSK_MODEL_DIR)
        recognizer = KaldiRecognizer(model, 16000)
    except Exception as e:
        print(f"❌ Ошибка Vosk: {e}")
        sys.exit(1)

    print("\n🤫 Ассистент работает в фоне.")
    print("Активационные слова: 'Акали', 'Ассистент', 'Компьютер'.")

    backoff = AUDIO_INITIAL_BACKOFF
    for attempt in range(1, AUDIO_MAX_RETRIES + 1):
        try:
            run_audio_loop()
            break
        except KeyboardInterrupt:
            print("\n👋 Завершение работы...")
            time.sleep(0.5)
            sys.exit(0)
        except sd.PortAudioError as e:
            print(f"\n❌ Аудио-ошибка (попытка {attempt}/{AUDIO_MAX_RETRIES}): {e}")
            if attempt >= AUDIO_MAX_RETRIES:
                print("💀 Аудио всё ещё не работает. Перезапусти вручную после исправления оборудования.")
                sys.exit(1)
            print("🔄 Перезапускаю аудиосервер (PipeWire/PulseAudio)...")
            attempt_audio_recovery()
            print(f"⏳ Ожидание {backoff:.1f}с и повторная попытка...")
            time.sleep(backoff)
            backoff *= 1.5


if __name__ == "__main__":
    main()
