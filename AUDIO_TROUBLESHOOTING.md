# 🎤 Диагностика проблем с распознаванием речи на Kali + Wayland

После обновления (commit 5868888) добавлены явные сообщения об ошибках.

## 🔍 Если микрофон не работает

### Сценарий 1: "⚠️ Микрофон молчит 5+ сек"

**Это значит:** sounddevice подключилась, но звук не идёт.

**Диагностика:**
```bash
# Проверить устройства звука
pactl list short sources
# Найти свой микрофон

# Проверить, слышит ли его PipeWire/PulseAudio
pactl list sources | grep -A 5 "Name: alsa_input"

# Проверить уровень звука
alsamixer
# Найти микрофон, убедиться, что громкость не 0

# Проверить права доступа
ls -la /dev/snd/
# Твой пользователь должен быть в группе audio
groups $USER
# Если нет audio, добавить:
# sudo usermod -aG audio $USER
# потом перезагрузиться
```

**Как исправить:**
```bash
# Перезапустить PipeWire
systemctl --user restart pipewire-pulse wireplumber pipewire

# Или PulseAudio
pulseaudio -k
```

---

### Сценарий 2: "⚠️ Ошибка запроса микрофона"

**Это значит:** sounddevice не может инициализировать вообще.

**Причины:**
- Nolabel PortAudio backend (pulseaudio)
- Нет микрофона в системе
- Микрофон отключен в BIOS/Firmware
- Конфликт с другим приложением

**Диагностика:**
```bash
# Проверить, видит ли PortAudio устройства
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Проверить логи PipeWire
journalctl -u pipewire --no-pager | tail -20

# Проверить конфликты
lsof /dev/snd/*

# Проверить в BIOS/DMI
sudo dmidecode | grep -A 5 "Onboard Devices"
```

**Как исправить:**
```bash
# Переключиться на другой backend (если доступен)
export PULSE_ALSA_DEFAULT_DEVICE=default

# Или явно указать микрофон в акали:
# Настройки → Микрофон → Выбрать из списка
```

---

### Сценарий 3: "❌ Команда содержит недопустимые символы"

**Это значит:** голос распознан, но содержит опасные символы.

**Примеры опасных фраз:**
- "закрой окно; удали всё"
- "открой терминал && запусти хак"
- "создай файл > /tmp/bad"

**Это нормально** — это защита от случайных voice injection.

**Как исправить:**
- Просто перефразируй команду без метасимволов
- Вместо "окно; удали" → "закрой окно" + отдельно "удали"

---

### Сценарий 4: "⚠️ Ollama недоступна"

**Это значит:** Vector-поиск отключён, работает только fuzzy-matching.

**Диагностика:**
```bash
# Проверить, установлена ли ollama
which ollama

# Запущена ли служба
curl http://localhost:11434/api/tags

# Загружена ли модель
ollama list | grep all-minilm
```

**Как исправить:**
```bash
# Установить Ollama (если нет)
curl -fsSL https://ollama.com/install.sh | sh

# Загрузить модель
ollama pull all-minilm

# Запустить Ollama (в фоне или отдельном терминале)
ollama serve
```

---

## 📊 Проверка после исправлений

### Шаг 1: Проверить, что компилируется
```bash
cd /path/to/akali
python3 -m py_compile akali/core/audio_worker.py akali/core/executor.py akali/core/backend.py
echo "✓ Синтаксис OK"
```

### Шаг 2: Запустить тесты
```bash
python3 test_smoke.py
# Должны пройти все 11 проверок
```

### Шаг 3: Запустить GUI (если PySide6 установлена)
```bash
# На целевой системе (Kali + Plasma 6)
python3 akali.py

# В логе должны быть сообщения типа:
# ⚙ Загружена база: 189 команд...
# 🎤 Микрофон: [имя микрофона] @ [rate]Hz
```

### Шаг 4: Тестировать микрофон
```bash
# Нажать "Слушать" в UI
# Сказать: "Акали, тест"
# В логе должны быть:
# 🎙 «акали тест»
# 🔔 Wake-word
# ✕ «тест» (no match, если нет команды "тест")
```

---

## 🐛 Если всё ещё не работает

### Сбор диагностики
```bash
# Сохранить логи
python3 akali.py 2>&1 | tee /tmp/akali.log

# Информация о системе
uname -a
aplay -l
pacmd list-sinks
pacmd list-sources
systemctl --user status pipewire pipewire-pulse wireplumber

# Python-окружение
python3 -c "import vosk, sounddevice, ollama, PySide6; print('All deps OK')"
```

### Отправить баг-репорт
Включи:
1. `/tmp/akali.log` (с `⚠️ Ошибка запроса микрофона: ...`)
2. Вывод `pactl list short sources`
3. Вывод `uname -a` и `pacmd list-sinks`
4. Конфигурацию PipeWire: `pactl info`

---

## 📚 Ссылки

- [Vosk Models](https://alphacephei.com/vosk/models)
- [PipeWire Troubleshooting](https://wiki.archlinux.org/title/PipeWire)
- [Ollama Docs](https://github.com/ollama/ollama)
- [KDE Plasma Audio](https://userbase.kde.org/Plasma/Audio)
