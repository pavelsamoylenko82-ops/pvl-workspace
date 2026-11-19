from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Ошибка выполнения Git-команды."""


def _run_git(
    workspace: Path,
    *args: str,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("LC_ALL", "C.UTF-8")

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"Не удалось выполнить git {' '.join(args)}: {exc}") from exc

    if check and result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise GitError(f"git {' '.join(args)} завершился с ошибкой: {details}")
    return result


def pull_rebase(workspace: Path) -> str:
    """Синхронизация с удалённым репозиторием без merge-коммитов."""
    result = _run_git(workspace, "pull", "--rebase", check=False)
    if result.returncode != 0:
        # Если rebase завис из-за конфликта, не оставляем репозиторий в грязном состоянии.
        _run_git(workspace, "rebase", "--abort", check=False)
        details = (result.stderr or result.stdout).strip()
        raise GitError(f"git pull --rebase не выполнен: {details}")
    return (result.stdout or result.stderr).strip()


def commit_and_push(workspace: Path, commit_message: str) -> bool:
    """Коммитит все изменения и пушит. Перед push всегда делает pull --rebase."""
    _run_git(workspace, "add", "-A")

    diff = _run_git(workspace, "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        return False
    if diff.returncode != 1:
        details = (diff.stderr or diff.stdout).strip()
        raise GitError(f"git diff --cached завершился с ошибкой: {details}")

    _run_git(workspace, "commit", "-m", commit_message)

    # Жёсткое правило: перед push всегда подтягиваем удалённые изменения.
    pull_rebase(workspace)
    _run_git(workspace, "push")
    return True
