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
COMMANDS_FILE = "commands.txt"
VECTOR_CACHE_FILE = "vector_cache.json"

# Настройки поиска (Текстовое совпадение)
SIMILARITY_THRESHOLD = 0.70  
WAKE_WORDS = ["компьютер", "ассистент", "акали"]
WAKE_THRESHOLD = 0.75
ACTIVE_WINDOW = 5.0

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

# === НОВЫЙ ЧЕЛОВЕКОЧИТАЕМЫЙ ПАРСЕР БАЗЫ ===
def load_commands():
    commands_dict = {}
    if not os.path.exists(COMMANDS_FILE):
        return {}
    with open(COMMANDS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # Игнорируем пустые строки и любые комментарии/заголовки
            if not line or line.startswith('#'):
                continue
            # Парсим только строки, где есть разделитель '->'
            if '->' in line:
                cmd, triggers_str = line.split('->', 1)
                cmd = cmd.strip()
                # Разбиваем синонимы по запятым и очищаем от пробелов
                triggers = [t.strip().lower() for t in triggers_str.split(',')]
                commands_dict[cmd] = triggers
    return commands_dict

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
            with open(VECTOR_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump({"model": VECTOR_MODEL, "items": cache}, f, ensure_ascii=False)
            print(f"💾 Кэш сохранён: переиспользовано {reused}, построено {built}, всего {len(cache)}.")
        except OSError as e:
            print(f"⚠️  Не удалось сохранить кэш: {e}")

    return cache, reused, built

print("⏳ Чтение структурированной базы команд...")
commands_db = load_commands()

print("⏳ Векторизация базы для нейросети (дисковый кэш + достройка)...")
start_cache_time = time.time()
vector_cache, _reused, _built = load_or_build_vector_cache(commands_db)
print(f"✅ Векторизация ({len(vector_cache)} фраз) завершена за {time.time() - start_cache_time:.2f} сек.")

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

# === РАСПОЗНАВАНИЕ ===
print("⏳ Запуск аудио-движка (Vosk)...")
try:
    model = Model("model")
    recognizer = KaldiRecognizer(model, 16000)
except Exception as e:
    print(f"❌ Ошибка Vosk: {e}")
    sys.exit(1)

print("\n🤫 Ассистент работает в фоне.")
print("Активационные слова: 'Акали', 'Ассистент', 'Компьютер'.")

active_until = 0

try:
    with sd.RawInputStream(samplerate=16000, blocksize=16000, dtype='int16', channels=1) as stream:
        while True:
            data, status = stream.read(8000)
            if recognizer.AcceptWaveform(bytes(data)):
                result = json.loads(recognizer.Result())
                text = result.get("text", "").strip()
                if len(text) < 3: continue

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
                    if wake_word_found: break

                if wake_word_found:
                    active_until = current_time + ACTIVE_WINDOW
                    command_text = " ".join(words[wake_word_index + 1:]).strip()
                    if not command_text:
                        print("\n🔔 Слушаю...")
                        continue
                elif current_time < active_until:
                    command_text = text
                    active_until = 0
                else: continue

                if len(command_text) < 3: continue
                active_until = 0 
                
                print(f"\n🗣 Запрос: {command_text}")
                start_exec_time = time.time()
                
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
                        # Если команда завершается на &, запускаем в фоне без ожидания
                        if cmd.strip().endswith('&'):
                            subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            print("🖥️  [UI / Фоновый процесс запущен]")
                        else:
                            # Для консольных утилит ждем вывод
                            process = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
                            if process.stdout:
                                print(f"📄 Результат:\n{'-'*40}\n{process.stdout.strip()[:1000]}\n{'-'*40}")
                            if process.stderr:
                                print(f"⚠️ Лог: {process.stderr.strip()[:200]}")
                    except subprocess.TimeoutExpired:
                        print("⏱ Процесс остановлен (таймаут 15 сек).")
                    except Exception as e:
                        print(f"⚠️ Ошибка: {e}")

except sd.PortAudioError:
    print(f"\n❌ Микрофон занят. Выполните: `pulseaudio -k`.")
except KeyboardInterrupt:
    print("\n👋 Завершение работы...")
    time.sleep(0.5) 
    sys.exit(0)
