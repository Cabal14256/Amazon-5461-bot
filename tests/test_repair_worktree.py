"""Stage-7 repair worktree management (src/repair/worktree.py).

Every test runs against a throwaway git repository under ``tmp_path`` — the
production repository is never touched.  The fixture repo deliberately
contains untracked private-looking files (``runtime/private/``, ``.env``,
``data/``, ``brand_packs/``); tests assert they never enter a worktree.
"""

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import (  # noqa: E402
    create_patch_job,
    finish_repair_job,
    get_repair_job,
    init_db,
    set_repair_job_worktree,
)
from src.repair.worktree import (  # noqa: E402
    EVIDENCE_DIRNAME,
    WorktreeError,
    branch_name_for,
    cleanup_expired_worktrees,
    commit_patch,
    copy_evidence_into_worktree,
    create_repair_worktree,
    stage_patch_changes,
)
from src.web.config import WebSettings  # noqa: E402

INCIDENT = {"id": 42, "signature": "abc123def4567890"}


def git(cwd, *args) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )
    return proc.stdout


def make_git_repo(path: Path) -> Path:
    """Fixture repo: tracked source + untracked private-looking content."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test Fixture")
    (path / "config" / "selectors").mkdir(parents=True)
    (path / "config" / "selectors" / "us.yaml").write_text(
        "submit_button: '#submit-old'\n", encoding="utf-8"
    )
    (path / "src" / "executor").mkdir(parents=True)
    (path / "src" / "executor" / "probe.py").write_text(
        "def probe():\n    return 1\n", encoding="utf-8"
    )
    (path / "src" / "capture").mkdir(parents=True)
    (path / "tests").mkdir(exist_ok=True)
    (path / "tests" / "test_probe.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    # Untracked private-looking content — must never enter a worktree.
    (path / "runtime" / "private").mkdir(parents=True)
    (path / "runtime" / "private" / "accounts.json").write_text("{}", encoding="utf-8")
    (path / ".env").write_text("FIXTURE_NOT_A_REAL_SECRET=1\n", encoding="utf-8")
    (path / "data").mkdir()
    (path / "data" / "fixture.json").write_text("{}", encoding="utf-8")
    (path / "brand_packs" / "FIXTURE").mkdir(parents=True)
    (path / "brand_packs" / "FIXTURE" / "statement.md").write_text("x\n", encoding="utf-8")
    git(path, "add", "config", "src", "tests", "README.md")
    git(path, "commit", "-m", "fixture baseline")
    return path


@pytest.fixture()
def repo(tmp_path):
    return make_git_repo(tmp_path / "repo")


@pytest.fixture()
def settings(tmp_path, repo):
    db_path = tmp_path / "runtime" / "state" / "ledger.db"
    init_db(str(db_path))
    return WebSettings(
        db_path=db_path,
        repo_root=repo,
        codex_worktree_root=tmp_path / "worktrees",
        session_secret="0" * 64,
        codex_enabled=False,
    )


def _status(repo) -> str:
    return git(repo, "status", "--porcelain")


def test_branch_name_uses_incident_and_short_signature():
    assert branch_name_for(INCIDENT) == "codex/repair-42-abc123de"
    assert branch_name_for({"id": 7, "signature": ""}) == "codex/repair-7-nosig"


def test_create_worktree_applies_baseline_overlay(repo, settings):
    # Uncommitted tracked production state: one unstaged + one staged change.
    (repo / "config" / "selectors" / "us.yaml").write_text(
        "submit_button: '#submit-new'\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("fixture v2\n", encoding="utf-8")
    git(repo, "add", "README.md")
    head_before = git(repo, "rev-parse", "HEAD").strip()
    status_before = _status(repo)

    wt = create_repair_worktree(settings, job_id=101, incident=INCIDENT)

    assert wt.path.is_dir()
    assert wt.branch == "codex/repair-42-abc123de"
    # The overlay reproduces the production working tree inside the worktree.
    assert (wt.path / "config" / "selectors" / "us.yaml").read_text(
        encoding="utf-8"
    ) == "submit_button: '#submit-new'\n"
    assert (wt.path / "README.md").read_text(encoding="utf-8") == "fixture v2\n"
    # Baseline overlay is the branch's first commit, recorded as baseline SHA.
    assert wt.baseline_sha != head_before
    assert git(wt.path, "rev-parse", "HEAD").strip() == wt.baseline_sha
    messages = git(wt.path, "log", "--format=%s")
    assert f"baseline: uncommitted production state at {head_before}" in messages
    # Worktree is clean after the baseline commit.
    assert _status(wt.path) == ""
    # Production tree: HEAD, branch and dirty state all unchanged.
    assert git(repo, "rev-parse", "HEAD").strip() == head_before
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _status(repo) == status_before


def test_create_worktree_clean_tree_has_no_overlay_commit(repo, settings):
    head = git(repo, "rev-parse", "HEAD").strip()
    wt = create_repair_worktree(settings, job_id=102, incident=INCIDENT)
    assert wt.baseline_sha == head
    assert git(wt.path, "rev-list", "--count", "HEAD").strip() == "1"
    assert _status(wt.path) == ""


def test_worktree_never_contains_private_files(repo, settings):
    wt = create_repair_worktree(settings, job_id=103, incident=INCIDENT)
    assert not (wt.path / "runtime").exists()
    assert not (wt.path / ".env").exists()
    assert not (wt.path / "data").exists()
    assert not (wt.path / "brand_packs").exists()
    # Only tracked content was checked out.
    assert (wt.path / "config" / "selectors" / "us.yaml").is_file()
    assert (wt.path / "tests" / "test_probe.py").is_file()


def test_stale_branch_from_failed_attempt_is_replaced(repo, settings):
    git(repo, "branch", "codex/repair-42-abc123de")  # stale leftover
    wt = create_repair_worktree(settings, job_id=104, incident=INCIDENT)
    assert git(wt.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == wt.branch


def test_stale_worktree_holding_branch_is_removed_on_retry(repo, settings):
    """A failed attempt leaves its worktree on the repair branch; the next
    job for the same incident must evict it (git refuses to delete a branch
    that is checked out)."""
    first = create_repair_worktree(settings, job_id=110, incident=INCIDENT)
    assert first.path.is_dir()
    second = create_repair_worktree(settings, job_id=111, incident=INCIDENT)
    assert not first.path.exists()
    assert second.path.is_dir()
    assert git(second.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == second.branch
    assert second.branch == first.branch


def test_apply_failure_cleans_up_worktree_and_branch(repo, settings, monkeypatch):
    import src.repair.worktree as wt_mod

    real_git = wt_mod._git

    def flaky_git(cwd, args, *, input_text=None):
        if args and args[0] == "apply":
            raise WorktreeError("simulated apply conflict")
        return real_git(cwd, args, input_text=input_text)

    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr(wt_mod, "_git", flaky_git)
    with pytest.raises(WorktreeError):
        create_repair_worktree(settings, job_id=105, incident=INCIDENT)
    monkeypatch.undo()

    assert not (settings.codex_worktree_root / "repair-105").exists()
    # Branch and worktree registration are gone too.
    refs = git(repo, "branch", "--list", "codex/repair-*")
    assert refs.strip() == ""
    assert "repair-105" not in git(repo, "worktree", "list", "--porcelain")


def test_patch_commit_cycle_and_evidence_exclusion(repo, settings, tmp_path):
    wt = create_repair_worktree(settings, job_id=106, incident=INCIDENT)
    # Redacted evidence copy lives untracked inside the worktree.
    evidence_root = tmp_path / "evidence"
    bundle = evidence_root / "bundle-42"
    bundle.mkdir(parents=True)
    (bundle / "page.txt").write_text("Apply to sell\n", encoding="utf-8")
    (bundle / "shot.png").write_bytes(b"\x89PNG fixture")
    incident = dict(INCIDENT, evidence_bundle_path=str(bundle))
    copied = copy_evidence_into_worktree(incident, wt.path)
    assert copied == [f"{EVIDENCE_DIRNAME}/page.txt"]  # screenshots stay withheld

    # Codex edits a selector and adds a regression test.
    (wt.path / "config" / "selectors" / "us.yaml").write_text(
        "submit_button: '#submit-fixed'\n", encoding="utf-8"
    )
    (wt.path / "tests" / "test_selector_regression.py").write_text(
        "def test_selector():\n    assert True\n", encoding="utf-8"
    )
    changed = stage_patch_changes(wt.path, wt.baseline_sha)
    assert changed == ["config/selectors/us.yaml", "tests/test_selector_regression.py"]
    patch_sha = commit_patch(wt.path, 42)
    assert patch_sha != wt.baseline_sha
    log = git(wt.path, "log", "--format=%s")
    assert "codex: repair incident 42" in log
    # The evidence directory never entered the commit or the diff.
    diff = git(wt.path, "diff", wt.baseline_sha, patch_sha)
    assert EVIDENCE_DIRNAME not in diff
    # It stays untracked — the only status line left.
    assert _status(wt.path) == f"?? {EVIDENCE_DIRNAME}/\n"


def test_create_patch_job_dedup_and_reopen(settings):
    job = create_patch_job(str(settings.db_path), 42)
    assert job is not None and job["stage"] == "patch" and job["status"] == "running"
    # Second active patch job for the same incident is refused.
    assert create_patch_job(str(settings.db_path), 42) is None
    # A different incident is unaffected.
    assert create_patch_job(str(settings.db_path), 43) is not None
    # Terminal jobs free the slot again.
    finish_repair_job(str(settings.db_path), job["id"], "failed")
    reopened = create_patch_job(str(settings.db_path), 42)
    assert reopened is not None and reopened["id"] != job["id"]
    # patch_ready still holds the slot.
    finish_repair_job(str(settings.db_path), reopened["id"], "patch_ready")
    assert create_patch_job(str(settings.db_path), 42) is None


def test_cleanup_removes_only_expired_terminal_worktrees(repo, settings):
    wt_old = create_repair_worktree(settings, job_id=201, incident=INCIDENT)
    wt_new = create_repair_worktree(
        settings, job_id=202, incident={"id": 43, "signature": "bbbb2222cccc3333"}
    )
    db_path = str(settings.db_path)
    old_job = create_patch_job(db_path, 42)
    set_repair_job_worktree(
        db_path, old_job["id"],
        worktree_path=str(wt_old.path), branch_name=wt_old.branch,
        baseline_sha=wt_old.baseline_sha,
    )
    finish_repair_job(db_path, old_job["id"], "failed")
    new_job = create_patch_job(db_path, 43)
    set_repair_job_worktree(
        db_path, new_job["id"],
        worktree_path=str(wt_new.path), branch_name=wt_new.branch,
        baseline_sha=wt_new.baseline_sha,
    )
    finish_repair_job(db_path, new_job["id"], "patch_ready")

    # Age the old job beyond the retention window.
    import sqlite3

    old_ts = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE codex_repair_jobs SET finished_at=? WHERE id=?", (old_ts, old_job["id"])
    )
    conn.commit()
    conn.close()

    settings.codex_worktree_retention_days = 14
    removed = cleanup_expired_worktrees(settings)
    assert removed == [old_job["id"]]
    assert not wt_old.path.exists()
    # patch_ready is active — its worktree survives regardless of age.
    assert wt_new.path.is_dir()
    assert "repair-201" not in git(repo, "worktree", "list", "--porcelain")


def test_production_tree_untouched_by_full_cycle(repo, settings):
    head_before = git(repo, "rev-parse", "HEAD").strip()
    status_before = _status(repo)
    wt = create_repair_worktree(settings, job_id=301, incident=INCIDENT)
    (wt.path / "config" / "selectors" / "us.yaml").write_text(
        "submit_button: '#changed'\n", encoding="utf-8"
    )
    stage_patch_changes(wt.path, wt.baseline_sha)
    commit_patch(wt.path, 42)
    assert git(repo, "rev-parse", "HEAD").strip() == head_before
    assert _status(repo) == status_before
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    # The only new ref is the repair branch.
    branches = git(repo, "branch", "--format=%(refname:short)").split()
    assert set(branches) == {"main", "codex/repair-42-abc123de"}


def test_get_repair_job_carries_stage7_fields(settings):
    job = create_patch_job(str(settings.db_path), 55)
    set_repair_job_worktree(
        str(settings.db_path), job["id"],
        worktree_path="wt", branch_name="codex/repair-55-abc123de",
        baseline_sha="base-sha",
    )
    finished = finish_repair_job(
        str(settings.db_path), job["id"], "patch_ready",
        changed_files_json='["config/selectors/us.yaml"]', risk_level="R1",
        tests_passed=1, patch_sha="patch-sha",
    )
    assert finished["baseline_sha"] == "base-sha"
    assert finished["patch_sha"] == "patch-sha"
    assert finished["risk_level"] == "R1"
    assert finished["changed_files"] == ["config/selectors/us.yaml"]
    assert get_repair_job(str(settings.db_path), job["id"])["status"] == "patch_ready"
