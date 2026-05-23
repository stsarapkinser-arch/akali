"""QThread-friendly воркер микрофона + Vosk + роутер запросов.

AudioWorker — QObject, который перемещается в отдельный QThread. Слот
start_listening() блокирует поток на жизнь приложения, читая микрофон
порциями по 250 мс через callback PortAudio в потокобезопасную очередь.
Все события идут наружу через Qt-сигналы.

Пайплайн после распознавания финальной фразы (text):
    1. Извлечь часть после wake-word (или принять text как команду
       если открыто active-window).
    2. Гейт фильтров (len ≥ 3, не шум) — иначе игнор.
    3. Если фраза — переиндекс-команда, отметить флаг и продолжить.
    4. power_guard.match(text) → если совпало точно — выполнить.
    5. core.process(text) → router (cache → fastembed → LLM) → выполнить.
    6. Иначе — emit no_match.

Никаких параллельных fuzzy/vector-эвристик: всё через единый роутер.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import struct
import subprocess
import time

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .backend import ACTIVE_WINDOW_SECONDS, AssistantCore

log = logging.getLogger(__name__)


AUDIO_MAX_RETRIES = 5
AUDIO_INITIAL_BACKOFF = 2.0
AUDIO_SAMPLE_RATE = 16000           # Vosk small-ru ждёт ровно 16 кГц
AUDIO_BLOCK_FRAMES = 4000           # 250 мс @ 16 кГц — компромисс отзывчивость / CPU
AUDIO_QUEUE_MAX = 32                # ≈8 секунд буфера
# Порядок перебора частот: сначала нативные 16 кГц (PipeWire обычно ок),
# затем 48 кГц (аппаратный стандарт ALC269VC) с последующим даунсэмплом 3:1
AUDIO_SAMPLE_RATES_TRY = [16000, 48000]


class AudioWorker(QObject):
    """Слушает микрофон, распознаёт речь, ищет/выполняет команды."""

    # === Сигналы наружу ===
    status_changed = Signal(str)             # "starting", "listening", "waiting_command",
                                             # "processing", "reindexing", "recovering", "stopped"
    text_recognized = Signal(str)            # сырой распознанный текст из Vosk
    partial_text = Signal(str)               # partial из Vosk (на лету)
    wake_word_detected = Signal()            # услышали "Акали/Ассистент/Компьютер"
    command_matched = Signal(str, str, float, str)  # spoken, cmd, confidence, method
    command_executed = Signal(str, object)   # cmd, CommandResult
    no_match = Signal(str, float)            # spoken_text, max_vector_confidence
    error = Signal(str)                      # восстановимая ошибка (PortAudioError и т.п.)
    fatal_error = Signal(str)                # фатально, требует вмешательства
    level_changed = Signal(float)            # уровень микрофона 0..1
    reindex_done = Signal(bool, str, object) # ok, err_msg, ReloadStats|None
    device_info = Signal(str)                # инфо о выбранном микрофоне (одноразово)
    stopped = Signal()                       # цикл завершился

    def __init__(self, core: AssistantCore, vosk_model_dir: str,
                 device_index: int | None = None, parent=None):
        super().__init__(parent)
        self._core = core
        self._vosk_model_dir = vosk_model_dir
        self._device_index = device_index
        self._stop_requested = False
        self._reindex_pending = False

    # === Слоты ===
    @Slot(object)
    def set_device(self, device_index):
        """Меняет выбранное устройство; применится при следующем start."""
        self._device_index = device_index

    @Slot()
    def start_listening(self):
        """Главный цикл прослушки. Блокирует поток, выходит по request_stop."""
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
            import sounddevice as sd
        except ImportError as e:
            self.fatal_error.emit(f"Не установлены vosk/sounddevice: {e}")
            self.stopped.emit()
            return

        SetLogLevel(-1)
        if not os.path.isdir(self._vosk_model_dir):
            self.fatal_error.emit(
                f"Нет каталога Vosk-модели: {self._vosk_model_dir}\n"
                f"Скачай vosk-model-small-ru-0.22 и распакуй под именем 'model'.")
            self.stopped.emit()
            return

        try:
            self.status_changed.emit("starting")
            model = Model(self._vosk_model_dir)
            recognizer = KaldiRecognizer(model, AUDIO_SAMPLE_RATE)
        except Exception as e:
            self.fatal_error.emit(f"Не удалось загрузить Vosk: {e}")
            self.stopped.emit()
            return

        self._stop_requested = False
        backoff = AUDIO_INITIAL_BACKOFF
        attempt = 0
        while not self._stop_requested:
            attempt += 1
            try:
                self._audio_loop(recognizer, sd)
                break
            except sd.PortAudioError as e:
                self.error.emit(
                    f"Аудио-ошибка (попытка {attempt}/{AUDIO_MAX_RETRIES}): {e}")
                if attempt >= AUDIO_MAX_RETRIES:
                    self.fatal_error.emit(
                        "Аудио всё ещё не работает после нескольких попыток.")
                    break
                self.status_changed.emit("recovering")
                self._attempt_audio_recovery()
                slept_ms = 0
                step_ms = 100
                total_ms = int(backoff * 1000)
                while slept_ms < total_ms and not self._stop_requested:
                    QThread.msleep(step_ms)
                    slept_ms += step_ms
                backoff *= 1.5
                # Vosk State может содержать обрывки — пересоздаём
                recognizer = KaldiRecognizer(model, AUDIO_SAMPLE_RATE)
            except Exception as e:
                self.fatal_error.emit(f"Неожиданная ошибка: {e}")
                break

        self.status_changed.emit("stopped")
        self.stopped.emit()

    @Slot()
    def request_stop(self):
        """Просим главный цикл выйти при ближайшей возможности."""
        self._stop_requested = True

    @Slot()
    def request_reindex(self):
        """Просим выполнить реиндекс в ближайшем тике аудио-цикла."""
        self._reindex_pending = True

    # === Внутренности ===
    def _resolve_device(self, sd) -> int | None:
        """Подбираем рабочее input-устройство.

        Приоритеты:
            1) явно заданный self._device_index (если валидный input);
            2) sd.default.device (если валидный input);
            3) первое попавшееся устройство с input-каналами;
            4) None (PortAudio выберет сам).
        """
        try:
            devices = sd.query_devices()
        except Exception:
            return None

        def is_input(idx: int) -> bool:
            try:
                d = devices[idx]
                return d.get("max_input_channels", 0) > 0
            except (IndexError, KeyError, TypeError):
                return False

        # 1) Явно заданный
        if self._device_index is not None:
            if is_input(self._device_index):
                return self._device_index
            self.error.emit(
                f"Заданное устройство #{self._device_index} не имеет input-каналов.")

        # 2) Default
        try:
            default = sd.default.device
            if isinstance(default, (list, tuple)):
                default_in = default[0]
            else:
                default_in = default
            if isinstance(default_in, int) and default_in >= 0 and is_input(default_in):
                return default_in
        except (KeyError, IndexError, TypeError, AttributeError):
            pass

        # 3) Первое попавшееся
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                return i

        return None

    def _audio_loop(self, recognizer, sd):
        # Очередь буферов; callback кладёт сюда, основной цикл забирает
        audio_q: queue.Queue[bytes] = queue.Queue(maxsize=AUDIO_QUEUE_MAX)

        # Счётчик пустых буферов для обнаружения отсутствия звука
        empty_buffer_count = 0
        buffer_validity_threshold = 20  # ~5 сек на 250 мс блоках

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            try:
                audio_q.put_nowait(bytes(indata))
            except queue.Full:
                pass

        # Всегда используем device=None (PipeWire/PulseAudio wrapper),
        # а не сырое hw:X,Y — иначе PipeWire блокирует доступ
        device = None
        device_name = "PipeWire/PulseAudio default"

        try:
            info = sd.query_devices(kind="input")
            if isinstance(info, dict):
                device_name = info.get("name", device_name)
                hw_rate = info.get("default_samplerate", "?")
            else:
                hw_rate = "?"
        except Exception:
            hw_rate = "?"

        # Пробуем частоты по приоритету: 16000 (нативно для Vosk) → 48000 (ALC269VC)
        actual_rate = None
        last_open_err = None
        for try_rate in AUDIO_SAMPLE_RATES_TRY:
            try:
                # Проверяем, что поток открывается на этой частоте
                probe = sd.RawInputStream(
                    samplerate=try_rate,
                    blocksize=AUDIO_BLOCK_FRAMES * (try_rate // AUDIO_SAMPLE_RATE),
                    dtype="int16",
                    channels=1,
                    device=device,
                )
                probe.close()
                actual_rate = try_rate
                break
            except Exception as e:
                last_open_err = e
                continue

        if actual_rate is None:
            raise sd.PortAudioError(
                f"Не удалось открыть RawInputStream ни на одной частоте "
                f"({AUDIO_SAMPLE_RATES_TRY}). Последняя ошибка: {last_open_err}"
            )

        need_resample = actual_rate != AUDIO_SAMPLE_RATE
        resample_ratio = actual_rate // AUDIO_SAMPLE_RATE  # целочисленное: 3 для 48000/16000
        blocksize = AUDIO_BLOCK_FRAMES * (actual_rate // AUDIO_SAMPLE_RATE)

        self.device_info.emit(
            f"{device_name} @ {actual_rate}Hz"
            f"{' (→16kHz 3:1)' if need_resample else ''} "
            f"(hw default={hw_rate}Hz)"
        )

        import numpy as np  # уже в зависимостях через embed_cache

        with sd.RawInputStream(
            samplerate=actual_rate,
            blocksize=blocksize,
            dtype="int16",
            channels=1,
            callback=callback,
            device=device,
        ):
            self.status_changed.emit("listening")
            active_until = 0.0
            partial_wake_emitted = False
            last_partial = ""

            while not self._stop_requested:
                # ── Реиндекс по запросу ────────────────────────────
                if self._reindex_pending:
                    self._reindex_pending = False
                    self.status_changed.emit("reindexing")
                    res = self._core.reindex_system()
                    self.reindex_done.emit(
                        bool(res.get("ok")), res.get("error", ""),
                        res.get("stats"))
                    # Сбрасываем партиал, чтобы не подцепить старые слова.
                    try:
                        recognizer.FinalResult()
                    except Exception:
                        pass
                    self.status_changed.emit("listening")

                # ── Берём один блок (250 мс) с таймаутом ────────────
                try:
                    buf = audio_q.get(timeout=0.25)
                except queue.Empty:
                    continue

                # Даунсэмплинг 48000→16000 (берём каждый resample_ratio-й сэмпл)
                if need_resample:
                    arr = np.frombuffer(buf, dtype=np.int16)
                    buf = arr[::resample_ratio].tobytes()

                # Валидация буфера: проверяем, что микрофон работает
                level = _compute_level(buf)
                self.level_changed.emit(level)

                # Детектим мёртвый микрофон: если 5+ сек полной тишины, это ошибка
                if level < 0.001:  # практически нулевой уровень
                    empty_buffer_count += 1
                    if empty_buffer_count >= buffer_validity_threshold:
                        self.error.emit(
                            f"Микрофон молчит 5+ сек. Проверь подключение "
                            f"(PipeWire/PulseAudio/права доступа). "
                            f"Устройство: {device_name}")
                        empty_buffer_count = 0  # сбросим счётчик, чтобы не спамить
                else:
                    empty_buffer_count = 0  # есть звук → сбрасываем счётчик

                final = recognizer.AcceptWaveform(buf)

                if not final:
                    # Partial: для UX-фидбека и для wake-word до тишины
                    try:
                        partial = json.loads(recognizer.PartialResult()).get(
                            "partial", "").strip()
                    except (json.JSONDecodeError, AttributeError):
                        partial = ""
                    if partial and partial != last_partial:
                        last_partial = partial
                        self.partial_text.emit(partial)
                        if not partial_wake_emitted:
                            words = partial.split()
                            if self._core.detect_wake_word(words) >= 0:
                                self.wake_word_detected.emit()
                                self.status_changed.emit("waiting_command")
                                partial_wake_emitted = True
                                active_until = time.time() + ACTIVE_WINDOW_SECONDS
                    continue

                # ── Финал: полная распознанная фраза ───────────────
                last_partial = ""
                partial_wake_emitted = False
                try:
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                except (json.JSONDecodeError, AttributeError):
                    text = ""
                # ТЗ: текст короче 3 символов сразу игнорируем
                if len(text) < 3:
                    continue

                self.text_recognized.emit(text)

                now = time.time()
                words = text.split()
                wake_idx = self._core.detect_wake_word(words)

                command_text = ""
                if wake_idx >= 0:
                    self.wake_word_detected.emit()
                    active_until = now + ACTIVE_WINDOW_SECONDS
                    command_text = " ".join(words[wake_idx + 1:]).strip()
                    if not command_text:
                        self.status_changed.emit("waiting_command")
                        continue
                elif now < active_until:
                    command_text = text
                else:
                    # Не вызывали и активного окна нет — игнорируем
                    continue

                active_until = 0.0
                # ── Фильтр: длина и шум — до роутера ───────────────
                if len(command_text) < 3 or _is_noise_text(command_text):
                    log.debug("игнорирую (шум/короткая): %r", command_text)
                    self.status_changed.emit("listening")
                    continue

                self.status_changed.emit("processing")

                if self._core.is_reindex_phrase(command_text):
                    self._reindex_pending = True
                    continue

                # ── Полный пайплайн через core.process ─────────────
                # (power_guard → QueryRouter: кэш → FastEmbed → LLM)
                cmd, source = self._core.process(command_text)
                if cmd:
                    log.info("Команда [%s]: %r → %s", source, command_text, cmd)
                    self.command_matched.emit(command_text, cmd, 1.0, source)
                    exec_result = self._core.execute(cmd)
                    self.command_executed.emit(cmd, exec_result)
                else:
                    log.info("Не нашёл команду для %r", command_text)
                    self.no_match.emit(command_text, 0.0)
                self.status_changed.emit("listening")

    def _attempt_audio_recovery(self):
        """Пытаемся перезапустить PipeWire/PulseAudio. Ошибки шагов игнорим."""
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
        # Даём сервисам подняться — но не зависаем весь поток для N100
        # Вместо QThread.sleep(2) используем микрыхи с проверкой остановки
        slept = 0
        step = 100  # 100 мс за раз
        total = 2000  # 2 сек всего
        while slept < total and not self._stop_requested:
            QThread.msleep(step)
            slept += step


_VOWELS = frozenset("аеёиоуыьъэюяaeiouy")
_CONSONANTS = frozenset("бвгджзйклмнпрстфхцчшщbcdfghjklmnpqrstvwxz")


def _is_noise_text(text: str) -> bool:
    """True если текст — артефакт распознавания (слоги, случайные буквы).

    Эвристики:
    1. Текст < 3 символов → шум (это ТЗ).
    2. Слов нет вообще → шум.
    3. Ни одного слова длиной ≥ 3 нет → шум.
    4. Каждое «слово» состоит почти только из гласных или только из
       согласных (характерный мусор от Vosk при тишине / эхо).
    """
    text = text.strip()
    if len(text) < 3:
        return True
    words = text.split()
    if not words:
        return True
    meaningful = 0
    for w in words:
        lw = w.lower()
        if len(lw) < 3:
            continue
        n_vowels = sum(1 for c in lw if c in _VOWELS)
        n_consonants = sum(1 for c in lw if c in _CONSONANTS)
        # «Слово» = только гласные или только согласные → не считаем
        if n_vowels == 0 or n_consonants == 0:
            continue
        meaningful += 1
    return meaningful == 0


def _compute_level(data_bytes: bytes) -> float:
    """Возвращает пиковый уровень микрофона из 16-bit PCM-блока, нормированный 0..1."""
    n = len(data_bytes) // 2
    if n == 0:
        return 0.0
    try:
        samples = struct.unpack(f"{n}h", data_bytes)
    except struct.error:
        return 0.0
    peak = 0
    for s in samples:
        if s < 0:
            s = -s
        if s > peak:
            peak = s
    return min(peak / 32768.0, 1.0)


def list_input_devices() -> list[tuple[int, str, int]]:
    """Утилита для UI: возвращает [(idx, name, max_input_channels), ...].

    Импортирует sounddevice только при вызове, чтобы UI мог рендериться
    на машинах без аудио-стека.
    """
    try:
        import sounddevice as sd  # noqa: WPS433
    except ImportError:
        return []
    try:
        devices = sd.query_devices()
    except Exception:
        return []
    result = []
    for i, d in enumerate(devices):
        ch = d.get("max_input_channels", 0)
        if ch > 0:
            result.append((i, d.get("name", f"dev-{i}"), ch))
    return result
