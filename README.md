# Akali — голосовой ассистент для Kali Linux

Локальный (offline) ассистент: распознаёт голос через Vosk, ищет команду
из базы через difflib + векторные эмбеддинги (Ollama / all-minilm) и
выполняет через `bash`. Облачные API не используются — всё работает на
Intel N100 без перегрева.

## Стек

| Компонент   | Технология                                     |
|-------------|------------------------------------------------|
| Платформа   | Kali Linux + KDE Plasma 6 (Wayland)            |
| Язык        | Python 3.13+                                   |
| STT         | [vosk](https://alphacephei.com/vosk/) (`vosk-model-small-ru-0.22`) |
| Аудио       | [sounddevice](https://python-sounddevice.readthedocs.io/) (PipeWire / PulseAudio) |
| Эмбеддинги  | [ollama](https://ollama.com/) (модель `all-minilm`) |
| Управление окнами | `qdbus org.kde.kglobalaccel` → KWin (xdotool НЕ работает на Wayland) |

## Архитектура

```
голос → Vosk → текст
                  │
                  ▼
        ┌─── Wake Word? ──── нет ──── игнор
        │   (Акали/Ассистент/Компьютер, 5-сек окно)
        │
        ▼ да
   ┌──── Fuzzy match (difflib, порог 0.70) ──── найдено ──── ►
   │                                                          │
   │ нет                                                      ▼
   ▼                                                       Запуск
   Vector match (cosine_similarity, порог 0.55)              │
   с эмбеддингами из vector_cache.json ────────► найдено ────┘
```

База команд собирается из **двух источников**:

1. **`commands.txt`** — ручные/курируемые алиасы, формат `bash_cmd -> синоним1, синоним2, …`.
2. **`auto_commands.json`** — автоматически индексируемая база системы
   (см. `system_indexer.py`).

При совпадении команды в обоих источниках приоритет у `commands.txt`,
но синонимы из авто-индекса дополняют список триггеров.

## Файлы

| Файл                      | Назначение |
|---------------------------|------------|
| `app.py`                  | Основной движок: STT → поиск → выполнение |
| `commands.txt`            | Ручная база команд (188 команд / 450 триггеров) |
| `system_indexer.py`       | Авто-индексатор системы (`.desktop` + KWin + `$PATH`) |
| `auto_commands.json`      | Результат работы индексатора (генерируется) |
| `vector_cache.json`       | Дисковый кэш эмбеддингов (генерируется) |
| `validate_commands.py`    | Проверяет наличие всех бинарей из `commands.txt` |
| `test_smoke.py`           | 11 unit-тестов без внешних зависимостей |

## Установка

```bash
# 1. Зависимости
sudo apt update
sudo apt install -y python3-pip portaudio19-dev qdbus-qt6 konsole pipewire-pulse
pip3 install --user vosk sounddevice ollama

# 2. Ollama + модель эмбеддингов
curl -fsSL https://ollama.com/install.sh | sh
ollama pull all-minilm

# 3. Vosk-модель для русского
wget https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip
unzip vosk-model-small-ru-0.22.zip && mv vosk-model-small-ru-0.22 model

# 4. Проверка: какие бинари из commands.txt установлены
python3 validate_commands.py

# 5. (Опционально) проиндексировать всю систему — добавит ~200+ команд
python3 system_indexer.py            # только GUI + KWin
python3 system_indexer.py --binaries # + $PATH (медленнее)

# 6. Старт
python3 app.py
```

## Использование

| Фраза | Действие |
|------|----------|
| «Акали, открой терминал» | konsole |
| «Компьютер, сверни окно» | qdbus → KWin Window Minimize |
| «Ассистент, громче»      | pactl set-sink-volume +10% |
| «Акали, переиндексируй»  | перезапускает `system_indexer.py` и обновляет кэш на лету |

Доступные wake-words: **Акали**, **Ассистент**, **Компьютер**.
После активации даётся 5 секунд на следующую команду.

## Тесты

```bash
python3 test_smoke.py
# 11 проверок: кэш, fuzzy/vector матчинг, audio-recovery, мерж auto+curated
```

## Что нового в этой ветке

- **Авто-восстановление аудио**: при `PortAudioError` пробуем перезапустить
  PipeWire / PulseAudio автоматически (5 попыток с экспоненциальным backoff)
  вместо немедленного падения.
- **`system_indexer.py`** — собирает базу из `.desktop`-файлов,
  KWin-шорткатов и (опционально) `$PATH`. Русские имена для приложений
  берутся из `Name[ru]` / `GenericName[ru]` / `Comment[ru]` бесплатно.
- **Голосовая команда «переиндексируй»** перезапускает индексатор
  и пересобирает кэш без рестарта `app.py`.
- **`commands.txt`** расширен с 50 до 188 команд:
  тайлинг окон, виртуальные столы, `pactl`, `playerctl`, скриншоты через
  `spectacle`, DNS-диагностика, Wi-Fi, пентест-арсенал
  (recon / web / SMB-AD / wireless / crypto / anonymity).
- **xdotool → qdbus**: все оконные операции переведены на KWin shortcuts
  (xdotool не работает на Wayland).
- **Дисковый кэш эмбеддингов** (`vector_cache.json`) с инкрементальной
  достройкой и инвалидацией по версии модели.
- **TUI-команды** (`htop`, `msfconsole`, `wifite`, `setoolkit`)
  обёрнуты в `konsole -e …`, иначе они не отображаются.

## Roadmap

- inotify-watch на `/usr/share/applications/` → автоматический реиндекс
  при `apt install`.
- TTS (espeak-ng / piper-tts) для голосовой обратной связи.
- Простой «вопрос-ответ» режим: «что такое X» → `whatis X`, «где живёт X»
  → `which X`.
- Pexpect-обёртка для интерактивных консольных команд.
