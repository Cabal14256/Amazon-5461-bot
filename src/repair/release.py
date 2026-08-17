"""Auditable local merge and git-revert operations for Stage 8."""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.windows_subprocess import no_window_kwargs

_GIT_AUTHOR = ("Amazon5461 Release Bot", "codex-release@localhost")


class ReleaseConflict(RuntimeError):
    """A safe release/revert precondition failed (mapped to HTTP 409)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
        **no_window_kwargs(),
    )
    if check and proc.returncode != 0:
        raise ReleaseConflict("git_operation_failed")
    return proc


def _sha(repo: Path, revision: str = "HEAD") -> str:
    return _git(repo, "rev-parse", revision).stdout.strip()


def _require_clean(repo: Path) -> None:
    if _git(repo, "status", "--porcelain").stdout.strip():
        raise ReleaseConflict("dirty_worktree")


def merge_repair_branch(settings, job: dict, *, expected_patch_sha: str) -> dict:
    """Merge one reviewed repair branch locally with a no-ff audit commit."""
    repo = Path(settings.repo_root).resolve()
    _require_clean(repo)
    current_branch = _git(
        repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
    ).stdout.strip()
    if not current_branch or current_branch.startswith("codex/repair-"):
        raise ReleaseConflict("invalid_release_target_branch")
    baseline = str(job.get("baseline_sha") or "")
    patch_sha = str(job.get("patch_sha") or "")
    branch = str(job.get("branch_name") or "")
    if not baseline or _sha(repo) != baseline:
        raise ReleaseConflict("baseline_head_mismatch")
    if not patch_sha or patch_sha != str(expected_patch_sha):
        raise ReleaseConflict("stale_patch_sha")
    if not branch.startswith("codex/repair-"):
        raise ReleaseConflict("invalid_repair_branch")
    try:
        branch_sha = _sha(repo, branch)
    except ReleaseConflict as exc:
        raise ReleaseConflict("repair_branch_missing") from exc
    if branch_sha != patch_sha:
        raise ReleaseConflict("repair_branch_head_mismatch")
    ancestry = _git(
        repo, "merge-base", "--is-ancestor", baseline, patch_sha, check=False
    )
    if ancestry.returncode != 0:
        raise ReleaseConflict("patch_not_based_on_baseline")
    name, email = _GIT_AUTHOR
    proc = _git(
        repo,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "merge",
        "--no-ff",
        "--no-edit",
        branch,
        check=False,
    )
    if proc.returncode != 0:
        _git(repo, "merge", "--abort", check=False)
        raise ReleaseConflict("merge_failed")
    return {"pre_release_sha": baseline, "release_sha": _sha(repo)}


def verify_release_head(settings, job: dict) -> None:
    repo = Path(settings.repo_root).resolve()
    _require_clean(repo)
    if _sha(repo) != str(job.get("release_sha") or ""):
        raise ReleaseConflict("release_head_mismatch")


def revert_release(settings, job: dict) -> str:
    """Create an auditable revert commit; never reset or rewrite history."""
    repo = Path(settings.repo_root).resolve()
    _require_clean(repo)
    release_sha = str(job.get("release_sha") or "")
    if not release_sha or _sha(repo) != release_sha:
        raise ReleaseConflict("release_head_mismatch")
    parents = _git(repo, "rev-list", "--parents", "-n", "1", release_sha).stdout.split()
    if not parents or parents[0] != release_sha:
        raise ReleaseConflict("release_commit_missing")
    name, email = _GIT_AUTHOR
    args = [
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "revert",
    ]
    if len(parents) > 2:
        args += ["-m", "1"]
    args += ["--no-edit", release_sha]
    proc = _git(repo, *args, check=False)
    if proc.returncode != 0:
        _git(repo, "revert", "--abort", check=False)
        raise ReleaseConflict("revert_failed")
    return _sha(repo)
