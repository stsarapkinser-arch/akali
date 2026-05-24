"""Tests for akali.core.updater — git pull/fetch on real tmp repos."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from akali.core import updater


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True)


def test_check_repo_on_clean_git_repo(tmp_git_repo):
    ok, msg = updater.check_repo(str(tmp_git_repo))
    assert ok, msg
    assert msg == ""


def test_check_repo_on_dirty_tree(tmp_git_repo):
    (tmp_git_repo / "a.py").write_text("x = 2\n")
    ok, msg = updater.check_repo(str(tmp_git_repo))
    assert not ok
    assert "локальные" in msg.lower() or "несохранён" in msg.lower()


def test_check_repo_on_non_git_dir(tmp_path):
    ok, msg = updater.check_repo(str(tmp_path))
    assert not ok
    assert "git" in msg.lower()


def test_check_repo_on_missing_dir():
    ok, msg = updater.check_repo("/nonexistent/path/zzz/123")
    assert not ok


def test_get_version_info_basic(tmp_git_repo):
    info = updater.get_version_info(str(tmp_git_repo))
    assert isinstance(info, updater.VersionInfo)
    assert info.current_sha
    assert len(info.short_sha) == 8
    assert info.branch == "main"
    assert "init" in info.last_commit_msg


def test_get_version_info_with_updates(tmp_git_repo_with_remote):
    local, _ = tmp_git_repo_with_remote
    info = updater.get_version_info(str(local))
    assert info.error == "" or "fetch" in info.error.lower()
    if not info.error:
        assert info.has_updates
        assert info.commits_behind >= 1
        assert any("second" in msg for _, msg in info.remote_commits)


def test_get_version_info_non_git(tmp_path):
    info = updater.get_version_info(str(tmp_path))
    assert info.error
    assert "git" in info.error.lower()


def test_pull_no_changes_returns_already_up_to_date(tmp_git_repo_with_remote):
    local, _ = tmp_git_repo_with_remote
    r1 = updater.pull(str(local))
    assert r1.ok, r1.error
    # Второй раз — уже актуально
    r2 = updater.pull(str(local))
    assert r2.ok
    assert r2.already_up_to_date


def test_pull_brings_remote_commits(tmp_git_repo_with_remote):
    local, _ = tmp_git_repo_with_remote
    r = updater.pull(str(local))
    assert r.ok, r.error
    assert not r.already_up_to_date
    assert len(r.pulled_commits) >= 1
    assert any("second" in msg for _, msg in r.pulled_commits)
    assert "b.py" in r.changed_files


def test_pull_refuses_dirty_tree_without_force(tmp_git_repo_with_remote):
    local, _ = tmp_git_repo_with_remote
    (local / "a.py").write_text("dirty\n")
    r = updater.pull(str(local), force=False)
    assert not r.ok
    assert "локальные" in r.error.lower() or "stash" in r.error.lower()


def test_pull_force_stashes_and_pulls(tmp_git_repo_with_remote):
    local, _ = tmp_git_repo_with_remote
    (local / "a.py").write_text("dirty\n")
    r = updater.pull(str(local), force=True)
    assert r.ok, r.error
    assert r.stashed
    # проверим, что stash действительно создался
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=local,
        capture_output=True, text=True, check=True,
    )
    assert "akali-auto-" in stash_list.stdout


def test_pull_force_reset_hard_when_diverged(tmp_git_repo_with_remote):
    local, remote = tmp_git_repo_with_remote
    # Создаём расходящийся коммит в local (отличный от remote)
    (local / "a.py").write_text("local divergence\n")
    _git(local, "config", "user.email", "u@u")
    _git(local, "config", "user.name", "user")
    _git(local, "config", "commit.gpgsign", "false")
    _git(local, "add", ".")
    _git(local, "commit", "-qm", "local-only")
    r = updater.pull(str(local), force=True)
    assert r.ok, r.error
    assert r.forced_reset


def test_pull_non_git_dir(tmp_path):
    r = updater.pull(str(tmp_path))
    assert not r.ok
    assert "git" in r.error.lower()


def test_pull_missing_dir():
    r = updater.pull("/nonexistent/zzz/abc")
    assert not r.ok
    assert "не существует" in r.error or "directory" in r.error.lower()


def test_pull_needs_restart_for_py_changes(tmp_git_repo_with_remote):
    local, _ = tmp_git_repo_with_remote
    r = updater.pull(str(local))
    # b.py был добавлен в remote
    assert r.needs_restart


def test_pull_no_restart_for_text_changes(tmp_path):
    # Делаем свою пару remote/local с .txt-only коммитом
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "config", "commit.gpgsign", "false")
    (work / "readme.txt").write_text("hello\n")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "init")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "-u", "origin", "main")

    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)],
                   check=True, capture_output=True)
    _git(local, "config", "user.email", "u@u")
    _git(local, "config", "user.name", "u")

    (work / "readme.txt").write_text("hello v2\n")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "doc only")
    _git(work, "push", "-q")

    r = updater.pull(str(local))
    assert r.ok
    assert not r.needs_restart
    assert "readme.txt" in r.changed_files


def test_pull_returns_update_result_on_exception(monkeypatch, tmp_git_repo):
    # Заставим _pull_impl упасть глобально
    def explode(*args, **kwargs):
        raise RuntimeError("boom!")
    monkeypatch.setattr(updater, "_pull_impl", explode)
    r = updater.pull(str(tmp_git_repo))
    assert not r.ok
    assert "внутренняя ошибка" in r.error or "boom" in r.error


def test_version_info_returns_error_on_bad_git(monkeypatch, tmp_path):
    # Симулируем отсутствие git
    (tmp_path / ".git").mkdir()
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("git not installed")
    monkeypatch.setattr(updater, "_run", fake_run)
    info = updater.get_version_info(str(tmp_path))
    assert info.error


def test_update_result_defaults():
    r = updater.UpdateResult()
    assert not r.ok
    assert r.pulled_commits == []
    assert r.changed_files == []
    assert not r.needs_restart
    assert not r.stashed
    assert not r.forced_reset
