"""QThread-friendly воркер микрофона + Vosk + поиска + выполнения.

AudioWorker — QObject, который перемещается в отдельный QThread. Слот
start_listening() блокирует поток на жизнь приложения, читая микрофон
порциями по 0.5 секунды. Все события (статус, распознанный текст,
найденная команда, ошибка) идут наружу через Qt-сигналы.

Авто-восстановление PortAudioError встроено: до AUDIO_MAX_RETRIES попыток
с экспоненциальным backoff, между ними дергается attempt_audio_recovery().

Реиндекс выполняется тут же — между чтениями микрофона. Это короткий
блокирующий момент, но не требует перезапуска приложения.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import time

from PySide6.QtCore import QObject, QThread, Signal, Slot

from .backend import ACTIVE_WINDOW_SECONDS, AssistantCore


AUDIO_MAX_RETRIES = 5
AUDIO_INITIAL_BACKOFF = 2.0


class AudioWorker(QObject):
    """Слушает микрофон, распознаёт речь, ищет/выполняет команды."""

    # === Сигналы наружу ===
    status_changed = Signal(str)             # "starting", "listening", "waiting_command",
                                             # "processing", "reindexing", "recovering", "stopped"
    text_recognized = Signal(str)            # сырой распознанный текст из Vosk
    wake_word_detected = Signal()            # услышали "Акали/Ассистент/Компьютер"
    command_matched = Signal(str, str, float, str)  # spoken, cmd, confidence, method
    command_executed = Signal(str, object)   # cmd, CommandResult
    no_match = Signal(str, float)            # spoken_text, max_vector_confidence
    error = Signal(str)                      # восстановимая ошибка (PortAudioError и т.п.)
    fatal_error = Signal(str)                # фатально, требует вмешательства
    level_changed = Signal(float)            # уровень микрофона 0..1
    reindex_done = Signal(bool, str, object) # ok, err_msg, ReloadStats|None
    stopped = Signal()                       # цикл завершился

    def __init__(self, core: AssistantCore, vosk_model_dir: str, parent=None):
        super().__init__(parent)
        self._core = core
        self._vosk_model_dir = vosk_model_dir
        self._stop_requested = False
        self._reindex_pending = False

    # === Слоты ===
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
            recognizer = KaldiRecognizer(model, 16000)
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
                # Sleep с прерыванием, чтобы не игнорить stop
                slept_ms = 0
                step_ms = 100
                total_ms = int(backoff * 1000)
                while slept_ms < total_ms and not self._stop_requested:
                    QThread.msleep(step_ms)
                    slept_ms += step_ms
                backoff *= 1.5
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
    def _audio_loop(self, recognizer, sd):
        active_until = 0.0
        self.status_changed.emit("listening")
        with sd.RawInputStream(samplerate=16000, blocksize=16000,
                               dtype='int16', channels=1) as stream:
            while not self._stop_requested:
                # Реиндекс, если запросили
                if self._reindex_pending:
                    self._reindex_pending = False
                    self.status_changed.emit("reindexing")
                    res = self._core.reindex_system()
                    self.reindex_done.emit(
                        bool(res.get("ok")), res.get("error", ""),
                        res.get("stats"))
                    self.status_changed.emit("listening")

                # Читаем 0.5 сек аудио
                data, _status = stream.read(8000)
                buf = bytes(data)
                # Уровень микрофона
                self.level_changed.emit(_compute_level(buf))
                if not recognizer.AcceptWaveform(buf):
                    continue

                result = json.loads(recognizer.Result())
                text = (result.get("text") or "").strip()
                if len(text) < 3:
                    continue

                self.text_recognized.emit(text)

                # Wake-word логика
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
                    active_until = 0
                else:
                    continue

                if len(command_text) < 3:
                    continue
                active_until = 0
                self.status_changed.emit("processing")

                # Голосовая команда «переиндексируй»
                if self._core.is_reindex_phrase(command_text):
                    self._reindex_pending = True
                    continue

                match = self._core.find(command_text)
                if not match.found:
                    self.no_match.emit(command_text, match.confidence)
                    self.status_changed.emit("listening")
                    continue

                self.command_matched.emit(
                    command_text, match.cmd, match.confidence, match.method)
                exec_result = self._core.execute(match.cmd)
                self.command_executed.emit(match.cmd, exec_result)
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
        # Даём сервисам подняться.
        QThread.sleep(2)


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
