"""Tests for akali.core.tts — TTS engines, cache, queue."""
from __future__ import annotations

import shutil
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from akali.core import tts


HAS_ESPEAK = bool(shutil.which("espeak-ng") or shutil.which("espeak"))


# ── _clean_for_speech ─────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected_empty", [
    ("sudo apt install foo", True),
    ("SUDO apt install foo", True),
    ("rm -rf /tmp/x", True),
    ("rm file.txt", True),  # начинается с "rm "
    ("dd if=/dev/zero", True),
    ("mkfs.ext4 /dev/sda", True),
    ("/dev/null", True),
])
def test_clean_for_speech_blocks_forbidden(text, expected_empty):
    result = tts._clean_for_speech(text)
    if expected_empty:
        assert result == ""


def test_clean_for_speech_passes_normal():
    assert tts._clean_for_speech("Привет, мир") == "Привет, мир"
    assert tts._clean_for_speech("Готово") == "Готово"


def test_clean_for_speech_strips_metachars():
    assert "|" not in tts._clean_for_speech("foo | bar")
    assert "&" not in tts._clean_for_speech("foo & bar")
    assert ";" not in tts._clean_for_speech("foo;bar")
    assert "$" not in tts._clean_for_speech("foo $var")
    assert "`" not in tts._clean_for_speech("foo `bar`")


def test_clean_for_speech_truncates_at_140():
    long = "а" * 200
    out = tts._clean_for_speech(long)
    assert len(out) <= 140


def test_clean_for_speech_collapses_whitespace():
    assert tts._clean_for_speech("foo    bar\n\tbaz") == "foo bar baz"


def test_clean_for_speech_empty():
    assert tts._clean_for_speech("") == ""
    assert tts._clean_for_speech("   ") == ""


# ── _cache_key ────────────────────────────────────────────────────────

def test_cache_key_stable_for_same_inputs():
    k1 = tts._cache_key("piper", "voice-sig-1", "привет")
    k2 = tts._cache_key("piper", "voice-sig-1", "привет")
    assert k1 == k2
    assert len(k1) == 16


def test_cache_key_changes_with_engine():
    k1 = tts._cache_key("piper", "v", "x")
    k2 = tts._cache_key("espeak", "v", "x")
    assert k1 != k2


def test_cache_key_changes_with_voice_signature():
    k1 = tts._cache_key("piper", "voice-a", "x")
    k2 = tts._cache_key("piper", "voice-b", "x")
    assert k1 != k2


def test_cache_key_changes_with_text():
    k1 = tts._cache_key("piper", "v", "x")
    k2 = tts._cache_key("piper", "v", "y")
    assert k1 != k2


# ── _cache_dir ────────────────────────────────────────────────────────

def test_cache_dir_under_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    d = tts._cache_dir()
    assert d.is_dir()
    assert d == tmp_path / "akali" / "tts"


# ── Engines ───────────────────────────────────────────────────────────

def test_base_engine_defaults():
    e = tts._Engine()
    assert e.name == "none"
    assert not e.available()
    assert e.voice_signature() == "none"
    assert not e.synthesize_wav("x", Path("/tmp/x"))


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak-ng не установлен")
def test_espeak_engine_available():
    e = tts._EspeakEngine()
    assert e.available()
    assert e.name == "espeak-ng"
    sig = e.voice_signature()
    assert "espeak" in sig


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak-ng не установлен")
def test_espeak_synthesizes_real_wav(tmp_path):
    e = tts._EspeakEngine()
    dest = tmp_path / "out.wav"
    ok = e.synthesize_wav("привет", dest)
    assert ok
    assert dest.exists()
    assert dest.stat().st_size > 1000
    with wave.open(str(dest), "rb") as w:
        assert w.getframerate() > 0
        assert w.getnframes() > 0


def test_espeak_synthesize_fails_on_unwritable_path(tmp_path):
    if not HAS_ESPEAK:
        pytest.skip("espeak missing")
    e = tts._EspeakEngine()
    # Безнадёжный путь — не существует и не создаётся
    bad = Path("/proc/1/forbidden.wav")
    ok = e.synthesize_wav("x", bad)
    assert not ok


def test_piper_engine_unavailable_without_binary(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    e = tts._PiperEngine()
    assert not e.available()
    assert e.voice_signature().startswith("piper")


def test_piper_synthesize_returns_false_no_voice(tmp_path):
    e = tts._PiperEngine()
    e.voice = None  # форсируем
    ok = e.synthesize_wav("x", tmp_path / "y.wav")
    assert not ok


def test_xtts_engine_unavailable_without_reference():
    e = tts._XTTSEngine(reference_wav=None)
    assert not e.available()
    assert "no-ref" in e.voice_signature()


def test_xtts_engine_unavailable_without_package(monkeypatch, tmp_path):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"\x00" * 2000)
    monkeypatch.setattr(tts, "_xtts_package_available", lambda: False)
    e = tts._XTTSEngine(reference_wav=wav)
    assert not e.available()


def test_xtts_synthesize_fails_without_model(monkeypatch, tmp_path):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"\x00" * 2000)
    e = tts._XTTSEngine(reference_wav=wav)
    monkeypatch.setattr(e, "_get_model", lambda: None)
    ok = e.synthesize_wav("x", tmp_path / "out.wav")
    assert not ok


# ── _select_engine ─────────────────────────────────────────────────

def test_select_engine_off_returns_noop():
    e = tts._select_engine(prefer="off")
    assert e.name == "none"


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak missing")
def test_select_engine_auto_picks_available():
    e = tts._select_engine(prefer="auto")
    # Если только espeak доступен — он и выбирается
    assert e.available()


def test_select_engine_falls_back_to_noop_when_all_unavailable(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    e = tts._select_engine(prefer="auto")
    assert e.name == "none"


# ── _audio_player_cmd ───────────────────────────────────────────────

def test_audio_player_cmd_returns_list_or_none(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which",
                        lambda x: "/usr/bin/" + x if x == "paplay" else None)
    cmd = tts._audio_player_cmd()
    assert cmd == ["paplay"]


def test_audio_player_cmd_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    assert tts._audio_player_cmd() is None


def test_audio_raw_player_cmd_returns_list_or_none(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    assert tts._audio_raw_player_cmd() is None


# ── TextToSpeech service ────────────────────────────────────────────

def test_text_to_speech_disabled_when_no_engine(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    svc = tts.TextToSpeech(enabled=True, prefer="auto")
    assert not svc.enabled
    assert svc.engine_name == "none"


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak missing")
def test_text_to_speech_enabled_with_espeak(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    svc = tts.TextToSpeech(enabled=True, prefer="espeak")
    assert svc.enabled
    assert svc.engine_name == "espeak-ng"
    svc.shutdown()


def test_text_to_speech_set_enabled():
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc.set_enabled(False)
    assert not svc.enabled


def test_text_to_speech_set_voice_switches_engine(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    assert svc.engine_name == "none"
    svc.set_voice("off")
    # Без изменений
    assert svc.engine_name == "none"


def test_text_to_speech_say_skips_empty():
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc.say("")  # не должно крашиться
    svc.say("   ")
    svc.shutdown()


def test_text_to_speech_say_skipped_when_disabled():
    svc = tts.TextToSpeech(enabled=False, prefer="off")
    svc.say("привет")
    svc.shutdown()


def test_text_to_speech_say_skipped_for_forbidden_text(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc.say("sudo rm -rf /")
    svc.shutdown()


def test_text_to_speech_shutdown_is_idempotent():
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc.shutdown()
    svc.shutdown()


def test_text_to_speech_interrupt_drains_queue():
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc._queue.put_nowait("a")
    svc._queue.put_nowait("b")
    svc.interrupt()
    assert svc._queue.empty()


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak missing")
@pytest.mark.slow
def test_text_to_speech_prewarm_blocking_creates_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    svc = tts.TextToSpeech(enabled=True, prefer="espeak")
    try:
        svc.prewarm(["Готово", "Ошибка"], blocking=True)
        cache = tts._cache_dir()
        wavs = list(cache.glob("*.wav"))
        assert len(wavs) >= 2
        for w in wavs:
            assert w.stat().st_size > 100
    finally:
        svc.shutdown()


def test_text_to_speech_prewarm_skipped_when_disabled(tmp_path):
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc.prewarm(["x"], blocking=True)
    # не должно создать ничего и не упасть
    svc.shutdown()


@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak missing")
def test_text_to_speech_cache_path_returns_wav_in_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    svc = tts.TextToSpeech(enabled=True, prefer="espeak")
    p = svc._cache_path("привет")
    assert p.suffix == ".wav"
    assert p.is_relative_to(tmp_path)
    svc.shutdown()


def test_text_to_speech_is_enabled_alias():
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    assert svc.is_enabled() == svc.enabled


# ── _find_piper_voice ─────────────────────────────────────

def test_find_piper_voice_prefer_name_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.system_check, "PIPER_VOICE_DIR", tmp_path)
    onnx = tmp_path / "ru_RU-irina-medium.onnx"
    onnx.write_bytes(b"x")
    result = tts._find_piper_voice("ru_RU-irina-medium")
    assert result == onnx


def test_find_piper_voice_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.system_check, "PIPER_VOICE_DIR", tmp_path)
    default = tmp_path / "default.onnx"
    default.write_bytes(b"x")
    monkeypatch.setattr(tts.system_check, "PIPER_VOICE_FILE", default)
    result = tts._find_piper_voice(None)
    assert result == default


def test_find_piper_voice_finds_any_medium(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.system_check, "PIPER_VOICE_DIR", tmp_path)
    monkeypatch.setattr(tts.system_check, "PIPER_VOICE_FILE",
                        tmp_path / "missing.onnx")
    alt = tmp_path / "ru_RU-someone-medium.onnx"
    alt.write_bytes(b"x")
    result = tts._find_piper_voice(None)
    assert result == alt


def test_find_piper_voice_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.system_check, "PIPER_VOICE_DIR", tmp_path)
    monkeypatch.setattr(tts.system_check, "PIPER_VOICE_FILE",
                        tmp_path / "missing.onnx")
    assert tts._find_piper_voice(None) is None


# ── _audio_player_cmd / _audio_raw_player_cmd ─────────────

def test_audio_player_cmd_pwplay(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which",
                        lambda x: "/usr/bin/pw-play" if x == "pw-play" else None)
    assert tts._audio_player_cmd() == ["pw-play"]


def test_audio_player_cmd_aplay(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which",
                        lambda x: "/usr/bin/aplay" if x == "aplay" else None)
    cmd = tts._audio_player_cmd()
    assert cmd[0] == "aplay"


def test_audio_raw_player_cmd_pwplay(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which",
                        lambda x: "/usr/bin/pw-play" if x == "pw-play" else None)
    cmd = tts._audio_raw_player_cmd()
    assert cmd is not None
    assert "pw-play" in cmd[0]


# ── _play_wav ─────────────────────────────────────────────

def test_play_wav_no_player_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    # Без плеера — функция должна тихо вернуться
    tts._play_wav(tmp_path / "x.wav")


def test_play_wav_subprocess_error_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.shutil, "which", lambda x: "/usr/bin/paplay")
    import subprocess
    def boom(*a, **kw):
        raise OSError("crashed")
    monkeypatch.setattr(subprocess, "run", boom)
    tts._play_wav(tmp_path / "x.wav")  # не должно крашиться


def test_play_wav_handles_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.shutil, "which", lambda x: "/usr/bin/paplay")
    import subprocess
    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="paplay", timeout=30)
    monkeypatch.setattr(subprocess, "run", slow)
    tts._play_wav(tmp_path / "x.wav")


# ── _select_engine variations ─────────────────────────────

def test_select_engine_piper_fallthrough(monkeypatch):
    # piper выбран, но piper и xtts недоступны → fallback на espeak
    monkeypatch.setattr(tts.shutil, "which",
                        lambda x: "/usr/bin/" + x if "espeak" in x else None)
    e = tts._select_engine(prefer="piper")
    assert e.name in ("espeak-ng", "none")


# ── TextToSpeech _loop через реальный say + espeak ────────

@pytest.mark.skipif(not HAS_ESPEAK, reason="espeak missing")
@pytest.mark.slow
def test_text_to_speech_say_creates_cache_via_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Подменяем _play_wav чтобы не открывать аудио в тестах
    monkeypatch.setattr(tts, "_play_wav", lambda p: None)
    svc = tts.TextToSpeech(enabled=True, prefer="espeak")
    svc.say("уникальная фраза для теста")
    # Ждём, пока worker обработает фразу
    for _ in range(50):
        cache = tts._cache_dir()
        if list(cache.glob("*.wav")):
            break
        time.sleep(0.1)
    svc.shutdown()
    cache = tts._cache_dir()
    wavs = list(cache.glob("*.wav"))
    assert wavs, "say не создал WAV в кэше"


def test_text_to_speech_set_voice_to_off(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc._prefer = "espeak"  # имитация другого prefer
    svc.set_voice("off")
    assert svc.engine_name == "none"


def test_text_to_speech_set_voice_handles_stop_exception(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda x: None)
    svc = tts.TextToSpeech(enabled=True, prefer="off")
    svc._engine.stop = MagicMock(side_effect=RuntimeError("boom"))
    svc.set_voice("piper")  # не должно крашиться


# ── _XTTSEngine extra ─────────────────────────────────────

def test_xtts_reference_wav_returns_none_when_missing(monkeypatch):
    from akali import paths
    monkeypatch.setattr(paths, "XTTS_REFERENCE_WAV",
                        Path("/nonexistent-xtts-ref.wav"))
    assert tts._xtts_reference_wav() is None


def test_xtts_package_available_returns_bool():
    result = tts._xtts_package_available()
    assert isinstance(result, bool)


def test_xtts_get_model_returns_none_without_package(monkeypatch, tmp_path):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"\x00" * 2000)
    monkeypatch.setattr(tts, "_xtts_package_available", lambda: True)
    # Сбросим глобальный _XTTS_MODEL
    tts._XTTS_MODEL = None
    import builtins
    orig = builtins.__import__
    def bad(name, *a, **kw):
        if name == "TTS.api" or name.startswith("TTS"):
            raise ImportError("no TTS")
        return orig(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", bad)
    e = tts._XTTSEngine(reference_wav=wav)
    model = e._get_model()
    assert model is None


def test_xtts_synthesize_returns_false_without_reference(tmp_path):
    e = tts._XTTSEngine(reference_wav=None)
    assert not e.synthesize_wav("hi", tmp_path / "out.wav")


# ── _PiperEngine extra ────────────────────────────────────

def test_piper_voice_signature_no_voice():
    e = tts._PiperEngine()
    e.voice = None
    sig = e.voice_signature()
    assert "no-voice" in sig


def test_piper_voice_signature_with_voice(tmp_path):
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"x")
    e = tts._PiperEngine()
    e.voice = voice
    sig = e.voice_signature()
    assert "piper:" in sig


def test_piper_synthesize_handles_subprocess_error(monkeypatch, tmp_path):
    e = tts._PiperEngine()
    e.voice = tmp_path / "v.onnx"
    e.voice.write_bytes(b"x")
    import subprocess
    def boom(*a, **kw):
        raise OSError("piper crashed")
    monkeypatch.setattr(subprocess, "run", boom)
    assert not e.synthesize_wav("x", tmp_path / "y.wav")


def test_piper_synthesize_handles_low_returncode(monkeypatch, tmp_path):
    e = tts._PiperEngine()
    e.voice = tmp_path / "v.onnx"
    e.voice.write_bytes(b"x")
    import subprocess
    fake = subprocess.CompletedProcess(args=[], returncode=1,
                                        stdout=b"", stderr=b"fail")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert not e.synthesize_wav("x", tmp_path / "y.wav")
