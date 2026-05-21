"""Обновление кода ассистента из git-репозитория.

Через UI запускается git pull --ff-only в директории проекта. Перед
обновлением проверяем, что:
  1. Директория — это git-репозиторий.
  2. Working tree чистый (нет несохранённых изменений).
  3. Текущая ветка не detached HEAD.

Возвращаем структурированный результат с диффом коммитов и списком
изменённых файлов, чтобы UI мог показать changelog.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field


@dataclass
class UpdateResult:
    ok: bool = False
    error: str = ""
    already_up_to_date: bool = False
    pulled_commits: list[tuple[str, str]] = field(default_factory=list)  # (sha, msg)
    changed_files: list[str] = field(default_factory=list)
    needs_restart: bool = False
    raw_output: str = ""


def _run(args: list[str], cwd: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def check_repo(repo_dir: str) -> tuple[bool, str]:
    """Проверяет, что repo_dir — рабочая копия git без локальных правок."""
    if not os.path.isdir(repo_dir):
        return False, f"Каталог не существует: {repo_dir}"
    git_dir = os.path.join(repo_dir, ".git")
    if not os.path.exists(git_dir):
        return False, f"Это не git-репозиторий: {repo_dir}"
    try:
        st = _run(["git", "status", "--porcelain"], repo_dir, timeout=10)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        return False, f"git недоступен: {e}"
    if st.returncode != 0:
        return False, f"git status вернул код {st.returncode}: {st.stderr.strip()}"
    if st.stdout.strip():
        return False, "Есть локальные несохранённые изменения. Закоммить или спрячь их перед обновлением."
    return True, ""


def pull(repo_dir: str, remote: str = "origin", branch: str | None = None) -> UpdateResult:
    """Выполняет git pull --ff-only и формирует структурированный результат."""
    result = UpdateResult()

    ok, err = check_repo(repo_dir)
    if not ok:
        result.error = err
        return result

    # Текущая ветка
    try:
        head_proc = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_dir, timeout=5)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        result.error = f"git rev-parse: {e}"
        return result
    cur_branch = head_proc.stdout.strip()
    if not cur_branch or cur_branch == "HEAD":
        result.error = "HEAD в detached-состоянии — переключись на ветку перед обновлением."
        return result
    branch = branch or cur_branch

    # Запоминаем sha до пула
    try:
        before_proc = _run(["git", "rev-parse", "HEAD"], repo_dir, timeout=5)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        result.error = f"git rev-parse HEAD: {e}"
        return result
    before_sha = before_proc.stdout.strip()

    # Fetch
    try:
        fetch_proc = _run(["git", "fetch", "--prune", remote], repo_dir, timeout=60)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        result.error = f"git fetch: {e}"
        return result
    if fetch_proc.returncode != 0:
        result.error = f"git fetch вернул код {fetch_proc.returncode}: {fetch_proc.stderr.strip()[:300]}"
        return result

    # Pull --ff-only
    try:
        pull_proc = _run(["git", "pull", "--ff-only", remote, branch], repo_dir, timeout=60)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        result.error = f"git pull: {e}"
        return result
    result.raw_output = pull_proc.stdout.strip() or pull_proc.stderr.strip()
    if pull_proc.returncode != 0:
        result.error = (
            f"git pull вернул код {pull_proc.returncode}: "
            f"{(pull_proc.stderr or pull_proc.stdout).strip()[:300]}")
        return result

    # Sha после
    try:
        after_proc = _run(["git", "rev-parse", "HEAD"], repo_dir, timeout=5)
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        result.error = f"git rev-parse HEAD после pull: {e}"
        return result
    after_sha = after_proc.stdout.strip()

    if before_sha == after_sha:
        result.ok = True
        result.already_up_to_date = True
        return result

    # Список новых коммитов
    try:
        log_proc = _run(
            ["git", "log", "--pretty=format:%h%x09%s", f"{before_sha}..{after_sha}"],
            repo_dir, timeout=10)
        if log_proc.returncode == 0:
            for line in log_proc.stdout.splitlines():
                sha, _, msg = line.partition("\t")
                if sha:
                    result.pulled_commits.append((sha, msg.strip()))
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass

    # Изменённые файлы
    try:
        diff_proc = _run(
            ["git", "diff", "--name-only", f"{before_sha}..{after_sha}"],
            repo_dir, timeout=10)
        if diff_proc.returncode == 0:
            result.changed_files = [
                f.strip() for f in diff_proc.stdout.splitlines() if f.strip()
            ]
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass

    # Если изменились .py / .qss / .json — рестарт нужен
    restart_exts = (".py", ".qss")
    result.needs_restart = any(f.endswith(restart_exts) for f in result.changed_files)
    result.ok = True
    return result
