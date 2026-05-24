"""Озвучка ответов ассистента приятным человеческим голосом.

Приоритет движков:
    1. `xtts` (Coqui XTTS v2 — клонированный голос Джарвиса из reference WAV).
       Включается ТОЛЬКО если установлен пакет `TTS` И существует
       voices/jarvis_reference.wav. Высокое качество, но требует ~2 ГБ модели
       и заметно медленнее piper. Идеален для прекэша.
    2. `piper` (нейронный, оффлайн, мужской голос ru_RU-dmitri-medium «Джарвис»).
    3. `espeak-ng -v ru+m3` — фоллбэк для машин без piper.
    4. Тихий no-op, если ничего не доступно.

Архитектура:
    • Все say() — неблокирующие: фраза кладётся в очередь, воркер играет.
    • Прогрев: при старте генерируем .wav файлы для COMMON_PHRASES и
      кэшируем в ~/.cache/akali/tts/. Кэш привязан к движку + voice mtime
      (если голос обновили — кэш инвалидируется).
    • say(text) сначала ищет кэш-файл; если есть — играем мгновенно через
      paplay/pw-play/aplay. Иначе синтезируем налету.

ВАЖНО: НЕ озвучиваем сами bash-команды (там может быть rm -rf или другая
чувствительная инфа). Только короткие подтверждения.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Iterable, Optional

from . import system_check

log = logging.getLogger(__name__)


# ── Прогреваемые фразы ==================================================
# Часто используемые ответы — кэшируются как WAV при первом старте,
# чтобы reproduction был мгновенным.
COMMON_PHRASES: tuple[str, ...] = (
    "Понял, выполняю",
    "Запускаю",
    "Сделано",
    "Готово",
    "Не понял команду",
    "Команда заблокирована",
    "Выполняю команду питания",
    "Перезапускаюсь",
    "Ошибка выполнения",
    "Команды нет в базе",
    "Жду команду",
)


def _cache_dir() -> Path:
    """Возвращает ~/.cache/akali/tts/, создавая директорию."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or
                Path.home() / ".cache")
    d = base / "akali" / "tts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(engine_name: str, voice_signature: str, text: str) -> str:
    h = hashlib.sha1(
        f"{engine_name}|{voice_signature}|{text}".encode("utf-8")
    ).hexdigest()[:16]
    return h


# ── Движки ==============================================================
class _Engine:
    """Базовый интерфейс TTS-движка."""

    name = "none"

    def available(self) -> bool:
        return False

    def voice_signature(self) -> str:
        """Уникальный отпечаток голоса (для инвалидации кэша)."""
        return self.name

    def synthesize_wav(self, text: str, dest: Path) -> bool:
        """Синтезирует фразу в WAV-файл. True если успех."""
        return False

    def speak(self, text: str) -> None:
        """Синхронно произносит текст (для streaming-режима, не из кэша)."""
        return None

    def stop(self) -> None:
        return None


def _find_piper_voice(prefer_name: Optional[str] = None) -> Optional[Path]:
    """Возвращает .onnx файл голоса для piper.
    1. Если задано prefer_name (например 'ru_RU-dmitri-medium') — он.
    2. Иначе — дефолт из system_check (dmitri).
    3. Если ни одно не существует — любой ru_RU-*-medium.onnx.
    """
    voices_dir = system_check.PIPER_VOICE_DIR
    if prefer_name:
        candidate = voices_dir / f"{prefer_name}.onnx"
        if candidate.exists():
            return candidate
    if system_check.PIPER_VOICE_FILE.exists():
        return system_check.PIPER_VOICE_FILE
    for alt in voices_dir.glob("ru_RU-*-medium.onnx"):
        return alt
    return None


class _PiperEngine(_Engine):
    name = "piper"

    def __init__(self, voice_name: Optional[str] = None):
        self.voice = _find_piper_voice(voice_name)
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._aplay: Optional[subprocess.Popen[bytes]] = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if not shutil.which("piper"):
            return False
        if self.voice is None or not self.voice.exists():
            return False
        return bool(_audio_player_cmd())

    def voice_signature(self) -> str:
        if self.voice is None:
            return "piper:no-voice"
        try:
            return f"piper:{self.voice.name}:{self.voice.stat().st_mtime_ns}"
        except OSError:
            return "piper:no-voice"

    def synthesize_wav(self, text: str, dest: Path) -> bool:
        """piper --output_file dest.wav. Без стрима — чистый WAV."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".wav.part")
        try:
            proc = subprocess.run(
                ["piper", "--model", str(self.voice),
                 "--output_file", str(tmp), "--quiet"],
                input=text.encode("utf-8"),
                capture_output=True, timeout=20,
            )
            if proc.returncode != 0:
                log.debug("piper synthesize rc=%d: %s", proc.returncode,
                          proc.stderr[:200].decode(errors="ignore"))
                tmp.unlink(missing_ok=True)
                return False
            if not tmp.exists() or tmp.stat().st_size < 100:
                tmp.unlink(missing_ok=True)
                return False
            os.replace(tmp, dest)
            return True
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("piper synthesize error: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def speak(self, text: str) -> None:
        """Стрим: piper --output_raw | paplay --raw."""
        player = _audio_raw_player_cmd()
        if player is None:
            log.debug("piper: нет проигрывателя сырого PCM")
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


# ── XTTS v2 (Coqui) ─────────────────────────────────────────────────
# Заглушка-инфраструктура. Реальная инициализация модели произойдёт ТОЛЬКО
# когда пользователь предоставит voices/jarvis_reference.wav. До этого
# момента engine.available() возвращает False и движок не выбирается.
#
# Принципы:
#   • lazy-load: import TTS происходит только при первой попытке использовать
#     движок (он тащит torch ~2 ГБ — не хотим грузить даром).
#   • thread-safe init: одна общая модель XTTS на процесс, защищена локом.
#   • language='ru' (XTTS v2 поддерживает мультиязычность, для жалкого ру
#     лучше всего работает явное указание).
#   • кэш весов модели лежит в ~/.local/share/tts/ (стандартный путь Coqui)

_XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
_XTTS_LOCK = threading.Lock()
_XTTS_MODEL = None  # type: ignore[var-annotated]


def _xtts_package_available() -> bool:
    """Импортится ли пакет TTS (Coqui). НЕ загружает модель."""
    try:
        import importlib.util  # noqa: WPS433
        return importlib.util.find_spec("TTS") is not None
    except Exception:  # noqa: BLE001
        return False


def _xtts_reference_wav() -> Optional[Path]:
    """Возвращает путь к jarvis_reference.wav если он существует.
    Импорт `paths` лениво, чтобы не было циклических импортов."""
    try:
        from .. import paths  # noqa: WPS433
        wav = paths.XTTS_REFERENCE_WAV
        if wav.exists() and wav.stat().st_size > 1000:
            return wav
        return None
    except Exception:  # noqa: BLE001
        return None


class _XTTSEngine(_Engine):
    """XTTS v2 (Coqui) — клонированный голос из reference WAV.

    Готовность движка определяется по двум условиям:
      1. Установлен пакет `TTS` (pip install TTS).
      2. Существует voices/jarvis_reference.wav (загружается пользователем).

    Если любое условие не выполнено — engine.available() = False, и
    выбор движков в _select_engine() пропускает XTTS.
    """

    name = "xtts"

    def __init__(self, reference_wav: Optional[Path] = None,
                 language: str = "ru"):
        self.reference_wav = reference_wav or _xtts_reference_wav()
        self.language = language
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        if not _xtts_package_available():
            return False
        if self.reference_wav is None or not self.reference_wav.exists():
            return False
        return bool(_audio_player_cmd())

    def voice_signature(self) -> str:
        if self.reference_wav is None:
            return "xtts:no-ref"
        try:
            return f"xtts:{self.reference_wav.name}:{self.reference_wav.stat().st_mtime_ns}"
        except OSError:
            return "xtts:no-ref"

    def _get_model(self):
        """Lazy-load модели. Возвращает TTS instance или None."""
        global _XTTS_MODEL
        with _XTTS_LOCK:
            if _XTTS_MODEL is not None:
                return _XTTS_MODEL
            try:
                from TTS.api import TTS as _TTSApi  # type: ignore  # noqa: WPS433
            except Exception as e:  # noqa: BLE001
                log.warning("XTTS: пакет TTS не загружается: %s", e)
                return None
            try:
                log.info("XTTS: первая загрузка модели %s "
                         "(может занять минуту)…", _XTTS_MODEL_NAME)
                _XTTS_MODEL = _TTSApi(model_name=_XTTS_MODEL_NAME,
                                       progress_bar=False, gpu=False)
                log.info("XTTS: модель готова")
                return _XTTS_MODEL
            except Exception as e:  # noqa: BLE001
                log.error("XTTS: не удалось загрузить модель: %s", e)
                return None

    def synthesize_wav(self, text: str, dest: Path) -> bool:
        if self.reference_wav is None:
            return False
        model = self._get_model()
        if model is None:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".wav.part")
        try:
            with self._lock:
                model.tts_to_file(
                    text=text,
                    speaker_wav=str(self.reference_wav),
                    language=self.language,
                    file_path=str(tmp),
                )
            if not tmp.exists() or tmp.stat().st_size < 100:
                tmp.unlink(missing_ok=True)
                return False
            os.replace(tmp, dest)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("XTTS synthesize_wav: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def speak(self, text: str) -> None:
        """Синхронный синтез + воспроизведение через временный WAV."""
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "out.wav"
            if self.synthesize_wav(text, wav):
                _play_wav(wav)

    def stop(self) -> None:
        return None


class _EspeakEngine(_Engine):
    name = "espeak-ng"

    def __init__(self, voice: str = "ru+m3"):
        # m3 = мужской голос (близко к Джарвису, в духе мужского piper).
        # f3 был старым дефолтом, но user попросил мужской.
        self.voice = voice
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(shutil.which("espeak-ng") or shutil.which("espeak"))

    def voice_signature(self) -> str:
        return f"espeak:{self.voice}"

    def _bin(self) -> str:
        return "espeak-ng" if shutil.which("espeak-ng") else "espeak"

    def synthesize_wav(self, text: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".wav.part")
        try:
            proc = subprocess.run(
                [self._bin(), "-v", self.voice, "-s", "165", "-p", "55",
                 "--punct=", "-w", str(tmp), "--", text],
                capture_output=True, timeout=20,
            )
            if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 100:
                tmp.unlink(missing_ok=True)
                return False
            os.replace(tmp, dest)
            return True
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("espeak synthesize error: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def speak(self, text: str) -> None:
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    [self._bin(), "-v", self.voice, "-s", "165",
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


# ── Хелперы аудио ======================================================
def _audio_player_cmd() -> list[str] | None:
    """Команда для проигрывания WAV-файла. Принимает путь как arg."""
    for cmd in (
        ["paplay"],
        ["pw-play"],
        ["aplay", "-q"],
    ):
        if shutil.which(cmd[0]):
            return cmd
    return None


def _audio_raw_player_cmd() -> list[str] | None:
    """Команда для проигрывания сырого PCM 22050 Hz s16le mono со стандартного входа."""
    for cmd in (
        ["pw-play", "-"],
        ["paplay", "--raw", "--rate=22050",
         "--format=s16le", "--channels=1"],
        ["aplay", "-q", "-r", "22050", "-f", "S16_LE", "-c", "1"],
    ):
        if shutil.which(cmd[0]):
            return cmd
    return None


def _play_wav(path: Path) -> None:
    """Проигрывает готовый WAV-файл, не возвращается пока не доиграется."""
    player = _audio_player_cmd()
    if player is None:
        log.debug("нет аудио-плеера для %s", path)
        return
    try:
        subprocess.run(
            [*player, str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.debug("WAV play error: %s", e)


def _select_engine(prefer: str = "auto",
                    piper_voice: Optional[str] = None) -> _Engine:
    """Выбирает доступный движок по приоритету.
    prefer ∈ {auto, xtts, piper, espeak, off}.
    piper_voice — имя голоса для piper, например 'ru_RU-dmitri-medium'.

    Auto-приоритет: xtts (если есть reference WAV + пакет) > piper > espeak.
    Если prefer='xtts' но XTTS не готов — НЕ падаем, плавно скатываемся
    дальше по списку.
    """
    if prefer == "off":
        return _Engine()
    if prefer in ("auto", "xtts"):
        xtts = _XTTSEngine()
        if xtts.available():
            return xtts
        if prefer == "xtts":
            log.warning("XTTS выбран, но недоступен (нет reference WAV "
                        "или пакета TTS) — фоллбэк на piper/espeak")
    if prefer in ("auto", "piper", "xtts"):
        piper = _PiperEngine(voice_name=piper_voice)
        if piper.available():
            return piper
    if prefer in ("auto", "espeak", "piper", "xtts"):
        espeak = _EspeakEngine()
        if espeak.available():
            return espeak
    return _Engine()  # no-op


# ── Сервис ==============================================================
class TextToSpeech:
    """Очередь озвучек с фоновым потоком и WAV-прекэшем частых фраз."""

    def __init__(self, enabled: bool = True, prefer: str = "auto",
                  piper_voice: Optional[str] = None):
        self._enabled = enabled
        self._prefer = prefer
        self._piper_voice = piper_voice
        self._engine: _Engine = _select_engine(prefer, piper_voice)
        self._queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=16)
        self._worker: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._cache_lock = threading.Lock()
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

    def set_voice(self, prefer: str,
                  piper_voice: Optional[str] = None) -> None:
        """Переключает движок налету (auto/piper/espeak/off).
        Если piper_voice указан, переключает голос Piper в т.ч. на лету.
        """
        if prefer == self._prefer and piper_voice == self._piper_voice:
            return
        self._prefer = prefer
        if piper_voice is not None:
            self._piper_voice = piper_voice
        if prefer == "off":
            self._engine = _Engine()
            return
        try:
            self._engine.stop()
        except Exception:  # noqa: BLE001
            pass
        self._engine = _select_engine(prefer, self._piper_voice)
        if self._engine.name == "piper" and isinstance(self._engine, _PiperEngine):
            log.info("TTS: голос Piper → %s",
                     self._engine.voice.name if self._engine.voice else "?")
        else:
            log.info("TTS: переключён движок → %r", self._engine.name)

    def is_enabled(self) -> bool:
        return self.enabled

    def say(self, text: str) -> None:
        """Кладёт фразу в очередь, не блокируя поток."""
        if not self.is_enabled() or not text.strip():
            return
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

    # ── Прекэш ════════════════════════════════════════════════════════
    def prewarm(self, phrases: Iterable[str] = COMMON_PHRASES,
                blocking: bool = False) -> None:
        """Генерирует WAV-файлы для частых фраз. По умолчанию — фоном."""
        if not self.is_enabled():
            log.debug("TTS prewarm пропущен: движок недоступен")
            return
        if blocking:
            self._prewarm_run(list(phrases))
        else:
            threading.Thread(
                target=self._prewarm_run, args=(list(phrases),),
                name="akali-tts-prewarm", daemon=True,
            ).start()

    def _prewarm_run(self, phrases: list[str]) -> None:
        log.info("TTS: прогреваю кэш на %d фраз (%s)…",
                 len(phrases), self._engine.name)
        ok = 0
        for ph in phrases:
            cleaned = _clean_for_speech(ph)
            if not cleaned:
                continue
            path = self._cache_path(cleaned)
            if path.exists() and path.stat().st_size > 100:
                ok += 1
                continue
            with self._cache_lock:
                if self._engine.synthesize_wav(cleaned, path):
                    ok += 1
        log.info("TTS: прогрев готов (%d/%d фраз закэшировано в %s)",
                 ok, len(phrases), _cache_dir())

    def _cache_path(self, text: str) -> Path:
        key = _cache_key(self._engine.name,
                         self._engine.voice_signature(), text)
        return _cache_dir() / f"{key}.wav"

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
                # Сначала — кэш
                cached = self._cache_path(item)
                if cached.exists() and cached.stat().st_size > 100:
                    _play_wav(cached)
                    continue
                # Иначе синтез на лету. Параллельно пишем в кэш, чтобы
                # следующий раз был мгновенным.
                if self._engine.name in ("piper", "espeak-ng"):
                    if self._engine.synthesize_wav(item, cached):
                        _play_wav(cached)
                        continue
                # Фоллбэк на стрим
                self._engine.speak(item)
            except Exception as e:  # noqa: BLE001
                log.warning("TTS playback error: %s", e)


_FORBIDDEN_PREFIX = ("sudo", "rm ", "dd ", "mkfs", "/dev/")


def _clean_for_speech(text: str) -> str:
    """Убирает технические символы и опасные подстроки из реплики."""
    text = text.strip()
    lower = text.lower()
    if any(lower.startswith(p) for p in _FORBIDDEN_PREFIX):
        return ""
    for ch in "|&;<>`$\\":
        text = text.replace(ch, " ")
    text = " ".join(text.split())
    return text[:140]
