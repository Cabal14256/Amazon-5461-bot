"""Stage-7 repair worktree management (blueprint §17, stage-7 plan §7.2).

Red lines enforced here:

- Git *mutations* only ever target the repair worktree and its own
  ``codex/repair-*`` branch (worktree add/remove, baseline + patch commits).
  The production tree is only ever read (``rev-parse``/``diff``/``status``);
  it is never committed, checked out, reset, or otherwise modified.
- The worktree baseline is the production HEAD commit plus the production
  tree's **uncommitted tracked diff** (``git diff --cached`` then
  ``git diff``) applied and committed as the branch's first commit
  (stage-7 plan Day-0 decision 2).  Untracked files — including every
  private directory (``.env``, ``runtime/``, ``data/``, ``brand_packs/``) —
  never enter the worktree because only tracked content is overlaid.
- The redacted evidence copy lives in the worktree-local, untracked
  ``.repair-evidence/`` directory, which is pathspec-excluded from every
  ``git add`` so it can never leak into a commit or the patch diff.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from src.db import list_patch_jobs_past_retention
from src.incidents.evidence_bundle import copy_sanitized_evidence_bundle
from src.windows_subprocess import no_window_kwargs

logger = logging.getLogger(__name__)

BRANCH_PREFIX = "codex/repair-"
WORKTREE_PREFIX = "repair-"
EVIDENCE_DIRNAME = ".repair-evidence"
_GIT_TIMEOUT_SEC = 120
_GIT_AUTHOR = ("Amazon5461 Repair Bot", "codex-repair@localhost")


class WorktreeError(RuntimeError):
    """A git operation on the repair worktree/branch failed."""


@dataclass
class WorktreeInfo:
    path: Path
    branch: str
    baseline_sha: str


def _git(cwd: Path, args: list[str], *, input_text: str | None = None) -> str:
    """Run one git command; raise WorktreeError on non-zero exit."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
        **no_window_kwargs(),
    )
    if proc.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _git_ok(cwd: Path, args: list[str]) -> bool:
    try:
        _git(cwd, args)
        return True
    except (WorktreeError, subprocess.TimeoutExpired):
        return False


def _commit(wt_path: Path, message: str) -> None:
    name, email = _GIT_AUTHOR
    _git(
        wt_path,
        ["-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", message],
    )


def branch_name_for(incident: dict) -> str:
    short_signature = (str(incident.get("signature") or "").strip() or "nosig")[:8]
    return f"{BRANCH_PREFIX}{int(incident['id'])}-{short_signature}"


def _worktree_path_for_branch(repo_root: Path, branch: str) -> Path | None:
    """The worktree currently checking out ``branch``, if any."""
    try:
        listing = _git(repo_root, ["worktree", "list", "--porcelain"])
    except WorktreeError:
        return None
    current_path: Path | None = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):].strip())
        elif line == f"branch refs/heads/{branch}" and current_path is not None:
            return current_path
    return None


def create_repair_worktree(settings, *, job_id: int, incident: dict) -> WorktreeInfo:
    """Create the isolated worktree and commit the baseline overlay.

    On any failure the half-created worktree (and its branch) is removed and
    the error propagates — the caller closes the job row as failed.
    """
    repo_root = Path(settings.repo_root).resolve()
    worktree_root = Path(settings.codex_worktree_root).resolve()
    worktree_root.mkdir(parents=True, exist_ok=True)
    branch = branch_name_for(incident)
    wt_path = worktree_root / f"{WORKTREE_PREFIX}{int(job_id)}"

    # Production tree: read-only snapshots only.
    head_sha = _git(repo_root, ["rev-parse", "HEAD"]).strip()
    staged_diff = _git(repo_root, ["diff", "--cached", "--binary"])
    unstaged_diff = _git(repo_root, ["diff", "--binary"])

    if wt_path.exists():
        raise WorktreeError(f"worktree path already exists: {wt_path}")
    # A stale branch from an earlier failed attempt is inside our own
    # namespace; its orphaned repair worktree is removed first so the branch
    # can be deleted (git refuses to drop a branch checked out elsewhere).
    if _git_ok(repo_root, ["show-ref", "--verify", f"refs/heads/{branch}"]):
        stale_wt = _worktree_path_for_branch(repo_root, branch)
        if stale_wt is not None:
            if stale_wt.name.startswith(WORKTREE_PREFIX):
                _git(repo_root, ["worktree", "remove", "--force", str(stale_wt)])
            else:
                raise WorktreeError(f"branch {branch} checked out by foreign worktree {stale_wt}")
        _git(repo_root, ["branch", "-D", branch])

    _git(repo_root, ["worktree", "add", str(wt_path), "-b", branch, "HEAD"])
    try:
        # Overlay order matters: cached diff is HEAD->index, plain diff is
        # index->worktree; applying both reproduces the production worktree.
        if staged_diff.strip():
            _git(wt_path, ["apply", "--whitespace=nowarn"], input_text=staged_diff)
        if unstaged_diff.strip():
            _git(wt_path, ["apply", "--whitespace=nowarn"], input_text=unstaged_diff)
        if staged_diff.strip() or unstaged_diff.strip():
            _git(wt_path, ["add", "-A"])
            _commit(wt_path, f"baseline: uncommitted production state at {head_sha}")
        status = _git(wt_path, ["status", "--porcelain"])
        if status.strip():
            raise WorktreeError("baseline overlay left a dirty worktree")
        baseline_sha = _git(wt_path, ["rev-parse", "HEAD"]).strip()
    except Exception:
        remove_repair_worktree(settings, worktree_path=wt_path, branch=branch)
        raise
    return WorktreeInfo(path=wt_path, branch=branch, baseline_sha=baseline_sha)


def copy_evidence_into_worktree(incident: dict, wt_path: Path) -> list[str]:
    """Copy the redacted evidence bundle into ``<wt>/.repair-evidence/``.

    The bundle is already redacted at capture time; image payloads stay
    withheld.  Returns repo-relative (posix) paths for the prompt.  The
    directory is untracked and pathspec-excluded from every ``git add``.
    """
    raw = str(incident.get("evidence_bundle_path") or "").strip()
    if not raw:
        return []
    bundle_dir = Path(raw)
    if not bundle_dir.is_dir():
        return []
    dest_dir = Path(wt_path) / EVIDENCE_DIRNAME
    copied = copy_sanitized_evidence_bundle(
        bundle_dir,
        dest_dir,
        incident=incident,
    )
    return [f"{EVIDENCE_DIRNAME}/{name}" for name in copied]


def stage_patch_changes(wt_path: Path, baseline_sha: str) -> list[str]:
    """Stage Codex's changes (never ``.repair-evidence/``) and list them.

    Returns the changed file list (repo-relative posix paths) versus the
    baseline commit — robust even if the agent committed on the branch.
    """
    wt_path = Path(wt_path)
    _git(wt_path, ["add", "-A", "--", ".", f":(exclude){EVIDENCE_DIRNAME}"])
    listing = _git(wt_path, ["diff", "--cached", "--name-only", str(baseline_sha)])
    return [line.strip() for line in listing.splitlines() if line.strip()]


def staged_diff_against(wt_path: Path, baseline_sha: str) -> str:
    """Full diff (index vs the baseline commit) — the candidate patch."""
    return _git(Path(wt_path), ["diff", "--cached", "--binary", str(baseline_sha)])


def commit_patch(wt_path: Path, incident_id: int) -> str:
    """Commit the staged patch as the branch's second commit; return its SHA.

    If the agent already committed its own work on the repair branch the
    index is clean — no empty commit is created, HEAD is simply recorded.
    """
    wt_path = Path(wt_path)
    if not _git_ok(wt_path, ["diff", "--cached", "--quiet"]):
        _commit(wt_path, f"codex: repair incident {int(incident_id)}")
    return _git(wt_path, ["rev-parse", "HEAD"]).strip()


def remove_repair_worktree(settings, *, worktree_path: Path,
                           branch: str | None = None) -> None:
    """Best-effort ``git worktree remove`` (+ optional branch delete).

    Only ever touches ``repair-*`` worktrees and ``codex/repair-*`` branches.
    """
    repo_root = Path(settings.repo_root).resolve()
    wt_path = Path(worktree_path)
    if wt_path.name.startswith(WORKTREE_PREFIX):
        _git_ok(repo_root, ["worktree", "remove", "--force", str(wt_path)])
    else:
        logger.warning("[repair] 拒绝清理非 repair worktree 路径: %s", wt_path)
    _git_ok(repo_root, ["worktree", "prune"])
    if branch and branch.startswith(BRANCH_PREFIX):
        _git_ok(repo_root, ["branch", "-D", branch])


def cleanup_expired_worktrees(settings, *, now: datetime | None = None) -> list[int]:
    """Remove worktrees of terminal patch jobs past the retention window.

    Active jobs (running / patch_ready / validating) are never touched — the
    retention window only starts at ``finished_at`` (blueprint §17.4).
    Returns the job ids whose worktree was removed.
    """
    moment = now or datetime.now()
    cutoff = (moment - timedelta(days=int(settings.codex_worktree_retention_days))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    jobs = list_patch_jobs_past_retention(str(settings.db_path), cutoff)
    removed: list[int] = []
    for job in jobs:
        raw_path = str(job.get("worktree_path") or "").strip()
        if not raw_path:
            continue
        try:
            remove_repair_worktree(settings, worktree_path=Path(raw_path))
            removed.append(int(job["id"]))
        except Exception:  # noqa: BLE001 — cleanup must never break the caller
            logger.exception("[repair] worktree 清理失败 job=%s", job["id"])
    _git_ok(Path(settings.repo_root).resolve(), ["worktree", "prune"])
    return removed
