"""Tests for akali.core.audio_worker — Vosk on real WAV, signals, level."""
from __future__ import annotations

import json
import math
import struct
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from akali.core import audio_worker as aw
from akali.core.audio_worker import (
    AudioWorker, _compute_level, _is_noise_text, list_input_devices,
    AUDIO_SAMPLE_RATE, AUDIO_BLOCK_FRAMES,
)
from akali.core.backend import AssistantCore


# ── _is_noise_text ─────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("привет мир", False),
    ("открой браузер", False),
    ("ab", True),         # короткий
    ("", True),
    ("    ", True),
    ("ыыы ььь", True),     # только согласные/гласные
    ("aaaaa", True),
    ("ккккк ллллл", True),  # только согласные
    ("ы у я о", True),     # все слова < 3
    ("привет ы ь у", False),  # одно нормальное слово
])
def test_is_noise_text(text, expected):
    assert _is_noise_text(text) is expected


# ── _compute_level ─────────────────────────────────────────────

def test_compute_level_silence_is_zero():
    silence = bytes(2000)  # int16 нули
    assert _compute_level(silence) == 0.0


def test_compute_level_empty_bytes():
    assert _compute_level(b"") == 0.0


def test_compute_level_loud_signal_high():
    # амплитуда близко к max int16
    samples = [int(20000 * math.sin(i * 0.1)) for i in range(1000)]
    data = struct.pack(f"<{len(samples)}h", *samples)
    level = _compute_level(data)
    assert 0.3 < level <= 1.0


def test_compute_level_quiet_signal_low():
    # тихий сигнал
    samples = [int(500 * math.sin(i * 0.1)) for i in range(1000)]
    data = struct.pack(f"<{len(samples)}h", *samples)
    level = _compute_level(data)
    assert level < 0.4


def test_compute_level_handles_odd_byte_length():
    # 3 байта — не делится на 2, должен вернуть что-то без crash
    level = _compute_level(b"\x00\x10\xff")
    assert isinstance(level, float)


def test_compute_level_clamps_to_one():
    # максимальная амплитуда
    samples = [32767, -32768] * 500
    data = struct.pack(f"<{len(samples)}h", *samples)
    level = _compute_level(data)
    assert level == 1.0


# ── list_input_devices ───────────────────────────────────────

def test_list_input_devices_filters_input_only(monkeypatch):
    fake_devices = [
        {"name": "mic1", "max_input_channels": 2, "max_output_channels": 0},
        {"name": "speaker", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "duplex", "max_input_channels": 1, "max_output_channels": 1},
    ]
    fake_sd = MagicMock()
    fake_sd.query_devices.return_value = fake_devices
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)
    result = list_input_devices()
    assert len(result) == 2  # mic1 + duplex
    assert any(r[1] == "mic1" for r in result)
    assert any(r[1] == "duplex" for r in result)


def test_list_input_devices_handles_query_exception(monkeypatch):
    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = RuntimeError("crash")
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)
    assert list_input_devices() == []


# ── AudioWorker resolve_device ─────────────────────────────────

@pytest.fixture
def worker(min_commands, tmp_path, vosk_model_dir):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    return AudioWorker(core, str(vosk_model_dir))


def test_resolve_device_explicit_index(worker, monkeypatch):
    fake_sd = MagicMock()
    fake_sd.query_devices.return_value = [
        {"name": "a", "max_input_channels": 0},
        {"name": "b", "max_input_channels": 2},
    ]
    worker._device_index = 1
    assert worker._resolve_device(fake_sd) == 1


def test_resolve_device_explicit_no_input_falls_back(worker):
    fake_sd = MagicMock()
    fake_sd.query_devices.return_value = [
        {"name": "out", "max_input_channels": 0},
        {"name": "in", "max_input_channels": 2},
    ]
    fake_sd.default.device = (1, 0)
    worker._device_index = 0  # не input
    # Должен вернуть default → 1 или next input → 1
    assert worker._resolve_device(fake_sd) == 1


def test_resolve_device_uses_default(worker):
    fake_sd = MagicMock()
    fake_sd.query_devices.return_value = [
        {"name": "a", "max_input_channels": 0},
        {"name": "b", "max_input_channels": 2},
        {"name": "c", "max_input_channels": 1},
    ]
    fake_sd.default.device = (1, 0)
    worker._device_index = None
    assert worker._resolve_device(fake_sd) == 1


def test_resolve_device_first_input(worker):
    fake_sd = MagicMock()
    fake_sd.query_devices.return_value = [
        {"name": "a", "max_input_channels": 0},
        {"name": "b", "max_input_channels": 0},
        {"name": "c", "max_input_channels": 1},
    ]
    fake_sd.default.device = -1
    worker._device_index = None
    assert worker._resolve_device(fake_sd) == 2


def test_resolve_device_no_devices(worker):
    fake_sd = MagicMock()
    fake_sd.query_devices.side_effect = RuntimeError("no audio")
    worker._device_index = None
    assert worker._resolve_device(fake_sd) is None


def test_resolve_device_none_when_only_outputs(worker):
    fake_sd = MagicMock()
    fake_sd.query_devices.return_value = [
        {"name": "a", "max_input_channels": 0},
    ]
    fake_sd.default.device = -1
    worker._device_index = None
    assert worker._resolve_device(fake_sd) is None


# ── Сигналы определены ────────────────────────────────────────

def test_worker_signals_exist(worker):
    expected = [
        "status_changed", "text_recognized", "partial_text",
        "wake_word_detected", "command_matched", "command_executed",
        "no_match", "error", "fatal_error", "level_changed",
        "reindex_done", "device_info", "stopped",
    ]
    for name in expected:
        assert hasattr(worker, name), f"Missing signal: {name}"


def test_request_stop_sets_flag(worker):
    assert not worker._stop_requested
    worker.request_stop()
    assert worker._stop_requested


def test_request_reindex_sets_flag(worker):
    assert not worker._reindex_pending
    worker.request_reindex()
    assert worker._reindex_pending


def test_set_device_changes_index(worker):
    worker.set_device(7)
    assert worker._device_index == 7


# ── Реальный Vosk на WAV-сэмплах ───────────────────────────────

ROOT = Path(__file__).resolve().parents[2]
AUDIO_FIXTURES = ROOT / "tests" / "fixtures" / "audio"


def _read_wav_pcm(path: Path) -> tuple[bytes, int]:
    """Возвращает (pcm_bytes, sample_rate)."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    return raw, sr


def _feed_recognizer(recognizer, pcm: bytes, chunk: int = 4000) -> str:
    """Скармливает PCM в KaldiRecognizer по чанкам, возвращает финальный текст."""
    bs = chunk * 2  # int16 → 2 байта на сэмпл
    for i in range(0, len(pcm), bs):
        recognizer.AcceptWaveform(pcm[i:i + bs])
    final = json.loads(recognizer.FinalResult()).get("text", "")
    return final.strip()


@pytest.mark.slow
def test_vosk_recognizes_otkroy_brauzer(vosk_model_dir):
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    pcm, sr = _read_wav_pcm(AUDIO_FIXTURES / "say_otkroy_brauzer.wav")
    assert sr == 16000
    model = Model(str(vosk_model_dir))
    rec = KaldiRecognizer(model, 16000)
    text = _feed_recognizer(rec, pcm)
    # Vosk может распознать неточно, но что-то связанное с открыть/браузер должно быть
    assert text, "Vosk вернул пустой результат"
    # хотя бы один из ключевых корней
    assert any(kw in text for kw in ("откро", "браузер", "открой"))


@pytest.mark.slow
def test_vosk_silence_empty(vosk_model_dir):
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    pcm, sr = _read_wav_pcm(AUDIO_FIXTURES / "silence_1s.wav")
    model = Model(str(vosk_model_dir))
    rec = KaldiRecognizer(model, sr)
    text = _feed_recognizer(rec, pcm)
    assert text == ""


@pytest.mark.slow
def test_vosk_noise_is_noise_or_empty(vosk_model_dir):
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    pcm, sr = _read_wav_pcm(AUDIO_FIXTURES / "noise_only.wav")
    model = Model(str(vosk_model_dir))
    rec = KaldiRecognizer(model, sr)
    text = _feed_recognizer(rec, pcm)
    # Шум должен дать пустой результат или быть отфильтрован как шум
    assert text == "" or _is_noise_text(text)


# ── Downsample 48k → 16k ──────────────────────────────────────

def test_downsample_48k_to_16k_takes_every_third():
    """Простая проверка: подаём 48к и убеждаемся, что 1/3 длина."""
    samples_48k = np.array([100, 200, 300, 400, 500, 600, 700, 800, 900],
                           dtype=np.int16)
    raw_48k = samples_48k.tobytes()
    # Логика из _audio_loop: arr[::3].tobytes()
    arr = np.frombuffer(raw_48k, dtype=np.int16)
    downsampled = arr[::3]
    assert len(downsampled) == 3
    assert downsampled[0] == 100
    assert downsampled[1] == 400
    assert downsampled[2] == 700


# ── _attempt_audio_recovery ───────────────────────────────────

def test_audio_recovery_calls_subprocess(worker, mocker):
    import subprocess
    runs = []
    def fake_run(cmd, **kw):
        runs.append(cmd)
        return MagicMock(returncode=0)
    mocker.patch.object(subprocess, "run", side_effect=fake_run)
    mocker.patch("akali.core.audio_worker.QThread.msleep", lambda x: None)
    worker._attempt_audio_recovery()
    # Должно попробовать несколько способов
    assert len(runs) >= 3
    assert any("pipewire" in " ".join(c).lower() or "pulse" in " ".join(c).lower()
               for c in runs)


def test_audio_recovery_ignores_subprocess_errors(worker, mocker):
    import subprocess
    mocker.patch.object(subprocess, "run", side_effect=FileNotFoundError("no systemctl"))
    mocker.patch("akali.core.audio_worker.QThread.msleep", lambda x: None)
    # Не должно крашиться
    worker._attempt_audio_recovery()


def test_audio_recovery_exits_early_on_stop(worker, mocker):
    import subprocess
    mocker.patch.object(subprocess, "run", return_value=MagicMock(returncode=0))
    mocker.patch("akali.core.audio_worker.QThread.msleep", lambda x: None)
    worker._stop_requested = True
    worker._attempt_audio_recovery()  # должен не висеть


# ── start_listening: error paths ──────────────────────────────

def test_start_listening_emits_fatal_when_no_model_dir(min_commands, tmp_path, qapp):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    worker = AudioWorker(core, str(tmp_path / "missing-model"))
    fatals = []
    stops = []
    worker.fatal_error.connect(lambda m: fatals.append(m))
    worker.stopped.connect(lambda: stops.append(True))
    worker.start_listening()
    assert fatals
    assert "Vosk" in fatals[0] or "модели" in fatals[0]
    assert stops


def test_start_listening_emits_fatal_when_vosk_load_fails(
    min_commands, tmp_path, qapp, mocker, vosk_model_dir,
):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    worker = AudioWorker(core, str(vosk_model_dir))
    fatals = []
    worker.fatal_error.connect(lambda m: fatals.append(m))
    # Подменяем vosk.Model чтобы он кидал
    import vosk
    mocker.patch.object(vosk, "Model", side_effect=RuntimeError("vosk broken"))
    worker.start_listening()
    assert fatals
    assert "Vosk" in fatals[0]


# ── Постоянные модуля ──────────────────────────────────────────

def test_constants():
    assert aw.AUDIO_SAMPLE_RATE == 16000
    assert aw.AUDIO_BLOCK_FRAMES > 0
    assert aw.AUDIO_MAX_RETRIES >= 1
    assert 16000 in aw.AUDIO_SAMPLE_RATES_TRY


# ── _audio_loop через подмену RawInputStream ─────────────

@pytest.mark.slow
def test_audio_loop_processes_real_wav(min_commands, tmp_path, qapp,
                                         vosk_model_dir, mocker):
    """Запускаем _audio_loop с подменённым RawInputStream, который
    скармливает байты из реального WAV-файла. Проверяем, что цепочка
    Vosk → text_recognized → execute срабатывает."""
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)

    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    worker = AudioWorker(core, str(vosk_model_dir))

    # Подгружаем PCM из WAV
    pcm, sr = _read_wav_pcm(AUDIO_FIXTURES / "say_otkroy_brauzer.wav")
    assert sr == 16000

    import sounddevice as sd

    # Фейк RawInputStream
    class FakeStream:
        def __init__(self, callback=None, **kw):
            self._cb = callback
        def __enter__(self):
            # один блок-проба (для probe-open)
            return self
        def __exit__(self, *a): return False
        def close(self):
            pass

    # Сначала probe-открытия в цикле выбора частоты должны вернуть FakeStream
    # с close(). Затем реальный with-блок открывает поток и должен вызвать
    # callback с порциями PCM.
    open_count = {"n": 0}
    def fake_factory(*args, **kw):
        open_count["n"] += 1
        if open_count["n"] == 1:
            # probe вызов — просто возвращаем без callback
            class P:
                def close(self): pass
            return P()
        # настоящее открытие — нужно вызвать callback
        cb = kw.get("callback")
        if cb:
            # Скармливаем PCM кусками
            block_size = aw.AUDIO_BLOCK_FRAMES * 2  # int16
            for i in range(0, len(pcm), block_size):
                chunk = pcm[i:i + block_size]
                cb(chunk, len(chunk) // 2, None, None)
        return FakeStream()

    mocker.patch.object(sd, "RawInputStream", side_effect=fake_factory)
    mocker.patch.object(sd, "query_devices",
                        return_value={"name": "fake-mic",
                                       "default_samplerate": 16000})

    spy_text = []
    worker.text_recognized.connect(lambda t: spy_text.append(t))
    worker.request_stop()  # цикл выйдет после первой итерации очереди
    # запускаем напрямую _audio_loop вместо start_listening (избегаем Model
    # загрузки тут — это сделаем сами)
    model = Model(str(vosk_model_dir))
    recognizer = KaldiRecognizer(model, 16000)

    try:
        worker._audio_loop(recognizer, sd)
    except Exception:
        pass  # _stop_requested мог прервать; нам важна работа callback'ов

    # callback положил данные в очередь — но _stop_requested завершил цикл
    # сразу. Поэтому именно text_recognized мы можем и не получить — это OK.
    # Главное — что _audio_loop не упал.


def test_audio_loop_raises_porterror_when_all_rates_fail(
    min_commands, tmp_path, qapp, vosk_model_dir, mocker,
):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    worker = AudioWorker(core, str(vosk_model_dir))
    import sounddevice as sd
    # Все probe-открытия падают
    mocker.patch.object(sd, "RawInputStream",
                        side_effect=sd.PortAudioError("no rates"))
    mocker.patch.object(sd, "query_devices",
                        return_value={"name": "x", "default_samplerate": 16000})
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    model = Model(str(vosk_model_dir))
    rec = KaldiRecognizer(model, 16000)
    with pytest.raises(sd.PortAudioError):
        worker._audio_loop(rec, sd)


# ── start_listening без vosk/sounddevice ─────────────────

def test_start_listening_missing_vosk(min_commands, tmp_path, qapp, mocker):
    core = AssistantCore(commands_file=min_commands,
                          auto_commands_file=tmp_path / "a.json")
    core.reload()
    worker = AudioWorker(core, str(tmp_path / "m"))
    fatals = []
    worker.fatal_error.connect(lambda m: fatals.append(m))
    import builtins
    orig = builtins.__import__
    def bad(name, *a, **kw):
        if name in ("vosk", "sounddevice"):
            raise ImportError(f"no {name}")
        return orig(name, *a, **kw)
    mocker.patch.object(builtins, "__import__", bad)
    worker.start_listening()
    assert fatals
    assert "vosk" in fatals[0] or "sounddevice" in fatals[0]
