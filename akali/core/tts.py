"""Озвучка ответов ассистента приятным человеческим голосом.

Приоритет движков:
    1. `piper` (нейронный, оффлайн, голос ru_RU-irina-medium) — основной.
    2. `espeak-ng -v ru+f3` — фоллбэк для машин без piper.
    3. Тихий no-op, если ни того ни другого нет.

Все вызовы — неблокирующие: текст складывается в очередь и
проигрывается фоновым потоком. Прерывание (`stop`) останавливает текущее
проигрывание сразу.

ВАЖНО: НЕ озвучиваем сами bash-команды (там может быть rm -rf или
другая чувствительная инфа). Озвучиваем только короткие подтверждения
типа «Выполнено», «Не понял», «Запускаю».
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

from . import system_check

log = logging.getLogger(__name__)


# ── Движки ==============================================================
class _Engine:
    """Базовый интерфейс TTS-движка."""

    name = "none"

    def available(self) -> bool:
        return False

    def speak(self, text: str) -> None:  # noqa: D401
        return None

    def stop(self) -> None:
        return None


class _PiperEngine(_Engine):
    name = "piper"

    def __init__(self, voice: Path = system_check.PIPER_VOICE_FILE):
        self.voice = voice
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._aplay: Optional[subprocess.Popen[bytes]] = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if not shutil.which("piper"):
            return False
        if not self.voice.exists():
            return False
        # Нужен либо aplay (alsa), либо paplay (pulse), либо pw-play (pipewire)
        return bool(shutil.which("aplay") or shutil.which("paplay") or shutil.which("pw-play"))

    def _player_cmd(self) -> list[str] | None:
        for cmd in (
            ["pw-play", "-"],                       # PipeWire
            ["paplay", "--raw", "--rate=22050",     # PulseAudio (raw 22 kHz PCM)
             "--format=s16le", "--channels=1"],
            ["aplay", "-r", "22050", "-f", "S16_LE", "-c", "1"],
        ):
            if shutil.which(cmd[0]):
                return cmd
        return None

    def speak(self, text: str) -> None:
        player = self._player_cmd()
        if player is None:
            log.debug("piper: нет проигрывателя (pw-play/paplay/aplay)")
            return
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    ["piper", "--model", str(self.voice),
                     "--output_raw", "--quiet"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                self._aplay = subprocess.Popen(
                    player, stdin=self._proc.stdout,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if self._proc.stdin:
                    self._proc.stdin.write(text.encode("utf-8"))
                    self._proc.stdin.close()
            self._aplay.wait(timeout=30)
            self._proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("piper speak error: %s", e)
        finally:
            with self._lock:
                self._proc = None
                self._aplay = None

    def stop(self) -> None:
        with self._lock:
            for p in (self._aplay, self._proc):
                if p and p.poll() is None:
                    try:
                        p.terminate()
                    except OSError:
                        pass
            self._aplay = None
            self._proc = None


class _EspeakEngine(_Engine):
    name = "espeak-ng"

    def __init__(self, voice: str = "ru+f3"):
        self.voice = voice
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(shutil.which("espeak-ng") or shutil.which("espeak"))

    def speak(self, text: str) -> None:
        bin_name = "espeak-ng" if shutil.which("espeak-ng") else "espeak"
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    [bin_name, "-v", self.voice, "-s", "165",
                     "-p", "55", "--punct=", "--", text],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            self._proc.wait(timeout=30)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("espeak speak error: %s", e)
        finally:
            with self._lock:
                self._proc = None

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except OSError:
                    pass
            self._proc = None


def _select_engine(prefer: str = "auto") -> _Engine:
    """Выбирает доступный движок по приоритету."""
    if prefer != "espeak":
        piper = _PiperEngine()
        if piper.available():
            return piper
    if prefer != "piper":
        espeak = _EspeakEngine()
        if espeak.available():
            return espeak
    return _Engine()  # no-op


# ── Сервис ==============================================================
class TextToSpeech:
    """Очередь озвучек с фоновым потоком-проигрывателем."""

    def __init__(self, enabled: bool = True, prefer: str = "auto"):
        self._enabled = enabled
        self._prefer = prefer
        self._engine: _Engine = _select_engine(prefer)
        self._queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=16)
        self._worker: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        if self._engine.name != "none":
            log.info("TTS: использую движок %r", self._engine.name)
        else:
            log.warning("TTS: ни piper, ни espeak-ng не доступны — озвучка выключена")

    # ── public ─────────────────────────────────────────────────────────
    @property
    def engine_name(self) -> str:
        return self._engine.name

    @property
    def enabled(self) -> bool:
        return self._enabled and self._engine.name != "none"

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def set_voice(self, prefer: str) -> None:
        """Переключает движок налету (auto/piper/espeak/off)."""
        if prefer == self._prefer:
            return
        self._prefer = prefer
        if prefer == "off":
            self._engine = _Engine()
            return
        try:
            self._engine.stop()
        except Exception:  # noqa: BLE001
            pass
        self._engine = _select_engine(prefer)
        log.info("TTS: переключён движок → %r", self._engine.name)

    def is_enabled(self) -> bool:
        return self.enabled

    def say(self, text: str) -> None:
        """Кладёт фразу в очередь, не блокируя поток."""
        if not self.is_enabled() or not text.strip():
            return
        # Безопасность: вырезаем подозрительные технические символы из речи.
        cleaned = _clean_for_speech(text)
        if not cleaned:
            return
        self._ensure_worker()
        try:
            self._queue.put_nowait(cleaned)
        except queue.Full:
            log.debug("TTS очередь переполнена, пропускаем фразу")

    def interrupt(self) -> None:
        """Сбрасывает очередь и останавливает текущее проигрывание."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._engine.stop()

    def shutdown(self) -> None:
        self._stop_flag.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not None:
            self._worker.join(timeout=2.0)
        self._engine.stop()

    # ── internals ──────────────────────────────────────────────────────
    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_flag.clear()
        self._worker = threading.Thread(
            target=self._loop, name="akali-tts", daemon=True,
        )
        self._worker.start()

    def _loop(self) -> None:
        while not self._stop_flag.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                self._engine.speak(item)
            except Exception as e:  # noqa: BLE001
                log.warning("TTS sayback error: %s", e)


_FORBIDDEN_PREFIX = ("sudo", "rm ", "dd ", "mkfs", "/dev/")


def _clean_for_speech(text: str) -> str:
    """Убирает технические символы и опасные подстроки из реплики."""
    text = text.strip()
    lower = text.lower()
    if any(lower.startswith(p) for p in _FORBIDDEN_PREFIX):
        return ""
    # Срезаем самые шумные символы: |, &, ;, > и т.п.
    for ch in "|&;<>`$\\":
        text = text.replace(ch, " ")
    text = " ".join(text.split())
    return text[:140]   # длиннее не озвучиваем
