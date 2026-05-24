"""Tests for akali.core.system_check — dependency checks and installers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from akali.core import system_check as sc


# ── CheckResult / SystemReport ─────────────────────────────────────

def test_check_result_dataclass():
    r = sc.CheckResult(name="x", ok=True, message="m")
    assert r.name == "x"
    assert r.ok
    assert r.message == "m"
    assert not r.fixable
    assert not r.auto_safe


def test_system_report_all_ok():
    r = sc.SystemReport(items=[
        sc.CheckResult("a", True),
        sc.CheckResult("b", True),
    ])
    assert r.all_ok


def test_system_report_not_all_ok():
    r = sc.SystemReport(items=[
        sc.CheckResult("a", True),
        sc.CheckResult("b", False),
    ])
    assert not r.all_ok


def test_system_report_counts():
    r = sc.SystemReport(items=[
        sc.CheckResult("a", True),
        sc.CheckResult("b", False, fixable=True, auto_safe=True),
        sc.CheckResult("c", False, fixable=True, auto_safe=False),
        sc.CheckResult("d", False, fixable=False),
    ])
    assert r.fixable_count == 2
    assert r.auto_safe_count == 1


def test_system_report_empty():
    r = sc.SystemReport()
    assert r.all_ok
    assert r.fixable_count == 0
    assert r.auto_safe_count == 0


# ── check_pip_package ──────────────────────────────────────────────

def test_check_pip_existing_package():
    r = sc.check_pip_package("pytest", "pytest")
    assert r.ok
    assert "доступен" in r.message
    assert not r.fixable


def test_check_pip_missing_package():
    r = sc.check_pip_package("definitely_not_a_real_module_xyz_123",
                              "fake-pkg")
    assert not r.ok
    assert r.fixable
    assert r.auto_safe


def test_check_pip_heavy_package_not_auto_safe():
    r = sc.check_pip_package("definitely_not_a_real_module_xyz_124", "TTS")
    assert not r.ok
    assert r.fixable
    assert not r.auto_safe  # heavy


def test_check_pip_handles_oserror(mocker):
    def bad_import(name):
        raise OSError("PortAudio not found")
    mocker.patch("importlib.import_module", side_effect=bad_import)
    r = sc.check_pip_package("anything", "pkg-x")
    assert not r.ok
    assert not r.fixable
    assert "не загружается" in r.message


# ── check_vosk_model ───────────────────────────────────────────────

def test_check_vosk_model_present(vosk_model_dir):
    r = sc.check_vosk_model()
    assert r.ok
    assert "найдена" in r.message


def test_check_vosk_model_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sc.paths, "DEFAULT_VOSK_MODEL_DIR",
                        tmp_path / "nope")
    r = sc.check_vosk_model()
    assert not r.ok
    assert r.fixable
    assert r.auto_safe


# ── check_ollama_binary ────────────────────────────────────────────

def test_check_ollama_binary_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/ollama" if x == "ollama" else None)
    r = sc.check_ollama_binary()
    assert r.ok


def test_check_ollama_binary_missing(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.check_ollama_binary()
    assert not r.ok
    assert r.fixable


# ── check_ollama_model ─────────────────────────────────────────────

def test_check_ollama_model_no_binary(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.check_ollama_model()
    assert not r.ok
    assert "не установлен" in r.message


def test_check_ollama_model_found(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/ollama" if x == "ollama" else None)
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="NAME\nqwen2.5-coder:1.5b  abc123\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    r = sc.check_ollama_model()
    assert r.ok


def test_check_ollama_model_not_pulled(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/ollama" if x == "ollama" else None)
    fake = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="NAME\n", stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    r = sc.check_ollama_model()
    assert not r.ok
    assert r.fixable


def test_check_ollama_model_subprocess_fails(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/ollama" if x == "ollama" else None)
    def boom(*a, **kw):
        raise OSError("crash")
    monkeypatch.setattr(subprocess, "run", boom)
    r = sc.check_ollama_model()
    assert not r.ok


# ── check_piper_* ──────────────────────────────────────────────────

def test_check_piper_binary_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/piper" if x == "piper" else None)
    r = sc.check_piper_binary()
    assert r.ok


def test_check_piper_binary_missing(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.check_piper_binary()
    assert not r.ok
    assert r.fixable
    assert r.auto_safe


def test_check_piper_voice_present(monkeypatch, tmp_path):
    onnx = tmp_path / "voice.onnx"
    onnx.write_bytes(b"fake")
    jsn = tmp_path / "voice.onnx.json"
    jsn.write_text("{}")
    monkeypatch.setattr(sc, "PIPER_VOICE_FILE", onnx)
    monkeypatch.setattr(sc, "PIPER_VOICE_JSON", jsn)
    monkeypatch.setattr(sc, "PIPER_VOICE_DIR", tmp_path)
    r = sc.check_piper_voice()
    assert r.ok


def test_check_piper_voice_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "PIPER_VOICE_FILE", tmp_path / "missing.onnx")
    monkeypatch.setattr(sc, "PIPER_VOICE_JSON", tmp_path / "missing.onnx.json")
    monkeypatch.setattr(sc, "PIPER_VOICE_DIR", tmp_path)
    r = sc.check_piper_voice()
    assert not r.ok
    assert r.fixable
    assert r.auto_safe


# ── check_xtts_reference ───────────────────────────────────────────

def test_check_xtts_reference_missing_optional(monkeypatch, tmp_path):
    monkeypatch.setattr(sc.paths, "XTTS_REFERENCE_WAV", tmp_path / "missing.wav")
    r = sc.check_xtts_reference()
    # Отсутствие — это ОК (опционально)
    assert r.ok
    assert "опционально" in r.message.lower() or "не загруж" in r.message.lower()


def test_check_xtts_reference_present_without_package(monkeypatch, tmp_path):
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"\x00" * 2000)
    monkeypatch.setattr(sc.paths, "XTTS_REFERENCE_WAV", wav)
    def fake_import(name):
        if name == "TTS":
            raise ImportError("no TTS")
        return __import__(name)
    monkeypatch.setattr(sc.importlib, "import_module", fake_import)
    r = sc.check_xtts_reference()
    assert not r.ok
    assert r.fixable


# ── check_espeak / check_audio_player / check_qdbus / check_curl ──

def test_check_espeak_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/espeak-ng" if "espeak" in x else None)
    r = sc.check_espeak()
    assert r.ok


def test_check_espeak_missing(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    r = sc.check_espeak()
    assert not r.ok


def test_check_audio_player_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/paplay" if x == "paplay" else None)
    r = sc.check_audio_player()
    assert r.ok
    assert "paplay" in r.message or "paplay" in r.name


def test_check_audio_player_missing(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.check_audio_player()
    assert not r.ok
    assert not r.fixable  # не fixable — системная либа


def test_check_qdbus_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/qdbus" if x == "qdbus" else None)
    r = sc.check_qdbus()
    assert r.ok


def test_check_qdbus_missing(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.check_qdbus()
    assert not r.ok


def test_check_curl_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/curl" if x == "curl" else None)
    r = sc.check_curl()
    assert r.ok


def test_check_curl_missing(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.check_curl()
    assert not r.ok


# ── check_gemini ───────────────────────────────────────────────────

def test_check_gemini_no_key():
    r = sc.check_gemini(None)
    assert not r.ok
    assert "ключ" in r.message.lower()


def test_check_gemini_no_genai_package(monkeypatch):
    import builtins
    orig_import = builtins.__import__
    def bad(name, *a, **kw):
        if name in ("google.genai", "google"):
            raise ImportError(name)
        return orig_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", bad)
    # Также вычистим уже импортированный google.genai если был
    monkeypatch.delitem(sys.modules, "google.genai", raising=False)
    monkeypatch.delitem(sys.modules, "google", raising=False)
    r = sc.check_gemini("fake-key")
    assert not r.ok
    assert "google-genai" in r.message or "import" in r.message.lower()


def test_check_gemini_api_rejects_key(mocker):
    fake_genai = mocker.MagicMock()
    client = mocker.MagicMock()
    client.models.generate_content.side_effect = Exception("invalid key")
    fake_genai.Client.return_value = client
    mocker.patch.dict(sys.modules, {"google.genai": fake_genai})
    import builtins
    orig_import = builtins.__import__
    def patched_import(name, *a, **kw):
        if name == "google.genai":
            return fake_genai
        if name == "google":
            return type("g", (), {"genai": fake_genai})()
        return orig_import(name, *a, **kw)
    mocker.patch("builtins.__import__", side_effect=patched_import)
    r = sc.check_gemini("bad-key")
    assert not r.ok


# ── _safe wrapper ──────────────────────────────────────────────────

def test_safe_wrapper_catches_exceptions():
    def boom():
        raise RuntimeError("crash")
    r = sc._safe(boom, "TestCheck")
    assert not r.ok
    assert "проверка упала" in r.message


def test_safe_wrapper_passes_through_success():
    def good():
        return sc.CheckResult("x", True, "fine")
    r = sc._safe(good, "TestCheck")
    assert r.ok
    assert r.message == "fine"


# ── _has_pkexec_gui ───────────────────────────────────────────────

def test_has_pkexec_gui_no_binary(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    monkeypatch.setenv("DISPLAY", ":0")
    assert not sc._has_pkexec_gui()


def test_has_pkexec_gui_no_display(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/pkexec" if x == "pkexec" else None)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert not sc._has_pkexec_gui()


def test_has_pkexec_gui_ok(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/pkexec" if x == "pkexec" else None)
    monkeypatch.setenv("DISPLAY", ":0")
    assert sc._has_pkexec_gui()


# ── _in_venv ──────────────────────────────────────────────────────

def test_in_venv_with_virtual_env_set(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/venv")
    assert sc._in_venv()


# ── _download ─────────────────────────────────────────────────────

def test_download_writes_file(monkeypatch, tmp_path):
    fake_data = b"hello world data" * 100
    class FakeResponse:
        def __init__(self, data):
            self._data = data
            self._pos = 0
            self.headers = {"Content-Length": str(len(data))}
        def read(self, n):
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
            return chunk
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(sc, "urlopen", lambda url, timeout: FakeResponse(fake_data))
    dest = tmp_path / "file.dat"
    ok = sc._download("http://fake/x", dest)
    assert ok
    assert dest.read_bytes() == fake_data


def test_download_handles_url_error(monkeypatch, tmp_path):
    from urllib.error import URLError
    def boom(url, timeout):
        raise URLError("dns")
    monkeypatch.setattr(sc, "urlopen", boom)
    dest = tmp_path / "file.dat"
    ok = sc._download("http://fake/x", dest)
    assert not ok
    assert not dest.exists()


# ── install_pip_package ───────────────────────────────────────────

def test_install_pip_package_success(monkeypatch, mocker):
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    mocker.patch.object(subprocess, "run", return_value=fake)
    r = sc.install_pip_package("pytest")
    assert r.ok


def test_install_pip_package_failure(monkeypatch, mocker):
    fake = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="not found")
    mocker.patch.object(subprocess, "run", return_value=fake)
    r = sc.install_pip_package("nopkg")
    assert not r.ok
    assert "код 1" in r.message


def test_install_pip_package_timeout(mocker):
    mocker.patch.object(subprocess, "run",
                        side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=600))
    r = sc.install_pip_package("slow-pkg")
    assert not r.ok


# ── install_vosk_model ────────────────────────────────────────────

def test_install_vosk_model_already_present(vosk_model_dir):
    r = sc.install_vosk_model()
    assert r.ok
    assert "уже" in r.message


def test_install_vosk_model_download_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(sc.paths, "DEFAULT_VOSK_MODEL_DIR", tmp_path / "missing")
    monkeypatch.setattr(sc, "_download", lambda *a, **kw: False)
    r = sc.install_vosk_model()
    assert not r.ok


# ── install_piper_voice ───────────────────────────────────────────

def test_install_piper_voice_already_present(monkeypatch, tmp_path):
    onnx = tmp_path / "ru_RU-dmitri-medium.onnx"
    onnx.write_bytes(b"fake")
    jsn = tmp_path / "ru_RU-dmitri-medium.onnx.json"
    jsn.write_text("{}")
    monkeypatch.setattr(sc, "PIPER_VOICE_DIR", tmp_path)
    monkeypatch.setattr(sc, "_piper_voice_paths",
                        lambda v=None: (onnx, jsn, "http://x"))
    r = sc.install_piper_voice()
    assert r.ok


def test_install_piper_voice_download_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "PIPER_VOICE_DIR", tmp_path)
    monkeypatch.setattr(sc, "_piper_voice_paths",
                        lambda v=None: (tmp_path / "a.onnx", tmp_path / "a.json", "u"))
    monkeypatch.setattr(sc, "_download", lambda *a, **kw: False)
    r = sc.install_piper_voice()
    assert not r.ok


# ── install_espeak_ng / install_ollama ────────────────────────────

def test_install_espeak_already_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/espeak-ng" if "espeak" in x else None)
    r = sc.install_espeak_ng()
    assert r.ok


def test_install_espeak_no_pkexec(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.install_espeak_ng()
    assert not r.ok


def test_install_ollama_already_present(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which",
                        lambda x: "/usr/bin/ollama" if x == "ollama" else None)
    r = sc.install_ollama()
    assert r.ok


def test_install_ollama_no_curl(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.install_ollama()
    assert not r.ok
    assert "curl" in r.message


# ── pull_ollama_model ──────────────────────────────────────────────

def test_pull_ollama_model_no_binary(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda x: None)
    r = sc.pull_ollama_model()
    assert not r.ok
    assert "не установлен" in r.message


# ── run_all_checks ─────────────────────────────────────────────────

def test_run_all_checks_returns_report():
    report = sc.run_all_checks(gemini_api_key=None)
    assert isinstance(report, sc.SystemReport)
    assert len(report.items) >= 5
    for it in report.items:
        assert isinstance(it, sc.CheckResult)


def test_run_all_checks_with_gemini_key(monkeypatch):
    monkeypatch.setattr(sc, "check_gemini",
                        lambda key: sc.CheckResult("Gemini API", False, "test"))
    report = sc.run_all_checks(gemini_api_key="fake")
    assert any(it.name == "Gemini API" for it in report.items)


# ── install_missing ────────────────────────────────────────────────

def test_install_missing_skips_ok(mocker):
    report = sc.SystemReport(items=[
        sc.CheckResult("Vosk модель", True),
    ])
    mock_install = mocker.patch.object(sc, "install_vosk_model")
    sc.install_missing(report)
    mock_install.assert_not_called()


def test_install_missing_skips_unfixable():
    report = sc.SystemReport(items=[
        sc.CheckResult("Vosk модель", False, fixable=False),
    ])
    results = sc.install_missing(report)
    assert results == []


def test_install_missing_only_auto_safe_skips_non_safe(mocker):
    report = sc.SystemReport(items=[
        sc.CheckResult("Ollama (бинарь)", False, fixable=True, auto_safe=False),
    ])
    mock_install = mocker.patch.object(sc, "install_ollama")
    sc.install_missing(report, only_auto_safe=True)
    mock_install.assert_not_called()


def test_install_missing_calls_installer(mocker):
    report = sc.SystemReport(items=[
        sc.CheckResult("Vosk модель", False, fixable=True, auto_safe=True),
    ])
    mock = mocker.patch.object(sc, "install_vosk_model",
                                return_value=sc.CheckResult("Vosk модель", True))
    sc.install_missing(report)
    mock.assert_called_once()


def test_install_missing_catches_exception(mocker):
    report = sc.SystemReport(items=[
        sc.CheckResult("Vosk модель", False, fixable=True, auto_safe=True),
    ])
    mocker.patch.object(sc, "install_vosk_model",
                       side_effect=RuntimeError("boom"))
    results = sc.install_missing(report)
    assert len(results) == 1
    assert not results[0].ok
    assert "boom" in results[0].message or "упала" in results[0].message


# ── auto_install_safe ──────────────────────────────────────────────

def test_auto_install_safe_returns_report(mocker):
    mocker.patch.object(sc, "install_missing", return_value=[])
    report = sc.auto_install_safe(api_key=None)
    assert isinstance(report, sc.SystemReport)


def test_auto_install_safe_no_safe_to_install_skips(mocker):
    fake_report = sc.SystemReport(items=[sc.CheckResult("x", True)])
    mocker.patch.object(sc, "run_all_checks", return_value=fake_report)
    mock_install = mocker.patch.object(sc, "install_missing")
    report = sc.auto_install_safe()
    mock_install.assert_not_called()
    assert report is fake_report


def test_auto_install_safe_catches_exception(mocker):
    mocker.patch.object(sc, "run_all_checks",
                       side_effect=RuntimeError("boom"))
    report = sc.auto_install_safe()
    assert isinstance(report, sc.SystemReport)
    assert not report.all_ok


# ── print_report ───────────────────────────────────────────────────

def test_print_report_does_not_raise():
    report = sc.SystemReport(items=[
        sc.CheckResult("ok", True),
        sc.CheckResult("fix", False, fixable=True, auto_safe=True),
        sc.CheckResult("bad", False, fixable=False),
    ])
    sc.print_report(report)  # не должно крашиться


def test_print_report_empty():
    sc.print_report(sc.SystemReport())


# ── piper_voice_paths ──────────────────────────────────────────────

def test_piper_voice_paths_default():
    onnx, jsn, url = sc._piper_voice_paths()
    assert onnx.name.endswith(".onnx")
    assert jsn.name.endswith(".onnx.json")
    assert url.startswith("https://huggingface.co/")


def test_piper_voice_paths_custom():
    onnx, jsn, url = sc._piper_voice_paths("ru_RU-irina-medium")
    assert "irina" in onnx.name
    assert "irina" in url
