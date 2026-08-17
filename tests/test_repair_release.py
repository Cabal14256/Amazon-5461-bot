"""Stage-8 merge/restart/revert safety in a throwaway Git repository."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.repair.release import (
    ReleaseConflict,
    merge_repair_branch,
    revert_release,
    verify_release_head,
)
from tests.test_repair_worktree import git, make_git_repo


def _release_fixture(tmp_path):
    repo = make_git_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text(
        ".env\nbrand_packs/\ndata/\nruntime/\n", encoding="utf-8"
    )
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore private runtime")
    baseline = git(repo, "rev-parse", "HEAD").strip()
    branch = "codex/repair-7-fixture"
    git(repo, "checkout", "-b", branch)
    (repo / "config" / "selectors" / "us.yaml").write_text(
        "submit_button: '#submit-fixed'\n", encoding="utf-8"
    )
    git(repo, "add", "config/selectors/us.yaml")
    git(repo, "commit", "-m", "repair selector")
    patch_sha = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "main")
    settings = SimpleNamespace(repo_root=repo)
    job = {
        "baseline_sha": baseline,
        "patch_sha": patch_sha,
        "branch_name": branch,
    }
    return repo, settings, job


def test_merge_then_git_revert_is_auditable(tmp_path):
    repo, settings, job = _release_fixture(tmp_path)

    merged = merge_repair_branch(settings, job, expected_patch_sha=job["patch_sha"])

    assert merged["pre_release_sha"] == job["baseline_sha"]
    assert merged["release_sha"] == git(repo, "rev-parse", "HEAD").strip()
    parents = git(repo, "rev-list", "--parents", "-n", "1", merged["release_sha"]).split()
    assert len(parents) == 3
    verify_release_head(settings, {**job, **merged})

    rollback_sha = revert_release(settings, {**job, **merged})

    assert rollback_sha == git(repo, "rev-parse", "HEAD").strip()
    assert rollback_sha != merged["pre_release_sha"]
    assert "#submit-old" in (repo / "config" / "selectors" / "us.yaml").read_text()
    assert git(repo, "status", "--porcelain") == ""


def test_release_rejects_dirty_tree_stale_sha_and_head_drift(tmp_path):
    repo, settings, job = _release_fixture(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ReleaseConflict, match="dirty_worktree"):
        merge_repair_branch(settings, job, expected_patch_sha=job["patch_sha"])
    git(repo, "restore", "README.md")

    with pytest.raises(ReleaseConflict, match="stale_patch_sha"):
        merge_repair_branch(settings, job, expected_patch_sha="stale")

    (repo / "README.md").write_text("drift\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "head drift")
    with pytest.raises(ReleaseConflict, match="baseline_head_mismatch"):
        merge_repair_branch(settings, job, expected_patch_sha=job["patch_sha"])


def test_revert_refuses_head_mismatch_instead_of_resetting(tmp_path):
    repo, settings, job = _release_fixture(tmp_path)
    merged = merge_repair_branch(settings, job, expected_patch_sha=job["patch_sha"])
    (repo / "README.md").write_text("post release drift\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "post release drift")

    with pytest.raises(ReleaseConflict, match="release_head_mismatch"):
        revert_release(settings, {**job, **merged})

    assert "post release drift" in (repo / "README.md").read_text()


def test_release_rejects_patch_not_descended_from_recorded_baseline(tmp_path):
    repo, settings, job = _release_fixture(tmp_path)
    tree = git(repo, "rev-parse", f"{job['patch_sha']}^{{tree}}").strip()
    unrelated = git(repo, "commit-tree", tree, "-m", "unrelated repair").strip()
    git(repo, "branch", "-f", job["branch_name"], unrelated)
    job["patch_sha"] = unrelated

    with pytest.raises(ReleaseConflict, match="patch_not_based_on_baseline"):
        merge_repair_branch(settings, job, expected_patch_sha=unrelated)


def test_release_rejects_detached_head(tmp_path):
    repo, settings, job = _release_fixture(tmp_path)
    git(repo, "checkout", "--detach", job["baseline_sha"])

    with pytest.raises(ReleaseConflict, match="invalid_release_target_branch"):
        merge_repair_branch(settings, job, expected_patch_sha=job["patch_sha"])
