from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import __version__

APP_ROOT = Path(__file__).resolve().parent.parent


def _run_git(args: list[str]) -> str | None:
    """Return a short git command result when this install is a git checkout.

    Release ZIP installs usually do not have a .git directory, so all git data is
    best-effort and falls back to env vars or "unknown".
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(APP_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.5,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


@lru_cache(maxsize=1)
def get_build_info() -> dict[str, Any]:
    commit = (
        _env_first("CC2_DASH_GIT_COMMIT", "GITHUB_SHA", "SOURCE_COMMIT")
        or _run_git(["rev-parse", "--short=12", "HEAD"])
        or "unknown"
    )
    if len(commit) > 12 and commit != "unknown":
        commit_short = commit[:12]
    else:
        commit_short = commit

    branch = (
        _env_first("CC2_DASH_GIT_BRANCH", "GITHUB_REF_NAME", "SOURCE_BRANCH")
        or _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        or "unknown"
    )
    repo = _env_first("CC2_DASH_REPO", "GITHUB_REPOSITORY") or _run_git(["config", "--get", "remote.origin.url"]) or ""

    dirty = False
    if (APP_ROOT / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "diff", "--quiet"],
                cwd=str(APP_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                check=False,
            )
            dirty = proc.returncode == 1
        except Exception:
            dirty = False

    return {
        "app": "cc2-dash-lite",
        "version": __version__,
        "git_commit": commit,
        "git_commit_short": commit_short,
        "git_branch": branch,
        "git_dirty": dirty,
        "repo": repo,
        "source": "git" if commit != "unknown" else "archive",
    }
