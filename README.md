# Akali — голосовой ассистент для Kali Linux / KDE Plasma 6

Локальный (offline) ассистент с **GUI на PySide6**, системным треем и
встроенным авто-обновлением из git. Распознаёт голос через Vosk, ищет
команду из объединённой базы (curated + авто-индекс системы) с помощью
fuzzy- и векторного поиска (Ollama / all-minilm), выполняет через
`bash`. Облачные API не используются — всё на Intel N100 без перегрева.

## Стек

| Компонент   | Технология                                     |
|-------------|------------------------------------------------|
| Платформа   | Kali Linux + KDE Plasma 6 (Wayland)            |
| Язык        | Python 3.10+                                   |
| GUI         | [PySide6](https://wiki.qt.io/Qt_for_Python) (Qt 6)|
| STT         | [vosk](https://alphacephei.com/vosk/) (`vosk-model-small-ru-0.22`) |
| Аудио       | [sounddevice](https://python-sounddevice.readthedocs.io/) (PipeWire / PulseAudio) |
| Эмбеддинги  | [ollama](https://ollama.com/) (модель `all-minilm`) |
| Управление окнами | `qdbus org.kde.kglobalaccel` → KWin (xdotool НЕ работает на Wayland) |

## Архитектура

```
                ┌──────────────────────────────┐
                │      QApplication (UI)       │
                │  ┌─────────┐  ┌────────────┐ │
                │  │ MainWin │  │ TrayIcon   │ │
                │  └────┬────┘  └──────┬─────┘ │
                └───────┼──────────────┼──────┘
                  signals│              │signals
                ┌────────▼──────────────▼────────┐
                │  AkaliApp (координатор)         │
                └────────┬────────────────────────┘
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
   ┌──────────┐   ┌──────────────┐   ┌──────────┐
   │AudioWorkr│   │AssistantCore │   │UpdateRunr│
   │(QThread) │   │(база+поиск)  │   │(git pull)│
   └─────┬────┘   └──────────────┘   └──────────┘
         │ микрофон+Vosk
         ▼
   bash subprocess
```

База команд собирается из **двух источников**:

1. **`commands.txt`** — ручные/курируемые алиасы, формат `bash_cmd -> синоним1, синоним2, …`.
2. **`auto_commands.json`** — автоматически индексируемая база системы
   (см. `system_indexer.py`).

При совпадении команды в обоих источниках приоритет у `commands.txt`,
синонимы из авто-индекса дополняют список триггеров.

## Файлы

| Файл / Папка              | Назначение |
|---------------------------|------------|
| `akali.py`                | Entry point (`python3 akali.py`) |
| `core/backend.py`         | Чистая логика: база, поиск, выполнение, реиндекс |
| `core/audio_worker.py`    | QThread: микрофон + Vosk + recovery |
| `core/updater.py`         | git pull в фоне |
| `ui/main_window.py`       | Главное окно (4 вкладки) |
| `ui/tray.py`              | Системный трей |
| `ui/styles.qss`           | Тёмная тема |
| `assets/icon.svg`         | Иконка приложения |
| `akali.desktop`           | Шаблон .desktop-файла для KDE-меню |
| `commands.txt`            | Ручная база команд (188 команд / 450 триггеров) |
| `system_indexer.py`       | Авто-индексатор системы (`.desktop` + KWin + `$PATH`) |
| `auto_commands.json`      | Результат работы индексатора (генерируется) |
| `vector_cache.json`       | Дисковый кэш эмбеддингов (генерируется) |
| `validate_commands.py`    | Проверяет наличие всех бинарей из `commands.txt` |
| `test_smoke.py`           | 11 smoke-тестов без микрофона/ollama/Vosk |
| `requirements.txt`        | Список python-зависимостей |

## Установка

```bash
# 1. Системные пакеты (под Kali)
sudo apt update
sudo apt install -y python3-pip portaudio19-dev qt6-tools konsole pipewire-pulse

# 2. Python-зависимости
pip3 install --user -r requirements.txt

# 3. Ollama + модель эмбеддингов
curl -fsSL https://ollama.com/install.sh | sh
ollama pull all-minilm

# 4. Vosk-модель для русского
wget https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip
unzip vosk-model-small-ru-0.22.zip && mv vosk-model-small-ru-0.22 model

# 5. (Опционально) проиндексировать систему
python3 system_indexer.py            # GUI-приложения + KWin (быстро)
python3 system_indexer.py --binaries # + $PATH (медленнее, шумнее)

# 6. Старт GUI
python3 akali.py
```

После запуска приложение живёт **в системном трее**. По клику на иконку
открывается главное окно с четырьмя вкладками:

- **🏠 Главная** — большой статус, уровень микрофона, история команд, кнопки.
- **📚 Команды** — поиск по всей базе (curated + auto), хеппинг с триггерами.
- **⚙ Настройки** — пороги fuzzy/vector/wake, wake-words, путь к репо, **«Обновить из репо»**.
- **📜 Лог** — потоковый журнал всех событий (распознанные фразы, ошибки, ответы команд).

Меню в трее: Слушать/Стоп, Переиндексировать, Обновить из репо, Показать окно, Выход.

## Использование

| Фраза | Действие |
|------|----------|
| «Акали, открой терминал» | konsole |
| «Компьютер, сверни окно» | qdbus → KWin Window Minimize |
| «Ассистент, громче»      | pactl set-sink-volume +10% |
| «Акали, переиндексируй»  | перезапускает `system_indexer.py` и обновляет кэш на лету |

Доступные wake-words: **Акали**, **Ассистент**, **Компьютер**.
После активации даётся 5 секунд на следующую команду.

## Обновление из репо

В Настройках → «Обновить из репо (git pull)» (или в трей-меню).
Под капотом:
1. Проверяем, что директория — git-репо.
2. Проверяем чистый working tree.
3. `git fetch --prune` + `git pull --ff-only`.
4. Показываем список новых коммитов и изменённых файлов.
5. Если изменились `.py` или `.qss` — предупреждаем о необходимости перезапуска.

Force-merge не делаем, поэтому если у тебя есть локальные правки —
обновление откажется и попросит разрулить вручную.

## Регистрация в KDE-меню (опционально)

```bash
# 1. Подставь свой реальный путь
sed "s|%CHANGE_ME_TO_AKALI_PATH%|$HOME/akali|g" akali.desktop > ~/.local/share/applications/akali.desktop

# 2. Скопируй иконку
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
cp assets/icon.svg ~/.local/share/icons/hicolor/scalable/apps/akali.svg

# 3. Обнови кэш меню
kbuildsycoca6 2>/dev/null || kbuildsycoca5
```

После этого Akali появится в KDE-меню «Утилиты».

## Тесты

```bash
python3 test_smoke.py
# 11 проверок: кэш, fuzzy/vector матчинг, audio-recovery API,
# мёрж auto+curated, реиндекс через subprocess, wake-word логика.
```

Тесты подменяют `ollama` фейком, поэтому не требуют ни сети, ни ollama
вживую.

## Что нового

### GUI (PySide6 + tray)
- Полноценное desktop-приложение вместо CLI.
- Тёмная тема, системный трей, 4 вкладки.
- Авто-обновление из репо одной кнопкой.
- Все настройки (пороги, wake-words, пути) персистентны через `QSettings`.

### Авто-индексация системы
- `system_indexer.py` собирает базу из `.desktop`-файлов
  (`Name[ru]`/`Comment[ru]` тащит бесплатно), KWin-шорткатов
  (`qdbus`/`qdbus6`/`qdbus-qt6` авто-detection) и опционально `$PATH`.
- Голосовая команда «переиндексируй» перезапускает индексатор и
  пересобирает векторный кэш без остановки приложения.

### Аудио-recovery
- При `PortAudioError` ассистент пробует перезапустить
  PipeWire/PulseAudio автоматически (5 попыток с экспоненциальным
  backoff) вместо немедленного падения.

### Wayland-совместимость
- `xdotool → qdbus` (KWin shortcuts). Все оконные операции работают
  на Wayland.
- TUI-команды (`htop`, `msfconsole`, `wifite`) обёрнуты в `konsole -e …`.

### Дисковый кэш эмбеддингов
- `vector_cache.json` с инкрементальной достройкой и инвалидацией по
  имени модели. Атомарная запись через `.tmp` + `os.replace`.

## Roadmap

- inotify-watch на `/usr/share/applications/` → авто-реиндекс после `apt install`.
- TTS-ответ (`espeak-ng` или `piper-tts`).
- Q&A-режим: «что такое X» → `whatis X`, «где живёт X» → `which X`.
- Визуальный selector аудио-устройств в Настройках.
- Pexpect-обвязка для интерактивных команд (apt с подтверждением и т.п.).
