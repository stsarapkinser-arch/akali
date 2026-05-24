"""Общие фикстуры для всего pytest-набора Akali.

Содержит:
- offscreen Qt платформу
- session-фикстуру `qapp` (QCoreApplication для тестов QObject/QThread)
- автоматическое провижнинг Vosk-модели (скачивает в repo/model/ если её нет)
- фикстуры путей и временных файлов (commands.txt, git-репо)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

FIXTURES = HERE / "fixtures"
AUDIO_FIXTURES = FIXTURES / "audio"
DESKTOP_FIXTURES = FIXTURES / "desktop"

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
VOSK_DIR = ROOT / "model"


def _ensure_vosk_model() -> None:
    """Скачивает small-ru Vosk model один раз в repo/model/."""
    if (VOSK_DIR / "am" / "final.mdl").exists():
        return
    zip_path = ROOT / "_vosk.zip"
    print(f"\n[conftest] Downloading Vosk model → {VOSK_DIR} (~50 MB)…")
    urllib.request.urlretrieve(VOSK_URL, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(ROOT)
    extracted = ROOT / "vosk-model-small-ru-0.22"
    if extracted.exists():
        if VOSK_DIR.exists():
            shutil.rmtree(VOSK_DIR, ignore_errors=True)
        extracted.rename(VOSK_DIR)
    zip_path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def vosk_model_dir() -> Path:
    """Гарантирует, что Vosk-модель есть, и возвращает путь к ней."""
    _ensure_vosk_model()
    return VOSK_DIR


@pytest.fixture(scope="session")
def qapp():
    """QApplication (offscreen) для тестов QObject/QWidget/сигналов без GUI.

    QApplication (а не QCoreApplication) нужна, потому что AkaliApp создаёт
    MainWindow/TrayController, а они требуют QApplication. offscreen
    platform держит весь GUI в памяти без X/Wayland.
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["test"])
    yield app


@pytest.fixture
def tmp_commands(tmp_path: Path) -> Path:
    """Копия реального commands.txt в tmp."""
    dst = tmp_path / "commands.txt"
    shutil.copyfile(ROOT / "commands.txt", dst)
    return dst


@pytest.fixture
def min_commands(tmp_path: Path) -> Path:
    """Минимальная commands.txt из 3 записей."""
    p = tmp_path / "commands.txt"
    p.write_text(
        "# minimal fixture\n"
        "ls -la -> покажи файлы, листинг каталога\n"
        "date -> текущее время, какая дата сегодня\n"
        "firefox & -> открой браузер, запусти файрфокс\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def empty_auto_json(tmp_path: Path) -> Path:
    """Несуществующий путь к auto_commands.json."""
    return tmp_path / "auto_commands.json"


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=repo, check=True,
        capture_output=True, text=True,
    )
    return res.stdout


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Локальное git-репо с одним коммитом."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "test")
    _git(r, "config", "commit.gpgsign", "false")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def tmp_git_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Pair: (local_repo, remote_bare). Local clone with one extra commit on remote."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "test")
    _git(work, "config", "commit.gpgsign", "false")
    (work / "a.py").write_text("x = 1\n")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "init")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "-u", "origin", "main")

    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)],
                   check=True, capture_output=True)
    _git(local, "config", "user.email", "u@u")
    _git(local, "config", "user.name", "user")
    _git(local, "config", "commit.gpgsign", "false")

    # second commit on remote (via work)
    (work / "b.py").write_text("y = 2\n")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "second")
    _git(work, "push", "-q")

    return local, remote


@pytest.fixture(autouse=True)
def _isolate_user_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Изолирует HOME/XDG_CACHE_HOME чтобы тесты не писали в реальный ~/.cache."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    yield
